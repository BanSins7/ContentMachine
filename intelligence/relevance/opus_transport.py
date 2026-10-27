"""M27 strict OpenRouter Opus 5.5 transport envelope and zero-network fake.

This module deliberately has NO live-send authorization. Building an HTTP
request or possessing an M26 committed intent is NOT a paid POST permit.
M27 prepares one exact, preplanned chat completion, offers an injectable fake,
and has a private one-shot HTTPS implementation for a later M28/M29 gated
worker. Public send_live_openrouter_opus fails closed without touching keys or
network until exact tokens/account fees, atomic receipts and UNKNOWN recovery
have been implemented and independently verified.

No generic provider adapter, automatic retries, SDK fallback, env-based
enablement, redirects, streaming, silent model replacement or analytics writes.
"""
from __future__ import annotations

import json
import ssl
from urllib.error import HTTPError, URLError
from urllib.request import (
    Request, HTTPSHandler, HTTPRedirectHandler, ProxyHandler,
    build_opener,
)

from .opus_catalog_preflight import (\n    MODEL_ID, preflight_exact_opus_batch, preflight_exact_opus_catalog,\n)
from .opus_evidence_verifier import _manifest
from .opus_request_packer import PACK_VERSION
from .v1_contracts import (
    SREContractError, canonical_json, canonical_uuid, fingerprint, positive,
    project_id, record, reject, sha256_digest,
)

VERSION = "sre-openrouter-opus-transport/v1"
URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_REQUEST_BYTES = 262144
MAX_RESPONSE_BYTES = 262144
MIN_TIMEOUT_SECONDS = 5
MAX_TIMEOUT_SECONDS = 90
# A selected provider endpoint must support every requested response parameter.
# Avoid server/provider-specific autofallback, floating model ids, routes and ZDR
# weakening; these are static STRICT restrictions, not user options.
PROVIDER_POLICY = {
    "allow_fallbacks": False,
    "require_parameters": True,
    "data_collection": "deny",
    "zdr": True,
}
# The response format mirrors the M21 exact category/decision/reason/evidence
# verifier. A JSON Schema is a structural request, never trust in the answer.
_REPLY_SCHEMA = {
    "type": "object",
    "properties": {
        "category_key": {"type": "string"},
        "decision": {"type": "string", "enum": ["match", "no_match", "uncertain"]},
        "reason": {"type": "string"},
        "evidence": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string"},
                    "index": {"type": "integer"},
                    "start_char": {"type": "integer"},
                    "end_char": {"type": "integer"},
                    "quote": {"type": "string"},
                    "start_ms": {"type": ["integer", "null"]},
                    "end_ms": {"type": ["integer", "null"]},
                },
                "required": [
                    "source", "index", "start_char", "end_char", "quote",
                    "start_ms", "end_ms",
                ],
                "additionalProperties": False,
            },
        },
    },
    "required": ["category_key", "decision", "reason", "evidence"],
    "additionalProperties": False,
}


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _timeout(seconds):
    if type(seconds) is not int or not MIN_TIMEOUT_SECONDS<=seconds<=MAX_TIMEOUT_SECONDS:
        reject("transport.timeout_seconds", "timeout must be bounded 5..90 seconds",
               "invalid_transport_config")
    return seconds


def _intent_row(intent, manifest, ordinal):
    """Compare an M26 DB-backed record to a fully sealed M20 batch.

    Pure validation cannot authenticate DB provenance. Only the trusted host
    may fetch M26 from PostgreSQL with read_committed_live_sre_opus_intent.
    """
    record(intent, "m26_intent", required=(
        "schema_version", "status", "intent_id", "attempt_id",
        "project_id", "run_id", "item_id", "request_ordinal",
        "request_sha256", "batch_manifest_sha256", "reservation_id",
        "quote_sha256", "reserved_upper_bound_usd", "intent_at",
        "automatic_retry_allowed", "requires_exact_token_and_account_price_check",
        "provider_send_implemented", "dispatch_allowed", "paid_calls",
    ))
    if (intent["schema_version"]!="sre-live-opus-dispatch-intent/v1"
            or intent["status"]!="recorded_no_provider_receipt"
            or intent["automatic_retry_allowed"] is not False
            or intent["requires_exact_token_and_account_price_check"] is not True
            or intent["provider_send_implemented"] is not False
            or intent["dispatch_allowed"] is not False
            or intent["paid_calls"]!=0):
        reject("m26_intent","untrusted or prematurely dispatched intent",
               "rights_denied")
    for field in ("intent_id","attempt_id","run_id","reservation_id"):
        canonical_uuid(intent[field], "m26_intent."+field)
    project_id(intent["project_id"])
    positive(intent["item_id"],"m26_intent.item_id",maximum=9223372036854775807)
    sha256_digest(intent["quote_sha256"],"m26_intent.quote_sha256")
    sha256_digest(intent["request_sha256"],"m26_intent.request_sha256")
    sha256_digest(intent["batch_manifest_sha256"],"m26_intent.batch_manifest_sha256")
    if (type(ordinal) is not int or not 0<=ordinal<len(manifest["requests"])
            or intent["request_ordinal"]!=ordinal
            or intent["project_id"]!=manifest["project_id"]
            or intent["batch_manifest_sha256"]!=manifest["batch_manifest_sha256"]):
        reject("m26_intent","intent not bound to chosen M20 batch",
               "snapshot_mismatch")
    selected=manifest["requests"][ordinal]
    if selected["request_sha256"]!=intent["request_sha256"]:
        reject("m26_intent","M20 request SHA changed since commit",
               "snapshot_mismatch")
    # Do not infer per-request billable cost from a run-wide reserved ceiling.
    from .opus_final_quote import _usd
    if _usd(intent["reserved_upper_bound_usd"],"m26_intent.reserved_upper_bound_usd")<=0:
        reject("m26_intent","missing nonzero exact reserved USD amount",
               "budget_not_reserved")
    return selected


def build_pinned_opus_transport_envelope(
    m20_manifest, m26_record, catalog_snapshot, *,
    operator_expected_sha256, operator_endpoint_tag,
    operator_model_revision, now=None, timeout_seconds=30,
):
    """Build a read-only exact request envelope for fake/offline validation.

    The operator SHA is separate trusted configuration; a self-hashed
    user-supplied endpoint is not sufficient proof of current pricing.
    """
    _timeout(timeout_seconds)
    _manifest(m20_manifest)
    if m20_manifest["model_id"]!=MODEL_ID or MODEL_ID!="anthropic/claude-opus-5.5":
        reject("opus.model", "exact Opus model missing; no fallback",
               "unsupported_model")
    preflight=preflight_exact_opus_batch(
        m20_manifest,catalog_snapshot,
        operator_expected_sha256=operator_expected_sha256,
        operator_endpoint_tag=operator_endpoint_tag,
        operator_model_revision=operator_model_revision,
        now=now,
    )
    if (preflight["model_id"]!=MODEL_ID or
            preflight["endpoint_tag"]!=operator_endpoint_tag or
            preflight["batch_manifest_sha256"]!=m20_manifest["batch_manifest_sha256"]
            or preflight["catalog_sha256"]!=m20_manifest["catalog_sha256"]):
        reject("opus.catalog","catalog revision/endpoint drift",
               "snapshot_mismatch")
    if type(m26_record) is not dict:
        reject("m26_intent","trusted committed-intent metadata required",
               "rights_denied")
    ordinal=m26_record.get("request_ordinal")
    request=_intent_row(m26_record,m20_manifest,ordinal)
    if (request["schema_version"]!=PACK_VERSION or
            request["model_id"]!=MODEL_ID or
            request["model_revision"]!=operator_model_revision or
            request["catalog_sha256"]!=preflight["catalog_sha256"] or
            request["temperature_milli"]!=0 or
            request["dispatch_allowed"] is not False):
        reject("opus.request","unsupported model or tampered request",
               "snapshot_mismatch")
    endpoint=preflight_exact_opus_catalog(\n        catalog_snapshot,operator_expected_sha256=operator_expected_sha256,\n        operator_endpoint_tag=operator_endpoint_tag,\n        operator_model_revision=operator_model_revision,\n        requested_max_output_tokens=m20_manifest["requests"][0]["max_output_tokens"],\n        requested_input_proxy=m20_manifest["input_token_budget_proxy"],\n        now=now,\n    )\n    routing=endpoint.get("routing")
    if (type(routing) is not dict or
            routing.get("only")!=[operator_endpoint_tag] or
            routing.get("allow_fallbacks") is not False):
        reject("opus.catalog","pinned provider route missing or changed",
               "snapshot_mismatch")
    payload={
        "model": MODEL_ID,
        "messages": request["messages"],
        "temperature": 0,
        "max_tokens": request["max_output_tokens"],
        "stream": False,
        "provider": {**PROVIDER_POLICY,"only":[operator_endpoint_tag]},
        "response_format": {
            "type":"json_schema",
            "json_schema":{
                "name":"sre_opus_chunk",
                "strict":True,
                "schema":_REPLY_SCHEMA,
            },
        },
    }
    encoded=canonical_json(payload).encode("utf-8")
    if len(encoded)>MAX_REQUEST_BYTES:
        reject("transport.body","request exceeds bounded HTTPS payload",
               "transport_payload_too_large")
    result={
        "schema_version": VERSION,
        "status": "sealed_preview_not_authorized_to_send",
        "url": URL,
        "method": "POST",
        "model_id": MODEL_ID,
        "project_id": m26_record["project_id"],
        "intent_id": m26_record["intent_id"],
        "request_sha256": request["request_sha256"],
        "batch_manifest_sha256": m20_manifest["batch_manifest_sha256"],
        "catalog_sha256": m20_manifest["catalog_sha256"],
        "endpoint_tag": operator_endpoint_tag,
        "body_sha256": fingerprint(payload),
        "reserved_upper_bound_usd": m26_record["reserved_upper_bound_usd"],
        "body": payload,
        "timeout_seconds": timeout_seconds,
        "exact_provider_tokens_verified": False,
        "account_extra_fees_verified": False,
        "automatic_retry_allowed": False,
        "fake_only": True,
        "dispatch_allowed": False,
        "paid_calls": 0,
    }
    return result


def _checked_envelope(envelope):
    record(envelope,"transport",required=(
        "schema_version", "status", "url", "method", "model_id", "project_id",
        "intent_id", "request_sha256", "batch_manifest_sha256", "catalog_sha256",
        "endpoint_tag", "body_sha256", "reserved_upper_bound_usd",
        "body", "timeout_seconds",
        "exact_provider_tokens_verified", "account_extra_fees_verified",
        "automatic_retry_allowed", "fake_only", "dispatch_allowed","paid_calls",
    ))
    if (envelope["schema_version"]!=VERSION or
            envelope["status"]!="sealed_preview_not_authorized_to_send"
            or envelope["url"]!=URL or envelope["method"]!="POST"
            or envelope["model_id"]!=MODEL_ID
            or envelope["fake_only"] is not True
            or envelope["dispatch_allowed"] is not False
            or envelope["paid_calls"]!=0
            or envelope["automatic_retry_allowed"] is not False
            or envelope["exact_provider_tokens_verified"] is not False
            or envelope["account_extra_fees_verified"] is not False
            or fingerprint(envelope["body"])!=envelope["body_sha256"]):
        reject("transport","unsealed or fake-only permission escalation",
               "rights_denied")
    _timeout(envelope["timeout_seconds"])
    from .opus_final_quote import _usd
    if _usd(envelope["reserved_upper_bound_usd"],"transport.reserved_upper_bound_usd")<=0:
        reject("transport.cap","positive consent-backed budget hold required",
               "budget_not_reserved")
    canonical_uuid(envelope["intent_id"],"intent_id")
    sha256_digest(envelope["request_sha256"],"request_sha256")
    sha256_digest(envelope["batch_manifest_sha256"],"batch_manifest_sha256")
    sha256_digest(envelope["catalog_sha256"],"catalog_sha256")
    project_id(envelope["project_id"])
    body=envelope["body"]
    record(body,"transport.body",required=(
        "model","messages","temperature","max_tokens",
        "stream","provider","response_format",
    ))
    if (body["model"]!=MODEL_ID or body["stream"] is not False or
            body["temperature"]!=0 or
            body["provider"]!=PROVIDER_POLICY
            or body["response_format"]!={"type":"json_schema","json_schema":{
                "name":"sre_opus_chunk","strict":True,"schema":_REPLY_SCHEMA}}
            or type(body["messages"]) is not list
            or len(body["messages"])!=2
            or any(type(msg) is not dict for msg in body["messages"])
            or body["messages"][0].get("role")!="system"
            or body["messages"][1].get("role")!="user"
            or not 1<=positive(body["max_tokens"],"max_tokens",maximum=128000)<=128000):
        reject("transport.body","unsafe Opus request profile","rights_denied")
    encoded=canonical_json(body).encode("utf-8")
    if len(encoded)>MAX_REQUEST_BYTES:
        reject("transport.body","oversized body","transport_payload_too_large")
    return encoded


def safe_transport_telemetry(envelope, *, outcome="fake_prepared"):
    """Safe-to-log, prompt- and secret-free M27 counters, NOT billing telemetry."""
    _checked_envelope(envelope)
    if type(outcome) is not str or outcome not in (
        "fake_prepared","fake_response","transport_blocked","provider_unknown",
    ):
        reject("transport.outcome","unknown audited telemetry event",
               "invalid_transport_config")
    return {
        "schema_version":VERSION,
        "event":"sre.opus.transport."+outcome,
        "project_id":envelope["project_id"],
        "intent_id":envelope["intent_id"],
        "request_sha256":envelope["request_sha256"],
        "body_sha256":envelope["body_sha256"],
        "model_id":MODEL_ID,
        "reserved_upper_bound_usd":envelope["reserved_upper_bound_usd"],
        "paid_calls_confirmed":0,
        "authenticated_provider_receipt":False,
        "automatic_retry_allowed":False,
        "source_text_in_telemetry":False,
    }


class FakeOpusTransport:
    """Explicit scripted zero-cost transport. No socket, opener or credentials.

    It cannot alter SQL, grant a consent, produce a trusted provider receipt,
    or write decisions. Scripted raw JSON remains untrusted until M21.
    """
    def __init__(self, scripted=None):
        if scripted is None:
            scripted={}
        if type(scripted) is not dict or len(scripted)>2048:
            reject("fake.scripted","invalid fake response collection")
        for key,value in scripted.items():
            sha256_digest(key,"fake.request_sha256")
            if type(value) is not str or not value or len(value.encode("utf-8"))>MAX_RESPONSE_BYTES:
                reject("fake.response","scripted UTF-8 JSON bounded response required",
                       "malformed_model_output")
        self._scripted=dict(scripted)
        self._delivered=set()

    def execute(self, envelope, *, cancelled=False):
        _checked_envelope(envelope)
        if cancelled is not False:
            reject("fake.cancelled","caller cancelled before fake send",
                   "transport_cancelled")
        key=envelope["request_sha256"]
        if key in self._delivered:
            reject("fake.request_sha256","fake cannot auto-retry an intent",
                   "unknown_paid_outcome")
        # Even if no script exists, do not manufacture positive/negative
        # semantic labels, fake receipts or pretend the paid HTTP succeeded.
        if key not in self._scripted:
            reject("fake.request_sha256","scripted response missing",
                   "fake_response_missing")
        self._delivered.add(key)
        return {
            "schema_version":VERSION,
            "status":"fake_untrusted_response_no_charge",
            "intent_id":envelope["intent_id"],
            "request_sha256":key,
            "body_sha256":envelope["body_sha256"],
            "raw_answer":self._scripted[key],
            "source":"synthetic_fixture_not_provider",
            "provider_response_id":None,
            "authenticated_provider_usage":None,
            "actual_charge_usd":None,
            "receipt_verified":False,
            "dispatch_allowed":False,
            "paid_calls":0,
            "automatic_retry_allowed":False,
        }


def send_live_openrouter_opus(*args, **kwargs):
    """Public paid dispatch intentionally CLOSED while M28/M29 are absent.

    No env var, header, callback, consent object or model pin can override this
    guard. An M26 intent is a possible charge, not an executable send ticket.
    """
    reject("openrouter.live_send",
           "M27 HTTPS implementation is staged but LIVE paid POST remains "
           "blocked until M28/M29 durable receipts, exact tokens, fees and "
           "one-shot reconciliation are independently accepted",
           "paid_transport_not_ready")


def _build_https_request(payload, *, api_key):
    """Private wire constructor, no I/O and no implicit network permission."""
    if (type(api_key) is not str or not 12<=len(api_key)<=512
            or any(ch in api_key for ch in "\r\n\x00")):
        reject("transport.credentials","trusted host API key unavailable",
               "credentials_missing")
    if type(payload) is not bytes or not 1<=len(payload)<=MAX_REQUEST_BYTES:
        reject("transport.body","invalid wire bytes","transport_payload_too_large")
    return Request(
        URL,
        data=payload,
        method="POST",
        headers={
            "Authorization":"Bearer "+api_key,
            "Content-Type":"application/json",
            "Accept":"application/json",
            "User-Agent":"ContentMachine-SRE-OpusTransport/1",
        },
    )


def _one_https_post_for_future_gate(payload, *, api_key, timeout_seconds=30,
                                    opener=None,cancellation_checker=None):
    """Private single attempt primitive for later M28/M29 integration only.

    No PUBLIC M27 method invokes this. Test code may inject a fake opener;
    production must not call it until authenticated budget/receipts are ready.
    No redirects, proxies, retries or alternate hosts; error details are
    intentionally redacted to avoid leaking API keys or D1 prompts.
    """
    _timeout(timeout_seconds)
    if cancellation_checker is not None:
        if not callable(cancellation_checker):
            reject("transport.cancel_checker","trusted cancellation callable required",
                   "invalid_transport_config")
        try:
            cancelled=cancellation_checker()
        except Exception:
            reject("transport.cancel_checker","cannot confirm active worker",
                   "transport_cancelled")
        if cancelled is not False:
            reject("transport.cancel_checker","cancelled before HTTPS POST",
                   "transport_cancelled")
    request=_build_https_request(payload,api_key=api_key)
    client=opener
    if client is None:
        # Even direct imports of this private staging primitive CANNOT POST.
        # A subsequent audited M28/M29 code change must install a real
        # single-use gate before selecting verified TLS transport.
        reject("transport.network",
               "real HTTPS POST is disabled until M28/M29 reconciliation gate",
               "paid_transport_not_ready")
    try:
        with client.open(request,timeout=timeout_seconds) as response:
            if (response.geturl()!=URL or response.status!=200 or
                    response.headers.get("Content-Type","").split(";",1)[0].strip().lower()
                    !="application/json"):
                reject("transport.response","unexpected redirect/status/media type",
                       "provider_outcome_unknown")
            raw=response.read(MAX_RESPONSE_BYTES+1)
            if type(raw) is not bytes or len(raw)>MAX_RESPONSE_BYTES:
                reject("transport.response","oversized provider JSON",
                       "provider_outcome_unknown")
    except (HTTPError,URLError,TimeoutError,ConnectionError,ssl.SSLError,OSError):
        # Never retry: even an HTTP error or connect reset could be billable.
        raise SREContractError(
            "provider_outcome_unknown","transport.response",
            "OpenRouter outcome could be paid: retain reservation; reconcile"
        ) from None
    try:
        response=json.loads(raw.decode("utf-8"))
    except (ValueError,UnicodeDecodeError,RecursionError):
        reject("transport.response","invalid JSON; outcome unknown",
               "provider_outcome_unknown")
    if (type(response) is not dict or type(response.get("id")) is not str
            or not 1<=len(response["id"])<=256
            or response.get("model")!=MODEL_ID
            or type(response.get("choices")) is not list
            or len(response["choices"])!=1
            or response.get("error") is not None):
        reject("transport.response","missing exact model/provider response",
               "provider_outcome_unknown")
    return response


__all__=(
    "VERSION","URL","PROVIDER_POLICY",
    "build_pinned_opus_transport_envelope","FakeOpusTransport",
    "safe_transport_telemetry",
    "send_live_openrouter_opus",
)
