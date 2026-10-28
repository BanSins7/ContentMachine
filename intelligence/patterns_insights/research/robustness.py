"""PIE-MOD-23: exact source-bound descriptive sensitivity and negative controls.

Consumes a rebuilt candidate stage22 report and its original discovery pairs.
Does not create new high/low outcomes, confirm on a held-out cohort, generate
valid p-values, or authorize publication. Outlier/creator/threshold changes are
registered before discovery and every check has a reproducible audit entry.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, localcontext, ROUND_HALF_EVEN
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
from .holdout import (CandidateHoldoutSplit,HoldoutPolicy,HoldoutMediaLineage,
                      require_candidate_holdout_scope)
from .technical_numeric import (TechnicalNumericPolicy,CandidateTechnicalNumericReport,
                                 replay_candidate_technical_numeric_report)

SCHEMA='pie-robustness-sensitivity-candidate/v1'
_SHA=re.compile(r'[0-9a-f]{64}\Z')
_VER=re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_RATE=re.compile(r'(?:0(?:\.\d{1,6})?|1(?:\.0{1,6})?)\Z')
_MAX_PAIRS=1000
_MAX_BYTES=16*1024*1024
_MAX_FACTORS=16


def _fail(code:str,detail:str)->None:
    raise PIEBoundaryError(code,detail)


def _time(v:object)->datetime:
    if type(v) is not str or not 10<=len(v)<=40:
        _fail('invalid_robustness_policy','Timezone-aware registration required')
    try:
        t=datetime.fromisoformat(v.replace('Z','+00:00'))
    except ValueError as exc:
        raise PIEBoundaryError('invalid_robustness_policy','Bad registration time') from exc
    if t.utcoffset() is None:
        _fail('invalid_robustness_policy','Timezone offset required')
    return t.astimezone(timezone.utc)


def _frac(v:Fraction|None)->dict|None:
    if v is None:return None
    with localcontext() as c:
        c.prec=80
        c.rounding=ROUND_HALF_EVEN
        display=(Decimal(v.numerator)/Decimal(v.denominator)).quantize(Decimal('0.000000000001'))
    return {'numerator':v.numerator,'denominator':v.denominator,
            'decimal_12dp':format(display,'f')}


def _sign(v:Fraction)->str:
    return 'positive' if v>0 else 'negative' if v<0 else 'zero'


def _mean(vals:tuple[int,...])->Fraction:
    if not vals:_fail('robustness_empty_population','Nonempty complete pair sample required')
    return Fraction(sum(vals),len(vals))


@dataclass(frozen=True,slots=True)
class SensitivityFactor:
    factor_id:str
    alternative_exposure_cutoffs_raw:tuple[int,...]

    def __post_init__(self)->None:
        if type(self.factor_id) is not str or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}',self.factor_id):
            _fail('invalid_robustness_policy','Stable registered factor ID required')
        if (type(self.alternative_exposure_cutoffs_raw) is not tuple
            or not 1<=len(self.alternative_exposure_cutoffs_raw)<=8
            or any(type(x) is not int or not 0<x<2**63-1
                   for x in self.alternative_exposure_cutoffs_raw)
            or self.alternative_exposure_cutoffs_raw!=
               tuple(sorted(set(self.alternative_exposure_cutoffs_raw)))):
            _fail('invalid_robustness_policy','1..8 unique ascending positive raw cutoffs required')


@dataclass(frozen=True,slots=True)
class RobustnessPolicy:
    policy_version:str
    algorithm_version:str
    design_sha256:str
    snapshot_sha256:str
    controls_sha256:str
    matrix_sha256:str
    holdout_sha256:str
    technical_report_sha256:str
    registered_at:str
    registration_claim_id:str
    seed:int
    negative_control_permutations:int
    minimum_complete_pairs:int
    minimum_creators:int
    minimum_after_leave_creator_out:int
    trim_largest_abs_pair_differences:int
    maximum_descriptive_null_exceedance_fraction:str
    factors:tuple[SensitivityFactor,...]
    scope:str='discovery_only'
    permutation_unit:str='whole_matched_pair_sign_flip'
    deletion_unit:str='all_pairs_touched_by_creator'
    alternate_outcome_windows:str='blocked_requires_new_qualified_uae_replay'
    heldout_evaluation:str='not_touched'
    significance:str='no_valid_p_values_or_confirmed_results'

    def __post_init__(self)->None:
        for name in ('policy_version','algorithm_version','registration_claim_id'):
            x=getattr(self,name)
            if type(x) is not str or not _VER.fullmatch(x):
                _fail('invalid_robustness_policy','Versioned policy and registration claim required')
        for name in ('design_sha256','snapshot_sha256','controls_sha256',
                     'matrix_sha256','holdout_sha256','technical_report_sha256'):
            x=getattr(self,name)
            if type(x) is not str or not _SHA.fullmatch(x):
                _fail('invalid_robustness_policy','Every input SHA is pinned')
        _time(self.registered_at)
        if type(self.seed) is not int or not 0<=self.seed<2**64:
            _fail('invalid_robustness_policy','Require explicit uint64 seed')
        if (type(self.negative_control_permutations) is not int or
            not 32<=self.negative_control_permutations<=512 or
            self.negative_control_permutations%32):
            _fail('invalid_robustness_policy','32..512 negative controls in multiples of 32')
        for name in ('minimum_complete_pairs','minimum_creators',
                     'minimum_after_leave_creator_out'):
            v=getattr(self,name)
            if type(v) is not int or not 2<=v<=_MAX_PAIRS:
                _fail('invalid_robustness_policy','All support minima must be >=2 and bounded')
        if (type(self.trim_largest_abs_pair_differences) is not int
            or not 0<=self.trim_largest_abs_pair_differences<=_MAX_PAIRS-2):
            _fail('invalid_robustness_policy','Bounded prespecified outlier removal required')
        if (type(self.maximum_descriptive_null_exceedance_fraction) is not str or
            not _RATE.fullmatch(self.maximum_descriptive_null_exceedance_fraction)):
            _fail('invalid_robustness_policy','Exact decimal null-control tolerance required')
        if (type(self.factors) is not tuple or not 1<=len(self.factors)<=_MAX_FACTORS
            or any(type(x) is not SensitivityFactor for x in self.factors)
            or tuple(x.factor_id for x in self.factors)!=
               tuple(sorted(set(x.factor_id for x in self.factors)))):
            _fail('invalid_robustness_policy','Complete sorted bounded factor family required')
        if (self.scope!='discovery_only'
            or self.permutation_unit!='whole_matched_pair_sign_flip'
            or self.deletion_unit!='all_pairs_touched_by_creator'
            or self.alternate_outcome_windows!='blocked_requires_new_qualified_uae_replay'
            or self.heldout_evaluation!='not_touched'
            or self.significance!='no_valid_p_values_or_confirmed_results'):
            _fail('invalid_robustness_policy','Unverified holdout/outcome/inference mode is forbidden')

    @property
    def policy_sha256(self)->str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True,slots=True)
class CandidateRobustnessReport:
    artifact_sha256:str
    project_id:str
    policy_sha256:str
    status:str
    _json:str=field(repr=False)

    @property
    def ready_for_analysis(self)->bool:return False

    @property
    def verified_live(self)->bool:return False

    def to_json(self)->str:return self._json

    def as_dict(self)->dict:return json.loads(self._json)


def _permutation_counts(differences:tuple[int,...],seed:int,draws:int,identity:str)->dict:
    """Versioned SHA256 counter bitstream; no reseeding or floating point RNG."""
    if not differences:_fail('robustness_empty_population','Cannot permute empty sample')
    observed=abs(sum(differences))
    count=0
    fingerprint=sha256(identity.encode('utf-8')).digest()
    for draw in range(draws):
        signed_sum=0
        for pair_index,difference in enumerate(differences):
            block=sha256(b'PIE-MOD-23-sign-flip-v1'+seed.to_bytes(8,'big')+
                         fingerprint+draw.to_bytes(4,'big')+
                         pair_index.to_bytes(4,'big')).digest()
            signed_sum+=difference if block[0]&1 else -difference
        if abs(signed_sum)>=observed:
            count+=1
    return {
        'draw_count':draws,
        'abs_sum_at_least_observed_count':count,
        'descriptive_exceedance_fraction':_frac(Fraction(count,draws)),
        'rng_algorithm':'sha256-counter-independent-whole-pair-signflip-v1',
        'inferential_p_value':None,'calibrated_negative_control':False,
    }


def _audit_pairs(values:tuple[tuple[int,int,str,str,str],...],
                 policy:RobustnessPolicy, factor:SensitivityFactor,
                 reference_cutoff:int)->dict:
    """Five-tuples: high raw, low raw, high creator, low creator, stable pair key."""
    if len(values)<policy.minimum_complete_pairs:
        _fail('robustness_insufficient_pairs','Need frozen positive paired sample')
    n=len(values)
    creators={k for a,b,high_creator,low_creator,key in values
              for k in (high_creator,low_creator)}
    if len(creators)<policy.minimum_creators:
        _fail('robustness_insufficient_creators','Not enough distinct source creators')
    differences=tuple(a-b for a,b,_,_,_ in values)
    baseline=_mean(differences)
    baseline_sign=_sign(baseline)
    deleted=[]
    for creator in sorted(creators):
        after=tuple(a-b for a,b,h,l,key in values if creator not in (h,l))
        estimate=_mean(after) if len(after)>=policy.minimum_after_leave_creator_out else None
        deleted.append({'creator_key':creator,'remaining_pairs':len(after),
                        'difference':_frac(estimate),'direction':_sign(estimate) if estimate is not None else
                        'unsupported_after_creator_deletion'})
    trimmed=sorted(values,key=lambda pair:(-abs(pair[0]-pair[1]),pair[4]))
    removed=[x[4] for x in trimmed[:policy.trim_largest_abs_pair_differences]]
    after=tuple(a-b for a,b,h,l,key in trimmed[policy.trim_largest_abs_pair_differences:])
    trimmed_mean=_mean(after) if len(after)>=policy.minimum_after_leave_creator_out else None
    tested_cutoffs=tuple(sorted(set((reference_cutoff,)+factor.alternative_exposure_cutoffs_raw)))
    alternatives=[]
    for cutoff in tested_cutoffs:
        high_hits=sum(a>=cutoff for a,b,h,l,key in values)
        low_hits=sum(b>=cutoff for a,b,h,l,key in values)
        effect=Fraction(high_hits-low_hits,n)
        alternatives.append({'cutoff_raw_inclusive':cutoff,'matched_pair_count':n,
                             'high_hit_posts':high_hits,'low_hit_posts':low_hits,
                             'difference':_frac(effect),'direction':_sign(effect),
                             'is_original_stage22_cutoff':cutoff==reference_cutoff})
    negatives=_permutation_counts(differences,policy.seed,
                  policy.negative_control_permutations,factor.factor_id)
    unsupported=any(x['difference'] is None for x in deleted) or trimmed_mean is None
    signs=[x['direction'] for x in deleted if x['difference'] is not None]
    signs.append(_sign(trimmed_mean) if trimmed_mean is not None else 'unsupported')
    primary=next(x['direction'] for x in alternatives if x['is_original_stage22_cutoff'])
    alt_signs=[x['direction'] for x in alternatives]
    if unsupported or baseline_sign=='zero' or primary=='zero':
        status='blocked_insufficient_support_or_zero_baseline'
    elif any(s!=baseline_sign for s in signs):
        status='fragile_leave_creator_out_or_outlier_flip'
    elif any(s!=primary for s in alt_signs):
        status='fragile_alternate_exposure_definition'
    elif (Fraction(negatives['abs_sum_at_least_observed_count'],
                   policy.negative_control_permutations)>
          Fraction(Decimal(policy.maximum_descriptive_null_exceedance_fraction))):
        status='fragile_negative_control_high_exceedance'
    else:
        status='directionally_robust_descriptive_only'
    return {
        'factor_id':factor.factor_id,'sample_pair_count':n,
        'creator_count':len(creators),'baseline_raw_difference':_frac(baseline),
        'baseline_direction':baseline_sign,
        'leave_one_creator_out':deleted,
        'outlier_trim':{'trim_count':len(removed),
                        'removed_pair_keys':removed,
                        'remaining_pair_count':len(after),
                        'difference':_frac(trimmed_mean),
                        'direction':_sign(trimmed_mean) if trimmed_mean is not None else 'unsupported'},
        'exposure_sensitivity':alternatives,
        'negative_control':negatives,'status':status,
        'may_promote_as_universal_pattern':False,
        'causal_claim':False,
    }


def _compose(snapshot:CandidateResearchSnapshot,strata:CandidateStrata,
             outcome:CandidateOutcomePlan,controls:CandidateMatchedControls,
             matrix:CandidateFeatureMatrix,split:CandidateHoldoutSplit,
             holdout_policy:HoldoutPolicy,lineage:tuple[HoldoutMediaLineage,...],
             numeric_policy:TechnicalNumericPolicy,
             numeric_report:CandidateTechnicalNumericReport,
             policy:RobustnessPolicy)->dict:
    if not (type(snapshot) is CandidateResearchSnapshot and type(strata) is CandidateStrata
            and type(outcome) is CandidateOutcomePlan and type(controls) is CandidateMatchedControls
            and type(matrix) is CandidateFeatureMatrix and type(split) is CandidateHoldoutSplit
            and type(numeric_policy) is TechnicalNumericPolicy
            and type(numeric_report) is CandidateTechnicalNumericReport
            and type(policy) is RobustnessPolicy):
        _fail('invalid_robustness_input','All exact frozen typed candidate artifacts required')
    rebuilt=replay_candidate_technical_numeric_report(
        numeric_report.to_json(),numeric_report.artifact_sha256,
        snapshot,strata,outcome,controls,matrix,split,holdout_policy,lineage)
    if rebuilt!=numeric_report:
        _fail('robustness_source_replay_mismatch','Numeric report wrapper tampered')
    raw=numeric_report.as_dict()
    source=snapshot.as_dict()
    mat=matrix.as_dict()
    ctl=controls.as_dict()
    if (raw.get('schema_version')!='pie-numeric-technical-factors-candidate/v1'
        or raw.get('approved_live') is not False
        or raw.get('confirmed_discoveries') is not None
        or numeric_report.project_id!=snapshot.project_id
        or policy.design_sha256!=source['design']['design_sha256']
        or policy.design_sha256!=numeric_policy.design_sha256
        or numeric_report.policy_sha256!=numeric_policy.policy_sha256
        or policy.snapshot_sha256!=snapshot.snapshot_sha256
        or policy.controls_sha256!=controls.artifact_sha256
        or policy.matrix_sha256!=matrix.artifact_sha256
        or policy.holdout_sha256!=split.artifact_sha256
        or policy.technical_report_sha256!=numeric_report.artifact_sha256
        or raw.get('source_holdout_sha256')!=split.artifact_sha256):
        _fail('robustness_lineage_mismatch','Stage22 source or registered design differs')
    if _time(policy.registered_at)>_time(holdout_policy.discovery_before):
        _fail('robustness_post_selection','Sensitivity tests cannot be registered after discovery')
    if (tuple(x.factor_id for x in policy.factors)!=
        tuple(x.factor_id for x in numeric_policy.factors)):
        _fail('robustness_family_mismatch','Sensitivity must cover every original registered factor')
    require_candidate_holdout_scope(split,arm='discovery',video_ids=split.discovery_video_ids)
    lookup={(c['family'],c['name']):i for i,c in enumerate(mat['columns'])}
    if len(lookup)!=len(mat['columns']):
        _fail('robustness_dictionary_mismatch','Duplicate matrix columns')
    included={x['video_id']:x for x in source['selection']['included']}
    matrix_rows={x['video_id']:x for x in mat['rows']}
    if len(included)!=len(source['selection']['included']) or len(matrix_rows)!=len(mat['rows']):
        _fail('robustness_population_mismatch','Duplicate frozen row')
    discovery=set(split.discovery_video_ids)
    complete_pairs=[]
    reserved=[]
    for item in ctl['pairs']:
        h,l=item['case_video_id'],item['control_video_id']
        if (h in discovery)!=(l in discovery):
            _fail('robustness_cross_arm_pair','Pair must not cross discovery/confirmation')
        if h in discovery:
            complete_pairs.append((h,l))
        else:reserved.append({'case_video_id':h,'control_video_id':l,
                              'reason_code':'outside_discovery_not_examined'})
    ids={i for pair in complete_pairs for i in pair}
    if (len(ids)!=2*len(complete_pairs) or not ids<=discovery
        or not policy.minimum_complete_pairs<=len(complete_pairs)<=_MAX_PAIRS
        or raw.get('discovery_pair_count')!=len(complete_pairs)
        or len(raw.get('factors',[]))!=len(policy.factors)):
        _fail('robustness_population_mismatch','Exact original matched discovery sample required')
    for h,l in complete_pairs:
        for vid,role in ((h,'high'),(l,'low')):
            a=included.get(vid);r=matrix_rows.get(vid)
            if (a is None or r is None or r['row_sha256']!=a['row_sha256']
                or r['role']!=role or len(r['features'])!=len(mat['columns'])):
                _fail('robustness_row_mismatch','Cannot alter source identity or role')
    rows=[]
    for factor,frozen_factor,original in zip(policy.factors,numeric_policy.factors,raw['factors']):
        if factor.factor_id!=frozen_factor.factor_id or original['factor_id']!=factor.factor_id:
            _fail('robustness_family_mismatch','Numeric factor position/ID altered')
        index=lookup.get((frozen_factor.family,frozen_factor.feature_name))
        if index is None:
            _fail('robustness_feature_missing','Frozen technical feature unavailable')
        paired=[]
        missing=[]
        for hi,lo in complete_pairs:
            pair=[]
            for vid in (hi,lo):
                cell=matrix_rows[vid]['features'][index]['observation']
                if cell['state']!='present':
                    missing.append(vid)
                    continue
                value=cell['value']
                if type(value) is not int or not 0<=value<2**63:
                    _fail('robustness_invalid_value','Exact nonnegative stage22 integer required')
                pair.append(value)
            if len(pair)==2:
                ha=included[hi]['row']['creator_key']
                la=included[lo]['row']['creator_key']
                if not all(type(x) is str and x for x in (ha,la)):
                    _fail('robustness_invalid_creator','Exact creator keys required')
                paired.append((pair[0],pair[1],ha,la,f'{hi}:{lo}'))
        if missing or original['status']!='candidate_descriptive_only':
            rows.append({'factor_id':factor.factor_id,'status':'blocked_upstream_numeric_or_missing',
                         'missing_video_ids':sorted(missing),'sensitivity':None,
                         'may_promote_as_universal_pattern':False})
            continue
        if len(paired)!=len(complete_pairs):
            _fail('robustness_population_mismatch','Loss of complete matched pair')
        sensitivity=_audit_pairs(tuple(paired),policy,factor,
                                 frozen_factor.bin_cutoffs_raw[0])
        rows.append({'factor_id':factor.factor_id,'status':sensitivity['status'],
                     'missing_video_ids':[],'sensitivity':sensitivity,
                     'may_promote_as_universal_pattern':False})
    all_robust=all(x['status']=='directionally_robust_descriptive_only' for x in rows)
    return {
        'schema_version':SCHEMA,'evidence_state':'unverified','approved_live':False,
        'project_id':snapshot.project_id,
        'source_snapshot_sha256':snapshot.snapshot_sha256,
        'source_controls_sha256':controls.artifact_sha256,
        'source_matrix_sha256':matrix.artifact_sha256,
        'source_holdout_sha256':split.artifact_sha256,
        'source_technical_numeric_sha256':numeric_report.artifact_sha256,
        'policy':asdict(policy),'policy_sha256':policy.policy_sha256,
        'registered_factor_count':len(policy.factors),
        'discovery_pair_count':len(complete_pairs),
        'reserved_pairs':reserved,'unmatched_discovery_video_ids':sorted(discovery-ids),
        'factor_sensitivities':rows,
        'report_status':('candidate_directional_robustness_only' if all_robust else
                         'downgraded_fragile_or_blocked_factors'),
        'downgraded_or_blocked_factor_ids':[x['factor_id'] for x in rows
             if x['status']!='directionally_robust_descriptive_only'],
        'alternate_outcome_window_status':'blocked_requires_new_qualified_uae_replay',
        'heldout_subgroup_status':'blocked_no_confirmation_arm_inspected',
        'c7_raw_growth_dominance':'unknown_requires_source_metric_decomposition',
        'independent_out_of_sample_replication':False,
        'valid_inferential_p_value_count':0,'confirmed_discoveries':None,
        'can_publish_significance':False,'causal_claim':False,
        'warnings':['source_and_significance_unverified',
                    'pseudo_negative_control_is_descriptive_not_a_p_value',
                    'leave_one_creator_out_can_delete_both_pair_members',
                    'outlier_trim_does_not_correct_selection_or_clustering',
                    'alternative_outcome_windows_need_new_upstream_outcome_replay',
                    'holdout_confirmation_not_examined',
                    'c7_growth_component_dominance_unavailable_from_current_export',
                    'directionally_robust_not_authorized_as_universal_pattern']
    }


def _freeze(data:dict)->CandidateRobustnessReport:
    raw=json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    if len(raw.encode('utf-8'))>_MAX_BYTES:
        _fail('payload_too_large','Robustness artifact exceeds bound')
    return CandidateRobustnessReport(sha256(raw.encode('utf-8')).hexdigest(),
                                    data['project_id'],data['policy_sha256'],
                                    data['report_status'],raw)


def build_candidate_robustness_report(snapshot:CandidateResearchSnapshot,
        strata:CandidateStrata,outcome:CandidateOutcomePlan,
        controls:CandidateMatchedControls,matrix:CandidateFeatureMatrix,
        split:CandidateHoldoutSplit,holdout_policy:HoldoutPolicy,
        lineage:tuple[HoldoutMediaLineage,...],
        numeric_policy:TechnicalNumericPolicy,
        numeric_report:CandidateTechnicalNumericReport,
        policy:RobustnessPolicy)->CandidateRobustnessReport:
    return _freeze(_compose(snapshot,strata,outcome,controls,matrix,split,
                            holdout_policy,lineage,numeric_policy,numeric_report,policy))


def replay_candidate_robustness_report(raw:str|bytes,expected_sha256:str,
        snapshot:CandidateResearchSnapshot,strata:CandidateStrata,
        outcome:CandidateOutcomePlan,controls:CandidateMatchedControls,
        matrix:CandidateFeatureMatrix,split:CandidateHoldoutSplit,
        holdout_policy:HoldoutPolicy,lineage:tuple[HoldoutMediaLineage,...],
        numeric_policy:TechnicalNumericPolicy,
        numeric_report:CandidateTechnicalNumericReport)->CandidateRobustnessReport:
    if type(raw) not in (str,bytes) or type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_robustness_artifact','Expected canonical JSON and SHA')
    try:
        encoded=raw if type(raw) is bytes else raw.encode('utf-8')
    except UnicodeError as exc:
        raise PIEBoundaryError('invalid_robustness_artifact','Invalid UTF-8') from exc
    if len(encoded)>_MAX_BYTES:
        _fail('payload_too_large','Oversized robustness artifact')
    def unique(items):
        obj={}
        for key,val in items:
            if key in obj:_fail('duplicate_json_key','Duplicate robustness property')
            obj[key]=val
        return obj
    try:
        data=json.loads(encoded,object_pairs_hook=unique,
                        parse_constant=lambda _:_fail('invalid_robustness_artifact','Nonfinite JSON'))
    except PIEBoundaryError:raise
    except (ValueError,UnicodeError,TypeError,RecursionError) as exc:
        raise PIEBoundaryError('invalid_robustness_artifact','Malformed JSON') from exc
    if type(data) is not dict or canonical_hash(data)!=expected_sha256:
        _fail('robustness_hash_mismatch','Untrusted payload digest mismatch')
    if data.get('schema_version')!=SCHEMA or data.get('approved_live') is not False:
        _fail('unsupported_robustness_artifact','Cannot promote candidate to live')
    spec=data.get('policy')
    if type(spec) is not dict or set(spec)!=set(RobustnessPolicy.__dataclass_fields__):
        _fail('invalid_robustness_policy','Complete frozen policy required')
    try:
        if type(spec['factors']) is not list:
            _fail('invalid_robustness_policy','Complete factor family list required')
        factors=tuple(SensitivityFactor(**{**f,
            'alternative_exposure_cutoffs_raw':tuple(f['alternative_exposure_cutoffs_raw'])
            if type(f.get('alternative_exposure_cutoffs_raw')) is list
            else f.get('alternative_exposure_cutoffs_raw')})
            for f in spec['factors'])
        policy=RobustnessPolicy(**{**spec,'factors':factors})
    except PIEBoundaryError:raise
    except (KeyError,TypeError,ValueError) as exc:
        raise PIEBoundaryError('invalid_robustness_policy','Malformed robustness family') from exc
    rebuilt=build_candidate_robustness_report(
        snapshot,strata,outcome,controls,matrix,split,holdout_policy,lineage,
        numeric_policy,numeric_report,policy)
    if (rebuilt.artifact_sha256!=expected_sha256 or rebuilt.to_json().encode('utf-8')!=encoded
        or rebuilt.as_dict()!=data):
        _fail('robustness_replay_mismatch','Rehashed forged report or modified source')
    return rebuilt
