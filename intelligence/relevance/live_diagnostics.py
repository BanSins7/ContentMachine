"""M31 SRE-only privacy-minimal run diagnostics, exact USD and audit snapshots.

No provider requests, no source text/prompt/keys/LLM reasons in responses or
persistent checkpoints. The host authenticates project actors. A snapshot
distinguishes M28 billable inference from M30 evidence-backed decisions,
M26 outstanding paid uncertainty and M25 funds retained.

A checkpoint is *not* a financial receipt or a promise of account-wide costs.
Read-only report and trace never mutate SQL. The checkpoint INSERT is
independently revalidated by forward-only 065 PostgreSQL triggers.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import uuid4

from .opus_consent import _actor, _authorized
from .opus_dispatch_intent import _committable
from .v1_contracts import (
    canonical_uuid, fingerprint, positive, project_id, reject, sha256_digest,
)

VERSION = "sre-live-diagnostics/v1"
_ZERO = Decimal("0.000000000")
_MAX_PAIRS = 480000
_STATE_PAIRS = frozenset(("not_evaluated","blocked_rights","blocked_budget",
                          "match","no_match","uncertain"))
_STATE_ITEMS = frozenset((
    "not_evaluated","queued","running","waiting_source","paused_budget",
    "paused_rights","unknown","partial","completed","failed","cancelled",
))
_EVENT_KINDS = frozenset((
    "reserved","released","retention_released","settled",
))
_RECON_KINDS = frozenset((
    "unknown_no_generation_id","generation_lookup_unverified",
    "generation_accounted",
))

SQL_RUN = """SELECT state,cancel_requested,expected_items,finished_items,
    blocked_items,execution_mode
    FROM sre_live_runs WHERE project_id=%s AND run_id=%s::uuid"""
SQL_ITEMS = """SELECT state,count(*)::integer FROM sre_live_items
    WHERE project_id=%s AND run_id=%s::uuid GROUP BY state ORDER BY state"""
SQL_EXPECT = """SELECT count(*)::integer,
    coalesce(sum(jsonb_array_length(expected_hypothesis_keys)),0)::integer
    FROM sre_live_items WHERE project_id=%s AND run_id=%s::uuid"""
SQL_PAIRS = """SELECT state,count(*)::integer FROM sre_live_expected_pairs
    WHERE project_id=%s AND run_id=%s::uuid GROUP BY state ORDER BY state"""
SQL_PROVEN = """SELECT count(*)::integer,
    count(*) FILTER (WHERE p.category_key IS NOT NULL
       AND p.state=v.decision
       AND t.generation_authenticated IS TRUE
       AND t.evidence_status='validated_untrusted_output'
       AND t.evidence_sha256=v.verified_chunk_sha256)::integer
    FROM sre_live_verified_pair_results v
    LEFT JOIN sre_live_expected_pairs p
      ON (p.project_id,p.run_id,p.item_id,p.category_key)=
         (v.project_id,v.run_id,v.item_id,v.category_key)
    LEFT JOIN sre_live_opus_receipts t
      ON t.project_id=v.project_id AND t.receipt_id=v.receipt_id
    WHERE v.project_id=%s AND v.run_id=%s::uuid"""
SQL_INTENTS = """SELECT count(*)::integer,
    count(*) FILTER (WHERE t.receipt_id IS NULL)::integer
    FROM sre_live_opus_dispatch_intents i
    LEFT JOIN sre_live_opus_receipts t
      ON t.project_id=i.project_id AND t.intent_id=i.intent_id
    WHERE i.project_id=%s AND i.run_id=%s::uuid"""
SQL_RECEIPTS = """SELECT count(*)::integer,coalesce(sum(charged_usd),0)
    FROM sre_live_opus_receipts
    WHERE project_id=%s AND run_id=%s::uuid"""
SQL_RESERVES = """SELECT state,count(*)::integer,coalesce(sum(amount_usd),0)
    FROM sre_live_budget_reservations
    WHERE project_id=%s AND run_id=%s::uuid GROUP BY state ORDER BY state"""
SQL_BUDGET_EVENTS = """SELECT e.event_kind,count(*)::integer
    FROM sre_live_budget_events e
    JOIN sre_live_budget_reservations r
      ON r.project_id=e.project_id AND r.reservation_id=e.reservation_id
    WHERE r.project_id=%s AND r.run_id=%s::uuid
    GROUP BY e.event_kind ORDER BY e.event_kind"""
SQL_RECON_EVENTS = """SELECT event_kind,count(*)::integer
    FROM sre_live_opus_reconciliation_events
    WHERE project_id=%s AND run_id=%s::uuid
    GROUP BY event_kind ORDER BY event_kind"""
SQL_PROJECT_BUDGET = """SELECT cap_usd,reserved_usd,spent_usd,released_usd
    FROM sre_live_budget_accounts WHERE project_id=%s"""
SQL_WORKSPACE_SHARE = """SELECT 1 FROM sre_workspaces
    WHERE project_id=%s"""
SQL_WORKSPACE_EXCLUSIVE = """SELECT 1 FROM sre_workspaces
    WHERE project_id=%s FOR UPDATE"""
SQL_TRACE = """SELECT i.state,p.state,v.decision,v.result_id::text,
    v.verified_chunk_sha256,t.receipt_id::text,t.request_sha256,
    t.input_tokens,t.output_tokens,t.charged_usd,
    t.evidence_status,t.generation_authenticated,
    d.intent_id::text,d.request_ordinal,d.model_id,d.catalog_sha256
    FROM sre_live_items i
    JOIN sre_live_expected_pairs p
      ON (p.project_id,p.run_id,p.item_id)=
         (i.project_id,i.run_id,i.id) AND p.category_key=%s::uuid
    LEFT JOIN sre_live_verified_pair_results v
      ON (v.project_id,v.run_id,v.item_id,v.category_key)=
         (p.project_id,p.run_id,p.item_id,p.category_key)
    LEFT JOIN sre_live_opus_receipts t
      ON t.project_id=v.project_id AND t.receipt_id=v.receipt_id
    LEFT JOIN sre_live_opus_dispatch_intents d
      ON d.project_id=t.project_id AND d.intent_id=t.intent_id
    WHERE i.project_id=%s AND i.run_id=%s::uuid AND i.id=%s"""
SQL_INSERT = """INSERT INTO sre_live_diagnostic_checkpoints(
    checkpoint_id,project_id,run_id,actor_id,snapshot_sha256,
    run_state,cancel_requested,expected_items,physical_items,
    expected_pairs,physical_pairs,proven_pairs,dispatched_intents,
    unsettled_intents,confirmed_receipts,reserved_usd,inference_spent_usd,
    finance_event_count,reconciliation_event_count,alert_code)
    VALUES(%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
           %s::numeric,%s::numeric,%s,%s,%s)
    ON CONFLICT DO NOTHING RETURNING checkpoint_id::text"""
SQL_EXISTING = """SELECT checkpoint_id::text
    FROM sre_live_diagnostic_checkpoints
    WHERE project_id=%s AND run_id=%s::uuid AND snapshot_sha256=%s"""


def _count(n, name, *, maximum=_MAX_PAIRS):
    if type(n) is not int or not 0 <= n <= maximum:
        reject(name, "out-of-range integral database count", "budget_invariant")
    return n


def _usd(n, name):
    if type(n) is not Decimal or not n.is_finite() or n < _ZERO:
        reject(name, "invalid exact PostgreSQL USD value", "budget_invariant")
    if n.as_tuple().exponent < -9 or n >= Decimal("1000000000000000"):
        reject(name, "unbounded or overprecise USD", "budget_invariant")
    return format(n,".9f")


def _states(rows, allowed, name, limit):
    if type(rows) is not list:
        reject(name, "missing trusted grouped ledger rows", "budget_invariant")
    seen={}
    for state,n in rows:
        if type(state) is not str or state not in allowed or state in seen:
            reject(name, "unknown/duplicated ledger state", "budget_invariant")
        seen[state]=_count(n,name,maximum=limit)
    return {key:seen[key] for key in sorted(seen)}


def _alert(summary):
    c=summary["counts"]
    if c["unsettled_intents"]:
        return "unknown_paid_outcome"
    if (c["physical_items"]!=c["expected_items"]
            or c["physical_pairs"]!=c["expected_pairs"]):
        return "incomplete_pair_registry"
    if c["confirmed_receipts"]>c["proven_pairs"]:
        return "cost_without_complete_evidence"
    if (summary["pair_states"].get("blocked_rights",0)
            or summary["pair_states"].get("blocked_budget",0)):
        return "blocked_rights_or_budget"
    if c["proven_pairs"]<c["expected_pairs"]:
        return "incomplete_classification"
    return "no_alert"


def _snapshot(conn,pid,rid):
    """Strict whitelist from SRE tables only; no SELECT of prompts/JSON."""
    row=conn.execute(SQL_RUN,(pid,rid)).fetchone()
    if row is None or row[5]!="live":
        reject("run_id","foreign or non-LIVE run","rights_denied")
    state,cancel,expected,finished,blocked,_=row
    if (type(state) is not str or type(cancel) is not bool):
        reject("run_id","invalid LIVE status","budget_invariant")
    exp=_count(expected,"expected_items",maximum=10000)
    if not 1<=exp<=10000:
        reject("expected_items","invalid planned item count","budget_invariant")
    fin=_count(finished,"finished_items",maximum=10000)
    blk=_count(blocked,"blocked_items",maximum=10000)
    if fin+blk>exp:
        reject("run","invalid finished/blocked child counts","budget_invariant")
    items=_states(conn.execute(SQL_ITEMS,(pid,rid)).fetchall(),
                  _STATE_ITEMS,"item_states",10000)
    (physical_items,expected_pairs)=conn.execute(
        SQL_EXPECT,(pid,rid)).fetchone()
    physical_items=_count(physical_items,"physical_items",maximum=10000)
    expected_pairs=_count(expected_pairs,"expected_pairs")
    if sum(items.values())!=physical_items or physical_items>exp:
        reject("items","physical item count mismatch","incomplete_coverage")
    pairs=_states(conn.execute(SQL_PAIRS,(pid,rid)).fetchall(),
                  _STATE_PAIRS,"pair_states",_MAX_PAIRS)
    physical_pairs=sum(pairs.values())
    if expected_pairs>_MAX_PAIRS or physical_pairs>expected_pairs:
        reject("pairs","physical pair matrix mismatch","incomplete_coverage")
    all_proof,valid_proof=conn.execute(SQL_PROVEN,(pid,rid)).fetchone()
    all_proof=_count(all_proof,"all_result_proofs")
    valid_proof=_count(valid_proof,"proven_pairs")
    if all_proof!=valid_proof or valid_proof>physical_pairs:
        reject("receipt","invalid or duplicated M30 evidence proof",
               "invalid_evidence")
    evaluated=sum(pairs.get(k,0) for k in ("match","no_match","uncertain"))
    if evaluated!=valid_proof:
        reject("pairs","evaluated decision lacks genuine M30 proof",
               "invalid_evidence")
    dispatched,unsettled=conn.execute(SQL_INTENTS,(pid,rid)).fetchone()
    dispatched=_count(dispatched,"dispatched_intents")
    unsettled=_count(unsettled,"unsettled_intents")
    receipts,spent=conn.execute(SQL_RECEIPTS,(pid,rid)).fetchone()
    receipts=_count(receipts,"confirmed_receipts")
    spent_usd=_usd(spent,"inference_spent_usd")
    if receipts>dispatched or unsettled!=dispatched-receipts:
        reject("provider","M26/M28 receipt count inconsistent",
               "budget_invariant")
    reserves=conn.execute(SQL_RESERVES,(pid,rid)).fetchall()
    reserved_count=0
    reserved=_ZERO
    for state,count,money in reserves:
        if state not in ("reserved","settled","released"):
            reject("reserve","untrusted terminal reserve state","budget_invariant")
        _count(count,"reservation_count")
        _usd(money,"reservation_total_usd")
        if state=="reserved":
            reserved+=money
            reserved_count+=count
    if unsettled>reserved_count:
        reject("reserve","possibly paid M26 intent without frozen funds",
               "budget_invariant")
    reserve_usd=_usd(reserved,"reserved_usd")
    budget_events=_states(conn.execute(
        SQL_BUDGET_EVENTS,(pid,rid)).fetchall(),
        _EVENT_KINDS,"finance_events",_MAX_PAIRS)
    reconciliation_events=_states(conn.execute(
        SQL_RECON_EVENTS,(pid,rid)).fetchall(),
        _RECON_KINDS,"reconciliation_events",_MAX_PAIRS)
    counts={
        "expected_items":exp,
        "physical_items":physical_items,
        "finished_items":fin,
        "blocked_items":blk,
        "expected_pairs":expected_pairs,
        "physical_pairs":physical_pairs,
        "proven_pairs":valid_proof,
        "unproven_pairs":expected_pairs-valid_proof,
        "dispatched_intents":dispatched,
        "unsettled_intents":unsettled,
        "confirmed_receipts":receipts,
        "active_reservations":reserved_count,
        "finance_event_count":sum(budget_events.values()),
        "reconciliation_event_count":sum(reconciliation_events.values()),
    }
    summary={
        "schema_version":VERSION,
        "project_id":pid,"run_id":rid,
        "run_state":state,"cancel_requested":cancel,
        "item_states":items,"pair_states":pairs,
        "finance_events":budget_events,
        "reconciliation_events":reconciliation_events,
        "counts":counts,
        "run_reserved_usd":reserve_usd,
        "run_confirmed_inference_spent_usd":spent_usd,
    }
    summary["alert_code"]=_alert(summary)
    summary["snapshot_sha256"]=fingerprint(summary)
    return summary


def _public(summary):
    return {
        **summary,
        "provider_fee_tax_proven":False,
        "semantic_ground_truth_proven":False,
        "run_auto_resume_allowed":False,
        "paid_post_allowed":False,
        "paid_post_calls_issued_here":0,
        "source_text_or_key_in_report":False,
    }


def read_sre_live_diagnostics(conn,project,run_id,*,actor_id,actor_authorizer):
    """Repeatable-read, read-only, project-scoped diagnostic view."""
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"read_sre_live_diagnostics")
    _committable(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if conn.execute(SQL_WORKSPACE_SHARE,(pid,)).fetchone() is None:
            reject("project_id","SRE workspace missing","rights_denied")
        report=_snapshot(conn,pid,rid)
        project_budget=conn.execute(SQL_PROJECT_BUDGET,(pid,)).fetchone()
        if project_budget is not None:
            cap,reserved,spent,released=project_budget
            _usd(cap,"project_cap")
            if cap<=0 or reserved+spent>cap:
                reject("budget","invalid original project cap","budget_invariant")
            report["project_budget_usd"]={
                "cap":_usd(cap,"project_cap"),
                "reserved":_usd(reserved,"project_reserved"),
                "inference_spent":_usd(spent,"project_spent"),
                "released":_usd(released,"project_released"),
            }
        else:
            report["project_budget_usd"]=None
    return _public(report)


def read_sre_live_pair_trace(
    conn,project,run_id,item_id,category_key,*,
    actor_id,actor_authorizer,
):
    """Read one immutable category trace with only IDs, hashes and tokens."""
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    key=canonical_uuid(category_key,"category_key")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"trace_sre_live_pair")
    _committable(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if conn.execute(SQL_WORKSPACE_SHARE,(pid,)).fetchone() is None:
            reject("project_id","SRE workspace missing","rights_denied")
        run=conn.execute(SQL_RUN,(pid,rid)).fetchone()
        if run is None or run[5]!="live":
            reject("run_id","foreign/non-live run","rights_denied")
        row=conn.execute(SQL_TRACE,(key,pid,rid,iid)).fetchone()
        if row is None:
            reject("category_key","foreign or unapproved C9 category pair",
                   "rights_denied")
        (item_state,pair_state,decision,result_id,evidence_sha,
         receipt_id,request_sha,input_tokens,output_tokens,charged,
         evidence_status,authenticated,intent_id,ordinal,model_id,
         catalog_sha)=row
        if pair_state in ("match","no_match","uncertain"):
            if (result_id is None or decision!=pair_state or
                    receipt_id is None or authenticated is not True or
                    evidence_status!="validated_untrusted_output"):
                reject("pair","published category has no verified provider proof",
                       "invalid_evidence")
        elif result_id is not None:
            reject("pair","result exists but category remains unevaluated",
                   "invalid_evidence")
        trace={
            "schema_version":VERSION,
            "project_id":pid,"run_id":rid,"item_id":iid,
            "category_key":key,"run_state":run[0],
            "item_state":item_state,"pair_state":pair_state,
            "technical_decision":decision if result_id is not None else None,
            "result_id":result_id,"receipt_id":receipt_id,
            "intent_id":intent_id,
            "request_sha256":request_sha,
            "evidence_sha256":evidence_sha,
            "original_request_ordinal":ordinal,
            "model_id":model_id,"catalog_sha256":catalog_sha,
            "provider_inference_usd":_usd(charged,"charged_usd")
                    if charged is not None else None,
            "input_tokens":input_tokens,"output_tokens":output_tokens,
            "provider_receipt_confirmed":authenticated is True,
            "verified_d1_evidence":evidence_status=="validated_untrusted_output"
                    and result_id is not None,
            "all_hypotheses_complete":False,
            "source_text_or_key_in_report":False,
            "paid_post_allowed":False,
        }
    return trace


def record_sre_live_diagnostic_checkpoint(
    conn,project,run_id,*,actor_id,actor_authorizer,
):
    """Append one immutable, DB-verified financial/coverage snapshot.

    Checkpoints are idempotent per immutable source-derived digest.
    No submitted counter/alert/fee/source text is accepted from caller.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"record_sre_diagnostic_checkpoint")
    _committable(conn)
    with conn.transaction():
        if conn.execute(SQL_WORKSPACE_EXCLUSIVE,(pid,)).fetchone() is None:
            reject("project_id","SRE workspace missing","rights_denied")
        report=_snapshot(conn,pid,rid)
        c=report["counts"]
        requested_id=str(uuid4())
        row=conn.execute(SQL_INSERT,(
            requested_id,pid,rid,actor,report["snapshot_sha256"],
            report["run_state"],report["cancel_requested"],
            c["expected_items"],c["physical_items"],
            c["expected_pairs"],c["physical_pairs"],c["proven_pairs"],
            c["dispatched_intents"],c["unsettled_intents"],
            c["confirmed_receipts"],report["run_reserved_usd"],
            report["run_confirmed_inference_spent_usd"],
            c["finance_event_count"],c["reconciliation_event_count"],
            report["alert_code"],
        )).fetchone()
        if row is None:
            previous=conn.execute(SQL_EXISTING,(
                pid,rid,report["snapshot_sha256"],
            )).fetchone()
            if previous is None:
                reject("checkpoint","checkpoint write or owner mismatch",
                       "budget_invariant")
            saved_id=previous[0]
            created=False
        else:
            saved_id=row[0]
            created=True
    return {
        "schema_version":VERSION,
        "checkpoint_id":saved_id,"created":created,
        "project_id":pid,"run_id":rid,
        "snapshot_sha256":report["snapshot_sha256"],
        "alert_code":report["alert_code"],
        "counts":c,
        "run_reserved_usd":report["run_reserved_usd"],
        "run_confirmed_inference_spent_usd":
            report["run_confirmed_inference_spent_usd"],
        "has_raw_source_or_secrets":False,
        "paid_post_allowed":False,"paid_post_calls_issued_here":0,
    }


__all__=(
    "VERSION","read_sre_live_diagnostics","read_sre_live_pair_trace",
    "record_sre_live_diagnostic_checkpoint",
)
