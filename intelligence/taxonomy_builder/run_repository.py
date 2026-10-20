"""Explicit, sequential, project-scoped Taxonomy Builder execution.

No background loops/retries. Each HTTP 'advance' dispatches at most ONE paid
request after a transaction reserves funds. All human approvals are immutable.
"""
from __future__ import annotations
from datetime import datetime,timezone
from uuid import UUID
from decimal import Decimal
import json
from .pipeline_contracts import PipelineError,sha256,canonical
from .cost_repository import _approved,_price_snapshot,_source,_confirmed_pipeline,_latest_quote,_consent,_now
from .pipeline_repository import _pid,_connect
from .cost_estimator import amount,usd
from .provider import request_payload,reservation_cost,invoke,ProviderUncertain
from .run_contracts import validate_facts,review_facts,validate_topics,review_topics,validate_critic
from projects import _PROFILE_WRITE_LOCK

STATE_TO_STAGE={
 "extraction_pending":("fact_extraction","extraction_running"),
 "generation_pending":("taxonomy_generation","generation_running"),
 "critic_pending":("taxonomy_critic","critic_running")
}
CURRENT_STATES=tuple(STATE_TO_STAGE)
STOPPED={"cancelled","approved","failed","uncertain","paused_budget","paused_price"}

def _id(value,label):
    try:return str(UUID(str(value)))
    except (ValueError,TypeError,AttributeError) as exc:
        raise PipelineError("Invalid "+label,400) from exc

def _row(conn,pid,run_id,lock=False):
    suffix=" FOR UPDATE" if lock else ""
    row=conn.execute("""SELECT id,status,profile_snapshot,config_snapshot,
          model_snapshots,authorized_usd,spent_usd,reserved_usd,
          quote_id,consent_id,pipeline_id,profile_revision,profile_hash
          FROM tb_runs WHERE id=%s AND project_id=%s"""+suffix,
          (_id(run_id,"run_id"),pid)).fetchone()
    if not row:raise PipelineError("Run not found in project",404)
    return {"run_id":str(row[0]),"status":row[1],"profile":row[2],
            "config":row[3],"models":row[4],"authorized_usd":row[5],
            "spent_usd":row[6],"reserved_usd":row[7],
            "quote_id":str(row[8]),"consent_id":str(row[9]),
            "pipeline_id":str(row[10]),"profile_revision":row[11],
            "profile_hash":row[12]}

def _attempt(conn,pid,rid,role):
    row=conn.execute("""SELECT id,status,output,reservation_usd,charged_usd
        FROM tb_stage_attempts WHERE project_id=%s AND run_id=%s AND role=%s""",
        (pid,rid,role)).fetchone()
    return {"id":str(row[0]),"status":row[1],"output":row[2],
            "reservation_usd":row[3],"charged_usd":row[4]} if row else None

def _reviewed(conn,pid,rid):
    row=conn.execute("""SELECT approved_facts FROM tb_fact_review_submissions
          WHERE project_id=%s AND run_id=%s""",(pid,rid)).fetchone()
    return row[0] if row else None

def _generation(conn,pid,rid):
    a=_attempt(conn,pid,rid,"taxonomy_generation")
    if not a or a["status"]!="completed":raise PipelineError("Taxonomy not yet generated",409)
    return a["output"]["topics"]

def _inputs(conn,pid,run,role):
    if role=="fact_extraction":
        return {"questionnaire":run["profile"],"niche":"health_fitness",
                "research_goal":"maximize_views","attachments":"excluded"}
    facts=_reviewed(conn,pid,run["run_id"])
    if not facts:raise PipelineError("Human fact review required",409)
    if role=="taxonomy_generation":
        return {"approved_facts":facts,"niche":"health_fitness",
                "research_goal":"maximize_views","request":"Generate 6-10 distinct useful topics"}
    return {"approved_facts":facts,"topics":_generation(conn,pid,run["run_id"]),
            "review_only":True}

def _spec(run,role):
    for st in run["config"]["stages"]:
        if st["role"]==role and st.get("enabled",True):
            if role not in run["models"]:raise PipelineError("Missing pinned model snapshot")
            return st,run["models"][role]
    raise PipelineError("Pipeline stage is disabled",409)

def start_run(project_id,quote_id,consent_id,idempotency_key):
    pid=_pid(project_id)
    if not isinstance(idempotency_key,str) or not 12<=len(idempotency_key)<=128:
        raise PipelineError("Provide a unique idempotency key of 12-128 chars")
    with _PROFILE_WRITE_LOCK:
        try:
            with _connect() as conn:
                with conn.transaction():
                    rev,digest,pipe=_confirmed_pipeline(conn,pid)
                    price_hash=_price_snapshot(conn,pipe)
                    quote=_latest_quote(conn,pid,pipe["pipeline_id"])
                    if not quote or quote["quote_id"]!=_id(quote_id,"quote_id"):
                        raise PipelineError("Current quote is required",409)
                    if _now()>=datetime.fromisoformat(quote["expires_at"]):
                        raise PipelineError("Quote expired before execution; obtain a new quote",409)
                    if quote["details"]["pricing_sha256"]!=price_hash:
                        raise PipelineError("Pricing has changed",409)
                    accepted=_consent(conn,pid,quote["quote_id"])
                    if not accepted or accepted["consent_id"]!=_id(consent_id,"consent_id") or accepted["revoked"]:
                        raise PipelineError("Active consent for this exact quote required",409)
                    source=_source(pid,rev,digest)
                    if quote["details"]["source_hash"]!=sha256({
                        "profile_hash":digest,"documents":source["research_documents"]}):
                        raise PipelineError("Source material changed",409)
                    # Text-only scope stays explicit even if the user has attachments.
                    current=conn.execute("""SELECT id,quote_id,consent_id FROM tb_runs
                        WHERE project_id=%s AND idempotency_key=%s FOR UPDATE""",
                        (pid,idempotency_key)).fetchone()
                    if current:
                        if str(current[1])!=quote["quote_id"] or str(current[2])!=accepted["consent_id"]:
                            raise PipelineError("Idempotency key already used for different inputs",409)
                        return {"run_id":str(current[0]),"status":"existing","paid_calls":0}
                    # Approval of a limit is ONE-RUN authorization, not a
                    # reusable spending wallet. Also protected by unique index.
                    already_used=conn.execute("""SELECT id FROM tb_runs
                        WHERE consent_id=%s""",(accepted["consent_id"],)).fetchone()
                    if already_used:
                        raise PipelineError("This budget consent was already used for a run; create a new quote and consent",409)
                    row=conn.execute("""INSERT INTO tb_runs
                        (project_id,pipeline_id,quote_id,consent_id,profile_revision,
                         profile_hash,idempotency_key,profile_snapshot,config_snapshot,
                         model_snapshots,input_sha256,authorized_usd)
                        VALUES (%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s::jsonb,%s,%s)
                        RETURNING id""",
                        (pid,pipe["pipeline_id"],quote["quote_id"],accepted["consent_id"],
                         rev,digest,idempotency_key,canonical(source["profile"]),
                         canonical(pipe["config"]),canonical(pipe["snapshots"]),
                         quote["details"]["source_hash"],accepted["authorized_usd"])).fetchone()
                    return {"run_id":str(row[0]),"status":"extraction_pending","paid_calls":0}
        except PipelineError:raise
        except Exception as exc:
            raise PipelineError("Run could not be created",503) from exc

def _stage_output(role,provider_result,profile,facts,topics):
    if provider_result["finish_reason"] not in ("stop",):
        raise PipelineError("Provider did not finish a complete JSON response")
    raw=provider_result["raw_json"]
    if role=="fact_extraction":return {"facts":validate_facts(raw,profile)}
    if role=="taxonomy_generation":return {"topics":validate_topics(raw,facts)}
    return {"findings":validate_critic(raw,topics)}

def advance_run(project_id,run_id,*,provider_call=None,api_key=None):
    """Reserve in DB, perform one call, finalize. Fail closed on interrupted calls."""
    pid=_pid(project_id);rid=_id(run_id,"run_id")
    # Missing credentials must not create a paid/uncertain attempt.
    if provider_call is None and api_key is None:
        from .provider import resolve_key
        api_key=resolve_key()
        if not api_key:raise PipelineError("Set the OpenRouter key in ContentMachine Settings",503)
    with _connect() as conn:
        with conn.transaction():
            run=_row(conn,pid,rid,lock=True)
            if run["status"] not in STATE_TO_STAGE:
                raise PipelineError("The run is not waiting for a model request",409)
            role,running=STATE_TO_STAGE[run["status"]]
            if _attempt(conn,pid,rid,role):
                raise PipelineError("An attempt already exists; paid retries are disabled",409)
            revoked=conn.execute("""SELECT 1 FROM tb_spending_revocations
                WHERE consent_id=%s AND project_id=%s""",
                (run["consent_id"],pid)).fetchone()
            if revoked:raise PipelineError("Spending consent was revoked",409)
            st,snapshot=_spec(run,role)
            inputs=_inputs(conn,pid,run,role)
            payload=request_payload(st,snapshot,inputs)
            reserve=reservation_cost(payload,snapshot)
            remaining=run["authorized_usd"]-run["spent_usd"]-run["reserved_usd"]
            if reserve>remaining:
                conn.execute("""UPDATE tb_runs SET status='paused_budget',updated_at=now()
                    WHERE id=%s""",(rid,))
                return {"status":"paused_budget","required_reservation_usd":str(reserve),
                        "remaining_usd":str(remaining),"paid_calls":0}
            # Catalog freshness is checked on every paid stage, but its tariffs
            # must still match the originally approved price snapshot.
            from .catalog import latest_verified
            available=latest_verified(conn,{st["model_id"]})[st["model_id"]]
            if (available["catalog_version_id"]!=st["catalog_version_id"] or
                available["catalog_sha256"]!=snapshot["catalog_sha256"]):
                conn.execute("""UPDATE tb_runs SET status='paused_price',updated_at=now()
                    WHERE id=%s""",(rid,))
                return {"status":"paused_price","paid_calls":0}
            attempt=conn.execute("""INSERT INTO tb_stage_attempts
                  (project_id,run_id,role,status,reservation_usd,model_id,reasoning,
                   temperature,prompt_version,prompt_sha256,request_sha256)
                  VALUES (%s,%s,%s,'reserved',%s,%s,%s::jsonb,%s,%s,%s,%s)
                  RETURNING id""",
                  (pid,rid,role,reserve,st["model_id"],canonical(st["reasoning"]),
                   st["temperature"],st["prompt_version"],sha256(payload["messages"]),
                   sha256(payload))).fetchone()[0]
            conn.execute("""UPDATE tb_runs SET reserved_usd=reserved_usd+%s,
                    status=%s,updated_at=now() WHERE id=%s""",(reserve,running,rid))
    # IMPORTANT: the external request is outside the transaction. If the process
    # dies, the reserved attempt stays, and further paid requests are disabled.
    outcome=None; failure=None; uncertain=False
    try:
        outcome=invoke(st,snapshot,inputs,sender=provider_call,key=api_key)
        if outcome["charged_usd"]>reserve:
            failure="Actual provider charge exceeded reserved maximum; stopped"
    except ProviderUncertain as exc:
        failure=str(exc);uncertain=True
    except PipelineError as exc:
        # If failure occurs after dispatch, usage may be unknown; treat as such.
        failure=str(exc);uncertain=True
    except Exception:
        failure="Unexpected provider request outcome";uncertain=True
    with _connect() as conn:
        with conn.transaction():
            run=_row(conn,pid,rid,lock=True)
            if run["status"]!=running:
                raise PipelineError("Run state changed during provider request; reconcile attempt",409)
            if uncertain or outcome is None:
                conn.execute("""UPDATE tb_stage_attempts SET status='uncertain',
                  error_message=%s,ended_at=now() WHERE id=%s""",(failure,attempt))
                conn.execute("""UPDATE tb_runs SET status='uncertain',updated_at=now()
                  WHERE id=%s""",(rid,))
                return {"status":"uncertain","message":failure,
                        "reservation_usd":str(reserve),"paid_calls":1}
            charged=outcome["charged_usd"]
            model_mismatch=outcome["model_id"] not in (None,st["model_id"])
            approved=_reviewed(conn,pid,rid)
            original=_attempt(conn,pid,rid,"taxonomy_generation")
            issues=None;output=None
            try:
                if model_mismatch:
                    raise PipelineError("Provider returned a different model than requested")
                output=_stage_output(role,outcome,run["profile"],
                  approved,original["output"]["topics"] if original and original["output"] else None)
            except PipelineError as exc:
                issues=str(exc)
            next_status=("fact_review" if role=="fact_extraction" else
                         "critic_pending" if role=="taxonomy_generation" and any(
                            s["role"]=="taxonomy_critic" and s.get("enabled",True)
                            for s in run["config"]["stages"]) else "topic_review")
            if role=="taxonomy_critic":next_status="topic_review"
            status="error" if issues or failure else "completed"
            if charged>reserve:
                status="error";next_status="paused_budget"
            elif issues:
                next_status="failed"
            conn.execute("""UPDATE tb_stage_attempts SET status=%s,charged_usd=%s,
                    usage=%s::jsonb,output=%s::jsonb,provider_response_id=%s,
                    error_message=%s,ended_at=now() WHERE id=%s""",
                    (status,charged,canonical(outcome["usage"]),
                     canonical(output) if output else None,outcome["response_id"],
                     issues or failure,attempt))
            conn.execute("""UPDATE tb_runs SET spent_usd=spent_usd+%s,
                    reserved_usd=reserved_usd-%s,status=%s,updated_at=now()
                    WHERE id=%s""",(charged,reserve,next_status,rid))
            return {"status":next_status,"role":role,"charged_usd":str(charged),
                    "paid_calls":1,"message":issues or failure,
                    "provider_response_id":outcome["response_id"]}

def submit_fact_review(project_id,run_id,decisions):
    pid=_pid(project_id);rid=_id(run_id,"run_id")
    try:
        with _connect() as conn:
            with conn.transaction():
                run=_row(conn,pid,rid,True)
                if run["status"]!="fact_review":raise PipelineError("Facts are not awaiting review",409)
                extraction=_attempt(conn,pid,rid,"fact_extraction")
                approved=review_facts(extraction["output"]["facts"],decisions)
                conn.execute("""INSERT INTO tb_fact_review_submissions
                   (project_id,run_id,approved_facts,reviewed_by)
                   VALUES (%s,%s,%s::jsonb,'local_owner')""",
                   (pid,rid,canonical(approved)))
                conn.execute("""UPDATE tb_runs SET status='generation_pending',
                    updated_at=now() WHERE id=%s""",(rid,))
                return {"status":"generation_pending","approved_facts":approved}
    except PipelineError:raise
    except Exception as exc:raise PipelineError("Unable to save fact review",503) from exc

def publish_reviewed_topics(project_id,run_id,choices):
    pid=_pid(project_id);rid=_id(run_id,"run_id")
    try:
        with _connect() as conn:
            with conn.transaction():
                run=_row(conn,pid,rid,True)
                if run["status"]!="topic_review":raise PipelineError("Topics are not awaiting review",409)
                topics=_generation(conn,pid,rid)
                approved=review_topics(topics,choices,_reviewed(conn,pid,rid))
                version=conn.execute("""SELECT COALESCE(MAX(taxonomy_version),0)+1
                     FROM tb_taxonomy_publications WHERE project_id=%s""",(pid,)).fetchone()[0]
                saved=conn.execute("""INSERT INTO tb_taxonomy_publications
                      (project_id,run_id,taxonomy_version,approved_topics,approved_by)
                      VALUES (%s,%s,%s,%s::jsonb,'local_owner') RETURNING id""",
                      (pid,rid,version,canonical(approved))).fetchone()[0]
                from .sre_bridge import mirror_approved_taxonomy
                sre=mirror_approved_taxonomy(conn,pid,run,str(saved),
                    approved,_reviewed(conn,pid,rid))
                conn.execute("""UPDATE tb_runs SET status='approved',updated_at=now()
                      WHERE id=%s""",(rid,))
                return {"status":"approved","taxonomy_id":str(saved),
                        "taxonomy_version":version,"topics":approved,**sre}
    except PipelineError:raise
    except Exception as exc:raise PipelineError("Cannot publish reviewed taxonomy",503) from exc

def cancel_run(project_id,run_id):
    pid=_pid(project_id);rid=_id(run_id,"run_id")
    with _connect() as conn:
        with conn.transaction():
            run=_row(conn,pid,rid,True)
            if run["status"] in ("extraction_running","generation_running","critic_running","uncertain"):
                raise PipelineError("In-flight or uncertain provider request cannot be cancelled/retried safely",409)
            if run["status"]=="approved":raise PipelineError("Published taxonomy cannot be cancelled",409)
            if run["status"]!="cancelled":
                conn.execute("""UPDATE tb_runs SET status='cancelled',updated_at=now()
                     WHERE id=%s""",(rid,))
            return {"status":"cancelled","spent_usd":str(run["spent_usd"])}

def get_run(project_id,run_id):
    pid=_pid(project_id);rid=_id(run_id,"run_id")
    with _connect() as conn:
        run=_row(conn,pid,rid)
        attempts=conn.execute("""SELECT role,status,charged_usd,reservation_usd,usage,
              output,error_message,provider_response_id
              FROM tb_stage_attempts WHERE run_id=%s AND project_id=%s ORDER BY started_at""",
              (rid,pid)).fetchall()
        result={k:(str(v) if isinstance(v,Decimal) else v) for k,v in run.items()
                if k not in ("models","config")}
        result["stages"]=[{"role":a[0],"status":a[1],
             "charged_usd":str(a[2]) if a[2] is not None else None,
             "reservation_usd":str(a[3]),"usage":a[4],"error":a[6],
             "provider_response_id":a[7]} for a in attempts]
        result["fact_proposals"]=(next((a[5]["facts"] for a in attempts
          if a[0]=="fact_extraction" and a[5]),[]))
        result["approved_facts"]=_reviewed(conn,pid,rid)
        result["topic_proposals"]=(next((a[5]["topics"] for a in attempts
          if a[0]=="taxonomy_generation" and a[5]),[]))
        result["critic_findings"]=(next((a[5]["findings"] for a in attempts
          if a[0]=="taxonomy_critic" and a[5]),[]))
        published=conn.execute("""SELECT taxonomy_version,approved_topics FROM tb_taxonomy_publications
           WHERE project_id=%s AND run_id=%s""",(pid,rid)).fetchone()
        result["publication"]={"version":published[0],"topics":published[1]} if published else None
        return result
