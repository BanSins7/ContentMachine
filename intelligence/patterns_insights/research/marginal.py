"""PIE-MOD-13: preregistered *descriptive-only* single-feature candidates.

Never infer significance, adjust effect sizes, rank by observed uplift, or make
an unverified research dataset production-ready. The upstream PIE snapshots,
strata, controls, feature matrix and coverage QA are fully replayed every time.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from decimal import Decimal, ROUND_HALF_EVEN
from hashlib import sha256
import json
import re
from typing import Literal

from ..contracts import PIEBoundaryError, canonical_hash
from .dataset_builder import CandidateResearchSnapshot, replay_candidate_snapshot
from .strata import CandidateStrata, replay_candidate_strata
from .outcomes import CandidateOutcomePlan, replay_candidate_outcome_plan
from .controls import CandidateMatchedControls, replay_candidate_controls
from .feature_matrix import CandidateFeatureMatrix, replay_candidate_feature_matrix
from .coverage import CandidateCoverageAudit, replay_candidate_coverage_audit

SCHEMA = 'pie-marginal-candidates/v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_VERSION = re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_ID = re.compile(r'[a-z][a-z0-9_]{0,79}\Z')
_MAX_HYPOTHESES = 64
_MAX_RAW_BYTES = 128 * 1024 * 1024
_TYPES = {'numeric_distribution':'numeric_int', 'boolean_equals':'boolean',
          'category_equals':'category', 'multilabel_contains':'multi_label',
          'sequence_equals':'ordered_sequence'}


def _fail(code: str, text: str) -> None:
    raise PIEBoundaryError(code, text)


def _stamp(text: str) -> datetime:
    if type(text) is not str or 'T' not in text or len(text) > 40:
        _fail('invalid_discovery_policy', 'Explicit registration timestamp required')
    try:
        d = datetime.fromisoformat(text.replace('Z', '+00:00'))
    except ValueError as exc:
        raise PIEBoundaryError('invalid_discovery_policy', 'Invalid preregistration time') from exc
    if d.utcoffset() is None:
        _fail('invalid_discovery_policy', 'Timezone-aware preregistration required')
    return d


@dataclass(frozen=True, slots=True)
class MarginalHypothesis:
    """An explicit, type-checked SINGLE feature test, never a post-hoc search."""
    hypothesis_id: str
    family: Literal['a', 'b']
    feature_name: str
    analysis: Literal['numeric_distribution','boolean_equals','category_equals',
                      'multilabel_contains','sequence_equals']
    target: None | bool | str | tuple[str, ...]
    numeric_boundaries: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        if type(self.hypothesis_id) is not str or not _ID.fullmatch(self.hypothesis_id):
            _fail('invalid_hypothesis', 'Stable hypothesis ID required')
        if type(self.family) is not str or self.family not in ('a','b'):
            _fail('invalid_hypothesis', 'Only observed Group A/B feature families allowed')
        if type(self.feature_name) is not str or not _ID.fullmatch(self.feature_name):
            _fail('invalid_hypothesis', 'Explicit bounded feature name required')
        if type(self.analysis) is not str or self.analysis not in _TYPES:
            _fail('invalid_hypothesis', 'Unsupported unregistered analysis method')
        if type(self.numeric_boundaries) is not tuple:
            _fail('invalid_hypothesis', 'Numeric bins must be immutable tuple')
        if self.analysis == 'numeric_distribution':
            if self.target is not None or not (1 <= len(self.numeric_boundaries) <= 32) or any(
                type(v) is not int or not -(2**63) <= v < 2**63
                for v in self.numeric_boundaries
            ) or tuple(sorted(set(self.numeric_boundaries))) != self.numeric_boundaries:
                _fail('invalid_hypothesis', 'Predeclared numeric histogram boundaries required')
        else:
            if self.numeric_boundaries:
                _fail('invalid_hypothesis', 'Non-numeric test cannot invent numeric buckets')
            if self.analysis == 'boolean_equals' and type(self.target) is not bool:
                _fail('invalid_hypothesis', 'Boolean predicate requires explicit bool')
            if self.analysis in ('category_equals','multilabel_contains') and (
                type(self.target) is not str or not 1 <= len(self.target) <= 120
                or any(ord(ch) < 32 for ch in self.target)
            ):
                _fail('invalid_hypothesis', 'Categorical predicate needs bounded observed-text label')
            if self.analysis == 'sequence_equals' and (
                type(self.target) is not tuple or len(self.target) > 64 or any(
                    type(x) is not str or not 1 <= len(x) <= 120 or any(ord(ch) < 32 for ch in x)
                    for x in self.target
                )
            ):
                _fail('invalid_hypothesis', 'Exact ordered sequence must be explicit tuple')


@dataclass(frozen=True, slots=True)
class MarginalDiscoveryPolicy:
    policy_version: str
    design_sha256: str
    registered_at: str
    registration_claim_id: str
    minimum_observed_per_role: int
    hypotheses: tuple[MarginalHypothesis, ...]

    def __post_init__(self) -> None:
        if type(self.policy_version) is not str or not _VERSION.fullmatch(self.policy_version):
            _fail('invalid_discovery_policy', 'Explicit version required')
        if type(self.design_sha256) is not str or not _SHA.fullmatch(self.design_sha256):
            _fail('invalid_discovery_policy', 'Pinned original research design SHA required')
        _stamp(self.registered_at)
        if type(self.registration_claim_id) is not str or not _VERSION.fullmatch(self.registration_claim_id):
            _fail('invalid_discovery_policy', 'Registration reference must be explicit (not an attestation)')
        if type(self.minimum_observed_per_role) is not int or not 1 <= self.minimum_observed_per_role <= 50000:
            _fail('invalid_discovery_policy', 'Explicit positive per-arm minimum required')
        if type(self.hypotheses) is not tuple or not 1 <= len(self.hypotheses) <= _MAX_HYPOTHESES or any(
            type(h) is not MarginalHypothesis for h in self.hypotheses
        ):
            _fail('invalid_discovery_policy', 'Bounded immutable preregistered search space required')
        ids = tuple(h.hypothesis_id for h in self.hypotheses)
        if ids != tuple(sorted(set(ids))):
            _fail('invalid_discovery_policy', 'Hypothesis IDs must be unique and sorted BEFORE observing outcomes')
        if len({(h.family,h.feature_name,h.analysis,str(h.target),h.numeric_boundaries)
                for h in self.hypotheses}) != len(self.hypotheses):
            _fail('duplicate_hypothesis', 'Repeated hypothesis predicate cannot inflate search space')

    @property
    def policy_sha256(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class CandidateMarginalDiscovery:
    artifact_sha256: str
    project_id: str
    source_coverage_sha256: str
    source_matrix_sha256: str
    policy_sha256: str
    hypothesis_count: int
    matched_n: int
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


def _median(values: list[int]) -> str:
    ordered = sorted(values)
    n = len(ordered)
    if n % 2:
        return str(ordered[n//2])
    return str((Decimal(ordered[n//2-1]) + Decimal(ordered[n//2]))/2)


def _hit(value: object, hypothesis: MarginalHypothesis) -> bool:
    if hypothesis.analysis == 'boolean_equals':
        return type(value) is bool and value is hypothesis.target
    if hypothesis.analysis == 'category_equals':
        return type(value) is str and value == hypothesis.target
    if hypothesis.analysis == 'multilabel_contains':
        # The set is semantic, NEVER count duplicate labels as extra samples.
        return type(value) is list and hypothesis.target in value
    if hypothesis.analysis == 'sequence_equals':
        # Ordered B3 positions and repetitions remain part of the predicate.
        return type(value) is list and value == list(hypothesis.target)
    _fail('invalid_hypothesis', 'Unsupported predicate')


def _group(rows: list[dict], index: int, hypothesis: MarginalHypothesis) -> dict:
    counts = {'total_n':len(rows),'observed_n':0,'null_n':0,'absent_n':0}
    hits = 0
    values: list[int] = []
    for row in rows:
        cell = row['features'][index]
        observed = cell['observation']
        state = observed['state']
        if state == 'absent':
            counts['absent_n'] += 1
        elif state == 'null':
            counts['null_n'] += 1
        elif state == 'present':
            counts['observed_n'] += 1
            if hypothesis.analysis == 'numeric_distribution':
                value = observed['value']
                if type(value) is not int:
                    _fail('feature_type_mismatch', 'Numeric source feature changed type')
                values.append(value)
            elif _hit(observed['value'],hypothesis):
                hits += 1
        else:
            _fail('invalid_feature_state', 'Unknown/partial must not be inferred as valid observation')
    if sum(counts[k] for k in ('observed_n','null_n','absent_n')) != counts['total_n']:
        _fail('sample_count_mismatch', 'All role observations must reconcile')
    if hypothesis.analysis == 'numeric_distribution':
        bins = [0] * (len(hypothesis.numeric_boundaries)+1)
        for value in values:
            ix = 0
            while ix < len(hypothesis.numeric_boundaries) and value >= hypothesis.numeric_boundaries[ix]:
                ix += 1
            bins[ix] += 1
        if sum(bins)!=counts['observed_n']:
            _fail('sample_count_mismatch', 'Histogram must cover every observed numeric value')
        return {**counts,'median_raw':_median(values) if values else None,
                'bin_counts':bins, 'hit_n':None,'hit_fraction_observed':None}
    return {**counts,'median_raw':None,'bin_counts':None,'hit_n':hits,
            'hit_fraction_observed':str((Decimal(hits)/Decimal(counts['observed_n'])).quantize(
                Decimal('0.000001'),rounding=ROUND_HALF_EVEN)) if counts['observed_n'] else None}


def _compose(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
             outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
             matrix: CandidateFeatureMatrix, coverage: CandidateCoverageAudit,
             policy: MarginalDiscoveryPolicy) -> dict:
    # Do not bypass preceding gates or trust a SHA as permission to read/research.
    if (type(snapshot) is not CandidateResearchSnapshot or type(strata) is not CandidateStrata
        or type(outcome) is not CandidateOutcomePlan or type(controls) is not CandidateMatchedControls
        or type(matrix) is not CandidateFeatureMatrix or type(coverage) is not CandidateCoverageAudit):
        _fail('invalid_discovery_input', 'All six frozen upstream PIE candidate artifacts required')
    source = replay_candidate_snapshot(snapshot.to_json(),snapshot.snapshot_sha256).as_dict()
    groups = replay_candidate_strata(strata.to_json(),strata.artifact_sha256,snapshot).as_dict()
    comparison = replay_candidate_outcome_plan(outcome.to_json(),outcome.artifact_sha256,snapshot,strata).as_dict()
    matched = replay_candidate_controls(controls.to_json(),controls.artifact_sha256,snapshot,strata,outcome).as_dict()
    features = replay_candidate_feature_matrix(matrix.to_json(),matrix.artifact_sha256,
                                                snapshot,strata,outcome,controls).as_dict()
    qa = replay_candidate_coverage_audit(coverage.to_json(),coverage.artifact_sha256,
                                         snapshot,strata,outcome,controls,matrix).as_dict()
    if any(d.get('evidence_state')!='unverified' for d in (source,groups,comparison,matched,features,qa)):
        _fail('unverified_input', 'Cannot upgrade structurally checked dataset to authorized')
    if (not all(d['project_id']==snapshot.project_id for d in (groups,comparison,matched,features,qa))
        or coverage.matrix_sha256!=matrix.artifact_sha256
        or coverage.controls_sha256!=controls.artifact_sha256
        or features['source_controls_sha256']!=controls.artifact_sha256
        or qa['source_matrix_sha256']!=matrix.artifact_sha256):
        _fail('discovery_scope_mismatch', 'Project/input versions not aligned')
    if (policy.design_sha256!=source['design']['design_sha256']
        or policy.design_sha256!=comparison['policy']['design_sha256']
        or policy.design_sha256!=qa['policy']['design_sha256']):
        _fail('discovery_design_mismatch', 'Exact preregistered study design required')
    if _stamp(policy.registered_at)>_stamp(comparison['policy']['observation_start']):
        _fail('late_preregistration', 'Search space must be recorded before observation window')
    if qa['offline_coverage_status']!='pass' or qa['blockers'] or coverage.offline_coverage_status!='pass':
        _fail('coverage_blocked', 'Unresolved coverage blockers prevent even exploratory candidate generation')
    if qa['population']['matched_n'] != matrix.row_count or qa['population']['pairs_n']!=controls.pair_count:
        _fail('discovery_population_mismatch', 'Paired sample count differs from coverage report')
    columns = {(x['family'],x['name']):(i,x) for i,x in enumerate(features['columns'])}
    if len(columns)!=matrix.column_count:
        _fail('discovery_population_mismatch', 'Duplicate or missing matrix columns')
    audited = {(x['family'],x['name']):x for x in qa['feature_coverage']}
    high = [r for r in features['rows'] if r['role']=='high']
    low = [r for r in features['rows'] if r['role']=='low']
    if len(high)!=controls.pair_count or len(low)!=controls.pair_count:
        _fail('discovery_population_mismatch', 'High/low must each have exact matched pair count')
    report = []
    for hypothesis in policy.hypotheses:
        identity = (hypothesis.family,hypothesis.feature_name)
        if identity not in columns or identity not in audited:
            _fail('unsupported_feature', 'Unregistered or unknown UAE feature')
        i, col = columns[identity]
        if col['representation'] != _TYPES[hypothesis.analysis]:
            _fail('feature_type_mismatch', 'Hypothesis must match frozen feature representation')
        coverage_col = audited[identity]
        for role in ('high','low'):
            stats = coverage_col['matched_roles'][role]
            if stats['suppressed'] or stats['n'] < qa['policy']['min_cell_disclosure_n']:
                _fail('coverage_suppressed', 'Suppressed role cannot be included in discovery output')
        arm_high,arm_low=_group(high,i,hypothesis),_group(low,i,hypothesis)
        if any(arm['observed_n']<policy.minimum_observed_per_role for arm in (arm_high,arm_low)):
            _fail('insufficient_observed_n', 'Insufficient observed values in a registered hypothesis')
        for name,arm in (('high',arm_high),('low',arm_low)):
            counts=coverage_col['matched_roles'][name]
            if (arm['observed_n']!=counts['present_n'] or arm['null_n']!=counts['null_n']
                or arm['absent_n']!=counts['absent_n'] or arm['total_n']!=counts['n']):
                _fail('discovery_population_mismatch', 'Coverage and marginal observations disagree')
        report.append({'hypothesis':asdict(hypothesis),'high':arm_high,'low':arm_low,
                       'reporting_status':'descriptive_only_no_inference'})
    selection = features['population']
    return {'schema_version':SCHEMA,'evidence_state':'unverified','approved_live':False,
        'project_id':snapshot.project_id,'source_snapshot_sha256':snapshot.snapshot_sha256,
        'source_strata_sha256':strata.artifact_sha256,'source_outcome_sha256':outcome.artifact_sha256,
        'source_controls_sha256':controls.artifact_sha256,'source_matrix_sha256':matrix.artifact_sha256,
        'source_coverage_sha256':coverage.artifact_sha256,
        'policy':asdict(policy),'policy_sha256':policy.policy_sha256,
        'hypothesis_family_sha256':canonical_hash({'hypotheses':[asdict(h) for h in policy.hypotheses]}),
        'registered_hypothesis_count':len(policy.hypotheses),
        'comparisons_per_hypothesis':2, 'rejected_implicit_tests':True,
        'matched_n':matrix.row_count,'pairs_n':controls.pair_count,
        'source_population':{'scanned_n':qa['population']['scanned_n'],
            'included_n':qa['population']['included_n'],'matched_n':qa['population']['matched_n'],
            'unmatched_n':qa['population']['unmatched_n'], 'neutral_n':qa['population']['neutral_n'],
            'other_strata_n':qa['population']['other_strata_n'],
            'excluded_n':qa['population']['excluded_n'],
            'excluded_by_reason':qa['population']['excluded_by_reason']},
        'hypotheses':report,'warnings':['exploratory_only','no_statistical_significance',
                                      'multiple_testing_uncorrected','not_live_authorized',
                                      'selection_bias_unknown','not_run_partial_unknown'],
        'p_values':None,'adjusted_p_values':None,'causal_claim':False}


def _freeze(payload: dict) -> CandidateMarginalDiscovery:
    encoded = json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    if len(encoded.encode('utf-8'))>_MAX_RAW_BYTES:
        _fail('payload_too_large', 'Bounded discovery output exceeds cap')
    return CandidateMarginalDiscovery(sha256(encoded.encode('utf-8')).hexdigest(),payload['project_id'],
        payload['source_coverage_sha256'],payload['source_matrix_sha256'],payload['policy_sha256'],
        payload['registered_hypothesis_count'],payload['matched_n'],encoded)


def build_candidate_marginal_discovery(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
                                      outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
                                      matrix: CandidateFeatureMatrix, coverage: CandidateCoverageAudit,
                                      policy: MarginalDiscoveryPolicy) -> CandidateMarginalDiscovery:
    """Reproduce exact structural chain; no claims of live approval or effect."""
    if type(policy) is not MarginalDiscoveryPolicy:
        _fail('invalid_discovery_policy', 'Explicit bounded preregistered hypothesis policy required')
    return _freeze(_compose(snapshot,strata,outcome,controls,matrix,coverage,policy))


def replay_candidate_marginal_discovery(raw: str|bytes, expected_sha256: str,
                                        snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
                                        outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
                                        matrix: CandidateFeatureMatrix,
                                        coverage: CandidateCoverageAudit) -> CandidateMarginalDiscovery:
    """Offline byte/reconstruction integrity only; never current rights validation."""
    if type(raw) not in (str,bytes) or type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_discovery', 'Expected JSON and canonical SHA-256')
    if len(raw if type(raw) is bytes else raw.encode('utf-8'))>_MAX_RAW_BYTES:
        _fail('payload_too_large', 'Discovery candidate exceeds byte limit')
    def unique(pairs):
        obj={}
        for k,v in pairs:
            if k in obj:_fail('duplicate_json_key', 'Duplicate discovery JSON key')
            obj[k]=v
        return obj
    try:
        p=json.loads(raw,object_pairs_hook=unique,
                     parse_constant=lambda _: _fail('invalid_discovery','Nonfinite JSON number'))
    except PIEBoundaryError:
        raise
    except (ValueError,UnicodeError,TypeError,RecursionError) as exc:
        raise PIEBoundaryError('invalid_discovery','Malformed discovery JSON') from exc
    if type(p) is not dict or canonical_hash(p)!=expected_sha256:
        _fail('discovery_hash_mismatch','Content SHA mismatch')
    if p.get('schema_version')!=SCHEMA or p.get('evidence_state')!='unverified' or p.get('approved_live') is not False:
        _fail('unsupported_discovery','Cannot upgrade candidate to production evidence')
    spec=p.get('policy')
    if type(spec) is not dict or set(spec)!={'policy_version','design_sha256','registered_at',
                                            'registration_claim_id','minimum_observed_per_role','hypotheses'}:
        _fail('invalid_discovery_policy','Complete explicit discovery policy required')
    try:
        if type(spec['hypotheses']) is not list:
            _fail('invalid_discovery_policy','Hypothesis list must be explicit')
        hypotheses=tuple(MarginalHypothesis(hypothesis_id=h['hypothesis_id'],family=h['family'],
               feature_name=h['feature_name'],analysis=h['analysis'],
               target=tuple(h['target']) if h['analysis']=='sequence_equals' and type(h['target']) is list else h['target'],
               numeric_boundaries=tuple(h['numeric_boundaries']) if type(h['numeric_boundaries']) is list else h['numeric_boundaries'])
                for h in spec['hypotheses'])
        policy=MarginalDiscoveryPolicy(spec['policy_version'],spec['design_sha256'],
                        spec['registered_at'],spec['registration_claim_id'],
                        spec['minimum_observed_per_role'],hypotheses)
    except (TypeError,KeyError) as exc:
        raise PIEBoundaryError('invalid_discovery_policy','Malformed hypothesis registry') from exc
    rebuilt=build_candidate_marginal_discovery(snapshot,strata,outcome,controls,matrix,coverage,policy)
    if rebuilt.as_dict()!=p:
        _fail('discovery_replay_mismatch','Resealed candidate differs from real frozen feature chain')
    return rebuilt
