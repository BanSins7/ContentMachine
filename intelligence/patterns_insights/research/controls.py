"""PIE-MOD-10: frozen, offline-only matched comparison assembly.

Maximum-cardinality one-to-one matching uses only explicit pre-outcome
covariates (creator, publication clock, observation schedule, pinned stratum).
Never uses C7 score, high/low magnitude, engagement, or inferred topic for
match distance. This is not causal inference or production authorization.
"""
from __future__ import annotations

from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime
from hashlib import sha256
import json
import re
from typing import Literal

from ..contracts import PIEBoundaryError, canonical_hash
from .dataset_builder import CandidateResearchSnapshot, replay_candidate_snapshot
from .strata import CandidateStrata, replay_candidate_strata, require_one_stratum
from .outcomes import CandidateOutcomePlan, replay_candidate_outcome_plan

SCHEMA = 'pie-matched-controls/v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_VERSION = re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
MAX_POOL_SIZE = 1000
MAX_EDGE_COUNT = 100_000
MAX_JSON_BYTES = 128 * 1024 * 1024


def _fail(code: str, detail: str) -> None:
    raise PIEBoundaryError(code, detail)


def _ts(s: object, label: str) -> datetime:
    if type(s) is not str or not 1 <= len(s) <= 40 or 'T' not in s:
        _fail('invalid_matching_clock', f'{label} must be timezone-aware')
    try:
        t = datetime.fromisoformat(s.replace('Z', '+00:00'))
    except ValueError as e:
        raise PIEBoundaryError('invalid_matching_clock', f'{label} invalid') from e
    if t.utcoffset() is None:
        _fail('invalid_matching_clock', f'{label} must include offset')
    return t


def _delta_micro(a: object, b: object) -> int:
    duration = abs(_ts(a, 'case/control time') - _ts(b, 'case/control time'))
    return ((duration.days * 86400 + duration.seconds) * 1_000_000 + duration.microseconds)


@dataclass(frozen=True, slots=True)
class MatchingPolicy:
    """All matching rules explicitly set; no outcome-driven feature selection."""
    policy_version: str
    creator_rule: Literal['same_creator', 'stratum_only_no_creator_control']
    require_observed_niche: bool
    max_publication_gap_seconds: int
    max_observation_gap_seconds: int
    minimum_pairs: int
    require_all_high_matched: bool

    def __post_init__(self) -> None:
        if type(self.policy_version) is not str or not _VERSION.fullmatch(self.policy_version):
            _fail('invalid_matching_policy', 'Versioned matching rule required')
        if type(self.creator_rule) is not str or self.creator_rule not in ('same_creator', 'stratum_only_no_creator_control'):
            _fail('invalid_matching_policy', 'Explicit creator handling required')
        if type(self.require_observed_niche) is not bool or type(self.require_all_high_matched) is not bool:
            _fail('invalid_matching_policy', 'Explicit niche and coverage decisions required')
        for attr in ('max_publication_gap_seconds', 'max_observation_gap_seconds'):
            v = getattr(self, attr)
            if type(v) is not int or not 0 <= v <= 10**10:
                _fail('invalid_matching_policy', 'Time gap must be bounded nonnegative whole seconds')
        if type(self.minimum_pairs) is not int or not 1 <= self.minimum_pairs <= MAX_POOL_SIZE:
            _fail('invalid_matching_policy', 'Explicit positive minimum pair count required')

    @property
    def policy_sha256(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class CandidateMatchedControls:
    """Immutable matched pairs; evidence and authorization stay unverified."""
    artifact_sha256: str
    source_snapshot_sha256: str
    source_strata_sha256: str
    source_outcome_sha256: str
    policy_sha256: str
    project_id: str
    pair_count: int
    high_video_ids: tuple[int, ...]
    low_video_ids: tuple[int, ...]
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


def _validated_inputs(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
                      outcome: CandidateOutcomePlan) -> tuple[dict, dict, dict]:
    if (type(snapshot) is not CandidateResearchSnapshot or type(strata) is not CandidateStrata
            or type(outcome) is not CandidateOutcomePlan):
        _fail('invalid_matching_input', 'Exact frozen candidate snapshot/strata/outcome required')
    c = replay_candidate_snapshot(snapshot.to_json(), snapshot.snapshot_sha256).as_dict()
    s = replay_candidate_strata(strata.to_json(), strata.artifact_sha256, snapshot).as_dict()
    o = replay_candidate_outcome_plan(outcome.to_json(), outcome.artifact_sha256, snapshot, strata).as_dict()
    if (c['source']['pointer']['project_id'] != s['project_id'] or s['project_id'] != o['project_id']
        or snapshot.project_id != outcome.project_id or snapshot.snapshot_sha256 != outcome.snapshot_sha256
        or strata.artifact_sha256 != outcome.strata_sha256):
        _fail('matching_scope_mismatch', 'Snapshot, strata and outcome must share exact project and versions')
    return c, s, o


def _maximum_matching(adjacency: dict[int, list[int]], high: list[int]) -> dict[int, int]:
    """Deterministic iterative augmenting paths: max cardinality, not causal optimality."""
    high_to_low: dict[int, int] = {}
    low_to_high: dict[int, int] = {}
    for root in high:
        queue = deque([root])
        seen_high = {root}
        seen_low: set[int] = set()
        parent: dict[int, int] = {}
        free_low: int | None = None
        while queue and free_low is None:
            current_high = queue.popleft()
            for low in adjacency[current_high]:
                if low in seen_low:
                    continue
                seen_low.add(low)
                parent[low] = current_high
                if low not in low_to_high:
                    free_low = low
                    break
                next_high = low_to_high[low]
                if next_high not in seen_high:
                    seen_high.add(next_high)
                    queue.append(next_high)
        if free_low is None:
            continue
        current_low = free_low
        while True:
            high_id = parent[current_low]
            previous_low = high_to_low.get(high_id)
            high_to_low[high_id] = current_low
            low_to_high[current_low] = high_id
            if previous_low is None:
                break
            del low_to_high[previous_low]
            current_low = previous_low
    return high_to_low


def _compose(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
             outcome: CandidateOutcomePlan, policy: MatchingPolicy) -> dict:
    c, s, o = _validated_inputs(snapshot, strata, outcome)
    comp = o['comparison']
    target_ids = tuple(x['video_id'] for x in comp['high'])
    control_ids = tuple(x['video_id'] for x in comp['low'])
    if target_ids != outcome.high_video_ids or control_ids != outcome.low_video_ids:
        _fail('matching_population_mismatch', 'Outcome group identities must be frozen')
    if (not target_ids or not control_ids or len(target_ids) > MAX_POOL_SIZE
            or len(control_ids) > MAX_POOL_SIZE):
        _fail('matching_capacity', 'Groups must have nonempty bounded candidate pool')
    if set(target_ids) & set(control_ids) or set(outcome.neutral_video_ids) & (set(target_ids)|set(control_ids)):
        _fail('matching_overlap', 'High, low, neutral must be disjoint')
    selected = tuple(sorted(set(target_ids)|set(control_ids)|set(outcome.neutral_video_ids)))
    if tuple(sorted(g['video_id'] for g in comp['low'] + comp['high'] + comp['neutral'])) != selected:
        _fail('matching_overlap', 'Outcome plan contains duplicate assignment')
    stratum = require_one_stratum(strata, selected)
    if stratum != comp['stratum_sha256'] or stratum != o['policy']['stratum_sha256']:
        _fail('incompatible_strata', 'Only one explicitly pinned stratum is permitted')
    group = next((g for g in s['strata'] if g['key_sha256'] == stratum), None)
    if group is None or not group['members']:
        _fail('incompatible_strata', 'Selected stratum not present')
    if policy.require_observed_niche and not group['observed_niche']:
        _fail('missing_niche', 'Unobserved niche cannot satisfy required topic control')
    if (policy.creator_rule == 'stratum_only_no_creator_control'
            and (group['key']['creator_mode'] != 'cohort'
                 or not group['key']['creator_cohort_version'])):
        _fail('incompatible_creator', 'No frozen cohort-definition stratum for unadjusted matching')
    by_id = {m['video_id']:m for m in c['selection']['included']}
    if set(by_id) != set(snapshot.included_video_ids):
        _fail('matching_population_mismatch', 'Cannot create or remove included records')
    # Same cohort VERSION is NOT proof that two creators share a cohort.
    # stratum_only_no_creator_control is therefore explicitly UNADJUSTED.
    # Scores and B/A features are NEVER consulted here: they could leak the outcome.
    edges: dict[int, list[int]] = {}
    no_time_or_creator_edge: dict[int, bool] = {}
    total_edges = 0
    for high_id in target_ids:
        case_row = by_id[high_id]['row']
        candidates: list[tuple[int,int,int]] = []
        for low_id in control_ids:
            control_row = by_id[low_id]['row']
            if (policy.creator_rule == 'same_creator'
                    and case_row['creator_key'] != control_row['creator_key']):
                continue
            publish_gap = _delta_micro(case_row['published_at'], control_row['published_at'])
            observe_gap = _delta_micro(case_row['observed_at'], control_row['observed_at'])
            if (publish_gap > policy.max_publication_gap_seconds * 1_000_000
                or observe_gap > policy.max_observation_gap_seconds * 1_000_000):
                continue
            candidates.append((publish_gap, observe_gap, low_id))
            total_edges += 1
            if total_edges > MAX_EDGE_COUNT:
                _fail('matching_capacity', 'Match graph exceeds bounded resource limit')
        candidates.sort()
        edges[high_id] = [candidate[-1] for candidate in candidates]
        no_time_or_creator_edge[high_id] = not candidates
    pairs = _maximum_matching(edges, list(target_ids))
    if len(pairs) < policy.minimum_pairs or (policy.require_all_high_matched and len(pairs) != len(target_ids)):
        _fail('insufficient_controls', 'Explicit minimum pairs or full case coverage not satisfied')
    matched_control_ids = set(pairs.values())
    matched_case_ids = set(pairs)
    frozen_pairs = []
    for high_id in sorted(pairs):
        low_id = pairs[high_id]
        high_row = by_id[high_id]['row']
        low_row = by_id[low_id]['row']
        frozen_pairs.append({
            'case_video_id': high_id, 'control_video_id': low_id,
            'case_snapshot_id': by_id[high_id]['uae_snapshot_id'],
            'control_snapshot_id': by_id[low_id]['uae_snapshot_id'],
            'case_row_sha256': by_id[high_id]['row_sha256'],
            'control_row_sha256': by_id[low_id]['row_sha256'],
            'case_weight': by_id[high_id]['analysis_weight'],
            'control_weight': by_id[low_id]['analysis_weight'],
            'creator_rule': policy.creator_rule,
            'publication_gap_us': _delta_micro(high_row['published_at'], low_row['published_at']),
            'observation_gap_us': _delta_micro(high_row['observed_at'], low_row['observed_at']),
        })
    unused_cases = [{'video_id':i, 'reason_code':('no_eligible_control' if no_time_or_creator_edge[i] else 'control_capacity_exhausted')}
                    for i in target_ids if i not in matched_case_ids]
    unused_controls = [{'video_id':i, 'reason_code':'not_selected_for_pair'}
                       for i in control_ids if i not in matched_control_ids]
    if (len(frozen_pairs)+len(unused_cases)!=len(target_ids)
        or len(frozen_pairs)+len(unused_controls)!=len(control_ids)):
        _fail('matching_population_mismatch', 'Matched and unmatched counts must reconcile')
    return {
        'schema_version':SCHEMA, 'evidence_state':'unverified', 'approved_live':False,
        'project_id':snapshot.project_id,
        'source_snapshot_sha256':snapshot.snapshot_sha256,
        'source_strata_sha256':strata.artifact_sha256,
        'source_outcome_sha256':outcome.artifact_sha256,
        'matching_policy':asdict(policy), 'matching_policy_sha256':policy.policy_sha256,
        'stratum_sha256':stratum,
        'case_group':'high', 'control_group':'low',
        'objective':'maximum_cardinality_deterministic_pre_outcome_matching',
        'uncontrolled_dimensions': (['creator_identity'] if policy.creator_rule == 'stratum_only_no_creator_control' else [])
            + (['niche'] if not group['observed_niche'] else []),
        'pairs':frozen_pairs, 'pair_count':len(frozen_pairs),
        'unmatched_cases':unused_cases, 'unmatched_controls':unused_controls,
        'neutral_video_ids':list(outcome.neutral_video_ids),
        'other_strata_video_ids':comp['other_strata_video_ids'],
        'original_exclusions':comp['original_exclusions'],
        'population':{
            'scanned_total':c['selection']['scanned_count'],
            'included_total':len(snapshot.included_video_ids),
            'selected_total':comp['selected_total'],
            'high_total':len(target_ids), 'low_total':len(control_ids),
            'neutral_total':len(outcome.neutral_video_ids),
            'original_excluded_total':len(comp['original_exclusions']),
            'other_strata_total':len(comp['other_strata_video_ids']),
        },
    }


def _freeze(payload: dict) -> CandidateMatchedControls:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(encoded.encode('utf-8')) > MAX_JSON_BYTES:
        _fail('payload_too_large', 'Matched candidate exceeds byte limit')
    return CandidateMatchedControls(
        artifact_sha256=sha256(encoded.encode('utf-8')).hexdigest(),
        source_snapshot_sha256=payload['source_snapshot_sha256'],
        source_strata_sha256=payload['source_strata_sha256'],
        source_outcome_sha256=payload['source_outcome_sha256'],
        policy_sha256=payload['matching_policy_sha256'],
        project_id=payload['project_id'],
        pair_count=payload['pair_count'],
        high_video_ids=tuple(p['case_video_id'] for p in payload['pairs']),
        low_video_ids=tuple(p['control_video_id'] for p in payload['pairs']),
        _json=encoded,
    )


def build_candidate_controls(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
                             outcome: CandidateOutcomePlan, policy: MatchingPolicy) -> CandidateMatchedControls:
    """Offline planning: the caller must recheck LIVE authorization before statistics."""
    if type(policy) is not MatchingPolicy:
        _fail('invalid_matching_policy', 'Explicit typed matching policy required')
    return _freeze(_compose(snapshot, strata, outcome, policy))


def replay_candidate_controls(raw: str | bytes, expected_sha256: str,
                              snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
                              outcome: CandidateOutcomePlan) -> CandidateMatchedControls:
    """Defeat rehashed forgery by recreating all pairs from pinned source snapshots."""
    if type(raw) not in (str,bytes) or type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_matched_controls', 'Expected JSON and valid SHA256')
    if len(raw if type(raw) is bytes else raw.encode('utf-8')) > MAX_JSON_BYTES:
        _fail('payload_too_large', 'Matched candidate exceeds byte limit')
    def unique(pairs):
        result = {}
        for key,value in pairs:
            if key in result:
                _fail('duplicate_json_key', 'Duplicate matched JSON key')
            result[key]=value
        return result
    try:
        payload=json.loads(raw,object_pairs_hook=unique,
                           parse_constant=lambda _: _fail('invalid_matched_controls','Nonfinite JSON number'))
    except PIEBoundaryError:
        raise
    except (ValueError,TypeError,UnicodeError,RecursionError) as exc:
        raise PIEBoundaryError('invalid_matched_controls', 'Malformed matched JSON') from exc
    if type(payload) is not dict or canonical_hash(payload) != expected_sha256:
        _fail('matched_hash_mismatch','Content differs from expected hash')
    if payload.get('schema_version') != SCHEMA or payload.get('evidence_state') != 'unverified' or payload.get('approved_live') is not False:
        _fail('unsupported_matched_controls','Offline matched candidates cannot be upgraded to live')
    if type(payload.get('matching_policy')) is not dict or set(payload['matching_policy']) != {
        'policy_version', 'creator_rule', 'require_observed_niche',
        'max_publication_gap_seconds', 'max_observation_gap_seconds',
        'minimum_pairs', 'require_all_high_matched'
    }:
        _fail('invalid_matching_policy','Complete matching policy required')
    try:
        policy=MatchingPolicy(**payload['matching_policy'])
    except (TypeError,KeyError) as exc:
        raise PIEBoundaryError('invalid_matching_policy','Malformed matching policy') from exc
    rebuilt=build_candidate_controls(snapshot,strata,outcome,policy)
    if rebuilt.as_dict() != payload:
        _fail('matched_replay_mismatch','Resealed selection or lineage mismatch')
    return rebuilt
