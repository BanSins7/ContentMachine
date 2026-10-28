"""M29 fail-stop reconciliation of possibly paid OpenRouter Opus requests.

There is intentionally NO public or private paid POST entry point here.
An UNKNOWN request WITHOUT a provider generation ID cannot be proven unpaid:
it stays frozen with the M25 reserve. An ID permits a separately authenticated
M28 generation GET and one receipt, never a second inference request.
Only final receipt => financial state known, NOT semantic completion or
permission to continue LIVE automatic worker dispatch. All decisions are
audited in additive immutable PostgreSQL 063.

The M26 one-intent-per-run schema and M14 no-automatic-reclaim constraints
remain enforced. M29 returns the remaining source-pinned request ordinals
as a conservative read-only continuation plan; true multiple paid requests
requires a later audited per-request reservation and fresh worker boundary.
"""
from __future__ import annotations

from .opus_consent import _actor, _authorized
from .opus_dispatch_intent import _committable
from .opus_receipt import (
    _generation_id, settle_live_sre_opus_generation,
)
from .v1_contracts import (
    SREContractError, canonical_uuid, fingerprint, positive, project_id, reject,
    sha256_digest,
)

VERSION="sre-opus-unknown-reconciliation/v1"

SQL_WORKSPACE_LOCK="SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR UPDATE"
SQL_SOURCE="""SELECT i.intent_id::text,i.attempt_id::text,i.request_sha256,
  i.request_ordinal,i.batch_manifest_sha256,i.reservation_id::text,
  i.quote_sha256,i.catalog_sha256,i.reserved_usd,
  r.state,r.amount_usd,
  t.receipt_id::text,t.generation_id,t.charged_usd,
  q.quote
  FROM sre_live_opus_dispatch_intents i
  JOIN sre_live_budget_reservations r
    ON r.project_id=i.project_id AND r.reservation_id=i.reservation_id
  JOIN sre_live_opus_quotes q
    ON q.project_id=i.project_id AND q.run_id=i.run_id
     AND q.item_id=i.item_id AND q.quote_sha256=i.quote_sha256
  LEFT JOIN sre_live_opus_receipts t
    ON t.project_id=i.project_id AND t.intent_id=i.intent_id
  WHERE i.project_id=%s AND i.run_id=%s::uuid AND i.item_id=%s
    AND i.intent_id=%s::uuid
  FOR UPDATE OF r"""
SQL_AUDIT_INSERT="""INSERT INTO sre_live_opus_reconciliation_events(
    project_id,run_id,item_id,intent_id,event_kind,generation_id,
    receipt_id,actor_id,reason_code)
  VALUES(%s,%s::uuid,%s,%s::uuid,%s,%s,%s::uuid,%s,%s)
  ON CONFLICT DO NOTHING RETURNING event_id"""
SQL_AUDIT_FIND="""SELECT event_id,recorded_at
  FROM sre_live_opus_reconciliation_events
  WHERE project_id=%s AND intent_id=%s::uuid AND event_kind=%s
    AND generation_id IS NOT DISTINCT FROM %s ORDER BY event_id LIMIT 1"""
SQL_PROGRESS="""SELECT i.item_id,i.intent_id::text,i.request_ordinal,
    i.request_sha256,r.state,t.receipt_id::text,t.generation_id,
    t.charged_usd,q.quote
  FROM sre_live_opus_dispatch_intents i
  JOIN sre_live_budget_reservations r
    ON r.project_id=i.project_id AND r.reservation_id=i.reservation_id
  JOIN sre_live_opus_quotes q
    ON q.project_id=i.project_id AND q.run_id=i.run_id
   AND q.item_id=i.item_id AND q.quote_sha256=i.quote_sha256
  LEFT JOIN sre_live_opus_receipts t
    ON t.project_id=i.project_id AND t.intent_id=i.intent_id
  WHERE i.project_id=%s AND i.run_id=%s::uuid
  ORDER BY i.item_id,i.request_ordinal"""
SQL_RUN="""SELECT state,cancel_requested FROM sre_live_runs
  WHERE project_id=%s AND run_id=%s::uuid"""


def _scope(conn,pid):
    if conn.execute(SQL_WORKSPACE_LOCK,(pid,)).fetchone() is None:
        reject("project_id","trusted SRE workspace missing","rights_denied")


def _get(conn,pid,rid,iid,ident):
    row=conn.execute(SQL_SOURCE,(pid,rid,iid,ident)).fetchone()
    if row is None:
        reject("intent_id","M26 intent absent/foreign","rights_denied")
    (intent_id,attempt,req_sha,ordinal,batch_sha,res_id,quote_sha,catalog_sha,
     held,res_state,res_amount,receipt_id,gen_id,charge,quote)=row
    if (intent_id!=ident or res_state not in ("reserved","settled")
            or res_amount!=held or type(quote) is not dict
            or quote.get("quote_sha256")!=quote_sha
            or quote.get("batch_manifest_sha256")!=batch_sha
            or quote.get("catalog_sha256")!=catalog_sha
            or quote.get("project_id")!=pid
            or type(quote.get("per_request")) is not list
            or type(ordinal) is not int
            or not 0<=ordinal<len(quote["per_request"])
            or quote["per_request"][ordinal].get("request_sha256")!=req_sha):
        reject("reconciliation","original run/quote/reservation fingerprint invalid",
               "snapshot_mismatch")
    if (receipt_id is None and res_state!="reserved") or (
        receipt_id is not None and res_state!="settled"):
        reject("reconciliation","receipt and reservation state diverged",
               "budget_invariant")
    return {
        "intent_id":intent_id,"attempt_id":attempt,"request_sha256":req_sha,
        "request_ordinal":ordinal,"batch_manifest_sha256":batch_sha,
        "reservation_id":res_id,"quote_sha256":quote_sha,
        "reserved_usd":str(held),"reservation_state":res_state,
        "receipt_id":receipt_id,"generation_id":gen_id,
        "charged_usd":str(charge) if charge is not None else None,
        "quote":quote,
    }


def _write_audit(conn,pid,rid,iid,ident,actor,kind,gid,receipt_id):
    reasons={
        "unknown_no_generation_id":"no_generation_id",
        "generation_lookup_unverified":"generation_not_confirmed",
        "generation_accounted":"generation_receipt_confirmed",
    }
    if kind not in reasons:
        reject("reconciliation.event_kind","unsupported audit event")
    conn.execute(SQL_AUDIT_INSERT,(
        pid,rid,iid,ident,kind,gid,receipt_id,actor,reasons[kind],
    ))
    result=conn.execute(SQL_AUDIT_FIND,(pid,ident,kind,gid)).fetchone()
    if result is None:
        reject("reconciliation","immutable audit event not committed",
               "budget_invariant")
    return result[0]


def _audit(conn,pid,rid,iid,ident,actor,kind,gid):
    _committable(conn)
    with conn.transaction():
        _scope(conn,pid)
        state=_get(conn,pid,rid,iid,ident)
        if state["receipt_id"] is not None:
            if gid is not None and gid!=state["generation_id"]:
                reject("generation_id","a different known paid generation exists",
                       "receipt_already_settled")
            kind="generation_accounted"
            gid=state["generation_id"]
            receipt_id=state["receipt_id"]
        else:
            receipt_id=None
            if kind=="generation_accounted":
                reject("generation","no authenticated M28 ledger receipt",
                       "generation_unverified")
        event_id=_write_audit(
            conn,pid,rid,iid,ident,actor,kind,gid,receipt_id)
        return {
            "schema_version":VERSION,
            "project_id":pid,"run_id":rid,"item_id":iid,
            "intent_id":ident,"request_sha256":state["request_sha256"],
            "event_id":event_id,
            "status":("generation_accounted_still_no_repost"
                      if receipt_id else "unknown_reserved_no_repost"),
            "reason_code":kind,
            "generation_id":gid,
            "receipt_id":receipt_id,
            "held_upper_bound_usd":state["reserved_usd"],
            "actual_provider_cost_usd":state["charged_usd"],
            "original_reservation_state":state["reservation_state"],
            "automatic_paid_retry_allowed":False,
            "next_paid_request_allowed":False,
            "classification_results_written":0,
            "paid_post_calls_issued_here":0,
        }


def reconcile_live_sre_opus_outcome(
    conn,project,run_id,item_id,intent_id,*,actor_id,
    actor_authorizer,generation_id=None,api_key=None,
    provider_response=None,catalog_snapshot=None,operator_expected_sha256=None,
):
    """Reconcile a possibly paid M26 intent without making ANY paid POST.

    No generation_id -> immutable UNKNOWN audit, reserve held, no charge
    asserted. Known id -> M28 authentic read-only HTTPS GET; a missing
    completion may be safely accounted but no classification is inferred.
    Even an authenticated receipt never silently restarts a cancelled run.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    ident=canonical_uuid(intent_id,"intent_id")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"reconcile_sre_opus_outcome")
    _committable(conn)
    if generation_id is not None:
        gid=_generation_id(generation_id)
    else:
        gid=None
        if provider_response is not None or api_key is not None:
            reject("generation_id","response/credentials cannot prove unknown generation",
                   "generation_unverified")

    # Query project-scoped intent FIRST. Do not leak privileged generation
    # metadata to a caller that owns no such M26 witness.
    with conn.transaction():
        _scope(conn,pid)
        state=_get(conn,pid,rid,iid,ident)
    if state["receipt_id"] is not None:
        return _audit(conn,pid,rid,iid,ident,actor,
                      "generation_accounted",gid)
    if gid is None:
        return _audit(conn,pid,rid,iid,ident,actor,
                      "unknown_no_generation_id",None)

    # Caller with generation id but no host key is NOT proof of no charge.
    if api_key is None:
        reject("api_key","trusted generation GET credential required",
               "credentials_missing")
    try:
        settle_live_sre_opus_generation(
            conn,pid,rid,iid,ident,provider_response,gid,
            api_key=api_key,actor_id=actor,actor_authorizer=actor_authorizer,
            catalog_snapshot=catalog_snapshot,
            operator_expected_sha256=operator_expected_sha256,
        )
    except SREContractError as exc:
        if exc.code not in (
            "generation_unverified","provider_outcome_unknown",
            "invalid_provider_receipt","provider_cost_exceeds_reserve",
            "provider_token_mismatch","account_fee_unverified",
            "budget_not_reserved","receipt_already_settled",
        ):
            raise
        # Fail closed even after HTTP 404/timeouts and over-budget metadata.
        # If a concurrent reconciler settled it, the audit will see the
        # immutable receipt and report the original generation instead.
        return _audit(conn,pid,rid,iid,ident,actor,
                      "generation_lookup_unverified",gid)
    return _audit(conn,pid,rid,iid,ident,actor,
                  "generation_accounted",gid)


def inspect_live_sre_opus_reconciliation(
    conn,project,run_id,*,actor_id,actor_authorizer,
):
    """Read-only source-pinned per-request progress, NEVER a paid-send plan.

    One M26 intent/run is enforced by SQL 061. Remaining M20 ordinals are
    informative and NOT implicit permission to re-reserve, restart or POST.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"read_sre_opus_reconciliation")
    _committable(conn)
    with conn.transaction():
        if conn.execute(
            "SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR SHARE",
            (pid,)).fetchone() is None:
            reject("project_id","SRE workspace missing","rights_denied")
        parent=conn.execute(SQL_RUN,(pid,rid)).fetchone()
        if parent is None:
            reject("run_id","foreign LIVE run","rights_denied")
        records=conn.execute(SQL_PROGRESS,(pid,rid)).fetchall()
        if len(records)>1:
            reject("run_id","M26 immutable one-per-run fence broken",
                   "budget_invariant")
        if not records:
            return {
                "schema_version":VERSION,"project_id":pid,"run_id":rid,
                "run_state":parent[0],"cancel_requested":parent[1],
                "status":"no_committed_paid_intent",
                "known_request_count":None,"accounted_request_ordinals":[],
                "remaining_request_ordinals":[],
                "outstanding_unknown_intents":0,
                "all_hypotheses_complete":False,
                "next_paid_request_allowed":False,
                "automatic_paid_retry_allowed":False,"paid_post_calls_issued_here":0,
            }
        (item,intent,ordinal,sha,res_state,receipt,gid,charged,quote)=records[0]
        if (type(quote) is not dict or type(quote.get("per_request")) is not list
                or quote.get("request_count")!=len(quote["per_request"])
                or not 0<=ordinal<len(quote["per_request"])
                or quote["per_request"][ordinal].get("request_sha256")!=sha):
            reject("quote","immutable original request set invalid",
                   "snapshot_mismatch")
        accounted=[ordinal] if receipt is not None and res_state=="settled" else []
        unaccounted=0 if accounted else 1
        all_requested=list(range(len(quote["per_request"])))
        remaining=[i for i in all_requested if i not in accounted]
        return {
            "schema_version":VERSION,"project_id":pid,"run_id":rid,
            "run_state":parent[0],"cancel_requested":parent[1],
            "status":("one_generation_accounted_incomplete"
                      if accounted else "one_unknown_intent_funds_held"),
            "known_request_count":len(all_requested),
            "accounted_request_ordinals":accounted,
            "remaining_request_ordinals":remaining,
            "outstanding_unknown_intents":unaccounted,
            "verified_generation_id":gid if accounted else None,
            "confirmed_spent_usd":str(charged) if accounted else None,
            "all_hypotheses_complete":False,
            "next_paid_request_allowed":False,
            "automatic_paid_retry_allowed":False,
            "paid_post_calls_issued_here":0,
        }


__all__=(
    "VERSION","reconcile_live_sre_opus_outcome",
    "inspect_live_sre_opus_reconciliation",
)
