"""PIE-MOD-15 candidate-only matched-pair bootstrap intervals.

A frozen descriptive/exploratory interval is NOT confirmatory evidence. Read-time
rights, reviewer approval, holdout, multiple testing and causal validity remain
unverified. Resamples entire matched pairs, never independent publication rows.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
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
from .marginal import CandidateMarginalDiscovery, MarginalHypothesis, _hit
from .effects import CandidateDescriptiveEffects, replay_candidate_descriptive_effects, _fraction

SCHEMA = 'pie-paired-bootstrap-uncertainty/v1'
_SHA = re.compile(r'[0-9a-f]{64}\Z')
_VER = re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_MAX_BYTES = 128 * 1024 * 1024
_MAX_OPERATIONS = 1_200_000


def _fail(code: str, message: str) -> None:
    raise PIEBoundaryError(code, message)


@dataclass(frozen=True, slots=True)
class UncertaintyPolicy:
    policy_version: str
    method_version: str
    design_sha256: str
    seed: int
    resamples: int
    minimum_independent_pairs: int
    confidence_level: str = '0.95'
    resampling_unit: str = 'matched_pair'
    creator_dependence: str = 'reject_creators_reused_across_pairs'
    missingness: str = 'block_if_any_pair_missing_feature'
    interval_method: str = 'nearest_rank_percentile'
    ratio_zero_rule: str = 'undefined_if_any_resample_has_zero_low_hits'

    def __post_init__(self) -> None:
        if any(type(getattr(self, name)) is not str or not _VER.fullmatch(getattr(self, name))
               for name in ('policy_version', 'method_version')):
            _fail('invalid_uncertainty_policy', 'Both method and policy versions required')
        if type(self.design_sha256) is not str or not _SHA.fullmatch(self.design_sha256):
            _fail('invalid_uncertainty_policy', 'Pinned study design required')
        if type(self.seed) is not int or not 0 <= self.seed < 2**64:
            _fail('invalid_uncertainty_policy', 'Seed must be explicit uint64')
        if type(self.resamples) is not int or not 400 <= self.resamples <= 2000 or self.resamples % 40:
            _fail('invalid_uncertainty_policy', 'Require 400..2000 draws, multiple of 40')
        if type(self.minimum_independent_pairs) is not int or not 8 <= self.minimum_independent_pairs <= 1000:
            _fail('invalid_uncertainty_policy', 'Minimum distinct pairs must be >= 8')
        if (self.confidence_level != '0.95' or self.resampling_unit != 'matched_pair'
            or self.creator_dependence != 'reject_creators_reused_across_pairs'
            or self.missingness != 'block_if_any_pair_missing_feature'
            or self.interval_method != 'nearest_rank_percentile'
            or self.ratio_zero_rule != 'undefined_if_any_resample_has_zero_low_hits'):
            _fail('invalid_uncertainty_policy', 'Unsupported/incompatible uncertainty method')

    @property
    def policy_sha256(self) -> str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True, slots=True)
class CandidateUncertaintyIntervals:
    artifact_sha256: str
    project_id: str
    source_effects_sha256: str
    policy_sha256: str
    interval_count: int
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


def _draw_indices(seed: int, count: int, draws: int):
    """Independent version-stable SHA256 counter stream; rejection prevents modulo bias."""
    counter = 0
    cutoff = (2**64 // count) * count
    prefix = seed.to_bytes(8, 'big')
    for _ in range(draws):
        sample = []
        for _ in range(count):
            while True:
                digest = sha256(prefix + counter.to_bytes(8, 'big')).digest()
                counter += 1
                value = int.from_bytes(digest[:8], 'big')
                if value < cutoff:
                    sample.append(value % count)
                    break
        yield tuple(sample)


def _numeric_stats(high: list[int], low: list[int], boundaries: list[int]) -> tuple[Decimal, Fraction]:
    def median(v: list[int]) -> Decimal:
        s = sorted(v)
        return Decimal(s[len(s)//2]) if len(s)%2 else (Decimal(s[len(s)//2-1])+Decimal(s[len(s)//2]))/2
    def hist(values: list[int]):
        bins=[0] * (len(boundaries)+1)
        for value in values:
            i=0
            while i<len(boundaries) and value>=boundaries[i]:i+=1
            bins[i]+=1
        return bins
    h=hist(high); l=hist(low); n=len(high); m=len(low)
    tv=sum((abs(Fraction(a,n)-Fraction(b,m)) for a,b in zip(h,l)),Fraction(0))/2
    return median(high)-median(low),tv


def _binary_stats(high: list[bool], low: list[bool]) -> tuple[Fraction, Fraction | None]:
    h=sum(high); l=sum(low)
    rh=Fraction(h,len(high)); rl=Fraction(l,len(low))
    return rh-rl, None if l==0 else rh/rl


def _bounds(values: list[Fraction] | list[Decimal], places: int, kind: str) -> dict:
    ordered=sorted(values)
    n=len(ordered)
    # ceil(alpha/2 * B) - 1 and ceil((1-alpha/2) * B) - 1.
    low=ordered[(n+39)//40-1]
    high=ordered[(39*n+39)//40-1]
    if kind == 'fraction':
        return {'lower':_fraction(low,places), 'upper':_fraction(high,places)}
    return {'lower_raw':format(low,'f'), 'upper_raw':format(high,'f')}


def _compose(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
             outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
             matrix: CandidateFeatureMatrix, coverage: CandidateCoverageAudit,
             discovery: CandidateMarginalDiscovery, effects: CandidateDescriptiveEffects,
             policy: UncertaintyPolicy) -> dict:
    if type(effects) is not CandidateDescriptiveEffects:
        _fail('invalid_uncertainty_input', 'Exact frozen source effects required')
    verified = replay_candidate_descriptive_effects(effects.to_json(),effects.artifact_sha256,
          snapshot,strata,outcome,controls,matrix,coverage,discovery)
    if verified != effects:
        _fail('uncertainty_lineage_mismatch', 'Effect source wrapper altered')
    e=verified.as_dict(); d=discovery.as_dict(); f=matrix.as_dict()
    c=controls.as_dict(); source=snapshot.as_dict()
    if (e.get('approved_live') is not False or e.get('evidence_state')!='unverified'
        or e['project_id']!=snapshot.project_id or policy.design_sha256!=e['policy']['design_sha256']
        or d['registered_hypothesis_count'] != e['effect_count']):
        _fail('uncertainty_lineage_mismatch', 'Study/design/hypothesis family mismatch')
    pairs=c['pairs']; n=len(pairs)
    if (n<policy.minimum_independent_pairs or n>1000 or n*policy.resamples*effects.effect_count>_MAX_OPERATIONS):
        _fail('uncertainty_insufficient_pairs','Sample too small or bounded bootstrap work exceeded')
    by_id={r['video_id']:r for r in f['rows']}
    source_by_id={r['video_id']:r['row'] for r in source['selection']['included']}
    if len(by_id)!=2*n or len(source_by_id)!=len(snapshot.included_video_ids):
        _fail('uncertainty_lineage_mismatch','Matrix or source population inconsistent')
    # Authors may correlate posts across different matched pairs. Do not pretend
    # pair-resampling clusters them correctly; refuse until creator-cluster bootstrap.
    seen_creator: set[str] = set()
    for pair in pairs:
        h=pair['case_video_id']; l=pair['control_video_id']
        if h not in by_id or l not in by_id or h not in source_by_id or l not in source_by_id:
            _fail('uncertainty_lineage_mismatch','Unmatched feature row')
        creators={source_by_id[h]['creator_key'],source_by_id[l]['creator_key']}
        if creators & seen_creator:
            _fail('uncertainty_creator_dependence','Creator appears in multiple matched pairs')
        seen_creator.update(creators)
    column_index={(col['family'],col['name']):i for i,col in enumerate(f['columns'])}
    # Derive *all* row level values before resampling. No post-hoc hypothesis filtering.
    prepared=[]
    for item, original in zip(d['hypotheses'],e['effects']):
        hspec=item['hypothesis']
        if hspec['hypothesis_id']!=original['hypothesis_id']:
            _fail('uncertainty_lineage_mismatch','Effect/hypothesis order mismatch')
        key=(hspec['family'],hspec['feature_name'])
        ix=column_index.get(key)
        if ix is None:
            _fail('uncertainty_lineage_mismatch','Missing frozen feature column')
        hyp=MarginalHypothesis(hspec['hypothesis_id'],hspec['family'],hspec['feature_name'],
            hspec['analysis'],tuple(hspec['target']) if hspec['analysis']=='sequence_equals' else hspec['target'],
            tuple(hspec['numeric_boundaries']))
        cells=[]
        for pair_index,pair in enumerate(pairs):
            values=[]
            for side,role in (('case','high'),('control','low')):
                row=by_id[pair[side+'_video_id']]
                if row['role']!=role or row['pair_index']!=pair_index:
                    _fail('uncertainty_lineage_mismatch','Pair and role assignment changed')
                state=row['features'][ix]['observation']
                if state['state']!='present':
                    _fail('uncertainty_missing_pair','Cannot infer complete-pair uncertainty using absent/null cells')
                v=state['value']
                if hspec['analysis']=='numeric_distribution':
                    if type(v) is not int:
                        _fail('uncertainty_invalid_feature','Numeric cell must be integer')
                    values.append(v)
                else:
                    values.append(_hit(v,hyp))
            cells.append(tuple(values))
        highs=[v[0] for v in cells]; lows=[v[1] for v in cells]
        if hspec['analysis']=='numeric_distribution':
            point=_numeric_stats(highs,lows,hspec['numeric_boundaries'])
            if (format(point[0],'f')!=original['median_difference_high_minus_low_raw']
                or point[1]!=Fraction(original['histogram_tv_distance']['numerator'],original['histogram_tv_distance']['denominator'])):
                _fail('uncertainty_point_mismatch','Bootstrap target differs from stage14 observed point estimate')
        else:
            point=_binary_stats(highs,lows)
            if point[0]!=Fraction(original['risk_difference']['numerator'],original['risk_difference']['denominator']):
                _fail('uncertainty_point_mismatch','Binary bootstrap point differs from effect')
            if point[1] is None:
                if original['risk_ratio'] is not None:
                    _fail('uncertainty_point_mismatch','Undefined zero baseline was ignored')
            elif (original['risk_ratio'] is None or point[1]!=Fraction(original['risk_ratio']['numerator'],original['risk_ratio']['denominator'])):
                _fail('uncertainty_point_mismatch','Ratio source differs')
        prepared.append((hspec,original,cells))
    series=[{'hypothesis_id':spec['hypothesis_id'],'analysis':spec['analysis'],
             'n_complete_pairs':n,'source_effect_sha256':canonical_hash(original),
             'replicate_statistics':[]}
            for spec,original,_ in prepared]
    # Identical resample draws for all hypotheses to preserve across-feature dependence.
    for ix_tuple in _draw_indices(policy.seed,n,policy.resamples):
        for series_item,(spec,original,pair_values) in zip(series,prepared):
            h=[pair_values[i][0] for i in ix_tuple];l=[pair_values[i][1] for i in ix_tuple]
            value=(_numeric_stats(h,l,spec['numeric_boundaries']) if spec['analysis']=='numeric_distribution'
                   else _binary_stats(h,l))
            series_item['replicate_statistics'].append(value)
    results=[]
    for entry,(spec,original,_) in zip(series,prepared):
        values=entry.pop('replicate_statistics')
        if spec['analysis']=='numeric_distribution':
            median_interval=_bounds([v[0] for v in values],e['policy']['decimal_places'],'decimal')
            tv_interval=_bounds([v[1] for v in values],e['policy']['decimal_places'],'fraction')
            result={'median_difference_interval':{**median_interval,'unit':original['unit']},
                    'histogram_tv_interval':{**tv_interval,'unit':'fraction_of_observed_distribution'},
                    'risk_difference_interval':None,'risk_ratio_interval':None,
                    'risk_ratio_interval_status':'not_applicable'}
        else:
            delta_interval=_bounds([v[0] for v in values],e['policy']['decimal_places'],'fraction')
            ratios=[v[1] for v in values]
            no_ratio=any(v is None for v in ratios)
            result={'median_difference_interval':None,'histogram_tv_interval':None,
                    'risk_difference_interval':{**delta_interval,'unit':'probability_fraction'},
                    'risk_ratio_interval':None if no_ratio else {**_bounds(ratios,e['policy']['decimal_places'],'fraction'),'unit':'dimensionless'},
                    'risk_ratio_interval_status':'undefined_bootstrap_zero_baseline' if no_ratio else 'defined'}
        results.append({**entry,**result})
    return {'schema_version':SCHEMA,'evidence_state':'unverified','approved_live':False,
            'project_id':snapshot.project_id,'source_effects_sha256':effects.artifact_sha256,
            'source_discovery_sha256':discovery.artifact_sha256,
            'source_matrix_sha256':matrix.artifact_sha256,'source_controls_sha256':controls.artifact_sha256,
            'registered_hypothesis_family_sha256':d['hypothesis_family_sha256'],
            'registered_hypothesis_count':e['effect_count'],
            'policy':asdict(policy),'policy_sha256':policy.policy_sha256,
            'bootstrap_unit':'matched_pair','replicate_count':policy.resamples,
            'confidence_level':'0.95','quantile_definition':'nearest_rank_indices_ceil_0.025B_and_ceil_0.975B',
            'independent_pair_count':n,'all_hypotheses':results,
            'warnings':['exploratory_not_confirmatory','unverified_source_and_review',
                'no_multiple_testing_correction','conditional_on_match_and_feature_selection',
                'not_a_population_sampling_frame','not_causal','no_creator_clustering_beyond_matched_pair'],
            'p_values':None,'adjusted_p_values':None,'causal_claim':False}


def _freeze(value: dict) -> CandidateUncertaintyIntervals:
    raw=json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
    if len(raw.encode('utf-8'))>_MAX_BYTES:
        _fail('payload_too_large','Bootstrap candidate exceeds byte limit')
    return CandidateUncertaintyIntervals(sha256(raw.encode()).hexdigest(),value['project_id'],
          value['source_effects_sha256'],value['policy_sha256'],len(value['all_hypotheses']),raw)


def build_candidate_uncertainty_intervals(snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
            outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
            matrix: CandidateFeatureMatrix, coverage: CandidateCoverageAudit,
            discovery: CandidateMarginalDiscovery, effects: CandidateDescriptiveEffects,
            policy: UncertaintyPolicy) -> CandidateUncertaintyIntervals:
    if type(policy) is not UncertaintyPolicy:
        _fail('invalid_uncertainty_policy','Explicit bootstrap policy required')
    return _freeze(_compose(snapshot,strata,outcome,controls,matrix,coverage,discovery,effects,policy))


def replay_candidate_uncertainty_intervals(raw: str|bytes, expected_sha256: str,
            snapshot: CandidateResearchSnapshot, strata: CandidateStrata,
            outcome: CandidateOutcomePlan, controls: CandidateMatchedControls,
            matrix: CandidateFeatureMatrix, coverage: CandidateCoverageAudit,
            discovery: CandidateMarginalDiscovery, effects: CandidateDescriptiveEffects) -> CandidateUncertaintyIntervals:
    if type(raw) not in (str,bytes) or type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_uncertainty_artifact','Expected canonical JSON and SHA')
    if len(raw if type(raw) is bytes else raw.encode('utf-8'))>_MAX_BYTES:
        _fail('payload_too_large','Uncertainty artifact oversized')
    def unique(pairs):
        d={}
        for k,v in pairs:
            if k in d:_fail('duplicate_json_key','Duplicate uncertainty JSON key')
            d[k]=v
        return d
    try:
        data=json.loads(raw,object_pairs_hook=unique,
                        parse_constant=lambda _: _fail('invalid_uncertainty_artifact','Nonfinite JSON'))
    except PIEBoundaryError:raise
    except (ValueError,UnicodeError,TypeError,RecursionError) as exc:
        raise PIEBoundaryError('invalid_uncertainty_artifact','Malformed uncertainty JSON') from exc
    if type(data) is not dict or canonical_hash(data)!=expected_sha256:
        _fail('uncertainty_hash_mismatch','Uncertainty artifact digest mismatch')
    if data.get('schema_version')!=SCHEMA or data.get('evidence_state')!='unverified' or data.get('approved_live') is not False:
        _fail('unsupported_uncertainty_artifact','Cannot promote candidate uncertainty to production')
    settings=data.get('policy')
    required=set(UncertaintyPolicy.__dataclass_fields__)
    if type(settings) is not dict or set(settings)!=required:
        _fail('invalid_uncertainty_policy','Bootstrap policy incomplete')
    try:policy=UncertaintyPolicy(**settings)
    except (TypeError,KeyError) as exc:raise PIEBoundaryError('invalid_uncertainty_policy','Bad resampling policy') from exc
    rebuilt=build_candidate_uncertainty_intervals(snapshot,strata,outcome,controls,matrix,coverage,discovery,effects,policy)
    if rebuilt.as_dict()!=data:
        _fail('uncertainty_replay_mismatch','Resealed/bootstrap rows differ from source')
    return rebuilt
