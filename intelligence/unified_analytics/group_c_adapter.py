"""UA-MOD-10 — persisted, source-backed Group C C1–C7 verification.

Never recompute C1–C7, cap Breakout Score at 100, or infer missing cohort
approval. Group C 057 only supports "frozen_unreviewed": no real signed
approval protocol exists yet. An integrity-checked score is inspectable as
unapproved facts but NEVER emits a qualified EvidenceRef via this port.

Caller injects a project-authorized dict-row PG connection and performs the
whole read in a single repeatable-read transaction; no automatic DB access.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Mapping
from uuid import UUID

from .contracts import AnalyticsBoundaryError, canonical_hash
from .dto import EvidenceRead, InputScope, ReadState
from .ports import CapabilityState, PortCapability, PORT_SCHEMAS
from .qdp_adapter import QDPQualifiedSetReader

C_READER_WIRE = "uae-group-c-persisted/v1"  # local reader revision, NOT a C producer signature
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(r"[A-Za-z0-9._:/-]{1,160}\Z")
REQUIRED_RELATIONS = (
    "group_c_runs", "group_c_inbox", "group_c_cohort_freezes",
    "group_c_cohort_freeze_members", "group_c_revisions",
    "calculated_metrics", "metric_calculation_configs",
    "comparison_cohorts", "comparison_cohort_members",
    "metric_snapshots", "source_video_metadata",
    "source_policy_versions", "sources", "sre_project_videos",
)
PROBE_SQL = "SELECT " + ", ".join(
    f"to_regclass('public.{table}') AS {table}" for table in REQUIRED_RELATIONS
)
SCORE_SQL = """
SELECT r.id::text AS c_run_id,r.project_id,r.video_id,r.source_id,
       r.source_policy_version_id AS run_policy_id,
       r.qdp_input_sha256,r.state AS run_state,r.reason_code,
       r.snapshot_id AS run_snapshot_id,
       r.previous_snapshot_id AS run_previous_id,
       r.published_metadata_id AS run_metadata_id,
       r.calculation_config_id AS run_config_id,
       r.creator_cohort_id AS run_creator_id,
       r.niche_cohort_id AS run_niche_id,
       r.prior_cohort_id AS run_prior_id,
       r.calculated_metric_id AS run_calculated_id,
       r.completed_at,
       inbox.qdp_run_id::text AS qdp_run_id,
       inbox.state AS inbox_state,inbox.analysis_group,
       inbox.producer_schema AS inbox_schema,
       inbox.input_sha256 AS inbox_input_sha,
       cm.id AS calculated_id,cm.post_id,cm.snapshot_id,
       cm.previous_snapshot_id,cm.published_metadata_id,
       cm.calculation_config_id,
       cm.creator_cohort_id,cm.niche_cohort_id,cm.prior_cohort_id,
       cm.calculation_status,cm.score_status,cm.calculated_at,
       cm.creator_median_views,cm.niche_median_views,
       cm.alpha_used,cm.k_used,
       cm.p_l_used,cm.p_c_used,cm.p_s_used,
       cm.creator_ratio,cm.niche_outlier,cm.views_growth_per_hour,
       cm.weighted_engagement_smoothed,cm.freshness_score,
       cm.score_creator_component,cm.score_growth_component,
       cm.score_engagement_component,cm.score_freshness_component,
       cm.breakout_score,
       cfg.calculation_version,cfg.is_active AS config_active,
       cfg.alpha AS configured_alpha,cfg.bayesian_k AS configured_k,
       cfg.weights_json,cfg.freshness_half_life_h,cfg.normalization_enabled,
       cfg.prior_source_policy,cfg.cohort_selection_rules,cfg.growth_policy,
       md.id AS metadata_id,md.video_id AS metadata_video_id,
       md.source_id AS metadata_source_id,
       md.source_policy_version_id AS metadata_policy_id,
       md.is_current AS metadata_current,md.deleted_at AS metadata_deleted_at,
       md.expires_at AS metadata_expires_at,md.published_at,
       cur.id AS current_id,cur.video_id AS current_video_id,
       cur.source_id AS current_source_id,
       cur.source_policy_version_id AS current_policy_id,
       cur.source_metric_definition AS current_metric_definition,
       cur.source_metric_version AS current_metric_version,
       cur.observed_at AS current_observed_at,
       cur.collected_at AS current_collected_at,
       cur.expires_at AS current_expires_at,
       cur.data_quality_status AS current_quality,
       cur.observation_time_assumed AS current_time_assumed,
       cur.precision AS current_precision,
       cur.views AS current_views,cur.likes AS current_likes,
       cur.comments AS current_comments,cur.shares AS current_shares,
       prev.id AS previous_id,prev.video_id AS previous_video_id,
       prev.source_id AS previous_source_id,
       prev.source_policy_version_id AS previous_policy_id,
       prev.source_metric_definition AS previous_metric_definition,
       prev.source_metric_version AS previous_metric_version,
       prev.observed_at AS previous_observed_at,
       prev.collected_at AS previous_collected_at,
       prev.expires_at AS previous_expires_at,
       prev.data_quality_status AS previous_quality,
       prev.observation_time_assumed AS previous_time_assumed,
       prev.precision AS previous_precision,
       prev.views AS previous_views,
       src.status AS source_state,src.can_store_metrics,
       src.can_derive_scores,src.retention_days AS source_retention_days,
       pol.is_active AS policy_active,pol.reviewed_at AS policy_reviewed_at,
       pol.permissions AS policy_permissions,
       pol.retention_policy AS retention_policy
FROM group_c_runs r
JOIN group_c_inbox inbox ON inbox.event_id=r.inbox_event_id
 AND inbox.project_id=r.project_id AND inbox.video_id=r.video_id
JOIN calculated_metrics cm ON cm.id=r.calculated_metric_id
 AND cm.post_id=r.video_id
JOIN metric_calculation_configs cfg ON cfg.id=cm.calculation_config_id
JOIN metric_snapshots cur ON cur.id=cm.snapshot_id AND cur.video_id=r.video_id
JOIN metric_snapshots prev ON prev.id=cm.previous_snapshot_id
 AND prev.video_id=r.video_id
JOIN source_video_metadata md ON md.id=cm.published_metadata_id
 AND md.video_id=r.video_id
JOIN sources src ON src.id=r.source_id
JOIN source_policy_versions pol ON pol.id=r.source_policy_version_id
 AND pol.source_id=r.source_id
JOIN sre_project_videos membership
 ON membership.project_id=r.project_id AND membership.video_id=r.video_id
WHERE r.project_id=%s AND r.id=%s::uuid AND r.video_id=%s
 AND cm.id=%s
"""
FREEZE_SQL = """
SELECT f.id AS freeze_id,f.project_id,f.legacy_cohort_id,
       f.target_video_id,f.target_snapshot_id,f.target_metadata_id,
       f.purpose,f.source_id,f.source_policy_version_id,
       f.source_metric_definition,f.source_metric_version,
       f.rules_version,f.rules_sha256,f.selection_sha256,
       f.evidence_sha256,f.estimator_version,f.member_count,
       f.median_views,f.prior_like_rate,f.prior_comment_rate,
       f.prior_share_rate,f.state AS freeze_state,f.sealed_at,
       c.id AS cohort_id,c.purpose AS legacy_purpose,c.is_frozen,
       c.post_count,c.evaluated_as_of,c.median_views AS legacy_median,
       c.prior_like_rate AS legacy_like_rate,
       c.prior_comment_rate AS legacy_comment_rate,
       c.prior_share_rate AS legacy_share_rate,
       (SELECT count(*) FROM group_c_cohort_freeze_members m
        WHERE m.freeze_id=f.id) AS frozen_members,
       (SELECT count(*) FROM comparison_cohort_members m
        WHERE m.cohort_id=f.legacy_cohort_id) AS legacy_members,
       (SELECT count(DISTINCT m.video_id)
        FROM group_c_cohort_freeze_members m
        WHERE m.freeze_id=f.id) AS distinct_videos,
       (SELECT count(*) FROM group_c_cohort_freeze_members m
        JOIN metric_snapshots ms ON ms.id=m.snapshot_id
         AND ms.video_id=m.video_id
        WHERE m.freeze_id=f.id
          AND (m.video_id=f.target_video_id OR
               m.source_id<>f.source_id OR
               m.source_policy_version_id<>f.source_policy_version_id OR
               m.metric_definition<>f.source_metric_definition OR
               m.metric_version<>f.source_metric_version OR
               ms.source_id<>m.source_id OR
               ms.source_policy_version_id<>m.source_policy_version_id OR
               ms.data_quality_status<>'ok' OR
               ms.precision<>'exact' OR
               ms.observation_time_assumed IS DISTINCT FROM FALSE OR
               (ms.expires_at IS NOT NULL AND ms.expires_at<=%s))) AS invalid_members
FROM group_c_cohort_freezes f
JOIN comparison_cohorts c ON c.id=f.legacy_cohort_id
WHERE f.project_id=%s AND f.legacy_cohort_id=%s
"""
REVISION_SQL = """
SELECT revision,revision_kind,evidence_sha256
FROM group_c_revisions WHERE project_id=%s AND run_id=%s::uuid
ORDER BY revision
"""


def _fail(code: str) -> None:
    raise AnalyticsBoundaryError(code, "Group C persisted evidence rejected: "+code)


def _positive(x: Any) -> bool:
    return type(x) is int and x > 0


def _sha(x: Any) -> bool:
    return type(x) is str and bool(_SHA.fullmatch(x))


def _uuid(x: Any) -> bool:
    if type(x) is not str: return False
    try: return str(UUID(x)) == x
    except (ValueError,TypeError,AttributeError): return False


def _utc(x: Any) -> datetime | None:
    if not isinstance(x,datetime) or x.tzinfo is None or x.utcoffset() is None:
        return None
    return x.astimezone(timezone.utc)


def _decimal(x: Any, *, positive: bool = False) -> Decimal | None:
    if type(x) not in (Decimal,int,str): return None
    try: d = Decimal(str(x))
    except (ValueError,InvalidOperation): return None
    return d if d.is_finite() and (d>0 if positive else d>=0) else None


def _row(conn: Any, sql: str, args: tuple = ()) -> dict | None:
    try: result=conn.execute(sql,args).fetchone()
    except Exception as exc:
        raise AnalyticsBoundaryError("c_dependency_unavailable",
                                     "Group C DB read failed") from exc
    if result is None: return None
    if not isinstance(result,Mapping): _fail("c_db_row_invalid")
    return dict(result)


def _rows(conn: Any, sql: str, args: tuple) -> list[dict]:
    try: values=conn.execute(sql,args).fetchall()
    except Exception as exc:
        raise AnalyticsBoundaryError("c_dependency_unavailable",
                                     "Group C DB read failed") from exc
    if type(values) is not list or any(not isinstance(v,Mapping) for v in values):
        _fail("c_db_row_invalid")
    return [dict(v) for v in values]


@dataclass(frozen=True,slots=True)
class GroupCPersistedPins:
    scope: InputScope
    c_run_id: str
    calculated_metric_id: int
    current_snapshot_id: int
    previous_snapshot_id: int
    metadata_id: int
    config_id: int
    config_version: str
    creator_cohort_id: int
    prior_cohort_id: int
    niche_cohort_id: int | None
    creator_freeze_sha256: str
    prior_freeze_sha256: str
    niche_freeze_sha256: str | None
    min_creator_peers: int
    min_prior_peers: int
    min_niche_peers: int | None
    source_metric_version: str
    qdp_c_preparation_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.scope,InputScope) or not _uuid(self.c_run_id):
            _fail("c_pins_invalid")
        for name in ("calculated_metric_id","current_snapshot_id",
                     "previous_snapshot_id","metadata_id","config_id",
                     "creator_cohort_id","prior_cohort_id",
                     "min_creator_peers","min_prior_peers"):
            if not _positive(getattr(self,name)): _fail("c_pins_invalid")
        if self.current_snapshot_id==self.previous_snapshot_id:
            _fail("c_same_snapshot")
        if (self.niche_cohort_id is None)!=(self.niche_freeze_sha256 is None) or (
            self.niche_cohort_id is None)!=(self.min_niche_peers is None):
            _fail("c_niche_pins_inconsistent")
        if self.niche_cohort_id is not None and (
            not _positive(self.niche_cohort_id) or
            not _positive(self.min_niche_peers) or
            not _sha(self.niche_freeze_sha256)):
            _fail("c_niche_pins_invalid")
        if not all(_sha(v) for v in (
            self.creator_freeze_sha256,self.prior_freeze_sha256,
            self.qdp_c_preparation_sha256)):
            _fail("c_pins_invalid")
        if any(type(v) is not str or not _VERSION.fullmatch(v) for v in (
            self.config_version,self.source_metric_version)):
            _fail("c_version_invalid")


@dataclass(frozen=True,slots=True)
class PersistedScoreFacts:
    """Only existing stored facts, not an authorization or recomputed score."""
    calculated_metric_id: int
    current_snapshot_id: int
    previous_snapshot_id: int
    calculation_version: str
    breakout_score: Decimal
    creator_ratio: Decimal
    growth_per_hour: Decimal
    smoothed_engagement: Decimal
    freshness_score: Decimal
    creator_count: int
    prior_count: int
    niche_count: int | None
    lineage_sha256: str
    approved: bool = False
    status: str = "integrity_checked_unapproved"


class GroupCPersistedScoreReader:
    """GroupCReader protocol; rights/currentness checked without any scoring."""

    def __init__(self,conn: Any,pins: GroupCPersistedPins,
                 qdp: QDPQualifiedSetReader,*,as_of: datetime):
        if not isinstance(pins,GroupCPersistedPins):
            _fail("c_pins_invalid")
        if not isinstance(qdp,QDPQualifiedSetReader):
            _fail("c_qdp_dependency_invalid")
        if qdp._conn is not conn or qdp._pins.scope != pins.scope or (
            qdp._pins.preparation_shas[2]!=pins.qdp_c_preparation_sha256
        ) or qdp._pins.group_c_snapshot_ids != (
            pins.previous_snapshot_id,pins.current_snapshot_id
        ) or qdp._pins.group_c_config_id != pins.config_id:
            _fail("c_qdp_pins_mismatch")
        date=_utc(as_of)
        if date is None or as_of.utcoffset().total_seconds()!=0:
            _fail("c_invalid_as_of")
        self._conn,self._pins,self._qdp,self._as_of=conn,pins,qdp,date

    def capability(self) -> PortCapability:
        if self._conn is None or not callable(getattr(self._conn,"execute",None)):
            return PortCapability("group_c",CapabilityState.UNAVAILABLE,
                                  reason_code="not_connected")
        try: relations=_row(self._conn,PROBE_SQL)
        except AnalyticsBoundaryError:
            return PortCapability("group_c",CapabilityState.UNAVAILABLE,
                                  reason_code="c_schema_probe_failed")
        if relations is None or any(relations.get(n) is None for n in REQUIRED_RELATIONS):
            return PortCapability("group_c",CapabilityState.UNAVAILABLE,
                                  reason_code="c_peer_schema_missing")
        if self._qdp.capability().state is not CapabilityState.DECLARED:
            return PortCapability("group_c",CapabilityState.UNAVAILABLE,
                                  reason_code="c_qdp_unavailable")
        return PortCapability("group_c",CapabilityState.DECLARED,
                              PORT_SCHEMAS["group_c"],C_READER_WIRE)

    def inspect_persisted_score(self,scope: InputScope) -> PersistedScoreFacts:
        p=self._pins
        if not isinstance(scope,InputScope) or scope!=p.scope:
            _fail("c_scope_mismatch")
        if self.capability().state is not CapabilityState.DECLARED:
            _fail("c_peer_schema_unavailable")
        if self._qdp.read_qualified_membership(scope).state is not ReadState.LOCATED:
            _fail("c_qdp_membership_unverified")
        row=_row(self._conn,SCORE_SQL,(
            scope.project_id,p.c_run_id,scope.video_id,p.calculated_metric_id
        ))
        if row is None: _fail("c_persisted_score_missing")
        self._score_identity(row,p,self._as_of)
        self._snapshots_rights(row,p)
        self._score_metrics(row,p)
        frozen={}
        for purpose,cohort_id,sha,minimum in (
            ("creator",p.creator_cohort_id,p.creator_freeze_sha256,p.min_creator_peers),
            ("engagement_prior",p.prior_cohort_id,p.prior_freeze_sha256,p.min_prior_peers),
            ("niche",p.niche_cohort_id,p.niche_freeze_sha256,p.min_niche_peers),
        ):
            if cohort_id is None: continue
            f=_row(self._conn,FREEZE_SQL,(self._as_of,scope.project_id,cohort_id))
            if f is None: _fail("c_cohort_freeze_missing")
            self._freeze(f,purpose,cohort_id,sha,minimum,row,p)
            frozen[purpose]=f
        revisions=_rows(self._conn,REVISION_SQL,(scope.project_id,p.c_run_id))
        last=0
        for revision in revisions:
            idx=revision.get("revision")
            if not _positive(idx) or idx!=last+1 or not _sha(
                revision.get("evidence_sha256")
            ):
                _fail("c_revision_lineage_invalid")
            if revision.get("revision_kind") in (
                "publication_correction","config_changed","cohort_changed",
                "source_revoked","manual_review","new_snapshot"
            ):
                _fail("c_score_superseded_or_review_pending")
            last=idx
        digest=canonical_hash({
            "schema":C_READER_WIRE,
            "scope":scope.scope_sha256,
            "c_run_id":p.c_run_id,"metric_id":p.calculated_metric_id,
            "current_snapshot_id":p.current_snapshot_id,
            "previous_snapshot_id":p.previous_snapshot_id,
            "metadata_id":p.metadata_id,"config_id":p.config_id,
            "config_version":p.config_version,
            "creator_evidence":frozen["creator"]["evidence_sha256"],
            "prior_evidence":frozen["engagement_prior"]["evidence_sha256"],
            "niche_evidence":(
                frozen["niche"]["evidence_sha256"] if "niche" in frozen else None
            ),
            "score":str(row["breakout_score"]),
            "score_as_of":row["calculated_at"].isoformat(),
            "verified_as_of":self._as_of.isoformat(),
        })
        return PersistedScoreFacts(
            calculated_metric_id=p.calculated_metric_id,
            current_snapshot_id=p.current_snapshot_id,
            previous_snapshot_id=p.previous_snapshot_id,
            calculation_version=p.config_version,
            breakout_score=row["breakout_score"],
            creator_ratio=row["creator_ratio"],
            growth_per_hour=row["views_growth_per_hour"],
            smoothed_engagement=row["weighted_engagement_smoothed"],
            freshness_score=row["freshness_score"],
            creator_count=frozen["creator"]["member_count"],
            prior_count=frozen["engagement_prior"]["member_count"],
            niche_count=frozen["niche"]["member_count"] if "niche" in frozen else None,
            lineage_sha256=digest,
        )

    def read_persisted_metric(self,scope: InputScope) -> EvidenceRead:
        if not isinstance(scope,InputScope) or scope!=self._pins.scope:
            _fail("c_scope_mismatch")
        if self.capability().state is not CapabilityState.DECLARED:
            return EvidenceRead("group_c",scope,ReadState.UNAVAILABLE,
                                reason_code="c_peer_schema_unavailable")
        try:
            checked=self.inspect_persisted_score(scope)
        except AnalyticsBoundaryError as exc:
            if exc.code=="c_dependency_unavailable":
                return EvidenceRead("group_c",scope,ReadState.UNAVAILABLE,
                                    reason_code=exc.code)
            return EvidenceRead("group_c",scope,ReadState.MISSING,
                                reason_code=exc.code)
        # GC057 state='frozen_unreviewed' only and GC peer has no trusted
        # config/cohort approval receipt. A valid number is not approval.
        assert checked.approved is False
        return EvidenceRead("group_c",scope,ReadState.MISSING,
                            reason_code="c_effective_approval_unavailable")

    @staticmethod
    def _score_identity(r: dict,p: GroupCPersistedPins,as_of: datetime) -> None:
        s=p.scope
        expected={
            "c_run_id":p.c_run_id,"project_id":s.project_id,
            "video_id":s.video_id,"source_id":s.source_id,
            "run_policy_id":s.source_policy_version_id,
            "qdp_input_sha256":p.qdp_c_preparation_sha256,
            "qdp_run_id":s.qdp_run_id,"inbox_input_sha":p.qdp_c_preparation_sha256,
            "analysis_group":"C","inbox_schema":"qdp-handoff/v1",
            "run_snapshot_id":p.current_snapshot_id,
            "run_previous_id":p.previous_snapshot_id,
            "run_metadata_id":p.metadata_id,"run_config_id":p.config_id,
            "run_creator_id":p.creator_cohort_id,
            "run_prior_id":p.prior_cohort_id,"run_niche_id":p.niche_cohort_id,
            "run_calculated_id":p.calculated_metric_id,
            "calculated_id":p.calculated_metric_id,"post_id":s.video_id,
            "snapshot_id":p.current_snapshot_id,
            "previous_snapshot_id":p.previous_snapshot_id,
            "published_metadata_id":p.metadata_id,"calculation_config_id":p.config_id,
            "creator_cohort_id":p.creator_cohort_id,
            "prior_cohort_id":p.prior_cohort_id,
            "niche_cohort_id":p.niche_cohort_id,
            "calculation_version":p.config_version,
        }
        if any(r.get(k)!=v for k,v in expected.items()):
            _fail("c_persisted_lineage_mismatch")
        if (r.get("run_state")!="completed" or r.get("reason_code") is not None
            or r.get("inbox_state")!="accepted"
            or r.get("calculation_status")!="complete"
            or r.get("score_status")!="complete"
            or r.get("config_active") is not True
            or r.get("normalization_enabled") is not False
            or _decimal(r.get("freshness_half_life_h")) != Decimal("48")):
            _fail("c_unapproved_or_incomplete_score")
        completed=_utc(r.get("completed_at"))
        calculated=_utc(r.get("calculated_at"))
        if (completed is None or calculated is None
            or calculated>completed or completed>as_of):
            _fail("c_score_date_invalid")
        if (r.get("source_state")!="enabled"
            or r.get("can_store_metrics") is not True
            or r.get("can_derive_scores") is not True
            or r.get("policy_active") is not True
            or type(r.get("policy_permissions")) is not dict
            or r["policy_permissions"].get("can_store_metrics") is not True
            or r["policy_permissions"].get("can_derive_scores") is not True):
            _fail("c_source_score_rights_revoked")

    def _snapshots_rights(self,r:dict,p:GroupCPersistedPins)->None:
        s=p.scope
        if (
            r.get("metadata_id") != p.metadata_id
            or r.get("metadata_video_id") != s.video_id
            or r.get("metadata_source_id") != s.source_id
            or r.get("metadata_policy_id") != s.source_policy_version_id
            or r.get("metadata_current") is not True
            or r.get("metadata_deleted_at") is not None
        ):
            _fail("c_publication_unavailable")
        now=self._as_of
        published=_utc(r.get("published_at"))
        if published is None or published>now:
            _fail("c_publication_date_invalid")
        for suffix,expected_id in (("current",p.current_snapshot_id),
                                   ("previous",p.previous_snapshot_id)):
            if (r.get(suffix+"_id")!=expected_id
                or r.get(suffix+"_video_id")!=s.video_id
                or r.get(suffix+"_source_id")!=s.source_id
                or r.get(suffix+"_policy_id")!=s.source_policy_version_id
                or r.get(suffix+"_metric_definition")!="platform_post_views"
                or r.get(suffix+"_metric_version")!=p.source_metric_version
                or r.get(suffix+"_quality")!="ok"
                or r.get(suffix+"_time_assumed") is not False
                or r.get(suffix+"_precision")!="exact"):
                _fail("c_snapshot_source_or_version_mismatch")
            observed=_utc(r.get(suffix+"_observed_at"))
            collected=_utc(r.get(suffix+"_collected_at"))
            if (observed is None or collected is None or
                observed<published or observed>collected or collected>now):
                _fail("c_snapshot_timestamps_invalid")
            end=r.get(suffix+"_expires_at")
            if end is not None and (_utc(end) is None or _utc(end)<=now):
                _fail("c_snapshot_expired")
        end=r.get("metadata_expires_at")
        if end is not None and (_utc(end) is None or _utc(end)<=now):
            _fail("c_publication_expired")
        if r["previous_observed_at"]>=r["current_observed_at"]:
            _fail("c_snapshot_time_not_increasing")
        before=r.get("previous_views")
        views=r.get("current_views")
        if (type(before) is not int or type(views) is not int or
            before<0 or views<=0 or views<before or
            any(type(r.get(k)) is not int or r[k]<0 for k in (
                "current_likes","current_comments","current_shares"
            ))):
            _fail("c_raw_counters_invalid")

    @staticmethod
    def _score_metrics(r:dict,p:GroupCPersistedPins)->None:
        for key in ("configured_alpha","configured_k","alpha_used","k_used"):
            if _decimal(r.get(key),positive=True) is None:
                _fail("c_config_parameters_missing")
        if (r["configured_alpha"]!=r["alpha_used"] or
            r["configured_k"]!=r["k_used"] or
            type(r.get("weights_json")) is not dict or
            r["weights_json"] != {
                "creator_ratio":0.45,"views_growth_per_hour":0.30,
                "weighted_engagement_smoothed":0.15,"freshness_score":0.10
            }):
            _fail("c_score_config_mismatch")
        if any(type(r.get(k)) is not dict for k in (
            "prior_source_policy","cohort_selection_rules","growth_policy"
        )):
            _fail("c_config_policy_missing")
        for name in ("creator_ratio","views_growth_per_hour",
                     "weighted_engagement_smoothed","freshness_score",
                     "score_creator_component","score_growth_component",
                     "score_engagement_component","score_freshness_component",
                     "breakout_score"):
            if _decimal(r.get(name)) is None:
                _fail("c_persisted_component_invalid")
        if _decimal(r["freshness_score"])>100:
            _fail("c_freshness_out_of_range")
        # Read-only integrity verification of the *persisted* components:
        # no re-computation of C1-C7 from raw metrics or normalization.
        if r["breakout_score"] != sum((
            r["score_creator_component"],r["score_growth_component"],
            r["score_engagement_component"],r["score_freshness_component"]
        ),Decimal("0")):
            _fail("c_persisted_components_inconsistent")
        if _decimal(r.get("creator_median_views"),positive=True) is None:
            _fail("c_creator_baseline_missing")
        for name in ("p_l_used","p_c_used","p_s_used"):
            if (_decimal(r.get(name)) is None or
                _decimal(r[name])>1):
                _fail("c_prior_rate_invalid")
        if (r.get("niche_cohort_id") is None and (
            r.get("niche_median_views") is not None or
            r.get("niche_outlier") is not None)):
            _fail("c_niche_not_optional")

    def _freeze(self,f:dict,purpose:str,cohort_id:int,sha:str,
                minimum:int,r:dict,p:GroupCPersistedPins)->None:
        s=p.scope
        expected={
            "project_id":s.project_id,"legacy_cohort_id":cohort_id,
            "cohort_id":cohort_id,"target_video_id":s.video_id,
            "target_snapshot_id":p.current_snapshot_id,
            "target_metadata_id":p.metadata_id,"purpose":purpose,
            "legacy_purpose":purpose,"source_id":s.source_id,
            "source_policy_version_id":s.source_policy_version_id,
            "source_metric_definition":"platform_post_views",
            "source_metric_version":p.source_metric_version,
            "rules_version":"group-c-cohort-rules/v1",
            "estimator_version":"group-c-cohort-estimator/v1",
            "evidence_sha256":sha,
        }
        if any(f.get(key)!=value for key,value in expected.items()):
            _fail("c_cohort_wrong_lineage")
        if any(not _sha(f.get(k)) for k in (
            "rules_sha256","selection_sha256","evidence_sha256"
        )):
            _fail("c_cohort_hash_missing")
        if (f.get("is_frozen") is not True or
            f.get("freeze_state") != "frozen_unreviewed" or
            _utc(f.get("sealed_at")) is None or
            _utc(f["sealed_at"])>self._as_of):
            _fail("c_cohort_not_frozen")
        actual=f.get("member_count")
        if (type(actual) is not int or actual<minimum
            or any(f.get(k)!=actual for k in (
                "post_count","frozen_members","legacy_members","distinct_videos"
            )) or f.get("invalid_members")!=0):
            _fail("c_cohort_insufficient_or_poisoned")
        stamp=_utc(f.get("evaluated_as_of"))
        if stamp is None or stamp>self._as_of:
            _fail("c_cohort_evaluation_invalid")
        if purpose in ("creator","niche"):
            median=_decimal(f.get("median_views"),positive=True)
            if median is None or median!=_decimal(f.get("legacy_median")):
                _fail("c_cohort_baseline_invalid")
            metric=("creator_median_views" if purpose=="creator"
                    else "niche_median_views")
            if median!=_decimal(r.get(metric)):
                _fail("c_cohort_baseline_mismatch")
        else:
            for key1,key2,key3 in (
                ("prior_like_rate","legacy_like_rate","p_l_used"),
                ("prior_comment_rate","legacy_comment_rate","p_c_used"),
                ("prior_share_rate","legacy_share_rate","p_s_used"),
            ):
                observed=_decimal(f.get(key1))
                if (observed is None or observed>1 or
                    observed!=_decimal(f.get(key2)) or
                    observed!=_decimal(r.get(key3))):
                    _fail("c_cohort_priors_mismatch")



__all__=(
    "GroupCPersistedPins","PersistedScoreFacts","GroupCPersistedScoreReader",
    "C_READER_WIRE","REQUIRED_RELATIONS","PROBE_SQL","SCORE_SQL",
    "FREEZE_SQL","REVISION_SQL",
)
