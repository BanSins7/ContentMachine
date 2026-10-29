"""PIE-MOD-20: predeclared paired interactions, exploratory ONLY.

Never substitutes descriptive lift/excess co-occurrence for calibrated inference.
The frozen registered family is preserved even if some features are absent.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_EVEN, localcontext
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

SCHEMA = 'pie-pairwise-interactions-candidate/v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'[a-z][a-z0-9_]{0,79}\Z')
_VERSION = re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_MAX_HYPOTHESES = 32
_MAX_BYTES = 16 * 1024 * 1024
_MAX_PAIR_OPERATIONS = 100_000
_TYPES = {
    'boolean_equals': 'boolean',
    'category_equals': 'category',
    'multilabel_contains': 'multi_label',
    'sequence_equals': 'ordered_sequence',
}


def _fail(code: str, reason: str) -> None:
    raise PIEBoundaryError(code, reason)


def _timestamp(value: str) -> datetime:
    if type(value) is not str or not 10 <= len(value) <= 40:
        _fail('invalid_interaction_registration','Timezone-aware registration clock required')
    try:
        stamp = datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as exc:
        raise PIEBoundaryError('invalid_interaction_registration','Malformed registration clock') from exc
    if stamp.utcoffset() is None:
        _fail('invalid_interaction_registration','Registration clock requires offset')
    return stamp.astimezone(timezone.utc)


def _fraction(value: Fraction | None) -> dict | None:
    if value is None:
        return None
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.rounding = ROUND_HALF_EVEN
        approx = (Decimal(value.numerator)/Decimal(value.denominator)).quantize(Decimal('0.000000000001'))
    return {'numerator':value.numerator,'denominator':value.denominator,
            'decimal_12dp':format(approx,'f')}


def _text(value: object) -> bool:
    return type(value) is str and 1 <= len(value) <= 120 and all(ord(c) >= 32 for c in value)


@dataclass(frozen=True, slots=True)
class InteractionPredicate:
    family: str
    feature_name: str
    operation: str
    target: bool | str | tuple[str,...]

    def __post_init__(self) -> None:
        if (type(self.family) is not str or self.family not in ('a','b')
            or type(self.feature_name) is not str or not _ID.fullmatch(self.feature_name)
            or type(self.operation) is not str or self.operation not in _TYPES):
            _fail('invalid_interaction_predicate','Require named typed Group A/B predicate')
        if self.operation == 'boolean_equals':
            valid = type(self.target) is bool
        elif self.operation in ('category_equals','multilabel_contains'):
            valid = _text(self.target)
        else:
            valid = (type(self.target) is tuple and 1 <= len(self.target) <= 32
                     and all(_text(v) for v in self.target))
        if not valid:
            _fail('invalid_interaction_predicate','Nonempty exact typed predicate target required')

    @property
    def identity_sha256(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class InteractionHypothesis:
    hypothesis_id: str
    left: InteractionPredicate
    right: InteractionPredicate

    def __post_init__(self) -> None:
        if type(self.hypothesis_id) is not str or not _ID.fullmatch(self.hypothesis_id):
            _fail('invalid_interaction_hypothesis','Stable preregistered hypothesis ID required')
        if type(self.left) is not InteractionPredicate or type(self.right) is not InteractionPredicate:
            _fail('invalid_interaction_hypothesis','Two exact typed predicates required')
        a=(self.left.family,self.left.feature_name)
        b=(self.right.family,self.right.feature_name)
        if a >= b:
            _fail('invalid_interaction_hypothesis','Distinct canonical A/B feature order required')


@dataclass(frozen=True, slots=True)
class InteractionPolicy:
    policy_version: str
    method_version: str
    design_sha256: str
    snapshot_sha256: str
    controls_sha256: str
    matrix_sha256: str
    holdout_sha256: str
    registered_at: str
    registration_claim_id: str
    minimum_discovery_pairs: int
    minimum_joint_hits_per_role: int
    hypotheses: tuple[InteractionHypothesis,...]
    cohort_scope: str = 'discovery_only'
    family_scope: str = 'entire_preregistered_interaction_family'
    missingness: str = 'block_if_any_discovery_pair_feature_missing'
    multiplicity_mode: str = 'blocked_no_valid_inferential_p_values'
    confidence_mode: str = 'none_without_valid_independent_inference'

    def __post_init__(self) -> None:
        for name in ('policy_version','method_version','registration_claim_id'):
            val=getattr(self,name)
            if type(val) is not str or not _VERSION.fullmatch(val):
                _fail('invalid_interaction_policy','Explicit method/version and registration claim required')
        for name in ('design_sha256','snapshot_sha256','controls_sha256','matrix_sha256','holdout_sha256'):
            val=getattr(self,name)
            if type(val) is not str or not _SHA.fullmatch(val):
                _fail('invalid_interaction_policy','All source SHA pins required')
        _timestamp(self.registered_at)
        for name in ('minimum_discovery_pairs','minimum_joint_hits_per_role'):
            val=getattr(self,name)
            if type(val) is not int or not 1 <= val <= 1000:
                _fail('invalid_interaction_policy','Explicit positive bounded support thresholds required')
        if (type(self.hypotheses) is not tuple
            or not 1 <= len(self.hypotheses) <= _MAX_HYPOTHESES
            or any(type(x) is not InteractionHypothesis for x in self.hypotheses)):
            _fail('invalid_interaction_policy','Require complete 1..32 hypothesis family')
        ids=tuple(h.hypothesis_id for h in self.hypotheses)
        if ids != tuple(sorted(set(ids))):
            _fail('invalid_interaction_policy','Hypothesis IDs must be unique and sorted')
        signatures=[(h.left.identity_sha256,h.right.identity_sha256) for h in self.hypotheses]
        if len(signatures) != len(set(signatures)):
            _fail('duplicate_interaction_hypothesis','Cannot repeat a pair with a new ID')
        if (self.cohort_scope != 'discovery_only'
            or self.family_scope != 'entire_preregistered_interaction_family'
            or self.missingness != 'block_if_any_discovery_pair_feature_missing'
            or self.multiplicity_mode != 'blocked_no_valid_inferential_p_values'
            or self.confidence_mode != 'none_without_valid_independent_inference'):
            _fail('invalid_interaction_policy','Partial family, live or significance promotion is prohibited')

    @property
    def policy_sha256(self) -> str:
        return canonical_hash(asdict(self))

    @property
    def family_sha256(self) -> str:
        return canonical_hash({'hypotheses':[asdict(h) for h in self.hypotheses]})


@dataclass(frozen=True, slots=True)
class CandidateInteractionReport:
    artifact_sha256: str
    project_id: str
    policy_sha256: str
    registered_family_sha256: str
    hypothesis_count: int
    _json: str = field(repr=False)

    @property
    def verified_live(self) -> bool: return False

    @property
    def ready_for_analysis(self) -> bool: return False

    def to_json(self) -> str: return self._json

    def as_dict(self) -> dict: return json.loads(self._json)


def _hit(value: object, p: InteractionPredicate) -> bool:
    if p.operation == 'boolean_equals':
        if type(value) is not bool: _fail('interaction_feature_type_mismatch','Expected exact Boolean')
        return value is p.target
    if p.operation == 'category_equals':
        if type(value) is not str: _fail('interaction_feature_type_mismatch','Expected exact text')
        return value == p.target
    if type(value) is not list or any(type(x) is not str for x in value):
        _fail('interaction_feature_type_mismatch','Expected ordered text list')
    if p.operation == 'multilabel_contains':
        return p.target in value
    return value == list(p.target)  # B3 ordered sequence; repeated positions preserved.


def _compose(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
             outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
             matrix: CandidateFeatureMatrix, split: CandidateHoldoutSplit,
             holdout_policy: HoldoutPolicy, lineage: tuple[HoldoutMediaLineage,...],
             policy: InteractionPolicy) -> dict:
    if not (type(snapshot) is CandidateResearchSnapshot and type(strata) is CandidateStrata
            and type(outcome) is CandidateOutcomePlan and type(controls) is CandidateMatchedControls
            and type(matrix) is CandidateFeatureMatrix and type(split) is CandidateHoldoutSplit
            and type(holdout_policy) is HoldoutPolicy and type(policy) is InteractionPolicy):
        _fail('invalid_interaction_input','Exact typed sources and preregistered policy required')
    c=replay_candidate_controls(controls.to_json(),controls.artifact_sha256,
                                snapshot,strata,outcome)
    m=replay_candidate_feature_matrix(matrix.to_json(),matrix.artifact_sha256,
                                      snapshot,strata,outcome,controls)
    s=replay_candidate_holdout_split(split.to_json(),split.artifact_sha256,
                                     snapshot,holdout_policy,lineage)
    if c != controls or m != matrix or s != split:
        _fail('interaction_source_replay_mismatch','Frozen source wrapper altered')
    source=snapshot.as_dict()
    raw_controls=controls.as_dict()
    features=matrix.as_dict()
    if (snapshot.project_id != controls.project_id
        or snapshot.project_id != matrix.project_id or snapshot.project_id != split.project_id
        or policy.snapshot_sha256 != snapshot.snapshot_sha256
        or policy.design_sha256 != source['design']['design_sha256']
        or policy.design_sha256 != holdout_policy.design_sha256
        or policy.controls_sha256 != controls.artifact_sha256
        or policy.matrix_sha256 != matrix.artifact_sha256
        or policy.holdout_sha256 != split.artifact_sha256
        or features.get('source_controls_sha256') != controls.artifact_sha256
        or raw_controls.get('source_snapshot_sha256') != snapshot.snapshot_sha256
        or features.get('project_id',snapshot.project_id) != snapshot.project_id):
        _fail('interaction_lineage_mismatch','All project/design/version/source identities must match')
    if _timestamp(policy.registered_at) > _timestamp(holdout_policy.discovery_before):
        _fail('interaction_post_selection','Registration claim must precede discovery cutoff')
    require_candidate_holdout_scope(split,arm='discovery',video_ids=split.discovery_video_ids)
    pairs=raw_controls['pairs']
    discovery=set(split.discovery_video_ids)
    confirmation=set(split.confirmation_video_ids)
    selected=[]
    reserved=[]
    for pair in pairs:
        hi,lo=pair['case_video_id'],pair['control_video_id']
        if (hi in discovery) != (lo in discovery):
            _fail('interaction_cross_arm_pair','Matched pair straddles discovery boundary')
        if hi in discovery:
            selected.append((hi,lo))
        else:
            reserved.append({'case_video_id':hi,'control_video_id':lo,
                             'reason_code':('confirmation_reserved' if hi in confirmation and lo in confirmation
                                            else 'embargo_or_quarantine')})
    if (not selected or len(selected) < policy.minimum_discovery_pairs
        or len(selected) * len(policy.hypotheses) > _MAX_PAIR_OPERATIONS
        or len(selected)>1000 or len(pairs)!=len(selected)+len(reserved)):
        _fail('interaction_insufficient_pairs','Bounded minimum discovery pair support required')
    paired_discovery_ids={i for pair in selected for i in pair}
    if len(paired_discovery_ids)!=2*len(selected) or not paired_discovery_ids <= discovery:
        _fail('interaction_population_mismatch','Discovery pairs must be unique and within frozen scope')
    unpaired_discovery_ids=sorted(discovery-paired_discovery_ids)
    members={item['video_id']:item for item in source['selection']['included']}
    rows={item['video_id']:item for item in features['rows']}
    if len(members)!=len(source['selection']['included']) or len(rows)!=len(features['rows']):
        _fail('interaction_population_mismatch','Duplicate frozen source feature row')
    columns=features['columns']
    lookup={(col['family'],col['name']):(ix,col['representation'])
             for ix,col in enumerate(columns)}
    if len(lookup)!=len(columns):
        _fail('interaction_feature_dictionary_mismatch','Duplicate feature columns')
    for hi,lo in selected:
        for i,role in ((hi,'high'),(lo,'low')):
            a=members.get(i);r=rows.get(i)
            if (a is None or r is None or r['row_sha256']!=a['row_sha256']
                or r['role']!=role or len(r['features'])!=len(columns)):
                _fail('interaction_row_mismatch','Matched role or row-level provenance changed')

    results=[]
    for h in policy.hypotheses:
        indices=[]
        for p in (h.left,h.right):
            entry=lookup.get((p.family,p.feature_name))
            if entry is None or entry[1]!=_TYPES[p.operation]:
                _fail('interaction_feature_unavailable','Predeclared predicate does not match frozen matrix dictionary')
            indices.append(entry[0])
        role_hits={'high': [0,0,0], 'low':[0,0,0]}
        missing=[]
        for hi,lo in selected:
            values=[]
            for i,role in ((hi,'high'),(lo,'low')):
                row=rows[i]
                observed=[row['features'][ix]['observation'] for ix in indices]
                if any(obs['state']!='present' for obs in observed):
                    missing.append({'video_id':i,'role':role,'reason_code':'absent_or_null_predicate'})
                    continue
                hits=[_hit(obs['value'],p) for obs,p in zip(observed,(h.left,h.right))]
                role_hits[role][0]+=int(hits[0])
                role_hits[role][1]+=int(hits[1])
                role_hits[role][2]+=int(all(hits))
        n=len(selected)
        for role in ('high','low'):
            if any(x>n for x in role_hits[role]):
                _fail('interaction_count_mismatch','Each post may count only once per predicate')
        enough=(not missing and
                all(role_hits[role][2] >= policy.minimum_joint_hits_per_role
                    for role in ('high','low')))
        delta=ratio=excess_h=excess_l=excess_delta=None
        if enough:
            a=role_hits['high'];b=role_hits['low']
            ph=Fraction(a[2],n);pl=Fraction(b[2],n)
            delta=ph-pl
            ratio=None if b[2]==0 else ph/pl
            excess_h=ph-Fraction(a[0]*a[1],n*n)
            excess_l=pl-Fraction(b[0]*b[1],n*n)
            excess_delta=excess_h-excess_l
        results.append({
            'hypothesis_id':h.hypothesis_id,'specification':asdict(h),
            'discovery_pair_count':n,'role_counts':{
                role:{'n':n,'left_hits':vals[0],'right_hits':vals[1],'joint_hits':vals[2]}
                for role,vals in role_hits.items()},
            'missing_cells':missing,
            'status':'descriptive_candidate_only' if enough else 'blocked_incomplete_or_sparse_joint_support',
            'joint_prevalence_difference_high_minus_low':_fraction(delta),
            'joint_prevalence_ratio_high_over_matched_low':_fraction(ratio),
            'excess_cooccurrence_high':_fraction(excess_h),
            'excess_cooccurrence_low':_fraction(excess_l),
            'excess_cooccurrence_difference':_fraction(excess_delta),
            'p_value':None,'adjusted_q_value':None,
            'multiplicity_adjusted_confidence_interval':None,
            'hypothesis_confirmed':False
        })
    all_supported=all(r['status']=='descriptive_candidate_only' for r in results)
    return {
        'schema_version':SCHEMA,'evidence_state':'unverified','approved_live':False,
        'project_id':snapshot.project_id,'source_snapshot_sha256':snapshot.snapshot_sha256,
        'source_controls_sha256':controls.artifact_sha256,
        'source_matrix_sha256':matrix.artifact_sha256,
        'source_holdout_sha256':split.artifact_sha256,
        'registered_hypothesis_family_sha256':policy.family_sha256,
        'registered_hypothesis_count':len(policy.hypotheses),
        'policy':asdict(policy),'policy_sha256':policy.policy_sha256,
        'discovery_pair_count':len(selected),
        'discovery_video_count':len(discovery),
        'unmatched_discovery_video_ids':unpaired_discovery_ids,
        'reserved_pairs':reserved,
        'hypotheses':results,
        'report_status':('candidate_descriptive_interactions_only' if all_supported
                         else 'blocked_incomplete_registered_family'),
        'multiplicity_status':'blocked_no_valid_inferential_p_values',
        'tested_inferential_hypothesis_count':0,
        'significance_threshold':None,'simultaneous_confidence_interval':None,
        'confirmed_discoveries':None,'can_publish_significance':False,
        'heldout_confirmatory_analysis':False,'causal_claim':False,
        'warnings':['family_bounded_and_frozen','not_independent_p_value_generator',
                    'no_valid_multiple_testing_adjustment_or_confidence_interval',
                    'no_confirmatory_holdout','no_current_uae_rights_or_review',
                    'cooccurrence_and_score_association_are_not_causal_uplift',
                    'same_creator_or_matched_pair_dependence_unresolved']
    }


def _freeze(payload:dict) -> CandidateInteractionReport:
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    if len(raw.encode('utf-8'))>_MAX_BYTES:
        _fail('payload_too_large','Interaction candidate too large')
    return CandidateInteractionReport(sha256(raw.encode('utf-8')).hexdigest(),
        payload['project_id'],payload['policy_sha256'],
        payload['registered_hypothesis_family_sha256'],
        payload['registered_hypothesis_count'],raw)


def build_candidate_interaction_report(
        snapshot:CandidateResearchSnapshot, strata:CandidateStrata,
        outcome:CandidateOutcomePlan, controls:CandidateMatchedControls,
        matrix:CandidateFeatureMatrix, split:CandidateHoldoutSplit,
        holdout_policy:HoldoutPolicy, lineage:tuple[HoldoutMediaLineage,...],
        policy:InteractionPolicy) -> CandidateInteractionReport:
    return _freeze(_compose(snapshot,strata,outcome,controls,matrix,split,
                            holdout_policy,lineage,policy))


def replay_candidate_interaction_report(raw:str|bytes, expected_sha256:str,
        snapshot:CandidateResearchSnapshot, strata:CandidateStrata,
        outcome:CandidateOutcomePlan, controls:CandidateMatchedControls,
        matrix:CandidateFeatureMatrix, split:CandidateHoldoutSplit,
        holdout_policy:HoldoutPolicy, lineage:tuple[HoldoutMediaLineage,...]) -> CandidateInteractionReport:
    if type(raw) not in (str,bytes) or type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_interaction_artifact','Canonical JSON and digest required')
    try:
        encoded=raw if type(raw) is bytes else raw.encode('utf-8')
    except UnicodeError as exc:
        raise PIEBoundaryError('invalid_interaction_artifact','Invalid UTF-8') from exc
    if len(encoded)>_MAX_BYTES:
        _fail('payload_too_large','Interaction report exceeds byte bound')
    def unique(pairs):
        result={}
        for k,v in pairs:
            if k in result: _fail('duplicate_json_key','Repeated interaction JSON property')
            result[k]=v
        return result
    try:
        payload=json.loads(encoded,object_pairs_hook=unique,
                  parse_constant=lambda _: _fail('invalid_interaction_artifact','Nonfinite JSON'))
    except PIEBoundaryError: raise
    except (ValueError,UnicodeError,TypeError,RecursionError) as exc:
        raise PIEBoundaryError('invalid_interaction_artifact','Malformed interaction JSON') from exc
    if type(payload) is not dict or canonical_hash(payload)!=expected_sha256:
        _fail('interaction_hash_mismatch','SHA differs from reported artifact')
    if payload.get('schema_version')!=SCHEMA or payload.get('approved_live') is not False:
        _fail('unsupported_interaction_artifact','Cannot promote exploratory report')
    spec=payload.get('policy')
    if type(spec) is not dict or set(spec)!=set(InteractionPolicy.__dataclass_fields__):
        _fail('invalid_interaction_policy','Full immutable interaction policy required')
    try:
        source_hypotheses=spec['hypotheses']
        if type(source_hypotheses) is not list: _fail('invalid_interaction_policy','Invalid family list')
        hypotheses=tuple(InteractionHypothesis(
            h['hypothesis_id'],
            InteractionPredicate(**{**h['left'],
                'target':tuple(h['left']['target']) if h['left']['operation']=='sequence_equals' else h['left']['target']}),
            InteractionPredicate(**{**h['right'],
                'target':tuple(h['right']['target']) if h['right']['operation']=='sequence_equals' else h['right']['target']}))
            for h in source_hypotheses)
        kwargs={**spec,'hypotheses':hypotheses}
        policy=InteractionPolicy(**kwargs)
    except PIEBoundaryError: raise
    except (TypeError,ValueError,KeyError) as exc:
        raise PIEBoundaryError('invalid_interaction_policy','Invalid interaction policy specification') from exc
    rebuilt=build_candidate_interaction_report(snapshot,strata,outcome,controls,matrix,
                                                split,holdout_policy,lineage,policy)
    if (rebuilt.to_json().encode('utf-8')!=encoded or rebuilt.artifact_sha256!=expected_sha256
        or rebuilt.as_dict()!=payload):
        _fail('interaction_replay_mismatch','Content rehashed or upstream family changed')
    return rebuilt
