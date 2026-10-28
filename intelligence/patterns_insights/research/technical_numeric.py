"""PIE-MOD-22: frozen numeric-int technical factors; descriptive exploratory ONLY.

Stage11 accepts integer numeric features with explicit units, not floating point.
No guessed conversions or pretending B5 source authority is verified. Group C7
defines the high/low roles and must never become a technical predictor.
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

SCHEMA = 'pie-numeric-technical-factors-candidate/v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_ID = re.compile(r'[a-z][a-z0-9_]{0,79}\Z')
_VER = re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_KINDS = {'duration': 'a', 'speech_wpm': 'a', 'speech_coverage': 'a',
          'cuts_per_minute': 'b', 'first_5_seconds_cuts': 'b'}
_MAX_FACTORS = 16
_MAX_BINS = 16
_MAX_PAIRS = 1000
_MAX_BYTES = 16 * 1024 * 1024
_MAX_RAW = 2**63 - 1


def _fail(code: str, msg: str) -> None:
    raise PIEBoundaryError(code, msg)


def _time(v: object) -> datetime:
    if type(v) is not str or not 10 <= len(v) <= 40:
        _fail('invalid_numeric_registration', 'Explicit timezone-aware registration required')
    try:
        d = datetime.fromisoformat(v.replace('Z', '+00:00'))
    except ValueError as exc:
        raise PIEBoundaryError('invalid_numeric_registration','Invalid ISO timestamp') from exc
    if d.utcoffset() is None:
        _fail('invalid_numeric_registration','Timezone offset required')
    return d.astimezone(timezone.utc)


def _ratio(v: Fraction | None) -> dict | None:
    if v is None:
        return None
    with localcontext() as c:
        c.prec=80
        c.rounding=ROUND_HALF_EVEN
        display=(Decimal(v.numerator)/Decimal(v.denominator)).quantize(
            Decimal('0.000000000001'))
    return {'numerator':v.numerator,'denominator':v.denominator,
            'decimal_12dp':format(display,'f')}


@dataclass(frozen=True, slots=True)
class TechnicalNumericFactor:
    factor_id: str
    metric_kind: str
    family: str
    feature_name: str
    source_unit: str
    value_scale: int
    bin_cutoffs_raw: tuple[int, ...]
    min_per_role_per_bin: int
    min_creators_per_bin: int
    source_origin_claim: str='unverified_group_a_or_b5_metric'

    def __post_init__(self) -> None:
        if type(self.factor_id) is not str or not _ID.fullmatch(self.factor_id):
            _fail('invalid_numeric_factor','Stable factor ID required')
        if (type(self.metric_kind) is not str or self.metric_kind not in _KINDS
            or type(self.family) is not str or self.family != _KINDS[self.metric_kind]):
            _fail('invalid_numeric_factor','Metric family A vs B5 must be explicit')
        if (type(self.feature_name) is not str or not _ID.fullmatch(self.feature_name)
            or type(self.source_unit) is not str or not _ID.fullmatch(self.source_unit)):
            _fail('invalid_numeric_factor','Observed named numeric feature and unit required')
        if type(self.value_scale) is not int or not 1 <= self.value_scale <= 10**9:
            _fail('invalid_numeric_factor','Positive bounded fixed-point scale required')
        if (type(self.bin_cutoffs_raw) is not tuple or not 1 <= len(self.bin_cutoffs_raw) < _MAX_BINS
            or any(type(x) is not int or not 0 < x < _MAX_RAW for x in self.bin_cutoffs_raw)
            or self.bin_cutoffs_raw != tuple(sorted(set(self.bin_cutoffs_raw)))):
            _fail('invalid_numeric_factor','Ordered unique positive raw integer boundaries required')
        for key in ('min_per_role_per_bin','min_creators_per_bin'):
            v=getattr(self,key)
            if type(v) is not int or not 1<=v<=_MAX_PAIRS:
                _fail('invalid_numeric_factor','Explicit positive bin support and creator minima required')
        if self.source_origin_claim!='unverified_group_a_or_b5_metric':
            _fail('invalid_numeric_factor','Stage22 cannot attest upstream technical metric origin')


@dataclass(frozen=True, slots=True)
class TechnicalNumericPolicy:
    policy_version: str
    algorithm_version: str
    design_sha256: str
    snapshot_sha256: str
    controls_sha256: str
    matrix_sha256: str
    holdout_sha256: str
    registered_at: str
    registration_claim_id: str
    min_discovery_pairs: int
    factors: tuple[TechnicalNumericFactor, ...]
    sample_scope: str='discovery_only'
    sample_weighting: str='unweighted_matched_pairs'
    treatment_of_missing: str='block_entire_factor_without_imputation'
    bins: str='frozen_half_open_raw_nonnegative'
    inference: str='blocked_no_calibrated_cluster_uncertainty'

    def __post_init__(self) -> None:
        for key in ('policy_version','algorithm_version','registration_claim_id'):
            v=getattr(self,key)
            if type(v) is not str or not _VER.fullmatch(v):
                _fail('invalid_numeric_policy','Explicit version and registration claim required')
        for key in ('design_sha256','snapshot_sha256','controls_sha256','matrix_sha256','holdout_sha256'):
            v=getattr(self,key)
            if type(v) is not str or not _SHA.fullmatch(v):
                _fail('invalid_numeric_policy','Full upstream SHA identity required')
        _time(self.registered_at)
        if type(self.min_discovery_pairs) is not int or not 1<=self.min_discovery_pairs<=_MAX_PAIRS:
            _fail('invalid_numeric_policy','Positive bounded matched pair minimum required')
        if (type(self.factors) is not tuple or not 1<=len(self.factors)<=_MAX_FACTORS
            or any(type(x) is not TechnicalNumericFactor for x in self.factors)):
            _fail('invalid_numeric_policy','Frozen complete 1..16 factor family required')
        ids=tuple(x.factor_id for x in self.factors)
        if ids!=tuple(sorted(set(ids))):
            _fail('invalid_numeric_policy','Factor IDs unique and sorted')
        cols=[(f.family,f.feature_name) for f in self.factors]
        if len(set(cols))!=len(cols):
            _fail('duplicate_numeric_feature','Cannot test same numeric column under another factor ID')
        if (self.sample_scope!='discovery_only'
            or self.sample_weighting!='unweighted_matched_pairs'
            or self.treatment_of_missing!='block_entire_factor_without_imputation'
            or self.bins!='frozen_half_open_raw_nonnegative'
            or self.inference!='blocked_no_calibrated_cluster_uncertainty'):
            _fail('invalid_numeric_policy','Cannot weaken source, inference or missingness semantics')

    @property
    def policy_sha256(self) -> str:
        return canonical_hash(asdict(self))

    @property
    def family_sha256(self) -> str:
        return canonical_hash({'factors':[asdict(f) for f in self.factors]})


@dataclass(frozen=True, slots=True)
class CandidateTechnicalNumericReport:
    artifact_sha256: str
    project_id: str
    policy_sha256: str
    factor_family_sha256: str
    report_status: str
    _json: str=field(repr=False)

    @property
    def verified_live(self) -> bool: return False

    @property
    def ready_for_analysis(self) -> bool: return False

    def to_json(self) -> str: return self._json

    def as_dict(self) -> dict: return json.loads(self._json)


def _bin_index(x: int, cutoffs: tuple[int,...]) -> int:
    # [0,c0), [c0,c1), ..., [c_last,infinity), no learned cutpoints.
    return sum(x>=cut for cut in cutoffs)


def _factor_stats(factor:TechnicalNumericFactor, selected:list[tuple[int,int]],
                  members:dict[int,dict], rows:dict[int,dict], col_idx:int) -> dict:
    size=len(factor.bin_cutoffs_raw)+1
    bins=[{'bin_index':i,'lower_inclusive_raw':(0 if i==0 else factor.bin_cutoffs_raw[i-1]),
           'upper_exclusive_raw':(factor.bin_cutoffs_raw[i] if i<size-1 else None),
           'high_n':0,'low_n':0,'distinct_creator_count':0,
           'high_fraction_of_role':None,'low_fraction_of_role':None,
           'prevalence_difference_high_minus_low':None,
           'high_share_within_bin':None,'supported':False}
          for i in range(size)]
    creators=[set() for _ in bins]
    pairs_transition=[[0]*size for _ in range(size)]
    missing=[]
    value_sums={'high':0,'low':0}
    signs={'high_greater':0,'low_greater':0,'equal':0}
    source_value_ranges={'high':[],'low':[]}
    for hi,lo in selected:
        observations=[]
        for vid,role in ((hi,'high'),(lo,'low')):
            r=rows[vid]
            cell=r['features'][col_idx]['observation']
            if cell['state']!='present':
                missing.append({'video_id':vid,'role':role,'reason_code':'null_or_absent_numeric'})
                continue
            value=cell['value']
            if type(value) is not int or not 0<=value<=_MAX_RAW:
                _fail('numeric_invalid_value','Raw numeric metric must be nonnegative int, not bool/float')
            idx=_bin_index(value,factor.bin_cutoffs_raw)
            creator=members[vid]['row']['creator_key']
            if type(creator) is not str or not creator:
                _fail('numeric_invalid_creator','Exact creator key needed for grouped dependence')
            bins[idx][role+'_n']+=1
            creators[idx].add(creator)
            value_sums[role]+=value
            source_value_ranges[role].append(value)
            observations.append((value,idx))
        if len(observations)==2:
            (high_val,hi_bin),(low_val,lo_bin)=observations
            pairs_transition[hi_bin][lo_bin]+=1
            signs['high_greater' if high_val>low_val else
                  'low_greater' if high_val<low_val else 'equal']+=1
    n=len(selected)
    enough=not missing
    ordered_shares=[]
    for item,cids in zip(bins,creators):
        i=item['bin_index']
        high,low=item['high_n'],item['low_n']
        item['distinct_creator_count']=len(cids)
        item['high_fraction_of_role']=_ratio(Fraction(high,n))
        item['low_fraction_of_role']=_ratio(Fraction(low,n))
        item['prevalence_difference_high_minus_low']=_ratio(Fraction(high-low,n))
        item['high_share_within_bin']=_ratio(Fraction(high,high+low)) if high+low else None
        item['supported']= (high>=factor.min_per_role_per_bin and
                           low>=factor.min_per_role_per_bin and
                           len(cids)>=factor.min_creators_per_bin)
        if not item['supported']: enough=False
        if item['high_share_within_bin'] is not None:
            ordered_shares.append(Fraction(high,high+low))
        else:
            ordered_shares.append(None)
    if sum(x['high_n'] for x in bins)!=n-len([x for x in missing if x['role']=='high']) or sum(x['low_n'] for x in bins)!=n-len([x for x in missing if x['role']=='low']):
        _fail('numeric_population_mismatch','All observations must reconcile')
    missing_ids={entry['video_id'] for entry in missing}
    valid_transition_pairs=sum(hi not in missing_ids and lo not in missing_ids
                               for hi,lo in selected)
    if sum(sum(row) for row in pairs_transition)!=valid_transition_pairs:
        _fail('numeric_population_mismatch','Transition matrix misses a complete observed pair')
    direction='blocked_insufficient_joint_bin_support'
    raw_delta=None
    scaled_delta=None
    all_nonempty=bool(ordered_shares) and all(x is not None for x in ordered_shares)
    if enough and all_nonempty:
        up=all(a<=b for a,b in zip(ordered_shares,ordered_shares[1:]))
        down=all(a>=b for a,b in zip(ordered_shares,ordered_shares[1:]))
        direction=('flat_descriptive' if up and down else
                   'nondecreasing_descriptive' if up else
                   'nonincreasing_descriptive' if down else
                   'nonmonotone_descriptive')
        raw_delta=_ratio(Fraction(value_sums['high']-value_sums['low'],n))
        scaled_delta=_ratio(Fraction(value_sums['high']-value_sums['low'],
                                     n*factor.value_scale))
    return {
        'factor_id':factor.factor_id,'metric_kind':factor.metric_kind,
        'feature_family':factor.family,'feature_name':factor.feature_name,
        'source_unit':factor.source_unit,'value_scale':factor.value_scale,
        'observed_raw_ranges':{
            role:{'minimum':min(values),'maximum':max(values)} if values else None
            for role,values in source_value_ranges.items()},
        'bins':bins,'transition_matrix_high_bin_by_low_bin':pairs_transition,
        'paired_raw_direction_counts':signs,
        'missing_observations':missing,
        'sample_pair_count':n,'supported_bin_count':sum(item['supported'] for item in bins),
        'status':'candidate_descriptive_only' if enough else 'blocked_incomplete_or_sparse_bin',
        'monotonicity':direction,
        'matched_mean_raw_high_minus_low':raw_delta,
        'matched_mean_scaled_high_minus_low':scaled_delta,
        'uncertainty_interval':None,'p_value':None,'adjusted_q_value':None,
        'confirmed_effect':False,
    }


def _compose(snapshot:CandidateResearchSnapshot,strata:CandidateStrata,
             outcome:CandidateOutcomePlan,controls:CandidateMatchedControls,
             matrix:CandidateFeatureMatrix,split:CandidateHoldoutSplit,
             holdout_policy:HoldoutPolicy,lineage:tuple[HoldoutMediaLineage,...],
             policy:TechnicalNumericPolicy) -> dict:
    if not (type(snapshot) is CandidateResearchSnapshot and type(strata) is CandidateStrata
            and type(outcome) is CandidateOutcomePlan and type(controls) is CandidateMatchedControls
            and type(matrix) is CandidateFeatureMatrix and type(split) is CandidateHoldoutSplit
            and type(holdout_policy) is HoldoutPolicy and type(policy) is TechnicalNumericPolicy):
        _fail('invalid_numeric_input','Exact upstream typed inputs required')
    c=replay_candidate_controls(controls.to_json(),controls.artifact_sha256,snapshot,strata,outcome)
    m=replay_candidate_feature_matrix(matrix.to_json(),matrix.artifact_sha256,
                                      snapshot,strata,outcome,controls)
    s=replay_candidate_holdout_split(split.to_json(),split.artifact_sha256,snapshot,holdout_policy,lineage)
    if c!=controls or m!=matrix or s!=split:
        _fail('numeric_source_replay_mismatch','Typed source wrapper changed since freeze')
    src=snapshot.as_dict();ctl=controls.as_dict();mat=matrix.as_dict()
    if (snapshot.project_id!=controls.project_id or snapshot.project_id!=matrix.project_id
        or snapshot.project_id!=split.project_id
        or policy.snapshot_sha256!=snapshot.snapshot_sha256
        or policy.design_sha256!=src['design']['design_sha256']
        or policy.design_sha256!=holdout_policy.design_sha256
        or policy.controls_sha256!=controls.artifact_sha256
        or policy.matrix_sha256!=matrix.artifact_sha256
        or policy.holdout_sha256!=split.artifact_sha256
        or mat.get('source_controls_sha256')!=controls.artifact_sha256
        or ctl.get('source_snapshot_sha256')!=snapshot.snapshot_sha256):
        _fail('numeric_lineage_mismatch','Project, study, source or SHA disagreement')
    if _time(policy.registered_at)>_time(holdout_policy.discovery_before):
        _fail('numeric_post_selection','Registered bins must precede discovery')
    require_candidate_holdout_scope(split,arm='discovery',video_ids=split.discovery_video_ids)
    selected=[];reserved=[];discovery=set(split.discovery_video_ids)
    confirmation=set(split.confirmation_video_ids)
    for pair in ctl['pairs']:
        h,l=pair['case_video_id'],pair['control_video_id']
        if (h in discovery)!=(l in discovery):
            _fail('numeric_cross_arm_pair','Cannot cross holdout boundary')
        if h in discovery:
            selected.append((h,l))
        else:
            reserved.append({'case_video_id':h,'control_video_id':l,
                             'reason_code':'confirmation_reserved' if
                             h in confirmation and l in confirmation else 'embargo_or_quarantine'})
    selected_ids={i for p in selected for i in p}
    if (not policy.min_discovery_pairs<=len(selected)<=_MAX_PAIRS
        or len(selected_ids)!=2*len(selected) or not selected_ids<=discovery
        or len(selected)+len(reserved)!=len(ctl['pairs'])):
        _fail('numeric_insufficient_pairs','Complete bounded unique matched discovery sample required')
    members={x['video_id']:x for x in src['selection']['included']}
    rows={x['video_id']:x for x in mat['rows']}
    if len(members)!=len(src['selection']['included']) or len(rows)!=len(mat['rows']):
        _fail('numeric_population_mismatch','Duplicate source or matrix row')
    for h,l in selected:
        for i,role in ((h,'high'),(l,'low')):
            sr=members.get(i);mr=rows.get(i)
            if (sr is None or mr is None or mr['role']!=role
                or mr['row_sha256']!=sr['row_sha256']
                or len(mr['features'])!=len(mat['columns'])):
                _fail('numeric_row_mismatch','Matched role and SHA required')
    cols={(x['family'],x['name']):(j,x) for j,x in enumerate(mat['columns'])}
    if len(cols)!=len(mat['columns']):
        _fail('numeric_dictionary_mismatch','Duplicate technical columns forbidden')
    result=[]
    for factor in policy.factors:
        item=cols.get((factor.family,factor.feature_name))
        if (item is None or item[1]['representation']!='numeric_int'
            or item[1].get('unit')!=factor.source_unit):
            _fail('numeric_feature_unavailable','Exact raw integer feature and declared source unit required')
        for h,l in selected:
            for i in (h,l):
                cell=rows[i]['features'][item[0]]
                if (cell.get('family')!=factor.family
                    or cell.get('name')!=factor.feature_name
                    or cell.get('representation')!='numeric_int'):
                    _fail('numeric_cell_mismatch','Feature identity differs from frozen columns')
        result.append(_factor_stats(factor,selected,members,rows,item[0]))
    complete=all(x['status']=='candidate_descriptive_only' for x in result)
    return {
        'schema_version':SCHEMA,'evidence_state':'unverified','approved_live':False,
        'project_id':snapshot.project_id,'source_snapshot_sha256':snapshot.snapshot_sha256,
        'source_controls_sha256':controls.artifact_sha256,
        'source_matrix_sha256':matrix.artifact_sha256,
        'source_holdout_sha256':split.artifact_sha256,
        'policy':asdict(policy),'policy_sha256':policy.policy_sha256,
        'registered_factor_family_sha256':policy.family_sha256,
        'registered_factor_count':len(policy.factors),
        'report_status':'candidate_descriptive_numeric_only' if complete
                        else 'blocked_incomplete_registered_numeric_family',
        'discovery_pair_count':len(selected),
        'unmatched_discovery_video_ids':sorted(discovery-selected_ids),
        'reserved_pairs':reserved,
        'factors':result,
        'source_stratum_sha256':ctl.get('stratum_sha256'),
        'uncertainty_status':'blocked_no_calibrated_cluster_uncertainty',
        'valid_inferential_p_value_count':0,
        'confirmed_discoveries':None,'can_publish_significance':False,
        'causal_claim':False,'heldout_confirmation_used':False,
        'warnings':['numeric_int_source_only_no_guessed_float_conversions',
                    'fixed_point_scale_is_explicit_source_claim_not_verified_unit_conversion',
                    'group_a_and_b5_provenance_not_authenticated',
                    'do_not_tune_cutoffs_on_the_same_discovery_data',
                    'high_low_roles_defined_by_group_c7_outcome',
                    'monotonic_pattern_descriptive_not_causal',
                    'no_valid_cluster_uncertainty_or_multiple_testing_p_values',
                    'live_uae_source_rights_review_and_holdout_confirmation_blocked']
    }


def _freeze(data:dict)->CandidateTechnicalNumericReport:
    raw=json.dumps(data,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    if len(raw.encode('utf-8'))>_MAX_BYTES:
        _fail('payload_too_large','Numeric technical candidate exceeds limit')
    return CandidateTechnicalNumericReport(sha256(raw.encode('utf-8')).hexdigest(),
        data['project_id'],data['policy_sha256'],
        data['registered_factor_family_sha256'],data['report_status'],raw)


def build_candidate_technical_numeric_report(
        snapshot:CandidateResearchSnapshot,strata:CandidateStrata,
        outcome:CandidateOutcomePlan,controls:CandidateMatchedControls,
        matrix:CandidateFeatureMatrix,split:CandidateHoldoutSplit,
        holdout_policy:HoldoutPolicy,lineage:tuple[HoldoutMediaLineage,...],
        policy:TechnicalNumericPolicy)->CandidateTechnicalNumericReport:
    return _freeze(_compose(snapshot,strata,outcome,controls,matrix,split,
                            holdout_policy,lineage,policy))


def replay_candidate_technical_numeric_report(raw:str|bytes,expected_sha256:str,
        snapshot:CandidateResearchSnapshot,strata:CandidateStrata,
        outcome:CandidateOutcomePlan,controls:CandidateMatchedControls,
        matrix:CandidateFeatureMatrix,split:CandidateHoldoutSplit,
        holdout_policy:HoldoutPolicy,lineage:tuple[HoldoutMediaLineage,...])->CandidateTechnicalNumericReport:
    if type(raw) not in (str,bytes) or type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_numeric_artifact','Canonical JSON and SHA required')
    try:
        encoded=raw if type(raw) is bytes else raw.encode('utf-8')
    except UnicodeError as exc:
        raise PIEBoundaryError('invalid_numeric_artifact','Invalid UTF-8') from exc
    if len(encoded)>_MAX_BYTES:
        _fail('payload_too_large','Oversized numeric candidate')
    def unique(items):
        result={}
        for k,v in items:
            if k in result:_fail('duplicate_json_key','Duplicate JSON key')
            result[k]=v
        return result
    try:
        data=json.loads(encoded,object_pairs_hook=unique,
                        parse_constant=lambda _: _fail('invalid_numeric_artifact','Nonfinite JSON'))
    except PIEBoundaryError: raise
    except (UnicodeError,ValueError,TypeError,RecursionError) as exc:
        raise PIEBoundaryError('invalid_numeric_artifact','Malformed numeric JSON') from exc
    if type(data) is not dict or canonical_hash(data)!=expected_sha256:
        _fail('numeric_hash_mismatch','Report fingerprint mismatch')
    if data.get('schema_version')!=SCHEMA or data.get('approved_live') is not False:
        _fail('unsupported_numeric_artifact','No live/verified promotion from offline candidate')
    spec=data.get('policy')
    if type(spec) is not dict or set(spec)!=set(TechnicalNumericPolicy.__dataclass_fields__):
        _fail('invalid_numeric_policy','Complete frozen policy object required')
    try:
        if type(spec['factors']) is not list:
            _fail('invalid_numeric_policy','Factor family must be list')
        factors=tuple(TechnicalNumericFactor(**{**f,
            'bin_cutoffs_raw':tuple(f['bin_cutoffs_raw']) if type(f.get('bin_cutoffs_raw')) is list
            else f.get('bin_cutoffs_raw')}) for f in spec['factors'])
        policy=TechnicalNumericPolicy(**{**spec,'factors':factors})
    except PIEBoundaryError: raise
    except (KeyError,TypeError,ValueError) as exc:
        raise PIEBoundaryError('invalid_numeric_policy','Malformed numeric family') from exc
    rebuilt=build_candidate_technical_numeric_report(
        snapshot,strata,outcome,controls,matrix,split,holdout_policy,lineage,policy)
    if (rebuilt.artifact_sha256!=expected_sha256 or rebuilt.as_dict()!=data
        or rebuilt.to_json().encode('utf-8')!=encoded):
        _fail('numeric_replay_mismatch','Changed source or rehashed artifact cannot replay')
    return rebuilt
