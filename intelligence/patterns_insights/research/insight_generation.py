"""PIE-MOD-30: supervised Insight generation from fresh source evidence only.

The default path is LOCAL, deterministic and free. A paid-result path accepts
ONLY an existing stage29 reported-charged attempt plus its exact response bytes
and independently verifies database provenance. Neither path publishes,
claims supported significance, calls a model, or repeats paid requests.
"""
from __future__ import annotations

from dataclasses import dataclass,field
from datetime import datetime,timezone
from decimal import Decimal
from hashlib import sha256
import json
import re
import uuid

from ..contracts import PIEBoundaryError,canonical_hash
from ..intake.provenance import SourceEligibilityGate
from .insight_semantics import (
    CandidateInsightPrompt, CandidateInsightDraft, _require_sealed_prompt,
    prepare_candidate_insight_prompt,compile_candidate_insight_draft,
    replay_candidate_insight_draft,
)
from .pattern_queries import (
    RegistryQuery,CurrentProjectAuthorizer,FreshUAEReader,CursorSigner,
)
from .pattern_invalidation import require_noninvalidated_pattern_for_insight
from .model_governance import ModelDispatchReceipt,CandidateModelQuote

SCHEMA='pie-candidate-insight-generation/v1'
_SHA=re.compile(r'[0-9a-f]{64}\Z')
_REF=re.compile(r'pie:pattern:([0-9a-f]{64}):revision:([1-9][0-9]{0,9})\Z')
_HYP=re.compile(r'pie:hypothesis:([0-9a-f]{64})\Z')
_MAX_BYTES=16000


def _deny(code:str,detail:str)->None:
    raise PIEBoundaryError(code,detail)


def _json(value:dict)->str:
    return json.dumps(value,sort_keys=True,separators=(',',':'),
                      ensure_ascii=False,allow_nan=False)

def _is_uuid(value:str)->bool:
    try:
        return str(uuid.UUID(value))==value.lower()
    except (ValueError,AttributeError,TypeError):
        return False



@dataclass(frozen=True,slots=True)
class CandidateInsightGeneration:
    artifact_sha256:str
    project_id:str
    source_series_id:str
    source_revision:int
    source_hypothesis_sha256:str
    generation_mode:str
    candidate_draft_sha256:str
    source_prompt_sha256:str
    source_facts_sha256:str
    evidence_ids:tuple[str,...]
    model_attempt_id:str|None
    model_response_sha256:str|None
    _json:str=field(repr=False)

    @property
    def approved_live(self)->bool:return False

    @property
    def can_publish(self)->bool:return False

    @property
    def ready_for_public_read(self)->bool:return False

    def to_json(self)->str:return self._json

    def as_dict(self)->dict:return json.loads(self._json)


@dataclass(frozen=True,slots=True)
class InsightGenerationReceipt:
    project_id:str
    artifact_sha256:str
    source_series_id:str
    source_revision:int
    generation_mode:str
    idempotent:bool
    publication_allowed:bool=False
    independently_verified_scientific_claim:bool=False


def _lineage(prompt:CandidateInsightPrompt)->tuple[str,int,str]:
    _require_sealed_prompt(prompt)
    m=_REF.fullmatch(prompt.source_ref)
    hyp=_HYP.fullmatch(prompt.evidence_ids[1])
    if m is None or hyp is None or m[0]!=prompt.evidence_ids[0]:
        _deny('insight_generation_source_invalid','Exact pattern/revision/hypothesis required')
    series_id=m[1]
    revision=int(m[2])
    if prompt.source_revision!=revision:
        _deny('insight_generation_source_invalid','Source revision changed')
    return series_id,revision,hyp[1]


def _verified_paid_provenance(connection,project_id:str,
                              prompt:CandidateInsightPrompt,
                              quote:CandidateModelQuote,
                              receipt:ModelDispatchReceipt,
                              model_json:str|bytes)->tuple[str,str]:
    if (type(quote) is not CandidateModelQuote
        or type(receipt) is not ModelDispatchReceipt
        or receipt.project_id!=project_id or quote.project_id!=project_id
        or receipt.disposition!='reported_charged'
        or receipt.actual_billing_independently_verified is not False
        or quote.prompt_sha256!=prompt.prompt_sha256
        or quote.provider_origin!='openrouter_catalog_claim_not_attested'
        or quote.approved_live is not False
        or type(receipt.reported_cost_usd) is not str):
        _deny('insight_generation_provider_invalid','Only exact reported candidate output accepted')
    try:
        encoded=model_json.encode('utf-8') if type(model_json) is str else model_json
    except (UnicodeError,AttributeError) as exc:
        raise PIEBoundaryError('insight_generation_provider_invalid',
                               'Expected bounded original JSON bytes') from exc
    if type(encoded) is not bytes or len(encoded)>4096:
        _deny('insight_generation_provider_invalid','Original model response missing/oversized')
    response_sha=sha256(encoded).hexdigest()
    if receipt.response_sha256!=response_sha:
        _deny('insight_generation_provider_invalid','Model response digest mismatch')
    # Secure database cross-check against the immutable pre-send claim, outcome
    # and project/authorization budget. A Python receipt by itself is untrusted.
    sql="""SELECT o.outcome,o.response_sha256,o.reported_cost_usd::text,
                  c.prompt_sha256,c.quote_sha256,c.request_sha256,
                  c.model_slug,a.reserved_usd::text,
                  c.catalog_sha256,o.provider_generation_id
             FROM pie_model_dispatch_outcomes o
             JOIN pie_model_dispatch_claims c
               ON c.project_id=o.project_id AND c.attempt_id=o.attempt_id
             JOIN pie_model_attempts a
               ON a.project_id=c.project_id AND a.id=c.attempt_id
            WHERE o.project_id=%s AND o.attempt_id=%s"""
    try:
        row=connection.execute(sql,(project_id,receipt.attempt_id)).fetchone()
    except Exception as exc:
        raise PIEBoundaryError('insight_generation_provider_unavailable',
                               'No authenticated financial outcome ledger') from exc
    if (row is None or len(row)!=10
        or row[0]!='reported_charged' or row[1]!=response_sha
        or row[3]!=prompt.prompt_sha256
        or row[4]!=quote.quote_sha256
        or row[5]!=quote.request_sha256
        or row[6]!=quote.model_slug
        or row[8]!=quote.catalog_sha256
        or row[9]!=receipt.provider_generation_id
        or Decimal(row[2])!=Decimal(receipt.reported_cost_usd)
        or Decimal(row[2])>Decimal(row[7])):
        _deny('insight_generation_provider_invalid','Actual receipt not bound to source or reservation')
    return receipt.attempt_id,response_sha


def build_candidate_insight_generation(
        prompt:CandidateInsightPrompt,model_json:str|bytes,
        *,model_attempt_id:str|None=None,
        model_response_sha256:str|None=None)->CandidateInsightGeneration:
    """Immutable canonical artifact; accepts only exact stage28 enum response."""
    series_id,revision,hypothesis=_lineage(prompt)
    if (model_attempt_id is None)!=(model_response_sha256 is None):
        _deny('insight_generation_provider_invalid','Provider receipt is all-or-none')
    if model_attempt_id is not None:
        if (type(model_attempt_id) is not str
            or type(model_response_sha256) is not str
            or not _SHA.fullmatch(model_response_sha256)
            or (not _is_uuid(model_attempt_id))):
            _deny('insight_generation_provider_invalid','Expected provider attempt and response hash')
        mode='provider_reported'
    else:
        mode='local_deterministic'
        # The local path must contain *only* exactly declared codes, with no
        # provider-written narrative, fake citations, money, or remote context.
        expected=_json(prompt.expected_codes)
        if (type(model_json) not in (str,bytes)
            or (model_json.decode('utf-8') if type(model_json) is bytes else model_json)!=expected):
            _deny('insight_generation_local_invalid','Local deterministic selection only')
    draft=compile_candidate_insight_draft(prompt,model_json)
    result={
        'schema_version':SCHEMA,'project_id':prompt.project_id,
        'source_series_id':series_id,'source_revision':revision,
        'source_hypothesis_sha256':hypothesis,
        'source_prompt_sha256':prompt.prompt_sha256,
        'source_facts_sha256':prompt.source_facts_sha256,
        'candidate_draft_sha256':draft.artifact_sha256,
        'evidence_ids':list(prompt.evidence_ids),
        'generation_mode':mode,
        'model_attempt_id':model_attempt_id,
        'model_response_sha256':model_response_sha256,
        'draft':draft.as_dict(),
        'review_state':'unverified_candidate',
        'statistical_confidence':'uncalibrated_descriptive_candidate',
        'sample_scope':'registered_discovery_only',
        'recommended_followup':'independent_preregistered_rights_checked_test_only',
        'independent_holdout_confirmed':False,
        'approved_live':False,'can_publish':False,'causal_claim':False,
        'verified_rights_at_generation_only':True,
        'rights_for_future_read_guaranteed':False,
        'cost_settled_independently':False,
    }
    raw=_json(result)
    if len(raw.encode('utf-8'))>_MAX_BYTES:
        _deny('insight_generation_too_large','Bounded candidate artifact required')
    digest=sha256(raw.encode('utf-8')).hexdigest()
    return CandidateInsightGeneration(
        digest,prompt.project_id,series_id,revision,hypothesis,
        mode,draft.artifact_sha256,prompt.prompt_sha256,
        prompt.source_facts_sha256,tuple(prompt.evidence_ids),
        model_attempt_id,model_response_sha256,raw,
    )


def replay_candidate_insight_generation(
        encoded:str|bytes,expected_sha256:str,
        prompt:CandidateInsightPrompt,model_json:str|bytes,
        *,model_attempt_id:str|None=None,
        model_response_sha256:str|None=None)->CandidateInsightGeneration:
    if type(expected_sha256) is not str or not _SHA.fullmatch(expected_sha256):
        _deny('insight_generation_replay_invalid','Expected canonical SHA256')
    expected=build_candidate_insight_generation(
        prompt,model_json,model_attempt_id=model_attempt_id,
        model_response_sha256=model_response_sha256)
    try:
        raw=encoded.encode('utf-8') if type(encoded) is str else encoded
    except (UnicodeError,AttributeError) as exc:
        raise PIEBoundaryError('insight_generation_replay_invalid','Invalid original bytes') from exc
    if (type(raw) is not bytes or len(raw)>_MAX_BYTES
        or raw!=expected.to_json().encode('utf-8')
        or sha256(raw).hexdigest()!=expected_sha256
        or expected.artifact_sha256!=expected_sha256):
        _deny('insight_generation_replay_mismatch','Source or canonical output was altered')
    return expected


def generate_candidate_insight(
        connection,query:RegistryQuery,series_id:str,
        authorizer:CurrentProjectAuthorizer,reader:FreshUAEReader,
        gate:SourceEligibilityGate,signer:CursorSigner,
        *,model_quote:CandidateModelQuote|None=None,
        dispatch_receipt:ModelDispatchReceipt|None=None,
        model_response_json:str|bytes|None=None,
        now:datetime|None=None)->tuple[CandidateInsightGeneration,InsightGenerationReceipt]:
    """One project-locked current-evidence write; no network or money spent.

    Default deterministic path is free. Optional paid-result assembly requires
    exact immutable stage29 outcome recorded in DB plus byte-identical JSON.
    No current source or tombstoned revision => no artifact.
    """
    if (type(query) is not RegistryQuery or type(series_id) is not str
        or not _SHA.fullmatch(series_id)
        or not ((model_quote is None and dispatch_receipt is None and model_response_json is None)
                or (model_quote is not None and dispatch_receipt is not None
                    and model_response_json is not None))):
        _deny('insight_generation_request_invalid','Exact project/series and all-or-none paid evidence')
    try:
        with connection.transaction():
            # Block concurrent source tombstones while obtaining fresh rights,
            # checking latest revision and writing the immutable candidate.
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                               ('pie-registry-project:'+query.project_id,))
            prompt=prepare_candidate_insight_prompt(
                connection,query,series_id,authorizer,reader,gate,signer,now=now)
            series,rev,hyp=_lineage(prompt)
            # Fresh source page alone is not enough if a new registry revision
            # appears or the requester supplied a stale signed cursor.
            actual=connection.execute(
                """SELECT r.revision,r.hypothesis_sha256
                   FROM pie_pattern_registry_revisions r
                   JOIN pie_pattern_registry_series s
                     ON s.project_id=r.project_id AND s.series_id=r.series_id
                  WHERE r.project_id=%s AND r.series_id=%s AND s.owner_id=%s
                  ORDER BY r.revision DESC LIMIT 1""",
                (query.project_id,series,query.principal_id)).fetchone()
            if actual is None or tuple(actual)!=(rev,hyp):
                _deny('insight_generation_stale_source','Current latest source revision required')
            require_noninvalidated_pattern_for_insight(connection,query.project_id,series,rev)
            attempt=None;response_sha=None
            if model_quote is not None:
                attempt,response_sha=_verified_paid_provenance(
                    connection,query.project_id,prompt,model_quote,
                    dispatch_receipt,model_response_json)
                chosen=model_response_json
            else:
                chosen=_json(prompt.expected_codes)
            artifact=build_candidate_insight_generation(
                prompt,chosen,model_attempt_id=attempt,
                model_response_sha256=response_sha)
            from psycopg.types.json import Jsonb
            row=connection.execute(
                """INSERT INTO pie_candidate_insight_generations
                   (project_id,generation_sha256,series_id,source_revision,
                    hypothesis_sha256,source_prompt_sha256,source_facts_sha256,
                    candidate_draft_sha256,evidence_ids,generation_mode,
                    model_attempt_id,model_response_sha256,artifact)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT(project_id,generation_sha256) DO NOTHING
                   RETURNING generation_sha256""",
                (artifact.project_id,artifact.artifact_sha256,series,rev,hyp,
                 artifact.source_prompt_sha256,artifact.source_facts_sha256,
                 artifact.candidate_draft_sha256,Jsonb(list(artifact.evidence_ids)),
                 artifact.generation_mode,attempt,response_sha,
                 Jsonb(artifact.as_dict()))).fetchone()
            stored=connection.execute(
                """SELECT artifact,candidate_draft_sha256,source_revision,generation_mode
                   FROM pie_candidate_insight_generations
                  WHERE project_id=%s AND generation_sha256=%s""",
                (query.project_id,artifact.artifact_sha256)).fetchone()
            if (stored is None or stored[0]!=artifact.as_dict()
                or stored[1]!=artifact.candidate_draft_sha256
                or stored[2]!=rev or stored[3]!=artifact.generation_mode):
                _deny('insight_generation_conflict','Immutable same-generation identity changed')
            return artifact,InsightGenerationReceipt(
                query.project_id,artifact.artifact_sha256,series,rev,
                artifact.generation_mode,row is None)
    except PIEBoundaryError:raise
    except Exception as exc:
        # No raw SQL/provider data or source identities in errors.
        raise PIEBoundaryError('insight_generation_store_unavailable',
                               'Current candidate generation failed closed') from exc
