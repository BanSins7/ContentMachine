"""M25 SRE-only Decimal project budget and atomic pre-dispatch reservation.

Trusted internal service ports only. No provider POST, token charge, ledger
settlement, worker trigger, public HTTP mutation or payment capability.
Budget is absent/disabled until an authenticated project owner explicitly
configures a fixed cap. SRE M24 consent != budget; reservation != dispatch.
"""
from __future__ import annotations

from uuid import uuid4
from decimal import Decimal

from .opus_final_quote import _usd
from .opus_consent import (
    _actor, _authorized, _time, _stamp, _enforce_trusted_db_clock,
    verify_live_sre_opus_consent,
)
from .v1_contracts import canonical_uuid, project_id, positive, reject, sha256_digest

VERSION = "sre-live-budget-ledger/v1"
# Account lock MUST be taken after the workspace EXCLUSIVE lock. M15 attempt
# and revoke coordination uses the same workspace first.
SQL_WORKSPACE_LOCK = "SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR UPDATE"
SQL_BUDGET_CREATE = """INSERT INTO sre_live_budget_accounts
    (project_id,cap_usd,configured_actor) VALUES (%s,%s::numeric,%s)
    ON CONFLICT DO NOTHING"""
SQL_ACCOUNT = """SELECT cap_usd,reserved_usd,spent_usd,released_usd,configured_actor
    FROM sre_live_budget_accounts WHERE project_id=%s FOR UPDATE"""
SQL_EXISTING = """SELECT reservation_id::text,consent_id::text,amount_usd,
    state FROM sre_live_budget_reservations
    WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s
      AND quote_sha256=%s FOR UPDATE"""
SQL_ITEM_ACTIVE = """SELECT reservation_id::text FROM sre_live_budget_reservations
    WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s AND state='reserved'
    FOR UPDATE"""
SQL_INSERT = """INSERT INTO sre_live_budget_reservations
    (reservation_id,project_id,run_id,item_id,quote_sha256,consent_id,amount_usd)
    VALUES (%s::uuid,%s,%s::uuid,%s,%s,%s::uuid,%s::numeric)
    ON CONFLICT DO NOTHING"""
SQL_RELEASE_LOOKUP = """SELECT reservation_id::text,run_id::text,item_id,
    quote_sha256,amount_usd,state,release_actor,release_reason
    FROM sre_live_budget_reservations
    WHERE project_id=%s AND reservation_id=%s::uuid FOR UPDATE"""
SQL_RELEASE = """UPDATE sre_live_budget_reservations
    SET state='released', released_at=%s::timestamptz,
        release_actor=%s,release_reason=%s
    WHERE project_id=%s AND reservation_id=%s::uuid AND state='reserved'
    RETURNING reservation_id::text"""
POST_INTENT = """SELECT 1 FROM sre_live_attempt_fences
    WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s
      AND state IN ('dispatch_intent','unknown','completed','failed_known')
    LIMIT 1"""
RELEASE_REASONS = frozenset((
    "user_cancelled","operator_cancelled","consent_revoked",
    "rights_revoked","expired_pre_dispatch",
))


def _money(value, field, *, positive_only=True):
    amount=_usd(value,field)
    if positive_only and amount<=0:
        reject(field,"positive USD decimal cap required","invalid_budget_amount")
    return amount


def _money_text(value):
    return format(value,".9f")


def _workspace(conn,pid):
    if conn.execute(SQL_WORKSPACE_LOCK,(pid,)).fetchone() is None:
        reject("project_id","trusted SRE workspace unavailable","rights_denied")


def _budget(conn,pid):
    row=conn.execute(SQL_ACCOUNT,(pid,)).fetchone()
    if row is None:
        reject("budget","explicit owner-configured SRE budget missing",
               "budget_not_configured")
    cap,reserved,spent,released,who=row
    if any(type(v) is not Decimal or v<0 for v in (cap,reserved,spent,released)):
        reject("budget","invalid exact database accounting","budget_invariant")
    if cap<=0 or reserved+spent>cap:
        reject("budget","inconsistent budget counters","budget_invariant")
    return row


def _balance(pid,row):
    cap,reserved,spent,released,who=row
    return {
        "schema_version":VERSION, "project_id":pid,
        "cap_usd":_money_text(cap),
        "reserved_usd":_money_text(reserved),
        "spent_usd":_money_text(spent),
        "released_usd":_money_text(released),
        "available_usd":_money_text(cap-reserved-spent),
        "configured_actor":who,
        "dispatch_allowed":False,"paid_calls":0,
    }


def configure_live_sre_budget(
    conn, project, *, actor_id, actor_authorizer, project_cap_usd, now=None,
):
    """One explicit owner-set cap, immutable for M25. No silent replenishment.

    Changing/recharging a cap requires a later audited owner-budget operation;
    a replay with a different cap is denied even if no reserve exists.
    """
    pid=project_id(project)
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"configure_sre_budget")
    maximum=_money(project_cap_usd,"project_cap_usd")
    moment=_time(now)
    with conn.transaction():
        _enforce_trusted_db_clock(conn,moment)
        _workspace(conn,pid)
        conn.execute(SQL_BUDGET_CREATE,(pid,_money_text(maximum),actor))
        row=_budget(conn,pid)
        if row[0]!=maximum or row[4]!=actor:
            reject("budget","immutable configured cap/actor mismatch",
                   "budget_cap_conflict")
        return {"status":"configured_not_spendable",**_balance(pid,row)}


def reserve_live_sre_opus_budget(
    conn, project, run_id, item_id, quote_sha256, catalog_snapshot, *,
    actor_id, actor_authorizer,
    operator_expected_sha256, operator_endpoint_tag,
    operator_model_revision, operator_maximum_usd, now=None,
):
    """Reserve exactly ONE consent's M23 worst-case total for one LIVE item.

    PostgreSQL AFTER INSERT atomically increments account counters under a
    project row lock and enforces cap, including direct SQL and competing
    transactions. No quotes or consent can be created here.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    sha=sha256_digest(quote_sha256,"quote_sha256")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"reserve_sre_opus_budget")
    moment=_time(now)
    with conn.transaction():
        _enforce_trusted_db_clock(conn,moment)
        _workspace(conn,pid)
        consent=verify_live_sre_opus_consent(
            conn,pid,rid,iid,sha,catalog_snapshot,
            operator_expected_sha256=operator_expected_sha256,
            operator_endpoint_tag=operator_endpoint_tag,
            operator_model_revision=operator_model_revision,
            operator_maximum_usd=operator_maximum_usd,now=moment,
        )
        amount=_money(consent["max_usd"],"consent.max_usd")
        account=_budget(conn,pid)
        if conn.execute(POST_INTENT,(pid,rid,iid)).fetchone():
            reject("reservation","possibly paid attempt prohibits new reserve",
                   "unknown_paid_outcome")
        existing=conn.execute(SQL_EXISTING,(pid,rid,iid,sha)).fetchone()
        if existing is None:
            if conn.execute(SQL_ITEM_ACTIVE,(pid,rid,iid)).fetchone() is not None:
                reject("item_id","another active quote reserves this item",
                       "budget_conflict")
            if amount>account[0]-account[1]-account[2]:
                reject("budget","insufficient unspent/unreserved project cap",
                       "budget_exceeded")
            conn.execute(SQL_INSERT,(
                str(uuid4()),pid,rid,iid,sha,consent["consent_id"],
                _money_text(amount),
            ))
            existing=conn.execute(SQL_EXISTING,(pid,rid,iid,sha)).fetchone()
        if (existing is None or existing[1]!=consent["consent_id"] or
            existing[2]!=amount):
            reject("reservation","consent-bound reserve collision",
                   "budget_conflict")
        if existing[3]!="reserved":
            reject("reservation","released consent cannot be re-reserved",
                   "reservation_released")
        return {
            "status":"reserved_no_dispatch",
            "reservation_id":existing[0],
            "run_id":rid,"item_id":iid,"quote_sha256":sha,
            "consent_id":consent["consent_id"],
            "reserved_upper_bound_usd":_money_text(amount),
            "budget_reserved":True,
            "dispatch_allowed":False,"paid_calls":0,
            "account":_balance(pid,_budget(conn,pid)),
        }


def release_pre_dispatch_sre_budget(
    conn, project, reservation_id, *, actor_id, actor_authorizer,
    reason="user_cancelled", now=None,
):
    """One-way release only when *no* possibly paid intent exists.

    Does not require an active/unexpired consent: expiry/revocation may need
    safe refund. A dispatch_intent or UNKNOWN witness retains the USD hold.
    """
    pid=project_id(project)
    res=canonical_uuid(reservation_id,"reservation_id")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"release_sre_opus_budget")
    if type(reason) is not str or reason not in RELEASE_REASONS:
        reject("reason","unsupported audited pre-dispatch release reason",
               "invalid_budget_release")
    moment=_time(now)
    with conn.transaction():
        _enforce_trusted_db_clock(conn,moment)
        _workspace(conn,pid)
        _budget(conn,pid)
        row=conn.execute(SQL_RELEASE_LOOKUP,(pid,res)).fetchone()
        if row is None:
            reject("reservation_id","foreign/missing budget reserve",
                   "rights_denied")
        _,run_id,item_id,_,amount,state,old_actor,old_reason=row
        if state=="reserved":
            if conn.execute(POST_INTENT,(pid,run_id,item_id)).fetchone():
                reject("reservation_id","unknown/post-send funds cannot be freed",
                       "unknown_paid_outcome")
            changed=conn.execute(SQL_RELEASE,(
                _stamp(moment),actor,reason,pid,res,
            )).fetchone()
            if changed is None:
                reject("reservation_id","pre-dispatch release race",
                       "budget_conflict")
            old_actor,old_reason=actor,reason
        return {
            "schema_version":VERSION,
            "status":"released_pre_dispatch",
            "reservation_id":res,"project_id":pid,
            "released_usd":_money_text(amount),
            "release_actor":old_actor,"release_reason":old_reason,
            "dispatch_allowed":False,"paid_calls":0,
            "account":_balance(pid,_budget(conn,pid)),
        }


__all__=(
    "VERSION","configure_live_sre_budget","reserve_live_sre_opus_budget",
    "release_pre_dispatch_sre_budget",
)
