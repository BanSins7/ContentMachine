"""UA-MOD-08 Group A exact versioned, current-source, measured-facts reader.

Group A owns A003/A004 measurements and GA054 assembled results. A completed
run alone is NOT evidence. Current QDP membership and the A preparation are
read with the same injected connection, then the producer's exact run/result,
A003/A004, media refs, steps and stream evidence are all checked.

This adapter verifies stored DB evidence/asset hashes, NOT media bytes on disk.
Actual byte revalidation and complete QDP carousel support remain upstream
obligations. It never imports peer code, invents swipe timings or computes C.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from uuid import UUID

from .contracts import AnalyticsBoundaryError, canonical_hash
from .dto import EvidenceRead, EvidenceRef, InputScope, ReadState
from .ports import CapabilityState, PORT_SCHEMAS, PortCapability
from .qdp_adapter import QDPQualifiedSetReader

GA_PEER_WIRE = "group-a-to-b/v1"
_STREAMS_VIDEO = ("technical", "speech", "screen_text", "audio", "subtitles")
_STREAMS_CAROUSEL = ("slides", "screen_text")
_STREAMS_MIXED = ("slides", "technical", "speech", "screen_text", "audio", "subtitles")
_STREAM_EVIDENCE = {
    "technical": "scene", "speech": "transcript", "screen_text": "screen_text",
    "audio": "audio", "subtitles": "screen_text", "slides": "slide",
}
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"[A-Za-z0-9._:/-]{1,160}\Z")
REQUIRED_RELATIONS = (
    "ga_runs", "ga_results", "ga_media_refs", "ga_steps",
    "ga_local_readable_results", "video_feature_a_analyses",
    "video_feature_a_scenes", "video_feature_a_speech_segments",
    "video_feature_a_screen_text", "carousel_feature_a_analyses",
    "carousel_feature_a_items", "media_assets",
)
PROBE_SQL = "SELECT " + ", ".join(
    f"to_regclass('public.{name}') AS {name}" for name in REQUIRED_RELATIONS
)

GA_RESULT_SQL = """
SELECT r.id::text AS ga_run_id,r.project_id,r.qdp_run_id::text AS qdp_run_id,
       r.video_id,r.source_id,r.source_policy_version_id,
       r.qdp_input_sha256,r.manifest_sha256,r.content_kind,
       r.stage_version,r.state AS ga_state,r.expected_media_items,
       r.invalidated_at,
       o.id AS result_id,o.state AS result_state,o.result_schema,
       o.result_sha256,o.method_version,o.snapshot,o.evidence,
       o.video_a_analysis_id,o.carousel_a_analysis_id,
       v.result_id AS visible_result_id
FROM ga_runs r
JOIN ga_results o ON o.run_id=r.id AND o.project_id=r.project_id
 AND o.video_id=r.video_id AND o.source_id=r.source_id
JOIN ga_local_readable_results v ON v.run_id=r.id
 AND v.result_id=o.id AND v.project_id=r.project_id
WHERE r.id=%s::uuid AND r.project_id=%s AND r.video_id=%s
 AND o.id=%s
"""
A_VIDEO_SQL = """
SELECT a.id,a.video_id,a.source_id,a.source_policy_version_id,
       a.media_asset_id,a.analysis_version,a.state,a.scene_status,
       a.speech_status,a.screen_text_status,a.audio_status,
       a.measured_at,a.duration_sec,a.has_subtitles,
       (SELECT count(*) FROM video_feature_a_scenes s
         WHERE s.analysis_id=a.id) AS scene_count
FROM video_feature_a_analyses a WHERE a.id=%s
"""
A_CAROUSEL_SQL = """
SELECT a.id,a.video_id,a.source_id,a.source_policy_version_id,
       a.analysis_version,a.content_kind,a.state,a.slide_status,
       a.text_status,a.audio_status,a.measured_at,a.has_audio,
       (SELECT count(*) FROM carousel_feature_a_items i
         WHERE i.analysis_id=a.id) AS slide_count
FROM carousel_feature_a_analyses a WHERE a.id=%s
"""
MEDIA_SQL = """
SELECT m.slide_index,m.media_asset_id,m.media_kind,m.asset_sha256,
       m.byte_size,m.verified_at,
       a.id AS actual_asset_id,a.video_id AS asset_video_id,
       a.source_id AS asset_source_id,a.media_kind AS asset_kind,
       a.sha256 AS actual_sha256,a.permission AS asset_permission,
       a.expires_at AS asset_expires_at
FROM ga_media_refs m
LEFT JOIN media_assets a ON a.id=m.media_asset_id
WHERE m.run_id=%s::uuid AND m.project_id=%s
 AND m.video_id=%s AND m.source_id=%s
ORDER BY m.slide_index
"""
STEPS_SQL = """
SELECT step_key,step_kind,state,input_sha256,output_sha256,finished_at
FROM ga_steps WHERE run_id=%s::uuid AND project_id=%s
ORDER BY step_key
"""
SCENES_SQL = """
SELECT scene_index,start_sec,end_sec FROM video_feature_a_scenes
WHERE analysis_id=%s ORDER BY scene_index
"""
SLIDES_SQL = """
SELECT slide_index,item_type,media_asset_id,video_a_analysis_id,
       text_status,on_screen_text,screen_word_count,duration_sec
FROM carousel_feature_a_items WHERE analysis_id=%s ORDER BY slide_index
"""


def _sha(x: object) -> bool:
    return type(x) is str and bool(_SHA.fullmatch(x))


def _positive(x: object) -> bool:
    return type(x) is int and x > 0


def _uuid(x: object) -> bool:
    if type(x) is not str:
        return False
    try:
        return str(UUID(x)) == x
    except (ValueError, TypeError, AttributeError):
        return False


def _utc(x: object) -> datetime | None:
    if type(x) is not datetime or x.tzinfo is None or x.utcoffset() is None:
        return None
    return x.astimezone(timezone.utc)


def _num(x: object, *, zero: bool = False) -> Decimal | None:
    if isinstance(x, bool) or type(x) not in (int, float, str, Decimal):
        return None
    try:
        d = Decimal(str(x))
    except (InvalidOperation, ValueError):
        return None
    return d if d.is_finite() and (d >= 0 if zero else d > 0) else None


def _hash_json(data: object) -> str | None:
    try:
        value = json.dumps(data, sort_keys=True, ensure_ascii=False,
                           separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (ValueError, TypeError, OverflowError):
        return None
    return hashlib.sha256(value).hexdigest()


def _one(conn: Any, sql: str, args: tuple[Any, ...] = ()) -> dict | None:
    try:
        r = conn.execute(sql, args).fetchone()
    except Exception as exc:
        raise AnalyticsBoundaryError("ga_read_failed", "Group A DB read failed") from exc
    if r is None:
        return None
    if not isinstance(r, Mapping):
        raise AnalyticsBoundaryError("ga_invalid_row", "Group A needs dict-row DB")
    return dict(r)


def _many(conn: Any, sql: str, args: tuple[Any, ...]) -> list[dict]:
    try:
        rows = conn.execute(sql, args).fetchall()
    except Exception as exc:
        raise AnalyticsBoundaryError("ga_read_failed", "Group A DB read failed") from exc
    if type(rows) is not list or any(not isinstance(r, Mapping) for r in rows):
        raise AnalyticsBoundaryError("ga_invalid_row", "Group A needs dict rows")
    return [dict(row) for row in rows]


@dataclass(frozen=True, slots=True)
class GroupAVerifiedPins:
    scope: InputScope
    ga_run_id: str
    ga_result_id: int
    analysis_id: int
    qdp_a_preparation_sha256: str
    media_manifest_sha256: str
    result_sha256: str
    analysis_version: str
    method_version: str
    expected_media_items: int

    def __post_init__(self) -> None:
        if not isinstance(self.scope, InputScope) or not _uuid(self.ga_run_id):
            raise AnalyticsBoundaryError("ga_invalid_pins", "Invalid Group A scope/run")
        if not all(_positive(v) for v in (
            self.ga_result_id, self.analysis_id, self.expected_media_items
        )) or self.expected_media_items > 100:
            raise AnalyticsBoundaryError("ga_invalid_pins", "Invalid Group A IDs")
        for d in (self.qdp_a_preparation_sha256, self.media_manifest_sha256,
                  self.result_sha256):
            if not _sha(d):
                raise AnalyticsBoundaryError("ga_invalid_pins", "Invalid Group A digest")
        for v in (self.analysis_version, self.method_version):
            if type(v) is not str or not _VERSION.fullmatch(v):
                raise AnalyticsBoundaryError("ga_invalid_pins", "Invalid Group A version")
        if self.scope.content_kind in ("video", "slideshow_video"):
            if self.expected_media_items != 1:
                raise AnalyticsBoundaryError("ga_invalid_pins", "Video must pin one asset")
        elif self.expected_media_items < 2:
            raise AnalyticsBoundaryError("ga_invalid_pins", "Carousel requires two slides")


class GroupAVerifiedFactsReader:
    """Implements GroupAReader with source-backed QDP dependency injection.

    Does not accept a user-constructed EvidenceRead as authorization. It
    invokes the QDP adapter with the same DB and scope at read time.
    """

    def __init__(self, conn: Any, pins: GroupAVerifiedPins,
                 qdp: QDPQualifiedSetReader, *, as_of: datetime):
        if not isinstance(pins, GroupAVerifiedPins):
            raise AnalyticsBoundaryError("ga_invalid_pins", "GroupAVerifiedPins required")
        if not isinstance(qdp, QDPQualifiedSetReader):
            raise AnalyticsBoundaryError("ga_qdp_dependency_invalid", "QDPReader required")
        if qdp._conn is not conn or qdp._pins.scope != pins.scope or (
                qdp._pins.preparation_shas[0] != pins.qdp_a_preparation_sha256):
            raise AnalyticsBoundaryError("ga_qdp_lineage_mismatch", "QDP A preparation mismatch")
        instant = _utc(as_of)
        if instant is None or as_of.utcoffset().total_seconds() != 0:
            raise AnalyticsBoundaryError("ga_invalid_time", "UTC as_of required")
        self._conn, self._pins, self._qdp, self._as_of = conn, pins, qdp, instant

    def capability(self) -> PortCapability:
        if self._conn is None or not callable(getattr(self._conn, "execute", None)):
            return PortCapability("group_a", CapabilityState.UNAVAILABLE,
                                  reason_code="not_connected")
        try:
            available = _one(self._conn, PROBE_SQL)
        except AnalyticsBoundaryError:
            return PortCapability("group_a", CapabilityState.UNAVAILABLE,
                                  reason_code="ga_probe_failed")
        if available is None or any(available.get(k) is None for k in REQUIRED_RELATIONS):
            return PortCapability("group_a", CapabilityState.UNAVAILABLE,
                                  reason_code="ga_schema_missing")
        qdp = self._qdp.capability()
        if qdp.state is not CapabilityState.DECLARED:
            return PortCapability("group_a", CapabilityState.UNAVAILABLE,
                                  reason_code="ga_qdp_unavailable")
        return PortCapability("group_a", CapabilityState.DECLARED,
                              PORT_SCHEMAS["group_a"], GA_PEER_WIRE)

    @staticmethod
    def _missing(scope: InputScope, reason: str) -> EvidenceRead:
        return EvidenceRead("group_a", scope, ReadState.MISSING, reason_code=reason)

    def read_completed_analysis(self, scope: InputScope) -> EvidenceRead:
        p = self._pins
        if not isinstance(scope, InputScope) or scope != p.scope:
            raise AnalyticsBoundaryError("ga_scope_mismatch", "Wrong Group A project/source")
        cap = self.capability()
        if cap.state is not CapabilityState.DECLARED:
            return EvidenceRead("group_a", scope, ReadState.UNAVAILABLE,
                                reason_code=cap.reason_code)
        qualified = self._qdp.read_qualified_membership(scope)
        if qualified.state is not ReadState.LOCATED:
            return self._missing(scope, "ga_qdp_not_current")
        run = _one(self._conn, GA_RESULT_SQL,
                   (p.ga_run_id, scope.project_id, scope.video_id, p.ga_result_id))
        if run is None:
            return self._missing(scope, "ga_result_missing_or_revoked")
        video = scope.content_kind in ("video", "slideshow_video")
        selected = "video_a_analysis_id" if video else "carousel_a_analysis_id"
        excluded = "carousel_a_analysis_id" if video else "video_a_analysis_id"
        if (run.get("ga_run_id") != p.ga_run_id
            or run.get("project_id") != scope.project_id
            or str(run.get("qdp_run_id")) != scope.qdp_run_id
            or run.get("video_id") != scope.video_id
            or run.get("source_id") != scope.source_id
            or run.get("source_policy_version_id") != scope.source_policy_version_id
            or run.get("qdp_input_sha256") != p.qdp_a_preparation_sha256
            or run.get("manifest_sha256") != p.media_manifest_sha256
            or run.get("content_kind") != scope.content_kind
            or run.get("stage_version") != "group-a-assembly/v1"
            or run.get("ga_state") != "completed"
            or run.get("invalidated_at") is not None
            or run.get("result_id") != p.ga_result_id
            or run.get("visible_result_id") != p.ga_result_id
            or run.get("result_state") != "completed"
            or run.get("result_schema") != GA_PEER_WIRE
            or run.get("result_sha256") != p.result_sha256
            or run.get("method_version") != p.method_version
            or run.get("expected_media_items") != p.expected_media_items
            or run.get(selected) != p.analysis_id
            or run.get(excluded) is not None):
            return self._missing(scope, "ga_exact_run_or_result_mismatch")
        snap = run.get("snapshot")
        ev = run.get("evidence")
        if (type(snap) is not dict or type(ev) is not list
                or not self._snapshot_valid(snap, ev, p, video)):
            return self._missing(scope, "ga_stream_or_snapshot_incomplete")
        a = _one(self._conn, A_VIDEO_SQL if video else A_CAROUSEL_SQL,
                 (p.analysis_id,))
        if a is None or (
                a.get("id") != p.analysis_id or a.get("video_id") != scope.video_id
                or a.get("source_id") != scope.source_id
                or a.get("source_policy_version_id") != scope.source_policy_version_id
                or a.get("analysis_version") != p.analysis_version
                or a.get("state") != "completed"
                or _utc(a.get("measured_at")) is None
                or _utc(a["measured_at"]) > self._as_of):
            return self._missing(scope, "ga_a003_a004_version_missing")
        media = _many(self._conn, MEDIA_SQL,
                      (p.ga_run_id, scope.project_id, scope.video_id, scope.source_id))
        if not self._media_valid(media, p, video):
            return self._missing(scope, "ga_media_manifest_mismatch")
        if video and a.get("media_asset_id") != media[0]["media_asset_id"]:
            return self._missing(scope, "ga_a003_media_mismatch")
        if not video and a.get("content_kind") != scope.content_kind:
            return self._missing(scope, "ga_a004_kind_mismatch")
        steps = _many(self._conn, STEPS_SQL, (p.ga_run_id, scope.project_id))
        if not steps or any(
                step.get("state") != "completed"
                or not _sha(step.get("input_sha256"))
                or not _sha(step.get("output_sha256"))
                or _utc(step.get("finished_at")) is None
                or _utc(step["finished_at"]) > self._as_of
                for step in steps):
            return self._missing(scope, "ga_steps_unfinished")
        if video:
            if not self._video_measured(a):
                return self._missing(scope, "ga_video_streams_incomplete")
            scenes = _many(self._conn, SCENES_SQL, (p.analysis_id,))
            if not self._scenes_valid(scenes, a):
                return self._missing(scope, "ga_scenes_incomplete")
        else:
            if not self._carousel_measured(a):
                return self._missing(scope, "ga_carousel_streams_incomplete")
            slides = _many(self._conn, SLIDES_SQL, (p.analysis_id,))
            if not self._slides_valid(slides, media, scope.content_kind):
                return self._missing(scope, "ga_slide_positions_incomplete")
        digest = canonical_hash({
            "wire": GA_PEER_WIRE,
            "run_id": p.ga_run_id, "result_id": p.ga_result_id,
            "analysis_id": p.analysis_id, "result_sha256": p.result_sha256,
            "manifest_sha256": p.media_manifest_sha256,
            "qdp_a_sha256": p.qdp_a_preparation_sha256,
            "qdp_lineage_sha256": qualified.reference.lineage_sha256,
            "content_kind": scope.content_kind,
            "analysis_version": p.analysis_version,
            "method_version": p.method_version,
            "media_sha256": [row["asset_sha256"] for row in media],
            "step_sha256": [row["output_sha256"] for row in steps],
            "snapshot_sha256": _hash_json(snap),
            "stream_evidence_sha256": _hash_json(ev),
        })
        return EvidenceRead("group_a", scope, ReadState.LOCATED,
                            reference=EvidenceRef(
                                scope=scope, port="group_a",
                                record_key=f"ga:{p.ga_run_id}:{p.ga_result_id}",
                                producer_schema=GA_PEER_WIRE,
                                producer_version=p.analysis_version,
                                payload_sha256=digest))

    @staticmethod
    def _snapshot_valid(s: dict, evidence: list, p: GroupAVerifiedPins,
                        video: bool) -> bool:
        expected = _STREAMS_VIDEO if video else (
            _STREAMS_MIXED if p.scope.content_kind == "mixed_carousel"
            else _STREAMS_CAROUSEL
        )
        if (s.get("schema_version") != GA_PEER_WIRE
                or s.get("project_id") != p.scope.project_id
                or str(s.get("qdp_run_id")) != p.scope.qdp_run_id
                or s.get("video_id") != p.scope.video_id
                or s.get("qdp_input_sha256") != p.qdp_a_preparation_sha256
                or s.get("manifest_sha256") != p.media_manifest_sha256
                or s.get("content_kind") != p.scope.content_kind
                or s.get("analysis_state") != "completed"
                or s.get("method_version") != p.method_version
                or s.get("video_a_analysis_id") != (p.analysis_id if video else None)
                or s.get("carousel_a_analysis_id") != (None if video else p.analysis_id)
                or s.get("evidence") != evidence
                or _hash_json(s) != p.result_sha256):
            return False
        streams = s.get("streams")
        if type(streams) is not dict or set(streams) != set(expected):
            return False
        if len(evidence) > 2000 or any(
                type(x) is not dict
                or set(x) != {"kind", "ref_sha256", "asset_sha256"}
                or x.get("kind") not in ("scene","transcript","screen_text","audio","frame","slide")
                or not _sha(x.get("ref_sha256")) or not _sha(x.get("asset_sha256"))
                for x in evidence):
            return False
        for name, truth in streams.items():
            if type(truth) is not dict or set(truth) != (
                {"status","evidence_count","verified_absence","reason_code"}
            ):
                return False
            count = truth["evidence_count"]
            if (truth.get("status") != "complete"
                    or type(count) is not int or count < 0
                    or type(truth["verified_absence"]) is not bool
                    or truth["reason_code"] is not None
                    or truth["verified_absence"] != (count == 0)):
                return False
            provided = sum(
                e["kind"] == _STREAM_EVIDENCE[name] for e in evidence
            )
            if provided < max(1, count):
                return False
        return True

    def _media_valid(self, media: list, pins: GroupAVerifiedPins,
                     video: bool) -> bool:
        if len(media) != pins.expected_media_items:
            return False
        seen: set[int] = set()
        kinds = []
        for idx,row in enumerate(media, 1):
            asset = row.get("media_asset_id")
            if (row.get("slide_index") != idx or not _positive(asset)
                or asset in seen or row.get("actual_asset_id") != asset
                or row.get("asset_video_id") != pins.scope.video_id
                or row.get("asset_source_id") != pins.scope.source_id
                or not _sha(row.get("asset_sha256"))
                or row.get("asset_sha256") != row.get("actual_sha256")
                or row.get("asset_kind") != row.get("media_kind")
                or type(row.get("byte_size")) is not int
                or not 1 <= row["byte_size"] <= 2 * 1024 ** 3
                or row.get("asset_permission") in (None, "", "metadata_only")
                or _utc(row.get("verified_at")) is None
                or _utc(row["verified_at"]) > self._as_of):
                return False
            expiry = row.get("asset_expires_at")
            if expiry is not None and (_utc(expiry) is None
                                       or _utc(expiry) <= self._as_of):
                return False
            seen.add(asset)
            kinds.append(row["media_kind"])
        if video:
            return kinds == ["video"]
        if pins.scope.content_kind == "photo_carousel":
            return all(kind == "image" for kind in kinds)
        return "video" in kinds and "image" in kinds

    @staticmethod
    def _video_measured(a: dict) -> bool:
        return (all(a.get(field) == "complete" for field in (
            "scene_status", "speech_status", "screen_text_status", "audio_status"))
            and a.get("has_subtitles") in ("true","false")
            and _num(a.get("duration_sec")) is not None
            and _positive(a.get("scene_count")))

    @staticmethod
    def _carousel_measured(a: dict) -> bool:
        return (a.get("slide_status") == "complete"
                and a.get("text_status") == "complete"
                and a.get("audio_status") == "complete"
                and a.get("has_audio") in ("true","false")
                and _positive(a.get("slide_count")))

    @staticmethod
    def _scenes_valid(rows: list, a: dict) -> bool:
        if len(rows) != a.get("scene_count") or not rows:
            return False
        duration = _num(a.get("duration_sec"))
        if duration is None:
            return False
        last = Decimal(0)
        for i,row in enumerate(rows, 1):
            start = _num(row.get("start_sec"), zero=True)
            end = _num(row.get("end_sec"))
            if (row.get("scene_index") != i or start is None or end is None
                    or start < last or end <= start or end > duration):
                return False
            last = end
        return True

    @staticmethod
    def _slides_valid(rows: list, media: list, kind: str) -> bool:
        if len(rows) != len(media) or len(rows) < 2:
            return False
        for i,(slide,asset) in enumerate(zip(rows,media),1):
            if (slide.get("slide_index") != i
                    or slide.get("media_asset_id") != asset["media_asset_id"]
                    or slide.get("item_type") != asset["media_kind"]
                    or slide.get("text_status") != "complete"
                    or type(slide.get("on_screen_text")) is not str
                    or type(slide.get("screen_word_count")) is not int
                    or slide["screen_word_count"] < 0):
                return False
            if slide["item_type"] == "image":
                if slide.get("video_a_analysis_id") is not None or slide.get("duration_sec") is not None:
                    return False
            elif slide.get("duration_sec") is not None and _num(slide["duration_sec"]) is None:
                return False
        return kind == "mixed_carousel" or all(
            s["item_type"] == "image" for s in rows
        )


__all__ = (
    "GA_PEER_WIRE", "GroupAVerifiedPins", "GroupAVerifiedFactsReader",
    "REQUIRED_RELATIONS", "PROBE_SQL", "GA_RESULT_SQL", "A_VIDEO_SQL",
    "A_CAROUSEL_SQL", "MEDIA_SQL", "STEPS_SQL", "SCENES_SQL", "SLIDES_SQL",
)
