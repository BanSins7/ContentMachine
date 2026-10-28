"""M30 source-authenticated LIVE per-pair evidence and exhaustive terminal gate.

Only M28 settlement with genuine M21 verified D1 evidence can substantiate a
C9 category. A paid receipt, a fake provider reply or one completed M20
request is NEVER enough to complete a multi-leaf run. NO paid network calls.
SQL 064 adds append-only decision attestations and terminal-state triggers;
this host port validates the underlying original provider response hash and
current D1 permissions before one pair can move out of not_evaluated.
"""
from __future__ import annotations

from uuid import uuid4

from .opus_budget import _workspace
from .opus_consent import _actor,_authorized
from .opus_dispatch_intent import _committable
from .opus_receipt import _provider_answer
from .opus_catalog_preflight import preflight_exact_opus_catalog
from .opus_request_packer import pack_live_opus_requests
from .opus_evidence_verifier import _verify_single,_request_payload
from .live_content_gate import read_live_content_gate
from .v1_contracts import (
    canonical_uuid,fingerprint,positive,project_id,reject,sha256_digest,
)

VERSION="sre-live-verified-coverage/v1"
FINALS=frozenset(("match","no_match","uncertain"))
SQL_SOURCE="""SELECT r.state,r.cancel_requested,i.state,
    i.video_id,i.content_version_id,i.content_sha256,
    t.response_sha256,t.evidence_sha256,t.evidence_status,
    t.generation_id,t.request_sha256,d.request_ordinal,
    d.batch_manifest_sha256,d.catalog_sha256,d.model_revision,d.endpoint_tag,
    q.quote
    FROM sre_live_opus_receipts t
    JOIN sre_live_opus_dispatch_intents d
      ON d.project_id=t.project_id AND d.intent_id=t.intent_id
    JOIN sre_live_opus_quotes q
      ON q.project_id=d.project_id AND q.run_id=d.run_id
      AND q.item_id=d.item_id AND q.quote_sha256=d.quote_sha256
    JOIN sre_live_runs r
      ON r.project_id=d.project_id AND r.run_id=d.run_id
    JOIN sre_live_items i
      ON i.project_id=d.project_id AND i.run_id=d.run_id AND i.id=d.item_id
    WHERE t.project_id=%s AND t.run_id=%s::uuid AND t.item_id=%s
      AND t.receipt_id=%s::uuid FOR UPDATE OF r,i"""
SQL_INSERT="""INSERT INTO sre_live_verified_pair_results(
    result_id,project_id,run_id,item_id,category_key,receipt_id,
    request_sha256,request_ordinal,decision,verified_chunk_sha256,trusted_actor)
    VALUES(%s::uuid,%s,%s::uuid,%s,%s::uuid,%s::uuid,%s,%s,%s,%s,%s)
    RETURNING result_id::text"""
SQL_PAIR_UPDATE="""UPDATE sre_live_expected_pairs
    SET state=%s WHERE project_id=%s AND run_id=%s::uuid
      AND item_id=%s AND category_key=%s::uuid AND state='not_evaluated'
    RETURNING state"""
SQL_RUN="""SELECT state,cancel_requested,execution_mode,expected_items,
    finished_items,blocked_items FROM sre_live_runs
    WHERE project_id=%s AND run_id=%s::uuid"""
SQL_ITEMS="""SELECT id,video_id,content_version_id,content_sha256,state,
    expected_hypothesis_keys FROM sre_live_items
    WHERE project_id=%s AND run_id=%s::uuid ORDER BY id"""
SQL_PAIRS="""SELECT item_id,category_key::text,state
    FROM sre_live_expected_pairs WHERE project_id=%s AND run_id=%s::uuid
    ORDER BY item_id,category_key"""
SQL_RESULTS="""SELECT v.item_id,v.category_key::text,v.decision,
    v.receipt_id::text,v.verified_chunk_sha256,
    t.evidence_sha256,t.evidence_status,t.generation_authenticated
    FROM sre_live_verified_pair_results v
    JOIN sre_live_opus_receipts t
      ON t.project_id=v.project_id AND t.receipt_id=v.receipt_id
    WHERE v.project_id=%s AND v.run_id=%s::uuid ORDER BY v.item_id,v.category_key"""
SQL_BATCHES="""SELECT batch_id::text,state FROM sre_live_batches
    WHERE project_id=%s AND run_id=%s::uuid ORDER BY ordinal"""
SQL_MEMBERS="""SELECT item_id,batch_id::text
    FROM sre_live_batch_members WHERE project_id=%s AND run_id=%s::uuid
    ORDER BY item_id"""
SQL_PENDING="""SELECT count(*) FROM sre_live_opus_dispatch_intents d
    LEFT JOIN sre_live_opus_receipts t
      ON t.project_id=d.project_id AND t.intent_id=d.intent_id
    WHERE d.project_id=%s AND d.run_id=%s::uuid AND t.receipt_id IS NULL"""


def _verify_current_d1(conn,pid,video,content_id,digest):
    proof=read_live_content_gate(
        conn,pid,content_id,phase="before_publish",external_llm=False,
        pins={"video_id":video,"content_version_id":content_id,
              "content_sha256":digest},
    )
    if (proof["content"]["project_id"]!=pid or
            proof["content"]["video_id"]!=video or
            proof["content"]["content_sha256"]!=digest):
        reject("content","revoked or drifted D1 result source",
               "rights_denied")


def commit_verified_live_sre_pair(
    conn,project,run_id,item_id,receipt_id,provider_response,
    category_key,catalog_snapshot,*,operator_expected_sha256,
    actor_id,actor_authorizer,now=None,
):
    """Single source-grounded, paid-receipt-backed C9 category; no run finish.

    An authenticated trusted worker supplies the ORIGINAL response bytes from
    its private M27 channel. Public/client JSON is not provider authority.
    M28 hash/GET/receipt and D1/C9 M20 input must match exactly.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    receipt=canonical_uuid(receipt_id,"receipt_id")
    category=canonical_uuid(category_key,"category_key")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"commit_sre_verified_pair")
    pin_sha=sha256_digest(operator_expected_sha256,"operator_expected_sha256")
    _committable(conn)
    if type(provider_response) is not dict:
        reject("provider_response","trusted original provider response required",
               "invalid_provider_receipt")
    with conn.transaction():
        _workspace(conn,pid)
        src=conn.execute(SQL_SOURCE,(pid,rid,iid,receipt)).fetchone()
        if src is None:
            reject("receipt_id","foreign or missing authenticated receipt",
                   "rights_denied")
        (run_state,cancel,item_state,video,content_id,content_sha,
         response_sha,evidence_sha,evidence_status,generation_id,request_sha,
         ordinal,batch_sha,catalog_sha,model_revision,endpoint_tag,quote)=src
        if (run_state not in ("queued","running") or cancel is not False
                or item_state!="running"):
            reject("run","run cancelled, unknown or item not owned",
                   "invalid_transition")
        if evidence_status!="validated_untrusted_output":
            reject("receipt","M28 answer was not technically source-validated",
                   "invalid_evidence")
        if fingerprint(provider_response)!=response_sha:
            reject("receipt","provider response differs from authentic M28 hash",
                   "snapshot_mismatch")
        raw=_provider_answer(provider_response,generation_id)
        if raw is None:
            reject("response","no verified assistant text","malformed_model_output")
        if (type(quote) is not dict or
                type(quote.get("per_request")) is not list
                or quote.get("request_count")!=len(quote["per_request"])
                or quote.get("batch_manifest_sha256")!=batch_sha
                or quote.get("catalog_sha256")!=catalog_sha
                or quote.get("model_revision")!=model_revision
                or quote.get("endpoint_tag")!=endpoint_tag
                or type(ordinal) is not int
                or not 0<=ordinal<len(quote["per_request"])):
            reject("quote","original financial request pin changed",
                   "snapshot_mismatch")
        matching=[q for q in quote["per_request"]
                  if q.get("category_key")==category]
        if (len(matching)!=1 or
                matching[0].get("request_ordinal")!=ordinal or
                matching[0].get("request_sha256")!=request_sha):
            reject("category_key","split, missing or mismatched C9 category",
                   "incomplete_coverage")
        _verify_current_d1(conn,pid,video,content_id,content_sha)
        preflight=preflight_exact_opus_catalog(
            catalog_snapshot,operator_expected_sha256=pin_sha,
            operator_endpoint_tag=endpoint_tag,
            operator_model_revision=model_revision,
            requested_max_output_tokens=quote["max_output_tokens_per_request"],
            requested_input_proxy=quote["requested_input_token_proxy"],
            now=now,
        )
        m20=pack_live_opus_requests(
            conn,pid,rid,iid,operator_catalog_pin=preflight["m20_catalog_pin"])
        if (m20["batch_manifest_sha256"]!=batch_sha or
                m20["catalog_sha256"]!=catalog_sha
                or ordinal>=len(m20["requests"])):
            reject("m20","current exact C9 and D1 request set differs",
                   "snapshot_mismatch")
        req=m20["requests"][ordinal]
        if req["request_sha256"]!=request_sha or req["category_key"]!=category:
            reject("m20","receipt not for original approved C9 category",
                   "snapshot_mismatch")
        technical=_verify_single(req,_request_payload(req),raw)
        if fingerprint(technical)!=evidence_sha:
            reject("receipt","M28 source evidence hash differs",
                   "snapshot_mismatch")
        if technical["decision"] not in FINALS:
            reject("decision","unsupported semantic status",
                   "invalid_decision")
        result_id=str(uuid4())
        result=conn.execute(SQL_INSERT,(
            result_id,pid,rid,iid,category,receipt,request_sha,ordinal,
            technical["decision"],evidence_sha,actor,
        )).fetchone()
        if result is None or result[0]!=result_id:
            reject("result_id","immutable pair proof write failed",
                   "budget_invariant")
        marked=conn.execute(SQL_PAIR_UPDATE,(
            technical["decision"],pid,rid,iid,category,
        )).fetchone()
        if marked!=(technical["decision"],):
            reject("category_key","C9 pair absent, blocked or already finalized",
                   "incomplete_coverage")
    return {
        "schema_version":VERSION,
        "status":"one_pair_technically_verified_not_run_complete",
        "project_id":pid,"run_id":rid,"item_id":iid,
        "category_key":category,"decision":technical["decision"],
        "result_id":result_id,"receipt_id":receipt,
        "request_sha256":request_sha,
        "verified_chunk_sha256":evidence_sha,
        "semantics_independently_confirmed":False,
        "all_hypotheses_complete":False,
        "paid_post_calls_issued_here":0,
        "automatic_retry_allowed":False,
        "dispatch_allowed":False,
    }


def _coverage(items,pairs,results,pending):
    """Pure exact-set audit. No receipt or catalog can be trusted by itself."""
    if type(items) is not list or not items or len(items)>10000:
        reject("items","no valid pinned LIVE items","incomplete_coverage")
    expected=set()
    item_ids=set()
    for id,video,content_id,sha,state,keys in items:
        if (type(id) is not int or id in item_ids or
                type(keys) is not list or not 1<=len(keys)<=48 or
                keys!=sorted(set(keys))):
            reject("items","duplicate/malformed immutable approved C9 keyset",
                   "snapshot_mismatch")
        item_ids.add(id)
        for key in keys:
            expected.add((id,canonical_uuid(key,"category_key")))
    actual={}
    for item,key,state in pairs:
        key=canonical_uuid(key,"category_key")
        token=(item,key)
        if token in actual:
            reject("pairs","duplicate C9 pair","incomplete_coverage")
        actual[token]=state
    if set(actual)!=expected:
        reject("pairs","missing/extra C9 video category pair matrix",
               "incomplete_coverage")
    proof={}
    for item,key,decision,receipt,sha,receipt_sha,evidence_status,authenticated in results:
        key=canonical_uuid(key,"category_key")
        token=(item,key)
        if token in proof or token not in expected:
            reject("results","duplicate/foreign/extra proof","incomplete_coverage")
        if (decision not in FINALS or actual[token]!=decision
                or sha!=receipt_sha
                or evidence_status!="validated_untrusted_output"
                or authenticated is not True):
            reject("results","unproven M21 evidence/receipt/decision mismatch",
                   "invalid_evidence")
        canonical_uuid(receipt,"receipt_id")
        sha256_digest(sha,"verified_chunk_sha256")
        proof[token]=decision
    final_pairs={k:v for k,v in actual.items() if v in FINALS}
    if set(final_pairs)!=set(proof):
        reject("coverage","evaluated pair lacks genuine original receipt proof",
               "invalid_evidence")
    if type(pending) is not int or pending<0:
        reject("pending_intents","invalid unsettled M26 witness",
               "budget_invariant")
    total=len(expected);ok=len(proof)
    blocked=sum(v.startswith("blocked_") for v in actual.values())
    remaining=total-ok
    if pending:
        status="blocked_unknown_provider_charge"
    elif blocked:
        status="blocked_rights_or_budget"
    elif ok==total:
        status="complete_candidate_requires_terminal_checks"
    elif ok>0:
        status="partial_candidate_not_completed"
    else:
        status="not_evaluated"
    return {
        "schema_version":VERSION,"status":status,
        "expected_pair_count":total,"verified_pair_count":ok,
        "unverified_pair_count":remaining,"blocked_pair_count":blocked,
        "unsettled_possible_paid_intents":pending,
        "verified_pair_keys":sorted([{"item_id":i,"category_key":k}
                                     for i,k in proof],key=lambda row:(
                                         row["item_id"],row["category_key"])),
        "all_pair_evidence_present":ok==total and pending==0 and blocked==0,
        "run_completed":False,"dispatch_allowed":False,
        "automatic_retry_allowed":False,"paid_post_calls_issued_here":0,
    }


def inspect_live_sre_coverage(conn,project,run_id,*,actor_id,actor_authorizer):
    """Authentic project-scoped coverage report; never changes run state."""
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"read_sre_live_coverage")
    _committable(conn)
    with conn.transaction():
        if conn.execute(
            "SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR SHARE",
            (pid,)).fetchone() is None:
            reject("project_id","SRE workspace absent","rights_denied")
        run=conn.execute(SQL_RUN,(pid,rid)).fetchone()
        if run is None or run[2]!="live":
            reject("run_id","foreign/non-LIVE run","rights_denied")
        items=conn.execute(SQL_ITEMS,(pid,rid)).fetchall()
        pairs=conn.execute(SQL_PAIRS,(pid,rid)).fetchall()
        evidence=conn.execute(SQL_RESULTS,(pid,rid)).fetchall()
        batches=conn.execute(SQL_BATCHES,(pid,rid)).fetchall()
        members=conn.execute(SQL_MEMBERS,(pid,rid)).fetchall()
        if (len(members)!=len(items) or
                {row[0] for row in members}!={row[0] for row in items} or
                {row[1] for row in members}!={row[0] for row in batches}):
            reject("members","missing or foreign C9 batch assignment",
                   "incomplete_coverage")
        pending=conn.execute(SQL_PENDING,(pid,rid)).fetchone()[0]
        report=_coverage(items,pairs,evidence,pending)
        if len(items)!=run[3] or not batches:
            reject("run","missing LIVE item/batch registry","incomplete_coverage")
        report.update({
            "project_id":pid,"run_id":rid,"run_state":run[0],
            "cancel_requested":run[1],"expected_items":run[3],
            "finished_items":run[4],"blocked_items":run[5],
            "batch_states":[x[1] for x in batches],
            "all_children_completed":(
                all(i[4]=="completed" for i in items) and
                all(x[1]=="completed" for x in batches)),
        })
        report["eligible_for_run_completion"]=(
            report["all_pair_evidence_present"] and
            report["all_children_completed"] and
            not run[1] and run[4]==run[3] and run[5]==0 and
            run[0] in ("running","queued")
        )
        report["run_completed"]=run[0]=="completed"
    return report



SQL_FINAL_ITEM="""UPDATE sre_live_items
    SET state=%s,worker_token=NULL,lease_until=NULL,
        finished_at=clock_timestamp(),updated_at=clock_timestamp()
    WHERE project_id=%s AND run_id=%s::uuid AND id=%s
      AND state IN ('queued','running','unknown')
    RETURNING state"""
SQL_FINAL_BATCH="""UPDATE sre_live_batches
    SET state=%s,worker_token=NULL,lease_until=NULL,
        finished_at=clock_timestamp(),updated_at=clock_timestamp()
    WHERE project_id=%s AND run_id=%s::uuid AND batch_id=%s::uuid
      AND state IN ('queued','running','unknown')
    RETURNING state"""
SQL_FINAL_RUN="""UPDATE sre_live_runs SET
    state=%s,finished_items=%s,blocked_items=%s,
    worker_token=NULL,lease_until=NULL,
    finished_at=clock_timestamp(),updated_at=clock_timestamp()
    WHERE project_id=%s AND run_id=%s::uuid
      AND state IN ('queued','running','unknown')
      AND cancel_requested=FALSE RETURNING state"""


def finalize_live_sre_coverage(
    conn,project,run_id,*,scope,object_id=None,target_state,
    actor_id,actor_authorizer,
):
    """Explicit owner-controlled terminal transition, NOT auto-classification.

    All C9 rows and M28 source-grounded receipts are recomputed inside the
    same workspace-locked SQL transaction. Every relevant D1 source is checked
    freshly for publication. SQL 055+064 independently reject false terminal
    states even for a direct UPDATE from a trusted DB writer.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"finalize_sre_live_coverage")
    if scope not in ("item","batch","run") or target_state not in (
        "completed","partial",
    ):
        reject("scope","only explicit item/batch/run complete or partial",
               "invalid_transition")
    if scope=="item":
        object_id=positive(object_id,"item_id",maximum=9223372036854775807)
    elif scope=="batch":
        object_id=canonical_uuid(object_id,"batch_id")
    elif object_id is not None:
        reject("object_id","run scope has no item/batch identity")
    _committable(conn)
    with conn.transaction():
        _workspace(conn,pid)
        run=conn.execute(SQL_RUN,(pid,rid)).fetchone()
        if (run is None or run[2]!="live" or run[1] is not False or
                run[0] not in ("queued","running","unknown")):
            reject("run","cancelled or foreign LIVE run cannot finalize",
                   "invalid_transition")
        items=conn.execute(SQL_ITEMS,(pid,rid)).fetchall()
        pairs=conn.execute(SQL_PAIRS,(pid,rid)).fetchall()
        results=conn.execute(SQL_RESULTS,(pid,rid)).fetchall()
        batches=conn.execute(SQL_BATCHES,(pid,rid)).fetchall()
        members=conn.execute(SQL_MEMBERS,(pid,rid)).fetchall()
        pending=conn.execute(SQL_PENDING,(pid,rid)).fetchone()[0]
        if (len(items)!=run[3] or not batches or
                len(members)!=len(items)
                or {x[0] for x in members}!={x[0] for x in items}
                or {x[1] for x in members}!={x[0] for x in batches}):
            reject("run","incomplete immutable batch/video registry",
                   "incomplete_coverage")
        full=_coverage(items,pairs,results,pending)
        if pending:
            reject("provider","unknown possibly charged intent blocks terminal state",
                   "unknown_paid_outcome")
        by_item={i[0]:i for i in items}
        if scope=="item":
            if object_id not in by_item:
                reject("item_id","foreign/missing LIVE item","rights_denied")
            selected={object_id}
        elif scope=="batch":
            if object_id not in {x[0] for x in batches}:
                reject("batch_id","foreign/missing LIVE batch","rights_denied")
            selected={id for id,batch in members if batch==object_id}
        else:
            selected=set(by_item)
        if not selected:
            reject("scope","no original C9 item to finalize",
                   "incomplete_coverage")
        subset={(iid,key):state for iid,key,state in pairs if iid in selected}
        valid={(iid,key) for iid,key,decision,*_ in results if iid in selected}
        present=len(subset);complete=len(valid)
        if not present or (target_state=="completed" and complete!=present) or (
            target_state=="partial" and not 0<complete<present
        ):
            reject("coverage","cannot finalize with missing/extra C9 evidence",
                   "incomplete_coverage")
        # Compare against D1 now, not merely at M26 send time or M28 billing.
        for iid in sorted(selected):
            _,video,cid,sha,state,keys=by_item[iid]
            _verify_current_d1(conn,pid,video,cid,sha)
        if scope=="item":
            changed=conn.execute(SQL_FINAL_ITEM,(
                target_state,pid,rid,object_id,
            )).fetchone()
        elif scope=="batch":
            changed=conn.execute(SQL_FINAL_BATCH,(
                target_state,pid,rid,object_id,
            )).fetchone()
        else:
            finished=sum(i[4]=="completed" for i in items)
            blocked=sum(i[4] in (
                "partial","failed","cancelled","paused_rights",
                "paused_budget","waiting_source",
            ) for i in items)
            # Explicit terminalization only when every child is already
            # settled: no running/queued/unknown item can be hidden.
            if any(i[4] in ("not_evaluated","queued","running","unknown")
                   for i in items):
                reject("run","unfinished/unknown LIVE child blocks finalization",
                       "incomplete_coverage")
            if target_state=="completed" and (
                finished!=run[3] or blocked!=0 or
                any(state!="completed" for _,state in batches)
            ):
                reject("run","every video and batch must be completed first",
                       "incomplete_coverage")
            if target_state=="partial" and not 0<finished<run[3]:
                reject("run","partial run requires completed and blocked items",
                       "incomplete_coverage")
            changed=conn.execute(SQL_FINAL_RUN,(
                target_state,finished,blocked,pid,rid,
            )).fetchone()
        if changed!=(target_state,):
            reject("state","terminal status not committed for this scope",
                   "invalid_transition")
    return {
        "schema_version":VERSION,
        "status":target_state,"scope":scope,
        "project_id":pid,"run_id":rid,
        "object_id":object_id,
        "verified_scope_pairs":complete,
        "expected_scope_pairs":present,
        "unsettled_possible_paid_intents":0,
        "source_rights_rechecked_before_publish":True,
        "automatic_retry_allowed":False,"dispatch_allowed":False,
        "paid_post_calls_issued_here":0,
    }


__all__=(
    "VERSION","commit_verified_live_sre_pair",
    "inspect_live_sre_coverage","finalize_live_sre_coverage",
)
