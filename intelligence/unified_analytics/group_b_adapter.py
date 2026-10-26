"""UA-MOD-09 — source-backed exact Group B B1–B8 reader.

Requires current QDP B preparation + exact current Group A source facts via
injected reader, completed immutable B run/analysis, eight block states,
and re-reads every applicable B component in its owning 005 tables.

This is not an authenticated live producer signature or UAE qualification.
Future GB runtime/model results are unavailable on standalone main.
No automatic DB access, model call, scoring, inferred CTA or carousel timing.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any, Mapping
from uuid import UUID

from .contracts import AnalyticsBoundaryError, content_type_for_kind, canonical_hash
from .dto import EvidenceRead, EvidenceRef, InputScope, ReadState
from .group_a_adapter import GroupAVerifiedFactsReader
from .qdp_adapter import QDPQualifiedSetReader
from .ports import CapabilityState, PortCapability, PORT_SCHEMAS

B_READER_WIRE = "uae-group-b-exact/v1"  # LOCAL contract, NOT a claimed Group B event wire
BLOCKS = ("B1","B2","B3","B4","B5","B6","B7","B8")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_VER = re.compile(r"[A-Za-z0-9._:/-]{1,160}\Z")
REQUIRED_RELATIONS = (
    "group_b_runs", "group_b_inbox", "group_b_inbox_receipts",
    "group_b_block_states", "group_b_review_events", "content_b_analyses",
    "content_b_hooks", "content_b_hook_parts", "content_b_formats",
    "content_b_story_frames", "content_b_story_intervals",
    "content_b_story_slides", "content_b_story_sampling",
    "content_b_visual_content", "content_b_editing", "content_b_subtitles",
    "content_b_cta_analyses", "content_b_ctas",
    "content_b_topic_assignments", "content_b_topics",
)
PROBE_SQL = "SELECT " + ", ".join(
    f"to_regclass('public.{name}') AS {name}" for name in REQUIRED_RELATIONS
)
RUN_SQL = """
SELECT b.id::text AS run_id,b.project_id,b.video_id,b.source_id,
       b.source_policy_version_id,b.content_type,b.state AS run_state,
       b.video_a_analysis_id,b.carousel_a_analysis_id,
       b.a_snapshot_sha256,b.media_manifest_sha256,b.config_sha256,
       b.result_analysis_id,b.inbox_id,b.updated_at AS run_updated_at,
       i.status AS inbox_status,i.event_key,i.event_sha256,
       i.preparation_sha256,i.qdp_run_id::text AS qdp_run_id,
       i.producer_schema,
       rc.event_id::text AS receipt_event_id,
       rc.event_sha256 AS receipt_event_sha256,
       rc.preparation_sha256 AS receipt_preparation_sha256,
       a.id AS analysis_id,a.video_id AS analysis_video_id,
       a.source_id AS analysis_source_id,
       a.source_policy_version_id AS analysis_policy_id,
       a.video_a_analysis_id AS analysis_video_a_id,
       a.carousel_a_analysis_id AS analysis_carousel_a_id,
       a.content_type AS analysis_content_type,
       a.analysis_version,a.taxonomy_version,a.method_name,
       a.method_version,a.model_name,a.model_version,a.prompt_version,
       a.state AS analysis_state,a.needs_review,
       a.analyzed_at,a.unavailable_reasons,
       src.status AS source_state,src.can_analyze_media,
       pol.is_active AS policy_active,pol.permissions AS policy_permissions
FROM group_b_runs b
JOIN group_b_inbox i ON i.id=b.inbox_id AND i.project_id=b.project_id
 AND i.video_id=b.video_id AND i.source_id=b.source_id
LEFT JOIN group_b_inbox_receipts rc
  ON rc.project_id=b.project_id AND rc.inbox_id=i.id AND rc.run_id=b.id
LEFT JOIN content_b_analyses a ON a.id=b.result_analysis_id
 AND a.video_id=b.video_id AND a.source_id=b.source_id
JOIN sources src ON src.id=b.source_id
JOIN source_policy_versions pol ON pol.id=b.source_policy_version_id
 AND pol.source_id=b.source_id
WHERE b.id=%s::uuid AND b.project_id=%s AND b.video_id=%s
"""
BLOCK_SQL = """
SELECT block,status,evidence_sha256,error_code
FROM group_b_block_states WHERE project_id=%s AND run_id=%s::uuid
ORDER BY block
"""
REVIEW_SQL = """
SELECT revision,decision_sha256,decision
FROM group_b_review_events
WHERE project_id=%s AND run_id=%s::uuid
ORDER BY revision DESC LIMIT 1
"""
COMPONENT_SQL = {
    "B1": """SELECT h.classification_status,h.taxonomy_version,
      h.mechanisms,h.delivery,h.start_sec,h.end_sec,h.slide_index,
      (SELECT count(*) FROM content_b_hook_parts p WHERE p.analysis_id=h.analysis_id)
      AS parts
      FROM content_b_hooks h WHERE h.analysis_id=%s""",
    "B2": """SELECT classification_status,taxonomy_version,video_primary,
      video_secondary,carousel_format FROM content_b_formats WHERE analysis_id=%s""",
    "B3": """SELECT
      (SELECT count(*) FROM content_b_story_frames f WHERE f.analysis_id=%s) AS frames,
      (SELECT count(*) FROM content_b_story_intervals i WHERE i.analysis_id=%s) AS intervals,
      (SELECT count(*) FROM content_b_story_slides s WHERE s.analysis_id=%s) AS slides,
      (SELECT count(*) FROM content_b_story_sampling samp WHERE samp.analysis_id=%s)
      AS sampling_rows,
      (SELECT count(*) FROM content_b_story_frames f WHERE f.analysis_id=%s
       AND f.needs_review) AS frame_reviews,
      (SELECT count(*) FROM content_b_story_intervals i WHERE i.analysis_id=%s
       AND (i.needs_review OR i.boundary_quality='unknown')) AS interval_reviews,
      (SELECT count(*) FROM content_b_story_slides s WHERE s.analysis_id=%s
       AND s.needs_review) AS slide_reviews""",
    "B4": """SELECT ai_generated,person_visible,product_visible,logo_visible
      FROM content_b_visual_content WHERE analysis_id=%s""",
    "B5": """SELECT editing_type,cut_count,cuts_per_minute,avg_shot_duration,
      cuts_first_5_sec FROM content_b_editing WHERE analysis_id=%s""",
    "B6": """SELECT subtitle_type,position,font_size,font_style,text_color,
      highlight_color,background_style,display_mode
      FROM content_b_subtitles WHERE analysis_id=%s""",
    "B7": """SELECT c.analysis_status,c.taxonomy_version,c.analyzed_at,
      (SELECT count(*) FROM content_b_ctas x WHERE x.analysis_id=c.analysis_id)
      AS occurrence_count,
      (SELECT count(*) FROM content_b_ctas x WHERE x.analysis_id=c.analysis_id
       AND x.extraction_status='needs_review') AS review_count,
      (SELECT count(DISTINCT x.occurrence_index) FROM content_b_ctas x
       WHERE x.analysis_id=c.analysis_id) AS distinct_count,
      (SELECT max(x.occurrence_index) FROM content_b_ctas x
       WHERE x.analysis_id=c.analysis_id) AS last_occurrence
      FROM content_b_cta_analyses c WHERE c.analysis_id=%s""",
    "B8": """SELECT t.taxonomy_version,t.needs_review,t.category_id,t.topic_id,
      t.subtopic_id,c.category_id AS topic_category
      FROM content_b_topic_assignments t
      JOIN content_b_topics c ON c.id=t.topic_id
      WHERE t.analysis_id=%s""",
}
CAROUSEL_NONAPPLICABLE_SQL = """
SELECT (SELECT count(*) FROM content_b_editing WHERE analysis_id=%s)
           AS editing_rows,
       (SELECT count(*) FROM content_b_subtitles WHERE analysis_id=%s)
           AS subtitle_rows,
       (SELECT count(*) FROM content_b_story_frames WHERE analysis_id=%s)
           AS frame_rows,
       (SELECT count(*) FROM content_b_story_intervals WHERE analysis_id=%s)
           AS interval_rows
"""


def _reject(code: str) -> None:
    raise AnalyticsBoundaryError(code, "Group B verification rejected: " + code)


def _positive(x: Any) -> bool:
    return type(x) is int and x > 0


def _sha(x: Any) -> bool:
    return type(x) is str and bool(_SHA.fullmatch(x))


def _uuid(x: Any) -> bool:
    if type(x) is not str:
        return False
    try:
        return str(UUID(x)) == x
    except (ValueError, TypeError, AttributeError):
        return False


def _utc(x: Any) -> datetime | None:
    if type(x) is not datetime or x.tzinfo is None or x.utcoffset() is None:
        return None
    return x.astimezone(timezone.utc)


def _nonnegative(x: Any) -> bool:
    try:
        return not isinstance(x, bool) and Decimal(str(x)).is_finite() and Decimal(str(x)) >= 0
    except (ValueError, ArithmeticError):
        return False


def _positive_duration(x: Any) -> bool:
    try:
        d = Decimal(str(x))
        return not isinstance(x, bool) and d.is_finite() and d > 0
    except (ValueError, ArithmeticError):
        return False


def _db_one(conn: Any, sql: str, args: tuple = ()) -> dict | None:
    try:
        obj = conn.execute(sql, args).fetchone()
    except Exception as exc:
        raise AnalyticsBoundaryError("gb_database_unavailable", "Group B DB read failed") from exc
    if obj is None:
        return None
    if not isinstance(obj, Mapping):
        _reject("gb_db_row_invalid")
    return dict(obj)


def _db_many(conn: Any, sql: str, args: tuple) -> list[dict]:
    try:
        objects = conn.execute(sql, args).fetchall()
    except Exception as exc:
        raise AnalyticsBoundaryError("gb_database_unavailable", "Group B DB read failed") from exc
    if type(objects) is not list or any(not isinstance(x, Mapping) for x in objects):
        _reject("gb_db_row_invalid")
    return [dict(x) for x in objects]


@dataclass(frozen=True, slots=True)
class GroupBExactPins:
    scope: InputScope
    b_run_id: str
    b_analysis_id: int
    qdp_b_preparation_sha256: str
    group_a_analysis_id: int
    group_a_result_sha256: str
    group_a_media_manifest_sha256: str
    config_sha256: str
    analysis_version: str
    taxonomy_version: str
    method_version: str
    event_id: str
    event_sha256: str
    block_evidence_sha256s: tuple[str | None, ...]
    model_name: str | None = None
    model_version: str | None = None
    prompt_version: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.scope, InputScope) or not all(_uuid(v) for v in (
            self.b_run_id, self.event_id
        )):
            _reject("gb_invalid_pins")
        if not all(_positive(v) for v in (
            self.b_analysis_id, self.group_a_analysis_id
        )):
            _reject("gb_invalid_pins")
        if not all(_sha(v) for v in (
            self.qdp_b_preparation_sha256,self.group_a_result_sha256,
            self.group_a_media_manifest_sha256,self.config_sha256,self.event_sha256
        )):
            _reject("gb_invalid_pins")
        if any(type(v) is not str or not _VER.fullmatch(v) for v in (
            self.analysis_version,self.taxonomy_version,self.method_version
        )):
            _reject("gb_invalid_pins")
        if any(v is not None and (
            type(v) is not str or not _VER.fullmatch(v)
        ) for v in (self.model_name,self.model_version,self.prompt_version)):
            _reject("gb_invalid_pins")
        if (type(self.block_evidence_sha256s) is not tuple
            or len(self.block_evidence_sha256s) != 8
            or any(v is not None and not _sha(v)
                   for v in self.block_evidence_sha256s)):
            _reject("gb_invalid_block_pins")
        video = content_type_for_kind(self.scope.content_kind) == "video"
        for i, digest in enumerate(self.block_evidence_sha256s):
            if (not video and i in (4,5)):
                if digest is not None:
                    _reject("gb_nonapplicable_block_pins")
            elif not _sha(digest):
                _reject("gb_applicable_block_pins_missing")


class GroupBExactReader:
    """Structural GroupBReader; upstream A/QDP invoked on same connection."""

    def __init__(self, conn: Any, pins: GroupBExactPins,
                 group_a: GroupAVerifiedFactsReader,
                 qdp: QDPQualifiedSetReader, *, as_of: datetime):
        if not isinstance(pins, GroupBExactPins):
            _reject("gb_invalid_pins")
        if not isinstance(group_a, GroupAVerifiedFactsReader) or not isinstance(
            qdp, QDPQualifiedSetReader
        ):
            _reject("gb_dependencies_invalid")
        if (group_a._conn is not conn or qdp._conn is not conn
            or group_a._pins.scope != pins.scope or qdp._pins.scope != pins.scope
            or group_a._pins.analysis_id != pins.group_a_analysis_id
            or group_a._pins.result_sha256 != pins.group_a_result_sha256
            or group_a._pins.media_manifest_sha256 != pins.group_a_media_manifest_sha256
            or qdp._pins.preparation_shas[1] != pins.qdp_b_preparation_sha256):
            _reject("gb_upstream_pins_mismatch")
        stamp = _utc(as_of)
        if stamp is None or as_of.utcoffset().total_seconds() != 0:
            _reject("gb_invalid_time")
        self._conn,self._pins,self._group_a,self._qdp,self._as_of = (
            conn,pins,group_a,qdp,stamp
        )

    def capability(self) -> PortCapability:
        if self._conn is None or not callable(getattr(self._conn,"execute",None)):
            return PortCapability("group_b",CapabilityState.UNAVAILABLE,
                                  reason_code="not_connected")
        try:
            schema = _db_one(self._conn,PROBE_SQL)
        except AnalyticsBoundaryError:
            return PortCapability("group_b",CapabilityState.UNAVAILABLE,
                                  reason_code="gb_probe_failed")
        if schema is None or any(schema.get(name) is None for name in REQUIRED_RELATIONS):
            return PortCapability("group_b",CapabilityState.UNAVAILABLE,
                                  reason_code="gb_schema_missing")
        if (self._group_a.capability().state is not CapabilityState.DECLARED
            or self._qdp.capability().state is not CapabilityState.DECLARED):
            return PortCapability("group_b",CapabilityState.UNAVAILABLE,
                                  reason_code="gb_upstream_unavailable")
        return PortCapability("group_b",CapabilityState.DECLARED,
                              PORT_SCHEMAS["group_b"],B_READER_WIRE)

    @staticmethod
    def _missing(s: InputScope, reason: str) -> EvidenceRead:
        return EvidenceRead("group_b",s,ReadState.MISSING,reason_code=reason)

    def read_completed_analysis(self, scope: InputScope) -> EvidenceRead:
        pins = self._pins
        if not isinstance(scope, InputScope) or scope != pins.scope:
            _reject("gb_scope_mismatch")
        cap = self.capability()
        if cap.state is not CapabilityState.DECLARED:
            return EvidenceRead("group_b",scope,ReadState.UNAVAILABLE,
                                reason_code=cap.reason_code)
        a_fact = self._group_a.read_completed_analysis(scope)
        q_fact = self._qdp.read_qualified_membership(scope)
        if a_fact.state is not ReadState.LOCATED or q_fact.state is not ReadState.LOCATED:
            return self._missing(scope,"gb_upstream_no_longer_qualified")
        run = _db_one(self._conn,RUN_SQL,(
            pins.b_run_id,scope.project_id,scope.video_id
        ))
        if run is None:
            return self._missing(scope,"gb_completed_run_missing")
        video = content_type_for_kind(scope.content_kind) == "video"
        parent = "video_a_analysis_id" if video else "carousel_a_analysis_id"
        other = "carousel_a_analysis_id" if video else "video_a_analysis_id"
        a_parent = "analysis_video_a_id" if video else "analysis_carousel_a_id"
        a_other = "analysis_carousel_a_id" if video else "analysis_video_a_id"
        if (run.get("run_id") != pins.b_run_id
            or run.get("project_id") != scope.project_id
            or run.get("video_id") != scope.video_id
            or run.get("source_id") != scope.source_id
            or run.get("source_policy_version_id") != scope.source_policy_version_id
            or run.get("content_type") != content_type_for_kind(scope.content_kind)
            or run.get("run_state") != "completed"
            or run.get("result_analysis_id") != pins.b_analysis_id
            or run.get("analysis_id") != pins.b_analysis_id
            or run.get("analysis_video_id") != scope.video_id
            or run.get("analysis_source_id") != scope.source_id
            or run.get("analysis_policy_id") != scope.source_policy_version_id
            or run.get("analysis_content_type") != run.get("content_type")
            or run.get("analysis_state") != "completed"
            or run.get("needs_review") is not False
            or run.get(parent) != pins.group_a_analysis_id
            or run.get(other) is not None
            or run.get(a_parent) != pins.group_a_analysis_id
            or run.get(a_other) is not None
            or run.get("a_snapshot_sha256") != pins.group_a_result_sha256
            or run.get("media_manifest_sha256") != pins.group_a_media_manifest_sha256
            or run.get("config_sha256") != pins.config_sha256
            or run.get("analysis_version") != pins.analysis_version
            or run.get("taxonomy_version") != pins.taxonomy_version
            or run.get("method_version") != pins.method_version
            or run.get("model_name") != pins.model_name
            or run.get("model_version") != pins.model_version
            or run.get("prompt_version") != pins.prompt_version
            or type(run.get("method_name")) is not str
            or not run["method_name"]
            or run.get("inbox_status") != "accepted"
            or run.get("qdp_run_id") != scope.qdp_run_id
            or run.get("preparation_sha256") != pins.qdp_b_preparation_sha256
            or run.get("event_key") != pins.event_id
            or run.get("receipt_event_id") != pins.event_id
            or run.get("event_sha256") != pins.event_sha256
            or run.get("receipt_event_sha256") != pins.event_sha256
            or run.get("receipt_preparation_sha256") != pins.qdp_b_preparation_sha256
            or run.get("producer_schema") != "qdp-handoff/v1"):
            return self._missing(scope,"gb_exact_run_analysis_or_receipt_mismatch")
        if (run.get("source_state") != "enabled"
            or run.get("can_analyze_media") is not True
            or run.get("policy_active") is not True
            or type(run.get("policy_permissions")) is not dict
            or run["policy_permissions"].get("can_analyze_media") is not True):
            return self._missing(scope,"gb_current_source_rights_missing")
        analyzed = _utc(run.get("analyzed_at"))
        updated = _utc(run.get("run_updated_at"))
        if (analyzed is None or updated is None or analyzed > self._as_of
            or updated > self._as_of):
            return self._missing(scope,"gb_analysis_time_invalid")
        if run.get("unavailable_reasons") != {}:
            return self._missing(scope,"gb_unavailable_measurements")
        review = _db_one(self._conn,REVIEW_SQL,(scope.project_id,pins.b_run_id))
        if review is not None:
            # No documented contract yet equates B's generic review JSON to
            # approved B1–B8 outputs. A present review means a possibly
            # superseding human decision; do not invent approval semantics.
            return self._missing(scope,"gb_human_review_unresolved")
        blocks = _db_many(self._conn,BLOCK_SQL,(scope.project_id,pins.b_run_id))
        if len(blocks) != 8 or [r.get("block") for r in blocks] != list(BLOCKS):
            return self._missing(scope,"gb_block_matrix_incomplete")
        for index,b in enumerate(blocks):
            skipped = (not video and b["block"] in ("B5","B6"))
            if skipped:
                if (b.get("status") not in ("not_run","unavailable")
                    or b.get("evidence_sha256") is not None
                    or b.get("error_code") not in (None,"editing_video_only",
                                                   "video_subtitles_only")):
                    return self._missing(scope,"gb_nonapplicable_block_fabricated")
            elif (b.get("status") != "complete"
                  or not _sha(b.get("evidence_sha256"))
                  or b.get("error_code") is not None):
                return self._missing(scope,"gb_applicable_block_incomplete")
            if b.get("evidence_sha256") != pins.block_evidence_sha256s[index]:
                return self._missing(scope,"gb_block_digest_pin_mismatch")

        results: dict[str,dict] = {}
        for block in BLOCKS:
            if not video and block in ("B5","B6"):
                continue
            args = (pins.b_analysis_id,)* (7 if block=="B3" else 1)
            fact = _db_one(self._conn,COMPONENT_SQL[block],args)
            if fact is None or not self._valid_component(block,fact,video,pins):
                return self._missing(scope,"gb_"+block.lower()+"_fact_incomplete")
            if block=="B3" and not video and (
                fact["slides"] != self._group_a._pins.expected_media_items
            ):
                return self._missing(scope,"gb_b3_slide_coverage_mismatch")
            results[block] = fact
        if not video:
            counts = _db_one(self._conn,CAROUSEL_NONAPPLICABLE_SQL,
                             (pins.b_analysis_id,)*4)
            if counts is None or any(
                counts.get(k) != 0 for k in (
                    "editing_rows","subtitle_rows","frame_rows","interval_rows"
                )
            ):
                return self._missing(scope,"gb_carousel_video_only_rows_present")
        digest = canonical_hash({
            "contract":B_READER_WIRE,
            "scope":scope.scope_sha256,"b_run_id":pins.b_run_id,
            "b_analysis_id":pins.b_analysis_id,
            "a_lineage":a_fact.reference.lineage_sha256,
            "qdp_lineage":q_fact.reference.lineage_sha256,
            "a_result_sha256":pins.group_a_result_sha256,
            "b_preparation_sha256":pins.qdp_b_preparation_sha256,
            "analysis_version":pins.analysis_version,
            "method_version":pins.method_version,
            "taxonomy_version":pins.taxonomy_version,
            "model_version":run.get("model_version"),
            "prompt_version":run.get("prompt_version"),
            "blocks":[[r["block"],r["status"],r.get("evidence_sha256")]
                      for r in blocks],
            "component_hashes":{k:_json_digest(v) for k,v in results.items()},
        })
        return EvidenceRead("group_b",scope,ReadState.LOCATED,reference=EvidenceRef(
            scope=scope,port="group_b",
            record_key="gb:"+pins.b_run_id+":"+str(pins.b_analysis_id),
            producer_schema=B_READER_WIRE,
            producer_version=pins.analysis_version,
            payload_sha256=digest,
        ))

    def _valid_component(self,block: str,row: dict,video: bool,
                         pins: GroupBExactPins) -> bool:
        tax = row.get("taxonomy_version")
        if block in ("B1","B2","B7","B8") and tax != pins.taxonomy_version:
            return False
        if block=="B1":
            if row.get("classification_status") != "inferred":
                return False
            if (type(row.get("mechanisms")) is not list
                or not row["mechanisms"] or len(row["mechanisms"]) != len(set(row["mechanisms"]))
                or type(row.get("delivery")) is not list
                or not row["delivery"] or len(row["delivery"]) != len(set(row["delivery"]))):
                return False
            return ((video and row.get("slide_index") is None and
                     _nonnegative(row.get("start_sec"))
                     and _positive_duration(row.get("end_sec"))
                     and Decimal(str(row["end_sec"])) > Decimal(str(row["start_sec"])))
                    or (not video and type(row.get("slide_index")) is int
                        and row["slide_index"]>=1
                        and row.get("start_sec") is None and row.get("end_sec") is None))
        if block=="B2":
            if row.get("classification_status") != "inferred":
                return False
            return (bool(row.get("video_primary")) and row.get("carousel_format") is None
                    if video else bool(row.get("carousel_format"))
                    and row.get("video_primary") is None
                    and row.get("video_secondary") is None)
        if block=="B3":
            if any(type(row.get(k)) is not int or row[k]<0 for k in (
                "frames","intervals","slides","sampling_rows","frame_reviews","interval_reviews",
                "slide_reviews"
            )):
                return False
            if any(row[k] for k in ("frame_reviews","interval_reviews","slide_reviews")):
                return False
            return (row["frames"]+row["intervals"]>0 and row["slides"]==0
                    and row["sampling_rows"]==1 if video else row["slides"]>0 and
                    row["frames"]==0 and row["intervals"]==0
                    and row["sampling_rows"]==0)
        if block=="B4":
            return all(type(row.get(k)) is bool for k in (
                "person_visible","product_visible","logo_visible"
            )) and (row.get("ai_generated") is None or
                    type(row["ai_generated"]) is bool)
        if block=="B5":
            return (row.get("editing_type") in (
                "single_take","slow_cuts","medium_cuts","fast_cuts"
            ) and type(row.get("cut_count")) is int
                    and row["cut_count"]>=0 and
                    _nonnegative(row.get("cuts_per_minute")) and
                    type(row.get("cuts_first_5_sec")) is int and
                    0 <= row["cuts_first_5_sec"] <= row["cut_count"] and
                    _positive_duration(row.get("avg_shot_duration")))
        if block=="B6":
            typ = row.get("subtitle_type")
            return (typ in ("none","standard","dynamic") and (
                all(row.get(k) is None for k in (
                    "position","font_size","font_style","text_color",
                    "highlight_color","background_style","display_mode"
                )) if typ=="none" else all(row.get(k) is not None for k in (
                    "position","font_size","font_style","text_color",
                    "background_style","display_mode"
                ))))
        if block=="B7":
            count=row.get("occurrence_count")
            return (row.get("analysis_status")=="complete"
                and _utc(row.get("analyzed_at")) is not None
                and _utc(row["analyzed_at"])<=self._as_of
                and type(count) is int and count>=0
                and row.get("review_count")==0
                and row.get("distinct_count")==count
                and (row.get("last_occurrence") is None if count==0
                     else row.get("last_occurrence")==count))
        if block=="B8":
            return (row.get("needs_review") is False
                    and _positive(row.get("category_id"))
                    and _positive(row.get("topic_id"))
                    and row.get("topic_category")==row["category_id"]
                    and (row.get("subtopic_id") is None or
                         _positive(row["subtopic_id"])))
        return False


def _json_digest(obj: dict) -> str:
    """Never treat a JSON serialization hash as a producer signature."""
    try:
        raw = json.dumps(obj,sort_keys=True,ensure_ascii=False,
                         separators=(",",":"),allow_nan=False,default=_decimal_json).encode()
    except (TypeError,ValueError,OverflowError) as exc:
        raise AnalyticsBoundaryError("gb_json_invalid", "Group B fact cannot be hashed") from exc
    return hashlib.sha256(raw).hexdigest()


def _decimal_json(x: Any) -> Any:
    if isinstance(x,Decimal):
        return str(x)
    raise TypeError("Unsupported Group B field")


__all__ = (
    "GroupBExactPins","GroupBExactReader","B_READER_WIRE","BLOCKS",
    "PROBE_SQL","RUN_SQL","BLOCK_SQL","REVIEW_SQL","COMPONENT_SQL",
    "CAROUSEL_NONAPPLICABLE_SQL",
)
