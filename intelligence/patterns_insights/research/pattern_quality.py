"""PIE-MOD-24: source-pinned quality rubric with fail-closed release gates.

A descriptive pattern is never a publishable validated hypothesis. Explicit
quality dimensions replace arbitrary weighted numeric scores. "supported"
is deliberately unreachable while qualified UAE, independent holdout,
calibrated inference, authenticated registration, rights and review are absent.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
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
from .technical_numeric import (TechnicalNumericPolicy, CandidateTechnicalNumericReport)
from .robustness import (RobustnessPolicy, CandidateRobustnessReport,
                         replay_candidate_robustness_report)

SCHEMA='pie-pattern-quality-gates-candidate/v1'
_SHA=re.compile(r'[0-9a-f]{64}\Z')
_VERSION=re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_MAX_BYTES=16*1024*1024
_RUBRIC=(
    'sample_support', 'effect_magnitude_and_direction',
    'calibrated_uncertainty', 'multiple_testing',
    'holdout_confirmation', 'creator_and_fold_stability',
    'missingness', 'confounding_and_c7_growth',
    'source_identity_and_rights', 'preregistration',
    'reviewer_authorization',
)
_OUTCOMES=('exploratory','supported','rejected','needs_review')
_FRAGILE={
    'fragile_leave_creator_out_or_outlier_flip',
    'fragile_alternate_exposure_definition',
    'fragile_negative_control_high_exceedance',
    'fragile_discovery_cv_fold_direction',
}
_BLOCKED={
    'blocked_upstream_numeric_or_missing',
    'blocked_insufficient_support_or_zero_baseline',
    'blocked_matched_pair_crosses_discovery_cv_folds',
    'blocked_insufficient_discovery_cv_fold_pairs',
}
_STABLE='directionally_robust_descriptive_only'


def _fail(code:str,detail:str)->None:
    raise PIEBoundaryError(code,detail)


def _timestamp(value:object)->datetime:
    if type(value) is not str or not 10<=len(value)<=40:
        _fail('invalid_quality_policy','Timezone-aware claimed registration required')
    try:
        stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as exc:
        raise PIEBoundaryError('invalid_quality_policy','Malformed registration timestamp') from exc
    if stamp.utcoffset() is None:
        _fail('invalid_quality_policy','Registration timestamp requires timezone')
    return stamp.astimezone(timezone.utc)


@dataclass(frozen=True,slots=True)
class PatternQualityPolicy:
    policy_version:str
    algorithm_version:str
    design_sha256:str
    snapshot_sha256:str
    controls_sha256:str
    matrix_sha256:str
    holdout_sha256:str
    technical_report_sha256:str
    robustness_report_sha256:str
    registered_at:str
    registration_claim_id:str
    review_protocol_claim_id:str
    rubric_axes:tuple[str,...]=_RUBRIC
    release_gate:str='deny_until_authenticated_live_confirmatory_evidence'
    weighting:str='none_no_undocumented_numeric_thresholds'
    factor_family:str='entire_frozen_numeric_family'
    outcome_scope:str='stage22_raw_c7_discovery_association'
    evidence_authentication:str='candidate_only_unverified'
    decision_taxonomy:tuple[str,...]=_OUTCOMES

    def __post_init__(self)->None:
        for name in ('policy_version','algorithm_version','registration_claim_id',
                     'review_protocol_claim_id'):
            v=getattr(self,name)
            if type(v) is not str or not _VERSION.fullmatch(v):
                _fail('invalid_quality_policy','Explicit version and review/registration claims required')
        for name in ('design_sha256','snapshot_sha256','controls_sha256','matrix_sha256',
                     'holdout_sha256','technical_report_sha256','robustness_report_sha256'):
            v=getattr(self,name)
            if type(v) is not str or not _SHA.fullmatch(v):
                _fail('invalid_quality_policy','Complete frozen source digest pins required')
        _timestamp(self.registered_at)
        if (type(self.rubric_axes) is not tuple or self.rubric_axes!=_RUBRIC
            or self.release_gate!='deny_until_authenticated_live_confirmatory_evidence'
            or self.weighting!='none_no_undocumented_numeric_thresholds'
            or self.factor_family!='entire_frozen_numeric_family'
            or self.outcome_scope!='stage22_raw_c7_discovery_association'
            or self.evidence_authentication!='candidate_only_unverified'
            or self.decision_taxonomy!=_OUTCOMES):
            _fail('invalid_quality_policy','Rubric taxonomy cannot grant unverified promotion')

    @property
    def policy_sha256(self)->str:
        return canonical_hash(asdict(self))


@dataclass(frozen=True,slots=True)
class CandidatePatternQualityReport:
    artifact_sha256:str
    project_id:str
    policy_sha256:str
    report_status:str
    _json:str=field(repr=False)

    @property
    def verified_live(self)->bool:return False

    @property
    def ready_for_analysis(self)->bool:return False

    def to_json(self)->str:return self._json

    def as_dict(self)->dict:return json.loads(self._json)


def _axis(name:str,state:str,detail:str,evidence:object=None)->dict:
    if state not in ('descriptive_pass','failed','blocked','unverified'):
        _fail('invalid_quality_axis','Unregistered rubric axis status')
    return {'axis':name,'state':state,'reason_code':detail,'evidence':evidence}


def _grade_factor(numeric_factor:dict,robust_factor:dict,
                  n_pairs:int,quality:PatternQualityPolicy)->dict:
    """Pure, fully auditable deterministic gate based on source reports only."""
    if (type(numeric_factor) is not dict or type(robust_factor) is not dict
        or type(n_pairs) is not int or n_pairs<=0
        or numeric_factor.get('factor_id')!=robust_factor.get('factor_id')
        or numeric_factor.get('sample_pair_count')!=n_pairs):
        _fail('quality_factor_mismatch','Exact 1:1 complete factor/source sample required')
    factor_id=numeric_factor['factor_id']
    bins=numeric_factor.get('bins')
    if type(bins) is not list or not bins:
        _fail('quality_factor_mismatch','Original stage22 bins required')
    numeric_status=numeric_factor.get('status')
    robust_status=robust_factor.get('status')
    if (numeric_status not in ('candidate_descriptive_only','blocked_incomplete_or_sparse_bin')
        or robust_status not in (_FRAGILE|_BLOCKED|{_STABLE})):
        _fail('quality_factor_mismatch','Unrecognized source decision cannot be promoted')
    if type(robust_factor.get('may_promote_as_universal_pattern')) is not bool or robust_factor['may_promote_as_universal_pattern']:
        _fail('quality_publication_forgery','Stage23 may never authorize universal patterns')
    if (numeric_factor.get('confirmed_effect') is not False
        or numeric_factor.get('p_value') is not None
        or numeric_factor.get('adjusted_q_value') is not None
        or numeric_factor.get('uncertainty_interval') is not None):
        _fail('quality_inference_forgery','Stage22 cannot fabricate inferential evidence')
    details=robust_factor.get('sensitivity')
    if robust_status==_STABLE or robust_status in _FRAGILE or (
        robust_status in _BLOCKED and robust_status!='blocked_upstream_numeric_or_missing'):
        if type(details) is not dict:
            _fail('quality_factor_mismatch','Traceable sensitivity log required')
    if robust_status=='blocked_upstream_numeric_or_missing' and details is not None:
        _fail('quality_factor_mismatch','Blocked upstream cannot have computed sensitivity')
    if numeric_status!='candidate_descriptive_only' and robust_status!='blocked_upstream_numeric_or_missing':
        _fail('quality_factor_mismatch','Stage23 cannot override stage22 blocked numeric factors')
    supported=all(type(x) is dict and x.get('supported') is True for x in bins)
    missing=numeric_factor.get('missing_observations')
    if type(missing) is not list:
        _fail('quality_factor_mismatch','Exact missing-observation audit required')
    if (numeric_status=='candidate_descriptive_only'
        and (not supported or missing or
             numeric_factor.get('matched_mean_raw_high_minus_low') is None)):
        _fail('quality_factor_mismatch','Numeric sample support inconsistent with stage22')
    if robust_status==_STABLE and numeric_status!='candidate_descriptive_only':
        _fail('quality_factor_mismatch','Blocked numeric factor cannot become stable')
    if robust_status in _FRAGILE:
        result='rejected'
        reason='predeclared_robustness_failure'
    elif robust_status in _BLOCKED or numeric_status!='candidate_descriptive_only':
        result='needs_review'
        reason='insufficient_or_incomplete_evidence'
    else:
        result='exploratory'
        reason='consistent_descriptive_discovery_no_authenticated_confirmation'
    if result not in _OUTCOMES or result=='supported':
        _fail('quality_unsupported_transition','Unsupported result cannot be created')
    axes=[
        _axis('sample_support',
              'descriptive_pass' if numeric_status=='candidate_descriptive_only' else 'blocked',
              'stage22_original_registered_bins_and_minima',
              {'matched_discovery_pairs':n_pairs,'original_bin_count':len(bins),
               'supported_bin_count':numeric_factor.get('supported_bin_count'),
               'all_original_bins_supported':supported}),
        _axis('effect_magnitude_and_direction',
              'descriptive_pass' if numeric_factor.get('matched_mean_raw_high_minus_low') is not None else 'blocked',
              'exact_raw_association_not_causal_effect',
              {'matched_raw_difference':numeric_factor.get('matched_mean_raw_high_minus_low'),
               'scaled_difference':numeric_factor.get('matched_mean_scaled_high_minus_low'),
               'monotonicity':numeric_factor.get('monotonicity'),
               'minimum_effect_threshold':'none_no_post_selection'}),
        _axis('calibrated_uncertainty','blocked','no_calibrated_cluster_aware_interval',
              {'stage22_uncertainty_interval':None}),
        _axis('multiple_testing','blocked','no_valid_real_family_p_values',
              {'adjusted_q_value':None,'registered_family_only':True}),
        _axis('holdout_confirmation','blocked','confirmation_reserved_not_examined',
              {'independent_replication':False}),
        _axis('creator_and_fold_stability',
              'failed' if robust_status in _FRAGILE else
              'descriptive_pass' if robust_status==_STABLE else 'blocked',
              robust_status,{'source_robustness_status':robust_status,
                             'creator_count':details.get('creator_count') if details else None,
                             'discovery_fold_status':(
                                details.get('discovery_cv_fold_audit',{}).get('status')
                                if details else None)}),
        _axis('missingness','descriptive_pass' if not missing and supported else 'blocked',
              'strict_no_null_imputation',
              {'missing_discovery_observations':len(missing),
               'all_registered_bins_supported':supported}),
        _axis('confounding_and_c7_growth','unverified','raw_c7_growth_and_confounder_decomposition_missing',
              {'causal_claim':False,'creator_cluster_inference':False}),
        _axis('source_identity_and_rights','unverified','live_uae_qualified_rights_and_b5_not_authenticated'),
        _axis('preregistration','unverified','claimed_design_frozen_but_registration_not_authenticated'),
        _axis('reviewer_authorization','unverified','review_protocol_claim_not_human_approval'),
    ]
    if tuple(x['axis'] for x in axes)!=quality.rubric_axes:
        _fail('quality_rubric_mismatch','Whole immutable rubric required')
    return {
        'factor_id':factor_id,
        'numeric_source_status':numeric_status,
        'robustness_source_status':robust_status,
        'decision':result,
        'transition':{'from':'unassessed','to':result,'reason_code':reason},
        'quality_dimensions':axes,
        'quality_score':None,
        'eligible_for_supported':False,
        'eligible_for_release':False,
        'recommended_action':('retain_exploratory_with_caveats' if result=='exploratory'
                              else 'exclude_as_fragile' if result=='rejected'
                              else 'request_missing_evidence_and_review'),
    }


def _compose(snapshot:CandidateResearchSnapshot,strata:CandidateStrata,
             outcome:CandidateOutcomePlan,controls:CandidateMatchedControls,
             matrix:CandidateFeatureMatrix,split:CandidateHoldoutSplit,
             holdout_policy:HoldoutPolicy,lineage:tuple[HoldoutMediaLineage,...],
             numeric_policy:TechnicalNumericPolicy,
             numeric_report:CandidateTechnicalNumericReport,
             robustness_policy:RobustnessPolicy,
             robustness_report:CandidateRobustnessReport,
             policy:PatternQualityPolicy)->dict:
    if not (type(snapshot) is CandidateResearchSnapshot and type(strata) is CandidateStrata
            and type(outcome) is CandidateOutcomePlan and type(controls) is CandidateMatchedControls
            and type(matrix) is CandidateFeatureMatrix and type(split) is CandidateHoldoutSplit
            and type(holdout_policy) is HoldoutPolicy
            and type(numeric_policy) is TechnicalNumericPolicy
            and type(numeric_report) is CandidateTechnicalNumericReport
            and type(robustness_policy) is RobustnessPolicy
            and type(robustness_report) is CandidateRobustnessReport
            and type(policy) is PatternQualityPolicy):
        _fail('invalid_quality_input','Exact stage07-23 typed study and gate policy required')
    rebuilt=replay_candidate_robustness_report(
        robustness_report.to_json(),robustness_report.artifact_sha256,
        snapshot,strata,outcome,controls,matrix,split,holdout_policy,
        lineage,numeric_policy,numeric_report)
    if rebuilt!=robustness_report:
        _fail('quality_source_replay_mismatch','Stage23 source wrapper altered')
    technical=numeric_report.as_dict()
    robust=robustness_report.as_dict()
    source=snapshot.as_dict()
    if (policy.design_sha256!=source['design']['design_sha256']
        or policy.design_sha256!=numeric_policy.design_sha256
        or policy.design_sha256!=robustness_policy.design_sha256
        or policy.snapshot_sha256!=snapshot.snapshot_sha256
        or policy.controls_sha256!=controls.artifact_sha256
        or policy.matrix_sha256!=matrix.artifact_sha256
        or policy.holdout_sha256!=split.artifact_sha256
        or policy.technical_report_sha256!=numeric_report.artifact_sha256
        or policy.robustness_report_sha256!=robustness_report.artifact_sha256
        or technical.get('approved_live') is not False
        or robust.get('approved_live') is not False
        or numeric_report.policy_sha256!=numeric_policy.policy_sha256
        or robustness_report.policy_sha256!=robustness_policy.policy_sha256
        or robustness_report.project_id!=snapshot.project_id
        or numeric_report.project_id!=snapshot.project_id
        or split.project_id!=snapshot.project_id
        or technical.get('source_holdout_sha256')!=split.artifact_sha256
        or robust.get('source_technical_numeric_sha256')!=numeric_report.artifact_sha256
        or robust.get('source_holdout_sha256')!=split.artifact_sha256):
        _fail('quality_lineage_mismatch','Stage22/23 or study policy SHA mismatch')
    if _timestamp(policy.registered_at)>_timestamp(holdout_policy.discovery_before):
        _fail('quality_post_selection','Gates cannot claim preregistration after discovery')
    if (robust.get('confirmed_discoveries') is not None
        or technical.get('confirmed_discoveries') is not None
        or robust.get('can_publish_significance') is not False
        or technical.get('can_publish_significance') is not False
        or robust.get('independent_out_of_sample_replication') is not False
        or technical.get('heldout_confirmation_used') is not False
        or robust.get('valid_inferential_p_value_count')!=0
        or technical.get('valid_inferential_p_value_count')!=0
        or robust.get('c7_raw_growth_dominance')!='unknown_requires_source_metric_decomposition'):
        _fail('quality_inference_forgery','No external confirmation or calibrated inference can be synthesized')
    n=technical.get('discovery_pair_count')
    if type(n) is not int or n<=0 or n!=robust.get('discovery_pair_count'):
        _fail('quality_population_mismatch','Stage22 and Stage23 original matched sample differs')
    factors=technical.get('factors')
    audits=robust.get('factor_sensitivities')
    ids=tuple(x.factor_id for x in numeric_policy.factors)
    if (type(factors) is not list or type(audits) is not list
        or len(factors)!=len(ids) or len(audits)!=len(ids)
        or len(ids)!=technical.get('registered_factor_count')
        or len(ids)!=robust.get('registered_factor_count')
        or tuple(x['factor_id'] for x in factors)!=ids
        or tuple(x['factor_id'] for x in audits)!=ids
        or ids!=tuple(x.factor_id for x in robustness_policy.factors)):
        _fail('quality_family_mismatch','All original hypotheses must stay in source order')
    decisions=[_grade_factor(numeric,check,n,policy) for numeric,check in zip(factors,audits)]
    totals={name:sum(x['decision']==name for x in decisions) for name in _OUTCOMES}
    if totals['supported']!=0 or sum(totals.values())!=len(ids):
        _fail('quality_unsupported_transition','Cannot issue supported without trusted sources')
    report_status=('all_registered_factors_exploratory_only'
                   if totals['exploratory']==len(decisions)
                   else 'review_or_rejection_required')
    return {
        'schema_version':SCHEMA,'evidence_state':'unverified','approved_live':False,
        'project_id':snapshot.project_id,
        'source_snapshot_sha256':snapshot.snapshot_sha256,
        'source_controls_sha256':controls.artifact_sha256,
        'source_matrix_sha256':matrix.artifact_sha256,
        'source_holdout_sha256':split.artifact_sha256,
        'source_technical_numeric_sha256':numeric_report.artifact_sha256,
        'source_robustness_sha256':robustness_report.artifact_sha256,
        'policy':asdict(policy),'policy_sha256':policy.policy_sha256,
        'registered_factor_count':len(ids),
        'decision_taxonomy':list(_OUTCOMES),'rubric_axes':list(_RUBRIC),
        'decisions':decisions,'decision_counts':totals,
        'report_status':report_status,
        'supported_state_reachable_with_current_source':False,
        'authentic_reviewer_approval':False,
        'source_rights_authenticated':False,
        'independent_holdout_confirmed':False,
        'calibrated_multiple_testing_p_values_available':False,
        'can_release_to_registry_as_supported':False,
        'can_publish_patterns':False,
        'causal_claim':False,
        'confidence_intervals':None,
        'warnings':['no_arbitrary_weights_or_quality_thresholds',
                    'source_uae_rights_and_b5_not_authenticated',
                    'registration_and_reviewer_claims_not_authorization',
                    'pattern_direction_not_causal',
                    'candidate_cross_validation_is_not_independent_replication',
                    'synthetic_bh_fixture_not_valid_real_multiple_testing',
                    'no_independent_holdout_confirmatory_read',
                    'no_publishable_supported_state_without_trusted_evidence']
    }


def _freeze(payload:dict)->CandidatePatternQualityReport:
    raw=json.dumps(payload,ensure_ascii=False,sort_keys=True,
                   separators=(',',':'),allow_nan=False)
    if len(raw.encode('utf-8'))>_MAX_BYTES:
        _fail('payload_too_large','Pattern quality report exceeds byte cap')
    return CandidatePatternQualityReport(sha256(raw.encode('utf-8')).hexdigest(),
                                         payload['project_id'],payload['policy_sha256'],
                                         payload['report_status'],raw)


def build_candidate_pattern_quality_report(
        snapshot:CandidateResearchSnapshot,strata:CandidateStrata,
        outcome:CandidateOutcomePlan,controls:CandidateMatchedControls,
        matrix:CandidateFeatureMatrix,split:CandidateHoldoutSplit,
        holdout_policy:HoldoutPolicy,lineage:tuple[HoldoutMediaLineage,...],
        numeric_policy:TechnicalNumericPolicy,
        numeric_report:CandidateTechnicalNumericReport,
        robustness_policy:RobustnessPolicy,
        robustness_report:CandidateRobustnessReport,
        policy:PatternQualityPolicy)->CandidatePatternQualityReport:
    return _freeze(_compose(snapshot,strata,outcome,controls,matrix,split,
                            holdout_policy,lineage,numeric_policy,numeric_report,
                            robustness_policy,robustness_report,policy))


def replay_candidate_pattern_quality_report(raw:str|bytes,expected_sha256:str,
        snapshot:CandidateResearchSnapshot,strata:CandidateStrata,
        outcome:CandidateOutcomePlan,controls:CandidateMatchedControls,
        matrix:CandidateFeatureMatrix,split:CandidateHoldoutSplit,
        holdout_policy:HoldoutPolicy,lineage:tuple[HoldoutMediaLineage,...],
        numeric_policy:TechnicalNumericPolicy,
        numeric_report:CandidateTechnicalNumericReport,
        robustness_policy:RobustnessPolicy,
        robustness_report:CandidateRobustnessReport)->CandidatePatternQualityReport:
    if type(raw) not in (str,bytes) or type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _fail('invalid_quality_artifact','Expected canonical bytes and SHA')
    try:
        encoded=raw if type(raw) is bytes else raw.encode('utf-8')
    except UnicodeError as exc:
        raise PIEBoundaryError('invalid_quality_artifact','Invalid UTF-8') from exc
    if len(encoded)>_MAX_BYTES:
        _fail('payload_too_large','Oversized quality report')
    def unique(items):
        result={}
        for k,v in items:
            if k in result:_fail('duplicate_json_key','Duplicate JSON key')
            result[k]=v
        return result
    try:
        value=json.loads(encoded,object_pairs_hook=unique,
                         parse_constant=lambda _:_fail('invalid_quality_artifact','Nonfinite JSON'))
    except PIEBoundaryError:raise
    except (TypeError,UnicodeError,ValueError,RecursionError) as exc:
        raise PIEBoundaryError('invalid_quality_artifact','Malformed canonical report') from exc
    if type(value) is not dict or canonical_hash(value)!=expected_sha256:
        _fail('quality_hash_mismatch','Report digest mismatch')
    if value.get('schema_version')!=SCHEMA or value.get('approved_live') is not False:
        _fail('unsupported_quality_artifact','Cannot promote candidate to live')
    spec=value.get('policy')
    if type(spec) is not dict or set(spec)!=set(PatternQualityPolicy.__dataclass_fields__):
        _fail('invalid_quality_policy','Complete policy needed for independent replay')
    try:
        policy=PatternQualityPolicy(**{**spec,
            'rubric_axes':tuple(spec['rubric_axes']) if type(spec['rubric_axes']) is list else spec['rubric_axes'],
            'decision_taxonomy':tuple(spec['decision_taxonomy'])
            if type(spec['decision_taxonomy']) is list else spec['decision_taxonomy']})
    except PIEBoundaryError: raise
    except (TypeError,ValueError,KeyError) as exc:
        raise PIEBoundaryError('invalid_quality_policy','Malformed quality policy') from exc
    rebuilt=build_candidate_pattern_quality_report(
        snapshot,strata,outcome,controls,matrix,split,holdout_policy,lineage,
        numeric_policy,numeric_report,robustness_policy,robustness_report,policy)
    if (rebuilt.artifact_sha256!=expected_sha256
        or rebuilt.to_json().encode('utf-8')!=encoded or rebuilt.as_dict()!=value):
        _fail('quality_replay_mismatch','Rehashed modified data or source/policy mismatch')
    return rebuilt
