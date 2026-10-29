"""M26 SRE single-request immutable dispatch-intent transaction.

STRICTLY NO NETWORK and NO PROVIDER POST. Only a trusted host-owned operator
may record an intent for a real live worker with active M23/M24/M25 authority.
The single PostgreSQL transaction *commits* a reserved M15 witness, its
dispatch_intent transition and the sealed per-request M26 journal. We never
hold this DB transaction across an HTTP call. A successful return is NOT
permission to send: true billable token limits, account fees/taxes, fresh D1
send rights, session auth and a single-use M27 transport gate remain mandatory.

A committed intent is conservatively 'possibly charged' even when no bytes
were sent. Crash/timeout/replay cannot create a second paid attempt; M29 must
perform an audited reconciliation before any future charged retry.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from .opus_budget import _budget, _workspace, _money_text
from .opus_consent import (
    _actor, _authorized, _time, _enforce_trusted_db_clock,
    verify_live_sre_opus_consent,
)
from .opus_catalog_preflight import preflight_exact_opus_catalog
from .opus_request_packer import pack_live_opus_requests
from .v1_contracts import (
    canonical_uuid, positive, project_id, reject, sha256_digest,
)

VERSION = "sre-live-opus-dispatch-intent/v1"
MODEL_ID = "anthropic/claude-opus-5.5"

# A psycopg transaction nested inside an implicit/uncommitted caller
# transaction CANNOT prove that a later HTTP call observes durable intent.
# Require one top-level explicit autocommit-enabled physical DB connection.
def _committable(conn):
    info=getattr(conn,"info",None)
    status=getattr(getattr(info,"transaction_status",None),"name",None)
    if getattr(conn,"autocommit",None) is not True or status!="IDLE":
        reject("conn","dedicated idle autocommit PostgreSQL connection required",
               "dispatch_commit_not_guaranteed")


SQL_RUN_ITEM = """SELECT r.execution_mode,r.state,r.cancel_requested,
                        i.state,i.worker_token::text,i.lease_generation,
                        i.lease_until,i.video_id,i.content_version_id,
                        i.content_sha256
  FROM sre_live_items i JOIN sre_live_runs r
    ON (r.project_id,r.run_id)=(i.project_id,i.run_id)
  WHERE i.project_id=%s AND i.run_id=%s::uuid AND i.id=%s FOR UPDATE OF r,i"""
SQL_BATCH = """SELECT b.state FROM sre_live_batch_members m
  JOIN sre_live_batches b
    ON (b.project_id,b.run_id,b.batch_id)=
       (m.project_id,m.run_id,m.batch_id)
  WHERE m.project_id=%s AND m.run_id=%s::uuid AND m.item_id=%s
    AND m.batch_id=%s::uuid FOR SHARE OF b"""
SQL_RESERVE = """SELECT reservation_id::text,consent_id::text,amount_usd,state
  FROM sre_live_budget_reservations
  WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s
    AND quote_sha256=%s AND reservation_id=%s::uuid FOR UPDATE"""
SQL_QUOTE = """SELECT quote FROM sre_live_opus_quotes
  WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s
    AND quote_sha256=%s FOR SHARE"""
SQL_PRIOR = """SELECT attempt_id::text,state FROM sre_live_attempt_fences
  WHERE project_id=%s AND run_id=%s::uuid
    AND state IN ('dispatch_intent','unknown','completed','failed_known')
  LIMIT 1 FOR SHARE"""
SQL_PRIOR_M26 = """SELECT intent_id::text FROM sre_live_opus_dispatch_intents
  WHERE project_id=%s AND run_id=%s::uuid LIMIT 1 FOR SHARE"""
SQL_ATTEMPT_INSERT = """INSERT INTO sre_live_attempt_fences (
    attempt_id,project_id,run_id,item_id,batch_id,lease_generation,
    worker_token,request_sha256)
  VALUES (%s::uuid,%s,%s::uuid,%s,%s::uuid,%s,%s::uuid,%s)"""
SQL_MARK_ATTEMPT = """UPDATE sre_live_attempt_fences SET
    state='dispatch_intent',intent_at=clock_timestamp()
  WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s
    AND attempt_id=%s::uuid AND state='reserved'
  RETURNING intent_at"""
SQL_INTENT_INSERT = """INSERT INTO sre_live_opus_dispatch_intents (
    intent_id,project_id,run_id,item_id,batch_id,reservation_id,
    quote_sha256,consent_id,attempt_id,worker_token,lease_generation,
    request_ordinal,request_sha256,batch_manifest_sha256,model_id,
    model_revision,catalog_sha256,endpoint_tag,reserved_usd,operator_actor)
  VALUES (%s::uuid,%s,%s::uuid,%s,%s::uuid,%s::uuid,
          %s,%s::uuid,%s::uuid,%s::uuid,%s,
          %s,%s,%s,%s,%s,%s,%s,%s::numeric,%s)
  RETURNING intent_id::text,intent_at"""
SQL_INTENT_READ = """SELECT intent_id::text,attempt_id::text,
   request_ordinal,request_sha256,batch_manifest_sha256,reservation_id::text,
   quote_sha256,reserved_usd,intent_at
   FROM sre_live_opus_dispatch_intents
   WHERE project_id=%s AND run_id=%s::uuid AND item_id=%s
     AND intent_id=%s::uuid FOR SHARE"""


def _row_value(result,field):
    row=result.fetchone()
    if row is None:
        reject(field,"missing trusted LIVE database record","rights_denied")
    return row


def _lease(row,token,generation):
    mode,run_state,cancel,item_state,current,actual,deadline,video,cid,sha=row
    if (mode!="live" or run_state not in ("queued","running") or cancel is not False
            or item_state!="running" or current!=token or actual!=generation):
        reject("worker_token","current LIVE worker lease/cancel state invalid",
               "lease_lost")
    return video,cid,sha,deadline


def commit_live_sre_opus_dispatch_intent(
    conn, project, run_id, item_id, batch_id, reservation_id,
    quote_sha256, catalog_snapshot, *, request_ordinal,
    worker_token, lease_generation, actor_id, actor_authorizer,
    operator_expected_sha256, operator_endpoint_tag,
    operator_model_revision, operator_maximum_usd, now=None,
):
    """Commit exactly ONE M20 request intent; never perform its HTTP send.

    Fail-closed on nested transactions; one committed intent freezes the
    whole LIVE run before any possible paid retry (even after a crash).
    Future M27 must read this record with a new transaction/connection,
    independently enforce all pre-send gates and use single-use send fencing.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    bid=canonical_uuid(batch_id,"batch_id")
    res=canonical_uuid(reservation_id,"reservation_id")
    sha=sha256_digest(quote_sha256,"quote_sha256")
    token=canonical_uuid(worker_token,"worker_token")
    generation=positive(lease_generation,"lease_generation",
                        maximum=9223372036854775807)
    if type(request_ordinal) is not int or not 0<=request_ordinal<2048:
        reject("request_ordinal","request index outside exact M20 plan",
               "snapshot_mismatch")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"commit_sre_opus_dispatch_intent")
    moment=_time(now)
    _committable(conn)

    attempt=str(uuid4())
    intent=str(uuid4())
    with conn.transaction():
        _enforce_trusted_db_clock(conn,moment)
        _workspace(conn,pid)  # exclusive to serialize M15 cancellation/lease ops
        if conn.execute(SQL_PRIOR,(pid,rid)).fetchone() is not None or (
            conn.execute(SQL_PRIOR_M26,(pid,rid)).fetchone() is not None
        ):
            reject("attempt","previous possibly paid intent: reconcile first",
                   "unknown_paid_outcome")
        live=_row_value(conn.execute(SQL_RUN_ITEM,(pid,rid,iid)),"live_item")
        video,cid,content_sha,expires=_lease(live,token,generation)
        db_now=_row_value(conn.execute("SELECT clock_timestamp()"),"db_clock")[0]
        if expires<=db_now:
            reject("worker_token","lease expired before intent","lease_lost")
        batch=_row_value(conn.execute(SQL_BATCH,(pid,rid,iid,bid)),"batch")[0]
        if batch not in ("queued","running"):
            reject("batch_id","LIVE batch no longer available","lease_lost")
        # A new send boundary means a *new* mandatory D1 external permission
        # recheck; the earlier M12 planning gate is not enough.
        from .live_content_gate import read_live_content_gate
        right=read_live_content_gate(
            conn,pid,cid,phase="before_external_send",external_llm=True,
            pins={"video_id":video,"content_version_id":cid,
                  "content_sha256":content_sha},
        )
        if (right["content"]["project_id"]!=pid or
            right["content"]["video_id"]!=video or
            right["content"]["content_sha256"]!=content_sha):
            reject("content","D1 rights or exact content SHA changed",
                   "rights_denied")
        consent=verify_live_sre_opus_consent(
            conn,pid,rid,iid,sha,catalog_snapshot,
            operator_expected_sha256=operator_expected_sha256,
            operator_endpoint_tag=operator_endpoint_tag,
            operator_model_revision=operator_model_revision,
            operator_maximum_usd=operator_maximum_usd,now=moment,
        )
        reservation=_row_value(
            conn.execute(SQL_RESERVE,(pid,rid,iid,sha,res)),"reservation")
        if (reservation[0]!=res or reservation[1]!=consent["consent_id"]
                or reservation[3]!="reserved"
                or reservation[2]!=Decimal(consent["max_usd"])):
            reject("reservation","no exact active M25 USD reserve",
                   "budget_not_reserved")
        budget=_budget(conn,pid)
        if budget[1]<reservation[2]:
            reject("budget","funds no longer reserved","budget_invariant")
        quote=_row_value(conn.execute(SQL_QUOTE,(pid,rid,iid,sha)),"quote")[0]
        if type(quote) is not dict:
            reject("quote","invalid immutable M23 quote","snapshot_mismatch")
        preflight=preflight_exact_opus_catalog(
            catalog_snapshot,
            operator_expected_sha256=operator_expected_sha256,
            operator_endpoint_tag=operator_endpoint_tag,
            operator_model_revision=operator_model_revision,
            requested_max_output_tokens=quote["max_output_tokens_per_request"],
            requested_input_proxy=quote["requested_input_token_proxy"],
            now=moment,
        )
        manifest=pack_live_opus_requests(
            conn,pid,rid,iid,operator_catalog_pin=preflight["m20_catalog_pin"])
        if (manifest["batch_manifest_sha256"]!=quote["batch_manifest_sha256"]
                or manifest["request_count"]!=quote["request_count"]
                or quote["model_id"]!=MODEL_ID
                or quote["model_revision"]!=operator_model_revision
                or quote["endpoint_tag"]!=operator_endpoint_tag
                or quote["catalog_sha256"]!=manifest["catalog_sha256"]
                or quote["reserve_upper_bound_usd"]!=consent["max_usd"]):
            reject("quote","quote does not seal current complete M20 request batch",
                   "snapshot_mismatch")
        requests=manifest["requests"]
        if request_ordinal>=len(requests):
            reject("request_ordinal","unknown planned M20 request",
                   "snapshot_mismatch")
        request=requests[request_ordinal]
        if (request["request_ordinal"]!=request_ordinal
                or request["model_id"]!=MODEL_ID
                or request["dispatch_allowed"] is not False):
            reject("request","unsafe M20 request changed","snapshot_mismatch")
        req_sha=sha256_digest(request["request_sha256"],"request_sha256")
        # M15 technical intent and M26 immutable USD/request witness are
        # ONE atomic SQL commit. NO HTTP provider adapter is invoked here.
        conn.execute(SQL_ATTEMPT_INSERT,(
            attempt,pid,rid,iid,bid,generation,token,req_sha,
        ))
        _row_value(conn.execute(SQL_MARK_ATTEMPT,
                                (pid,rid,iid,attempt)),"attempt")
        saved=_row_value(conn.execute(SQL_INTENT_INSERT,(
            intent,pid,rid,iid,bid,res,sha,consent["consent_id"],
            attempt,token,generation,request_ordinal,req_sha,
            manifest["batch_manifest_sha256"],MODEL_ID,
            operator_model_revision,manifest["catalog_sha256"],
            operator_endpoint_tag,_money_text(reservation[2]),actor,
        )),"intent")
        if saved[0]!=intent:
            reject("intent_id","intent row differs before commit",
                   "snapshot_mismatch")
    # The outer with conn.transaction() has returned (real COMMIT). This
    # response is still NOT an OpenRouter send token or billed receipt.
    return {
        "schema_version":VERSION,
        "status":"committed_possibly_paid_no_transport",
        "intent_id":intent,"attempt_id":attempt,
        "project_id":pid,"run_id":rid,"item_id":iid,
        "reservation_id":res,"quote_sha256":sha,
        "request_ordinal":request_ordinal,"request_sha256":req_sha,
        "batch_manifest_sha256":manifest["batch_manifest_sha256"],
        "intent_at":saved[1].isoformat(),
        "reserved_upper_bound_usd":_money_text(reservation[2]),
        "intent_committed":True,
        "outcome":"unknown_until_authenticated_provider_receipt",
        "automatic_retry_allowed":False,
        "requires_new_transaction_before_send":True,
        "requires_exact_token_and_account_price_check":True,
        "provider_send_implemented":False,"dispatch_allowed":False,"paid_calls":0,
    }


def read_committed_live_sre_opus_intent(
    conn, project, run_id, item_id, intent_id,
):
    """Read the durable fence on a NEW idle autocommit connection.

    The returned record is for reconciliation/diagnostics, not direct
    permission to send. No rePOST based solely on finding this record.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    ident=canonical_uuid(intent_id,"intent_id")
    _committable(conn)
    with conn.transaction():
        row=_row_value(conn.execute(SQL_INTENT_READ,(pid,rid,iid,ident)),
                       "intent")
    if row[0]!=ident:
        reject("intent_id","mismatched persisted intent","snapshot_mismatch")
    return {
        "schema_version":VERSION,"status":"recorded_no_provider_receipt",
        "intent_id":ident,"attempt_id":row[1],
        "project_id":pid,"run_id":rid,"item_id":iid,
        "request_ordinal":row[2],"request_sha256":row[3],
        "batch_manifest_sha256":row[4],
        "reservation_id":row[5],"quote_sha256":row[6],
        "reserved_upper_bound_usd":_money_text(row[7]),
        "intent_at":row[8].isoformat(),
        "automatic_retry_allowed":False,
        "requires_exact_token_and_account_price_check":True,
        "provider_send_implemented":False,"dispatch_allowed":False,"paid_calls":0,
    }


__all__=(
    "VERSION","commit_live_sre_opus_dispatch_intent",
    "read_committed_live_sre_opus_intent",
)
