"""M24 SRE-only explicit per-quote spending consent and one-way revocation.

This is a TRUSTED INTERNAL service, not an HTTP route. The host MUST prove
the current authenticated project actor and capture a fresh explicit human
confirmation (quote SHA, exact USD upper bound and LIVE Opus stage). A client-
provided actor_id, project_id, checkbox or self-hashed document is NOT authority.
No provider API, payment, ledger reservation or run-state mutation here.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4

from .opus_catalog_preflight import _utc
from .opus_final_quote import (
    _usd, read_live_opus_quote,
    SQL_WORKSPACE,
)
from .v1_contracts import (
    canonical_uuid, fingerprint, positive, project_id, record, reject,
    sha256_digest,
)

VERSION = "sre-live-opus-consent/v1"
STAGE = "sre_live_opus_paid_execution"
GRANT_ACTION = "authorize_sre_spending"
REVOKE_ACTION = "revoke_sre_spending"
MAX_CONSENT_SECONDS = 300
ACTOR_RE = re.compile(r"[A-Za-z0-9:_@.-]{1,128}\Z")
REVOKE_REASONS = frozenset((
    "user_revoked", "operator_revoked", "security_revoked",
))

RUN_LOCK = """SELECT state,cancel_requested
              FROM sre_live_runs
              WHERE project_id=%s AND run_id=%s::uuid FOR UPDATE"""
QUOTE_LOCK = """SELECT quote,max_usd,expires_at
                FROM sre_live_opus_quotes
                WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s
                  AND quote_sha256=%s FOR SHARE"""
CONSENT_READ = """SELECT consent_id::text,actor_id,max_usd,granted_at,expires_at,
                         confirmation_sha256,revoked_at,revoked_by,revoke_reason,
                         consent_stage
                  FROM sre_live_opus_consents
                  WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s
                    AND quote_sha256=%s FOR UPDATE"""
CONSENT_VERIFY = """SELECT consent_id::text,actor_id,max_usd,granted_at,
                           expires_at,confirmation_sha256,revoked_at,
                           consent_stage
                    FROM sre_live_opus_consents
                    WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s
                      AND quote_sha256=%s FOR SHARE"""
CONSENT_INSERT = """INSERT INTO sre_live_opus_consents
  (consent_id,project_id,run_id,item_id,quote_sha256,actor_id,consent_stage,
   max_usd,granted_at,expires_at,confirmation_sha256)
   VALUES (%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s::numeric,
           %s::timestamptz,%s::timestamptz,%s)
   ON CONFLICT DO NOTHING"""
CONSENT_REVOKE = """UPDATE sre_live_opus_consents
                    SET revoked_at=%s::timestamptz,revoked_by=%s,
                        revoke_reason=%s
                    WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s
                      AND quote_sha256=%s AND revoked_at IS NULL
                    RETURNING consent_id::text"""


def _actor(value):
    if type(value) is not str or ACTOR_RE.fullmatch(value) is None:
        reject("actor_id","nonempty authenticated actor identifier required",
               "actor_not_authorized")
    return value


def _authorized(authorizer, pid, actor, permission):
    """Required host-supplied trusted owner/permission check; fail closed."""
    if not callable(authorizer):
        reject("actor_authorizer","trusted project/actor authorization missing",
               "actor_not_authorized")
    try:
        permitted=authorizer(pid,actor,permission)
    except Exception:
        reject("actor_authorizer","actor permission check unavailable",
               "actor_not_authorized")
    if permitted is not True:
        reject("actor_authorizer","actor cannot authorize this SRE operation",
               "actor_not_authorized")


def confirmation_for_quote(quote_sha256, usd):
    """Exact fields the authenticated confirmation UI must display/echo.

    Returning this *template* is NOT an approval; caller must receive the
    user's fresh confirmation through the trusted, CSRF-protected host.
    """
    sha=sha256_digest(quote_sha256,"quote_sha256")
    limit=_usd(usd,"confirmation.max_usd")
    if limit<=0:
        reject("confirmation.max_usd","positive USD cap required",
               "invalid_quote_amount")
    return {
        "action":GRANT_ACTION,"stage":STAGE,
        "quote_sha256":sha,
        "max_usd":format(limit,".9f"),
    }


def _confirmation(value, quote_sha256, quote_max):
    record(value,"explicit_confirmation",required=(
        "action","stage","quote_sha256","max_usd",
    ))
    expected=confirmation_for_quote(quote_sha256,quote_max)
    if value!=expected:
        reject("explicit_confirmation",
               "user did not expressly approve exact SRE stage/quote/USD limit",
               "consent_not_explicit")
    return fingerprint(value)


def _time(value=None):
    return _utc(value if value is not None else datetime.now(timezone.utc))


def _stamp(t):
    return t.isoformat(timespec="microseconds").replace("+00:00","Z")


def _run_state(conn,pid,rid, *,grant):
    run=conn.execute(RUN_LOCK,(pid,rid)).fetchone()
    if run is None:
        reject("run_id","foreign or missing SRE LIVE run","rights_denied")
    allowed=("pending_consent",) if grant else (
        "pending_consent","queued","running",
    )
    if run[0] not in allowed or run[1] is not False:
        reject("run_id","SRE run state/cancellation blocks consent",
               "consent_run_blocked")


def grant_live_sre_opus_consent(
    conn, project, run_id, item_id, quote_sha256, catalog_snapshot, *,
    actor_id, actor_authorizer, explicit_confirmation,
    operator_expected_sha256, operator_endpoint_tag,
    operator_model_revision, operator_maximum_usd,
    consent_ttl_seconds=180, now=None,
):
    """Explicit trusted-owner grant, project/quote/USD/stage scoped.

    No implicit consent on quote creation. One grant per quote: revocation
    is irreversible even on retry; a new quote must be issued for regrant.
    No run scheduling/budget/Opus API POST at this stage.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    digest=sha256_digest(quote_sha256,"quote_sha256")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"grant_sre_opus_consent")
    ttl=positive(consent_ttl_seconds,"consent_ttl_seconds",
                 maximum=MAX_CONSENT_SECONDS)
    moment=_time(now)
    with conn.transaction():
        if conn.execute(SQL_WORKSPACE,(pid,)).fetchone() is None:
            reject("project_id","workspace inaccessible","rights_denied")
        _run_state(conn,pid,rid,grant=True)
        verdict=read_live_opus_quote(
            conn,pid,rid,iid,digest,catalog_snapshot,
            operator_expected_sha256=operator_expected_sha256,
            operator_endpoint_tag=operator_endpoint_tag,
            operator_model_revision=operator_model_revision,
            operator_maximum_usd=operator_maximum_usd,now=moment,
        )
        if verdict["quote_sha256"]!=digest or verdict["quote_verified"] is not True:
            reject("quote_sha256","quote not verified","snapshot_mismatch")
        row=conn.execute(QUOTE_LOCK,(pid,rid,iid,digest)).fetchone()
        if row is None:
            reject("quote_sha256","foreign or deleted quote","rights_denied")
        quote,amount,expires=row
        if (type(quote) is not dict or
                quote.get("quote_sha256")!=digest or
                quote.get("currency")!="USD" or
                quote.get("status")!="issued_immutable_upper_bound_not_approved" or
                Decimal(quote["reserve_upper_bound_usd"])!=amount):
            reject("quote_sha256","untrusted quote amount","snapshot_mismatch")
        value=format(amount,".9f")
        signature=_confirmation(explicit_confirmation,digest,value)
        expiry=min(moment+timedelta(seconds=ttl),expires)
        if expiry<=moment:
            reject("consent_expires_at","no time left on quote",
                   "consent_expired")
        consent_id=str(uuid4())
        conn.execute(CONSENT_INSERT,(
            consent_id,pid,rid,iid,digest,actor,STAGE,value,
            _stamp(moment),_stamp(expiry),signature,
        ))
        saved=conn.execute(CONSENT_READ,(pid,rid,iid,digest)).fetchone()
        if saved is None:
            reject("consent","consent row not written","consent_denied")
        existing_id,who,max_usd,issued,deadline,seal,revoked,_,_,stage=saved
        if (who!=actor or max_usd!=amount or seal!=signature or stage!=STAGE):
            reject("consent","same quote already approved differently",
                   "idempotency_conflict")
        if revoked is not None:
            reject("consent","consent was revoked and cannot be regranted",
                   "consent_revoked")
        if deadline<=moment:
            reject("consent","consent expired","consent_expired")
        return {
            "schema_version":VERSION,
            "status":"active_consent_not_budget_reserved",
            "consent_id":existing_id,
            "project_id":pid,"run_id":rid,"item_id":iid,
            "quote_sha256":digest,
            "actor_id":who,
            "stage":STAGE,
            "max_usd":value,
            "granted_at":_stamp(issued),
            "expires_at":_stamp(deadline),
            "confirmation_sha256":seal,
            "consent_granted":True,
            "budget_reserved":False,
            "dispatch_allowed":False,
            "paid_calls":0,
        }


def verify_live_sre_opus_consent(
    conn, project, run_id, item_id, quote_sha256, catalog_snapshot, *,
    operator_expected_sha256, operator_endpoint_tag,
    operator_model_revision, operator_maximum_usd,now=None,
):
    """Read trusted stored consent with active quote/rights/price recheck.

    No actor authorizer here: an internal future worker must use its own
    authenticated workspace/lease gate; this alone NEVER authorizes dispatch.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    digest=sha256_digest(quote_sha256,"quote_sha256")
    moment=_time(now)
    with conn.transaction():
        if conn.execute(SQL_WORKSPACE,(pid,)).fetchone() is None:
            reject("project_id","workspace inaccessible","rights_denied")
        _run_state(conn,pid,rid,grant=False)
        quote=read_live_opus_quote(
            conn,pid,rid,iid,digest,catalog_snapshot,
            operator_expected_sha256=operator_expected_sha256,
            operator_endpoint_tag=operator_endpoint_tag,
            operator_model_revision=operator_model_revision,
            operator_maximum_usd=operator_maximum_usd,now=moment,
        )
        row=conn.execute(CONSENT_VERIFY,(pid,rid,iid,digest)).fetchone()
        if row is None:
            reject("consent","explicit SRE quote consent absent","consent_missing")
        consent_id,actor,limit,granted,expires,confirmation,revoked,stage=row
        if revoked is not None:
            reject("consent","original owner consent revoked","consent_revoked")
        if expires<=moment or granted>moment+timedelta(seconds=30):
            reject("consent","owner consent expired or future-dated",
                   "consent_expired")
        if (stage!=STAGE or limit!=_usd(quote["reserve_upper_bound_usd"],
                                      "quote.reserve_upper_bound_usd") or
                confirmation!=fingerprint(confirmation_for_quote(digest,quote[
                    "reserve_upper_bound_usd"]))):
            reject("consent","consent stage/amount does not match live quote",
                   "snapshot_mismatch")
        return {
            "schema_version":VERSION,
            "status":"active_consent_not_budget_reserved",
            "consent_id":consent_id,
            "project_id":pid,"run_id":rid,"item_id":iid,
            "actor_id":actor,
            "quote_sha256":digest,
            "stage":STAGE,"max_usd":format(limit,".9f"),
            "expires_at":_stamp(expires),
            "consent_granted":True,
            "budget_reserved":False,
            "dispatch_allowed":False,
            "paid_calls":0,
        }


def revoke_live_sre_opus_consent(
    conn, project, run_id, item_id, quote_sha256, *,
    actor_id, actor_authorizer, reason="user_revoked",now=None,
):
    """Explicit one-way revocation allowed even after source/run pauses.

    A normal caller must authenticate the project and actor independently.
    No C/Query consent tables are ever referenced.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    digest=sha256_digest(quote_sha256,"quote_sha256")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"revoke_sre_opus_consent")
    if type(reason) is not str or reason not in REVOKE_REASONS:
        reject("revoke_reason","invalid explicit revocation reason")
    moment=_time(now)
    with conn.transaction():
        if conn.execute(SQL_WORKSPACE,(pid,)).fetchone() is None:
            reject("project_id","workspace inaccessible","rights_denied")
        row=conn.execute(CONSENT_READ,(pid,rid,iid,digest)).fetchone()
        if row is None:
            reject("consent","foreign/missing consent","rights_denied")
        consent_id,grantor,_,granted,_,_,revoked,revoker,old_reason,stage=row
        if stage!=STAGE:
            reject("consent","not a dedicated SRE consent","consent_denied")
        if revoked is None:
            if moment<granted:
                reject("consent","revocation time precedes grant","consent_denied")
            found=conn.execute(CONSENT_REVOKE,(
                _stamp(moment),actor,reason,pid,rid,iid,digest,
            )).fetchone()
            if found is None or found[0]!=consent_id:
                reject("consent","failed to persist revocation","consent_denied")
        return {
            "schema_version":VERSION,"status":"revoked",
            "consent_id":consent_id,"project_id":pid,
            "run_id":rid,"item_id":iid,"quote_sha256":digest,
            "original_actor_id":grantor,
            "revoked_by":revoker or actor,
            "revoke_reason":old_reason or reason,
            "consent_granted":False,"budget_reserved":False,
            "dispatch_allowed":False,"paid_calls":0,
        }


__all__=(
    "VERSION","STAGE","GRANT_ACTION",
    "confirmation_for_quote","grant_live_sre_opus_consent",
    "verify_live_sre_opus_consent","revoke_live_sre_opus_consent",
)
