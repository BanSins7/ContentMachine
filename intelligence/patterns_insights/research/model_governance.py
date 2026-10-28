"""PIE-MOD-29: explicit OpenRouter catalog and single-send governed candidate LLM.

No network, imports of secrets, paid operation or model discovery at import.
A claimed approval/declared port is NEVER an authenticated approval. An
injected trusted host must authorize each reservation and dispatch. Only a
fake/test caller can override this gate; there is no connected live approval.
"""
from __future__ import annotations

from dataclasses import dataclass,asdict
from datetime import datetime,timezone,timedelta
from decimal import Decimal,InvalidOperation,ROUND_CEILING
from hashlib import sha256
from typing import Protocol
import json
import os
import re
import ssl
import urllib.parse
import urllib.request

from ..contracts import PIEBoundaryError,canonical_hash
from .insight_semantics import CandidateInsightPrompt,compile_candidate_insight_draft
from .pattern_queries import RegistryQuery,CurrentProjectAuthorizer,FreshUAEReader,CursorSigner
from ..intake.provenance import SourceEligibilityGate
from .pattern_invalidation import require_noninvalidated_pattern_for_insight
from .insight_semantics import prepare_candidate_insight_prompt

_SHA=re.compile(r'[0-9a-f]{64}\Z')
_MODEL=re.compile(r'[a-z0-9][a-z0-9._-]{0,60}/[a-z0-9][a-z0-9._:-]{0,90}\Z')
_UUID=re.compile(r'[0-9a-fA-F-]{36}\Z')
_MAX_BODY=65536
_MAX_OUTPUT=4096
_USD_QUANT=Decimal('0.000000000001')
_MAX_AGE=timedelta(minutes=2)
_API_ROOT='https://openrouter.ai/api/v1'


def _fail(code:str,msg:str)->None:
    raise PIEBoundaryError(code,msg)


def _encode(v)->str:
    return json.dumps(v,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)


def _money(value,*,zero_ok=True)->Decimal:
    if type(value) not in (str,int,Decimal):
        _fail('invalid_model_price','Only exact decimal money; never floats')
    try:d=Decimal(str(value))
    except (InvalidOperation,ValueError) as exc:
        raise PIEBoundaryError('invalid_model_price','Malformed price') from exc
    if not d.is_finite() or d<0 or (not zero_ok and d==0) or d>Decimal('1000'):
        _fail('invalid_model_price','Bounded finite nonnegative USD required')
    return d


def _time(value:str)->datetime:
    if type(value) is not str or len(value)>40:
        _fail('invalid_model_catalog','Offset-aware catalog timestamp required')
    try:out=datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError as exc:
        raise PIEBoundaryError('invalid_model_catalog','Malformed timestamp') from exc
    if out.utcoffset() is None:
        _fail('invalid_model_catalog','Naive catalog time')
    return out.astimezone(timezone.utc)


@dataclass(frozen=True,slots=True)
class OpenRouterModelSnapshot:
    model_slug:str
    context_length:int
    max_completion_tokens:int
    prompt_price_per_token:str
    completion_price_per_token:str
    request_price_usd:str
    supported_parameters:tuple[str,...]
    observed_at:str
    producer:'str'='openrouter_models_api_candidate'

    def __post_init__(self)->None:
        if (type(self.model_slug) is not str or not _MODEL.fullmatch(self.model_slug)
            or self.model_slug.startswith('openrouter/')
            or ':' in self.model_slug
            or type(self.context_length) is not int or self.context_length<4096
            or type(self.max_completion_tokens) is not int
            or not 1<=self.max_completion_tokens<=self.context_length
            or type(self.supported_parameters) is not tuple
            or not {'response_format','max_tokens'}.issubset(set(self.supported_parameters))
            or any(type(x) is not str for x in self.supported_parameters)
            or self.producer!='openrouter_models_api_candidate'):
            _fail('model_capability_unverified','Exact model slug and structured output route required')
        _money(self.prompt_price_per_token)
        _money(self.completion_price_per_token)
        _money(self.request_price_usd)
        _time(self.observed_at)

    @property
    def catalog_sha256(self)->str:
        return canonical_hash(asdict(self))


class OpenRouterTransport(Protocol):
    def fetch_model(self,slug:str)->dict:...
    def complete_once(self,request:dict,timeout_s:int)->dict:...


class OpenRouterHTTPS:
    """Single fixed HTTPS origin, no automatic retries, no redirect to other host."""
    def __init__(self,*,api_key:str|None=None):
        self._api_key=api_key

    @staticmethod
    def _call(method:str,path:str,body:dict|None,api_key:str|None,timeout_s:int)->dict:
        if not path.startswith('/') or '://' in path or type(timeout_s) is not int or not 1<=timeout_s<=60:
            _fail('invalid_model_transport','Fixed HTTPS path and bounded timeout required')
        token=api_key if api_key is not None else os.environ.get('OPENROUTER_API_KEY')
        if type(token) is not str or not token or '\n' in token or '\r' in token:
            _fail('model_credentials_unavailable','OPENROUTER_API_KEY not configured')
        raw=None if body is None else _encode(body).encode('utf-8')
        if raw is not None and len(raw)>_MAX_BODY:
            _fail('model_request_too_large','Bounded JSON request body required')
        request=urllib.request.Request(
            _API_ROOT+path,data=raw,method=method,
            headers={'Authorization':'Bearer '+token,
                     'Content-Type':'application/json','Accept':'application/json'})
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self,*args,**kwargs):
                return None
        opener=urllib.request.build_opener(
            NoRedirect,urllib.request.HTTPSHandler(context=ssl.create_default_context()))
        try:
            with opener.open(request,timeout=timeout_s) as response:
                if response.status!=200:
                    _fail('model_transport_failure','Unexpected HTTP status')
                payload=response.read(_MAX_BODY+1)
        except PIEBoundaryError:raise
        except Exception as exc:
            # HTTP status and transport exception texts can contain sensitive data.
            raise PIEBoundaryError('model_transport_uncertain','No safe charge conclusion') from exc
        if len(payload)>_MAX_BODY:
            _fail('model_transport_uncertain','Oversized response; billing unknown')
        try:decoded=json.loads(payload)
        except (ValueError,UnicodeError) as exc:
            raise PIEBoundaryError('model_transport_uncertain','Malformed provider JSON') from exc
        if type(decoded) is not dict:
            _fail('model_transport_uncertain','Unexpected provider result')
        return decoded

    def fetch_model(self,slug:str)->dict:
        if type(slug) is not str or not _MODEL.fullmatch(slug):
            _fail('invalid_model_slug','Exact author/slug required')
        return self._call('GET','/models/'+urllib.parse.quote(slug,safe='/'),
                          None,self._api_key,10)

    def complete_once(self,request:dict,timeout_s:int)->dict:
        return self._call('POST','/chat/completions',request,self._api_key,timeout_s)


def verify_model_catalog(raw:dict,slug:str,*,now:datetime)->OpenRouterModelSnapshot:
    """Parse model's live catalog response, NOT a hardcoded product/price list."""
    if type(raw) is not dict or type(raw.get('data')) is not dict:
        _fail('invalid_model_catalog','Exact provider catalog response required')
    d=raw['data']
    if d.get('id')!=slug or d.get('canonical_slug',slug)!=slug:
        _fail('model_slug_mismatch','Router alias cannot switch selected model')
    arc=d.get('architecture')
    if type(arc) is not dict or not {'text'}.issubset(set(arc.get('input_modalities',[]))) or not {'text'}.issubset(set(arc.get('output_modalities',[]))):
        _fail('model_capability_unverified','Text in/out required')
    prices=d.get('pricing')
    if type(prices) is not dict:
        _fail('invalid_model_catalog','Pricing missing')
    for extra in ('image','audio','web_search','internal_reasoning'):
        if extra in prices and _money(prices[extra])!=0:
            _fail('model_price_unbounded','Auxiliary billing not approved')
    context=d.get('context_length')
    top=d.get('top_provider')
    if type(top) is not dict:
        _fail('model_capability_unverified','Endpoint completion ceiling missing')
    params=d.get('supported_parameters')
    if type(params) is not list or any(type(x) is not str for x in params):
        _fail('model_capability_unverified','Structured JSON parameter list missing')
    if now.utcoffset() is None:
        _fail('invalid_model_catalog','Offset-aware clock expected')
    return OpenRouterModelSnapshot(
        slug,context,top.get('max_completion_tokens'),
        str(_money(prices.get('prompt'))),str(_money(prices.get('completion'))),
        str(_money(prices.get('request','0'))),tuple(sorted(set(params))),
        now.astimezone(timezone.utc).isoformat())


@dataclass(frozen=True,slots=True)
class CandidateModelQuote:
    project_id:str
    prompt_sha256:str
    model_slug:str
    catalog_sha256:str
    catalog_observed_at:str
    input_token_upper_bound:int
    max_output_tokens:int
    reserve_usd:str
    request_sha256:str
    quote_sha256:str
    provider_origin:str='openrouter_catalog_claim_not_attested'
    approved_live:bool=False


def model_quote(prompt:CandidateInsightPrompt,catalog:OpenRouterModelSnapshot,
                *,max_output_tokens:int=256,now:datetime|None=None)->CandidateModelQuote:
    if type(prompt) is not CandidateInsightPrompt or type(catalog) is not OpenRouterModelSnapshot:
        _fail('model_quote_invalid','Typed candidate prompt and verified model metadata needed')
    if prompt.ready_for_model_dispatch is not False or prompt.approved_live is not False or prompt.can_publish is not False:
        _fail('model_quote_invalid','Only non-releasable candidate insights')
    now=now or datetime.now(timezone.utc)
    if now.utcoffset() is None or now-_time(catalog.observed_at)>_MAX_AGE or _time(catalog.observed_at)>now+timedelta(seconds=5):
        _fail('model_catalog_stale','Model pricing must be freshly fetched before quote')
    if (type(max_output_tokens) is not int or not 1<=max_output_tokens<=_MAX_OUTPUT
        or max_output_tokens>catalog.max_completion_tokens):
        _fail('model_quote_invalid','Output tokens exceed declared endpoint ceiling')
    # Conservative tokenizer-independent upper bound: count UTF-8 bytes plus
    # fixed chat/JSON framing. A provider may still count differently; no
    # guaranteed token-price cap without provider max-price routing.
    input_bound=len(_encode(prompt.messages).encode('utf-8'))+len(_encode(prompt.response_schema).encode('utf-8'))+2048
    if input_bound+max_output_tokens>catalog.context_length:
        _fail('model_context_overflow','Conservative prompt bound exceeds context')
    amount=(Decimal(input_bound)*_money(catalog.prompt_price_per_token)
            +Decimal(max_output_tokens)*_money(catalog.completion_price_per_token)
            +_money(catalog.request_price_usd))
    amount=amount.quantize(_USD_QUANT,rounding=ROUND_CEILING)
    if amount<=0 or amount>Decimal('50'):
        _fail('model_quote_invalid','Quote must be nonzero bounded explicit spend')
    request_sha=canonical_hash({
        'project_id':prompt.project_id,'prompt_sha256':prompt.prompt_sha256,
        'model_slug':catalog.model_slug,'catalog_sha256':catalog.catalog_sha256,
        'max_output_tokens':max_output_tokens,
    })
    fields={
        'project_id':prompt.project_id,'prompt_sha256':prompt.prompt_sha256,
        'model_slug':catalog.model_slug,'catalog_sha256':catalog.catalog_sha256,
        'catalog_observed_at':catalog.observed_at,
        'input_token_upper_bound':input_bound,'max_output_tokens':max_output_tokens,
        'reserve_usd':str(amount),'request_sha256':request_sha,
    }
    return CandidateModelQuote(**fields,quote_sha256=canonical_hash(fields))


class SpendAuthority(Protocol):
    """Host must verify approver and effective current consent independently."""
    def require_current_spend_approval(self,project_id:str,principal_id:str,
                                       authorization_id:str,quote:CandidateModelQuote)->bool:...


@dataclass(frozen=True,slots=True)
class ModelReservation:
    project_id:str
    attempt_id:str
    authorization_id:str
    request_sha256:str
    quote_sha256:str
    reserved_usd:str


@dataclass(frozen=True,slots=True)
class ModelDispatchReceipt:
    project_id:str
    attempt_id:str
    disposition:str
    response_sha256:str|None
    reported_cost_usd:str|None
    provider_generation_id:str|None
    no_automatic_retry:bool=True
    actual_billing_independently_verified:bool=False


def _approval(authority:SpendAuthority,project:str,actor:str,approval_id:str,
              quote:CandidateModelQuote)->None:
    if authority is None or not callable(getattr(authority,'require_current_spend_approval',None)):
        _fail('spend_authority_unavailable','Trusted current approval adapter needed')
    try:
        ok=authority.require_current_spend_approval(project,actor,approval_id,quote)
    except Exception:ok=False
    if ok is not True:
        _fail('spend_authority_denied','Fresh explicit approval required')


def reserve_model_attempt(connection,quote:CandidateModelQuote,principal_id:str,
                          authorization_id:str,authority:SpendAuthority)->ModelReservation:
    """Existing migration200 DB trigger protects the hard spend reservation."""
    if type(quote) is not CandidateModelQuote or type(principal_id) is not str or not principal_id or type(authorization_id) is not str or not _UUID.fullmatch(authorization_id):
        _fail('model_reservation_invalid','Project scope/identity and UUID approval required')
    _approval(authority,quote.project_id,principal_id,authorization_id,quote)
    if not _SHA.fullmatch(quote.quote_sha256) or not _SHA.fullmatch(quote.request_sha256):
        _fail('model_reservation_invalid','Canonical quote and request required')
    # Unique project+request SHA guarantees duplicate attempts never reserve twice.
    try:
        with connection.transaction():
            r=connection.execute(
                """INSERT INTO pie_model_attempts
                   (project_id,authorization_id,request_sha256,reserved_usd)
                   VALUES (%s,%s,%s,%s) RETURNING id::text""",
                (quote.project_id,authorization_id,quote.request_sha256,
                 Decimal(quote.reserve_usd))).fetchone()
            if r is None:_fail('model_reservation_invalid','Attempt not created')
            attempt=r[0]
            event=canonical_hash({'type':'reserved','attempt_id':attempt,
                                  'quote_sha256':quote.quote_sha256})
            connection.execute(
                """INSERT INTO pie_model_attempt_events
                    (project_id,attempt_id,event_seq,event_type,amount_usd,event_sha256)
                   VALUES (%s,%s,1,'reserved',%s,%s)""",
                (quote.project_id,attempt,Decimal(quote.reserve_usd),event))
            return ModelReservation(quote.project_id,attempt,authorization_id,
                                    quote.request_sha256,quote.quote_sha256,
                                    quote.reserve_usd)
    except PIEBoundaryError:raise
    except Exception as exc:
        raise PIEBoundaryError('model_reservation_conflict',
                               'Approval expired, budget exhausted or duplicate request') from exc


def _request(prompt:CandidateInsightPrompt,quote:CandidateModelQuote)->dict:
    return {
        'model':quote.model_slug,'messages':list(prompt.messages),
        'response_format':{'type':'json_schema',
                           'json_schema':{'name':'pie_candidate_insight_selection',
                                          'strict':True,
                                          'schema':prompt.response_schema}},
        'provider':{'require_parameters':True,'allow_fallbacks':False,
                    'data_collection':'deny'},
        'max_tokens':quote.max_output_tokens,'stream':False,
        'usage':{'include':True},
    }


def dispatch_reserved_once(
        connection,reservation:ModelReservation,quote:CandidateModelQuote,
        original_prompt:CandidateInsightPrompt,query:RegistryQuery,series_id:str,
        principal_id:str,authorizer:CurrentProjectAuthorizer,
        reader:FreshUAEReader,gate:SourceEligibilityGate,signer:CursorSigner,
        spend_authority:SpendAuthority,catalog_gateway:OpenRouterTransport,
        *,timeout_s:int=20,now:datetime|None=None)->ModelDispatchReceipt:
    """Claim BEFORE sending. On any uncertainty lock attempt permanently.

    Caller has explicit live-auth collaborators; no automatic payment from tests.
    """
    if (type(reservation) is not ModelReservation or type(quote) is not CandidateModelQuote
        or type(original_prompt) is not CandidateInsightPrompt
        or type(timeout_s) is not int or not 1<=timeout_s<=60
        or quote.project_id!=reservation.project_id
        or quote.request_sha256!=reservation.request_sha256
        or quote.quote_sha256!=reservation.quote_sha256
        or original_prompt.prompt_sha256!=quote.prompt_sha256
        or principal_id!=query.principal_id or series_id not in original_prompt.source_ref):
        _fail('model_dispatch_invalid','Explicit source/quote/owner/timeout binding required')
    now=now or datetime.now(timezone.utc)
    if now.utcoffset() is None:
        _fail('model_dispatch_invalid','UTC-aware clock required')
    # Fresh metadata fetch is READ-ONLY and never charged. Any price/model drift
    # blocks dispatch; request is not paid without a re-approved new quote.
    fresh=verify_model_catalog(catalog_gateway.fetch_model(quote.model_slug),
                               quote.model_slug,now=now)
    if fresh.catalog_sha256!=quote.catalog_sha256:
        _fail('model_price_changed','Model capabilities/prices differ from approved quote')
    if now-_time(quote.catalog_observed_at)>_MAX_AGE:
        _fail('model_catalog_stale','Reapproval required for stale price')
    fresh_prompt=prepare_candidate_insight_prompt(
        connection,query,series_id,authorizer,reader,gate,signer,now=now)
    if fresh_prompt.prompt_sha256!=original_prompt.prompt_sha256:
        _fail('model_prompt_stale','Source changed: new consent and quote required')
    _approval(spend_authority,quote.project_id,principal_id,
              reservation.authorization_id,quote)
    request=_request(fresh_prompt,quote)
    req_sha=canonical_hash(request)
    try:
        with connection.transaction():
            # Serialize with current stage27 source revocations before issuing a claim.
            connection.execute("SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                               ('pie-registry-project:'+quote.project_id,))
            require_noninvalidated_pattern_for_insight(
                connection,quote.project_id,series_id,fresh_prompt.source_revision)
            record=connection.execute(
                """SELECT a.reserved_usd::text,a.authorization_id::text
                     FROM pie_model_attempts a
                    WHERE a.project_id=%s AND a.id=%s AND a.request_sha256=%s
                      AND a.authorization_id=%s""",
                (quote.project_id,reservation.attempt_id,quote.request_sha256,
                 reservation.authorization_id)).fetchone()
            if (record is None or Decimal(record[0])!=Decimal(quote.reserve_usd)
                or record[1]!=reservation.authorization_id):
                _fail('model_dispatch_invalid','Reserved attempt identity mismatch')
            connection.execute(
                """INSERT INTO pie_model_dispatch_claims
                    (project_id,attempt_id,authorization_id,request_sha256,
                     quote_sha256,prompt_sha256,catalog_sha256,request_payload_sha256,
                     model_slug,max_output_tokens)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (quote.project_id,reservation.attempt_id,reservation.authorization_id,
                 quote.request_sha256,quote.quote_sha256,quote.prompt_sha256,
                 quote.catalog_sha256,req_sha,quote.model_slug,quote.max_output_tokens))
    except PIEBoundaryError:raise
    except Exception as exc:
        raise PIEBoundaryError('model_already_claimed_no_retry',
                               'No second model call allowed for this attempt') from exc
    # From here a crash/timeout is potentially billable; NEVER resend.
    response=None
    result_sha=None
    actual=None
    ident=None
    prompt_tokens=None
    completion_tokens=None
    outcome='uncertain_charge'
    try:
        response=catalog_gateway.complete_once(request,timeout_s)
        if type(response) is not dict or response.get('model')!=quote.model_slug:
            raise ValueError('Unexpected model')
        ident=response.get('id')
        usage=response.get('usage')
        choices=response.get('choices')
        if (type(ident) is not str or not 1<=len(ident)<=160
            or type(usage) is not dict or type(choices) is not list or len(choices)!=1
            or type(choices[0]) is not dict):
            raise ValueError('Unverifiable provider usage or choice')
        msg=choices[0].get('message')
        if (type(msg) is not dict or type(msg.get('content')) is not str
            or choices[0].get('finish_reason')!='stop'):
            raise ValueError('Incomplete completion cannot confirm cost')
        prompt_tokens=usage.get('prompt_tokens')
        completion_tokens=usage.get('completion_tokens')
        if (type(prompt_tokens) is not int or type(completion_tokens) is not int
            or not 0<=prompt_tokens<=quote.input_token_upper_bound
            or not 0<=completion_tokens<=quote.max_output_tokens):
            raise ValueError('Usage exceeds approved token envelope')
        actual=_money(usage.get('cost'),zero_ok=True).quantize(
            _USD_QUANT,rounding=ROUND_CEILING)
        if actual>Decimal(quote.reserve_usd):
            raise ValueError('Cost exceeds quoted reservation')
        # Strict source-grounded model JSON parser; fail closed on hallucinations.
        compile_candidate_insight_draft(fresh_prompt,msg['content'])
        result_sha=sha256(msg['content'].encode('utf-8')).hexdigest()
        outcome='reported_charged'
    except Exception:
        # Timeout, provider refusal, invalid JSON, partial output or missing cost
        # is not proof of zero bill. Quarantine and leave full reservation held.
        actual=None;result_sha=None;prompt_tokens=None;completion_tokens=None
        ident=None
    try:
        with connection.transaction():
            d={
                'project_id':quote.project_id,'attempt_id':reservation.attempt_id,
                'outcome':outcome,'response_sha256':result_sha,
                'reported_cost_usd':str(actual) if actual is not None else None,
                'provider_generation_id':ident,
                'prompt_tokens':prompt_tokens,'completion_tokens':completion_tokens,
            }
            connection.execute(
                """INSERT INTO pie_model_dispatch_outcomes
                   (project_id,attempt_id,result_sha256,outcome,reported_cost_usd,
                    provider_generation_id,response_sha256,reported_prompt_tokens,
                    reported_completion_tokens)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (quote.project_id,reservation.attempt_id,canonical_hash(d),outcome,
                 actual,ident,result_sha,prompt_tokens,completion_tokens))
            kind='charged' if outcome=='reported_charged' else 'uncertain'
            connection.execute(
                """INSERT INTO pie_model_attempt_events
                   (project_id,attempt_id,event_seq,event_type,amount_usd,event_sha256)
                   VALUES (%s,%s,2,%s,%s,%s)""",
                (quote.project_id,reservation.attempt_id,kind,actual,canonical_hash(d)))
    except Exception as exc:
        raise PIEBoundaryError('model_outcome_persistence_unknown',
                               'Claim persisted; manual billing reconciliation required; do not retry') from exc
    return ModelDispatchReceipt(
        quote.project_id,reservation.attempt_id,outcome,result_sha,
        str(actual) if actual is not None else None,ident)
