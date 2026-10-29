"""PIE-MOD-09: versioned, explicitly reviewed candidate outcome comparison.

Research-policy structure ONLY. No statistical effect, matched controls, paid
execution, inferred thresholds, or production source/approval attestation.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from hashlib import sha256
import json
import re
from typing import Literal, Protocol

from ..contracts import PIEBoundaryError, canonical_hash
from .dataset_builder import CandidateResearchSnapshot, replay_candidate_snapshot
from .strata import CandidateStrata, replay_candidate_strata, require_one_stratum

SCHEMA = 'pie-outcome-comparison-policy/v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_VERSION = re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_TOKEN = re.compile(r'[A-Za-z0-9._:-]{1,160}\Z')
_NUMBER = re.compile(r'-?(?:0|[1-9][0-9]{0,17})(?:\.[0-9]{1,12})?\Z')
_SUPPORTED = frozenset(('breakout_score', 'creator_ratio', 'creator_adjusted_views'))


def _fail(code: str, detail: str) -> None:
    raise PIEBoundaryError(code, detail)


def _stamp(value: object, name: str) -> datetime:
    if type(value) is not str or not 1 <= len(value) <= 40 or 'T' not in value:
        _fail('invalid_study_window', f'{name}: required timezone-aware time')
    try:
        ts = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise PIEBoundaryError('invalid_study_window', f'{name}: invalid timestamp') from exc
    if ts.utcoffset() is None:
        _fail('invalid_study_window', f'{name}: required timezone')
    return ts


def _decimal(text: object, label: str) -> Decimal:
    if type(text) is not str or not _NUMBER.fullmatch(text):
        _fail('invalid_outcome_threshold', f'{label}: expected finite decimal string')
    try:
        number = Decimal(text)
    except InvalidOperation as exc:
        raise PIEBoundaryError('invalid_outcome_threshold', f'{label}: invalid decimal') from exc
    if not number.is_finite():
        _fail('invalid_outcome_threshold', f'{label}: nonfinite decimal')
    return number


@dataclass(frozen=True, slots=True)
class OutcomeComparisonPolicy:
    """Explicit signed-off *design claim*, not proof of human authorization.

    Endpoint assignments: low <= lower_bound, high >= upper_bound; values
    strictly between become neutral, retained and counted, never imputed.
    """
    policy_version: str
    design_sha256: str
    outcome: Literal['breakout_score', 'creator_ratio', 'creator_adjusted_views']
    outcome_definition_version: str
    lower_bound: str
    upper_bound: str
    lower_group: Literal['at_or_below']
    upper_group: Literal['at_or_above']
    baseline_group: Literal['low']
    stratum_sha256: str
    observation_start: str
    observation_end: str
    min_low_count: int
    min_high_count: int

    def __post_init__(self) -> None:
        for name in ('policy_version', 'outcome_definition_version'):
            value = getattr(self, name)
            if type(value) is not str or not _VERSION.fullmatch(value):
                _fail('invalid_outcome_policy', f'{name} must be explicit and versioned')
        if type(self.design_sha256) is not str or not _SHA.fullmatch(self.design_sha256):
            _fail('invalid_outcome_policy', 'Research design SHA must be pinned')
        if type(self.stratum_sha256) is not str or not _SHA.fullmatch(self.stratum_sha256):
            _fail('invalid_outcome_policy', 'Comparable stratum SHA must be pinned')
        if type(self.outcome) is not str or self.outcome not in _SUPPORTED:
            _fail('unsupported_outcome', 'Only explicitly registered outcome definitions accepted')
        if (self.lower_group != 'at_or_below' or self.upper_group != 'at_or_above'
                or self.baseline_group != 'low'):
            _fail('invalid_outcome_policy', 'Endpoint and baseline semantics must be explicit')
        if _decimal(self.lower_bound, 'lower_bound') >= _decimal(self.upper_bound, 'upper_bound'):
            _fail('invalid_outcome_threshold', 'Low bound must precede high bound')
        if any(type(x) is not int or not 1 <= x <= 50000 for x in
               (self.min_low_count, self.min_high_count)):
            _fail('invalid_outcome_policy', 'Explicit positive group sample minima required')
        start, end = _stamp(self.observation_start, 'observation_start'), _stamp(self.observation_end, 'observation_end')
        if start > end:
            _fail('invalid_study_window', 'Observation window must be ordered')

    @property
    def policy_sha256(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class DesignApprovalDecision:
    """An authority's claimed review decision; not a verified producer signature."""
    project_id: str
    policy_sha256: str
    design_sha256: str
    snapshot_sha256: str
    strata_sha256: str
    approval_id: str
    reviewer_id: str
    preregistered_at: str
    reviewed_at: str
    checked_at: str
    expires_at: str
    approved: bool
    active: bool
    reviewer_authorized: bool
    not_revoked: bool

    def __post_init__(self) -> None:
        if type(self.project_id) is not str or not re.fullmatch(r'[0-9a-f]{32}', self.project_id):
            _fail('invalid_approval', 'Invalid scoped project')
        for name in ('policy_sha256', 'design_sha256', 'snapshot_sha256', 'strata_sha256'):
            v = getattr(self, name)
            if type(v) is not str or not _SHA.fullmatch(v):
                _fail('invalid_approval', 'Invalid approval identity hash')
        for name in ('approval_id', 'reviewer_id'):
            v = getattr(self, name)
            if type(v) is not str or not _TOKEN.fullmatch(v):
                _fail('invalid_approval', 'Invalid approval token')
        for name in ('preregistered_at','reviewed_at','checked_at','expires_at'):
            _stamp(getattr(self, name), name)
        for name in ('approved','active','reviewer_authorized','not_revoked'):
            if type(getattr(self, name)) is not bool:
                _fail('invalid_approval', 'Explicit authority decision booleans required')


class DesignApprovalAuthority(Protocol):
    def check_design(self, project_id: str, policy_sha256: str,
                     snapshot_sha256: str, strata_sha256: str) -> DesignApprovalDecision: ...


@dataclass(frozen=True, slots=True)
class CandidateOutcomePlan:
    """Full membership plan, never inferential/production-ready."""
    artifact_sha256: str
    snapshot_sha256: str
    strata_sha256: str
    policy_sha256: str
    project_id: str
    low_video_ids: tuple[int, ...]
    high_video_ids: tuple[int, ...]
    neutral_video_ids: tuple[int, ...]
    _json: str = field(repr=False)

    @property
    def verified_live(self) -> bool:
        return False

    @property
    def ready_for_analysis(self) -> bool:
        return False

    def to_json(self) -> str:
        return self._json

    def as_dict(self) -> dict:
        return json.loads(self._json)


def _check_decision(decision: object, policy: OutcomeComparisonPolicy,
                    snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
                    now: datetime) -> DesignApprovalDecision:
    if type(decision) is not DesignApprovalDecision:
        _fail('approval_unavailable', 'Human approval authority returned no typed decision')
    if (decision.project_id != snapshot.project_id
        or decision.policy_sha256 != policy.policy_sha256
        or decision.design_sha256 != policy.design_sha256
        or decision.snapshot_sha256 != snapshot.snapshot_sha256
        or decision.strata_sha256 != strata.artifact_sha256):
        _fail('approval_scope_mismatch', 'Human approval does not apply to exact study')
    if not all((decision.approved,decision.active,decision.reviewer_authorized,decision.not_revoked)):
        _fail('approval_denied', 'Research approval has been withheld or revoked')
    registered = _stamp(decision.preregistered_at, 'preregistered_at')
    reviewed = _stamp(decision.reviewed_at, 'reviewed_at')
    checked = _stamp(decision.checked_at, 'checked_at')
    expires = _stamp(decision.expires_at, 'expires_at')
    if registered > reviewed or reviewed > checked or registered > _stamp(policy.observation_start,'observation_start'):
        _fail('preregistration_invalid', 'Design must be registered before observation window')
    if checked > now or checked < now - timedelta(minutes=5) or expires <= now:
        _fail('approval_stale', 'Approval check must be fresh and unexpired')
    return decision


def _recheck(authority: DesignApprovalAuthority | None, policy: OutcomeComparisonPolicy,
             snapshot: CandidateResearchSnapshot, strata: CandidateStrata, now: datetime) -> DesignApprovalDecision:
    if authority is None or not callable(getattr(authority, 'check_design', None)):
        _fail('capability_unavailable', 'Explicit research approval authority required')
    try:
        decision = authority.check_design(snapshot.project_id, policy.policy_sha256,
                                          snapshot.snapshot_sha256, strata.artifact_sha256)
    except Exception as exc:
        raise PIEBoundaryError('approval_unavailable', 'Research approval authority unavailable') from exc
    return _check_decision(decision, policy, snapshot, strata, now)


def _compose(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
             policy: OutcomeComparisonPolicy, decision: DesignApprovalDecision) -> dict:
    if policy.outcome != 'breakout_score':
        # The current UAE row wire has ONLY breakout_score, not independently
        # evidenced creator_ratio/creator_adjusted_views endpoints.
        _fail('outcome_unavailable', 'Selected outcome not evidenced by UAE row contract')
    c = replay_candidate_snapshot(snapshot.to_json(), snapshot.snapshot_sha256).as_dict()
    s = replay_candidate_strata(strata.to_json(), strata.artifact_sha256, snapshot).as_dict()
    if c['source']['pointer']['project_id'] != s['project_id'] or snapshot.project_id != strata.as_dict()['project_id']:
        _fail('outcome_scope_mismatch', 'Research scopes must match exactly')
    if policy.design_sha256 != c['design']['design_sha256']:
        _fail('outcome_design_mismatch', 'Study design not pinned to research snapshot')
    group = next((g for g in s['strata'] if g['key_sha256'] == policy.stratum_sha256), None)
    if group is None:
        _fail('unknown_stratum', 'Research comparison group must be explicitly selected')
    selected_ids = tuple(sorted(member['video_id'] for member in group['members']))
    if require_one_stratum(strata, selected_ids) != policy.stratum_sha256:
        _fail('incompatible_strata', 'All comparison units must share one strict stratum')
    rows = {member['video_id']: member for member in c['selection']['included']}
    if set(rows) != set(snapshot.included_video_ids):
        _fail('population_mismatch', 'No silently changed sample population')
    start, end = _stamp(policy.observation_start,'observation_start'),_stamp(policy.observation_end,'observation_end')
    if end > _stamp(c['source']['pointer']['as_of'],'as_of'):
        _fail('invalid_study_window', 'Study may not observe future evidence')
    lower, upper = _decimal(policy.lower_bound,'lower_bound'),_decimal(policy.upper_bound,'upper_bound')
    assigned = {'low':[],'high':[],'neutral':[]}
    for video_id in selected_ids:
        row = rows[video_id]['row']
        observed = _stamp(row['observed_at'],'observed_at')
        if not start <= observed <= end:
            _fail('incomparable_window', 'All selected observations must fall within approved common window')
        if row['score_status'] != 'complete':
            _fail('outcome_unavailable', 'Only actual completed Group C scores')
        value = _decimal(row['breakout_score'], 'breakout_score')
        tag = 'low' if value <= lower else 'high' if value >= upper else 'neutral'
        assigned[tag].append({'video_id':video_id, 'row_sha256':rows[video_id]['row_sha256'],
                              'uae_snapshot_id':rows[video_id]['uae_snapshot_id'],
                              'outcome_raw':row['breakout_score'],
                              'analysis_weight':rows[video_id]['analysis_weight']})
    if len(assigned['low']) < policy.min_low_count or len(assigned['high']) < policy.min_high_count:
        _fail('insufficient_comparison', 'Explicit baseline/contrast minimum counts not met')
    other_groups = sorted(set(snapshot.included_video_ids) - set(selected_ids))
    return {
        'schema_version':SCHEMA, 'evidence_state':'unverified', 'approved_live':False,
        'source_snapshot_sha256':snapshot.snapshot_sha256,
        'source_strata_sha256':strata.artifact_sha256,
        'project_id':snapshot.project_id,
        'policy':asdict(policy), 'policy_sha256':policy.policy_sha256,
        'approval_claim':asdict(decision),
        'comparison': {
            'stratum_sha256': policy.stratum_sha256,
            'baseline':'low','contrast':'high', 'endpoint_rule':'low<=lower_bound;high>=upper_bound',
            'low':assigned['low'],'high':assigned['high'],'neutral':assigned['neutral'],
            'other_strata_video_ids':other_groups,
            'original_exclusions':c['selection']['excluded'],
            'included_total':len(snapshot.included_video_ids),
            'selected_total':len(selected_ids),
            'low_count':len(assigned['low']), 'high_count':len(assigned['high']),
            'neutral_count':len(assigned['neutral']),
        },
    }


def _freeze(payload: dict) -> CandidateOutcomePlan:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'),allow_nan=False)
    digest=sha256(raw.encode('utf-8')).hexdigest()
    comp=payload['comparison']
    return CandidateOutcomePlan(digest,payload['source_snapshot_sha256'],
        payload['source_strata_sha256'],payload['policy_sha256'],payload['project_id'],
        tuple(x['video_id'] for x in comp['low']),tuple(x['video_id'] for x in comp['high']),
        tuple(x['video_id'] for x in comp['neutral']),raw)


def build_candidate_outcome_plan(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
                                  policy: OutcomeComparisonPolicy,
                                  approval: DesignApprovalAuthority | None,
                                  *, now: datetime) -> CandidateOutcomePlan:
    """Check registered human decision twice. The resulting plan remains offline only."""
    if type(snapshot) is not CandidateResearchSnapshot or type(strata) is not CandidateStrata:
        _fail('invalid_candidate','Both frozen upstream artifacts are required')
    if type(policy) is not OutcomeComparisonPolicy:
        _fail('invalid_outcome_policy','Typed explicit outcome policy required')
    if type(now) is not datetime or now.utcoffset() is None:
        _fail('invalid_study_window','Explicit aware now required')
    # Reject impossible source/strata snapshots before contacting approval provider.
    replay_candidate_snapshot(snapshot.to_json(), snapshot.snapshot_sha256)
    replay_candidate_strata(strata.to_json(), strata.artifact_sha256, snapshot)
    first=_recheck(approval, policy, snapshot, strata, now)
    payload=_compose(snapshot,strata,policy,first)
    latest=_recheck(approval, policy, snapshot, strata, now)
    if latest.approval_id != first.approval_id or latest.reviewer_id != first.reviewer_id or latest.preregistered_at != first.preregistered_at:
        _fail('approval_changed','Research approval changed during planning')
    payload['approval_claim']=asdict(latest)
    return _freeze(payload)


def replay_candidate_outcome_plan(raw: str | bytes, expected_sha256: str,
                                  snapshot: CandidateResearchSnapshot,
                                  strata: CandidateStrata) -> CandidateOutcomePlan:
    """Deterministic, offline integrity check; NEVER renews approvals/rights."""
    if type(raw) not in (str,bytes) or type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_outcome_plan','Expected canonical JSON and SHA')
    if len(raw if type(raw) is bytes else raw.encode('utf-8')) > 128 * 1024 * 1024:
        _fail('payload_too_large','Oversized outcome plan')
    def unique(pairs):
        ret={}
        for k,v in pairs:
            if k in ret: _fail('duplicate_json_key','Duplicate outcome plan key')
            ret[k]=v
        return ret
    try:
        value=json.loads(raw,object_pairs_hook=unique,
                         parse_constant=lambda _:_fail('invalid_outcome_plan','Nonfinite JSON'))
    except PIEBoundaryError: raise
    except (ValueError,TypeError,UnicodeError,RecursionError) as exc:
        raise PIEBoundaryError('invalid_outcome_plan','Invalid outcome plan') from exc
    if type(value) is not dict or canonical_hash(value)!=expected_sha256:
        _fail('outcome_hash_mismatch','Plan has been altered')
    if set(value) != {'schema_version','evidence_state','approved_live','source_snapshot_sha256',
                      'source_strata_sha256','project_id','policy','policy_sha256','approval_claim','comparison'}:
        _fail('invalid_outcome_plan','Unexpected plan attributes')
    if value['schema_version']!=SCHEMA or value['evidence_state']!='unverified' or value['approved_live'] is not False:
        _fail('unsupported_outcome_plan','A replay never verifies source or reviewer')
    try:
        design=OutcomeComparisonPolicy(**value['policy'])
        decision=DesignApprovalDecision(**value['approval_claim'])
    except (TypeError, KeyError) as exc:
        raise PIEBoundaryError('invalid_outcome_plan','Malformed typed policy/approval') from exc
    if design.policy_sha256!=value['policy_sha256']:
        _fail('outcome_policy_mismatch','Policy hash mismatch')
    # Re-evaluate only recorded identity/time/order; offline replay does NOT ask authority.
    reviewed=_stamp(decision.reviewed_at,'reviewed_at')
    registered=_stamp(decision.preregistered_at,'preregistered_at')
    if registered > reviewed or registered > _stamp(design.observation_start,'observation_start'):
        _fail('preregistration_invalid','Unregistered design')
    if not all((decision.approved,decision.active,decision.reviewer_authorized,decision.not_revoked)):
        _fail('approval_denied','Stored receipt claims rejected approval')
    if (decision.project_id!=snapshot.project_id or decision.policy_sha256!=design.policy_sha256
        or decision.design_sha256!=design.design_sha256 or decision.snapshot_sha256!=snapshot.snapshot_sha256
        or decision.strata_sha256!=strata.artifact_sha256):
        _fail('approval_scope_mismatch','Recorded approval scope mismatch')
    expected=_compose(snapshot,strata,design,decision)
    if expected!=value:
        _fail('outcome_replay_mismatch','Modified threshold, group, counts or evidence')
    return _freeze(expected)
