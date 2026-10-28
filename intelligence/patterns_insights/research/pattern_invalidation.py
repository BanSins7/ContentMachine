"""PIE-MOD-27: immutable source invalidation tombstones and recompute signals.

No external adapter or event claims are trusted without injected current authority.
Event time does not decide precedence: ALL matching source tombstones suppress
the original evidence, including a late-delivered older revocation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone, timedelta
from hashlib import sha256
import json
import re
from typing import Protocol

from ..contracts import PIEBoundaryError, canonical_hash

SCHEMA='pie-source-invalidation-notice/v1'
_SHA=re.compile(r'[0-9a-f]{64}\Z')
_SOURCE_EVENT=re.compile(r'[A-Za-z0-9][A-Za-z0-9._:/-]{0,159}\Z')
_MAX_NOTICE_BYTES=8192
_KINDS={
    'uae_manifest':"r.payload->>'dataset_manifest_sha256'",
    'dataset_snapshot':'r.source_snapshot_sha256',
    'matched_rows':'r.source_rows_sha256',
    'quality_report':'r.source_quality_sha256',
    'holdout':"r.payload->>'source_holdout_sha256'",
    'numeric_report':"r.payload->>'source_technical_numeric_sha256'",
    'robustness_report':"r.payload->>'source_robustness_sha256'",
    'study_design':"r.payload->>'study_design_sha256'",
    'stratum':'r.stratum_sha256',
}
_PRODUCERS={
    'uae','qdp','group_a','group_b','group_c','sre',
    'source_policy','retention','research_config',
}
_REASONS={
    'upstream_revision','source_rights_revoked','source_retention_expired',
    'configuration_changed','new_source_snapshot','source_evidence_invalidated',
}
_REASON_PRODUCER={
    'source_rights_revoked':{'source_policy','uae'},
    'source_retention_expired':{'retention','source_policy','uae'},
    'configuration_changed':{'research_config','uae','qdp','group_a','group_b','group_c','sre'},
    'new_source_snapshot':{'uae'},
}


def _fail(code:str,detail:str)->None:
    raise PIEBoundaryError(code,detail)


def _utc(s:object)->datetime:
    if type(s) is not str or not 10<=len(s)<=40:
        _fail('invalid_pattern_invalidation','Timestamp must have explicit offset')
    try:
        t=datetime.fromisoformat(s.replace('Z','+00:00'))
    except ValueError as exc:
        raise PIEBoundaryError('invalid_pattern_invalidation','Malformed event timestamp') from exc
    if t.utcoffset() is None:
        _fail('invalid_pattern_invalidation','Naive event timestamp forbidden')
    return t.astimezone(timezone.utc)


@dataclass(frozen=True,slots=True)
class SourceInvalidationNotice:
    project_id:str
    target_kind:str
    target_sha256:str
    producer:str
    reason_code:str
    source_event_id:str
    event_effective_at:str
    replacement_sha256:str|None=None
    schema_version:str=SCHEMA
    provenance_claim:str='unverified_external_producer_claim'

    def __post_init__(self)->None:
        if (type(self.project_id) is not str or
            not re.fullmatch(r'[0-9a-f]{32}',self.project_id)):
            _fail('invalid_pattern_invalidation','Exact 32-hex project required')
        if self.target_kind not in _KINDS or type(self.target_kind) is not str:
            _fail('invalid_pattern_invalidation','Only typed source lineage keys')
        if type(self.target_sha256) is not str or not _SHA.fullmatch(self.target_sha256):
            _fail('invalid_pattern_invalidation','Exact original fingerprint required')
        if (type(self.producer) is not str or self.producer not in _PRODUCERS or
            type(self.reason_code) is not str or self.reason_code not in _REASONS):
            _fail('invalid_pattern_invalidation','Versioned known producer and reason needed')
        if (self.reason_code in _REASON_PRODUCER and
            self.producer not in _REASON_PRODUCER[self.reason_code]):
            _fail('invalid_pattern_invalidation','Producer cannot assert this reason')
        if type(self.source_event_id) is not str or not _SOURCE_EVENT.fullmatch(self.source_event_id):
            _fail('invalid_pattern_invalidation','Stable source event reference required')
        _utc(self.event_effective_at)
        if (self.replacement_sha256 is not None and
            (type(self.replacement_sha256) is not str
             or not _SHA.fullmatch(self.replacement_sha256)
             or self.replacement_sha256==self.target_sha256)):
            _fail('invalid_pattern_invalidation','A replacement must differ from stale evidence')
        if self.schema_version!=SCHEMA or self.provenance_claim!='unverified_external_producer_claim':
            _fail('invalid_pattern_invalidation','Never self-attest verified producer authority')

    @property
    def event_id(self)->str:
        return canonical_hash(asdict(self))

    def to_json(self)->str:
        return json.dumps(asdict(self),ensure_ascii=False,sort_keys=True,
                          separators=(',',':'),allow_nan=False)


class CurrentInvalidationAuthority(Protocol):
    """Trusted host MUST authenticate producer and exact current project scope."""
    def authorize_invalidation(self,notice:SourceInvalidationNotice)->bool:...


@dataclass(frozen=True,slots=True)
class InvalidationReceipt:
    project_id:str
    event_id:str
    idempotent:bool
    recompute_requests_added:int
    invalidated_target_kind:str
    no_source_data_rewritten:bool=True
    source_rights_or_confirmation_authenticated:bool=False


def _authorize(authority:CurrentInvalidationAuthority,
               notice:SourceInvalidationNotice)->None:
    if authority is None or not callable(getattr(authority,'authorize_invalidation',None)):
        _fail('invalidation_authority_unavailable','Authenticated current authority required')
    try:
        allowed=authority.authorize_invalidation(notice)
    except Exception:
        allowed=False
    if allowed is not True:
        _fail('invalidation_authority_denied','Unverified producer cannot invalidate project')


def invalidation_target_predicate(*,alias:str='r')->str:
    """Constant SQL predicate used by *every* read and write path.

    The target kind is taken exclusively from the frozen enum. No user SQL.
    Requires alias r with stage25 immutable revision and its JSONB payload.
    """
    if alias!='r':
        _fail('invalid_invalidation_query','Unsupported row alias')
    return '('+' OR '.join(
        f"(i.target_kind='{kind}' AND i.target_sha256={expression})"
        for kind,expression in _KINDS.items()
    )+')'


def is_revision_invalidated(connection,project_id:str,series_id:str,revision:int)->bool:
    """Fail CLOSED if the tombstone ledger is missing/unavailable."""
    if (type(project_id) is not str or not re.fullmatch(r'[0-9a-f]{32}',project_id)
        or type(series_id) is not str or not _SHA.fullmatch(series_id)
        or type(revision) is not int or revision<1):
        _fail('invalid_invalidation_query','Exact project/series/revision needed')
    sql=f"""SELECT EXISTS (
        SELECT 1 FROM pie_pattern_registry_revisions AS r
        JOIN pie_pattern_registry_invalidations AS i
          ON i.project_id=r.project_id AND {invalidation_target_predicate()}
        WHERE r.project_id=%s AND r.series_id=%s AND r.revision=%s
    )"""
    try:
        result=connection.execute(sql,(project_id,series_id,revision)).fetchone()
    except Exception as exc:
        raise PIEBoundaryError('invalidation_ledger_unavailable',
                               'Cannot verify current source revocation') from exc
    if type(result) not in (tuple,list) or len(result)!=1 or type(result[0]) is not bool:
        _fail('invalidation_ledger_unavailable','Unexpected database revocation response')
    return result[0]


def require_noninvalidated_pattern_for_insight(connection,project_id:str,
                                               series_id:str,revision:int)->None:
    """Preflight only. It NEVER grants insight publishing or supported status."""
    if is_revision_invalidated(connection,project_id,series_id,revision):
        _fail('pattern_evidence_invalidated','Tombstoned source cannot enter insight stage')


def record_source_invalidation(connection,notice:SourceInvalidationNotice,
                               authority:CurrentInvalidationAuthority,
                               *,now:datetime|None=None)->InvalidationReceipt:
    """Append atomic revocation + ALL affected reanalysis-request markers.

    No paid recompute job or authorization is produced. SQL inserts use a
    project-scoped exact-source key and an OR-set tombstone; no newer timestamps
    may supersede earlier affected-source revocations.
    """
    if type(notice) is not SourceInvalidationNotice:
        _fail('invalid_pattern_invalidation','Typed immutable source notice needed')
    _authorize(authority,notice)  # prior to any SQL/side effects
    checked=now if now is not None else datetime.now(timezone.utc)
    if type(checked) is not datetime or checked.tzinfo is None or checked.utcoffset() is None:
        _fail('invalid_invalidation_clock','Timezone-aware ingest clock required')
    # An effective time in the past is deliberately valid: late revocations
    # MUST NOT be dismissed as stale, no last-writer-wins timestamp policy.
    if _utc(notice.event_effective_at)>checked.astimezone(timezone.utc)+timedelta(minutes=5):
        _fail('invalid_pattern_invalidation','Future-dated claimed event cannot revoke')
    body=notice.to_json()
    if len(body.encode('utf-8'))>_MAX_NOTICE_BYTES:
        _fail('payload_too_large','Invalidation notice exceeds maximum bytes')
    from psycopg.types.json import Jsonb
    try:
        with connection.transaction():
            # Serialize producer event identity, INCLUDING conflicting retransmits.
            # Conflict with same event ID is idempotent only after full equality.
            inserted=connection.execute(
                """INSERT INTO pie_pattern_registry_invalidations
                   (project_id,event_id,target_kind,target_sha256,
                    replacement_sha256,producer,reason_code,
                    event_effective_at,source_event_id,notice)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (project_id,event_id) DO NOTHING
                   RETURNING event_id""",
                (notice.project_id,notice.event_id,notice.target_kind,
                 notice.target_sha256,notice.replacement_sha256,notice.producer,
                 notice.reason_code,_utc(notice.event_effective_at),
                 notice.source_event_id,Jsonb(asdict(notice)))).fetchone()
            persisted=connection.execute(
                """SELECT notice,target_kind,target_sha256,producer,source_event_id
                     FROM pie_pattern_registry_invalidations
                    WHERE project_id=%s AND event_id=%s FOR UPDATE""",
                (notice.project_id,notice.event_id)).fetchone()
            if (persisted is None or persisted[0]!=asdict(notice)
                or persisted[1:]!=(notice.target_kind,notice.target_sha256,
                                   notice.producer,notice.source_event_id)):
                _fail('invalidation_event_conflict','Never mutate/replace earlier notice')
            # Set based: no bounded backlog or discarded affected patterns.
            # Source-digest membership is checked on ALL immutable revisions.
            expr=_KINDS[notice.target_kind]
            pending=connection.execute(
                f"""WITH appended AS (
                   INSERT INTO pie_pattern_registry_recompute_requests
                     (project_id,event_id,series_id,source_revision_id,request_key)
                   SELECT r.project_id,%s,r.series_id,r.revision_id,
                       encode(sha256(convert_to(%s || ':' || r.revision_id::text,'UTF8')),'hex')
                   FROM pie_pattern_registry_revisions AS r
                   WHERE r.project_id=%s AND {expr}=%s
                   ON CONFLICT DO NOTHING
                   RETURNING 1
                ) SELECT count(*) FROM appended""",
                (notice.event_id,notice.event_id,notice.project_id,
                 notice.target_sha256)).fetchone()
            if (pending is None or type(pending[0]) is not int or pending[0]<0):
                _fail('invalidation_recompute_failed','Atomic request count unavailable')
            return InvalidationReceipt(
                notice.project_id,notice.event_id,inserted is None,
                pending[0],notice.target_kind)
    except PIEBoundaryError:
        raise
    except Exception as exc:
        # Suppress SQL/provider contents to prevent leaking affected row IDs.
        raise PIEBoundaryError('invalidation_ledger_unavailable',
                               'Atomic current invalidation could not be persisted') from exc
