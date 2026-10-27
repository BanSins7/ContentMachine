"""M28 verified OpenRouter generation receipt and atomic USD accounting.

No POST, no provider generation or new requests. The only financial authority
is an independently authenticated HTTPS GET of an already-known generation id
using a trusted host-held key. No caller-supplied metadata or fake response
can authorize settlement. If that independent read fails, original M25 reserve
remains frozen with the M26 unknown-paid witness (not a free retry).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_CEILING, InvalidOperation
from datetime import datetime, timezone
import hashlib
import json
import re
import ssl
from uuid import uuid4
from urllib.error import URLError, HTTPError
from urllib.parse import quote as urlquote
from urllib.request import Request, build_opener, ProxyHandler, HTTPSHandler

from .opus_budget import _budget, _money_text, _workspace
from .opus_final_quote import _usd
from .opus_consent import _actor, _authorized
from .opus_dispatch_intent import _committable
from .opus_evidence_verifier import _request_payload, _verify_single
from .opus_request_packer import pack_live_opus_requests
from .opus_catalog_preflight import MODEL_ID, preflight_exact_opus_catalog
from .v1_contracts import (
    SREContractError, canonical_uuid, positive, project_id, reject,
    sha256_digest, fingerprint,
)

VERSION="sre-opus-provider-receipt/v1"
GENERATION_URL="https://openrouter.ai/api/v1/generation"
MAX_GENERATION_BYTES=131072
MAX_PROVIDER_RESPONSE_BYTES=262144
USD_UNIT=Decimal("0.000000001")
GENERATION_ID=re.compile(r"[A-Za-z0-9_.:-]{4,200}\Z")
# Absence of these fields is NOT evidence they were zero.
AUXILIARY_ZERO=("num_fetches","num_search_results","num_media_prompt",
                "num_input_audio_prompt","num_media_completion")


class _NoRedirect(__import__("urllib.request",fromlist=["HTTPRedirectHandler"]).HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _key(value):
    if (type(value) is not str or not 12<=len(value)<=512 or
            any(x in value for x in "\r\n\x00")):
        reject("provider.api_key","trusted host-held OpenRouter key required",
               "credentials_missing")
    return value


def _generation_id(value):
    if type(value) is not str or GENERATION_ID.fullmatch(value) is None:
        reject("generation_id","invalid generation identity","invalid_provider_receipt")
    return value


def _decimal_cost(amount):
    """Provider HTTPS JSON number, decoded with parse_float=Decimal.

    No float, bool, NaN, implicit 0, hidden under-reservation or rounding
    DOWN. Currency/account/fee provenance must be verified separately.
    """
    if type(amount) not in (Decimal,int):
        reject("generation.total_cost","numeric exact HTTPS provider cost missing",
               "invalid_provider_receipt")
    value=Decimal(amount)
    if not value.is_finite() or value<0 or value>=Decimal("100000000000000"):
        reject("generation.total_cost","non-finite/negative/overflow cost",
               "invalid_provider_receipt")
    try:
        return value.quantize(USD_UNIT,rounding=ROUND_CEILING)
    except (InvalidOperation,ValueError):
        reject("generation.total_cost","unrepresentable provider USD cost",
               "invalid_provider_receipt")


def validate_openrouter_generation(generation, *, generation_id,
                                   upper_bound_usd, max_input_tokens,
                                   max_output_tokens):
    """Pure shape check ONLY. A dict alone cannot prove provider identity.

    The authenticated evidence source MUST be _read_generation_https below,
    which does strict TLS GET to the one fixed OpenRouter generation URL.
    """
    gid=_generation_id(generation_id)
    if type(generation) is not dict:
        reject("generation","missing provider generation object",
               "invalid_provider_receipt")
    if (generation.get("id")!=gid or generation.get("model")!=MODEL_ID or
            generation.get("provider_name")!="Anthropic" or
            generation.get("is_byok") is not False):
        reject("generation","generation/model/provider/account not exact",
               "invalid_provider_receipt")
    if generation.get("cancelled") is not False:
        reject("generation","cancelled or unknown generation status",
               "provider_outcome_unknown")
    for field in AUXILIARY_ZERO:
        if type(generation.get(field)) is not int or generation[field]!=0:
            reject("generation."+field,"unquoted auxiliary fee risk",
                   "account_fee_unverified")
    inp=generation.get("tokens_prompt")
    out=generation.get("tokens_completion")
    if (type(inp) is not int or inp<0 or type(out) is not int or out<0
            or inp>positive(max_input_tokens,"max_input_tokens",maximum=2000000)
            or out>positive(max_output_tokens,"max_output_tokens",maximum=128000)):
        reject("generation.tokens","invalid/out-of-quote provider token counts",
               "provider_token_mismatch")
    held=_usd(upper_bound_usd,"reservation.upper_bound_usd")
    if held<=0:
        reject("reservation","exact reserved Decimal USD required",
               "budget_not_reserved")
    charged=_decimal_cost(generation.get("total_cost"))
    if charged>held:
        reject("generation.total_cost","actual provider cost above held amount; "
               "manual accounting required, no automatic charge or release",
               "provider_cost_exceeds_reserve")
    # Numeric provider usage may be 0 for cached requests, but missing
    # original generation price is never interpreted as free.
    return {
        "schema_version":VERSION,
        "generation_id":gid,
        "model_id":MODEL_ID,
        "provider_name":"Anthropic",
        "input_tokens":inp,"output_tokens":out,
        "charged_usd":_money_text(charged),
        "held_usd":_money_text(held),
        "unspent_usd":_money_text(held-charged),
        "provider_shape_verified":True,
        "provider_origin_authenticated":False,
        "total_account_fees_verified":False,
        "semantic_ground_truth":False,
        "paid_calls_issued_here":0,
    }


def _read_generation_https(generation_id, api_key, *, timeout_seconds=8,
                           opener=None):
    """Read-only metadata lookup; no model/paid POST and no auto-retry.

    An injected opener is intended ONLY for offline tests. The public
    settlement path never accepts or forwards such a test opener.
    """
    gid=_generation_id(generation_id)
    token=_key(api_key)
    if type(timeout_seconds) is not int or not 1<=timeout_seconds<=10:
        reject("generation.timeout","bounded GET timeout 1..10 required",
               "invalid_transport_config")
    url=GENERATION_URL+"?id="+urlquote(gid,safe="")
    request=Request(url,headers={
        "Authorization":"Bearer "+token,
        "Accept":"application/json",
        "User-Agent":"ContentMachine-SRE-VerifiedGeneration/1",
    },method="GET")
    client=opener
    if client is None:
        client=build_opener(ProxyHandler({}),_NoRedirect(),
                            HTTPSHandler(context=ssl.create_default_context()))
    try:
        with client.open(request,timeout=timeout_seconds) as response:
            if (response.geturl()!=url or response.status!=200
                or response.headers.get("Content-Type","").split(";",1)[0]
                    .strip().lower()!="application/json"):
                reject("generation.transport","invalid independent provider GET",
                       "generation_unverified")
            data=response.read(MAX_GENERATION_BYTES+1)
            if type(data) is not bytes or len(data)>MAX_GENERATION_BYTES:
                reject("generation.transport","oversized metadata",
                       "generation_unverified")
    except (HTTPError,URLError,TimeoutError,ConnectionError,ssl.SSLError,OSError):
        reject("generation.transport","generation read unavailable; hold reserve",
               "generation_unverified")
    try:
        parsed=json.loads(
            data.decode("utf-8"),
            parse_float=Decimal,
            parse_constant=lambda _:reject(
                "generation","non-finite provider numeric",
                "generation_unverified"),
        )
    except (ValueError,TypeError,UnicodeError,RecursionError):
        reject("generation.transport","invalid provider JSON; hold reserve",
               "generation_unverified")
    if type(parsed) is not dict or type(parsed.get("data")) is not dict:
        reject("generation.transport","provider response has no generation",
               "generation_unverified")
    return parsed["data"],hashlib.sha256(data).hexdigest()


def _provider_answer(response,generation_id):
    """Bounded JSON from a trusted host-owned M27 response path, not user API."""
    gid=_generation_id(generation_id)
    if type(response) is not dict or response.get("id")!=gid or (
            response.get("model")!=MODEL_ID):
        reject("response","response id/model mismatch with generation",
               "invalid_provider_receipt")
    if type(response.get("choices")) is not list or len(response["choices"])!=1:
        reject("response","one exact Opus answer required",
               "invalid_provider_receipt")
    selected=response["choices"][0]
    if type(selected) is not dict or selected.get("index")!=0 or (
            type(selected.get("message")) is not dict):
        reject("response","malformed provider response choice",
               "invalid_provider_receipt")
    msg=selected["message"]
    if msg.get("role")!="assistant":
        reject("response","wrong model response role","invalid_provider_receipt")
    raw=msg.get("content")
    if type(raw) is not str or not raw or len(raw.encode("utf-8"))>32768:
        # A malformed charged answer is still charged. Classifier validation
        # will flag invalid output rather than invent a no_match.
        raw=None
    if len(json.dumps(response,ensure_ascii=False,default=str).encode("utf-8"))>MAX_PROVIDER_RESPONSE_BYTES:
        reject("response","provider response too large",
               "invalid_provider_receipt")
    return raw


SQL_INTENT = """SELECT i.reservation_id::text,i.quote_sha256,i.request_sha256,
    i.request_ordinal,i.batch_manifest_sha256,i.catalog_sha256,i.model_revision,
    i.endpoint_tag,i.reserved_usd,i.worker_token::text,i.lease_generation,
    r.state,r.amount_usd,q.quote
    FROM sre_live_opus_dispatch_intents i
    JOIN sre_live_budget_reservations r
       ON r.project_id=i.project_id AND r.reservation_id=i.reservation_id
    JOIN sre_live_opus_quotes q
       ON q.project_id=i.project_id AND q.run_id=i.run_id
      AND q.item_id=i.item_id AND q.quote_sha256=i.quote_sha256
    WHERE i.project_id=%s AND i.run_id=%s::uuid AND i.item_id=%s
      AND i.intent_id=%s::uuid FOR UPDATE OF r"""
SQL_EXISTS = """SELECT receipt_id::text FROM sre_live_opus_receipts
    WHERE project_id=%s AND intent_id=%s::uuid FOR SHARE"""
SQL_INSERT = """INSERT INTO sre_live_opus_receipts (
    receipt_id,project_id,run_id,item_id,intent_id,reservation_id,
    request_sha256,generation_id,model_id,provider_name,response_sha256,
    generation_metadata_sha256,evidence_sha256,evidence_status,
    input_tokens,output_tokens,held_usd,charged_usd,generation_authenticated)
    VALUES (%s::uuid,%s,%s::uuid,%s,%s::uuid,%s::uuid,
            %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::numeric,%s::numeric,TRUE)
    RETURNING receipt_id::text"""
SQL_SETTLED = """SELECT receipt_id::text,charged_usd,held_usd,
    generation_id,evidence_status
    FROM sre_live_opus_receipts
    WHERE project_id=%s AND intent_id=%s::uuid FOR SHARE"""


def settle_live_sre_opus_generation(
    conn,project,run_id,item_id,intent_id,
    provider_response, generation_id, *,
    api_key, actor_id, actor_authorizer, catalog_snapshot=None,
):
    """Trusted host-only one-transaction financial receipt settlement.

    The generation GET is an authenticated read-only HTTPS request made
    BEFORE taking project/database locks. This API DOES NOT send prompts.
    Unlike the M21 content verifier, charged generation reconciliation must
    remain possible after consent expiry/source revocation/cancellation.
    The receipt never writes semantic match/no_match into LIVE results.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    identity=canonical_uuid(intent_id,"intent_id")
    generation_id=_generation_id(generation_id)
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"settle_sre_opus_generation")
    _committable(conn)
    _key(api_key)
    raw=_provider_answer(provider_response,generation_id)
    # This is the ONLY trusted provider usage source. No caller-controlled
    # generation JSON, fake opener or environment override enters settlement.
    metadata,meta_sha=_read_generation_https(generation_id,api_key)

    with conn.transaction():
        _workspace(conn,pid)  # same lock order as M15/25/26
        row=conn.execute(SQL_INTENT,(pid,rid,iid,identity)).fetchone()
        if row is None:
            reject("intent","unknown/foreign M26 intent","rights_denied")
        (reservation_id,quote_sha,request_sha,ordinal,batch_sha,catalog_sha,
         revision,tag,held,res_token,generation,res_state,res_amount,quote)=row
        if conn.execute(SQL_EXISTS,(pid,identity)).fetchone() is not None:
            reject("intent","provider receipt already settled; no double debit",
                   "receipt_already_settled")
        if res_state!="reserved" or held!=res_amount:
            reject("reservation","missing original exact M25 reserve",
                   "budget_not_reserved")
        _budget(conn,pid)
        if (type(quote) is not dict or
                quote.get("quote_sha256")!=quote_sha or
                quote.get("batch_manifest_sha256")!=batch_sha or
                quote.get("catalog_sha256")!=catalog_sha or
                quote.get("model_id")!=MODEL_ID or
                quote.get("model_revision")!=revision or
                quote.get("endpoint_tag")!=tag or
                type(quote.get("per_request")) is not list or
                type(ordinal) is not int or not 0<=ordinal<len(quote["per_request"])
                or quote["per_request"][ordinal].get("request_sha256")!=request_sha):
            reject("quote","M23 immutable original per-request pin mismatch",
                   "snapshot_mismatch")
        per=quote["per_request"][ordinal]
        charge=validate_openrouter_generation(
            metadata,generation_id=generation_id,
            upper_bound_usd=_money_text(held),
            max_input_tokens=positive(per["input_token_upper_bound"],
                                      "quote.input_token_upper_bound"),
            max_output_tokens=positive(per["output_token_upper_bound"],
                                       "quote.output_token_upper_bound"),
        )
        if Decimal(charge["charged_usd"])>_usd(per["upper_usd"],"quote.per_request.upper_usd"):
            reject("generation.total_cost","provider cost exceeds this exact M23 request bound",
                   "provider_cost_exceeds_reserve")
        evidence_status="source_unavailable"
        evidence_hash=fingerprint({
            "request_sha256":request_sha,"generation_id":generation_id,
            "response_sha256":fingerprint(provider_response),
            "technical_status":evidence_status,
        })
        if raw is not None and catalog_snapshot is not None:
            try:
                # M21 checks ONE chunk only; never infer completion of every
                # approved C9 leaf or promote fake classifications to LIVE.
                # Revoked D1 rights, missing/stale catalog, invalid response
                # or chunk drift do not magically erase an incurred charge.
                pin=preflight_exact_opus_catalog(
                    catalog_snapshot,
                    operator_expected_sha256=catalog_snapshot["response_sha256"],
                    operator_endpoint_tag=tag,
                    operator_model_revision=revision,
                    requested_max_output_tokens=quote["max_output_tokens_per_request"],
                    requested_input_proxy=quote["requested_input_token_proxy"],
                )["m20_catalog_pin"]
                packed=pack_live_opus_requests(
                    conn,pid,rid,iid,operator_catalog_pin=pin)
                if (packed["batch_manifest_sha256"]!=batch_sha or
                        packed["requests"][ordinal]["request_sha256"]!=request_sha):
                    raise SREContractError("snapshot_mismatch","receipt",
                                           "original D1/C9 source changed")
                request=packed["requests"][ordinal]
                observed=_verify_single(
                    request,_request_payload(request),raw)
                evidence_status="validated_untrusted_output"
                evidence_hash=fingerprint(observed)
            except (SREContractError,ValueError,KeyError,TypeError):
                evidence_status="invalid_untrusted_output"
                evidence_hash=fingerprint({
                    "request_sha256":request_sha,
                    "response_sha256":fingerprint(provider_response),
                    "technical_status":evidence_status,
                })
        elif raw is not None:
            # Deliberately no source-verification claim when the original
            # operator-approved catalog/D1 context cannot be reassembled.
            evidence_status="source_unavailable"
        else:
            evidence_status="invalid_untrusted_output"
            evidence_hash=fingerprint({
                "request_sha256":request_sha,
                "response_sha256":fingerprint(provider_response),
                "technical_status":evidence_status,
            })
        receipt_id=str(uuid4())
        saved=conn.execute(SQL_INSERT,(
            receipt_id,pid,rid,iid,identity,reservation_id,
            request_sha,generation_id,MODEL_ID,"Anthropic",
            fingerprint(provider_response),meta_sha,evidence_hash,
            evidence_status,charge["input_tokens"],charge["output_tokens"],
            charge["held_usd"],charge["charged_usd"],
        )).fetchone()
        if saved is None or saved[0]!=receipt_id:
            reject("receipt","missing atomic ledger receipt","budget_invariant")
        account=_budget(conn,pid)
        if account[2]<Decimal(charge["charged_usd"]):
            reject("budget","spent balance not settled atomically",
                   "budget_invariant")
        updated=conn.execute(
            "SELECT state,settlement_receipt_id::text FROM "
            "sre_live_budget_reservations WHERE project_id=%s "
            "AND reservation_id=%s::uuid",
            (pid,reservation_id),
        ).fetchone()
        if updated!=("settled",receipt_id):
            reject("reservation","budget settlement not committed",
                   "budget_invariant")
    return {
        "schema_version":VERSION,
        "status":"provider_generation_accounted_not_full_classification",
        "receipt_id":receipt_id,
        "generation_id":generation_id,"project_id":pid,
        "run_id":rid,"item_id":iid,"intent_id":identity,
        "request_sha256":request_sha,
        "provider_inference_charged_usd":charge["charged_usd"],
        "unused_reservation_released_usd":charge["unspent_usd"],
        "provider_token_usage_confirmed":True,
        "provider_generation_queried_over_https":True,
        "account_extra_fees_verified":False,
        "evidence_status":evidence_status,
        "classification_results_written":0,
        "all_hypotheses_complete":False,
        "automatic_retry_allowed":False,
        "dispatch_allowed":False,
        "paid_post_calls_issued_here":0,
    }


__all__=(
    "VERSION","GENERATION_URL","validate_openrouter_generation",
    "settle_live_sre_opus_generation",
)
