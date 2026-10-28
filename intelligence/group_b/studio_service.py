"""GB-MOD-26 project-scoped Group B operator services.

Local UI routes use ONE project-scoped database; no SQL005 edits, paid network,
QDP fabricated receipt or HTML-supplied reviewer claims. Privileged actions
require an injected trusted server adapter with live authenticated capabilities.
"""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from datetime import datetime, date
from decimal import Decimal, InvalidOperation
from math import isfinite
from uuid import UUID
import re

from .budget import BudgetQuote
from .ledger import save_quote, approve_quote
from .orchestrator import enqueue_jobs, cancel_run
from .readiness import ReadinessReport
from .review import make_decision, append_review, read_effective_labels
from .errors import GroupBError

PID=re.compile(r"[0-9a-f]{32}\Z")
UID=re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\Z")
SHA=re.compile(r"[a-f0-9]{64}\Z")
BLOCKS=tuple("B"+str(i) for i in range(1,9))


class StudioProblem(Exception):
    def __init__(self,code="operation_denied",http_status=403):
        self.code=code
        self.http_status=http_status
        super().__init__(code)


def _reject(code,status=400):
    raise StudioProblem(code,status)


def _identity(project_id,run_id=None):
    if type(project_id) is not str or not PID.fullmatch(project_id):
        _reject("project_id_invalid",404)
    if run_id is not None:
        if type(run_id) is not str or not UID.fullmatch(run_id):
            _reject("run_id_invalid")
        try:
            if str(UUID(run_id))!=run_id:
                _reject("run_id_invalid")
        except ValueError:
            _reject("run_id_invalid")


def _json(x,depth=0):
    if depth>10:
        _reject("response_structure_invalid",500)
    if isinstance(x,Decimal):
        if not x.is_finite():
            _reject("nonfinite_response",500)
        return str(x)
    if isinstance(x,datetime):
        if x.utcoffset() is None:
            _reject("naive_time",500)
        return x.isoformat()
    if isinstance(x,date):
        return x.isoformat()
    if isinstance(x,UUID):
        return str(x)
    if type(x) is dict:
        return {str(k):_json(v,depth+1) for k,v in x.items()}
    if type(x) in (tuple,list):
        return [_json(v,depth+1) for v in x]
    if is_dataclass(x) and not isinstance(x,type):
        return _json(asdict(x),depth+1)
    if type(x) is float:
        if not isfinite(x):
            _reject("nonfinite_response",500)
        return x
    if x is None or type(x) in (str,bool,int):
        return x
    _reject("response_unserializable",500)


def _dict(cur):
    row=cur.fetchone()
    if row is None:
        return None
    if isinstance(row,Mapping):
        return dict(row)
    return dict(zip((x.name for x in cur.description),row))


def _many(cur):
    keys=[x.name for x in cur.description]
    return [dict(row) if isinstance(row,Mapping)
            else dict(zip(keys,row)) for row in cur.fetchall()]


def _project(conn,project_id,operator,action):
    _identity(project_id)
    if not callable(operator) or operator(project_id,action,conn) is not True:
        _reject("project_operator_not_authorized",403)
    exists=conn.execute(
        "SELECT EXISTS(SELECT 1 FROM project_registry WHERE id=%s)",
        (project_id,)
    ).fetchone()
    if exists is None or exists[0] is not True:
        _reject("project_not_found",404)


def _one_run(conn,project_id,run_id,*,lock=False):
    _identity(project_id,run_id)
    query="""SELECT r.id::text AS run_id,r.project_id,r.video_id,r.source_id,
                    r.content_type,r.state,r.config_sha256,r.a_snapshot_sha256,
                    r.media_manifest_sha256,r.source_policy_version_id,
                    r.result_analysis_id,r.last_error_code,r.created_at,
                    r.updated_at,r.inbox_id::text AS inbox_id,
                    i.status AS inbox_status,
                    a.analysis_key,a.analysis_version,a.needs_review,
                    a.state AS analysis_state,
                    a.method_details
             FROM group_b_runs r
             JOIN group_b_inbox i ON i.id=r.inbox_id AND i.project_id=r.project_id
             LEFT JOIN content_b_analyses a ON a.id=r.result_analysis_id
             WHERE r.project_id=%s AND r.id=%s::uuid"""
    if lock:
        query+=" FOR UPDATE OF r"
    return _dict(conn.execute(query,(project_id,run_id)))


def _assert_body(body,keys):
    if type(body) is not dict or set(body)-set(keys):
        _reject("request_fields_invalid")


def _page(offset,limit):
    if type(offset) is not int or type(limit) is not int or not (
        0<=offset<=100_000 and 1<=limit<=50
    ):
        _reject("pagination_invalid")
    return offset,limit


def _safe_run(row):
    return _json({k:v for k,v in row.items() if k!="method_details"})


# Explicit projection intentionally omits original speech/OCR, CTA links,
# handles, source file bytes and paid provider payloads.
_RESULTS={
 "B1":"""SELECT classification_status,mechanisms,delivery,is_question,
                 has_number,direct_address,has_negation,is_command,start_sec,
                 end_sec,slide_index FROM content_b_hooks WHERE analysis_id=%s""",
 "B2":"""SELECT classification_status,video_primary,video_secondary,
                 carousel_format,taxonomy_version
          FROM content_b_formats WHERE analysis_id=%s""",
 "B3":"""SELECT 'frame' AS kind,frame_index AS idx,role,
                 before_after_phase,needs_review
          FROM content_b_story_frames WHERE analysis_id=%s
          UNION ALL
          SELECT 'interval',interval_index,role,before_after_phase,needs_review
          FROM content_b_story_intervals WHERE analysis_id=%s
          UNION ALL
          SELECT 'slide',slide_index,role,before_after_phase,needs_review
          FROM content_b_story_slides WHERE analysis_id=%s
          ORDER BY kind,idx""",
 "B4":"""SELECT ai_generated,person_visible,product_visible,logo_visible
          FROM content_b_visual_content WHERE analysis_id=%s""",
 "B5":"""SELECT editing_type,cut_count,cuts_per_minute,
                 avg_shot_duration,cuts_first_5_sec
          FROM content_b_editing WHERE analysis_id=%s""",
 "B6":"""SELECT subtitle_type,position,font_size,font_style,
                 text_color,highlight_color,background_style,display_mode
          FROM content_b_subtitles WHERE analysis_id=%s""",
 "B7":"""SELECT analysis_status,has_cta,detected_occurrences
          FROM content_b_cta_summary WHERE analysis_id=%s""",
 "B8":"""SELECT category_id,topic_id,subtopic_id,
                 taxonomy_version,needs_review
          FROM content_b_topic_assignments WHERE analysis_id=%s""",
}


class GroupBStudioService:
    def __init__(self,connect,operator=None,host=None):
        if not callable(connect):
            raise ValueError("DB connection factory required")
        self.connect=connect
        self.operator=operator
        self.host=host

    def _admit(self,project_id,action,conn):
        _project(conn,project_id,self.operator,action)

    def _host(self,name,project_id,run_id,conn,*args):
        callback=getattr(self.host,name,None)
        if not callable(callback):
            _reject("trusted_"+name+"_not_connected",424)
        try:
            return callback(project_id,run_id,conn,*args)
        except StudioProblem:
            raise
        except Exception:
            _reject("trusted_"+name+"_unavailable",424)

    def list_runs(self,project_id,*,offset=0,limit=20):
        offset,limit=_page(offset,limit)
        with self.connect() as conn:
            self._admit(project_id,"group_b.read",conn)
            rows=_many(conn.execute(
                """SELECT r.id::text AS run_id,r.video_id,r.content_type,
                          r.state,r.created_at,r.updated_at,r.last_error_code,
                          r.result_analysis_id,
                          COUNT(*) OVER() AS total
                   FROM group_b_runs r
                   WHERE r.project_id=%s
                   ORDER BY r.created_at DESC,r.id DESC
                   LIMIT %s OFFSET %s""",
                (project_id,limit,offset)
            ))
            return {"items":_json([{k:v for k,v in row.items() if k!="total"}
                                   for row in rows]),
                    "total":rows[0]["total"] if rows else 0,
                    "offset":offset,"limit":limit}

    def detail(self,project_id,run_id,*,offset=0,limit=20):
        offset,limit=_page(offset,limit)
        with self.connect() as conn:
            self._admit(project_id,"group_b.read",conn)
            row=_one_run(conn,project_id,run_id)
            if row is None:
                _reject("run_not_found",404)
            blocks=_many(conn.execute(
                """SELECT block,status,evidence_sha256,error_code,updated_at
                   FROM group_b_block_states
                   WHERE project_id=%s AND run_id=%s::uuid ORDER BY block""",
                (project_id,run_id)
            ))
            jobs=_many(conn.execute(
                """SELECT block,kind,state,attempts,max_attempts,
                          result_sha256,last_error_code,updated_at
                   FROM group_b_jobs
                   WHERE project_id=%s AND run_id=%s::uuid ORDER BY block
                   LIMIT %s OFFSET %s""",
                (project_id,run_id,limit,offset)
            ))
            quotes=_many(conn.execute(
                """SELECT id::text AS quote_id,model_id,quoted_usd,
                          created_at,expires_at
                   FROM group_b_model_quotes
                   WHERE project_id=%s AND run_id=%s::uuid
                   ORDER BY created_at DESC LIMIT 20""",
                (project_id,run_id)
            ))
            review=conn.execute(
                """SELECT COALESCE(max(revision),0),
                          count(*) FILTER (WHERE decision->>'kind'='correction')
                   FROM group_b_review_events
                   WHERE project_id=%s AND run_id=%s::uuid""",
                (project_id,run_id)
            ).fetchone()
            invalidated=conn.execute(
                """SELECT EXISTS(SELECT 1 FROM group_b_outbox
                   WHERE project_id=%s AND run_id=%s::uuid
                     AND event_type='group_b.invalidated.v1')""",
                (project_id,run_id)
            ).fetchone()[0]
            return _json({
                "run":_safe_run(row),"blocks":blocks,"jobs":jobs,
                "quotes":quotes,"review_revision":review[0],
                "correction_count":review[1],
                "invalidated":invalidated,
                "current_qualification":"unverified",
                "has_more_jobs":len(jobs)==limit,
                "offset":offset,"limit":limit,
            })

    def results(self,project_id,run_id,*,block,offset=0,limit=20):
        offset,limit=_page(offset,limit)
        if block not in BLOCKS:
            _reject("block_invalid")
        with self.connect() as conn,conn.transaction():
            self._admit(project_id,"group_b.results",conn)
            run=_one_run(conn,project_id,run_id,lock=True)
            if run is None or run["result_analysis_id"] is None:
                _reject("original_analysis_missing",404)
            granted=self._host("verify_result_read",project_id,run_id,
                               conn,block,run["result_analysis_id"])
            if granted is not True:
                _reject("result_read_denied")
            sql=_RESULTS[block]
            args=(run["result_analysis_id"],)*(
                3 if block=="B3" else 1
            )
            # B3 can have multiple rows; all other SQL005 block projections
            # are 0..1 row. Deterministic offset/limit prevents oversized B3.
            cursor=conn.execute(sql+" LIMIT %s OFFSET %s",
                                args+(limit+1,offset))
            rows=_many(cursor)
            return {"block":block,"items":_json(rows[:limit]),
                    "has_more":len(rows)>limit,
                    "offset":offset,"limit":limit,
                    "analysis_id":run["result_analysis_id"]}

    def evidence(self,project_id,run_id,*,block,offset=0,limit=20):
        offset,limit=_page(offset,limit)
        if block not in BLOCKS:
            _reject("block_invalid")
        with self.connect() as conn,conn.transaction():
            self._admit(project_id,"group_b.evidence",conn)
            run=_one_run(conn,project_id,run_id,lock=True)
            if run is None or run["result_analysis_id"] is None:
                _reject("no_persisted_evidence",404)
            granted=self._host("verify_evidence_read",project_id,run_id,
                               conn,block,run["result_analysis_id"])
            if granted is not True:
                _reject("evidence_read_denied",403)
            # Host MUST read true source-attested refs, not prior review
            # comments; a bare SHA or claimed source file isn't sufficient.
            entries=self._host("list_verified_source_refs",project_id,run_id,
                               conn,block,run["result_analysis_id"],
                               offset,limit)
            if (type(entries) is not dict or
                set(entries)!={"items","has_more"} or
                type(entries["items"]) is not list or
                len(entries["items"])>limit or
                type(entries["has_more"]) is not bool):
                _reject("source_refs_not_attested",424)
            allowed={"ref_sha256","kind","time_sec","slide_index","source_status"}
            for entry in entries["items"]:
                if (type(entry) is not dict or set(entry)-allowed or
                    type(entry.get("ref_sha256")) is not str or
                    not SHA.fullmatch(entry["ref_sha256"]) or
                    entry.get("source_status")!="verified"):
                    _reject("source_ref_schema_invalid",424)
            return {"block":block,"items":_json(entries["items"]),
                    "offset":offset,"limit":limit,
                    "has_more":entries["has_more"]}

    def metrics(self,project_id,run_id):
        """Only separately authenticated monitoring operators may view
        project-scoped aggregate costs / queue durations after revocation
        checks. No prompt, provider body, raw evidence or private API key.
        """
        _identity(project_id,run_id)
        with self.connect() as conn,conn.transaction():
            self._admit(project_id,"group_b.metrics",conn)
            run=_one_run(conn,project_id,run_id,lock=True)
            if run is None:
                _reject("run_not_found",404)
            verified=self._host("verify_metrics_read",project_id,run_id,
                                conn,run)
            if verified is not True:
                _reject("monitoring_rights_unverified",403)
            from .monitoring import performance_snapshot
            from .recovery import read_recovery
            from .monitoring import ThroughputPolicy
            return _json({
                "performance":performance_snapshot(
                    conn,project_id=project_id,run_id=run_id
                ),
                "recovery":read_recovery(
                    conn,project_id=project_id,run_id=run_id,
                    policy=ThroughputPolicy()
                ),
                "qualified_for_downstream":False,
            })

    def create(self,project_id,body):
        _assert_body(body,("inbox_id","idempotency_key"))
        ident,request=body.get("inbox_id"),body.get("idempotency_key")
        _identity(project_id,ident)
        if type(request) is not str or not 12<=len(request)<=160:
            _reject("create_idempotency_key_invalid")
        # NO HTTP JSON can provide an accepted QDP receipt. The host must
        # authenticate an existing QDP event and persist draft with the
        # verified intake module. It returns the DB run ID.
        with self.connect() as conn:
            self._admit(project_id,"group_b.create",conn)
            run_id=self._host("create_verified_run",project_id,None,
                              conn,ident,request)
            _identity(project_id,run_id)
            created=_one_run(conn,project_id,run_id)
            if created is None or created["inbox_id"]!=ident:
                _reject("created_run_not_bound_to_qdp",424)
            return {"run":_safe_run(created)}

    def quote(self,project_id,run_id,body):
        _assert_body(body,("model_id","blocks","idempotency_key"))
        if (type(body.get("model_id")) is not str or
            type(body.get("blocks")) is not list or
            not body["blocks"] or set(body["blocks"])-set(BLOCKS) or
            type(body.get("idempotency_key")) is not str or
            not 12<=len(body["idempotency_key"])<=160):
            _reject("quote_parameters_invalid")
        with self.connect() as conn,conn.transaction():
            self._admit(project_id,"group_b.quote",conn)
            run=_one_run(conn,project_id,run_id,lock=True)
            if run is None or run["state"] in ("completed","failed","cancelled"):
                _reject("run_not_quotable",409)
            prepared=self._host("quote_verified_context",project_id,run_id,
                                conn,body,run)
            if not isinstance(prepared,BudgetQuote) or not prepared.verify():
                _reject("quote_not_verified",424)
            if prepared.project_id!=project_id or prepared.run_id!=run_id:
                _reject("quote_project_mismatch",403)
            q=save_quote(conn,prepared)
            return {"quote":_json(q)}

    def approve(self,project_id,run_id,body):
        _assert_body(body,("quote_id","authorized_usd","confirmation"))
        _identity(project_id,body.get("quote_id"))
        if (type(body.get("authorized_usd")) is not str or
            not body["authorized_usd"] or
            body.get("confirmation") is not True):
            _reject("explicit_budget_confirmation_required",400)
        with self.connect() as conn,conn.transaction():
            self._admit(project_id,"group_b.approve",conn)
            user=self._host("verified_approving_operator",project_id,run_id,
                            conn,body)
            if type(user) is not str or not user:
                _reject("approval_operator_not_authenticated",403)
            try:
                amount=Decimal(body["authorized_usd"])
            except InvalidOperation:
                _reject("usd_amount_invalid",400)
            if not amount.is_finite() or amount<=0:
                _reject("usd_amount_invalid",400)
            result=approve_quote(
                conn,project_id=project_id,run_id=run_id,
                quote_id=body["quote_id"],approved_by=user,
                authorized_usd=amount,explicit_confirmation=True,
            )
            return {"approval":_json(result),
                    "paid_dispatched":False}

    def start(self,project_id,run_id,body):
        _assert_body(body,("blocks","request_key"))
        blocks=body.get("blocks")
        if (type(blocks) is not list or not blocks or
            any(type(x) is not str or x not in BLOCKS for x in blocks) or
            len(set(blocks))!=len(blocks) or
            type(body.get("request_key")) is not str or
            not 12<=len(body["request_key"])<=160):
            _reject("start_block_selection_invalid")
        with self.connect() as conn,conn.transaction():
            self._admit(project_id,"group_b.start",conn)
            run=_one_run(conn,project_id,run_id,lock=True)
            if run is None or run["state"] in ("completed","cancelled","failed"):
                _reject("run_not_startable",409)
            # Host verifies live GA/media/permissions AND returns a
            # typed ReadinessReport and approved paid attempt IDs. Never
            # reserve/submit an attempt on the HTTP path.
            proof=self._host("verified_job_readiness",project_id,run_id,
                             conn,body,run)
            if (type(proof) is not tuple or len(proof)!=2 or
                not isinstance(proof[0],ReadinessReport) or
                type(proof[1]) is not dict):
                _reject("fresh_job_readiness_not_available",424)
            queued=enqueue_jobs(conn,project_id=project_id,run_id=run_id,
                                readiness=proof[0],paid_attempts=proof[1],
                                selected_blocks=tuple(blocks))
            return {"jobs":_json(queued),"paid_dispatched":False}

    def cancel(self,project_id,run_id,body):
        _assert_body(body,("confirm",))
        if body.get("confirm") is not True:
            _reject("explicit_cancel_confirmation_required")
        with self.connect() as conn,conn.transaction():
            self._admit(project_id,"group_b.cancel",conn)
            if self._host("verify_cancel_operator",project_id,run_id,conn) is not True:
                _reject("cancel_operator_not_authenticated")
            count=cancel_run(conn,project_id=project_id,run_id=run_id,
                             reason="operator_cancelled")
            return {"cancelled_jobs":count,"paid_reservations_refunded":False}

    def review(self,project_id,run_id,body):
        _assert_body(body,("kind","block","target","index","field","value",
                           "evidence_refs","reason","expected_revision",
                           "previous_sha256","gold_version","sample_key"))
        with self.connect() as conn,conn.transaction():
            self._admit(project_id,"group_b.review",conn)
            run=_one_run(conn,project_id,run_id,lock=True)
            if run is None or run["result_analysis_id"] is None:
                _reject("no_completed_or_partial_analysis",409)
            reviewer=self._host("verified_reviewer_identity",project_id,
                                run_id,conn,body)
            if type(reviewer) is not str or not reviewer:
                _reject("reviewer_identity_not_verified",403)
            if (type(run["method_details"]) is not dict or
                type(run["analysis_key"]) is not str):
                _reject("original_analysis_manifest_unavailable",424)
            decision=make_decision(
                kind=body.get("kind"),
                analysis_id=run["result_analysis_id"],
                analysis_key=run["analysis_key"],
                manifest_sha256=run["method_details"].get(
                    "group_b_media_evidence_manifest_sha256"),
                previous_revision=body.get("expected_revision"),
                previous_sha256=body.get("previous_sha256"),
                block=body.get("block"),target=body.get("target"),
                index=body.get("index"),field=body.get("field"),
                value=body.get("value"),
                evidence_refs=body.get("evidence_refs"),
                reason=body.get("reason"),
                gold_version=body.get("gold_version"),
                sample_key=body.get("sample_key"),
            )
            result=append_review(
                conn,project_id=project_id,run_id=run_id,
                reviewer_id=reviewer,
                expected_revision=body["expected_revision"],
                decision=decision,admission_verifier=self.host,
            )
            return {"revision":result.revision,
                    "decision_sha256":result.decision_sha256,
                    "replayed":result.replayed}
