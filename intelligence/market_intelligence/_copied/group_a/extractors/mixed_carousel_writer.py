"""GA17 opt-in mixed A004 transaction; exact A003 bindings, no GA18 promises."""
from __future__ import annotations

import json

from .carousel import CarouselError, _scope
from .carousel_writer import _rights
from .mixed_carousel import (
    validate_mixed_observation, _audio_proof, _summary
)
from ..adapters.a003_slide import PinnedA003SlideReader

VERSION = "GA17-v1"


def _compare_rows(conn, analysis_id, observation):
    rows=conn.execute(
        """SELECT slide_index,item_type,media_asset_id,video_a_analysis_id,
                  aspect_ratio,duration_sec,text_status,on_screen_text,
                  screen_word_count,ocr_confidence_pct
           FROM carousel_feature_a_items
           WHERE analysis_id=%s ORDER BY slide_index""",
        (analysis_id,)
    ).fetchall()
    if len(rows)!=len(observation["items"]):
        raise CarouselError("mixed_a004_replay_conflict")
    for row, item in zip(rows,observation["items"]):
        if (row[0]!=item["slide_index"] or row[1]!=item["item_type"]
            or row[2]!=item["media_asset_id"] or row[3]!=item["video_a_analysis_id"]
            or row[4]!=item["aspect_ratio"]
            or (None if row[5] is None else format(row[5],".3f"))!=item["duration_sec"]
            or row[6]!=item["text_status"]
            or row[7]!=item["on_screen_text"]
            or row[8]!=item["screen_word_count"]
            or (None if row[9] is None else format(row[9],".2f"))!=item["ocr_confidence_pct"]):
            raise CarouselError("mixed_a004_replay_conflict")


def persist_mixed_carousel(conn, manifest, observation, *, verify_current,
                           audio_proofs=None):
    """One locked/rollback-safe A004 analysis from a GA17 mixed observation.

    The analysis selects A003 video IDs explicitly and verifies the entire
    exact-asset A003 binding AGAIN within the A004 transaction. A003 is
    read-only here: GA18 owns creating or upgrading video measurements.
    """
    observed=validate_mixed_observation(manifest,observation)
    if not callable(verify_current) or not callable(getattr(conn,"transaction",None)):
        raise CarouselError("trusted_mixed_writer_ports_required")
    if audio_proofs is None:
        audio_proofs={}
    if type(audio_proofs) is not dict:
        raise CarouselError("invalid_audio_proofs")
    video_items=[x for x in manifest["items"] if x["kind"]=="video"]
    video_ids={x["media_asset_id"] for x in video_items}
    if any(type(k) is not int or k not in video_ids for k in audio_proofs):
        raise CarouselError("orphan_audio_proof")
    audio_states=[_audio_proof(item,audio_proofs) for item in video_items]
    expected=_summary(observed["items"],audio_states)
    for k in ("has_audio","audio_status","analysis_state","text_status",
              "first_text_slide","total_screen_word_count"):
        if observed[k]!=expected[k]:
            raise CarouselError("mixed_aggregate_claim_invalid")
    audio_proof_shas={
        str(item["media_asset_id"]): audio_proofs[item["media_asset_id"]].wire()["observation_sha256"]
        for item in video_items if item["media_asset_id"] in audio_proofs
    }
    methods={
        "group_a":"mixed-carousel/v1",
        "manifest_sha256":manifest["manifest_sha256"],
        "observation_sha256":observed["observation_sha256"],
        "a003_bindings":{
            str(x["media_asset_id"]):x["video_binding_sha256"]
            for x in observed["items"] if x["item_type"]=="video"
        },
        "ga09_audio_evidence":audio_proof_shas,
    }
    reasons={
        str(x["slide_index"]):x["reason_code"] for x in observed["items"]
        if x["reason_code"] is not None
    }
    ocr_method=(
        observed["ocr_model"]["engine"]+":"+observed["ocr_model"]["engine_version"]
        if observed["ocr_model"] else None
    )
    key="ga17-"+observed["observation_sha256"]

    with conn.transaction():
        _rights(conn,manifest)
        for item in manifest["items"]:
            if verify_current(_scope(manifest,item)) is not True:
                raise CarouselError("media_rights_unverified")
        reader=PinnedA003SlideReader(
            conn,lock_for_snapshot=True,analysis_ids={
                x["media_asset_id"]: x["video_a_analysis_id"]
                for x in observed["items"] if x["item_type"]=="video"
            }
        )
        for manifest_item, evidence in zip(manifest["items"],observed["items"]):
            if manifest_item["kind"]!="video":
                continue
            bound=reader(manifest,manifest_item)
            if bound is None:
                if evidence["video_a_analysis_id"] is not None:
                    raise CarouselError("mixed_a003_binding_mismatch")
            elif (bound["binding_sha256"]!=evidence["video_binding_sha256"]
                  or bound["duration_sec"]!=evidence["duration_sec"]
                  or bound["aspect_ratio"]!=evidence["aspect_ratio"]
                  or bound["timeline"]!=evidence["video_timeline"]
                  or (bound["state"]=="completed")!=(evidence["reason_code"] is None)):
                raise CarouselError("mixed_a003_binding_mismatch")
        row=conn.execute(
            """INSERT INTO carousel_feature_a_analyses
               (video_id,source_id,source_policy_version_id,analysis_key,
                analysis_version,method_name,method_version,model_name,model_version,
                content_kind,state,slide_status,text_status,audio_status,has_audio,
                audio_details,measurement_methods,unavailable_reasons,measured_at)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,'mixed_carousel',
                       'pending','not_run','not_run','not_run','unknown',
                       %s::jsonb,%s::jsonb,%s::jsonb,now())
               ON CONFLICT (analysis_key) DO NOTHING RETURNING id""",
            (manifest["video_id"],manifest["source_id"],
             manifest["source_policy_version_id"],key,VERSION,
             "group-a-mixed-carousel",observed["method_version"],
             observed["ocr_model"]["engine"] if observed["ocr_model"] else None,
             observed["ocr_model"]["engine_version"] if observed["ocr_model"] else None,
             json.dumps({"ga09_proofs": audio_proof_shas}),
             json.dumps(methods),json.dumps(reasons))
        ).fetchone()
        if row is None:
            old=conn.execute(
                """SELECT id,video_id,source_id,source_policy_version_id,
                          content_kind,state,slide_status,text_status,audio_status,
                          has_audio,measurement_methods,audio_details
                   FROM carousel_feature_a_analyses
                   WHERE analysis_key=%s FOR UPDATE""",
                (key,)
            ).fetchone()
            if old is None or old[1:]!=(
                manifest["video_id"],manifest["source_id"],
                manifest["source_policy_version_id"],"mixed_carousel",
                observed["analysis_state"],"complete",observed["text_status"],
                observed["audio_status"],observed["has_audio"],methods,
                {"ga09_proofs":audio_proof_shas}
            ):
                raise CarouselError("mixed_a004_replay_conflict")
            analysis_id=old[0]
            _compare_rows(conn,analysis_id,observed)
        else:
            analysis_id=row[0]
            for item in observed["items"]:
                conn.execute(
                    """INSERT INTO carousel_feature_a_items
                       (analysis_id,slide_index,item_type,media_asset_id,
                        video_a_analysis_id,aspect_ratio,duration_sec,text_status,
                        on_screen_text,screen_word_count,ocr_method,
                        ocr_confidence_pct,unavailable_reasons)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb)""",
                    (analysis_id,item["slide_index"],item["item_type"],
                     item["media_asset_id"],item["video_a_analysis_id"],
                     item["aspect_ratio"],item["duration_sec"],
                     item["text_status"],item["on_screen_text"],
                     item["screen_word_count"],
                     ocr_method if item["item_type"]=="image" else None,
                     item["ocr_confidence_pct"],
                     json.dumps({"reason_code":item["reason_code"]}
                                if item["reason_code"] else {}))
                )
            conn.execute(
                """UPDATE carousel_feature_a_analyses SET
                     state=%s,slide_status='complete',text_status=%s,
                     audio_status=%s,has_audio=%s WHERE id=%s""",
                (observed["analysis_state"],observed["text_status"],
                 observed["audio_status"],observed["has_audio"],analysis_id)
            )
        for item in manifest["items"]:
            if verify_current(_scope(manifest,item)) is not True:
                raise CarouselError("media_rights_revoked_before_commit")
    return analysis_id
