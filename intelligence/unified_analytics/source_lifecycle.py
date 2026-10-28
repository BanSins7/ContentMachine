"""UA-MOD-29: source lifecycle invalidations and UAE-only retention purge.

Not a peer-source delete API. No producer A/B/C/SRE data is altered.
Caller supplies trusted, already authorized project DB and explicit source.
Revocation synchronizes immutable invalidation/outbox without changing
immutable snapshot status. Purge deletes UAE records, never bypasses read
rights or writes a "qualified" row.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any,Mapping
import re

from .contracts import AnalyticsBoundaryError,PROJECT_RE,canonical_hash
from .job_queue import _rows
from .revision_events import OUTBOX_INSERT_SQL

LIFECYCLE_WIRE="uae-source-lifecycle/v1"
_SOURCE_RE=re.compile(r"[A-Za-z0-9_.:-]{1,160}\Z")
MAX_SWEEP=100
PROJECT_LOCK_SQL="SELECT id FROM project_registry WHERE id=%s FOR UPDATE"
SOURCE_SQL="SELECT id FROM sources WHERE id=%s"
# Retention/policy conditions intentionally mirror UA083/089 stricter of
# source retention and policy derived_retention_days. This also invalidates
# partial/blocked snapshots with a missing metric instead of treating it
# as a valid right. Never insert or promote qualified data.
REVOCATION_TARGETS_SQL="""
SELECT sn.id AS snapshot_id,
 CASE WHEN (
  src.status<>'enabled'
  OR src.can_store_metadata IS NOT TRUE
  OR src.can_analyze_media IS NOT TRUE
  OR src.can_store_metrics IS NOT TRUE
  OR src.can_derive_scores IS NOT TRUE
  OR pol.id IS NULL OR pol.is_active IS NOT TRUE
  OR pol.reviewed_at IS NULL OR pol.reviewed_at>clock_timestamp()
  OR pol.permissions->>'can_store_metadata' IS DISTINCT FROM 'true'
  OR pol.permissions->>'can_analyze_media' IS DISTINCT FROM 'true'
  OR pol.permissions->>'can_store_metrics' IS DISTINCT FROM 'true'
  OR pol.permissions->>'can_derive_scores' IS DISTINCT FROM 'true'
  OR src.retention_days IS NULL OR src.retention_days<=0
  OR (pol.retention_policy->>'derived_retention_days')
     !~ '^[1-9][0-9]{0,2}$'
  OR (SELECT count(*) FROM source_policy_versions p2
      WHERE p2.source_id=src.id AND p2.is_active IS TRUE)<>1
 ) THEN 'source_access_revoked'
 ELSE 'source_retention_expired' END AS reason_code
FROM uae_unified_snapshots sn
JOIN sources src ON src.id=sn.source_id
LEFT JOIN source_policy_versions pol
 ON pol.id=sn.source_policy_version_id AND pol.source_id=src.id
LEFT JOIN metric_snapshots ms
 ON ms.id=sn.metric_snapshot_id AND ms.video_id=sn.video_id
 AND ms.source_id=sn.source_id
 AND ms.source_policy_version_id=sn.source_policy_version_id
WHERE sn.project_id=%s AND sn.source_id=%s
 AND NOT EXISTS (
  SELECT 1 FROM uae_snapshot_invalidations inv
  WHERE inv.project_id=sn.project_id AND inv.snapshot_id=sn.id
 )
 AND (
  src.status<>'enabled'
  OR src.can_store_metadata IS NOT TRUE
  OR src.can_analyze_media IS NOT TRUE
  OR src.can_store_metrics IS NOT TRUE
  OR src.can_derive_scores IS NOT TRUE
  OR pol.id IS NULL OR pol.is_active IS NOT TRUE
  OR pol.reviewed_at IS NULL OR pol.reviewed_at>clock_timestamp()
  OR pol.permissions->>'can_store_metadata' IS DISTINCT FROM 'true'
  OR pol.permissions->>'can_analyze_media' IS DISTINCT FROM 'true'
  OR pol.permissions->>'can_store_metrics' IS DISTINCT FROM 'true'
  OR pol.permissions->>'can_derive_scores' IS DISTINCT FROM 'true'
  OR src.retention_days IS NULL OR src.retention_days<=0
  OR (pol.retention_policy->>'derived_retention_days')
      !~ '^[1-9][0-9]{0,2}$'
  OR ms.id IS NULL OR ms.expires_at<=clock_timestamp()
  OR ms.observed_at IS NULL
  OR ms.observed_at+make_interval(days=>LEAST(
    GREATEST(COALESCE(src.retention_days,0),1),
    CASE WHEN (pol.retention_policy->>'derived_retention_days')
               ~ '^[1-9][0-9]{0,2}$'
         THEN (pol.retention_policy->>'derived_retention_days')::int
         ELSE 1 END
  ))<=clock_timestamp()
 )
ORDER BY sn.id LIMIT %s
FOR UPDATE OF sn SKIP LOCKED
"""
INSERT_INVALIDATION_SQL="""
INSERT INTO uae_snapshot_invalidations(
 project_id,snapshot_id,reason_code,reason_sha256)
VALUES(%s,%s,%s,%s)
ON CONFLICT(project_id,snapshot_id,reason_sha256) DO NOTHING
RETURNING id::text AS id
"""
# Purge source-scoped records, plus project-only evidence that cannot be
# safely attributed to a source: conservatively invalidate the ENTIRE
# project's QDP receipts, quarantine, audit and ranked manifests. Existing
# peers can subsequently re-prove these, not get a false continued grant.
PURGE_PROJECT_QDP_QUARANTINE_SQL="""
DELETE FROM uae_qdp_handoff_quarantine WHERE project_id=%s
"""
PURGE_PROJECT_QDP_SQL="""
DELETE FROM uae_qdp_handoff_inbox WHERE project_id=%s
"""
PURGE_PROJECT_AUDIT_SQL="DELETE FROM uae_audit_events WHERE project_id=%s"
PURGE_PROJECT_RUNS_SQL="DELETE FROM uae_project_aggregate_runs WHERE project_id=%s"
PURGE_REVISIONS_SQL="""
DELETE FROM uae_revision_inbox
WHERE project_id=%s AND source_id=%s
"""
PURGE_REQUESTS_SQL="""
DELETE FROM uae_qualification_requests
WHERE project_id=%s AND source_id=%s
"""
# Snapshot -> immutable payloads/evidence/invalidations/outbox,
# request -> snapshots and jobs -> job_events are all existing
# ON DELETE CASCADE. The deletion guard UA086 is deferred and checks
# only when the parent immutable payload still exists.
COUNTS_SQL="""
SELECT
 (SELECT count(*)::bigint FROM uae_qualification_requests
   WHERE project_id=%s AND source_id=%s) AS requests,
 (SELECT count(*)::bigint FROM uae_unified_snapshots
   WHERE project_id=%s AND source_id=%s) AS snapshots,
 (SELECT count(*)::bigint FROM uae_revision_inbox
   WHERE project_id=%s AND source_id=%s) AS revisions,
 (SELECT count(*)::bigint FROM uae_qdp_handoff_inbox
   WHERE project_id=%s) AS project_qdp,
 (SELECT count(*)::bigint FROM uae_qdp_handoff_quarantine
   WHERE project_id=%s) AS project_qdp_quarantine,
 (SELECT count(*)::bigint FROM uae_audit_events
   WHERE project_id=%s) AS project_audit,
 (SELECT count(*)::bigint FROM uae_project_aggregate_runs
   WHERE project_id=%s) AS project_runs
"""


def _deny(code:str)->None:
    raise AnalyticsBoundaryError(code,"UAE source lifecycle rejected: "+code)


def _check(project:str,source:str)->None:
    if (type(project) is not str or PROJECT_RE.fullmatch(project) is None
        or type(source) is not str or _SOURCE_RE.fullmatch(source) is None):
        _deny("lifecycle_scope_invalid")


def _single(db:Any,sql:str,args:tuple)->dict|None:
    rows=db.execute(sql,args).fetchall()
    if type(rows) is not list or len(rows)>1 or any(
        not isinstance(x,Mapping) for x in rows
    ):_deny("lifecycle_storage_shape_invalid")
    return dict(rows[0]) if rows else None


def _count(x:Any)->int:
    if type(x) is not int or x<0 or x>2**63-1:
        _deny("lifecycle_count_invalid")
    return x


@dataclass(frozen=True,slots=True)
class RetentionPlan:
    """Digest is a stale-plan check, NOT an authorization signature."""
    scope_sha256:str
    counts:dict[str,int]
    project_wide_collateral:bool
    schema_version:str=LIFECYCLE_WIRE

    def __post_init__(self)->None:
        expected=("requests","snapshots","revisions","project_qdp",
                  "project_qdp_quarantine","project_audit","project_runs")
        if (type(self.scope_sha256) is not str
            or not re.fullmatch(r"[0-9a-f]{64}",self.scope_sha256)
            or type(self.counts) is not dict
            or tuple(self.counts)!=expected
            or type(self.project_wide_collateral) is not bool
            or self.schema_version!=LIFECYCLE_WIRE):
            _deny("lifecycle_plan_invalid")
        for v in self.counts.values():_count(v)


@dataclass(frozen=True,slots=True)
class LifecycleOutcome:
    operation:str
    affected:int
    idempotent_noop:bool
    qualified:bool=False
    schema_version:str=LIFECYCLE_WIRE

    def __post_init__(self)->None:
        if (self.operation not in ("invalidate","purge")
            or type(self.idempotent_noop) is not bool
            or type(self.qualified) is not bool or self.qualified
            or self.schema_version!=LIFECYCLE_WIRE):
            _deny("lifecycle_outcome_invalid")
        _count(self.affected)


class SourceLifecycle:
    def __init__(self,db:Any):
        if (db is None or not callable(getattr(db,"execute",None))
            or not callable(getattr(db,"transaction",None))):
            _deny("lifecycle_connection_required")
        self.db=db

    def _fresh(self)->None:
        if getattr(getattr(self.db,"info",None),"transaction_status",1)!=0:
            _deny("lifecycle_fresh_transaction_required")

    def _scope(self,project:str,source:str)->None:
        _check(project,source)
        if _single(self.db,PROJECT_LOCK_SQL,(project,)) is None:
            _deny("lifecycle_project_missing")
        if _single(self.db,SOURCE_SQL,(source,)) is None:
            _deny("lifecycle_source_missing")

    @staticmethod
    def _plan(db:Any,project:str,source:str)->RetentionPlan:
        r=_single(db,COUNTS_SQL,
            (project,source,project,source,project,source,
             project,project,project,project))
        if r is None:_deny("lifecycle_storage_shape_invalid")
        keys=("requests","snapshots","revisions","project_qdp",
              "project_qdp_quarantine","project_audit","project_runs")
        counts={k:_count(r.get(k)) for k in keys}
        # Do not erase another source's project QDP/audit records unless
        # at least one exact UAE source request, revision or snapshot exists.
        has_source=bool(counts["requests"] or counts["snapshots"]
                        or counts["revisions"])
        collateral=has_source and any(counts[k]>0 for k in keys[3:])
        digest=canonical_hash({
            "wire":LIFECYCLE_WIRE,"project":project,"source":source,
            "counts":counts,"source_present":has_source,
        })
        return RetentionPlan(digest,counts,collateral)

    def preview(self,project:str,source:str)->RetentionPlan:
        _check(project,source);self._fresh()
        try:
            with self.db.transaction():
                self.db.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ")
                self._scope(project,source)
                return self._plan(self.db,project,source)
        except AnalyticsBoundaryError:raise
        except Exception as exc:
            raise AnalyticsBoundaryError("lifecycle_storage_unavailable",
                "UAE retention preview unavailable") from exc

    def invalidate_unreadable(self,project:str,source:str,*,
                              limit:int=50)->LifecycleOutcome:
        """Only proven revoked/expired current rights; never unapproved writes."""
        _check(project,source)
        if type(limit) is not int or not 1<=limit<=MAX_SWEEP:
            _deny("lifecycle_limit_invalid")
        self._fresh()
        try:
            with self.db.transaction():
                self.db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                self._scope(project,source)
                targets=_rows(self.db.execute(REVOCATION_TARGETS_SQL,
                                              (project,source,limit)))
                if len(targets)>limit:
                    _deny("lifecycle_storage_shape_invalid")
                n=0
                for row in targets:
                    sid=row.get("snapshot_id")
                    reason=row.get("reason_code")
                    if (type(sid) is not int or sid<=0
                        or reason not in (
                            "source_access_revoked","source_retention_expired"
                        )):
                        _deny("lifecycle_storage_shape_invalid")
                    digest=canonical_hash({
                        "wire":LIFECYCLE_WIRE,"project":project,
                        "source":source,"snapshot":sid,"reason":reason,
                    })
                    added=_single(self.db,INSERT_INVALIDATION_SQL,
                        (project,sid,reason,digest))
                    if added is None:continue
                    from uuid import UUID
                    try:UUID(added["id"])
                    except (ValueError,TypeError,KeyError):
                        _deny("lifecycle_storage_shape_invalid")
                    self.db.execute(OUTBOX_INSERT_SQL,(
                        project,sid,added["id"],
                        canonical_hash({
                            "wire":"uae.snapshot_invalidated/v1",
                            "invalidation":added["id"],
                            "reason_sha256":digest,
                        }),
                    ))
                    n+=1
                return LifecycleOutcome("invalidate",n,n==0)
        except AnalyticsBoundaryError:raise
        except Exception as exc:
            raise AnalyticsBoundaryError("lifecycle_storage_unavailable",
                "UAE source invalidation unavailable") from exc

    def purge(self,project:str,source:str,*,expected_plan_sha256:str,
              confirm_project:str,confirm_source:str)->LifecycleOutcome:
        """Destructive UAE-only deletion, single transaction, fail closed.

        A preview digest confirms state shape; an independent operator
        authorization is REQUIRED at the CLI/caller boundary. Never assume
        this method authenticates the operator from merely matching strings.
        """
        _check(project,source)
        if (type(expected_plan_sha256) is not str
            or not re.fullmatch(r"[0-9a-f]{64}",expected_plan_sha256)
            or confirm_project!=project or confirm_source!=source):
            _deny("lifecycle_purge_confirmation_required")
        self._fresh()
        try:
            with self.db.transaction():
                self.db.execute("SET TRANSACTION ISOLATION LEVEL READ COMMITTED")
                self._scope(project,source)
                current=self._plan(self.db,project,source)
                if current.scope_sha256!=expected_plan_sha256:
                    _deny("lifecycle_plan_stale")
                target_n=(current.counts["requests"]+
                          current.counts["snapshots"]+
                          current.counts["revisions"])
                if target_n==0:
                    return LifecycleOutcome("purge",0,True)
                # Purge unscopable project-level QDP facts first. Purge
                # ranked manifests before deleting source snapshot records.
                self.db.execute(PURGE_PROJECT_RUNS_SQL,(project,))
                self.db.execute(PURGE_PROJECT_AUDIT_SQL,(project,))
                self.db.execute(PURGE_PROJECT_QDP_QUARANTINE_SQL,(project,))
                self.db.execute(PURGE_PROJECT_QDP_SQL,(project,))
                self.db.execute(PURGE_REVISIONS_SQL,(project,source))
                self.db.execute(PURGE_REQUESTS_SQL,(project,source))
                # The parent request/snapshot ON DELETE CASCADE removes
                # evidence, immutable payload, jobs, events, invalidations
                # and outbox. No explicit deletion of peer A/B/C/SRE facts.
                return LifecycleOutcome("purge",target_n,False)
        except AnalyticsBoundaryError:raise
        except Exception as exc:
            raise AnalyticsBoundaryError("lifecycle_storage_unavailable",
                "UAE retention purge unavailable") from exc


__all__=("LIFECYCLE_WIRE","RetentionPlan","LifecycleOutcome",
         "SourceLifecycle","REVOCATION_TARGETS_SQL","COUNTS_SQL",
         "PURGE_PROJECT_QDP_SQL","PURGE_PROJECT_RUNS_SQL",
         "PURGE_REQUESTS_SQL","PURGE_REVISIONS_SQL")
