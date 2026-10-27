"""PIE-MOD-19: reproducible discovery-only descriptive stability audit.

No post-selection inference, causal interpretation or authenticated producer claims.
Opposing directions, unknown peer providers and incomparable pair memberships
are explicit audit findings, not hidden outliers to remove.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
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
from .holdout import CandidateHoldoutSplit, HoldoutPolicy, HoldoutMediaLineage
from .adjustment import (AdjustmentPolicy, AdjustmentCovariate,
    CandidateAdjustmentReport, replay_candidate_adjustment_report)

SCHEMA = 'pie-temporal-cohort-stability-candidate/v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_VERSION = re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_RATE = re.compile(r'(?:0|1)(?:\.0{1,12})?\Z|0\.[0-9]{1,12}\Z')
_DIMENSIONS = ('publication_window','creator','niche','topic','age','posting_time','platform','source_provider')
_MAX_BYTES = 16 * 1024 * 1024
_MAX_ROWS = 2000


def _fail(code: str, reason: str) -> None:
    raise PIEBoundaryError(code, reason)


def _time(value: object) -> datetime:
    if type(value) is not str or not 10 <= len(value) <= 40:
        _fail('invalid_stability_time', 'Timezone-aware ISO timestamp required')
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise PIEBoundaryError('invalid_stability_time', 'Invalid ISO timestamp') from exc
    if dt.utcoffset() is None:
        _fail('invalid_stability_time', 'Timezone offset required')
    return dt.astimezone(timezone.utc)


def _fraction(f: Fraction) -> dict:
    # Never round using floating point or ambient decimal context.
    from decimal import localcontext, ROUND_HALF_EVEN
    with localcontext() as ctx:
        ctx.prec = 80
        ctx.rounding = ROUND_HALF_EVEN
        val = (Decimal(f.numerator) / Decimal(f.denominator)).quantize(Decimal('0.000000000001'))
    return {'numerator': f.numerator, 'denominator': f.denominator, 'decimal_12dp': format(val, 'f')}


@dataclass(frozen=True, slots=True)
class StabilityWindow:
    label: str
    start_inclusive: str
    end_exclusive: str

    def __post_init__(self) -> None:
        if type(self.label) is not str or not re.fullmatch(r'[a-z][a-z0-9_]{0,79}', self.label):
            _fail('invalid_stability_window','Stable machine label required')
        if _time(self.start_inclusive) >= _time(self.end_exclusive):
            _fail('invalid_stability_window','Half-open interval must be positive')


@dataclass(frozen=True, slots=True)
class StabilityPolicy:
    policy_version: str
    method_version: str
    design_sha256: str
    snapshot_sha256: str
    controls_sha256: str
    matrix_sha256: str
    holdout_sha256: str
    adjustment_sha256: str
    registered_at: str
    windows: tuple[StabilityWindow, ...]
    dimensions: tuple[str, ...]
    minimum_pairs_per_group: int
    minimum_creators_per_group: int
    minimum_groups_per_dimension: int
    direction_floor: str
    minimum_covered_pair_fraction: str
    sample_scope: str = 'discovery_only'
    pair_boundary: str = 'reject_cross_group_without_dropping'
    reference_metric: str = 'paired_boolean_feature_prevalence_difference'
    classification: str = 'descriptive_directional_robustness_only'

    def __post_init__(self) -> None:
        if any(type(getattr(self,n)) is not str or not _VERSION.fullmatch(getattr(self,n))
               for n in ('policy_version','method_version')):
            _fail('invalid_stability_policy','Explicit method versions required')
        for name in ('design_sha256','snapshot_sha256','controls_sha256','matrix_sha256',
                     'holdout_sha256','adjustment_sha256'):
            x = getattr(self, name)
            if type(x) is not str or not _SHA.fullmatch(x):
                _fail('invalid_stability_policy','All upstream digests are required')
        _time(self.registered_at)
        if (type(self.windows) is not tuple or not 2 <= len(self.windows) <= 32
            or any(type(x) is not StabilityWindow for x in self.windows)):
            _fail('invalid_stability_policy','2..32 predeclared temporal windows required')
        if len({w.label for w in self.windows}) != len(self.windows):
            _fail('invalid_stability_policy','Duplicate window label')
        for a,b in zip(self.windows,self.windows[1:]):
            if _time(a.end_exclusive) != _time(b.start_inclusive):
                _fail('invalid_stability_policy','Windows must form a gap-free sorted partition')
        if (type(self.dimensions) is not tuple or not 1 <= len(self.dimensions) <= len(_DIMENSIONS)
            or self.dimensions != tuple(d for d in _DIMENSIONS if d in self.dimensions)
            or any(type(d) is not str for d in self.dimensions)):
            _fail('invalid_stability_policy','Unique ordered dimensions required')
        for name,cap in (('minimum_pairs_per_group',_MAX_ROWS),
                         ('minimum_creators_per_group',_MAX_ROWS),
                         ('minimum_groups_per_dimension',32)):
            x = getattr(self,name)
            if type(x) is not int or not 1 <= x <= cap:
                _fail('invalid_stability_policy','Explicit positive group support threshold required')
        for name,allow_one in (('direction_floor',False),('minimum_covered_pair_fraction',True)):
            raw=getattr(self,name)
            if type(raw) is not str or not _RATE.fullmatch(raw):
                _fail('invalid_stability_policy','Only canonical bounded decimal fraction allowed')
            if Decimal(raw) == 0 or (not allow_one and Decimal(raw) >= 1):
                _fail('invalid_stability_policy','Positive direction and coverage thresholds required')
        if (self.sample_scope!='discovery_only'
            or self.pair_boundary!='reject_cross_group_without_dropping'
            or self.reference_metric!='paired_boolean_feature_prevalence_difference'
            or self.classification!='descriptive_directional_robustness_only'):
            _fail('invalid_stability_policy','Unsupported publication/causal/partial-sample mode')

    @property
    def policy_sha256(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class CandidateStabilityReport:
    artifact_sha256: str
    project_id: str
    source_adjustment_sha256: str
    policy_sha256: str
    audit_status: str
    _json: str = field(repr=False)

    @property
    def verified_live(self) -> bool: return False

    @property
    def ready_for_analysis(self) -> bool: return False

    def as_dict(self) -> dict: return json.loads(self._json)

    def to_json(self) -> str: return self._json


def _classify_groups(groups: dict[str, list[tuple[int, int, str, str]]],
                     *, expected_pairs: int, minimum_pairs: int,
                     minimum_creators: int, minimum_groups: int,
                     floor: Decimal, coverage: Decimal) -> dict:
    """Exact directional audit. Each pair carries (case boolean, low boolean,
    case creator, low creator); no single-arm rows or p-values are counted."""
    if expected_pairs <= 0:
        _fail('invalid_stability_population','Expected positive frozen discovery pair count')
    table=[]
    processed=0
    supported_directions=[]
    for group in sorted(groups):
        pairs=groups[group]
        creators={creator for h,l,high_creator,low_creator in pairs
                  for creator in (high_creator,low_creator)}
        hits_high=sum(h for h,l,_,_ in pairs)
        hits_low=sum(l for h,l,_,_ in pairs)
        n=len(pairs)
        processed+=n
        if n > expected_pairs or any(type(h) is not int or h not in (0,1) or
                                     type(l) is not int or l not in (0,1) for h,l,_,_ in pairs):
            _fail('invalid_stability_population','Unsupported or fabricated pair values')
        diff=Fraction(hits_high-hits_low,n) if n else None
        supported = n >= minimum_pairs and len(creators) >= minimum_creators
        direction=('positive' if diff is not None and Decimal(diff.numerator)/Decimal(diff.denominator) >= floor
                   else 'negative' if diff is not None and
                        Decimal(diff.numerator)/Decimal(diff.denominator) <= -floor
                   else 'near_zero') if supported else 'unsupported'
        table.append({'group':group,'pair_count':n,'distinct_creators':len(creators),
                      'high_hits':hits_high,'low_hits':hits_low,
                      'difference':_fraction(diff) if diff is not None else None,
                      'supported':supported,'direction':direction})
        if supported: supported_directions.append(direction)
    if processed > expected_pairs:
        _fail('invalid_stability_population','Groups duplicate frozen matched pairs')
    full_cover=processed == expected_pairs and Decimal(processed)/Decimal(expected_pairs) >= coverage
    all_supported=len(table)>=minimum_groups and all(x['supported'] for x in table)
    if not full_cover:
        status='blocked_incomplete_or_cross_group_coverage'
    elif not all_supported:
        status='blocked_insufficient_subgroup_support'
    elif len(set(supported_directions))==1 and supported_directions[0] in ('positive','negative'):
        status='consistent_direction_descriptive_only'
    elif 'positive' in supported_directions and 'negative' in supported_directions:
        status='unstable_direction_reversal'
    else:
        status='unstable_weak_or_mixed_direction'
    return {'status':status,'expected_discovery_pairs':expected_pairs,
            'covered_pairs':processed,'group_count':len(table),
            'supported_group_count':sum(v['supported'] for v in table),
            'groups':table,'causal_claim':False,'confirmed':False}


def _compose(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
             outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
             matrix: CandidateFeatureMatrix, split: CandidateHoldoutSplit,
             holdout_policy: HoldoutPolicy, lineage: tuple[HoldoutMediaLineage,...],
             adjustment_policy: AdjustmentPolicy,
             covariates: tuple[AdjustmentCovariate,...],
             adjustment: CandidateAdjustmentReport,
             policy: StabilityPolicy) -> dict:
    if not (type(snapshot) is CandidateResearchSnapshot and type(controls) is CandidateMatchedControls
            and type(matrix) is CandidateFeatureMatrix and type(split) is CandidateHoldoutSplit
            and type(adjustment) is CandidateAdjustmentReport and type(policy) is StabilityPolicy):
        _fail('invalid_stability_input','Exact frozen PIE artifacts and policy required')
    verified = replay_candidate_adjustment_report(
        adjustment.to_json(),adjustment.artifact_sha256,
        snapshot,strata,outcome,controls,matrix,split,holdout_policy,lineage,
        adjustment_policy,covariates)
    if verified != adjustment:
        _fail('stability_lineage_mismatch','Adjustment wrapper altered')
    a=verified.as_dict()
    if (a.get('schema_version')!='pie-covariate-standardization-candidate/v1'
        or a.get('report_status')!='candidate_descriptive_association_only'
        or a.get('approved_live') is not False
        or adjustment.project_id != snapshot.project_id
        or adjustment.project_id != split.project_id
        or adjustment.policy_sha256 != adjustment_policy.policy_sha256
        or a.get('source_matrix_sha256') != matrix.artifact_sha256
        or a.get('source_controls_sha256') != controls.artifact_sha256
        or a.get('source_holdout_sha256') != split.artifact_sha256
        or a.get('adjusted_risk_difference') is None):
        _fail('stability_adjustment_blocked','Only fully supported exact candidate adjustment can be audited')
    if (policy.design_sha256 != adjustment_policy.design_sha256
        or policy.snapshot_sha256!=snapshot.snapshot_sha256
        or policy.controls_sha256!=controls.artifact_sha256
        or policy.matrix_sha256!=matrix.artifact_sha256
        or policy.holdout_sha256!=split.artifact_sha256
        or policy.adjustment_sha256!=adjustment.artifact_sha256):
        _fail('stability_lineage_mismatch','All design and source hashes must match')
    if _time(policy.registered_at)>_time(holdout_policy.discovery_before):
        _fail('stability_post_selection','Stability strata must be frozen before discovery')
    source=snapshot.as_dict()
    included={x['video_id']:x for x in source['selection']['included']}
    features=matrix.as_dict()
    column=[i for i,x in enumerate(features['columns']) if
            x['family']==adjustment_policy.feature_family and
            x['name']==adjustment_policy.feature_name and x['representation']=='boolean']
    if len(column)!=1:
        _fail('stability_feature_missing','Exact frozen stage18 binary feature unavailable')
    ix=column[0]
    by_matrix={x['video_id']:x for x in features['rows']}
    by_cov={x.video_id:x for x in covariates}
    if len(by_matrix)!=len(features['rows']) or len(by_cov)!=len(covariates):
        _fail('stability_population_mismatch','Duplicate source features/covariates')
    discovery=set(split.discovery_video_ids)
    pairs=controls.as_dict()['pairs']
    grouped={d:{} for d in policy.dimensions}
    n_discovery=0
    dropped_by_dimension={d:[] for d in policy.dimensions}
    for pair in pairs:
        h,l=pair['case_video_id'],pair['control_video_id']
        if (h in discovery)!=(l in discovery):
            _fail('stability_cross_arm_pair','Cannot include contaminated holdout pairs')
        if h not in discovery:
            continue
        n_discovery+=1
        points=[]
        for i,role in ((h,'high'),(l,'low')):
            member=included.get(i)
            row=by_matrix.get(i)
            cov=by_cov.get(i)
            if member is None or row is None or cov is None or row['role']!=role or row['row_sha256']!=member['row_sha256'] or cov.row_sha256!=member['row_sha256']:
                _fail('stability_row_mismatch','Exact matched source identity missing')
            observation=row['features'][ix]['observation']
            if observation['state']!='present' or type(observation['value']) is not bool:
                _fail('stability_missing_feature','Null/missing/0 is not a Boolean observation')
            raw=member['row']
            pub=_time(raw['published_at'])
            observed=_time(raw['observed_at'])
            elapsed=observed-pub
            if elapsed.days<0:
                _fail('stability_invalid_time','No future publication may produce prior observation')
            age=elapsed.days*86400+elapsed.seconds
            age_label=str(sum(age >= cutoff for cutoff in adjustment_policy.age_cutoffs_seconds))
            hour_label=str(next((j for j in range(len(adjustment_policy.utc_hour_cutoffs)-1)
                         if adjustment_policy.utc_hour_cutoffs[j]<=pub.hour<adjustment_policy.utc_hour_cutoffs[j+1]),-1))
            window=next((w.label for w in policy.windows
                         if _time(w.start_inclusive)<=pub<_time(w.end_exclusive)),None)
            if window is None:
                _fail('stability_window_coverage','All discovery publications must fit frozen contiguous windows')
            points.append((int(observation['value']),raw['creator_key'],{
                'publication_window':window,'creator':raw['creator_key'],
                'niche':cov.niche,'topic':cov.topic,'age':age_label,
                'posting_time':hour_label,'platform':raw['platform'],
                'source_provider':source['source']['pointer']['source_id']}))
        for dimension in policy.dimensions:
            akey=points[0][2][dimension]
            bkey=points[1][2][dimension]
            if akey!=bkey:
                dropped_by_dimension[dimension].append({'case_video_id':h,'control_video_id':l,
                    'reason_code':'cross_group_pair_not_comparable'})
            else:
                grouped[dimension].setdefault(akey,[]).append(
                    (points[0][0],points[1][0],points[0][1],points[1][1]))
    if n_discovery!=a['sample']['discovery_pairs'] or n_discovery<=0 or 2*n_discovery>_MAX_ROWS:
        _fail('stability_population_mismatch','Discovery matched pair denominator altered')
    floor=Decimal(policy.direction_floor)
    cover=Decimal(policy.minimum_covered_pair_fraction)
    results=[]
    for dimension in policy.dimensions:
        result=_classify_groups(grouped[dimension],expected_pairs=n_discovery,
                 minimum_pairs=policy.minimum_pairs_per_group,
                 minimum_creators=policy.minimum_creators_per_group,
                 minimum_groups=policy.minimum_groups_per_dimension,
                 floor=floor,coverage=cover)
        result.update({'dimension':dimension,'cross_group_pairs':dropped_by_dimension[dimension],
                       'cross_group_pair_count':len(dropped_by_dimension[dimension])})
        results.append(result)
    return {
        'schema_version':SCHEMA,'evidence_state':'unverified','approved_live':False,
        'project_id':snapshot.project_id,
        'source_snapshot_sha256':snapshot.snapshot_sha256,
        'source_adjustment_sha256':adjustment.artifact_sha256,
        'source_holdout_sha256':split.artifact_sha256,
        'source_controls_sha256':controls.artifact_sha256,
        'source_matrix_sha256':matrix.artifact_sha256,
        'policy':asdict(policy),'policy_sha256':policy.policy_sha256,
        'audit_status':'candidate_descriptive_stability_audit_only',
        'discovery_pairs':n_discovery,'dimensions':results,
        'unstable_or_blocked_dimensions':[d['dimension'] for d in results
             if d['status']!='consistent_direction_descriptive_only'],
        'stable_direction_dimensions':[d['dimension'] for d in results
             if d['status']=='consistent_direction_descriptive_only'],
        'confirmation_sample_touched':False,'source_provider_count':1,
        'validated_multi_provider_stability':False,
        'eligible_for_pattern_promotion':False,'confirmed_discoveries':None,
        'p_values':None,'causal_claim':False,
        'warnings':['offline_candidate_not_current_source_authorization',
                    'prepublication_covariates_and_registration_unverified',
                    'single_uae_source_cannot_prove_cross_provider_stability',
                    'same_discovery_sample_does_not_establish_out_of_sample_replication',
                    'no_inferential_p_values_or_causal_effects']
    }


def _freeze(value: dict) -> CandidateStabilityReport:
    raw=json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'),allow_nan=False)
    if len(raw.encode('utf-8'))>_MAX_BYTES:
        _fail('payload_too_large','Stability artifact exceeds byte bound')
    return CandidateStabilityReport(sha256(raw.encode('utf-8')).hexdigest(),value['project_id'],
         value['source_adjustment_sha256'],value['policy_sha256'],value['audit_status'],raw)


def build_candidate_stability_report(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
        outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
        matrix: CandidateFeatureMatrix, split: CandidateHoldoutSplit,
        holdout_policy: HoldoutPolicy, lineage: tuple[HoldoutMediaLineage,...],
        adjustment_policy: AdjustmentPolicy, covariates: tuple[AdjustmentCovariate,...],
        adjustment: CandidateAdjustmentReport, policy: StabilityPolicy) -> CandidateStabilityReport:
    return _freeze(_compose(snapshot,strata,outcome,controls,matrix,split,holdout_policy,
                            lineage,adjustment_policy,covariates,adjustment,policy))


def replay_candidate_stability_report(raw: str|bytes, expected_sha256: str,
        snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
        outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
        matrix: CandidateFeatureMatrix, split: CandidateHoldoutSplit,
        holdout_policy: HoldoutPolicy, lineage: tuple[HoldoutMediaLineage,...],
        adjustment_policy: AdjustmentPolicy, covariates: tuple[AdjustmentCovariate,...],
        adjustment: CandidateAdjustmentReport, policy: StabilityPolicy) -> CandidateStabilityReport:
    if type(raw) not in (str,bytes) or type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_stability_artifact','Canonical JSON and SHA required')
    try:
        encoded=raw if type(raw) is bytes else raw.encode('utf-8')
    except UnicodeError as exc:
        raise PIEBoundaryError('invalid_stability_artifact','Invalid UTF-8 artifact') from exc
    if len(encoded)>_MAX_BYTES:
        _fail('payload_too_large','Stability artifact exceeds byte bound')
    def unique(items):
        obj={}
        for k,v in items:
            if k in obj:
                _fail('duplicate_json_key','Repeated JSON property')
            obj[k]=v
        return obj
    try:
        value=json.loads(encoded,object_pairs_hook=unique,
                         parse_constant=lambda _: _fail('invalid_stability_artifact','Nonfinite JSON'))
    except PIEBoundaryError: raise
    except (UnicodeError,ValueError,TypeError,RecursionError) as exc:
        raise PIEBoundaryError('invalid_stability_artifact','Malformed stability JSON') from exc
    if type(value) is not dict or canonical_hash(value)!=expected_sha256:
        _fail('stability_hash_mismatch','Digest mismatch')
    if value.get('schema_version')!=SCHEMA or value.get('approved_live') is not False:
        _fail('unsupported_stability_artifact','Cannot upgrade offline stability report to live')
    rebuilt=build_candidate_stability_report(snapshot,strata,outcome,controls,matrix,split,
              holdout_policy,lineage,adjustment_policy,covariates,adjustment,policy)
    if rebuilt.as_dict()!=value or rebuilt.artifact_sha256!=expected_sha256 or rebuilt.to_json().encode('utf-8')!=encoded:
        _fail('stability_replay_mismatch','Rehashed or changed report cannot replay')
    return rebuilt
