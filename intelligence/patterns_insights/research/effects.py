"""PIE-MOD-14: descriptive effect estimates from frozen marginal candidates.

All counts and medians come from replayed stage13; no implicit post-hoc
hypothesis selection, causal conclusion, statistical significance, or live grant.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation, ROUND_HALF_EVEN, localcontext
from fractions import Fraction
from hashlib import sha256
import json
import re

from ..contracts import PIEBoundaryError, canonical_hash
from .dataset_builder import CandidateResearchSnapshot
from .strata import CandidateStrata
from .outcomes import CandidateOutcomePlan
from .controls import CandidateMatchedControls
from .feature_matrix import CandidateFeatureMatrix
from .coverage import CandidateCoverageAudit
from .marginal import CandidateMarginalDiscovery, replay_candidate_marginal_discovery

SCHEMA = 'pie-descriptive-effects/v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_VERSION = re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_MAX_BYTES = 128 * 1024 * 1024


def _fail(code: str, detail: str) -> None:
    raise PIEBoundaryError(code, detail)


@dataclass(frozen=True, slots=True)
class EffectEstimationPolicy:
    """Whole registered family only; unweighted, high-minus-low and explicit rules."""
    policy_version: str
    design_sha256: str
    method_version: str
    minimum_observed_per_role: int
    decimal_places: int
    direction: str = 'high_minus_low'
    estimator: str = 'unweighted_observed_only'
    zero_baseline_ratio: str = 'undefined'
    numeric_shift: str = 'histogram_total_variation'

    def __post_init__(self) -> None:
        if (type(self.policy_version) is not str or not _VERSION.fullmatch(self.policy_version)
            or type(self.method_version) is not str or not _VERSION.fullmatch(self.method_version)):
            _fail('invalid_effect_policy', 'Versioned effect method/policy required')
        if type(self.design_sha256) is not str or not _SHA.fullmatch(self.design_sha256):
            _fail('invalid_effect_policy', 'Pinned research design SHA-256 required')
        if type(self.minimum_observed_per_role) is not int or not 1 <= self.minimum_observed_per_role <= 50000:
            _fail('invalid_effect_policy', 'Explicit positive per-role observation minimum required')
        if type(self.decimal_places) is not int or not 0 <= self.decimal_places <= 12:
            _fail('invalid_effect_policy', 'Explicit bounded display precision required')
        if (self.direction != 'high_minus_low' or self.estimator != 'unweighted_observed_only'
            or self.zero_baseline_ratio != 'undefined'
            or self.numeric_shift != 'histogram_total_variation'):
            _fail('invalid_effect_policy', 'Method semantics cannot silently change')

    @property
    def policy_sha256(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class CandidateDescriptiveEffects:
    artifact_sha256: str
    project_id: str
    source_discovery_sha256: str
    policy_sha256: str
    effect_count: int
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


def _positive_int(v: object, label: str) -> int:
    if type(v) is not int or not 1 <= v <= 50000:
        _fail('effect_invalid_denominator', f'{label} must be positive bounded count')
    return v


def _count(v: object, denom: int, label: str) -> int:
    if type(v) is not int or not 0 <= v <= denom:
        _fail('effect_invalid_count', f'{label} outside denominator')
    return v


def _quantized(fr: Fraction, places: int) -> str:
    # Arithmetic is exact Fraction until final explicit display rounding.
    with localcontext() as ctx:
        ctx.prec = 80
        value = Decimal(fr.numerator) / Decimal(fr.denominator)
        return format(value.quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_EVEN), 'f')


def _fraction(fr: Fraction, places: int) -> dict:
    return {'numerator': fr.numerator, 'denominator': fr.denominator,
            'display_decimal': _quantized(fr, places)}


def _median(v: object) -> Decimal:
    if type(v) is not str or not re.fullmatch(r'-?(?:0|[1-9]\d*)(?:\.\d+)?', v):
        _fail('effect_invalid_median', 'Expected explicit finite decimal median')
    try:
        n = Decimal(v)
    except InvalidOperation as exc:
        raise PIEBoundaryError('effect_invalid_median', 'Malformed decimal median') from exc
    if not n.is_finite():
        _fail('effect_invalid_median', 'Nonfinite median')
    return n


def _arm_stats(arm: dict, role: str, minimum: int) -> tuple[int, int]:
    if type(arm) is not dict:
        _fail('effect_invalid_arm', 'Expected frozen marginal arm stats')
    n = _positive_int(arm.get('observed_n'), role + '.observed_n')
    total = _positive_int(arm.get('total_n'), role + '.total_n')
    null = _count(arm.get('null_n'), total, role + '.null_n')
    absent = _count(arm.get('absent_n'), total, role + '.absent_n')
    if n < minimum or n + null + absent != total:
        _fail('effect_insufficient_sample', 'Missingness or minimum N inconsistent')
    return n, total


def _effect(entry: dict, policy: EffectEstimationPolicy, *, numeric_unit: str | None = None) -> dict:
    spec = entry['hypothesis']
    high, low = entry['high'], entry['low']
    H, Htotal = _arm_stats(high, 'high', policy.minimum_observed_per_role)
    L, Ltotal = _arm_stats(low, 'low', policy.minimum_observed_per_role)
    meta = {
        'hypothesis_id': spec['hypothesis_id'], 'feature_family': spec['family'],
        'feature_name': spec['feature_name'], 'analysis': spec['analysis'],
        'target': spec['target'], 'numeric_boundaries': spec['numeric_boundaries'],
        'high_observed_n': H, 'low_observed_n': L,
        'high_total_n': Htotal, 'low_total_n': Ltotal,
        'high_null_n': high['null_n'], 'low_null_n': low['null_n'],
        'high_absent_n': high['absent_n'], 'low_absent_n': low['absent_n'],
        'estimator': policy.estimator, 'comparison': policy.direction,
        'reporting_status': 'descriptive_only_no_significance',
    }
    if spec['analysis'] == 'numeric_distribution':
        if type(numeric_unit) is not str or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}',numeric_unit):
            _fail('effect_unknown_unit', 'Numeric effect must retain explicit frozen feature unit')
        med_high, med_low = _median(high['median_raw']), _median(low['median_raw'])
        binsH, binsL = high['bin_counts'], low['bin_counts']
        if (type(binsH) is not list or type(binsL) is not list or len(binsH) != len(binsL)
            or len(binsH) != len(spec['numeric_boundaries']) + 1
            or any(type(x) is not int or x < 0 for x in binsH + binsL)
            or sum(binsH) != H or sum(binsL) != L or high['hit_n'] is not None
            or low['hit_n'] is not None):
            _fail('effect_invalid_bins', 'Histogram bins/counts inconsistent')
        # TV = (1/2) sum_k |high_k/H - low_k/L|, exact rational in [0, 1].
        tv = sum((abs(Fraction(h, H) - Fraction(l, L)) for h,l in zip(binsH,binsL)), Fraction(0)) / 2
        diff = med_high - med_low
        if not diff.is_finite():
            _fail('effect_invalid_median', 'Median difference nonfinite')
        return {**meta, 'method':'median_difference_plus_binned_total_variation',
            'unit':numeric_unit,
            'median_high_raw':high['median_raw'], 'median_low_raw':low['median_raw'],
            'median_difference_high_minus_low_raw':format(diff,'f'),
            'histogram_high_counts':list(binsH),'histogram_low_counts':list(binsL),
            'histogram_tv_distance':_fraction(tv,policy.decimal_places),
            'histogram_tv_unit':'fraction_of_observed_distribution',
            'risk_difference':None,'risk_ratio':None}
    h, l = _count(high.get('hit_n'), H, 'high.hit_n'), _count(low.get('hit_n'),L,'low.hit_n')
    if any(arm['bin_counts'] is not None or arm['median_raw'] is not None for arm in (high,low)):
        _fail('effect_invalid_arm','Predicate statistics must not contain histogram/median')
    risk_high,risk_low=Fraction(h,H),Fraction(l,L)
    delta=risk_high-risk_low
    # Zero lower-arm hit count is not an infinity or made-up correction.
    ratio=None if l==0 else _fraction(risk_high/risk_low,policy.decimal_places)
    return {**meta,'method':'observed_binary_risk_difference_and_ratio',
            'unit':'probability_fraction', 'high_hit_n':h,'low_hit_n':l,
            'high_hit_rate':_fraction(risk_high,policy.decimal_places),
            'low_hit_rate':_fraction(risk_low,policy.decimal_places),
            'risk_difference':_fraction(delta,policy.decimal_places),
            'risk_ratio':ratio,'risk_ratio_status':'undefined_zero_baseline' if l==0 else 'defined',
            'risk_ratio_unit':'dimensionless',
            'median_high_raw':None,'median_low_raw':None,
            'median_difference_high_minus_low_raw':None,
            'histogram_tv_distance':None}


def _compose(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
             outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
             matrix: CandidateFeatureMatrix, coverage: CandidateCoverageAudit,
             discovery: CandidateMarginalDiscovery, policy: EffectEstimationPolicy) -> dict:
    if type(discovery) is not CandidateMarginalDiscovery:
        _fail('invalid_effect_input', 'Exact frozen marginal candidate required')
    previous = replay_candidate_marginal_discovery(discovery.to_json(),discovery.artifact_sha256,
                snapshot,strata,outcome,controls,matrix,coverage)
    if previous != discovery:
        _fail('effect_source_mismatch', 'Marginal candidate wrapper altered')
    d=previous.as_dict()
    if (d['evidence_state'] != 'unverified' or d['approved_live'] is not False
        or d['project_id'] != discovery.project_id or d['project_id'] != snapshot.project_id
        or d['source_coverage_sha256'] != coverage.artifact_sha256
        or d['source_matrix_sha256'] != matrix.artifact_sha256):
        _fail('effect_source_mismatch', 'Unverified source and exact identity required')
    if (policy.design_sha256 != d['policy']['design_sha256']
        or policy.minimum_observed_per_role < d['policy']['minimum_observed_per_role']):
        _fail('effect_policy_mismatch', 'Effect rule cannot weaken registered minimum/design')
    hypotheses=d['hypotheses']
    if (type(hypotheses) is not list or len(hypotheses)!=discovery.hypothesis_count
        or len(hypotheses)!=d['registered_hypothesis_count']):
        _fail('effect_search_space_mismatch', 'All registered hypotheses must be estimated')
    matrix_columns={(c['family'],c['name']):c for c in matrix.as_dict()['columns']}
    effect_rows=[]
    for item in hypotheses:
        hypothesis=item['hypothesis']
        source_column=matrix_columns.get((hypothesis['family'],hypothesis['feature_name']))
        if source_column is None:
            _fail('effect_source_mismatch', 'Effect feature absent from frozen matrix')
        effect_rows.append(_effect(item,policy,numeric_unit=source_column['unit']))
    if len(effect_rows)!=len(hypotheses):
        _fail('effect_search_space_mismatch', 'No silent exclusion of candidate effects')
    return {'schema_version':SCHEMA, 'evidence_state':'unverified', 'approved_live':False,
        'project_id':snapshot.project_id,'source_discovery_sha256':discovery.artifact_sha256,
        'source_coverage_sha256':coverage.artifact_sha256,
        'source_matrix_sha256':matrix.artifact_sha256,
        'registered_hypothesis_family_sha256':d['hypothesis_family_sha256'],
        'registered_hypothesis_count':d['registered_hypothesis_count'],
        'effect_count':len(effect_rows), 'policy':asdict(policy),'policy_sha256':policy.policy_sha256,
        'matched_n':discovery.matched_n, 'pairs_n':d['pairs_n'],
        'source_population':d['source_population'], 'effects':effect_rows,
        'warnings':['exploratory_descriptive_only','not_live_authorized',
            'matched_pair_dependence_not_modeled','unweighted_no_causal_inference',
            'no_uncertainty_interval','multiple_testing_uncorrected','selection_bias_unknown'],
        'p_values':None,'adjusted_p_values':None,'confidence_intervals':None,
        'causal_claim':False}


def _freeze(payload: dict) -> CandidateDescriptiveEffects:
    raw=json.dumps(payload,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    if len(raw.encode('utf-8'))>_MAX_BYTES:
        _fail('payload_too_large','Descriptive effect output exceeds size limit')
    return CandidateDescriptiveEffects(sha256(raw.encode('utf-8')).hexdigest(),payload['project_id'],
        payload['source_discovery_sha256'],payload['policy_sha256'],payload['effect_count'],raw)


def build_candidate_descriptive_effects(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
              outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
              matrix: CandidateFeatureMatrix, coverage: CandidateCoverageAudit,
              discovery: CandidateMarginalDiscovery,
              policy: EffectEstimationPolicy) -> CandidateDescriptiveEffects:
    """No inferential claim, no live read grant; recompute entire prior artifact chain."""
    if type(policy) is not EffectEstimationPolicy:
        _fail('invalid_effect_policy','An explicit typed estimation policy is mandatory')
    return _freeze(_compose(snapshot,strata,outcome,controls,matrix,coverage,discovery,policy))


def replay_candidate_descriptive_effects(raw: str|bytes, expected_sha256: str,
              snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
              outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
              matrix: CandidateFeatureMatrix, coverage: CandidateCoverageAudit,
              discovery: CandidateMarginalDiscovery) -> CandidateDescriptiveEffects:
    """Check checksum AND recompute every effect; replay is not authorization."""
    if type(raw) not in (str,bytes) or type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_effect_artifact','Expected canonical JSON + SHA256')
    if len(raw if type(raw) is bytes else raw.encode('utf-8'))>_MAX_BYTES:
        _fail('payload_too_large','Effect artifact exceeds byte cap')
    def unique(pairs):
        r={}
        for k,v in pairs:
            if k in r:_fail('duplicate_json_key','Duplicate effects JSON key')
            r[k]=v
        return r
    try:
        payload=json.loads(raw,object_pairs_hook=unique,
                           parse_constant=lambda _: _fail('invalid_effect_artifact','Nonfinite JSON'))
    except PIEBoundaryError:
        raise
    except (ValueError,UnicodeError,TypeError,RecursionError) as exc:
        raise PIEBoundaryError('invalid_effect_artifact','Malformed effect JSON') from exc
    if type(payload) is not dict or canonical_hash(payload)!=expected_sha256:
        _fail('effect_hash_mismatch','Effects content/hash mismatch')
    if (payload.get('schema_version')!=SCHEMA or payload.get('evidence_state')!='unverified'
        or payload.get('approved_live') is not False):
        _fail('unsupported_effect_artifact','Cannot upgrade effects to live evidence')
    policy_payload=payload.get('policy')
    if type(policy_payload) is not dict or set(policy_payload)!={
        'policy_version','design_sha256','method_version','minimum_observed_per_role',
        'decimal_places','direction','estimator','zero_baseline_ratio','numeric_shift'
    }:
        _fail('invalid_effect_policy','Complete exact effect policy required')
    try:
        policy=EffectEstimationPolicy(**policy_payload)
    except (TypeError,KeyError) as exc:
        raise PIEBoundaryError('invalid_effect_policy','Malformed effect policy') from exc
    rebuilt=build_candidate_descriptive_effects(snapshot,strata,outcome,controls,matrix,coverage,discovery,policy)
    if rebuilt.as_dict()!=payload:
        _fail('effect_replay_mismatch','Resealed effect differs from recomputed source evidence')
    return rebuilt
