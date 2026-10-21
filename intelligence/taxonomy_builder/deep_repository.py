"""Paid, manually advanced two-pass Opus taxonomy with two human approval gates.

Shares the original Builder run's immutable Opus snapshot and authorized budget,
but uses its OWN stage ledger and review records. No automatic retries or model
dispatch from GET, quality gates or the human-review endpoints.
"""
from __future__ import annotations
from decimal import Decimal
from .pipeline_contracts import PipelineError,canonical,sha256
from .pipeline_repository import _pid,_connect
from .run_repository import _id,_row,_reviewed,_spec
from .provider import request_payload,reservation_cost,invoke,resolve_key,ProviderUncertain
from .deep_contracts import validate_tree,quality_gate,review_nodes
from .catalog import latest_verified

ACTIVE={"pass1_pending":"pass1_running",
        "pass2_pending":"pass2_running",
        "expand_pending":"expand_running"}
MAX_EXPANSIONS=2

def _deep(conn,pid,rid,lock=False):
    r=conn.execute("""SELECT status,expansion_count,quality_report
       FROM tb_deep_runs WHERE project_id=%s AND run_id=%s"""+
       (" FOR UPDATE" if lock else ""), (pid,rid)).fetchone()
    return ({"status":r[0],"expansion_count":r[1],"quality_report":r[2]} if r else None)

def _attempts(conn,pid,rid):
    return conn.execute("""SELECT stage,status,reservation_usd,charged_usd,
        output,error_message,usage,response_id FROM tb_deep_attempts
        WHERE project_id=%s AND run_id=%s ORDER BY created_at,id""",(pid,rid)).fetchall()

def _review(conn,pid,rid,index):
    row=conn.execute("""SELECT approved_nodes FROM tb_deep_reviews
       WHERE project_id=%s AND run_id=%s AND review_no=%s""",
       (pid,rid,index)).fetchone()
    return row[0] if row else None

def _last_tree(conn,pid,rid):
    rows=_attempts(conn,pid,rid)
    for a in reversed(rows):
        if a[1]=="completed" and isinstance(a[4],dict) and a[4].get("nodes"):
            return a[4]["nodes"],a[4].get("duplicates",[])
    return [],[]

def _stage(run,deep,role):
    stage,snapshot=_spec(run,"taxonomy_generation")
    if stage["model_id"]!="anthropic/claude-opus-5.5":
        raise PipelineError("Deep taxonomy requires Opus 5.5 in the Generation stage",409)
    # No implicit increase over the user's pinned generation token cap.
    spec={**stage,"role":role,"prompt_version":"deep-v1",
          "max_output_tokens":min(stage["max_output_tokens"],16000)}
    if spec["max_output_tokens"]<4096:
        raise PipelineError("Opus deep mode needs at least 4096 generation tokens",422)
    return spec,snapshot

def _input(conn,pid,rid,run,deep,role):
    facts=_reviewed(conn,pid,rid)
    if not facts:raise PipelineError("Human-approved facts required first",409)
    if role=="deep_first":
        return {"approved_facts":facts,"product_type":run["profile"].get("product_type",""),
                "research_goal":"short form video research; maximize useful category coverage"}
    prior=_review(conn,pid,rid,1)
    if not prior:raise PipelineError("First human review is required",409)
    if role=="deep_second":
        matrix=quality_gate(prior,facts)
        return {"approved_facts":facts,"human_approved_tree":prior,
                "fact_coverage_matrix":matrix["coverage_matrix"],
                "task":"expand ONLY first-review-approved directions, repair gaps"}
    previous,_=_last_tree(conn,pid,rid)
    if not previous:raise PipelineError("No previous model tree to expand",409)
    gate=deep["quality_report"] or {}
    # Avoid re-sending duplicate 48-node trees and giant coverage matrices:
    # the previous full tree is authoritative; the H1 lock is represented by
    # immutable IDs and parent paths, and the gate by actionable gaps.
    protected=[{"id":n["id"],"name":n["name"],"kind":n["kind"],
                "parent_id":n["parent_id"]} for n in prior]
    actionable={"gaps":gate.get("gaps",[]),
        "branch_gaps":gate.get("branch_gaps",[]),
        "duplicates":gate.get("duplicates",[]),
        "new_direction_ids":gate.get("new_direction_ids",[])}
    return {"approved_facts":facts,"human_review1_protected":protected,
            "latest_tree":previous,"quality_issues_to_repair":actionable,
            "task":"repair every severe gap and claimed duplicate; test for additional material directions"}

def get_deep(project_id,run_id):
    pid=_pid(project_id);rid=_id(run_id,"run_id")
    with _connect() as conn:
        run=_row(conn,pid,rid)
        deep=_deep(conn,pid,rid)
        facts=_reviewed(conn,pid,rid)
        generation=next((x for x in run["config"]["stages"]
           if x["role"]=="taxonomy_generation"),{})
        eligible=(not deep and run["status"]=="generation_pending" and bool(facts)
                  and generation.get("model_id")=="anthropic/claude-opus-5.5")
        if not deep:
            estimate=None
            if eligible:
                try:
                    spec,snapshot=_stage(run,None,"deep_first")
                    reserve=reservation_cost(request_payload(spec,snapshot,
                        _input(conn,pid,rid,run,None,"deep_first")),snapshot)
                    remaining=run["authorized_usd"]-run["spent_usd"]-run["reserved_usd"]
                    estimate={"first_pass_conservative_reservation_usd":str(reserve),
                        "two_pass_rough_reservation_usd":str(reserve*2),
                        "remaining_authorized_usd":str(remaining),
                        "note":"Second pass and optional saturation prompts can cost more than this rough 2x illustration"}
                except PipelineError:
                    estimate=None
            return {"mode":"standard","eligible":eligible,
                "cost_preview":estimate,
                "eligibility_reason":None if eligible else
                    "Confirm facts and choose Claude Opus 5.5 for taxonomy generation"}
        attempts=_attempts(conn,pid,rid)
        proposal,_=_last_tree(conn,pid,rid)
        if deep["status"]=="review1":
            proposal=next((a[4]["nodes"] for a in attempts
                if a[0]=="deep_first" and a[1]=="completed"),[])
        if deep["status"]=="review2":
            proposal,_=_last_tree(conn,pid,rid)
        published=conn.execute("""SELECT tree FROM tb_deep_publications
          WHERE run_id=%s AND project_id=%s""",(rid,pid)).fetchone()
        return {"mode":"deep","status":deep["status"],
            "run_id":rid,"model":"anthropic/claude-opus-5.5",
            "expansion_count":deep["expansion_count"],
            "max_expansions":MAX_EXPANSIONS,
            "authorized_usd":str(run["authorized_usd"]),
            "spent_usd":str(run["spent_usd"]),
            "reserved_usd":str(run["reserved_usd"]),
            "facts":facts,"nodes":published[0] if published else proposal,
            "human_review1":_review(conn,pid,rid,1),
            "human_review2":_review(conn,pid,rid,2),
            "quality":deep["quality_report"],
            "attempts":[{"stage":x[0],"status":x[1],
                "quality_report":x[4].get("quality_report") if isinstance(x[4],dict) else None,
                "reservation_usd":str(x[2]),
                "charged_usd":str(x[3]) if x[3] is not None else None,
                "error":x[5],"usage":x[6],"response_id":x[7]} for x in attempts],
            "immutable":bool(published)}

def start_deep(project_id,run_id):
    pid=_pid(project_id);rid=_id(run_id,"run_id")
    with _connect() as conn:
        with conn.transaction():
            run=_row(conn,pid,rid,True)
            if run["status"]!="generation_pending" or not _reviewed(conn,pid,rid):
                raise PipelineError("Start deep mode only after first human-approved fact review",409)
            if _deep(conn,pid,rid):raise PipelineError("Deep mode already started",409)
            if conn.execute("""SELECT 1 FROM tb_stage_attempts WHERE run_id=%s
                 AND role='taxonomy_generation'""",(rid,)).fetchone():
                raise PipelineError("Flat generation was already called",409)
            spec,snapshot=_stage(run,None,"deep_first")
            reserve=reservation_cost(request_payload(spec,snapshot,
                         _input(conn,pid,rid,run,None,"deep_first")),snapshot)
            remaining=run["authorized_usd"]-run["spent_usd"]-run["reserved_usd"]
            if reserve*2>remaining:
                raise PipelineError("Budget cannot conservatively cover two main Opus calls. Set a larger authorized budget in a NEW run before starting deep mode.",409)
            conn.execute("""INSERT INTO tb_deep_runs(run_id,project_id,status)
                         VALUES (%s,%s,'pass1_pending')""",(rid,pid))
            return {"status":"pass1_pending","paid_calls":0,
                    "first_pass_reservation_usd":str(reserve),
                    "note":"Two Opus passes and possible saturation checks are separately paid; every call requires approval"}

def advance_deep(project_id,run_id,*,sender=None,api_key=None):
    pid=_pid(project_id);rid=_id(run_id,"run_id")
    if sender is None and api_key is None:
        api_key=resolve_key()
        if not api_key:raise PipelineError("Set the OpenRouter key in ContentMachine Settings",503)
    with _connect() as conn:
        with conn.transaction():
            run=_row(conn,pid,rid,True)
            deep=_deep(conn,pid,rid,True)
            if not deep or deep["status"] not in ACTIVE:
                raise PipelineError("This stage is not awaiting a manual Opus request",409)
            pending=deep["status"];running=ACTIVE[pending]
            iteration=deep["expansion_count"]+1
            role=("deep_first" if pending=="pass1_pending" else
                  "deep_second" if pending=="pass2_pending" else "deep_expand")
            key=("deep_first" if role=="deep_first" else
                 "deep_second" if role=="deep_second" else "deep_expand_"+str(iteration))
            if iteration>MAX_EXPANSIONS and role=="deep_expand":
                raise PipelineError("Saturation request limit reached",409)
            if conn.execute("""SELECT 1 FROM tb_deep_attempts
                 WHERE project_id=%s AND run_id=%s AND stage=%s""",
                 (pid,rid,key)).fetchone():
                raise PipelineError("Paid retry disabled: this stage was already sent",409)
            if conn.execute("""SELECT 1 FROM tb_spending_revocations
                 WHERE project_id=%s AND consent_id=%s""",
                 (pid,run["consent_id"])).fetchone():
                raise PipelineError("Budget consent revoked",409)
            spec,snapshot=_stage(run,deep,role)
            inp=_input(conn,pid,rid,run,deep,role)
            payload=request_payload(spec,snapshot,inp)
            reserve=reservation_cost(payload,snapshot)
            if reserve>run["authorized_usd"]-run["spent_usd"]-run["reserved_usd"]:
                conn.execute("""UPDATE tb_deep_runs SET status='paused_budget',updated_at=now()
                   WHERE project_id=%s AND run_id=%s""",(pid,rid))
                return {"status":"paused_budget","required_usd":str(reserve),"paid_calls":0}
            available=latest_verified(conn,{spec["model_id"]}).get(spec["model_id"])
            if not available or available["catalog_version_id"]!=spec["catalog_version_id"] or available["catalog_sha256"]!=snapshot["catalog_sha256"]:
                conn.execute("""UPDATE tb_deep_runs SET status='paused_price',updated_at=now()
                   WHERE project_id=%s AND run_id=%s""",(pid,rid))
                return {"status":"paused_price","paid_calls":0}
            aid=conn.execute("""INSERT INTO tb_deep_attempts
                (project_id,run_id,stage,status,reservation_usd,model_id,
                 request_sha256,prompt_sha256)
                VALUES (%s,%s,%s,'reserved',%s,%s,%s,%s) RETURNING id""",
                (pid,rid,key,reserve,spec["model_id"],sha256(payload),
                 sha256(payload["messages"]))).fetchone()[0]
            conn.execute("""UPDATE tb_runs SET reserved_usd=reserved_usd+%s,updated_at=now()
                 WHERE id=%s AND project_id=%s""",(reserve,rid,pid))
            conn.execute("""UPDATE tb_deep_runs SET status=%s,
                 updated_at=now() WHERE run_id=%s AND project_id=%s""",
                 (running,rid,pid))
    # External call strictly outside DB transaction; only one billable POST.
    result=None;error=None;uncertain=False
    try:
        result=invoke(spec,snapshot,inp,sender=sender,key=api_key,timeout=300)
    except ProviderUncertain as ex:
        error=str(ex);uncertain=True
    except PipelineError as ex:
        error=str(ex);uncertain=True
    except Exception:
        error="Unexpected provider outcome. Billing unknown.";uncertain=True
    with _connect() as conn:
        with conn.transaction():
            locked=_row(conn,pid,rid,True)
            dr=_deep(conn,pid,rid,True)
            if dr["status"]!=running:
                raise PipelineError("Deep stage changed during provider request, reconcile manually",409)
            if uncertain or result is None:
                conn.execute("""UPDATE tb_deep_attempts SET status='uncertain',
                   error_message=%s,completed_at=now() WHERE id=%s""",(error,aid))
                conn.execute("""UPDATE tb_deep_runs SET status='uncertain',updated_at=now()
                   WHERE run_id=%s AND project_id=%s""",(rid,pid))
                return {"status":"uncertain","paid_calls":1,"message":error,
                        "billing_unknown":True}
            charged=result["charged_usd"]
            issue=None;output=None;quality=None
            try:
                if result["model_id"] not in (None,spec["model_id"]):
                    raise PipelineError("Provider model differs from approved Opus 5.5",422)
                if result["finish_reason"]!="stop" or not isinstance(result["raw_json"],dict):
                    raise PipelineError("Opus did not finish a complete structured JSON response",422)
                obj=result["raw_json"]
                output={"nodes":validate_tree(obj.get("nodes"),_reviewed(conn,pid,rid))}
                prior=_review(conn,pid,rid,1)
                if prior:
                    approved_roots={n["id"]:n for n in prior if n["kind"]=="direction"}
                    new_id={n["id"]:n for n in output["nodes"]}
                    if any(ident not in new_id or
                           new_id[ident]["name"]!=root["name"] or
                           new_id[ident]["kind"]!="direction" for ident,root in approved_roots.items()):
                        raise PipelineError("Opus changed a direction fixed by Human Review 1",422)
                    # Return all H1 nodes; do not silently erase approved concepts.
                    if not {n["id"] for n in prior}.issubset(new_id):
                        raise PipelineError("Opus deleted a Human Review 1 node",422)
                if role!="deep_first":
                    pairs=obj.get("duplicates",[])
                    quality=quality_gate(output["nodes"],_reviewed(conn,pid,rid),
                        previous=prior if role=="deep_second" else _last_tree(conn,pid,rid)[0],
                        model_duplicates=pairs)
                    output["duplicates"]=pairs
                    # Persist gate evidence *for this pass*, not only the latest
                    # mutable deep_run status. Enables later audit of saturation.
                    output["quality_report"]=quality
            except PipelineError as exc:error=str(exc)
            if charged>reserve:
                error="Provider charged more than the conservative reserved maximum"
                next_state="paused_budget"
            elif error:next_state="failed"
            elif role=="deep_first":next_state="review1"
            elif quality["passed"]:next_state="review2"
            elif role=="deep_expand" and dr["expansion_count"]+1>=MAX_EXPANSIONS:
                next_state="blocked_quality"
            elif role=="deep_second":next_state="expand_pending"
            else:next_state="expand_pending"
            conn.execute("""UPDATE tb_deep_attempts SET status=%s,charged_usd=%s,
                  usage=%s::jsonb,output=%s::jsonb,response_id=%s,
                  error_message=%s,completed_at=now() WHERE id=%s""",
                  ("error" if error else "completed",charged,canonical(result["usage"]),
                   canonical(output) if output else None,result["response_id"],error,aid))
            conn.execute("""UPDATE tb_runs SET spent_usd=spent_usd+%s,
                  reserved_usd=reserved_usd-%s,updated_at=now()
                  WHERE id=%s AND project_id=%s""",(charged,reserve,rid,pid))
            conn.execute("""UPDATE tb_deep_runs SET status=%s,
                  expansion_count=expansion_count+%s,
                  quality_report=%s::jsonb,updated_at=now()
                  WHERE run_id=%s AND project_id=%s""",
                  (next_state,1 if role=="deep_expand" else 0,
                   canonical(quality) if quality else None,rid,pid))
            return {"status":next_state,"paid_calls":1,
                    "charged_usd":str(charged),"error":error,"quality":quality}

def first_review(project_id,run_id,decisions):
    pid=_pid(project_id);rid=_id(run_id,"run_id")
    with _connect() as conn:
        with conn.transaction():
            run=_row(conn,pid,rid,True);deep=_deep(conn,pid,rid,True)
            if not deep or deep["status"]!="review1":raise PipelineError("First human review is not pending",409)
            first=conn.execute("""SELECT output FROM tb_deep_attempts
               WHERE run_id=%s AND project_id=%s AND stage='deep_first' AND status='completed'""",
               (rid,pid)).fetchone()
            if not first:raise PipelineError("No first-pass Opus tree",409)
            approved=review_nodes(first[0]["nodes"],decisions,_reviewed(conn,pid,rid))
            conn.execute("""INSERT INTO tb_deep_reviews
                (run_id,project_id,review_no,approved_nodes)
                VALUES (%s,%s,1,%s::jsonb)""",(rid,pid,canonical(approved)))
            conn.execute("""UPDATE tb_deep_runs SET status='pass2_pending',
                updated_at=now() WHERE run_id=%s AND project_id=%s""",(rid,pid))
            return {"status":"pass2_pending","approved_nodes":approved,"paid_calls":0}

def final_review(project_id,run_id,decisions):
    pid=_pid(project_id);rid=_id(run_id,"run_id")
    with _connect() as conn:
        with conn.transaction():
            run=_row(conn,pid,rid,True);deep=_deep(conn,pid,rid,True)
            if not deep or deep["status"]!="review2":
                raise PipelineError("Quality gate must pass before final human approval",409)
            tree,_=_last_tree(conn,pid,rid)
            approved=review_nodes(tree,decisions,_reviewed(conn,pid,rid))
            # Final human edits can reintroduce gaps/overlaps: revalidate.
            quality=quality_gate(approved,_reviewed(conn,pid,rid),previous=None)
            if quality["severe_gap_count"] or quality["duplicate_count"]:
                raise PipelineError("Final edits introduced uncovered essential facts or duplicates. Fix before publishing.",422)
            # Human-approved append-only review, immutable hierarchy AND flat
            # SRE-compatible nodes are published atomically in one transaction.
            conn.execute("""INSERT INTO tb_deep_reviews
                (run_id,project_id,review_no,approved_nodes)
                VALUES (%s,%s,2,%s::jsonb)""",(rid,pid,canonical(approved)))
            version=conn.execute("""SELECT coalesce(max(taxonomy_version),0)+1
                FROM tb_taxonomy_publications WHERE project_id=%s""",(pid,)).fetchone()[0]
            from uuid import UUID,uuid5
            flat=[]
            for n in approved:
                # SRE category_key is UUID; model-provided short IDs remain
                # stable only *within* the original immutable tree.
                sre_key=str(uuid5(UUID(rid),n["id"]))
                flat.append({"id":sre_key,"name":n["name"],"relation":n["relation"],
                    "definition":n["definition"],"include":n["include"],"exclude":n["exclude"],
                    "fact_ids":n["source_fact_ids"],"parent_id":n["parent_id"],
                    "source_node_id":n["id"],"kind":n["kind"],"rationale":n["rationale"],
                    "positive_examples":n["examples"] or [n["name"]],
                    "negative_examples":[n["exclude"]],
                    "origin":"inferred"})
            bid=conn.execute("""INSERT INTO tb_taxonomy_publications
                (project_id,run_id,taxonomy_version,approved_topics,approved_by)
                VALUES (%s,%s,%s,%s::jsonb,'local_owner') RETURNING id""",
                (pid,rid,version,canonical(flat))).fetchone()[0]
            from .sre_bridge import mirror_approved_taxonomy
            sre=mirror_approved_taxonomy(conn,pid,run,str(bid),flat,_reviewed(conn,pid,rid))
            conn.execute("""INSERT INTO tb_deep_publications
                (run_id,project_id,builder_publication_id,tree,quality_report)
                VALUES (%s,%s,%s,%s::jsonb,%s::jsonb)""",
                (rid,pid,bid,canonical(approved),canonical(quality)))
            conn.execute("""UPDATE tb_deep_runs SET status='approved',quality_report=%s::jsonb,
                updated_at=now() WHERE run_id=%s AND project_id=%s""",
                (canonical(quality),rid,pid))
            conn.execute("""UPDATE tb_runs SET status='approved',updated_at=now()
                WHERE id=%s AND project_id=%s""",(rid,pid))
            return {"status":"approved","taxonomy_version":version,
                    "nodes":approved,"quality":quality,**sre}
