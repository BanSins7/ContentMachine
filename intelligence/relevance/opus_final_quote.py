"""M23: immutable, upper-bounded USD Opus quote AFTER exact M20 packaging.

No paid provider POST, no exact Opus token claim, no consent or budget reserve.
The strict upper bound uses the ENTIRE pinned endpoint context allowance, not
M20's byte proxy. This is deliberately pessimistic and conditional: the future
dispatcher MUST enforce exact provider token/context limits and reject unknown
non-token fees or price/endpoint drift before any charge.

A SHA seal is integrity/replay identity, NOT authorization. The PostgreSQL
port stores immutable quotes against real project LIVE run/item and DB-verified
C9/D1; owner identity still belongs to the host.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone, timedelta
from decimal import Decimal, ROUND_CEILING, localcontext

from .opus_catalog_preflight import (
    MAX_CATALOG_AGE_SECONDS, MODEL_ID, PRICE_KEYS,
    _utc, preflight_exact_opus_batch,
)
from .opus_request_packer import pack_live_opus_requests
from .v1_contracts import (
    canonical_uuid, fingerprint, positive, project_id, record, reject,
    sha256_digest,
)

VERSION = "sre-opus-final-quote/v1"
MAX_QUOTE_SECONDS = 300
USD_QUANT = Decimal("0.000000001")
USD_RE = re.compile(r"(?:0|[1-9][0-9]{0,11})(?:\.[0-9]{1,9})?\Z")
SQL_WORKSPACE = "SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR SHARE"
SQL_INSERT = """INSERT INTO sre_live_opus_quotes
 (project_id,run_id,item_id,batch_manifest_sha256,catalog_sha256,
  quote_sha256,min_usd,max_usd,issued_at,expires_at,quote)
 VALUES (%s,%s::uuid,%s,%s,%s,%s,%s::numeric,%s::numeric,
         %s::timestamptz,%s::timestamptz,%s::jsonb)
 ON CONFLICT DO NOTHING"""
SQL_READ = """SELECT quote,quote_sha256 FROM sre_live_opus_quotes
              WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s
                AND quote_sha256=%s"""


def _usd(value, path):
    """Money must be a finite plain decimal STRING; float/exponents denied."""
    if type(value) is not str or not USD_RE.fullmatch(value):
        reject(path,"expected nonnegative finite USD decimal string",
               "invalid_quote_amount")
    amount=Decimal(value)
    if not amount.is_finite() or amount<0:
        reject(path,"negative or nonfinite USD", "invalid_quote_amount")
    return amount


def _fixed(value):
    with localcontext() as ctx:
        ctx.prec=60
        return format(value.quantize(USD_QUANT,rounding=ROUND_CEILING),"f")


def _timestamp(dt):
    return dt.astimezone(timezone.utc).isoformat(timespec="microseconds").replace("+00:00","Z")


def build_final_opus_quote(m20_manifest, catalog_snapshot, *,
                           operator_expected_sha256,
                           operator_endpoint_tag,
                           operator_model_revision,
                           operator_maximum_usd,
                           quote_ttl_seconds=180,
                           now=None):
    """Pure quote; conservative max priced for ALL possible allowed tokens.

    The exact M20 batch, catalog SHA/prices and deterministic UTC issue time
    are immutable hash inputs. The quote is suitable for later SRE owner
    consent review ONLY after live-account prices/fees and token enforcement
    are independently established; it is NEVER direct dispatch authority.
    """
    maximum_allowed=_usd(operator_maximum_usd,"operator_maximum_usd")
    if maximum_allowed<=0:
        reject("operator_maximum_usd","positive operator spending cap required",
               "invalid_quote_amount")
    ttl=positive(quote_ttl_seconds,"quote_ttl_seconds",
                 maximum=MAX_QUOTE_SECONDS)
    issued=_utc(now if now is not None else datetime.now(timezone.utc))
    approved=preflight_exact_opus_batch(
        m20_manifest,catalog_snapshot,
        operator_expected_sha256=operator_expected_sha256,
        operator_endpoint_tag=operator_endpoint_tag,
        operator_model_revision=operator_model_revision,
        now=issued,
        max_age_seconds=MAX_CATALOG_AGE_SECONDS,
    )
    # Do not derive ANY payable token count from M20 UTF-8 byte proxy. A
    # full endpoint context ceiling is the only offline upper bound, and
    # even it depends on pre-send enforcement of actual provider limits.
    from .opus_catalog_preflight import preflight_exact_opus_catalog
    endpoint=preflight_exact_opus_catalog(
        catalog_snapshot,
        operator_expected_sha256=operator_expected_sha256,
        operator_endpoint_tag=operator_endpoint_tag,
        operator_model_revision=operator_model_revision,
        requested_max_output_tokens=approved["requested_max_output_tokens"],
        requested_input_proxy=approved["requested_input_token_proxy"],
        now=issued,
    )
    if endpoint["preflight_sha256"]!=approved["catalog_preflight_sha256"]:
        reject("opus.quote","model/catalog preflight drift","snapshot_mismatch")
    prices=endpoint["pricing_usd_per_token"]
    if set(prices)!=set(PRICE_KEYS):
        reject("opus.quote","incomplete pricing fields","catalog_price_missing")
    # Treat all cache-related rates as POSSIBLY ADDITIVE. This deliberately
    # over-reserves vs alternative cache bill modes, avoiding a false low
    # quote when the future implementation changes caching behavior.
    with localcontext() as ctx:
        ctx.prec=60
        input_price=sum((Decimal(prices[x]) for x in (
            "prompt","input_cache_read","input_cache_write",
            "input_cache_write_1h",
        )),Decimal(0))
        output_price=Decimal(prices["completion"])
        if input_price<=0 or output_price<=0:
            reject("opus.quote","missing safe token prices",
                   "catalog_price_missing")
        output_cap=positive(approved["requested_max_output_tokens"],
                            "quote.max_output_tokens",maximum=128000)
        context_cap=positive(endpoint["context_window_tokens"],
                             "quote.context_window_tokens",maximum=2000000)
        input_ceiling=context_cap-output_cap-1024
        if input_ceiling<=0:
            reject("opus.quote","no positive bounded input context",
                   "token_budget_exceeded")
        rows=[]
        total_max=Decimal(0)
        for request in m20_manifest["requests"]:
            if (request["max_output_tokens"]!=output_cap or
                    request["model_id"]!=MODEL_ID or
                    request["catalog_sha256"]!=approved["catalog_sha256"]):
                reject("opus.quote","request cap or price/model SHA changed",
                       "snapshot_mismatch")
            upper=Decimal(input_ceiling)*input_price+Decimal(output_cap)*output_price
            total_max+=upper
            rows.append({
                "request_ordinal":request["request_ordinal"],
                "request_sha256":request["request_sha256"],
                "category_key":request["category_key"],
                "input_token_lower_bound":0,
                "input_token_upper_bound":input_ceiling,
                "output_token_lower_bound":0,
                "output_token_upper_bound":output_cap,
                "upper_usd":_fixed(upper),
                "exact_input_token_count_known":False,
                "actual_paid_dispatch_permitted":False,
            })
        reserve_usd=_fixed(total_max)
        if Decimal(reserve_usd)>maximum_allowed:
            reject("opus.quote","worst-case reservation exceeds operator cap; "
                   "no silent reduction or paid fallback",
                   "quote_budget_exceeded")
    catalog_time=_utc(catalog_snapshot["captured_at"])
    remaining=(catalog_time+timedelta(seconds=MAX_CATALOG_AGE_SECONDS)-issued).total_seconds()
    effective_ttl=min(ttl,int(remaining))
    if effective_ttl<=0:
        reject("opus.quote","catalog/quote already expired","quote_expired")
    expires=issued+timedelta(seconds=effective_ttl)
    result={
        "schema_version":VERSION,
        "status":"issued_immutable_upper_bound_not_approved",
        "currency":"USD",
        "project_id":project_id(m20_manifest["project_id"]),
        "publication_id":m20_manifest["publication_id"],
        "video_id":m20_manifest["video_id"],
        "content_version_id":m20_manifest["content_version_id"],
        "content_sha256":m20_manifest["content_sha256"],
        "source_manifest_sha256":m20_manifest["source_manifest_sha256"],
        "batch_manifest_sha256":m20_manifest["batch_manifest_sha256"],
        "batch_preflight_sha256":approved["batch_preflight_sha256"],
        "catalog_preflight_sha256":approved["catalog_preflight_sha256"],
        "catalog_sha256":approved["catalog_sha256"],
        "model_id":MODEL_ID,
        "model_revision":operator_model_revision,
        "endpoint_tag":operator_endpoint_tag,
        "request_count":len(rows),
        "hypothesis_count":m20_manifest["hypothesis_count"],
        "expected_hypothesis_keys":list(m20_manifest["expected_hypothesis_keys"]),
        "usd_price_per_token":dict(prices),
        "worst_case_input_rate_usd_per_token":format(input_price,"f"),
        "max_output_rate_usd_per_token":format(output_price,"f"),
        "input_token_upper_bound_per_request":input_ceiling,
        "max_output_tokens_per_request":output_cap,
        "requested_input_token_proxy":approved["requested_input_token_proxy"],
        "total_input_token_upper_bound":input_ceiling*len(rows),
        "total_output_token_upper_bound":output_cap*len(rows),
        "token_count_exact":False,
        "token_bound_method":"full_pinned_endpoint_context_minus_output_and_headroom",
        "min_total_usd":"0.000000000",
        "max_total_usd":reserve_usd,
        "reserve_upper_bound_usd":reserve_usd,
        "operator_maximum_usd":_fixed(maximum_allowed),
        "per_request":rows,
        "created_at":_timestamp(issued),
        "expires_at":_timestamp(expires),
        "expires_in_seconds_at_issue":int((expires-issued).total_seconds()),
        "requires_exact_token_check_before_send":True,
        "requires_account_price_and_extra_fee_check":True,
        "requires_current_d1_rights":True,
        "requires_dedicated_sre_consent":True,
        "requires_atomic_budget_reservation":True,
        "requires_live_catalog_revalidation":True,
        "provider_receipts_authenticated":False,
        "dispatch_allowed":False,
        "paid_calls":0,
    }
    result["quote_sha256"]=fingerprint(result)
    return result


def verify_final_opus_quote(quote, m20_manifest, catalog_snapshot, *,
                            operator_expected_sha256,
                            operator_endpoint_tag,
                            operator_model_revision,
                            operator_maximum_usd,
                            now=None):
    """Rebuild full quote from exact pinned inputs and stored issue timestamp.

    Caller MUST fetch an immutable DB-owned quote; a self-hashed user JSON is
    not authority. This is a read-only signatureless integrity check, NOT
    permission to send or a substitute for the future consent service.
    """
    if type(quote) is not dict:
        reject("quote","missing immutable quote","snapshot_mismatch")
    record(quote,"quote",required=(
        "schema_version","status","currency","project_id",
        "publication_id","video_id","content_version_id","content_sha256",
        "source_manifest_sha256","batch_manifest_sha256",
        "batch_preflight_sha256","catalog_preflight_sha256","catalog_sha256",
        "model_id","model_revision","endpoint_tag","request_count",
        "hypothesis_count","expected_hypothesis_keys","usd_price_per_token",
        "worst_case_input_rate_usd_per_token","max_output_rate_usd_per_token",
        "input_token_upper_bound_per_request","max_output_tokens_per_request",
        "requested_input_token_proxy","total_input_token_upper_bound","total_output_token_upper_bound",
        "token_count_exact","token_bound_method","min_total_usd",
        "max_total_usd","reserve_upper_bound_usd","operator_maximum_usd",
        "per_request","created_at","expires_at","expires_in_seconds_at_issue",
        "requires_exact_token_check_before_send",
        "requires_account_price_and_extra_fee_check","requires_current_d1_rights",
        "requires_dedicated_sre_consent","requires_atomic_budget_reservation",
        "requires_live_catalog_revalidation","provider_receipts_authenticated",
        "dispatch_allowed","paid_calls","quote_sha256",
    ))
    sha256_digest(quote["quote_sha256"],"quote_sha256")
    if quote["quote_sha256"]!=fingerprint({
        k:v for k,v in quote.items() if k!="quote_sha256"
    }):
        reject("quote","immutable quote digest mismatch","snapshot_mismatch")
    current=_utc(now if now is not None else datetime.now(timezone.utc))
    started=_utc(quote["created_at"])
    until=_utc(quote["expires_at"])
    if current>=until or current<started-timedelta(seconds=30):
        reject("quote","expired or future forged quote","quote_expired")
    ttl=positive(quote["expires_in_seconds_at_issue"],
                 "quote.expires_in_seconds_at_issue",
                 maximum=MAX_QUOTE_SECONDS)
    if (until-started).total_seconds()!=ttl:
        reject("quote","expiry duration mismatch","snapshot_mismatch")
    expected=build_final_opus_quote(
        m20_manifest,catalog_snapshot,
        operator_expected_sha256=operator_expected_sha256,
        operator_endpoint_tag=operator_endpoint_tag,
        operator_model_revision=operator_model_revision,
        operator_maximum_usd=operator_maximum_usd,
        quote_ttl_seconds=ttl,now=started,
    )
    if expected!=quote:
        reject("quote","quote does not match actual manifest, model, prices, "
               "source or operator cap","snapshot_mismatch")
    # Freshness at VERIFICATION time: rebuilding with created_at is not
    # sufficient when a cached public endpoint snapshot is now stale.
    age=(current-_utc(catalog_snapshot["captured_at"])).total_seconds()
    if age>MAX_CATALOG_AGE_SECONDS or age < -30:
        reject("quote","underlying endpoint catalog is stale","stale_catalog")
    return {
        "project_id":quote["project_id"],
        "quote_sha256":quote["quote_sha256"],
        "batch_manifest_sha256":quote["batch_manifest_sha256"],
        "expires_at":quote["expires_at"],
        "reserve_upper_bound_usd":quote["reserve_upper_bound_usd"],
        "quote_verified":True,
        "dispatch_allowed":False,
        "consent_granted":False,
        "paid_calls":0,
    }


def issue_live_opus_quote(conn, project, run_id, item_id,
                          catalog_snapshot, *, operator_expected_sha256,
                          operator_endpoint_tag, operator_model_revision,
                          operator_maximum_usd, quote_ttl_seconds=180,
                          requested_max_output_tokens=4096,
                          requested_input_proxy=50000,now=None):
    """Persist once under PostgreSQL immutable project/run/item FK.

    Does not grant consent, reserve money or perform a paid provider call.
    Workspace SHARE synchronizes with source/cancellation actions; caller's
    trusted host authenticates project owner independently.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    from .opus_catalog_preflight import preflight_exact_opus_catalog
    with conn.transaction():
        if conn.execute(SQL_WORKSPACE,(pid,)).fetchone() is None:
            reject("project_id","SRE workspace inaccessible","rights_denied")
        model_pin=preflight_exact_opus_catalog(
            catalog_snapshot,
            operator_expected_sha256=operator_expected_sha256,
            operator_endpoint_tag=operator_endpoint_tag,
            operator_model_revision=operator_model_revision,
            requested_max_output_tokens=requested_max_output_tokens,
            requested_input_proxy=requested_input_proxy,
            now=now,
        )["m20_catalog_pin"]
        batch=pack_live_opus_requests(
            conn,pid,rid,iid,operator_catalog_pin=model_pin,
        )
        quote=build_final_opus_quote(
            batch,catalog_snapshot,
            operator_expected_sha256=operator_expected_sha256,
            operator_endpoint_tag=operator_endpoint_tag,
            operator_model_revision=operator_model_revision,
            operator_maximum_usd=operator_maximum_usd,
            quote_ttl_seconds=quote_ttl_seconds,now=now,
        )
        import json
        conn.execute(SQL_INSERT,(
            pid,rid,iid,quote["batch_manifest_sha256"],
            quote["catalog_sha256"],quote["quote_sha256"],
            quote["min_total_usd"],quote["max_total_usd"],
            quote["created_at"],quote["expires_at"],
            json.dumps(quote,ensure_ascii=False,separators=(",",":")),
        ))
        actual=conn.execute(SQL_READ,(pid,rid,iid,quote["quote_sha256"])).fetchone()
        if actual is None or actual[0]!=quote or actual[1]!=quote["quote_sha256"]:
            reject("quote","missing or divergent immutable DB quote",
                   "snapshot_mismatch")
        return quote


SQL_QUOTE_LOCK = """SELECT quote,quote_sha256,expires_at
                    FROM sre_live_opus_quotes
                    WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s
                      AND quote_sha256=%s
                    FOR SHARE"""


def read_live_opus_quote(conn, project, run_id, item_id, quote_sha256,
                         catalog_snapshot, *, operator_expected_sha256,
                         operator_endpoint_tag,operator_model_revision,
                         operator_maximum_usd,now=None):
    """Read real immutable DB quote and rebuild live pinned C9/D1 + M20.

    This intentionally does NOT grant/verify M24 consent or any permission to
    spend. Host must authenticate project and operator configuration.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    digest=sha256_digest(quote_sha256,"quote_sha256")
    moment=_utc(now if now is not None else datetime.now(timezone.utc))
    from .opus_catalog_preflight import preflight_exact_opus_catalog
    with conn.transaction():
        if conn.execute(SQL_WORKSPACE,(pid,)).fetchone() is None:
            reject("project_id","SRE workspace inaccessible","rights_denied")
        row=conn.execute(SQL_QUOTE_LOCK,(pid,rid,iid,digest)).fetchone()
        if row is None:
            reject("quote_sha256","unknown or foreign immutable quote",
                   "rights_denied")
        quote,stored_hash,expires_at=row
        if stored_hash!=digest or expires_at<=moment:
            reject("quote_sha256","quote expired or mismatched",
                   "quote_expired")
        pin=preflight_exact_opus_catalog(
            catalog_snapshot,
            operator_expected_sha256=operator_expected_sha256,
            operator_endpoint_tag=operator_endpoint_tag,
            operator_model_revision=operator_model_revision,
            requested_max_output_tokens=quote["max_output_tokens_per_request"],
            requested_input_proxy=positive(
                quote["requested_input_token_proxy"],
                "quote.requested_input_token_proxy",maximum=250000,
            ),
            now=moment,
        )["m20_catalog_pin"]
        m20=pack_live_opus_requests(
            conn,pid,rid,iid,operator_catalog_pin=pin,
        )
        verdict=verify_final_opus_quote(
            quote,m20,catalog_snapshot,
            operator_expected_sha256=operator_expected_sha256,
            operator_endpoint_tag=operator_endpoint_tag,
            operator_model_revision=operator_model_revision,
            operator_maximum_usd=operator_maximum_usd,
            now=moment,
        )
        return verdict


__all__=(
    "VERSION","build_final_opus_quote","verify_final_opus_quote",
    "issue_live_opus_quote","read_live_opus_quote",
)
