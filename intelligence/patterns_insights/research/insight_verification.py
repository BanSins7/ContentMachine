"""PIE-MOD-31: independent candidate Insight numerical/source/claim verification.

Never a public read endpoint: a 'checked' verdict is only eligible for future
independent review, NOT source approval, causal proof or release. Original UAE
rights and exact revocation are re-evaluated through stage26/27 on each call.
"""
from __future__ import annotations

from dataclasses import dataclass,field
from fractions import Fraction
from hashlib import sha256
from datetime import datetime
import json
import re

from ..contracts import PIEBoundaryError,canonical_hash
from ..intake.provenance import SourceEligibilityGate
from .pattern_queries import RegistryQuery,CurrentProjectAuthorizer,FreshUAEReader,CursorSigner
from .insight_semantics import CandidateInsightPrompt,prepare_candidate_insight_prompt,_require_sealed_prompt
from .insight_generation import CandidateInsightGeneration,build_candidate_insight_generation
from .pattern_invalidation import require_noninvalidated_pattern_for_insight

SCHEMA='pie-candidate-insight-verification/v1'
_SHA=re.compile(r'[0-9a-f]{64}\Z')
_MAX_BYTES=12000
_ROBUSTNESS=frozenset({
    'directionally_robust_descriptive_only',
    'consistent_discovery_cv_directions_descriptive_only',
    'fragile_leave_creator_out_or_outlier_flip',
    'fragile_alternate_exposure_definition',
    'fragile_negative_control_high_exceedance',
    'fragile_discovery_cv_fold_direction',
    'blocked_insufficient_support_or_zero_baseline',
    'blocked_matched_pair_crosses_discovery_cv_folds',
    'blocked_insufficient_discovery_cv_fold_pairs',
    'blocked_upstream_numeric_or_missing',
})
_REASONS={
    'exact_source_consistent',
    'numeric_or_scope_contradiction',
    'fabricated_or_modified_draft',
    'missing_counterevidence_audit',
}


def _fail(code:str,reason:str)->None:
    raise PIEBoundaryError(code,reason)


def _canonical(d:dict)->str:
    return json.dumps(d,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


@dataclass(frozen=True,slots=True)
class CandidateInsightVerification:
    verification_sha256:str
    project_id:str
    generation_sha256:str
    source_series_id:str
    source_revision:int
    verdict:str
    reason_code:str
    counterevidence_status:str
    _json:str=field(repr=False)
    @property
    def may_public_read(self)->bool:return False
    @property
    def may_publish(self)->bool:return False
    def as_dict(self)->dict:return json.loads(self._json)
    def to_json(self)->str:return self._json


@dataclass(frozen=True,slots=True)
class CandidateInsightVerificationReceipt:
    project_id:str
    generation_sha256:str
    verification_sha256:str
    verdict:str
    reason_code:str
    idempotent:bool
    may_public_read:bool=False
    may_publish:bool=False
    independent_counterevidence_confirmed:bool=False


def _source_truth(payload:dict,prompt:CandidateInsightPrompt,
                  source_sha:str,db_hypothesis:str,owner_id:str)->tuple[bool,bool]:
    """Cross-check ALL rendered numbers/scope/units with original registry JSON.

    Returns (consistent, counterevidence_metadata_present). No raw rows, videos
    or source URLs enter the response or its persisted report.
    """
    if (type(payload) is not dict or type(source_sha) is not str
        or not _SHA.fullmatch(source_sha) or canonical_hash(payload)!=source_sha):
        return False,False
    stats=payload.get('statistical_summary')
    hyp=payload.get('hypothesis_specification')
    facts=prompt.source_facts
    if (type(stats) is not dict or type(hyp) is not dict
        or payload.get('project_id')!=prompt.project_id
        or payload.get('series_id')!=prompt.source_ref.split(':')[2]
        or payload.get('hypothesis_sha256')!=db_hypothesis
        or payload.get('owner_id')!=owner_id
        or payload.get('analysis_revision_status')!=facts['status']
        or payload.get('sample_scope')!='discovery_only'
        or payload.get('factor_id')!=facts['factor_id']
        or hyp.get('feature_family')!=facts['feature_family']
        or hyp.get('feature_name')!=facts['feature_name']
        or hyp.get('metric_kind')!=facts['metric_kind']
        or hyp.get('source_unit')!=facts['raw_source_unit']
        or type(payload.get('matched_pair_count')) is not int
        or payload['matched_pair_count']!=facts['discovery_matched_pairs']
        or type(payload.get('source_discovery_pairs')) is not list
        or len(payload['source_discovery_pairs'])!=facts['discovery_matched_pairs']
        or stats.get('monotonicity')!=facts['descriptive_direction']
        or type(stats.get('bin_coverage')) is not list):
        return False,False
    bins=stats['bin_coverage']
    if any(type(x) is not dict or type(x.get('supported')) is not bool for x in bins):
        return False,False
    if sum(x['supported'] for x in bins)!=facts['supported_registered_bins']:
        return False,False
    raw=stats.get('raw_mean_high_minus_low')
    if raw is None:
        if facts['exact_raw_mean_high_minus_low'] is not None:return False,False
    else:
        if (type(raw) is not dict
            or type(raw.get('numerator')) is not int
            or type(raw.get('denominator')) is not int
            or not 1<=raw['denominator']<=1000000):
            return False,False
        exact=Fraction(raw['numerator'],raw['denominator'])
        encoded=f'{exact.numerator}/{exact.denominator}'
        if facts['exact_raw_mean_high_minus_low']!=encoded:return False,False
    if (stats.get('calibrated_p_value') is not None
        or stats.get('adjusted_q_value') is not None
        or stats.get('confidence_interval') is not None
        or payload.get('approved_live') is not False
        or payload.get('may_publish') is not False
        or payload.get('independent_holdout_confirmed') is not False
        or payload.get('rights_and_review_authenticated') is not False):
        return False,False
    quality=payload.get('source_quality_dimensions')
    robustness=stats.get('stage23_robustness_status')
    missing=stats.get('missing_observations')
    # A recorded robustness assessment is counter-evidence *metadata*, not
    # independent replication. No unknown outcome can be called "supported".
    counter_meta=(type(quality) is dict and bool(quality)
                  and type(robustness) is str and robustness in _ROBUSTNESS
                  and type(missing) is list)
    return True,counter_meta


def _frozen_generation(stored:dict,prompt:CandidateInsightPrompt,
                       generation_sha:str)->bool:
    if type(stored) is not dict or stored.get('schema_version')!='pie-candidate-insight-generation/v1':
        return False
    mode=stored.get('generation_mode')
    if mode not in ('local_deterministic','provider_reported'):
        return False
    attempt=stored.get('model_attempt_id') if mode=='provider_reported' else None
    response=stored.get('model_response_sha256') if mode=='provider_reported' else None
    try:
        expected=build_candidate_insight_generation(
            prompt,_canonical(prompt.expected_codes),
            model_attempt_id=attempt,model_response_sha256=response)
    except (PIEBoundaryError,TypeError,ValueError,UnicodeError):
        return False
    return (expected.artifact_sha256==generation_sha
            and stored==expected.as_dict()
            and expected.to_json()==_canonical(stored))


def _provider_response_current(connection,project_id:str,stored:dict)->bool:
    if stored.get('generation_mode')=='local_deterministic':
        return (stored.get('model_attempt_id') is None
                and stored.get('model_response_sha256') is None)
    attempt=stored.get('model_attempt_id')
    fingerprint=stored.get('model_response_sha256')
    if type(attempt) is not str or type(fingerprint) is not str or not _SHA.fullmatch(fingerprint):
        return False
    try:
        row=connection.execute(
            """SELECT o.outcome,o.response_sha256,c.prompt_sha256
                 FROM pie_model_dispatch_outcomes o
                 JOIN pie_model_dispatch_claims c
                   ON c.project_id=o.project_id AND c.attempt_id=o.attempt_id
                WHERE o.project_id=%s AND o.attempt_id=%s""",
            (project_id,attempt)).fetchone()
    except Exception:
        return False
    return (row is not None and tuple(row)==
            ('reported_charged',fingerprint,stored.get('source_prompt_sha256')))


def _audit(project_id:str,generation_sha:str,series_id:str,revision:int,
           hypothesis:str,registry_sha:str,prompt_sha:str,
           reason:str)->CandidateInsightVerification:
    if reason not in _REASONS:
        _fail('insight_verification_invalid','Unregistered verifier outcome')
    consistent=reason=='exact_source_consistent'
    verdict='candidate_checked_for_review' if consistent else 'quarantined'
    evidence='independent_not_measured' if consistent else 'unknown_quarantined'
    payload={
        'schema_version':SCHEMA,'project_id':project_id,
        'generation_sha256':generation_sha,
        'source_series_id':series_id,'source_revision':revision,
        'source_hypothesis_sha256':hypothesis,
        'source_registry_payload_sha256':registry_sha,
        'source_prompt_sha256':prompt_sha,
        'verdict':verdict,'reason_code':reason,
        'counterevidence_status':evidence,
        'numerical_registry_reconciliation':consistent,
        'model_facts_rederived_from_source':consistent,
        'independent_counterevidence_confirmed':False,
        'rights_permanent':False,
        'human_review_confirmed':False,
        'causal_claim':False,'may_public_read':False,'may_publish':False,
    }
    raw=_canonical(payload)
    if len(raw.encode('utf-8'))>_MAX_BYTES:
        _fail('insight_verification_invalid','Oversized safe audit')
    return CandidateInsightVerification(
        sha256(raw.encode('utf-8')).hexdigest(),project_id,generation_sha,
        series_id,revision,verdict,reason,evidence,raw)


def verify_candidate_insight(
        connection,query:RegistryQuery,series_id:str,generation_sha256:str,
        authorizer:CurrentProjectAuthorizer,reader:FreshUAEReader,
        gate:SourceEligibilityGate,signer:CursorSigner,
        *,now:datetime|None=None)->tuple[CandidateInsightVerification,
                                         CandidateInsightVerificationReceipt]:
    """No public result: only a safe verdict and immutable audit under lock.

    The original raw generation text is NEVER returned to an unverified client,
    even on success. Unauthorized/expired sources fail closed BEFORE reading it.
    """
    if (type(query) is not RegistryQuery or type(series_id) is not str
        or not _SHA.fullmatch(series_id)
        or type(generation_sha256) is not str or not _SHA.fullmatch(generation_sha256)):
        _fail('insight_verification_invalid','Scoped source and exact SHA required')
    try:
        with connection.transaction():
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                ('pie-registry-project:'+query.project_id,))
            prompt=prepare_candidate_insight_prompt(
                connection,query,series_id,authorizer,reader,gate,signer,now=now)
            _require_sealed_prompt(prompt)
            require_noninvalidated_pattern_for_insight(
                connection,query.project_id,series_id,prompt.source_revision)
            # Both latest revision and raw evidence are scope-checked by owner.
            registry=connection.execute(
                """SELECT r.revision,r.hypothesis_sha256,r.payload_sha256,r.payload
                     FROM pie_pattern_registry_revisions r
                     JOIN pie_pattern_registry_series s
                       ON s.project_id=r.project_id AND s.series_id=r.series_id
                    WHERE r.project_id=%s AND r.series_id=%s AND s.owner_id=%s
                    ORDER BY r.revision DESC LIMIT 1""",
                (query.project_id,series_id,query.principal_id)).fetchone()
            if (registry is None or len(registry)!=4
                or type(registry[0]) is not int
                or registry[0]!=prompt.source_revision
                or registry[1]!=prompt.evidence_ids[1].split(':')[-1]
                or type(registry[2]) is not str or not _SHA.fullmatch(registry[2])):
                _fail('insight_verification_source_changed','Latest research source unavailable')
            generation=connection.execute(
                """SELECT artifact,generation_sha256,source_revision,
                          hypothesis_sha256,source_prompt_sha256
                     FROM pie_candidate_insight_generations
                    WHERE project_id=%s AND generation_sha256=%s
                      AND series_id=%s""",
                (query.project_id,generation_sha256,series_id)).fetchone()
            if generation is None or len(generation)!=5:
                _fail('insight_verification_not_found','No authorized source generation')
            stored,db_digest,db_rev,db_hyp,db_prompt=generation
            if (db_digest!=generation_sha256 or db_rev!=prompt.source_revision
                or db_hyp!=registry[1] or db_prompt!=prompt.prompt_sha256):
                _fail('insight_verification_source_changed','Generation belongs to old revision')
            consistent,counter_meta=_source_truth(
                registry[3],prompt,registry[2],registry[1],query.principal_id)
            if not consistent:
                reason='numeric_or_scope_contradiction'
            elif not _frozen_generation(stored,prompt,generation_sha256):
                reason='fabricated_or_modified_draft'
            elif not _provider_response_current(connection,query.project_id,stored):
                reason='fabricated_or_modified_draft'
            elif not counter_meta:
                reason='missing_counterevidence_audit'
            else:
                reason='exact_source_consistent'
            audit=_audit(query.project_id,generation_sha256,series_id,
                         prompt.source_revision,registry[1],registry[2],
                         prompt.prompt_sha256,reason)
            from psycopg.types.json import Jsonb
            inserted=connection.execute(
                """INSERT INTO pie_candidate_insight_verification_events
                   (project_id,verification_sha256,generation_sha256,
                    source_series_id,source_revision,source_hypothesis_sha256,
                    source_registry_payload_sha256,source_prompt_sha256,
                    verdict,reason_code,counterevidence_status,audit)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(project_id,verification_sha256) DO NOTHING
                   RETURNING verification_sha256""",
                (audit.project_id,audit.verification_sha256,audit.generation_sha256,
                 series_id,prompt.source_revision,registry[1],registry[2],
                 prompt.prompt_sha256,audit.verdict,audit.reason_code,
                 audit.counterevidence_status,Jsonb(audit.as_dict()))).fetchone()
            existing=connection.execute(
                """SELECT audit,verdict,reason_code,source_registry_payload_sha256
                     FROM pie_candidate_insight_verification_events
                    WHERE project_id=%s AND verification_sha256=%s""",
                (query.project_id,audit.verification_sha256)).fetchone()
            if (existing is None or len(existing)!=4
                or existing[0]!=audit.as_dict()
                or existing[1]!=audit.verdict or existing[2]!=audit.reason_code
                or existing[3]!=registry[2]):
                _fail('insight_verification_conflict','Immutable audit re-use conflict')
            return audit,CandidateInsightVerificationReceipt(
                audit.project_id,audit.generation_sha256,audit.verification_sha256,
                audit.verdict,audit.reason_code,inserted is None)
    except PIEBoundaryError:raise
    except Exception as exc:
        raise PIEBoundaryError('insight_verification_unavailable',
                               'Cannot independently verify current evidence') from exc
