"""GA18 opt-in one-transaction Group A A003 + optional A004 repository.

No OCR/FFmpeg/paid execution under PostgreSQL locks. Source-side data is
prepared and trusted by the host before the transaction; DB mutation uses
current QDP/policy/media gates and immutable checksum-pinned replay.
"""
from __future__ import annotations

from datetime import datetime
from decimal import Decimal
import json

from .assembly import validate_video_snapshot, rebind_prepared_mixed
from .validation import SnapshotError, _certificate, video_scope
from .contracts_v1 import canonical_sha256
from .extractors.carousel import _scope, CarouselError
from .extractors.carousel_writer import _rights, persist_photo_carousel
from .extractors.mixed_carousel_writer import persist_mixed_carousel
from .extractors.mixed_carousel import validate_mixed_observation
from .extractors.carousel import validate_photo_observation
from .adapters.a003_slide import PinnedA003SlideReader

VERSION="GA18-v1"
METHOD="group-a-atomic-snapshot-v1"

ROWS=(
    ("scenes","video_feature_a_scenes",
     ("scene_index","start_sec","end_sec"),("scene_index","start_sec","end_sec")),
    ("speech_segments","video_feature_a_speech_segments",
     ("start_sec","end_sec","spoken_text","method_name","confidence_pct"),
     ("start_sec","end_sec","spoken_text","method_name","confidence_pct")),
    ("screen_text_segments","video_feature_a_screen_text",
     ("start_sec","end_sec","screen_text","method_name","confidence_pct"),
     ("start_sec","end_sec","screen_text","method_name","confidence_pct")),
    ("audio_events","video_feature_a_audio_events",
     ("event_type","start_sec","end_sec","label","method_name","confidence_pct"),
     ("event_type","start_sec","end_sec","label","method_name","confidence_pct")),
)


def _at(iso):
    return datetime.fromisoformat(iso.replace("Z","+00:00"))


def _normalized(x,name):
    if x is None:
        return None
    if name in ("start_sec","end_sec"):
        return format(x,".3f")
    if name=="confidence_pct":
        return format(x,".2f")
    return x


def _check_replay(conn, analysis_id, snap):
    for name,table,columns,keys in ROWS:
        select=", ".join(columns)
        order=("scene_index" if name=="scenes" else "start_sec,end_sec,id")
        rows=conn.execute(
            "SELECT "+select+" FROM "+table+
            " WHERE analysis_id=%s ORDER BY "+order,
            (analysis_id,)
        ).fetchall()
        expected=snap[name]
        if len(rows)!=len(expected):
            raise SnapshotError("ga18_replay_segment_count_changed")
        for db,origin in zip(rows,expected):
            if any(_normalized(value,key)!=origin[key]
                   for key,value in zip(keys,db)):
                raise SnapshotError("ga18_replay_segment_mismatch")


def _insert_rows(conn,analysis_id,snap):
    for name,table,columns,keys in ROWS:
        fields="analysis_id,"+",".join(columns)
        slots=",".join(["%s"]*(len(columns)+1))
        sql="INSERT INTO "+table+" ("+fields+") VALUES ("+slots+")"
        for row in snap[name]:
            conn.execute(sql,(analysis_id,)+tuple(row[k] for k in keys))


def _persist_video(conn,manifest,snap,verify_current,verify_coverage,verify_origin):
    snap=validate_video_snapshot(manifest,snap)
    _,item,scope=video_scope(manifest,snap["media_asset_id"])
    if verify_origin(dict(snap)) is not True:
        raise SnapshotError("snapshot_origin_untrusted")
    # Trust callbacks are forbidden to silently change snapshot content.
    validate_video_snapshot(manifest,snap)
    if verify_current(scope) is not True:
        raise SnapshotError("media_rights_unverified")
    for name,proof in snap["coverage_certificates"].items():
        _certificate(proof,stream=name,asset_sha=item["sha256"],
                     evidence_sha=snap["coverage"][name]["observation_sha256"],
                     verify_coverage=verify_coverage)
    # A003 current source/asset SHA, permissions and expiration are checked
    # under the same SQL lock as the eventual write to A003.
    row=conn.execute(
        """SELECT video_id,source_id,media_kind,sha256,permission,
                  (expires_at IS NULL OR expires_at>now())
           FROM media_assets WHERE id=%s FOR SHARE""",
        (item["media_asset_id"],)
    ).fetchone()
    if row!=(
        manifest["video_id"],manifest["source_id"],"video",
        item["sha256"],"analysis_allowed",True
    ):
        raise SnapshotError("media_asset_rights_or_hash_changed")
    methods={
        **snap["measurement_methods"],
        "ga18_snapshot_sha256":snap["observation_sha256"],
        "ga18_manifest_sha256":manifest["manifest_sha256"],
        "ga18_qdp_input_sha256":manifest["qdp_input_sha256"],
        "ga18_coverage":snap["coverage"],
        "ga18_certificates":{
            k:v["certificate_sha256"] for k,v in snap["coverage_certificates"].items()
        },
    }
    key="ga18-"+snap["observation_sha256"]
    insert=conn.execute(
        """INSERT INTO video_feature_a_analyses
           (video_id,media_asset_id,source_id,source_policy_version_id,
            analysis_key,analysis_version,method_name,method_version,
            state,scene_status,speech_status,screen_text_status,audio_status,
            duration_sec,aspect_ratio,spoken_words,speech_duration_sec,
            has_subtitles,cta_start_sec,measurement_methods,unavailable_reasons,
            measured_at)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,
             'pending','not_run','not_run','not_run','not_run',
             %s,%s,%s,%s,%s,NULL,%s::jsonb,%s::jsonb,%s)
           ON CONFLICT (analysis_key) DO NOTHING RETURNING id""",
        (snap["video_id"],snap["media_asset_id"],snap["source_id"],
         snap["source_policy_version_id"],key,VERSION,METHOD,snap["method_version"],
         snap["duration_sec"],snap["aspect_ratio"],snap["spoken_words"],
         snap["speech_duration_sec"],snap["has_subtitles"],json.dumps(methods),
         json.dumps(snap["unavailable_reasons"]),_at(snap["measured_at"]))
    ).fetchone()
    if insert is not None:
        analysis_id=insert[0]
        _insert_rows(conn,analysis_id,snap)
        conn.execute(
            """UPDATE video_feature_a_analyses SET
                 state=%s,scene_status=%s,speech_status=%s,
                 screen_text_status=%s,audio_status=%s
               WHERE id=%s""",
            (snap["analysis_state"],snap["scene_status"],
             snap["speech_status"],snap["screen_text_status"],
             snap["audio_status"],analysis_id)
        )
    else:
        old=conn.execute(
            """SELECT id,video_id,media_asset_id,source_id,source_policy_version_id,
                      analysis_version,method_name,method_version,state,
                      scene_status,speech_status,screen_text_status,audio_status,
                      duration_sec,aspect_ratio,spoken_words,speech_duration_sec,
                      has_subtitles,cta_start_sec,measurement_methods,
                      unavailable_reasons,measured_at
               FROM video_feature_a_analyses WHERE analysis_key=%s FOR UPDATE""",
            (key,)
        ).fetchone()
        if old is None:
            raise SnapshotError("ga18_replay_missing")
        expected=(
            snap["video_id"],snap["media_asset_id"],snap["source_id"],
            snap["source_policy_version_id"],VERSION,METHOD,snap["method_version"],
            snap["analysis_state"],snap["scene_status"],snap["speech_status"],
            snap["screen_text_status"],snap["audio_status"],
            Decimal(snap["duration_sec"]),snap["aspect_ratio"],
            snap["spoken_words"],
            None if snap["speech_duration_sec"] is None else Decimal(snap["speech_duration_sec"]),
            snap["has_subtitles"],None,methods,snap["unavailable_reasons"],
            _at(snap["measured_at"]),
        )
        if old[1:]!=expected:
            raise SnapshotError("ga18_replay_parent_mismatch")
        analysis_id=old[0]
        _check_replay(conn,analysis_id,snap)
    if verify_origin(dict(snap)) is not True or verify_current(scope) is not True:
        raise SnapshotError("snapshot_revoked_before_commit")
    validate_video_snapshot(manifest,snap)
    return analysis_id


def persist_group_a_snapshot(conn, manifest, *, video_snapshots=(),
                             prepared_carousel=None, verify_current,
                             verify_origin, verify_coverage=None,
                             audio_proofs=None):
    """One unit of work: all new A003 video rows and optionally A004 slides.

    For mixed carousel pass a prepared GA17 observation built entirely BEFORE
    the DB transaction, with missing/unbound A003 entries allowed. GA18
    rebinds only video metadata after inserting A003. For photo_carousel and
    image_post, use an already verified GA16 observation.
    All required mixed video assets must have a GA18 snapshot, avoiding
    cross-version snapshots and ambiguous selected A003 IDs.
    """
    if (not callable(getattr(conn,"transaction",None))
        or not callable(verify_current) or not callable(verify_origin)):
        raise SnapshotError("trusted_snapshot_writer_ports_required")
    if type(video_snapshots) not in (tuple,list) or len(video_snapshots)>100:
        raise SnapshotError("invalid_snapshot_batch")
    normalized=[validate_video_snapshot(manifest,s) for s in video_snapshots]
    seen=set()
    for snap in normalized:
        if snap["media_asset_id"] in seen:
            raise SnapshotError("duplicate_video_snapshot_asset")
        seen.add(snap["media_asset_id"])
    if prepared_carousel is not None:
        if manifest["content_kind"]=="mixed_carousel":
            validate_mixed_observation(manifest,prepared_carousel)
            videos={x["media_asset_id"] for x in manifest["items"] if x["kind"]=="video"}
            if seen!=videos:
                raise SnapshotError("mixed_snapshot_assets_incomplete")
        elif manifest["content_kind"] in ("image_post","photo_carousel"):
            validate_photo_observation(manifest,prepared_carousel)
            if normalized:
                raise SnapshotError("photo_cannot_attach_video_snapshot")
        else:
            raise SnapshotError("carousel_observation_kind_mismatch")
    if not normalized and prepared_carousel is None:
        raise SnapshotError("empty_atomic_snapshot")

    # Locks cover the SQL phase only. No subprocesses or media decoding here.
    with conn.transaction():
        _rights(conn,manifest)
        analysis_ids={}
        for snap in normalized:
            aid=_persist_video(conn,manifest,snap,verify_current,
                               verify_coverage,verify_origin)
            analysis_ids[snap["media_asset_id"]]=aid
        carousel_id=None
        if prepared_carousel is not None:
            if manifest["content_kind"]=="mixed_carousel":
                reader=PinnedA003SlideReader(
                    conn,analysis_ids=analysis_ids,lock_for_snapshot=True
                )
                finalized=rebind_prepared_mixed(
                    manifest,prepared_carousel,read_a003=reader,
                    verify_current=verify_current,audio_proofs=audio_proofs
                )
                carousel_id=persist_mixed_carousel(
                    conn,manifest,finalized,verify_current=verify_current,
                    audio_proofs=audio_proofs
                )
            else:
                carousel_id=persist_photo_carousel(
                    conn,manifest,prepared_carousel,verify_current=verify_current
                )
        for item in manifest["items"]:
            if verify_current(_scope(manifest,item)) is not True:
                raise SnapshotError("media_rights_revoked_before_commit")
        for snap in normalized:
            if verify_origin(dict(snap)) is not True:
                raise SnapshotError("snapshot_origin_revoked_before_commit")
            validate_video_snapshot(manifest,snap)
    return {"video_analysis_ids":analysis_ids,"carousel_analysis_id":carousel_id}
