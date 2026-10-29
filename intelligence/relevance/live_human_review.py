"""M32 human decisions are independent, immutable, source-pinned overlays.

A review never alters M21/M28/M30 provider output, money, C9 expected-pair
state, worker lease, or publisher. Human judgments require a separately
authenticated human-review role AND an exact source-bound confirmation.
A correction is effective only through this explicit downstream read API;
it is not automatically treated as a new paid model observation.
"""
from __future__ import annotations

from uuid import uuid4

from .opus_consent import _actor, _authorized
from .opus_dispatch_intent import _committable
from .live_verified_coverage import _verify_current_d1
from .v1_contracts import (
    canonical_uuid,positive,project_id,reject,sha256_digest,
)

VERSION="sre-live-human-review/v1"
DECISIONS=frozenset(("match","no_match","uncertain"))
REASONS=frozenset((
    "verified_model_decision","incorrect_positive","incorrect_negative",
    "insufficient_evidence","ambiguous_content","review_correction",
))
SQL_WORKSPACE_LOCK="SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR UPDATE"
SQL_WORKSPACE_SHARE="SELECT 1 FROM sre_workspaces WHERE project_id=%s"
SQL_SOURCE="""SELECT v.result_id::text,v.receipt_id::text,
    v.request_sha256,v.verified_chunk_sha256,
    d.quote_sha256,d.catalog_sha256,v.decision,
    i.video_id,i.content_version_id,i.content_sha256,
    r.state,r.cancel_requested,
    t.generation_authenticated,t.evidence_status,p.state
    FROM sre_live_verified_pair_results v
    JOIN sre_live_opus_receipts original_receipt
      ON original_receipt.project_id=v.project_id
     AND original_receipt.receipt_id=v.receipt_id
    JOIN sre_live_opus_dispatch_intents d
      ON d.project_id=original_receipt.project_id
     AND d.intent_id=original_receipt.intent_id
    JOIN sre_live_items i
      ON i.project_id=v.project_id AND i.run_id=v.run_id AND i.id=v.item_id
    JOIN sre_live_runs r
      ON r.project_id=v.project_id AND r.run_id=v.run_id
    JOIN sre_live_expected_pairs p
      ON p.project_id=v.project_id AND p.run_id=v.run_id
     AND p.item_id=v.item_id AND p.category_key=v.category_key
    JOIN sre_live_opus_receipts t
      ON t.project_id=v.project_id AND t.receipt_id=v.receipt_id
    WHERE v.project_id=%s AND v.run_id=%s::uuid
      AND v.item_id=%s AND v.category_key=%s::uuid"""
SQL_RUN="""SELECT execution_mode,state,cancel_requested FROM sre_live_runs
    WHERE project_id=%s AND run_id=%s::uuid"""
SQL_HEAD="""SELECT review_id::text,revision,human_decision,review_action,
    reason_code,reviewer_actor,reviewed_at,
    original_model_decision,base_result_id::text
    FROM sre_live_human_reviews
    WHERE project_id=%s AND run_id=%s::uuid
      AND item_id=%s AND category_key=%s::uuid
    ORDER BY revision DESC LIMIT 1"""
SQL_HISTORY="""SELECT review_id::text,revision,prior_review_id::text,
    human_decision,review_action,reason_code,reviewer_actor,reviewed_at,
    original_model_decision,base_result_id::text,receipt_id::text,
    request_sha256,verified_chunk_sha256,quote_sha256,catalog_sha256
    FROM sre_live_human_reviews
    WHERE project_id=%s AND run_id=%s::uuid
      AND item_id=%s AND category_key=%s::uuid
    ORDER BY revision"""
SQL_INSERT="""INSERT INTO sre_live_human_reviews(
    review_id,project_id,run_id,item_id,category_key,
    base_result_id,receipt_id,request_sha256,verified_chunk_sha256,
    quote_sha256,catalog_sha256,original_model_decision,human_decision,review_action,reason_code,
    reviewer_actor,human_verified,revision,prior_review_id)
    VALUES (%s::uuid,%s,%s::uuid,%s,%s::uuid,
            %s::uuid,%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,TRUE,%s,%s::uuid)
    RETURNING review_id::text"""
SQL_QUEUE="""SELECT v.item_id,v.category_key::text,v.result_id::text,
    v.decision,v.receipt_id::text,v.request_sha256,
    d.quote_sha256,d.catalog_sha256,
    i.video_id,i.content_version_id,i.content_sha256,
    recent.review_id::text,recent.revision,recent.human_decision,
    recent.review_action,recent.reviewer_actor
    FROM sre_live_verified_pair_results v
    JOIN sre_live_opus_receipts receipt
      ON receipt.project_id=v.project_id AND receipt.receipt_id=v.receipt_id
    JOIN sre_live_opus_dispatch_intents d
      ON d.project_id=receipt.project_id AND d.intent_id=receipt.intent_id
    JOIN sre_live_items i
      ON i.project_id=v.project_id AND i.run_id=v.run_id AND i.id=v.item_id
    JOIN sre_live_runs r
      ON r.project_id=v.project_id AND r.run_id=v.run_id
    LEFT JOIN LATERAL (
      SELECT h.review_id,h.revision,h.human_decision,
             h.review_action,h.reviewer_actor
      FROM sre_live_human_reviews h
      WHERE h.project_id=v.project_id AND h.run_id=v.run_id
        AND h.item_id=v.item_id AND h.category_key=v.category_key
      ORDER BY h.revision DESC LIMIT 1
    ) recent ON TRUE
    WHERE v.project_id=%s AND v.run_id=%s::uuid
      AND r.execution_mode='live'
      AND r.cancel_requested=FALSE
      AND r.state IN ('queued','running','partial','completed')
    ORDER BY v.item_id,v.category_key LIMIT %s"""


def _live_source(conn,pid,rid,iid,key):
    row=conn.execute(SQL_SOURCE,(pid,rid,iid,key)).fetchone()
    if row is None:
        reject("category_key","verified M30 pair absent/foreign","rights_denied")
    (result_id,receipt_id,request_sha,evidence_sha,quote_sha,catalog_sha,
     original,video,content_id,content_sha,run_state,cancel,authenticated,
     evidence_status,pair_state)=row
    if (original not in DECISIONS or pair_state!=original
            or authenticated is not True
            or evidence_status!="validated_untrusted_output"):
        reject("result","model source or M28 receipt no longer verified",
               "invalid_evidence")
    if cancel is not False or run_state not in (
            "queued","running","partial","completed"):
        reject("run_id","cancelled/UNKNOWN run is not human-reviewable",
               "invalid_transition")
    _verify_current_d1(conn,pid,video,content_id,content_sha)
    return {
        "base_result_id":result_id,"receipt_id":receipt_id,
        "request_sha256":request_sha,
        "verified_chunk_sha256":evidence_sha,
        "quote_sha256":quote_sha,"catalog_sha256":catalog_sha,
        "original_model_decision":original,
    }


def human_review_confirmation(
    project,run_id,item_id,category_key,base_result_id,
    original_model_decision,human_decision,reason_code,
    expected_prior_review_id=None,*,quote_sha256,catalog_sha256,
):
    """Exact human UI acceptance payload, NOT confirmation by itself."""
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    key=canonical_uuid(category_key,"category_key")
    base=canonical_uuid(base_result_id,"base_result_id")
    quote_pin=sha256_digest(quote_sha256,"quote_sha256")
    catalog_pin=sha256_digest(catalog_sha256,"catalog_sha256")
    if (type(original_model_decision) is not str or
            type(human_decision) is not str or
            original_model_decision not in DECISIONS or
            human_decision not in DECISIONS):
        reject("decision","unsupported immutable human/model decision")
    if type(reason_code) is not str or reason_code not in REASONS:
        reject("reason_code","unsupported privacy-minimal human-review reason")
    previous=(canonical_uuid(expected_prior_review_id,"expected_prior_review_id")
              if expected_prior_review_id is not None else None)
    return {
        "action":"sre-human-review-confirm/v1","project_id":pid,
        "run_id":rid,"item_id":iid,"category_key":key,
        "base_result_id":base,
        "quote_sha256":quote_pin,"catalog_sha256":catalog_pin,
        "original_model_decision":original_model_decision,
        "human_decision":human_decision,"reason_code":reason_code,
        "expected_prior_review_id":previous,
    }


def _effective(original,head):
    if head is None:
        return {
            "effective_decision":original,
            "effective_source":"original_verified_model_not_human_reviewed",
            "human_reviewed":False,"human_revision":0,
            "human_review_id":None,"human_review_action":None,
        }
    (revision_id,rev,decision,action,reason,actor,when,
     head_original,head_result)=head
    if (head_original!=original or decision not in DECISIONS
            or type(rev) is not int or rev<1):
        reject("review","human review chain diverges from original M30 result",
               "invalid_evidence")
    return {
        "effective_decision":decision,
        "effective_source":"explicit_verified_human_review",
        "human_reviewed":True,"human_revision":rev,
        "human_review_id":revision_id,"human_review_action":action,
    }


def record_live_sre_human_review(
    conn,project,run_id,item_id,category_key,*,reviewer_actor,
    actor_authorizer,human_decision,reason_code,explicit_confirmation,
    expected_prior_review_id=None,
):
    """Append first human confirmation/correction or optimistic supersession.

    The host MUST authenticate a real human with review permissions. A
    programmatic worker or model response is NOT a human attestation.
    Confirmation echoes original model & target identity exactly; caller
    cannot rewrite earlier review, switch category/receipt, or discard
    independently authenticated M30 provenance.
    """
    pid=project_id(project)
    rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    key=canonical_uuid(category_key,"category_key")
    actor=_actor(reviewer_actor)
    _authorized(actor_authorizer,pid,actor,"human_review_sre_live_pair")
    if type(human_decision) is not str or human_decision not in DECISIONS:
        reject("human_decision","unsupported category decision")
    if type(reason_code) is not str or reason_code not in REASONS:
        reject("reason_code","unsupported structured reason")
    prior=(canonical_uuid(expected_prior_review_id,"expected_prior_review_id")
           if expected_prior_review_id is not None else None)
    _committable(conn)
    with conn.transaction():
        if conn.execute(SQL_WORKSPACE_LOCK,(pid,)).fetchone() is None:
            reject("project_id","SRE workspace unavailable","rights_denied")
        source=_live_source(conn,pid,rid,iid,key)
        expected=human_review_confirmation(
            pid,rid,iid,key,source["base_result_id"],
            source["original_model_decision"],human_decision,
            reason_code,prior,
            quote_sha256=source["quote_sha256"],
            catalog_sha256=source["catalog_sha256"],
        )
        if type(explicit_confirmation) is not dict or explicit_confirmation!=expected:
            reject("explicit_confirmation",
                   "trusted human did not confirm exact source/category/decision",
                   "review_not_explicit")
        head=conn.execute(SQL_HEAD,(pid,rid,iid,key)).fetchone()
        if head is None:
            if prior is not None:
                reject("expected_prior_review_id",
                       "first review has no predecessor","stale_review")
            model=source["original_model_decision"]
            if human_decision==model:
                action="confirm"
                if reason_code!="verified_model_decision":
                    reject("reason_code","confirmation requires verified-model reason")
            else:
                action="correct"
                if reason_code=="verified_model_decision":
                    reject("reason_code","correction requires specific reason")
            revision=1
        else:
            if prior!=head[0]:
                reject("expected_prior_review_id",
                       "stale review would fork immutable history","stale_review")
            if head[8]!=source["base_result_id"]:
                reject("base_result_id","review chain source changed",
                       "snapshot_mismatch")
            if human_decision==head[2]:
                reject("human_decision","supersession must change effective decision",
                       "invalid_transition")
            if reason_code=="verified_model_decision":
                reject("reason_code","supersession needs correction reason")
            action="supersede"
            revision=head[1]+1
            if revision>10000:
                reject("revision","human review chain capacity exceeded",
                       "review_limit_reached")
        identity=str(uuid4())
        saved=conn.execute(SQL_INSERT,(
            identity,pid,rid,iid,key,
            source["base_result_id"],source["receipt_id"],
            source["request_sha256"],source["verified_chunk_sha256"],
            source["quote_sha256"],source["catalog_sha256"],
            source["original_model_decision"],human_decision,action,
            reason_code,actor,revision,prior,
        )).fetchone()
        if saved!=(identity,):
            reject("review","human review insert not durable",
                   "budget_invariant")
    return {
        "schema_version":VERSION,
        "status":"immutable_human_review_recorded",
        "project_id":pid,"run_id":rid,"item_id":iid,"category_key":key,
        "review_id":identity,"revision":revision,"review_action":action,
        "human_decision":human_decision,
        "original_model_decision":source["original_model_decision"],
        "base_result_id":source["base_result_id"],
        "receipt_id":source["receipt_id"],
        "quote_sha256":source["quote_sha256"],
        "catalog_sha256":source["catalog_sha256"],
        "prior_review_id":prior,
        "model_result_mutated":False,
        "classification_reprocessed":False,
        "provider_post_calls":0,
    }


def read_effective_live_sre_decision(
    conn,project,run_id,item_id,category_key,*,actor_id,actor_authorizer,
):
    """Explicit source-rights-aware effective view, NOT a model overwrite."""
    pid=project_id(project);rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    key=canonical_uuid(category_key,"category_key")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"read_sre_human_review")
    _committable(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if conn.execute(SQL_WORKSPACE_SHARE,(pid,)).fetchone() is None:
            reject("project_id","SRE workspace unavailable","rights_denied")
        source=_live_source(conn,pid,rid,iid,key)
        head=conn.execute(SQL_HEAD,(pid,rid,iid,key)).fetchone()
        effective=_effective(source["original_model_decision"],head)
        if head is not None and head[8]!=source["base_result_id"]:
            reject("review","review head not linked to original M30 result",
                   "invalid_evidence")
    return {
        "schema_version":VERSION,"project_id":pid,"run_id":rid,
        "item_id":iid,"category_key":key,
        "base_result_id":source["base_result_id"],
        "receipt_id":source["receipt_id"],
        "request_sha256":source["request_sha256"],
        "quote_sha256":source["quote_sha256"],
        "catalog_sha256":source["catalog_sha256"],
        "original_model_decision":source["original_model_decision"],
        **effective,
        "source_rights_rechecked":True,
        "paid_post_allowed":False,
        "source_text_or_notes_exposed":False,
    }


def read_sre_human_review_history(
    conn,project,run_id,item_id,category_key,*,actor_id,actor_authorizer,
):
    """Project-authorized full ordered immutable lineage (no reviewer notes)."""
    pid=project_id(project);rid=canonical_uuid(run_id,"run_id")
    iid=positive(item_id,"item_id",maximum=9223372036854775807)
    key=canonical_uuid(category_key,"category_key")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"read_sre_human_review")
    _committable(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if conn.execute(SQL_WORKSPACE_SHARE,(pid,)).fetchone() is None:
            reject("project_id","SRE workspace unavailable","rights_denied")
        original=_live_source(conn,pid,rid,iid,key)
        rows=conn.execute(SQL_HISTORY,(pid,rid,iid,key)).fetchall()
        seen=set();last_id=None
        history=[]
        for expected_revision,row in enumerate(rows,1):
            (rid2,revision,prior,decision,action,reason,reviewer,when,
             model,base,receipt,request_sha,evidence_sha,quote_sha,
             catalog_sha)=row
            if (revision!=expected_revision or prior!=last_id
                    or rid2 in seen or model!=original["original_model_decision"]
                    or base!=original["base_result_id"]
                    or receipt!=original["receipt_id"]
                    or request_sha!=original["request_sha256"]
                    or evidence_sha!=original["verified_chunk_sha256"]
                    or quote_sha!=original["quote_sha256"]
                    or catalog_sha!=original["catalog_sha256"]):
                reject("review","forked/mutated human review history",
                       "invalid_evidence")
            seen.add(rid2);last_id=rid2
            history.append({
                "review_id":rid2,"revision":revision,
                "prior_review_id":prior,
                "human_decision":decision,"review_action":action,
                "reason_code":reason,"reviewer_actor":reviewer,
                "reviewed_at":when.isoformat() if when is not None else None,
            })
    return {
        "schema_version":VERSION,
        "project_id":pid,"run_id":rid,"item_id":iid,"category_key":key,
        "original_model_decision":original["original_model_decision"],
        "original_result_id":original["base_result_id"],
        "reviews":history,
        "effective_decision":history[-1]["human_decision"]
            if history else original["original_model_decision"],
        "source_rights_rechecked":True,
        "immutable_model_decision_preserved":True,
        "paid_post_allowed":False,
    }


def read_live_sre_human_review_queue(
    conn,project,run_id,*,actor_id,actor_authorizer,limit=50,
):
    """Bounded page of M30 evidence-backed reviewable pairs, NOT all C9 work."""
    pid=project_id(project);rid=canonical_uuid(run_id,"run_id")
    actor=_actor(actor_id)
    _authorized(actor_authorizer,pid,actor,"read_sre_human_review_queue")
    maximum=positive(limit,"limit",maximum=100)
    _committable(conn)
    with conn.transaction():
        conn.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        if conn.execute(SQL_WORKSPACE_SHARE,(pid,)).fetchone() is None:
            reject("project_id","SRE workspace unavailable","rights_denied")
        run=conn.execute(SQL_RUN,(pid,rid)).fetchone()
        if (run is None or run[0]!="live" or run[2] is not False
                or run[1] not in ("queued","running","partial","completed")):
            reject("run_id","foreign/cancelled/non-LIVE run is not reviewable",
                   "rights_denied")
        rows=conn.execute(SQL_QUEUE,(pid,rid,maximum+1)).fetchall()
        # Also authorize the one extra row used to flag truncation. Otherwise
        # the truncated bit leaks the existence of a now-revoked D1 source.
        for row in rows:
            _verify_current_d1(conn,pid,row[8],row[9],row[10])
        payload=[]
        for row in rows[:maximum]:
            (iid,key,base,model,receipt,req_sha,quote_sha,catalog_sha,
             video,content_id,content_sha,
             review_id,revision,human_decision,action,reviewer)=row
            if model not in DECISIONS or (review_id is None)!=(revision is None):
                reject("queue","unverified original or invalid review state",
                       "invalid_evidence")
            payload.append({
                "item_id":iid,"category_key":key,
                "original_result_id":base,"original_model_decision":model,
                "receipt_id":receipt,"request_sha256":req_sha,
                "quote_sha256":quote_sha,"catalog_sha256":catalog_sha,
                "effective_decision":human_decision if review_id else model,
                "review_id":review_id,"revision":revision or 0,
                "review_state":"reviewed" if review_id else "awaiting_review",
                "source_rights_rechecked":True,
            })
    return {
        "schema_version":VERSION,"project_id":pid,"run_id":rid,
        "reviewable_verified_pair_count_in_page":len(payload),
        "truncated":len(rows)>maximum,
        "rows":payload,
        "paid_post_allowed":False,"model_results_mutated":False,
        "all_hypotheses_complete":False,
    }


__all__=(
    "VERSION","human_review_confirmation","record_live_sre_human_review",
    "read_effective_live_sre_decision","read_sre_human_review_history",
    "read_live_sre_human_review_queue",
)
