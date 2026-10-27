"""GA16 opt-in PostgreSQL A004 writer; one unit of work, no host activation.

Writer deliberately requires a LIVE host QDP/project authorization callback
AND locks on source, policy, publication and every permitted media asset.
This is not the GA18 cross-stream atomic Group A snapshot writer.
"""
from __future__ import annotations

import json

from ..contracts_v1 import canonical_sha256
from .carousel import CarouselError, validate_photo_observation, _scope

ANALYSIS_VERSION = "GA16-v1"


def _rights(conn, manifest):
    source = conn.execute(
        "SELECT status,can_analyze_media FROM sources WHERE id=%s FOR SHARE",
        (manifest["source_id"],)
    ).fetchone()
    if source != ("enabled", True):
        raise CarouselError("source_media_rights_revoked")
    policy = conn.execute(
        """SELECT is_active,permissions FROM source_policy_versions
           WHERE id=%s AND source_id=%s FOR SHARE""",
        (manifest["source_policy_version_id"], manifest["source_id"])
    ).fetchone()
    if (policy is None or policy[0] is not True or type(policy[1]) is not dict
        or policy[1].get("can_analyze_media") is not True):
        raise CarouselError("source_policy_revoked")
    row = conn.execute(
        "SELECT content_kind FROM videos WHERE id=%s FOR SHARE",
        (manifest["video_id"],)
    ).fetchone()
    if row is None or row[0] not in (None, manifest["content_kind"]):
        raise CarouselError("publication_kind_mismatch")
    for item in manifest["items"]:
        row = conn.execute(
            """SELECT video_id,source_id,media_kind,sha256,permission,
                      (expires_at IS NULL OR expires_at>now())
               FROM media_assets WHERE id=%s FOR SHARE""",
            (item["media_asset_id"],)
        ).fetchone()
        if (row is None or row != (
            manifest["video_id"], manifest["source_id"], "image",
            item["sha256"], "analysis_allowed", True
        )):
            raise CarouselError("media_asset_rights_or_sha_revoked")


def _assert_replay(conn, analysis_id, manifest, observation):
    rows = conn.execute(
        """SELECT slide_index,item_type,media_asset_id,aspect_ratio,duration_sec,
                  text_status,on_screen_text,screen_word_count,ocr_confidence_pct
           FROM carousel_feature_a_items WHERE analysis_id=%s ORDER BY slide_index""",
        (analysis_id,)
    ).fetchall()
    expected = observation["items"]
    if len(rows) != len(expected):
        raise CarouselError("analysis_key_replay_conflict")
    for row, item in zip(rows, expected):
        if (row[0] != item["slide_index"] or row[1] != "image"
            or row[2] != item["media_asset_id"] or row[3] != item["aspect_ratio"]
            or row[4] is not None or row[5] != item["text_status"]
            or row[6] != item["on_screen_text"]
            or row[7] != item["screen_word_count"]
            or (None if row[8] is None else format(row[8], ".2f")) != item["ocr_confidence_pct"]):
            raise CarouselError("analysis_key_replay_conflict")


def persist_photo_carousel(conn, manifest, observation, *, verify_current):
    """Durably persist one validated GA16 photo observation to real A004.

    psycopg3 connection; rights callback validates QDP input hash and current
    tenant/policy membership, not merely a historical manifest. Caller is
    responsible for avoiding revoked data during OCR; this writer rechecks.
    Successful replays of EXACTLY the same immutable observation return the
    existing A004 analysis id. A mismatched conflict fails closed.
    """
    observed = validate_photo_observation(manifest, observation)
    if not callable(verify_current) or not callable(getattr(conn, "transaction", None)):
        raise CarouselError("trusted_writer_ports_required")
    for item in manifest["items"]:
        if verify_current(_scope(manifest, item)) is not True:
            raise CarouselError("media_rights_unverified")
    analysis_key = "ga16-" + observed["observation_sha256"]
    methods = {
        "group_a": "photo-carousel/v1",
        "manifest_sha256": manifest["manifest_sha256"],
        "observation_sha256": observed["observation_sha256"],
        "ocr_model_sha256": (
            observed["ocr_model"]["model_sha256"] if observed["ocr_model"] else None
        ),
    }
    reasons = {str(item["slide_index"]): item["reason_code"]
               for item in observed["items"] if item["reason_code"] is not None}
    ocr_method = (observed["ocr_model"]["engine"] + ":" +
                  observed["ocr_model"]["engine_version"]
                  if observed["ocr_model"] else None)

    # A004 trigger checks the parent at "completed" transition, so insert
    # the parent as pending, then its children, then close the snapshot.
    with conn.transaction():
        _rights(conn, manifest)
        created = conn.execute(
            """INSERT INTO carousel_feature_a_analyses
               (video_id,source_id,source_policy_version_id,analysis_key,
                analysis_version,method_name,method_version,model_name,model_version,
                content_kind,state,slide_status,text_status,audio_status,
                has_audio,measurement_methods,unavailable_reasons,measured_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending','not_run',
                       'not_run','not_run','unknown',%s::jsonb,%s::jsonb,now())
               ON CONFLICT (analysis_key) DO NOTHING RETURNING id""",
            (manifest["video_id"], manifest["source_id"],
             manifest["source_policy_version_id"], analysis_key, ANALYSIS_VERSION,
             "group-a-photo-carousel", observed["method_version"],
             observed["ocr_model"]["engine"] if observed["ocr_model"] else None,
             observed["ocr_model"]["engine_version"] if observed["ocr_model"] else None,
             manifest["content_kind"], json.dumps(methods), json.dumps(reasons))
        ).fetchone()
        if created is None:
            previous = conn.execute(
                """SELECT id,video_id,source_id,source_policy_version_id,
                          content_kind,state,slide_status,text_status,
                          measurement_methods
                   FROM carousel_feature_a_analyses
                   WHERE analysis_key=%s FOR UPDATE""",
                (analysis_key,)
            ).fetchone()
            if (previous is None or previous[1:8] != (
                    manifest["video_id"], manifest["source_id"],
                    manifest["source_policy_version_id"],
                    manifest["content_kind"], observed["analysis_state"],
                    "complete", observed["text_status"]
                ) or previous[8] != methods):
                raise CarouselError("analysis_key_replay_conflict")
            _assert_replay(conn, previous[0], manifest, observed)
            analysis_id = previous[0]
        else:
            analysis_id = created[0]
            for item in observed["items"]:
                conn.execute(
                    """INSERT INTO carousel_feature_a_items
                       (analysis_id,slide_index,item_type,media_asset_id,
                        aspect_ratio,duration_sec,text_status,on_screen_text,
                        screen_word_count,ocr_method,ocr_confidence_pct,
                        unavailable_reasons)
                       VALUES (%s,%s,'image',%s,%s,NULL,%s,%s,%s,%s,%s,%s::jsonb)""",
                    (analysis_id, item["slide_index"], item["media_asset_id"],
                     item["aspect_ratio"], item["text_status"],
                     item["on_screen_text"], item["screen_word_count"],
                     ocr_method, item["ocr_confidence_pct"],
                     json.dumps({"reason_code": item["reason_code"]}
                                if item["reason_code"] is not None else {}))
                )
            conn.execute(
                """UPDATE carousel_feature_a_analyses SET
                    state=%s,slide_status='complete',text_status=%s
                   WHERE id=%s""",
                (observed["analysis_state"], observed["text_status"], analysis_id)
            )
        # Re-authorization is repeated INSIDE the lock-protected transaction
        # just before commit, including on idempotent replay.
        for item in manifest["items"]:
            if verify_current(_scope(manifest, item)) is not True:
                raise CarouselError("media_rights_revoked_before_commit")
    return analysis_id
