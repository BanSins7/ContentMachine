"""PIE-MOD-21: B3 semantic role n-grams, discovery matched pairs only.

An ordered B3 role list has NO intrinsic timestamp, slide or technical Group A
scene identity. Optional external role coordinate claims are row/SHA-bound but
remain UNVERIFIED, and cross-media comparisons fail closed.
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

SCHEMA='pie-b3-sequence-motifs-candidate/v1'
_SHA=re.compile(r'[0-9a-f]{64}\Z')
_ID=re.compile(r'[a-z][a-z0-9_]{0,79}\Z')
_VERSION=re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_MAX_BYTES=16*1024*1024
_MAX_MOTIFS=32
_MAX_SEQUENCE=64
_MAX_PAIRS=1000


def _fail(code: str, reason: str) -> None:
    raise PIEBoundaryError(code, reason)


def _time(value: object) -> datetime:
    if type(value) is not str or not 10 <= len(value) <= 40:
        _fail('invalid_story_registration','Timezone-aware registration required')
    try:
        dt=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as exc:
        raise PIEBoundaryError('invalid_story_registration','Malformed timestamp') from exc
    if dt.utcoffset() is None:
        _fail('invalid_story_registration','Timezone offset required')
    return dt.astimezone(timezone.utc)


def _valid_role(s: object) -> bool:
    return type(s) is str and 1 <= len(s) <= 120 and all(ord(c)>=32 for c in s)


def _fraction(value: Fraction | None) -> dict | None:
    if value is None:
        return None
    with localcontext() as ctx:
        ctx.prec=80
        ctx.rounding=ROUND_HALF_EVEN
        display=(Decimal(value.numerator)/Decimal(value.denominator)).quantize(
            Decimal('0.000000000001'))
    return {'numerator':value.numerator,'denominator':value.denominator,
            'decimal_12dp':format(display,'f')}


def count_story_ngram(sequence: tuple[str,...], motif: tuple[str,...]) -> int:
    """Count CONTIGUOUS semantic-role windows, preserving order and repeats."""
    if (type(sequence) is not tuple or type(motif) is not tuple
        or len(sequence)>_MAX_SEQUENCE or not 2<=len(motif)<=5
        or any(not _valid_role(x) for x in (*sequence,*motif))):
        _fail('invalid_story_sequence','Exact bounded semantic role tuples required')
    return sum(sequence[i:i+len(motif)]==motif
               for i in range(len(sequence)-len(motif)+1))


@dataclass(frozen=True, slots=True)
class StoryMotif:
    motif_id: str
    roles: tuple[str,...]
    operation: str='contiguous_semantic_ngram'

    def __post_init__(self) -> None:
        if type(self.motif_id) is not str or not _ID.fullmatch(self.motif_id):
            _fail('invalid_story_motif','Explicit stable motif ID required')
        if (type(self.roles) is not tuple or not 2<=len(self.roles)<=5
            or any(not _valid_role(x) for x in self.roles)
            or self.operation!='contiguous_semantic_ngram'):
            _fail('invalid_story_motif','Only fixed 2-5 ordered B3 roles permitted')


@dataclass(frozen=True, slots=True)
class StoryMotifPolicy:
    policy_version: str
    algorithm_version: str
    design_sha256: str
    snapshot_sha256: str
    controls_sha256: str
    matrix_sha256: str
    holdout_sha256: str
    registered_at: str
    registration_claim_id: str
    b3_feature_name: str
    minimum_discovery_pairs: int
    minimum_hit_posts_per_role: int
    motifs: tuple[StoryMotif,...]
    alignment_mode: str='sequence_only_no_geometry'
    source_feature_family: str='b'
    sequence_representation: str='ordered_sequence'
    sample_scope: str='discovery_only'
    missingness: str='block_if_any_matched_b3_missing'
    inference_status: str='no_p_values_or_confirmed_patterns'

    def __post_init__(self) -> None:
        for key in ('policy_version','algorithm_version','registration_claim_id'):
            v=getattr(self,key)
            if type(v) is not str or not _VERSION.fullmatch(v):
                _fail('invalid_story_policy','Explicit version and claimed registration ID required')
        for key in ('design_sha256','snapshot_sha256','controls_sha256','matrix_sha256','holdout_sha256'):
            v=getattr(self,key)
            if type(v) is not str or not _SHA.fullmatch(v):
                _fail('invalid_story_policy','Pinned source SHA required')
        _time(self.registered_at)
        if type(self.b3_feature_name) is not str or not _ID.fullmatch(self.b3_feature_name):
            _fail('invalid_story_policy','Named observed B3 feature is required')
        for key in ('minimum_discovery_pairs','minimum_hit_posts_per_role'):
            v=getattr(self,key)
            if type(v) is not int or not 1<=v<=_MAX_PAIRS:
                _fail('invalid_story_policy','Explicit positive sample support required')
        if (type(self.motifs) is not tuple or not 1<=len(self.motifs)<=_MAX_MOTIFS
            or any(type(x) is not StoryMotif for x in self.motifs)):
            _fail('invalid_story_policy','Bounded immutable motif family required')
        ids=tuple(x.motif_id for x in self.motifs)
        if ids!=tuple(sorted(set(ids))):
            _fail('invalid_story_policy','Motif IDs must be sorted and unique')
        if len({m.roles for m in self.motifs})!=len(self.motifs):
            _fail('invalid_story_policy','Duplicate semantic role predicate forbidden')
        if (self.alignment_mode not in ('sequence_only_no_geometry',
                                         'full_unverified_b3_role_coordinates')
            or self.source_feature_family!='b'
            or self.sequence_representation!='ordered_sequence'
            or self.sample_scope!='discovery_only'
            or self.missingness!='block_if_any_matched_b3_missing'
            or self.inference_status!='no_p_values_or_confirmed_patterns'):
            _fail('invalid_story_policy','Unsupported source, alignment, inference or partial-sample mode')

    @property
    def policy_sha256(self)->str:
        return canonical_hash(asdict(self))

    @property
    def family_sha256(self)->str:
        return canonical_hash({'motifs':[asdict(m) for m in self.motifs]})


@dataclass(frozen=True, slots=True)
class StoryRoleCoordinates:
    """External, unverified role-level annotation, NOT parsed from Group A cuts."""
    video_id: int
    row_sha256: str
    b3_analysis_sha256: str
    coordinate_kind: str
    positions: tuple[int,...]
    provenance: str='unverified_external_b3_role_coordinates'

    def __post_init__(self) -> None:
        if type(self.video_id) is not int or not 0<self.video_id<2**63:
            _fail('invalid_story_coordinates','Exact video ID required')
        if any(type(getattr(self,x)) is not str or not _SHA.fullmatch(getattr(self,x))
               for x in ('row_sha256','b3_analysis_sha256')):
            _fail('invalid_story_coordinates','Exact row and B3 analysis digest required')
        if self.coordinate_kind not in ('video_timestamp_ms','carousel_slide_number'):
            _fail('invalid_story_coordinates','Only B3 video times or carousel slide numbers allowed')
        if (type(self.positions) is not tuple or len(self.positions)>_MAX_SEQUENCE
            or any(type(p) is not int or not 0<=p<2**53 for p in self.positions)
            or any(a>=b for a,b in zip(self.positions,self.positions[1:]))):
            _fail('invalid_story_coordinates','Coordinates must be bounded strictly increasing integers')
        if self.coordinate_kind=='carousel_slide_number' and any(p<1 for p in self.positions):
            _fail('invalid_story_coordinates','Carousel slides are one-indexed')
        if self.provenance!='unverified_external_b3_role_coordinates':
            _fail('invalid_story_coordinates','Cannot claim authenticated timeline identity')


@dataclass(frozen=True, slots=True)
class CandidateStoryMotifReport:
    artifact_sha256: str
    project_id: str
    policy_sha256: str
    motif_family_sha256: str
    report_status: str
    _json: str=field(repr=False)

    @property
    def verified_live(self)->bool: return False

    @property
    def ready_for_analysis(self)->bool: return False

    def to_json(self)->str: return self._json

    def as_dict(self)->dict: return json.loads(self._json)


def _compose(snapshot:CandidateResearchSnapshot, strata:CandidateStrata,
             outcome:CandidateOutcomePlan, controls:CandidateMatchedControls,
             matrix:CandidateFeatureMatrix, split:CandidateHoldoutSplit,
             holdout_policy:HoldoutPolicy, lineage:tuple[HoldoutMediaLineage,...],
             policy:StoryMotifPolicy,
             coordinates:tuple[StoryRoleCoordinates,...]) -> dict:
    if not (type(snapshot) is CandidateResearchSnapshot and type(strata) is CandidateStrata
            and type(outcome) is CandidateOutcomePlan and type(controls) is CandidateMatchedControls
            and type(matrix) is CandidateFeatureMatrix and type(split) is CandidateHoldoutSplit
            and type(holdout_policy) is HoldoutPolicy and type(policy) is StoryMotifPolicy):
        _fail('invalid_story_input','Exact frozen typed source and policy required')
    # Rebuild upstream from authoritative pinned inputs; wrapper/sha alone is inadequate.
    c=replay_candidate_controls(controls.to_json(),controls.artifact_sha256,
                                snapshot,strata,outcome)
    m=replay_candidate_feature_matrix(matrix.to_json(),matrix.artifact_sha256,
                                      snapshot,strata,outcome,controls)
    s=replay_candidate_holdout_split(split.to_json(),split.artifact_sha256,
                                     snapshot,holdout_policy,lineage)
    if c!=controls or m!=matrix or s!=split:
        _fail('story_source_replay_mismatch','Typed source wrapper cannot replay')
    source=snapshot.as_dict()
    mat=matrix.as_dict()
    raw_controls=controls.as_dict()
    if (snapshot.project_id!=controls.project_id or snapshot.project_id!=matrix.project_id
        or snapshot.project_id!=split.project_id
        or policy.snapshot_sha256!=snapshot.snapshot_sha256
        or policy.design_sha256!=source['design']['design_sha256']
        or policy.design_sha256!=holdout_policy.design_sha256
        or policy.controls_sha256!=controls.artifact_sha256
        or policy.matrix_sha256!=matrix.artifact_sha256
        or policy.holdout_sha256!=split.artifact_sha256
        or mat.get('source_controls_sha256')!=controls.artifact_sha256
        or raw_controls.get('source_snapshot_sha256')!=snapshot.snapshot_sha256):
        _fail('story_lineage_mismatch','Frozen project/design/lineage mismatch')
    if _time(policy.registered_at)>_time(holdout_policy.discovery_before):
        _fail('story_post_selection','Motif family registration must precede discovery cutoff')
    require_candidate_holdout_scope(split,arm='discovery',
                                    video_ids=split.discovery_video_ids)
    lookup=[i for i,x in enumerate(mat['columns']) if x['family']=='b'
            and x['name']==policy.b3_feature_name and x['representation']=='ordered_sequence']
    if len(lookup)!=1:
        _fail('story_b3_feature_unavailable','Require exact observed semantic B3 ordered-sequence column')
    ix=lookup[0]
    members={r['video_id']:r for r in source['selection']['included']}
    rows={r['video_id']:r for r in mat['rows']}
    if (len(members)!=len(source['selection']['included'])
        or len(rows)!=len(mat['rows'])):
        _fail('story_population_mismatch','Duplicate source row')
    selected=[]
    reserved=[]
    discovery=set(split.discovery_video_ids)
    confirmation=set(split.confirmation_video_ids)
    for pair in raw_controls['pairs']:
        hi,lo=pair['case_video_id'],pair['control_video_id']
        if (hi in discovery)!=(lo in discovery):
            _fail('story_cross_arm_pair','Matched pair crosses discovery boundary')
        if hi in discovery:
            selected.append((hi,lo))
        else:
            reserved.append({'case_video_id':hi,'control_video_id':lo,
                'reason_code':'confirmation_reserved' if hi in confirmation and lo in confirmation
                              else 'embargo_or_quarantine'})
    selected_ids={i for pair in selected for i in pair}
    if (not policy.minimum_discovery_pairs<=len(selected)<=_MAX_PAIRS
        or len(raw_controls['pairs'])!=len(selected)+len(reserved)
        or len(selected_ids)!=2*len(selected) or not selected_ids<=discovery):
        _fail('story_insufficient_pairs','Require unique eligible bounded discovery pairs')
    if type(coordinates) is not tuple or any(type(x) is not StoryRoleCoordinates for x in coordinates):
        _fail('invalid_story_coordinates','Typed immutable coordinate records required')
    if policy.alignment_mode=='sequence_only_no_geometry':
        if coordinates:
            _fail('story_geometry_policy_mismatch','No secret positional claims allowed')
        coord_map={}
    else:
        if (len(coordinates)!=len(selected_ids)
            or tuple(x.video_id for x in coordinates)!=tuple(sorted(selected_ids))):
            _fail('story_geometry_population_mismatch','Complete, ordered, unique matched role coordinates required')
        coord_map={x.video_id:x for x in coordinates}
    # Match B3 analysis lineage exactly; not Group A's technical scene geometry.
    sequences={}
    missing=[]
    media_kind={}
    for hi,lo in selected:
        for vid,role in ((hi,'high'),(lo,'low')):
            member=members.get(vid)
            row=rows.get(vid)
            if (member is None or row is None or row['role']!=role
                or row['row_sha256']!=member['row_sha256']
                or len(row['features'])!=len(mat['columns'])):
                _fail('story_row_mismatch','Matched row ID/role/feature alignment mismatch')
            cell=row['features'][ix]
            if (cell['family']!='b' or cell['name']!=policy.b3_feature_name
                or cell['representation']!='ordered_sequence'
                or cell.get('analysis_sha256')!=member['row'].get('group_b_sha256')):
                _fail('story_b3_lineage_mismatch','Only source-bound semantic Group B3 role lists allowed')
            obs=cell['observation']
            if obs['state']!='present':
                missing.append({'video_id':vid,'role':role,'reason_code':'b3_absent_or_null'})
                continue
            roles=obs['value']
            if (type(roles) is not list or len(roles)>_MAX_SEQUENCE
                or any(not _valid_role(x) for x in roles)):
                _fail('story_invalid_sequence','Bounded exact semantic role list required')
            sequences[vid]=tuple(roles)
            if coord_map:
                claim=coord_map[vid]
                if (claim.row_sha256!=member['row_sha256']
                    or claim.b3_analysis_sha256!=cell['analysis_sha256']
                    or len(claim.positions)!=len(roles)):
                    _fail('story_coordinate_lineage_mismatch','Coordinate binding/role count changed')
                media_kind[vid]=claim.coordinate_kind

    different_media_pairs=[]
    media_kinds=set(media_kind.values())
    if coord_map and len(sequences)==len(selected_ids):
        for hi,lo in selected:
            if media_kind[hi]!=media_kind[lo]:
                different_media_pairs.append({'case_video_id':hi,'control_video_id':lo,
                                               'reason_code':'video_vs_carousel_incomparable'})
    unknown_time=(policy.alignment_mode=='sequence_only_no_geometry')
    blocked_media=bool(different_media_pairs or len(media_kinds)>1)
    baseline_blocked=bool(missing or blocked_media)
    motifs=[]
    for motif in policy.motifs:
        counts={'high':{'observed_posts':0,'hit_posts':0,'total_occurrences':0},
                'low':{'observed_posts':0,'hit_posts':0,'total_occurrences':0}}
        pairwise={'both_hit':0,'high_only':0,'low_only':0,'neither_hit':0}
        for hi,lo in selected:
            if hi not in sequences or lo not in sequences:
                continue
            ha=count_story_ngram(sequences[hi],motif.roles)
            la=count_story_ngram(sequences[lo],motif.roles)
            for role,cnt in (('high',ha),('low',la)):
                counts[role]['observed_posts']+=1
                counts[role]['hit_posts']+=int(cnt>0)
                counts[role]['total_occurrences']+=cnt
            if ha and la: pairwise['both_hit']+=1
            elif ha: pairwise['high_only']+=1
            elif la: pairwise['low_only']+=1
            else: pairwise['neither_hit']+=1
        supported=(not baseline_blocked
                   and counts['high']['hit_posts']>=policy.minimum_hit_posts_per_role
                   and counts['low']['hit_posts']>=policy.minimum_hit_posts_per_role)
        n=len(selected)
        difference=(Fraction(pairwise['high_only']-pairwise['low_only'],n)
                    if supported else None)
        results={
            'motif_id':motif.motif_id,'roles':list(motif.roles),
            'source_family':'b','source_representation':'ordered_sequence',
            'high':counts['high'],'low':counts['low'],
            'paired_discordance':pairwise,
            'status':'candidate_descriptive_motif_only' if supported else
                     'blocked_missing_sparse_or_incomparable_geometry',
            'matched_post_prevalence_difference':_fraction(difference),
            'p_value':None,'adjusted_q_value':None,
            'confidence_interval':None,'confirmed':False,
        }
        motifs.append(results)
    good=all(m['status']=='candidate_descriptive_motif_only' for m in motifs)
    return {
        'schema_version':SCHEMA,'evidence_state':'unverified','approved_live':False,
        'project_id':snapshot.project_id,
        'source_snapshot_sha256':snapshot.snapshot_sha256,
        'source_controls_sha256':controls.artifact_sha256,
        'source_matrix_sha256':matrix.artifact_sha256,
        'source_holdout_sha256':split.artifact_sha256,
        'policy':asdict(policy),'policy_sha256':policy.policy_sha256,
        'registered_motif_family_sha256':policy.family_sha256,
        'registered_motif_count':len(policy.motifs),
        'report_status':'candidate_descriptive_motifs_only' if good
                        else 'blocked_incomplete_registered_motif_family',
        'discovery_pair_count':len(selected),
        'unmatched_discovery_video_ids':sorted(discovery-selected_ids),
        'reserved_pairs':reserved,
        'missing_b3_roles':missing,
        'b3_role_coordinate_claim_sha256':(canonical_hash({'coordinates':[asdict(c) for c in coordinates]})
                                           if coord_map else None),
        'geometry_status':('not_available_no_temporal_or_slide_claim' if unknown_time
                           else 'unverified_external_coordinates_no_authentication'),
        'coordinate_kinds':sorted(media_kinds),
        'cross_media_matched_pairs':different_media_pairs,
        'mixed_media_pooled_comparison_blocked':blocked_media,
        'b3_role_order_preserved':True,'b3_role_repetitions_preserved':True,
        'group_a_technical_scene_geometry_used':False,
        'motifs':motifs,
        'inferential_hypotheses_tested':0,'multiplicity_status':'blocked_no_valid_p_values',
        'confirmed_discoveries':None,'can_publish_significance':False,'causal_claim':False,
        'warnings':['b3_semantic_roles_not_group_a_scenes',
                    'b3_order_does_not_imply_seconds_or_carousel_slide_positions',
                    'external_role_coordinates_unverified',
                    'registered_before_discovery_claim_not_authenticated',
                    'no_production_rights_or_reviewer',
                    'no_independent_p_values_or_multiple_testing_confidence',
                    'not_confirmed_by_holdout','not_a_causal_effect']
    }


def _freeze(data:dict)->CandidateStoryMotifReport:
    raw=json.dumps(data,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)
    if len(raw.encode('utf-8'))>_MAX_BYTES:
        _fail('payload_too_large','Story motif report exceeds limit')
    return CandidateStoryMotifReport(sha256(raw.encode('utf-8')).hexdigest(),
        data['project_id'],data['policy_sha256'],
        data['registered_motif_family_sha256'],data['report_status'],raw)


def build_candidate_story_motifs(
        snapshot:CandidateResearchSnapshot,strata:CandidateStrata,
        outcome:CandidateOutcomePlan,controls:CandidateMatchedControls,
        matrix:CandidateFeatureMatrix,split:CandidateHoldoutSplit,
        holdout_policy:HoldoutPolicy,lineage:tuple[HoldoutMediaLineage,...],
        policy:StoryMotifPolicy,
        coordinates:tuple[StoryRoleCoordinates,...]=())->CandidateStoryMotifReport:
    return _freeze(_compose(snapshot,strata,outcome,controls,matrix,split,
                            holdout_policy,lineage,policy,coordinates))


def replay_candidate_story_motifs(raw:str|bytes,expected_sha256:str,
        snapshot:CandidateResearchSnapshot,strata:CandidateStrata,
        outcome:CandidateOutcomePlan,controls:CandidateMatchedControls,
        matrix:CandidateFeatureMatrix,split:CandidateHoldoutSplit,
        holdout_policy:HoldoutPolicy,lineage:tuple[HoldoutMediaLineage,...],
        coordinates:tuple[StoryRoleCoordinates,...]=())->CandidateStoryMotifReport:
    if type(raw) not in (str,bytes) or type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_story_artifact','Canonical JSON and valid digest required')
    try:
        encoded=raw if type(raw) is bytes else raw.encode('utf-8')
    except UnicodeError as exc:
        raise PIEBoundaryError('invalid_story_artifact','Invalid UTF-8') from exc
    if len(encoded)>_MAX_BYTES:
        _fail('payload_too_large','Story report exceeds limit')
    def unique(items):
        result={}
        for k,v in items:
            if k in result: _fail('duplicate_json_key','Duplicate JSON property')
            result[k]=v
        return result
    try:
        data=json.loads(encoded,object_pairs_hook=unique,
                        parse_constant=lambda _: _fail('invalid_story_artifact','Nonfinite JSON'))
    except PIEBoundaryError: raise
    except (UnicodeError,ValueError,TypeError,RecursionError) as exc:
        raise PIEBoundaryError('invalid_story_artifact','Malformed JSON') from exc
    if type(data) is not dict or canonical_hash(data)!=expected_sha256:
        _fail('story_hash_mismatch','Artifact differs from expected digest')
    if data.get('schema_version')!=SCHEMA or data.get('approved_live') is not False:
        _fail('unsupported_story_artifact','Cannot promote candidate to live')
    spec=data.get('policy')
    if type(spec) is not dict or set(spec)!=set(StoryMotifPolicy.__dataclass_fields__):
        _fail('invalid_story_policy','Complete frozen policy required')
    try:
        if type(spec['motifs']) is not list:
            _fail('invalid_story_policy','Motif family must be a list')
        motifs=tuple(StoryMotif(**{**motif,
            'roles':tuple(motif['roles']) if type(motif.get('roles')) is list else motif.get('roles')})
            for motif in spec['motifs'])
        policy=StoryMotifPolicy(**{**spec,'motifs':motifs})
    except PIEBoundaryError: raise
    except (KeyError,TypeError,ValueError) as exc:
        raise PIEBoundaryError('invalid_story_policy','Malformed motif family') from exc
    expected=build_candidate_story_motifs(snapshot,strata,outcome,controls,matrix,split,
                                          holdout_policy,lineage,policy,coordinates)
    if (expected.artifact_sha256!=expected_sha256 or expected.as_dict()!=data
        or expected.to_json().encode('utf-8')!=encoded):
        _fail('story_replay_mismatch','Rehashed or revised result does not reconstruct')
    return expected
