"""PIE-MOD-16: frozen complete-family multiplicity controls; NOT inferential approval.

Previous PIE stages produce *no p-values*. The default report therefore carries
no q-values or discoveries. An explicitly synthetic-only evidence object allows
BH arithmetic regression tests without pretending that bootstrap CIs imply p.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal, InvalidOperation, localcontext
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
from .marginal import CandidateMarginalDiscovery
from .effects import CandidateDescriptiveEffects
from .uncertainty import CandidateUncertaintyIntervals, replay_candidate_uncertainty_intervals

SCHEMA = 'pie-multiplicity-candidate/v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_VERSION = re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_IDENTIFIER = re.compile(r'[a-z][a-z0-9_]{0,79}\Z')
_DECIMAL = re.compile(r'(?:0|1)(?:\.0{1,12})?\Z|0\.[0-9]{1,12}\Z')
_MAX_BYTES = 1024 * 1024
_MAX_FAMILY = 64


def _fail(code: str, reason: str) -> None:
    raise PIEBoundaryError(code, reason)


def _probability(value: object, label: str, *, strict_positive: bool = False) -> Decimal:
    if type(value) is not str or not _DECIMAL.fullmatch(value):
        _fail('invalid_probability', f'{label} must be canonical finite decimal text in [0,1]')
    try:
        result = Decimal(value)
    except InvalidOperation as exc:
        raise PIEBoundaryError('invalid_probability', f'{label} is not numeric') from exc
    if not 0 <= result <= 1 or (strict_positive and result == 0):
        _fail('invalid_probability', f'{label} out of bounds')
    return result


@dataclass(frozen=True, slots=True)
class MultiplicityPolicy:
    policy_version: str
    method_version: str
    design_sha256: str
    hypothesis_family_sha256: str
    family_size: int
    fdr_level: str
    correction: str = 'benjamini_hochberg_fdr'
    family_scope: str = 'all_preregistered_hypotheses'
    missing_p_values: str = 'all_unavailable_or_refuse_partial'
    ranking: str = 'p_value_then_hypothesis_id'

    def __post_init__(self) -> None:
        for name in ('policy_version', 'method_version'):
            val = getattr(self, name)
            if type(val) is not str or not _VERSION.fullmatch(val):
                _fail('invalid_multiplicity_policy', 'Explicit stable policy and method version required')
        for name in ('design_sha256', 'hypothesis_family_sha256'):
            val = getattr(self, name)
            if type(val) is not str or not _SHA.fullmatch(val):
                _fail('invalid_multiplicity_policy', 'Exact frozen SHA required')
        if type(self.family_size) is not int or not 1 <= self.family_size <= _MAX_FAMILY:
            _fail('invalid_multiplicity_policy', 'Family size must be 1..64 and exact')
        if self.correction != 'benjamini_hochberg_fdr' or self.family_scope != 'all_preregistered_hypotheses' or self.missing_p_values != 'all_unavailable_or_refuse_partial' or self.ranking != 'p_value_then_hypothesis_id':
            _fail('invalid_multiplicity_policy', 'Unsupported correction or selective search policy')
        _probability(self.fdr_level, 'fdr_level', strict_positive=True)

    @property
    def policy_sha256(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class SyntheticPValue:
    hypothesis_id: str
    p_value: str

    def __post_init__(self) -> None:
        if type(self.hypothesis_id) is not str or not _IDENTIFIER.fullmatch(self.hypothesis_id):
            _fail('invalid_p_evidence', 'Expected canonical preregistered hypothesis ID')
        _probability(self.p_value, 'p_value')


@dataclass(frozen=True, slots=True)
class SyntheticPValueEvidence:
    """An invented regression fixture. Not a source-attested test/statistical result."""
    fixture_id: str
    hypothesis_family_sha256: str
    source_discovery_sha256: str
    source_effects_sha256: str
    source_uncertainty_sha256: str
    values: tuple[SyntheticPValue, ...]
    provenance: str = 'synthetic_test_fixture_only'

    def __post_init__(self) -> None:
        if type(self.fixture_id) is not str or not self.fixture_id.startswith('synthetic-') or not _VERSION.fullmatch(self.fixture_id):
            _fail('invalid_p_evidence', 'An unmistakably synthetic fixture ID is required')
        for name in ('hypothesis_family_sha256', 'source_discovery_sha256', 'source_effects_sha256', 'source_uncertainty_sha256'):
            val = getattr(self, name)
            if type(val) is not str or not _SHA.fullmatch(val):
                _fail('invalid_p_evidence', 'Input evidence needs exact frozen source SHA')
        if self.provenance != 'synthetic_test_fixture_only':
            _fail('invalid_p_evidence', 'Only synthetic fixture evidence is accepted by this stage')
        if type(self.values) is not tuple or not 1 <= len(self.values) <= _MAX_FAMILY or any(type(v) is not SyntheticPValue for v in self.values):
            _fail('invalid_p_evidence', 'Complete immutable synthetic test vector required')
        ids = tuple(v.hypothesis_id for v in self.values)
        if ids != tuple(sorted(set(ids))):
            _fail('invalid_p_evidence', 'Hypothesis IDs must be strictly sorted, unique')


def benjamini_hochberg(values: tuple[SyntheticPValue, ...], alpha: str) -> tuple[dict, ...]:
    """Exact Decimal BH step-up q=min_{j>=i}(m*p_(j)/j,1).

    This is arithmetic only, not validation of the p-value-generating process.
    All hypotheses are kept; sorted tie-breaking is deterministic.
    """
    if type(values) is not tuple or not 1 <= len(values) <= _MAX_FAMILY or any(type(v) is not SyntheticPValue for v in values):
        _fail('invalid_p_evidence', 'Complete typed p-value vector required')
    ids = [v.hypothesis_id for v in values]
    if len(set(ids)) != len(ids):
        _fail('invalid_p_evidence', 'Duplicate hypothesis IDs')
    limit = _probability(alpha, 'alpha', strict_positive=True)
    m = len(values)
    ranks = sorted(values, key=lambda item: (_probability(item.p_value, 'p_value'), item.hypothesis_id))
    results = {}
    running = Decimal(1)
    # Context avoids dependence on caller's decimal precision/rounding mode.
    with localcontext() as ctx:
        ctx.prec = 100
        for rank in range(m, 0, -1):
            entry = ranks[rank - 1]
            p = Decimal(entry.p_value)
            running = min(running, p * m / rank)
            results[entry.hypothesis_id] = {
                'hypothesis_id': entry.hypothesis_id,
                'p_value': entry.p_value,
                'adjusted_q_value': format(running, 'f'),
                'bh_rank': rank,
                'passes_synthetic_bh_threshold': bool(running <= limit),
            }
    return tuple(results[item.hypothesis_id] for item in values)


@dataclass(frozen=True, slots=True)
class CandidateMultiplicityReport:
    artifact_sha256: str
    project_id: str
    source_uncertainty_sha256: str
    policy_sha256: str
    family_size: int
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


def _validated_source(snapshot, strata, outcome, controls, matrix, coverage, discovery, effects, uncertainty):
    if type(uncertainty) is not CandidateUncertaintyIntervals or type(discovery) is not CandidateMarginalDiscovery or type(effects) is not CandidateDescriptiveEffects:
        _fail('invalid_multiplicity_input', 'Expected exact typed source artifacts')
    rebuilt = replay_candidate_uncertainty_intervals(uncertainty.to_json(), uncertainty.artifact_sha256,
              snapshot, strata, outcome, controls, matrix, coverage, discovery, effects)
    if rebuilt != uncertainty:
        _fail('multiplicity_lineage_mismatch', 'Frozen interval source wrapper altered')
    u=rebuilt.as_dict(); d=discovery.as_dict(); e=effects.as_dict()
    if (u.get('schema_version')!='pie-paired-bootstrap-uncertainty/v1'
        or u.get('evidence_state')!='unverified' or u.get('approved_live') is not False
        or u.get('p_values') is not None or u.get('adjusted_p_values') is not None
        or u.get('project_id')!=snapshot.project_id or u.get('project_id')!=e.get('project_id')
        or u.get('project_id')!=d.get('project_id')
        or u.get('source_effects_sha256')!=effects.artifact_sha256
        or u.get('source_discovery_sha256')!=discovery.artifact_sha256
        or e.get('source_discovery_sha256')!=discovery.artifact_sha256
        or e.get('p_values') is not None or d.get('p_values') is not None
        or u.get('registered_hypothesis_family_sha256')!=d.get('hypothesis_family_sha256')
        or e.get('registered_hypothesis_family_sha256')!=d.get('hypothesis_family_sha256')):
        _fail('multiplicity_lineage_mismatch', 'Project/version/family identity mismatch')
    family = d.get('hypotheses'); intervals=u.get('all_hypotheses'); effect_rows=e.get('effects')
    if any(type(v) is not list for v in (family, intervals, effect_rows)):
        _fail('multiplicity_lineage_mismatch', 'No complete frozen registered hypothesis family')
    ids=[item['hypothesis']['hypothesis_id'] for item in family]
    if (not 1 <= len(ids) <= _MAX_FAMILY or ids != sorted(set(ids))
        or ids != [x['hypothesis_id'] for x in intervals]
        or ids != [x['hypothesis_id'] for x in effect_rows]
        or len(ids)!=d.get('registered_hypothesis_count')
        or len(ids)!=e.get('registered_hypothesis_count')
        or len(ids)!=u.get('registered_hypothesis_count')
        or len(ids)!=uncertainty.interval_count):
        _fail('multiplicity_family_mismatch', 'Missing, duplicate, or reordered registered hypothesis')
    return u,d,e,tuple(ids)


def _compose(snapshot, strata, outcome, controls, matrix, coverage, discovery, effects, uncertainty,
             policy: MultiplicityPolicy, synthetic: SyntheticPValueEvidence | None):
    u,d,e,ids = _validated_source(snapshot,strata,outcome,controls,matrix,coverage,discovery,effects,uncertainty)
    if (policy.design_sha256 != d['policy']['design_sha256']
        or policy.design_sha256 != e['policy']['design_sha256']
        or policy.design_sha256 != u['policy']['design_sha256']
        or policy.hypothesis_family_sha256 != d['hypothesis_family_sha256']
        or policy.family_size != len(ids)):
        _fail('multiplicity_policy_mismatch', 'Multiplicity policy not pinned to full preregistered family')
    if synthetic is not None and type(synthetic) is not SyntheticPValueEvidence:
        _fail('p_value_unavailable', 'Untrusted p-value evidence is not supported')
    if synthetic is None:
        entries = [{'hypothesis_id':i, 'p_value':None, 'adjusted_q_value':None,
                    'status':'not_tested_no_p_value_producer', 'passes_synthetic_bh_threshold':None}
                   for i in ids]
        state = 'blocked_no_inferential_p_values'
        evidence = None
    else:
        if (synthetic.hypothesis_family_sha256 != policy.hypothesis_family_sha256
            or synthetic.source_discovery_sha256 != discovery.artifact_sha256
            or synthetic.source_effects_sha256 != effects.artifact_sha256
            or synthetic.source_uncertainty_sha256 != uncertainty.artifact_sha256
            or tuple(x.hypothesis_id for x in synthetic.values) != ids):
            _fail('p_value_family_mismatch', 'Synthetic vector must contain every exact registered hypothesis')
        entries = [{**row,'status':'synthetic_bh_arithmetic_only'} for row in benjamini_hochberg(synthetic.values,policy.fdr_level)]
        state = 'synthetic_fixture_arithmetic_not_inference'
        evidence = asdict(synthetic)
    return {
        'schema_version': SCHEMA, 'evidence_state':'unverified', 'approved_live':False,
        'project_id':snapshot.project_id,
        'source_discovery_sha256':discovery.artifact_sha256,
        'source_effects_sha256':effects.artifact_sha256,
        'source_uncertainty_sha256':uncertainty.artifact_sha256,
        'hypothesis_family_sha256':d['hypothesis_family_sha256'],
        'registered_hypothesis_count':len(ids), 'tested_hypothesis_count':(len(ids) if synthetic is not None else 0),
        'policy':asdict(policy),'policy_sha256':policy.policy_sha256,
        'source_p_values':e.get('p_values'),
        'evidence_kind':'synthetic_test_fixture_only' if synthetic is not None else 'none',
        'synthetic_fixture':evidence,
        'correction_status':state,
        'hypotheses':entries,
        'warnings':['unverified_source','unverified_review','no_confirmatory_inference',
                    'not_calibrated_p_value_producer','multiplicity_status_not_authorization',
                    'no_significance_publication','causal_claims_forbidden'],
        'confirmed_discoveries':None,'can_publish_significance':False,'causal_claim':False,
    }


def _freeze(p: dict) -> CandidateMultiplicityReport:
    raw=json.dumps(p,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    if len(raw.encode('utf-8'))>_MAX_BYTES:
        _fail('payload_too_large','Multiplicity candidate exceeds size cap')
    return CandidateMultiplicityReport(sha256(raw.encode('utf-8')).hexdigest(),p['project_id'],
        p['source_uncertainty_sha256'],p['policy_sha256'],p['registered_hypothesis_count'],raw)


def build_candidate_multiplicity_report(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
              outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
              matrix: CandidateFeatureMatrix, coverage: CandidateCoverageAudit,
              discovery: CandidateMarginalDiscovery, effects: CandidateDescriptiveEffects,
              uncertainty: CandidateUncertaintyIntervals, policy: MultiplicityPolicy,
              synthetic: SyntheticPValueEvidence | None = None) -> CandidateMultiplicityReport:
    """Always candidate-only. No p-source -> block; synthetic source -> QA only."""
    if type(policy) is not MultiplicityPolicy:
        _fail('invalid_multiplicity_policy','Explicit frozen correction design required')
    return _freeze(_compose(snapshot,strata,outcome,controls,matrix,coverage,discovery,effects,uncertainty,policy,synthetic))


def replay_candidate_multiplicity_report(raw: str | bytes, expected_sha256: str,
              snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
              outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
              matrix: CandidateFeatureMatrix, coverage: CandidateCoverageAudit,
              discovery: CandidateMarginalDiscovery, effects: CandidateDescriptiveEffects,
              uncertainty: CandidateUncertaintyIntervals) -> CandidateMultiplicityReport:
    if type(raw) not in (str,bytes) or type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_multiplicity_artifact','Expected canonical JSON and SHA256')
    if len(raw if type(raw) is bytes else raw.encode('utf-8'))>_MAX_BYTES:
        _fail('payload_too_large','Oversized correction report')
    def unique(pairs):
        d={}
        for k,v in pairs:
            if k in d:_fail('duplicate_json_key','Duplicate JSON key')
            d[k]=v
        return d
    try:
        payload=json.loads(raw,object_pairs_hook=unique,
            parse_constant=lambda _: _fail('invalid_multiplicity_artifact','Nonfinite JSON number'))
    except PIEBoundaryError:raise
    except (ValueError,UnicodeError,TypeError,RecursionError) as exc:
        raise PIEBoundaryError('invalid_multiplicity_artifact','Malformed report') from exc
    if type(payload) is not dict or canonical_hash(payload)!=expected_sha256:
        _fail('multiplicity_hash_mismatch','Report SHA does not match payload')
    if (payload.get('schema_version')!=SCHEMA or payload.get('evidence_state')!='unverified'
        or payload.get('approved_live') is not False or payload.get('can_publish_significance') is not False
        or payload.get('confirmed_discoveries') is not None):
        _fail('unsupported_multiplicity_artifact','Cannot promote candidate report into live evidence')
    p=payload.get('policy')
    if type(p) is not dict or set(p)!=set(MultiplicityPolicy.__dataclass_fields__):
        _fail('invalid_multiplicity_policy','Malformed correction design')
    try:
        policy=MultiplicityPolicy(**p)
    except (TypeError,KeyError) as exc:
        raise PIEBoundaryError('invalid_multiplicity_policy','Malformed correction policy') from exc
    item=payload.get('synthetic_fixture')
    if item is not None:
        if type(item) is not dict or set(item)!=set(SyntheticPValueEvidence.__dataclass_fields__):
            _fail('invalid_p_evidence','Malformed synthetic test fixture')
        try:
            if type(item['values']) is not list:
                _fail('invalid_p_evidence','Synthetic fixture values must be list')
            values=tuple(SyntheticPValue(**x) for x in item['values'])
            synthetic=SyntheticPValueEvidence(**{**item,'values':values})
        except (TypeError,KeyError) as exc:
            raise PIEBoundaryError('invalid_p_evidence','Invalid synthetic fixture') from exc
    else:
        synthetic=None
    rebuilt=build_candidate_multiplicity_report(snapshot,strata,outcome,controls,matrix,coverage,
                                                 discovery,effects,uncertainty,policy,synthetic)
    if rebuilt.as_dict()!=payload:
        _fail('multiplicity_replay_mismatch','Altered result or unsupported test evidence')
    return rebuilt
