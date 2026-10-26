"""GB-MOD-05: exact-version Group A snapshot admission without peer imports.

Reads the A-owned 054 schema but never selects "latest" or creates A outputs.
A result is usable only with an explicit (result ID, sha256) pin, matching
project/QDP/video/source/kind and fully evidenced stream matrix. This is
not media byte verification (GB-MOD-06), nor a live Group A producer claim.
"""
from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from ..contracts import ContractError, canonical_hash, content_type_for_kind
from ..errors import DependencyUnavailable, DependencyContractMismatch

SHA = re.compile(r"[0-9a-f]{64}\Z")
VERSION = re.compile(r"[A-Za-z0-9._:-]{1,160}\Z")
REASON = re.compile(r"[a-z][a-z0-9_]{1,79}\Z")
STREAMS = {
    "video": frozenset(("technical", "speech", "screen_text", "audio", "subtitles")),
    "slideshow_video": frozenset(("technical", "speech", "screen_text", "audio", "subtitles")),
    "photo_carousel": frozenset(("slides", "screen_text")),
    "mixed_carousel": frozenset(("slides", "technical", "speech", "screen_text", "audio", "subtitles")),
}
EVIDENCE_KIND = {
    "technical": "scene", "speech": "transcript", "screen_text": "screen_text",
    "audio": "audio", "subtitles": "screen_text", "slides": "slide",
}
GA_TABLES = ("ga_runs", "ga_results", "ga_media_refs")
GA_CONTRACT = "group-a-to-b/v1"
GA_ASSEMBLY = "group-a-assembly/v1"


def _deny(reason: str):
    raise DependencyUnavailable("group_a", reason)


def _digest(value: object) -> bool:
    return type(value) is str and SHA.fullmatch(value) is not None


def _positive(value: object) -> bool:
    return type(value) is int and value > 0


def _uuid(value: object) -> bool:
    if type(value) is not str:
        return False
    try:
        return str(UUID(value)) == value
    except ValueError:
        return False


def _row(cursor):
    r = cursor.fetchone()
    if r is None:
        return None
    if isinstance(r, Mapping):
        return dict(r)
    return dict(zip((x.name for x in cursor.description), r))


def require_a_schema(conn) -> None:
    """Absent GA 054 is an unavailable dependency, not an empty A result."""
    try:
        missing = conn.execute(
            "SELECT r.rel FROM unnest(%s::text[]) AS r(rel) "
            "WHERE to_regclass('public.' || r.rel) IS NULL",
            (list(GA_TABLES),)
        ).fetchall()
    except Exception:
        _deny("ga_schema_probe_failed")
    if missing:
        _deny("ga_schema_missing")


def validate_a_snapshot(snapshot: object, *, project_id: str, qdp_run_id: str,
                        video_id: int, content_kind: str, input_sha256: str,
                        manifest_sha256: str, analysis_id: int) -> dict:
    """Strict projection of GA contracts_v1.group_a_to_b_v1, no GA import.

    Rejects partial, unavailable and unrun streams, missing positive evidence,
    and fake zero-coverage without a negative proof. GA producer itself may
    evolve; a changed wire shape fails rather than silently downgrades.
    """
    keys = {
        "schema_version", "project_id", "qdp_run_id", "video_id",
        "qdp_input_sha256", "manifest_sha256", "content_kind",
        "analysis_state", "video_a_analysis_id", "carousel_a_analysis_id",
        "method_version", "streams", "evidence",
    }
    if type(snapshot) is not dict or set(snapshot) != keys:
        raise DependencyContractMismatch("group_a")
    s = snapshot
    if s["schema_version"] != GA_CONTRACT:
        raise DependencyContractMismatch("group_a")
    if (s["project_id"],s["qdp_run_id"],s["video_id"],s["content_kind"],
        s["qdp_input_sha256"],s["manifest_sha256"]) != (
        project_id,qdp_run_id,video_id,content_kind,input_sha256,manifest_sha256
    ):
        _deny("a_snapshot_lineage_mismatch")
    if not _uuid(s["qdp_run_id"]) or not _digest(s["qdp_input_sha256"]) or not _digest(s["manifest_sha256"]):
        raise DependencyContractMismatch("group_a")
    if type(s["method_version"]) is not str or VERSION.fullmatch(s["method_version"]) is None:
        raise DependencyContractMismatch("group_a")
    video = content_kind in ("video","slideshow_video")
    selected_id = s["video_a_analysis_id"] if video else s["carousel_a_analysis_id"]
    if not _positive(selected_id) or not _positive(analysis_id):
        raise DependencyContractMismatch("group_a")
    if ((s["video_a_analysis_id"] if video else s["carousel_a_analysis_id"]) != analysis_id or
        (s["carousel_a_analysis_id"] if video else s["video_a_analysis_id"]) is not None):
        _deny("a_analysis_id_mismatch")
    if s["analysis_state"] != "completed":
        _deny("a_analysis_not_complete")
    expected = STREAMS.get(content_kind)
    actual = s["streams"]
    if expected is None or type(actual) is not dict or set(actual) != expected:
        raise DependencyContractMismatch("group_a")
    evid = s["evidence"]
    if type(evid) is not list or len(evid) > 2000:
        raise DependencyContractMismatch("group_a")
    counts = Counter()
    for item in evid:
        if (type(item) is not dict or set(item) != {"kind","ref_sha256","asset_sha256"} or
            item["kind"] not in ("scene","transcript","screen_text","audio","frame","slide") or
            not _digest(item["ref_sha256"]) or not _digest(item["asset_sha256"])):
            raise DependencyContractMismatch("group_a")
        counts[item["kind"]] += 1
    for name, truth in actual.items():
        if type(truth) is not dict or set(truth) != {
            "status","evidence_count","verified_absence","reason_code"
        }:
            raise DependencyContractMismatch("group_a")
        if truth["status"] != "complete":
            _deny("a_stream_" + name + "_not_complete")
        number,absence,reason = (truth["evidence_count"],truth["verified_absence"],truth["reason_code"])
        if (type(number) is not int or number < 0 or type(absence) is not bool or
            reason is not None or absence != (number == 0)):
            raise DependencyContractMismatch("group_a")
        # A's strict v1 producer requires negative proof even for true absence.
        if counts[EVIDENCE_KIND[name]] < max(1,number):
            _deny("a_stream_" + name + "_evidence_missing")
    return dict(s)


@dataclass(frozen=True,slots=True)
class GroupAExactPin:
    project_id: str
    b_run_id: str
    ga_run_id: str
    ga_result_id: int
    a_analysis_id: int
    content_type: str
    source_policy_version_id: int
    result_sha256: str
    manifest_sha256: str
    evidence_count: int
    media_asset_ids: tuple[int,...]
    media_sha256s: tuple[str,...]


def read_exact_a(conn, *, b_run_id: str, ga_result_id: int, expected_sha256: str) -> GroupAExactPin:
    """Do not choose latest A. Re-read one explicitly pinned A result and media.

    GA 054 is optional on main. If it is absent, return a typed unavailable.
    This transaction must be repeated before any B paid operation.
    """
    if not _uuid(b_run_id) or not _positive(ga_result_id) or not _digest(expected_sha256):
        raise DependencyContractMismatch("group_a")
    require_a_schema(conn)
    try:
        row = _row(conn.execute(
            """SELECT b.id::text AS b_run_id,b.project_id,b.video_id,b.source_id,
                      b.content_type,b.state AS b_state,b.inbox_id,
                      b.video_a_analysis_id AS b_video_a,
                      b.carousel_a_analysis_id AS b_carousel_a,
                      b.a_snapshot_sha256 AS b_sha,b.media_manifest_sha256 AS b_manifest,
                      b.source_policy_version_id AS b_policy,
                      i.qdp_run_id::text AS qdp_run_id,
                      i.preparation_sha256 AS qdp_sha,
                      r.id::text AS ga_run_id,r.state AS ga_state,
                      r.content_kind,r.stage_version,r.qdp_input_sha256,
                      r.manifest_sha256,r.source_policy_version_id AS ga_policy,
                      r.expected_media_items,
                      g.id AS ga_result_id,g.state AS ga_result_state,
                      g.result_schema,g.result_sha256,g.snapshot,
                      g.evidence,g.method_version,
                      g.video_a_analysis_id,g.carousel_a_analysis_id,
                      va.state AS video_a_state,ca.state AS carousel_a_state,
                      va.source_id AS video_a_source,ca.source_id AS carousel_a_source,
                      va.video_id AS video_a_video,ca.video_id AS carousel_a_video,
                      va.media_asset_id AS video_a_media_asset_id,
                      va.source_policy_version_id AS video_a_policy,
                      ca.source_policy_version_id AS carousel_a_policy,
                      va.scene_status AS video_a_scene_status,
                      va.speech_status AS video_a_speech_status,
                      va.screen_text_status AS video_a_screen_text_status,
                      va.audio_status AS video_a_audio_status,
                      ca.content_kind AS carousel_a_kind,
                      ca.slide_status AS carousel_a_slide_status,
                      ca.text_status AS carousel_a_text_status
                 FROM group_b_runs b
                 JOIN group_b_inbox i ON i.id=b.inbox_id AND i.project_id=b.project_id
                 JOIN ga_runs r ON r.project_id=b.project_id
                       AND r.qdp_run_id=i.qdp_run_id AND r.video_id=b.video_id
                       AND r.source_id=b.source_id
                       AND r.qdp_input_sha256=i.preparation_sha256
                 JOIN ga_results g ON g.id=%s AND g.run_id=r.id
                       AND g.project_id=b.project_id AND g.video_id=b.video_id
                       AND g.source_id=b.source_id
                 LEFT JOIN video_feature_a_analyses va ON va.id=g.video_a_analysis_id
                 LEFT JOIN carousel_feature_a_analyses ca ON ca.id=g.carousel_a_analysis_id
                WHERE b.id=%s::uuid""", (ga_result_id,b_run_id)
        ))
    except Exception:
        _deny("ga_read_contract_unavailable")
    if row is None:
        _deny("a_exact_snapshot_not_found")
    if row["b_state"] not in ("draft","needs_review","partial"):
        _deny("b_run_state_not_bindable")
    if row["ga_state"] != "completed" or row["ga_result_state"] != "completed":
        _deny("a_analysis_not_complete")
    if row["stage_version"] != GA_ASSEMBLY or row["result_schema"] != GA_CONTRACT:
        raise DependencyContractMismatch("group_a")
    if (row["result_sha256"] != expected_sha256 or
        canonical_hash(row["snapshot"]) != expected_sha256):
        _deny("a_result_hash_mismatch")
    if row["content_kind"] == "image_post":
        _deny("image_post_not_supported")
    try:
        mapped_kind = content_type_for_kind(row["content_kind"])
    except ContractError:
        raise DependencyContractMismatch("group_a") from None
    if mapped_kind != row["content_type"]:
        _deny("a_content_kind_mismatch")
    analysis_id = row["video_a_analysis_id"] if row["content_type"] == "video" else row["carousel_a_analysis_id"]
    a_state = row["video_a_state"] if row["content_type"] == "video" else row["carousel_a_state"]
    if (not _positive(analysis_id) or a_state != "completed" or
        (row["video_a_analysis_id"] is not None and row["carousel_a_analysis_id"] is not None)):
        _deny("canonical_a_analysis_not_complete")
    if ((row["video_a_source"] if row["content_type"]=="video" else row["carousel_a_source"]) != row["source_id"] or
        (row["video_a_video"] if row["content_type"]=="video" else row["carousel_a_video"]) != row["video_id"]):
        _deny("canonical_a_analysis_lineage_mismatch")
    native_policy = row["video_a_policy"] if row["content_type"]=="video" else row["carousel_a_policy"]
    if native_policy != row["ga_policy"]:
        _deny("a_canonical_policy_mismatch")
    if row["content_type"] == "video":
        for stream_name,db_field in (
            ("technical","video_a_scene_status"),
            ("speech","video_a_speech_status"),
            ("screen_text","video_a_screen_text_status"),
            ("audio","video_a_audio_status"),
        ):
            if row[db_field] != "complete":
                _deny("a_canonical_" + stream_name + "_incomplete")
    else:
        if (row["carousel_a_kind"] != row["content_kind"] or
            row["carousel_a_slide_status"] != "complete" or
            row["carousel_a_text_status"] != "complete"):
            _deny("a_canonical_carousel_incomplete")
    if (row["b_sha"] not in (None,expected_sha256) or
        row["b_manifest"] not in (None,row["manifest_sha256"]) or
        row["b_policy"] not in (None,row["ga_policy"]) or
        row["b_video_a"] not in (None,row["video_a_analysis_id"]) or
        row["b_carousel_a"] not in (None,row["carousel_a_analysis_id"])):
        _deny("b_a_pin_conflict")
    if row["evidence"] != row["snapshot"].get("evidence") or row["method_version"] != row["snapshot"].get("method_version"):
        _deny("a_evidence_snapshot_mismatch")
    validated=validate_a_snapshot(row["snapshot"],project_id=row["project_id"],
        qdp_run_id=row["qdp_run_id"],video_id=row["video_id"],
        content_kind=row["content_kind"],input_sha256=row["qdp_sha"],
        manifest_sha256=row["manifest_sha256"],analysis_id=analysis_id)
    try:
        media = conn.execute(
            """SELECT mr.slide_index,mr.media_asset_id,mr.media_kind,mr.asset_sha256,
                      mr.video_id,mr.source_id,ma.sha256,ma.permission,ma.expires_at,
                      ma.media_kind,ma.video_id,ma.source_id
                 FROM ga_media_refs mr JOIN media_assets ma ON ma.id=mr.media_asset_id
                WHERE mr.run_id=%s::uuid ORDER BY mr.slide_index""",
            (row["ga_run_id"],)
        ).fetchall()
        rights = conn.execute(
            """SELECT 1 FROM sources s
                 JOIN source_policy_versions p ON p.id=%s AND p.source_id=s.id
                WHERE s.id=%s AND s.status='enabled' AND s.can_analyze_media
                  AND p.is_active AND p.permissions->>'can_analyze_media'='true'""",
            (row["ga_policy"],row["source_id"])
        ).fetchone()
    except Exception:
        _deny("a_media_or_policy_read_unavailable")
    if rights is None:
        _deny("a_media_rights_revoked")
    if not _positive(row["ga_policy"]) or type(row["expected_media_items"]) is not int:
        raise DependencyContractMismatch("group_a")
    if len(media) != row["expected_media_items"] or len(media)==0:
        _deny("a_media_coverage_incomplete")
    assets, hashes=set(),set()
    ids,digests=[],[]
    for index,item in enumerate(media,1):
        (slide,asset,kind,digest,video,source,current_digest,permission,
         expiry,current_kind,current_video,current_source)=item
        if (slide!=index or not _positive(asset) or asset in assets or
            not _digest(digest) or digest!=current_digest or video!=row["video_id"] or
            source!=row["source_id"] or current_video!=video or current_source!=source or
            permission in (None,"","metadata_only") or kind!=current_kind):
            _deny("a_media_pin_mismatch")
        if expiry is not None:
            now_ok=conn.execute("SELECT %s > now()", (expiry,)).fetchone()
            if now_ok is None or now_ok[0] is not True:
                _deny("a_media_expired")
        assets.add(asset);ids.append(asset);digests.append(digest)
    # An immutable A proof may refer only to a byte-pinned asset of this run.
    if any(e["asset_sha256"] not in set(digests) for e in validated["evidence"]):
        _deny("a_proof_media_hash_mismatch")
    if row["content_type"] == "video":
        if row["video_a_media_asset_id"] != ids[0]:
            _deny("a_canonical_media_asset_mismatch")
    else:
        try:
            actual_slides = conn.execute(
                """SELECT slide_index,item_type,media_asset_id
                     FROM carousel_feature_a_items
                    WHERE analysis_id=%s ORDER BY slide_index""",
                (analysis_id,)
            ).fetchall()
        except Exception:
            _deny("a_carousel_slides_unavailable")
        expected_slides = [(index+1, m[2], m[1]) for index,m in enumerate(media)]
        if [tuple(x) for x in actual_slides] != expected_slides:
            _deny("a_carousel_slide_mapping_mismatch")
    if row["content_type"]=="video" and (len(ids)!=1 or media[0][2]!="video"):
        _deny("a_video_media_invalid")
    if row["content_type"]=="carousel" and len(ids)<2:
        _deny("a_carousel_media_invalid")
    if row["content_kind"]=="photo_carousel" and any(x[2]!="image" for x in media):
        _deny("a_carousel_media_invalid")
    if row["content_kind"]=="mixed_carousel" and {x[2] for x in media}!={"image","video"}:
        _deny("a_carousel_media_invalid")
    return GroupAExactPin(
        project_id=row["project_id"],b_run_id=b_run_id,ga_run_id=row["ga_run_id"],
        ga_result_id=ga_result_id,a_analysis_id=analysis_id,
        content_type=row["content_type"],source_policy_version_id=row["ga_policy"],
        result_sha256=expected_sha256,manifest_sha256=row["manifest_sha256"],
        evidence_count=len(validated["evidence"]),
        media_asset_ids=tuple(ids),media_sha256s=tuple(digests)
    )
