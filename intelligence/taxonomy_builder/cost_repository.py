"""Block 5: quote and budget consent persistence. NEVER starts paid work.

All mutations lock the same local questionnaire edit mutex AND the project's
PostgreSQL row. Historic quote/consent records are immutable. Execution of
consents is intentionally impossible until Block 6 implements a budgeted runner.
"""
from __future__ import annotations
from datetime import datetime, timezone, timedelta
from uuid import UUID
from .pipeline_contracts import PipelineError, sha256,canonical
from .pipeline_repository import _connect,_pid,_local_current,_confirmed
from .catalog import latest_verified
from .cost_estimator import estimate,check_budget,ESTIMATOR_VERSION,amount
from projects import get_project, taxonomy_input_record, _PROFILE_WRITE_LOCK

QUOTE_TTL=timedelta(minutes=30)

def _uuid(raw,name):
    try:
        return str(UUID(raw))
    except (ValueError,AttributeError,TypeError) as exc:
        raise PipelineError("Invalid "+name,400) from exc

def _now():
    return datetime.now(timezone.utc)

def _approved(conn,pid,rev,digest):
    row=conn.execute("""SELECT v.id,v.pipeline_version,v.config,v.model_snapshots,v.config_sha256
      FROM tb_pipeline_versions v
      JOIN tb_pipeline_approvals a ON a.pipeline_id=v.id AND a.project_id=v.project_id
      JOIN tb_pipeline_drafts d ON d.project_id=v.project_id
        AND d.profile_revision=v.profile_revision AND d.profile_hash=v.profile_hash
        AND d.draft_revision=v.source_draft_revision
      WHERE v.project_id=%s AND v.profile_revision=%s AND v.profile_hash=%s
      ORDER BY v.pipeline_version DESC LIMIT 1""",(pid,rev,digest)).fetchone()
    if not row:
        raise PipelineError("The CURRENT Pipeline draft needs approval before a quote",409)
    return {"pipeline_id":str(row[0]),"version":row[1],
            "config":row[2],"snapshots":row[3],"config_sha256":row[4]}

def _price_snapshot(conn,pipeline):
    snaps=pipeline["snapshots"]
    chosen={s["model_id"] for s in pipeline["config"]["stages"] if s.get("enabled",True)}
    current=latest_verified(conn,chosen)
    for role,snapshot in snaps.items():
        available=current.get(snapshot["model_id"])
        if (not available or available["catalog_version_id"]!=snapshot["catalog_version_id"] or
            available["catalog_sha256"]!=snapshot["catalog_sha256"]):
            raise PipelineError("A model or its pricing snapshot changed; return to Pipeline Studio and approve the new revision",409)
    return sha256({r:{"pricing":s["pricing"],"catalog_sha256":s["catalog_sha256"]}
                   for r,s in snaps.items()})

def _source(pid,rev,digest):
    record=get_project(pid)
    snapshot=taxonomy_input_record(record) if record is not None else None
    if not snapshot or snapshot.get("profile_revision")!=rev or snapshot.get("profile_hash")!=digest:
        raise PipelineError("Questionnaire changed; review and reconfirm it",409)
    return snapshot

def _confirmed_pipeline(conn,pid,expected_id=None):
    rev,digest=_local_current(pid)
    if _confirmed(conn,pid)!=(rev,digest):
        raise PipelineError("Questionnaire version changed",409)
    pipe=_approved(conn,pid,rev,digest)
    if expected_id and pipe["pipeline_id"]!=_uuid(expected_id,"pipeline_id"):
        raise PipelineError("Selected Pipeline is not the current approved version",409)
    return rev,digest,pipe

def _quote_row(row):
    if row is None:return None
    return {"quote_id":str(row[0]),"pipeline_id":str(row[1]),
        "low_usd":str(row[2]),"expected_usd":str(row[3]),
        "high_usd":str(row[4]),"currency":"USD",
        "created_at":row[5].isoformat(),"expires_at":row[6].isoformat(),
        "details":row[7]}

def _latest_quote(conn,pid,pipeline_id):
    row=conn.execute("""SELECT id,pipeline_id,low_usd,expected_usd,high_usd,
       created_at,expires_at,quote_details
       FROM tb_cost_quotes WHERE project_id=%s AND pipeline_id=%s
       ORDER BY created_at DESC,id DESC LIMIT 1""",
       (pid,pipeline_id)).fetchone()
    return _quote_row(row)

def _consent(conn,pid,quote_id):
    row=conn.execute("""SELECT c.id,c.authorized_usd,c.authorized_at,
          r.revoked_at FROM tb_spending_consents c
       LEFT JOIN tb_spending_revocations r ON r.consent_id=c.id AND r.project_id=c.project_id
       WHERE c.project_id=%s AND c.quote_id=%s""",(pid,quote_id)).fetchone()
    if not row:return None
    return {"consent_id":str(row[0]),"authorized_usd":str(row[1]),
            "authorized_at":row[2].isoformat(),"revoked":row[3] is not None}

def get_cost_state(project_id: str):
    pid=_pid(project_id)
    rev,digest=_local_current(pid)
    try:
        with _connect() as conn:
            if _confirmed(conn,pid)!=(rev,digest):
                raise PipelineError("The questionnaire needs reconfirmation",409)
            pipe=_approved(conn,pid,rev,digest)
            quote=_latest_quote(conn,pid,pipe["pipeline_id"])
            stale_reason=None
            if quote:
                if _now() >= datetime.fromisoformat(quote["expires_at"]):
                    stale_reason="The quote expired; calculate a new estimate"
                else:
                    try:_price_snapshot(conn,pipe)
                    except PipelineError as exc: stale_reason=str(exc)
                source=_source(pid,rev,digest)
                if quote["details"].get("source_hash")!=sha256({
                    "profile_hash":digest,"documents":source["research_documents"]}):
                    stale_reason="Questionnaire or research document set changed"
            consent=_consent(conn,pid,quote["quote_id"]) if quote else None
            valid=bool(quote and not stale_reason)
            return {"project_id":pid,"profile_revision":rev,
              "pipeline_id":pipe["pipeline_id"],"pipeline_version":pipe["version"],
              "config_sha256":pipe["config_sha256"],
              "quote":quote,"consent":consent if valid else None,
              "stale_reason":stale_reason,
              "status":"quote_required" if not quote else
                      "quote_stale" if not valid else
                      "spending_authorized" if consent and not consent["revoked"] else
                      "quote_ready",
              "launch_available":False}
    except PipelineError:raise
    except Exception as exc:
        raise PipelineError("Could not load quote state; check PostgreSQL",503) from exc

def create_quote(project_id:str, pipeline_id:str,force_new:bool=False):
    if type(force_new) is not bool:
        raise PipelineError('force_new must be boolean',400)
    pid=_pid(project_id)
    with _PROFILE_WRITE_LOCK:
        try:
            with _connect() as conn:
                with conn.transaction():
                    rev,digest,pipe=_confirmed_pipeline(conn,pid,pipeline_id)
                    price_hash=_price_snapshot(conn,pipe)
                    source=_source(pid,rev,digest)
                    quote_detail=estimate(source["profile"],source["research_documents"],
                                          pipe["config"],pipe["snapshots"])
                    quote_detail["source_hash"]=sha256({
                      "profile_hash":digest,"documents":source["research_documents"]})
                    quote_detail["profile_revision"]=rev
                    quote_detail["pipeline_version"]=pipe["version"]
                    quote_detail["config_sha256"]=pipe["config_sha256"]
                    quote_detail["pricing_sha256"]=price_hash
                    # Reuse the latest still-valid quote if all inputs and pricing agree.
                    last=conn.execute("""SELECT id,created_at,expires_at FROM tb_cost_quotes
                      WHERE project_id=%s AND pipeline_id=%s AND input_sha256=%s
                        AND pricing_sha256=%s AND estimator_version=%s
                      ORDER BY created_at DESC LIMIT 1""",
                      (pid,pipe["pipeline_id"],quote_detail["source_hash"],
                       price_hash,ESTIMATOR_VERSION)).fetchone()
                    if not force_new and last and last[2]>_now():
                        quote=_latest_quote(conn,pid,pipe["pipeline_id"])
                        if quote and quote["quote_id"]==str(last[0]):
                            return {"quote":quote,"reused":True,"next_step":"spending_consent"}
                    row=conn.execute("""INSERT INTO tb_cost_quotes
                     (project_id,pipeline_id,profile_revision,profile_hash,config_sha256,
                      input_sha256,pricing_sha256,estimator_version,quote_details,
                      low_usd,expected_usd,high_usd,expires_at)
                     VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s,now()+interval '30 minutes')
                     RETURNING id""",
                     (pid,pipe["pipeline_id"],rev,digest,pipe["config_sha256"],
                      quote_detail["source_hash"],price_hash,ESTIMATOR_VERSION,
                      canonical(quote_detail),quote_detail["low_usd"],
                      quote_detail["expected_usd"],quote_detail["high_usd"])).fetchone()
                    quote=_latest_quote(conn,pid,pipe["pipeline_id"])
                    return {"quote":quote,"reused":False,"next_step":"spending_consent"}
        except PipelineError:raise
        except Exception as exc:
            raise PipelineError("Could not persist cost estimate",503) from exc

def authorize_budget(project_id:str,quote_id:str,authorized_usd,
                     acknowledge_documents_excluded:bool=False,
                     approved_by:str="local_owner"):
    pid=_pid(project_id)
    if type(acknowledge_documents_excluded) is not bool:
        raise PipelineError("Document scope acknowledgement must be boolean",400)
    if not isinstance(approved_by,str) or not approved_by.strip():
        raise PipelineError("Budget approver is required",400)
    with _PROFILE_WRITE_LOCK:
        try:
            with _connect() as conn:
                with conn.transaction():
                    rev,digest,pipe=_confirmed_pipeline(conn,pid)
                    _price_snapshot(conn,pipe)
                    quote=_latest_quote(conn,pid,pipe["pipeline_id"])
                    if not quote or quote["quote_id"]!=_uuid(quote_id,"quote_id"):
                        raise PipelineError("The quote is not the latest for this Pipeline",409)
                    if _now()>=datetime.fromisoformat(quote["expires_at"]):
                        raise PipelineError("Quote expired; recalculate the estimate",409)
                    if quote["details"]["config_sha256"]!=pipe["config_sha256"]:
                        raise PipelineError("Pipeline configuration changed",409)
                    source=_source(pid,rev,digest)
                    if quote["details"]["source_hash"]!=sha256({
                        "profile_hash":digest,"documents":source["research_documents"]}):
                        raise PipelineError("Inputs changed; calculate a new estimate",409)
                    if (quote["details"]["assumptions"]["excluded_documents_count"] and
                        not acknowledge_documents_excluded):
                        raise PipelineError("Confirm that uploaded documents are NOT included in this estimate",422)
                    budget=check_budget(authorized_usd,quote["high_usd"])
                    existing=_consent(conn,pid,quote["quote_id"])
                    if existing:
                        if existing["revoked"] or amount(existing["authorized_usd"],"existing budget")!=budget:
                            raise PipelineError("This quote already has a different or revoked consent; get a new quote",409)
                        return {"consent":existing,"status":"spending_authorized",
                                "launch_available":False}
                    row=conn.execute("""INSERT INTO tb_spending_consents
                         (project_id,pipeline_id,quote_id,authorized_usd,authorized_by)
                         VALUES (%s,%s,%s,%s,%s) RETURNING id,authorized_at""",
                         (pid,pipe["pipeline_id"],quote["quote_id"],budget,approved_by.strip())).fetchone()
                    return {"consent":{"consent_id":str(row[0]),"authorized_usd":str(budget),
                                        "authorized_at":row[1].isoformat(),"revoked":False},
                            "status":"spending_authorized","launch_available":False}
        except PipelineError:raise
        except Exception as exc:
            raise PipelineError("Unable to record budget consent",503) from exc

def revoke_consent(project_id:str,consent_id:str,actor:str="local_owner"):
    pid=_pid(project_id)
    try:
        with _connect() as conn:
            with conn.transaction():
                result=conn.execute("""SELECT 1 FROM tb_spending_consents
                  WHERE id=%s AND project_id=%s""",(_uuid(consent_id,"consent_id"),pid)).fetchone()
                if not result:raise PipelineError("Consent not found in this project",404)
                conn.execute("""INSERT INTO tb_spending_revocations(consent_id,project_id,revoked_by)
                  VALUES (%s,%s,%s) ON CONFLICT(consent_id) DO NOTHING""",
                  (consent_id,pid,actor))
                return {"revoked":True,"launch_available":False}
    except PipelineError:raise
    except Exception as exc:
        raise PipelineError("Could not revoke budget consent",503) from exc
