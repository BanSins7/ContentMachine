"""SRE M22: exact Claude Opus 5.5 OpenRouter catalog/endpoint preflight.

ZERO PAID CALLS: no POST, completions, secrets, consent, quote or dispatch.
The optional public GET is a dedicated operator-triggered read-only action;
the normal validator is completely offline and requires an independently
trusted operator-approved SHA pin. A self-hashed JSON snapshot is NOT catalog
authority. An eligible model alone does not authorize external D1 content.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from urllib.error import HTTPError, URLError
from urllib.request import Request, build_opener, HTTPSHandler, HTTPRedirectHandler

from .opus_request_packer import validate_catalog_pin
from .v1_contracts import fingerprint, positive, record, reject, sha256_digest

MODEL_ID = "anthropic/claude-opus-5.5"
CATALOG_URL = "https://openrouter.ai/api/v1/models/anthropic/claude-opus-5.5/endpoints"
SNAPSHOT_VERSION = "sre-opus-5.5-endpoints-snapshot/v1"
PREFLIGHT_VERSION = "sre-opus-5.5-preflight/v1"
MAX_CATALOG_BYTES = 2 * 1024 * 1024
MAX_CATALOG_AGE_SECONDS = 900
MAX_CLOCK_FUTURE_SECONDS = 30
MAX_FETCH_TIMEOUT_SECONDS = 10
PRICE_KEYS = (
    "prompt", "completion", "input_cache_read",
    "input_cache_write", "input_cache_write_1h",
)
REQUIRED_JSON_PARAMETERS = frozenset(("response_format","structured_outputs"))


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, hdrs, newurl):
        return None


def _utc(value):
    if isinstance(value, datetime):
        dt=value
    elif type(value) is str:
        if len(value)>45 or not value or not (value.endswith("Z") or
                                               value.endswith("+00:00")):
            reject("catalog.captured_at","timestamp must be UTC ISO8601",
                   "stale_catalog")
        try:
            dt=datetime.fromisoformat(value.replace("Z","+00:00"))
        except ValueError:
            reject("catalog.captured_at","invalid ISO timestamp",
                   "stale_catalog")
    else:
        reject("catalog.captured_at","UTC datetime required","stale_catalog")
    if dt.tzinfo is None or dt.utcoffset() is None or dt.utcoffset().total_seconds()!=0:
        reject("catalog.captured_at","UTC offset required","stale_catalog")
    return dt.astimezone(timezone.utc)


def _amount(value, path):
    """Decimal from canonical provider decimal STRING only; never floats."""
    if (type(value) is not str or len(value)>48 or not value or
            not all(c in "0123456789." for c in value) or
            value.count(".")>1 or value.startswith(".") or value.endswith(".")):
        reject(path,"missing or malformed decimal provider price",
               "catalog_price_missing")
    try:
        n=Decimal(value)
    except InvalidOperation:
        reject(path,"invalid provider price","catalog_price_missing")
    if not n.is_finite() or n<0 or n>=1000 or (n.as_tuple().exponent<-18):
        reject(path,"unsafe provider price","catalog_price_missing")
    return format(n,"f")


def capture_exact_opus_catalog(*, timeout_seconds=5, opener=None, now=None):
    """Explicit operator action: GET one exact public HTTPS endpoint only.

    Does not use credentials or call a paid endpoint. The selected operator
    must still independently approve the snapshot SHA before quote use.
    An optional test opener enables full offline HTTP semantics tests.
    """
    if (type(timeout_seconds) is not int or not 1<=timeout_seconds<=
            MAX_FETCH_TIMEOUT_SECONDS):
        reject("catalog.timeout_seconds","timeout must be 1..10 seconds")
    request=Request(CATALOG_URL,headers={
        "Accept":"application/json","User-Agent":"ContentMachine-SRE-CatalogPreflight/1",
    },method="GET")
    client=opener or build_opener(_NoRedirect(),HTTPSHandler())
    try:
        with client.open(request,timeout=timeout_seconds) as response:
            if response.geturl()!=CATALOG_URL or response.status!=200:
                reject("catalog.transport","redirect or non-200 response",
                       "catalog_unavailable")
            content_type=response.headers.get("Content-Type","").split(";",1)[0].strip().lower()
            if content_type!="application/json":
                reject("catalog.transport","not JSON content",
                       "catalog_unavailable")
            raw=response.read(MAX_CATALOG_BYTES+1)
    except (HTTPError,URLError,OSError,TimeoutError,ValueError) as exc:
        reject("catalog.transport","catalog GET failed (no fallback)",
               "catalog_unavailable")
    if not raw or len(raw)>MAX_CATALOG_BYTES:
        reject("catalog.transport","missing or oversized catalog",
               "catalog_unavailable")
    def strict_pairs(pairs):
        obj={}
        for key,value in pairs:
            if key in obj:
                reject("catalog.transport","duplicate public catalog JSON key",
                       "catalog_unavailable")
            obj[key]=value
        return obj

    def invalid_constant(_value):
        reject("catalog.transport","nonstandard JSON number",
               "catalog_unavailable")
    try:
        obj=json.loads(raw.decode("utf-8"),
                       object_pairs_hook=strict_pairs,
                       parse_constant=invalid_constant)
    except (UnicodeError,ValueError,RecursionError):
        reject("catalog.transport","malformed public catalog JSON",
               "catalog_unavailable")
    if type(obj) is not dict or type(obj.get("data")) is not dict:
        reject("catalog.payload","catalog response data absent",
               "catalog_unavailable")
    timestamp=_utc(now if now is not None else datetime.now(timezone.utc))
    return {
        "schema_version":SNAPSHOT_VERSION,
        "source_url":CATALOG_URL,
        "captured_at":timestamp.isoformat().replace("+00:00","Z"),
        "response":obj,
        "response_sha256":fingerprint(obj),
    }


def _snapshot(snapshot, expected_sha, now, max_age_seconds):
    """Freshness is measured from a UTC trusted runtime clock.

    A caller can forge captured_at + SHA by self-signing: the host MUST
    obtain expected_sha from an independent operator-authorized setting,
    not from snapshot itself or a user-controlled GET parameter.
    """
    record(snapshot,"catalog_snapshot",required=(
        "schema_version","source_url","captured_at","response","response_sha256",
    ))
    if (snapshot["schema_version"]!=SNAPSHOT_VERSION or
            snapshot["source_url"]!=CATALOG_URL):
        reject("catalog_snapshot","untrusted catalog origin/version",
               "catalog_unavailable")
    try:
        encoded=json.dumps(snapshot["response"],ensure_ascii=False,
                           separators=(",",":"),allow_nan=False).encode("utf-8")
    except (TypeError,ValueError,UnicodeError,RecursionError):
        reject("catalog_snapshot","invalid JSON cached catalog",
               "catalog_unavailable")
    if len(encoded)>MAX_CATALOG_BYTES:
        reject("catalog_snapshot","oversized cached catalog",
               "catalog_unavailable")
    pin=sha256_digest(expected_sha,"catalog.expected_sha256")
    actual=sha256_digest(snapshot["response_sha256"],"catalog.response_sha256")
    if pin!=actual or actual!=fingerprint(snapshot["response"]):
        reject("catalog_snapshot","approved catalog SHA mismatch",
               "snapshot_mismatch")
    age_limit=positive(max_age_seconds,"max_age_seconds",
                       maximum=MAX_CATALOG_AGE_SECONDS)
    current=_utc(now if now is not None else datetime.now(timezone.utc))
    received=_utc(snapshot["captured_at"])
    elapsed=(current-received).total_seconds()
    if elapsed>age_limit or elapsed < -MAX_CLOCK_FUTURE_SECONDS:
        reject("catalog_snapshot","expired or future-dated catalog",
               "stale_catalog")
    payload=snapshot["response"]
    if type(payload) is not dict or type(payload.get("data")) is not dict:
        reject("catalog_snapshot","model endpoints response missing",
               "catalog_unavailable")
    return payload["data"],actual,received


def _eligible_endpoint(endpoint, model_revision):
    """Verify a SINGLE endpoint: no router fallback to weaker capability."""
    if type(endpoint) is not dict:
        reject("catalog.endpoint","invalid endpoint metadata",
               "catalog_unavailable")
    if (endpoint.get("model_id")!=MODEL_ID or
            type(endpoint.get("name")) is not str or
            not endpoint["name"].endswith(" | "+model_revision)):
        reject("catalog.endpoint","endpoint model revision not exact",
               "model_unavailable")
    if type(endpoint.get("status")) is not int or endpoint["status"]!=0:
        reject("catalog.endpoint","pinned provider endpoint offline",
               "model_unavailable")
    params=endpoint.get("supported_parameters")
    if (type(params) is not list or
            any(type(x) is not str for x in params) or
            not REQUIRED_JSON_PARAMETERS.issubset(params)):
        reject("catalog.endpoint","JSON schema / structured output unavailable",
               "model_capability_missing")
    ctx=positive(endpoint.get("context_length"),"catalog.context_length",
                 maximum=2000000)
    out=positive(endpoint.get("max_completion_tokens"),
                 "catalog.max_completion_tokens",maximum=200000)
    if ctx<=out+1024:
        reject("catalog.endpoint","no safe model input context",
               "model_capability_missing")
    prices=endpoint.get("pricing")
    if type(prices) is not dict:
        reject("catalog.pricing","missing pricing","catalog_price_missing")
    normalized={key:_amount(prices.get(key),"catalog.pricing."+key)
                for key in PRICE_KEYS}
    if Decimal(normalized["prompt"])<=0 or Decimal(normalized["completion"])<=0:
        reject("catalog.pricing","zero/negative billable token price",
               "catalog_price_missing")
    return ctx,out,normalized


def preflight_exact_opus_catalog(snapshot, *,
                                 operator_expected_sha256,
                                 operator_endpoint_tag,
                                 operator_model_revision,
                                 requested_max_output_tokens,
                                 requested_input_proxy,
                                 now=None,max_age_seconds=MAX_CATALOG_AGE_SECONDS):
    """Validated, fresh, price/capability-pinned quote-preflight BASE.

    Does not produce a quote, calculate actual tokens, promise 0 cost,
    authenticate account routing or make an Opus call.
    """
    if (type(operator_endpoint_tag) is not str or
            not 2<=len(operator_endpoint_tag)<=100 or
            any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-/_"
                for ch in operator_endpoint_tag)):
        reject("catalog.endpoint_tag","trusted specific provider tag required")
    if (type(operator_model_revision) is not str or
            not operator_model_revision.startswith(MODEL_ID+"-") or
            len(operator_model_revision)>130 or
            any(ch not in "abcdefghijklmnopqrstuvwxyz0123456789-./"
                for ch in operator_model_revision) or
            any(x in operator_model_revision.split("/")[-1]
                for x in ("latest","current","auto","floating"))):
        reject("catalog.model_revision","exact immutable Opus 5.5 revision required",
               "model_unavailable")
    data,digest,fetched_at=_snapshot(
        snapshot,operator_expected_sha256,now,max_age_seconds,
    )
    if data.get("id")!=MODEL_ID:
        reject("catalog.model_id","exact Opus 5.5 not present; NO fallback",
               "model_unavailable")
    endpoints=data.get("endpoints")
    if type(endpoints) is not list or not endpoints:
        reject("catalog.endpoints","provider endpoints missing",
               "model_unavailable")
    selected=[x for x in endpoints if type(x) is dict and
              x.get("tag")==operator_endpoint_tag]
    if len(selected)!=1:
        reject("catalog.endpoint","pinned endpoint missing or ambiguous",
               "model_unavailable")
    endpoint=selected[0]
    ctx,max_completion,pricing=_eligible_endpoint(
        endpoint,operator_model_revision,
    )
    output=positive(requested_max_output_tokens,"requested_max_output_tokens",
                    maximum=128000)
    budget=positive(requested_input_proxy,"requested_input_proxy",maximum=250000)
    if output>max_completion or budget<4096 or budget>ctx-output-1024:
        reject("catalog.budget","request does not fit exact endpoint",
               "token_budget_exceeded")
    # Reuse and verify the exact M20 contract shape; the provider's live
    # API does not promise that request temperature=0 is supported.
    pin=validate_catalog_pin({
        "schema_version":"sre-opus-catalog-pin/v1",
        "provider":"openrouter",
        "model_id":MODEL_ID,
        "model_revision":operator_model_revision,
        "catalog_revision":digest,
        "catalog_sha256":digest,
        "context_window_tokens":ctx,
        "max_output_tokens":output,
        "max_input_token_proxy":budget,
        "temperature_milli":0,
    })
    result={
        "schema_version":PREFLIGHT_VERSION,
        "status":"eligible_for_M23_quote_only",
        "provider":"openrouter",
        "model_id":MODEL_ID,
        "model_revision":operator_model_revision,
        "endpoint_tag":operator_endpoint_tag,
        "endpoint_name":endpoint["name"],
        "endpoint_provider_name":endpoint.get("provider_name"),
        "endpoint_status":0,
        "response_format_json_schema_supported":True,
        "structured_outputs_supported":True,
        "context_window_tokens":ctx,
        "max_completion_tokens":max_completion,
        "requested_max_output_tokens":output,
        "requested_input_token_proxy":budget,
        "catalog_source_url":CATALOG_URL,
        "catalog_captured_at":snapshot["captured_at"],
        "catalog_fresh_for_seconds":max_age_seconds,
        "catalog_sha256":digest,
        "pricing_usd_per_token":pricing,
        "m20_catalog_pin":pin,
        "routing":{
            "only":[operator_endpoint_tag],
            "allow_fallbacks":False,
        },
        "temperature_send_supported":"temperature" in endpoint["supported_parameters"],
        "token_count_exact":False,
        "requires_true_token_check":True,
        "requires_live_catalog_revalidation":True,
        "requires_provider_endpoint_enforcement":True,
        "dispatch_allowed":False,
        "quote_ready":False,
        "paid_calls":0,
    }
    result["preflight_sha256"]=fingerprint(result)
    return result


def preflight_exact_opus_batch(m20_manifest, snapshot, *,
                               operator_expected_sha256,
                               operator_endpoint_tag,
                               operator_model_revision,
                               now=None,
                               max_age_seconds=MAX_CATALOG_AGE_SECONDS):
    """Check exact M20 SHA/C9/request manifest against ONE price/catalog pin.

    Still NOT a financial quote. Future M23 must recompute this at quotation
    and store an immutable signed/authorized bound to batch+catalog hash.
    """
    from .opus_evidence_verifier import _manifest as verify_full_transport

    if type(m20_manifest) is not dict:
        reject("opus.batch","missing exact M20 manifest","snapshot_mismatch")
    try:
        verify_full_transport(m20_manifest)
    except (KeyError,IndexError,TypeError,ValueError,AttributeError,OverflowError):
        reject("opus.batch","corrupt nested M20 batch transport",
               "snapshot_mismatch")
    reqs=m20_manifest["requests"]
    if (not reqs or type(reqs[0]) is not dict or
            type(m20_manifest.get("input_token_budget_proxy")) is not int or
            type(m20_manifest.get("catalog_pin_sha256")) is not str):
        reject("opus.batch","invalid pinned catalog parameters",
               "snapshot_mismatch")
    outputs=set()
    for request in reqs:
        outputs.add(positive(request["max_output_tokens"],
                             "opus.request.max_output_tokens",maximum=128000))
    if len(outputs)!=1:
        reject("opus.batch","unequal per-request completion cap",
               "snapshot_mismatch")
    checked=preflight_exact_opus_catalog(
        snapshot,operator_expected_sha256=operator_expected_sha256,
        operator_endpoint_tag=operator_endpoint_tag,
        operator_model_revision=operator_model_revision,
        requested_max_output_tokens=next(iter(outputs)),
        requested_input_proxy=m20_manifest["input_token_budget_proxy"],
        now=now,max_age_seconds=max_age_seconds,
    )
    pin=checked["m20_catalog_pin"]
    if (m20_manifest.get("model_id")!=MODEL_ID or
            m20_manifest.get("model_revision")!=pin["model_revision"] or
            m20_manifest.get("catalog_revision")!=pin["catalog_revision"] or
            m20_manifest.get("catalog_sha256")!=pin["catalog_sha256"] or
            m20_manifest.get("catalog_pin_sha256")!=fingerprint(pin) or
            m20_manifest.get("token_count_exact") is not False or
            m20_manifest.get("requires_provider_preflight") is not True or
            m20_manifest.get("dispatch_allowed") is not False):
        reject("opus.batch","batch model/catalog/SHA differs from approved "
               "pinned Opus 5.5 catalog","snapshot_mismatch")
    result={
        "schema_version":PREFLIGHT_VERSION,
        "status":"eligible_for_M23_quote_only",
        "batch_manifest_sha256":m20_manifest["batch_manifest_sha256"],
        "model_id":MODEL_ID,
        "model_revision":pin["model_revision"],
        "catalog_sha256":pin["catalog_sha256"],
        "endpoint_tag":checked["endpoint_tag"],
        "catalog_preflight_sha256":checked["preflight_sha256"],
        "pricing_usd_per_token":checked["pricing_usd_per_token"],
        "requested_input_token_proxy":pin["max_input_token_proxy"],
        "requested_max_output_tokens":pin["max_output_tokens"],
        "request_count":len(reqs),
        "token_count_exact":False,
        "requires_live_catalog_revalidation":True,
        "quote_ready":False,
        "dispatch_allowed":False,
        "paid_calls":0,
    }
    result["batch_preflight_sha256"]=fingerprint(result)
    return result


__all__=(
    "MODEL_ID","CATALOG_URL","PREFLIGHT_VERSION","SNAPSHOT_VERSION",
    "capture_exact_opus_catalog","preflight_exact_opus_catalog",
    "preflight_exact_opus_batch",
)
