"""PIE-MOD-18: predeclared descriptive covariate standardization; no causal inference.

Strict candidate-only scope, immutable full-source replay and deterministic
Fraction arithmetic. An adjusted FEATURE prevalence contrast across score-defined
high/low groups is merely an association, never an effect on C7.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from fractions import Fraction
from hashlib import sha256
import json
import re

from ..contracts import PIEBoundaryError, canonical_hash
from .dataset_builder import CandidateResearchSnapshot
from .strata import CandidateStrata
from .outcomes import CandidateOutcomePlan
from .controls import CandidateMatchedControls, replay_candidate_controls
from .feature_matrix import CandidateFeatureMatrix, replay_candidate_feature_matrix
from .holdout import (CandidateHoldoutSplit, HoldoutPolicy, HoldoutMediaLineage,
                      replay_candidate_holdout_split, require_candidate_holdout_scope)

SCHEMA = 'pie-covariate-standardization-candidate/v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_VER = re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_LABEL = re.compile(r'[a-z][a-z0-9_]{0,79}\Z')
_ALLOWED = ('creator', 'niche', 'topic', 'age', 'posting_time', 'platform')
_MAX_BYTES = 16 * 1024 * 1024
_MAX_ROWS = 2000
_MAX_CELLS = 512


def _fail(code: str, reason: str) -> None:
    raise PIEBoundaryError(code, reason)


def _time(value: object) -> datetime:
    if type(value) is not str or not 10 <= len(value) <= 40:
        _fail('invalid_adjustment_time', 'Timezone-aware timestamp required')
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise PIEBoundaryError('invalid_adjustment_time', 'Invalid timestamp') from exc
    if dt.utcoffset() is None:
        _fail('invalid_adjustment_time', 'Timezone-aware timestamp required')
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True, slots=True)
class AdjustmentPolicy:
    policy_version: str
    method_version: str
    design_sha256: str
    snapshot_sha256: str
    controls_sha256: str
    matrix_sha256: str
    holdout_sha256: str
    registered_at: str
    arm: str
    feature_family: str
    feature_name: str
    adjust_dimensions: tuple[str, ...]
    age_cutoffs_seconds: tuple[int, ...]
    utc_hour_cutoffs: tuple[int, ...]
    minimum_per_role_per_cell: int
    minimum_cells: int
    minimum_distinct_creators: int
    max_cells: int
    method: str = 'pooled_cell_direct_standardization'
    insufficient_support: str = 'block_whole_estimate'
    label: str = 'descriptive_association_not_causal'

    def __post_init__(self) -> None:
        if any(type(getattr(self, n)) is not str or not _VER.fullmatch(getattr(self, n))
               for n in ('policy_version', 'method_version')):
            _fail('invalid_adjustment_policy', 'Explicit method and policy versions required')
        if any(type(getattr(self, n)) is not str or not _SHA.fullmatch(getattr(self, n))
               for n in ('design_sha256','snapshot_sha256','controls_sha256',
                         'matrix_sha256','holdout_sha256')):
            _fail('invalid_adjustment_policy', 'All source identifiers must be pinned')
        _time(self.registered_at)
        if self.arm != 'discovery':
            _fail('invalid_adjustment_policy', 'No confirmatory adjustment in this stage')
        if (self.feature_family not in ('a','b') or type(self.feature_family) is not str
            or type(self.feature_name) is not str or not _LABEL.fullmatch(self.feature_name)):
            _fail('invalid_adjustment_policy', 'Explicit eligible binary A/B feature required')
        if (type(self.adjust_dimensions) is not tuple
            or not 1 <= len(self.adjust_dimensions) <= len(_ALLOWED)
            or any(type(x) is not str or x not in _ALLOWED for x in self.adjust_dimensions)
            or self.adjust_dimensions != tuple(x for x in _ALLOWED if x in self.adjust_dimensions)):
            _fail('invalid_adjustment_policy', 'Unique, canonical ordered permitted dimensions required')
        if (type(self.age_cutoffs_seconds) is not tuple
            or not 1 <= len(self.age_cutoffs_seconds) <= 12
            or any(type(v) is not int or not 0 < v <= 10**10 for v in self.age_cutoffs_seconds)
            or tuple(sorted(set(self.age_cutoffs_seconds))) != self.age_cutoffs_seconds):
            _fail('invalid_adjustment_policy', 'Explicit unique ascending positive age cutoffs required')
        if (type(self.utc_hour_cutoffs) is not tuple
            or len(self.utc_hour_cutoffs) < 2 or len(self.utc_hour_cutoffs) > 25
            or self.utc_hour_cutoffs[0] != 0 or self.utc_hour_cutoffs[-1] != 24
            or any(type(v) is not int for v in self.utc_hour_cutoffs)
            or tuple(sorted(set(self.utc_hour_cutoffs))) != self.utc_hour_cutoffs):
            _fail('invalid_adjustment_policy', 'UTC hour intervals must be an explicit partition of [0,24)')
        for name, limit in (('minimum_per_role_per_cell', _MAX_ROWS),
                            ('minimum_cells', _MAX_CELLS),
                            ('minimum_distinct_creators', _MAX_ROWS),
                            ('max_cells', _MAX_CELLS)):
            v = getattr(self, name)
            if type(v) is not int or not 1 <= v <= limit:
                _fail('invalid_adjustment_policy', 'All sample/cell limits must be explicit positive bounded integers')
        if self.max_cells < self.minimum_cells:
            _fail('invalid_adjustment_policy', 'Cannot require more supported cells than max_cells')
        if (self.method != 'pooled_cell_direct_standardization'
            or self.insufficient_support != 'block_whole_estimate'
            or self.label != 'descriptive_association_not_causal'):
            _fail('invalid_adjustment_policy', 'No regression/causal/partial-overlap promotion supported')

    @property
    def policy_sha256(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class AdjustmentCovariate:
    """Pinned, unverified pre-publication niche/topic claims; never inferred from C7."""
    video_id: int
    row_sha256: str
    niche: str
    topic: str
    asserted_at: str
    provenance: str = 'unverified_external_prepublication_claim'

    def __post_init__(self) -> None:
        if type(self.video_id) is not int or not 0 < self.video_id < 2**63:
            _fail('invalid_adjustment_covariate', 'Exact canonical post ID required')
        if type(self.row_sha256) is not str or not _SHA.fullmatch(self.row_sha256):
            _fail('invalid_adjustment_covariate', 'Exact row SHA required')
        if any(type(getattr(self, k)) is not str or not _LABEL.fullmatch(getattr(self, k))
               for k in ('niche', 'topic')):
            _fail('invalid_adjustment_covariate', 'Known explicit categorical topic and niche required')
        _time(self.asserted_at)
        if self.provenance != 'unverified_external_prepublication_claim':
            _fail('invalid_adjustment_covariate', 'No fake authenticated model/source claims')


@dataclass(frozen=True, slots=True)
class CandidateAdjustmentReport:
    artifact_sha256: str
    project_id: str
    policy_sha256: str
    report_status: str
    _json: str = field(repr=False)

    @property
    def verified_live(self) -> bool:
        return False

    @property
    def ready_for_analysis(self) -> bool:
        return False

    def as_dict(self) -> dict:
        return json.loads(self._json)

    def to_json(self) -> str:
        return self._json


def _ratio(value: Fraction) -> dict:
    return {'numerator': value.numerator, 'denominator': value.denominator,
            'decimal_12dp': format(value.numerator / value.denominator, '.12f')}


def _bucket(value: int, cutoffs: tuple[int, ...]) -> str:
    for ix, bound in enumerate(cutoffs):
        if value < bound:
            return str(ix)
    return str(len(cutoffs))


def _compose(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
             outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
             matrix: CandidateFeatureMatrix, split: CandidateHoldoutSplit,
             holdout_policy: HoldoutPolicy, lineage: tuple[HoldoutMediaLineage, ...],
             policy: AdjustmentPolicy,
             covariates: tuple[AdjustmentCovariate, ...]) -> dict:
    if not (type(snapshot) is CandidateResearchSnapshot and type(strata) is CandidateStrata
            and type(outcome) is CandidateOutcomePlan and type(controls) is CandidateMatchedControls
            and type(matrix) is CandidateFeatureMatrix and type(split) is CandidateHoldoutSplit
            and type(holdout_policy) is HoldoutPolicy and type(policy) is AdjustmentPolicy):
        _fail('invalid_adjustment_input', 'Exact frozen stage07-11 and stage17 objects required')
    c = replay_candidate_controls(controls.to_json(), controls.artifact_sha256,
                                  snapshot, strata, outcome)
    m = replay_candidate_feature_matrix(matrix.to_json(), matrix.artifact_sha256,
                                        snapshot, strata, outcome, controls)
    s = replay_candidate_holdout_split(split.to_json(), split.artifact_sha256,
                                       snapshot, holdout_policy, lineage)
    if c != controls or m != matrix or s != split:
        _fail('adjustment_lineage_mismatch', 'Frozen source wrapper does not replay')
    if (policy.snapshot_sha256 != snapshot.snapshot_sha256
        or policy.design_sha256 != snapshot.as_dict()['design']['design_sha256']
        or policy.controls_sha256 != controls.artifact_sha256
        or policy.matrix_sha256 != matrix.artifact_sha256
        or policy.holdout_sha256 != split.artifact_sha256
        or policy.design_sha256 != holdout_policy.design_sha256
        or snapshot.project_id != controls.project_id
        or snapshot.project_id != matrix.project_id
        or snapshot.project_id != split.project_id
        or matrix.as_dict()['source_controls_sha256'] != controls.artifact_sha256
        or controls.as_dict()['source_snapshot_sha256'] != snapshot.snapshot_sha256):
        _fail('adjustment_lineage_mismatch', 'Project, source, holdout, design or matrix mismatch')
    if _time(policy.registered_at) > _time(holdout_policy.discovery_before):
        _fail('adjustment_post_selection', 'Adjustment design claim must precede discovery cutoff')
    require_candidate_holdout_scope(split, arm='discovery',
                                    video_ids=split.discovery_video_ids)
    rows = snapshot.as_dict()['selection']['included']
    ids = tuple(row['video_id'] for row in rows)
    if (not ids or len(ids) > _MAX_ROWS or type(covariates) is not tuple
        or len(covariates) != len(ids)
        or any(type(x) is not AdjustmentCovariate for x in covariates)
        or tuple(x.video_id for x in covariates) != ids):
        _fail('adjustment_covariate_population_mismatch', 'Complete exact ordered external covariates required')
    by_cov = {x.video_id: x for x in covariates}
    by_source = {x['video_id']: x for x in rows}
    for member in rows:
        x = by_cov[member['video_id']]
        if x.row_sha256 != member['row_sha256'] or _time(x.asserted_at) > _time(member['row']['published_at']):
            _fail('adjustment_covariate_mismatch', 'External claim changed row or is post-publication')
    feature = m.as_dict()
    columns = feature['columns']
    matching = [j for j, col in enumerate(columns) if (
        col['family'] == policy.feature_family and col['name'] == policy.feature_name
        and col['representation'] == 'boolean')]
    if len(matching) != 1:
        _fail('adjustment_feature_unavailable', 'Only exact frozen binary A/B feature allowed')
    index = matching[0]
    frows = {x['video_id']: x for x in feature['rows']}
    if len(frows) != len(feature['rows']):
        _fail('adjustment_matrix_mismatch', 'Duplicate feature row')
    discovery = set(split.discovery_video_ids)
    confirmation = set(split.confirmation_video_ids)
    pairs = c.as_dict()['pairs']
    selected: list[tuple[int, int]] = []
    excluded_pairs = []
    for item in pairs:
        high, low = item['case_video_id'], item['control_video_id']
        if (high in discovery) != (low in discovery):
            _fail('adjustment_cross_arm_pair', 'Stage10 matched pair touches the holdout boundary')
        if high in discovery:
            selected.append((high, low))
        else:
            reason = ('confirmation_reserved' if high in confirmation and low in confirmation
                      else 'embargo_or_quarantine')
            excluded_pairs.append({'case_video_id': high, 'control_video_id': low, 'reason_code': reason})
    if len(pairs) != len(selected) + len(excluded_pairs):
        _fail('adjustment_population_mismatch', 'Matched pairs do not reconcile')
    cells: dict[tuple[str,...], dict[str,list[bool]]] = {}
    creator_ids: set[str] = set()
    for high, low in selected:
        for i, role in ((high, 'high'), (low, 'low')):
            source = by_source.get(i)
            matrix_row = frows.get(i)
            if (source is None or matrix_row is None or matrix_row['role'] != role
                or matrix_row['row_sha256'] != source['row_sha256']
                or matrix_row['features'][index]['observation']['state'] != 'present'):
                _fail('adjustment_incomplete_feature', 'Missing/null/mismatched binary feature blocks adjustment')
            value = matrix_row['features'][index]['observation']['value']
            if type(value) is not bool:
                _fail('adjustment_invalid_feature', 'Boolean feature required, not 0/1 or imputation')
            row = source['row']
            creator_ids.add(row['creator_key'])
            published, observed = _time(row['published_at']), _time(row['observed_at'])
            seconds = int((observed-published).total_seconds())
            if seconds < 0:
                _fail('adjustment_invalid_time', 'Observed before publication')
            age = _bucket(seconds, policy.age_cutoffs_seconds)
            hour = published.hour
            posting = str(next((j for j in range(len(policy.utc_hour_cutoffs)-1)
                                if policy.utc_hour_cutoffs[j] <= hour < policy.utc_hour_cutoffs[j+1]), -1))
            if posting == '-1':
                _fail('adjustment_invalid_time', 'UTC hour outside frozen buckets')
            cov = by_cov[i]
            attributes = {'creator': row['creator_key'], 'niche': cov.niche,
                          'topic': cov.topic, 'age': age,
                          'posting_time': posting, 'platform': row['platform']}
            key = tuple(attributes[n] for n in policy.adjust_dimensions)
            cells.setdefault(key, {'high': [], 'low': []})[role].append(value)
    if len(cells) > min(_MAX_CELLS, policy.max_cells):
        _fail('adjustment_cell_capacity', 'Too many distinct cells, no silent merging allowed')
    summaries = []
    for key in sorted(cells):
        high, low = cells[key]['high'], cells[key]['low']
        record = {'dimensions': dict(zip(policy.adjust_dimensions, key)),
                  'high_n':len(high),'low_n':len(low),
                  'high_hits':sum(high),'low_hits':sum(low),
                  'supported': len(high) >= policy.minimum_per_role_per_cell
                               and len(low) >= policy.minimum_per_role_per_cell}
        summaries.append(record)
    n_high = len(selected)
    n_low = len(selected)
    enough = (bool(selected) and len(summaries) >= policy.minimum_cells
              and len(creator_ids) >= policy.minimum_distinct_creators
              and all(cell['supported'] for cell in summaries))
    adjusted = unadjusted = None
    if enough:
        total = n_high + n_low
        # Pooled empirical support weights, each cell represented once, exact Fraction.
        diff = Fraction(0)
        for cell in summaries:
            count = cell['high_n'] + cell['low_n']
            rate = (Fraction(cell['high_hits'], cell['high_n'])
                    - Fraction(cell['low_hits'], cell['low_n']))
            diff += Fraction(count, total) * rate
        adjusted = _ratio(diff)
        total_high = sum(x['high_hits'] for x in summaries)
        total_low = sum(x['low_hits'] for x in summaries)
        unadjusted = _ratio(Fraction(total_high, n_high)-Fraction(total_low, n_low))
    return {
        'schema_version':SCHEMA, 'evidence_state':'unverified',
        'approved_live':False, 'project_id':snapshot.project_id,
        'source_snapshot_sha256':snapshot.snapshot_sha256,
        'source_controls_sha256':controls.artifact_sha256,
        'source_matrix_sha256':matrix.artifact_sha256,
        'source_holdout_sha256':split.artifact_sha256,
        'external_covariate_sha256':canonical_hash({'rows':[asdict(x) for x in covariates]}),
        'covariate_provenance':'unverified_external_prepublication_claim',
        'policy':asdict(policy),'policy_sha256':policy.policy_sha256,
        'report_status':'candidate_descriptive_association_only' if enough
                        else 'blocked_insufficient_joint_support',
        'arm':'discovery','estimand':'pooled_overlap_standardized_high_minus_low_feature_prevalence',
        'adjust_dimensions':list(policy.adjust_dimensions),
        'sample':{'candidate_matched_pairs':len(pairs),
                  'discovery_pairs':len(selected),
                  'excluded_pairs':excluded_pairs, 'distinct_discovery_creators':len(creator_ids),
                  'cells':len(summaries),'supported_cells':sum(v['supported'] for v in summaries)},
        'cells':summaries,
        'adjusted_risk_difference': adjusted,
        'unadjusted_risk_difference':unadjusted,
        'p_values':None,'confirmed_effect':None,'causal_claim':False,
        'warnings':['observational_high_low_groups_are_outcome_defined',
                    'external_niche_topic_not_authenticated',
                    'source_rights_review_and_preregistration_not_attested',
                    'no_inferential_significance_or_causal_identification',
                    'no_posthoc_removal_of_unsupported_cells']
    }


def _freeze(payload: dict) -> CandidateAdjustmentReport:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(raw.encode('utf-8')) > _MAX_BYTES:
        _fail('payload_too_large', 'Adjustment candidate exceeds byte cap')
    return CandidateAdjustmentReport(sha256(raw.encode()).hexdigest(),
        payload['project_id'],payload['policy_sha256'],payload['report_status'],raw)


def build_candidate_adjustment_report(
        snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
        outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
        matrix: CandidateFeatureMatrix, split: CandidateHoldoutSplit,
        holdout_policy: HoldoutPolicy, lineage: tuple[HoldoutMediaLineage, ...],
        policy: AdjustmentPolicy,
        covariates: tuple[AdjustmentCovariate, ...]) -> CandidateAdjustmentReport:
    return _freeze(_compose(snapshot, strata, outcome, controls, matrix, split,
                            holdout_policy, lineage, policy, covariates))


def replay_candidate_adjustment_report(
        raw: str | bytes, expected_sha256: str,
        snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
        outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
        matrix: CandidateFeatureMatrix, split: CandidateHoldoutSplit,
        holdout_policy: HoldoutPolicy, lineage: tuple[HoldoutMediaLineage, ...],
        policy: AdjustmentPolicy,
        covariates: tuple[AdjustmentCovariate, ...]) -> CandidateAdjustmentReport:
    if type(raw) not in (str,bytes) or type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_adjustment_artifact','Canonical JSON and SHA256 required')
    try:
        encoded = raw if type(raw) is bytes else raw.encode('utf-8')
    except UnicodeError as exc:
        raise PIEBoundaryError('invalid_adjustment_artifact','Bad UTF-8') from exc
    if len(encoded) > _MAX_BYTES:
        _fail('payload_too_large', 'Artifact exceeds byte cap')
    def unique(items):
        result = {}
        for k,v in items:
            if k in result:
                _fail('duplicate_json_key','Duplicate JSON property')
            result[k] = v
        return result
    try:
        data = json.loads(encoded, object_pairs_hook=unique,
                          parse_constant=lambda _: _fail('invalid_adjustment_artifact','Nonfinite JSON'))
    except PIEBoundaryError:
        raise
    except (ValueError, UnicodeError, TypeError, RecursionError) as exc:
        raise PIEBoundaryError('invalid_adjustment_artifact','Malformed JSON') from exc
    if type(data) is not dict or canonical_hash(data) != expected_sha256:
        _fail('adjustment_hash_mismatch','Unexpected artifact digest')
    if data.get('schema_version') != SCHEMA or data.get('approved_live') is not False:
        _fail('unsupported_adjustment_artifact','Candidate cannot be promoted')
    rebuilt = build_candidate_adjustment_report(snapshot,strata,outcome,controls,matrix,split,
                                                holdout_policy,lineage,policy,covariates)
    if rebuilt.as_dict() != data or rebuilt.to_json().encode('utf-8') != encoded:
        _fail('adjustment_replay_mismatch', 'Exact upstream inputs/bytes differ from replay')
    return rebuilt
