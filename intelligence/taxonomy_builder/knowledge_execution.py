"""Stage14 separate, consent-gated C taxonomy generation from verified B9.

Never rerun MiMo, reuse B spending consent, silently retry a charged call,
or mutate existing legacy RTB invoice/run records. Only explicit advance()
can dispatch one provider request; a crashed/uncertain request stays blocked.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from psycopg.types.json import Jsonb
from intelligence.project_sources.context_builder import sha as canonical_sha
from intelligence.knowledge_extraction.human_review import _reviewer, _auth
from intelligence.knowledge_extraction.intake import IntakeError, _project
from intelligence.knowledge_extraction.knowledge_snapshot import KnowledgeSnapshotRepository
from .knowledge_binding import to_taxonomy_generation_payload
from .catalog import latest_verified
from .cost_estimator import amount, check_budget, usd
from .provider import request_payload,reservation_cost,invoke,ProviderUncertain
from .run_contracts import reviewable_topic_batch, review_topics
from .pipeline_contracts import PipelineError, sha256

SCHEMA="knowledge-c-generation-quote/v1"
ENGINE="c-knowledge-run/1.0.0"
STAGE="taxonomy_generation"


def _fail(reason):
    raise IntakeError(reason)


def _uuid(value):
    try:
        if not isinstance(value,str):
            raise ValueError()
        return str(UUID(value))
    except (TypeError,ValueError,AttributeError) as exc:
        raise IntakeError("c_id_invalid") from exc


def _current_catalog(conn, model_id):
    try:
        return latest_verified(conn,{model_id})[model_id]
    except (PipelineError,KeyError):
        _fail("c_catalog_stale_or_unavailable")


def _validate_model(entry,max_output_tokens):
    cap=entry["capabilities"]
    if (type(max_output_tokens) is not int or not 512<=max_output_tokens<=16000
            or (cap.get("max_output_tokens") is not None
                and max_output_tokens>int(cap["max_output_tokens"]))
            or not cap.get("text_output") or
            not cap.get("supports_structured_outputs")):
        _fail("c_model_configuration_invalid")
    stage={
        "role":STAGE,"enabled":True,"model_id":entry["model_id"],
        "catalog_version_id":entry["catalog_version_id"],
        "reasoning":{"mode":"default"},"temperature":None,
        "max_output_tokens":max_output_tokens,"prompt_version":"taxonomy-v2",
    }
    model={
        "catalog_version_id":entry["catalog_version_id"],
        "catalog_sha256":entry["catalog_sha256"],
        "model_id":entry["model_id"],
        "capabilities":cap,"pricing":entry["pricing"],
    }
    return stage,model


def _request(stage,model,inputs):
    try:
        payload=request_payload(stage,model,inputs)
        reserve=reservation_cost(payload,model)
        return payload,reserve
    except PipelineError as exc:
        _fail("c_request_invalid_"+type(exc).__name__)


def _read_quote(conn,pid,quote_id,lock=False):
    row=conn.execute(
        """SELECT id,snapshot_id,handoff_id,snapshot_sha256,quote_sha256,
                  request_sha256,model_id,catalog_version_id,catalog_sha256,
                  stage_config,model_snapshot,quoted_usd,quote,expires_at
           FROM knowledge_c_generation_quotes WHERE project_id=%s AND id=%s"""
        +(" FOR UPDATE" if lock else ""),
        (pid,_uuid(quote_id)),
    ).fetchone()
    if not row:
        _fail("c_quote_not_found")
    (qid,snapshot_id,handoff_id,snap_sha,sha,request_sha,model_id,cat_id,
     cat_sha,stage,model,reservation,quote,expires_at)=row
    if (quote.get("schema_version")!=SCHEMA
            or quote.get("request_sha256")!=request_sha
            or quote.get("snapshot_id")!=str(snapshot_id)
            or quote.get("handoff_id")!=str(handoff_id)
            or quote.get("model_id")!=model_id
            or quote.get("catalog_sha256")!=cat_sha
            or quote.get("quoted_usd")!=str(reservation)
            or canonical_sha(quote)!=sha
            or model.get("catalog_sha256")!=cat_sha
            or model.get("catalog_version_id")!=cat_id
            or stage.get("model_id")!=model_id):
        _fail("c_quote_integrity")
    return {"id":str(qid),"snapshot_id":str(snapshot_id),
            "handoff_id":str(handoff_id),"snapshot_sha256":snap_sha,
            "quote_sha256":sha,"request_sha256":request_sha,
            "model_id":model_id,"catalog_version_id":cat_id,
            "catalog_sha256":cat_sha,"stage":stage,"model":model,
            "quoted_usd":reservation,"quote":quote,"expires_at":expires_at}


def _run(conn,pid,run_id,lock=False):
    row=conn.execute(
        """SELECT id,quote_id,consent_id,snapshot_id,handoff_id,
                  status,authorized_usd,reserved_usd,spent_usd,idempotency_key
          FROM knowledge_c_generation_runs WHERE project_id=%s AND id=%s"""
        +(" FOR UPDATE" if lock else ""),
        (pid,_uuid(run_id)),
    ).fetchone()
    if not row:
        _fail("c_run_not_found")
    return {"id":str(row[0]),"quote_id":str(row[1]),"consent_id":str(row[2]),
            "snapshot_id":str(row[3]),"handoff_id":str(row[4]),
            "status":row[5],"authorized_usd":row[6],
            "reserved_usd":row[7],"spent_usd":row[8],"idempotency_key":row[9]}


def _priced_quote(snapshot,hand,preview,stage,model):
    payload,reserve=_request(stage,model,preview["generation_inputs"])
    quote={
        "schema_version":SCHEMA,"engine":ENGINE,
        "input_mode":"knowledge_snapshot_v1",
        "snapshot_id":snapshot["snapshot_id"],
        "snapshot_sha256":snapshot["snapshot_sha256"],
        "handoff_id":hand["handoff_id"],
        "handoff_sha256":hand["handoff_sha256"],
        "generation_inputs_sha256":canonical_sha(preview["generation_inputs"]),
        "request_sha256":sha256(payload),
        "model_id":stage["model_id"],
        "catalog_version_id":stage["catalog_version_id"],
        "catalog_sha256":model["catalog_sha256"],
        "stage_config_sha256":canonical_sha(stage),
        "model_snapshot_sha256":canonical_sha(model),
        "quoted_usd":str(reserve),
        "independent_c_consent_required":True,
        "retry_enabled":False,"provider_calls":0,
    }
    return quote,sha256(payload),reserve


class KnowledgeCExecutionRepository:
    def __init__(self,connection_factory=None,*,verify_local_files=True):
        self.snapshots=KnowledgeSnapshotRepository(
            connection_factory,verify_local_files=verify_local_files)
        self.connection_factory=self.snapshots.connection_factory

    def _source(self,pid,sid,actor):
        snap=self.snapshots.materialize(pid,sid,actor)
        hand=self.snapshots.materialize_c_handoff(pid,sid,actor)
        if hand["handoff"]["snapshot_sha256"]!=snap["snapshot_sha256"]:
            _fail("c_handoff_snapshot_changed")
        preflight=to_taxonomy_generation_payload(hand["handoff"],snap["snapshot"])
        if preflight["execution_authorized"] is not False:
            _fail("c_unexpected_execution_authorization")
        return snap,hand,preflight

    def _verified(self,pid,sid,actor,quote, *, fresh_model=False,conn=None):
        snap,hand,preflight=self._source(pid,sid,actor)
        if (quote["snapshot_id"]!=snap["snapshot_id"]
                or quote["handoff_id"]!=hand["handoff_id"]
                or quote["snapshot_sha256"]!=snap["snapshot_sha256"]
                or quote["quote"]["handoff_sha256"]!=hand["handoff_sha256"]):
            _fail("c_quote_source_identity")
        comparison,req_sha,reserve=_priced_quote(
            snap,hand,preflight,quote["stage"],quote["model"])
        if (comparison!=quote["quote"] or req_sha!=quote["request_sha256"]
                or reserve!=quote["quoted_usd"]):
            _fail("c_quote_replay_changed")
        if fresh_model:
            if conn is None:
                _fail("c_internal_connection_missing")
            current=_current_catalog(conn,quote["model_id"])
            if (current["catalog_version_id"]!=quote["catalog_version_id"]
                    or current["catalog_sha256"]!=quote["catalog_sha256"]):
                _fail("c_model_catalog_changed")
        return preflight

    def quote(self,project_id,session_id,reviewer_id,model_id,*,max_output_tokens=4096):
        """Mint a new *unspent* snapshot-pinned quote. Does not authorize C."""
        pid=_project(project_id);sid=_uuid(session_id);actor=_reviewer(reviewer_id)
        if not isinstance(model_id,str) or len(model_id)>140:
            _fail("c_model_id_invalid")
        snap,hand,preflight=self._source(pid,sid,actor)
        with self.connection_factory() as conn:
            _auth(conn,pid,actor)
            model_ref=_current_catalog(conn,model_id)
            stage,model=_validate_model(model_ref,max_output_tokens)
            body,_,reserve=_priced_quote(snap,hand,preflight,stage,model)
            quote_sha=canonical_sha(body)
            row=conn.execute(
                """INSERT INTO knowledge_c_generation_quotes(
                   project_id,snapshot_id,handoff_id,snapshot_sha256,quote_sha256,
                   request_sha256,model_id,catalog_version_id,catalog_sha256,
                   stage_config,model_snapshot,quoted_usd,quote)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (pid,snap["snapshot_id"],hand["handoff_id"],snap["snapshot_sha256"],
                 quote_sha,body["request_sha256"],model_id,model["catalog_version_id"],
                 model["catalog_sha256"],Jsonb(stage),Jsonb(model),reserve,Jsonb(body)),
            ).fetchone()
        return {"quote_id":str(row[0]),"quote_sha256":quote_sha,
                "required_usd":usd(reserve),"model_id":model_id,
                "consent_required":True,"provider_calls":0}

    def authorize(self,project_id,session_id,reviewer_id,quote_id,authorized_usd):
        """Explicit B9/C-only budget consent; cannot authorize B3 or RTB legacy."""
        pid=_project(project_id);sid=_uuid(session_id);actor=_reviewer(reviewer_id)
        with self.connection_factory() as conn:
            _auth(conn,pid,actor)
            q=_read_quote(conn,pid,quote_id,lock=True)
            self._verified(pid,sid,actor,q,fresh_model=True,conn=conn)
            if datetime.now(timezone.utc)>=q["expires_at"]:
                _fail("c_quote_expired")
            try: budget=check_budget(authorized_usd,q["quoted_usd"])
            except PipelineError:
                _fail("c_budget_invalid")
            earlier=conn.execute(
                """SELECT id,authorized_usd FROM knowledge_c_generation_consents
                  WHERE project_id=%s AND quote_id=%s""",
                (pid,q["id"]),
            ).fetchone()
            if earlier:
                if earlier[1]!=budget:
                    _fail("c_consent_already_fixed")
                return {"consent_id":str(earlier[0]),"created":False,
                        "authorized_usd":str(budget),"provider_calls":0}
            row=conn.execute(
                """INSERT INTO knowledge_c_generation_consents(
                   project_id,quote_id,authorized_usd,approved_by)
                   VALUES(%s,%s,%s,%s) RETURNING id""",
                (pid,q["id"],budget,actor),
            ).fetchone()
        return {"consent_id":str(row[0]),"created":True,
                "authorized_usd":str(budget),"provider_calls":0}

    def revoke(self,project_id,consent_id,reviewer_id):
        pid=_project(project_id);actor=_reviewer(reviewer_id)
        with self.connection_factory() as conn:
            _auth(conn,pid,actor)
            ok=conn.execute(
                """SELECT 1 FROM knowledge_c_generation_consents
                   WHERE project_id=%s AND id=%s FOR UPDATE""",
                (pid,_uuid(consent_id)),
            ).fetchone()
            if not ok:
                _fail("c_consent_not_found")
            conn.execute(
                """INSERT INTO knowledge_c_generation_revocations(
                   project_id,consent_id,revoked_by) VALUES(%s,%s,%s)
                   ON CONFLICT DO NOTHING""",
                (pid,consent_id,actor),
            )
        return {"revoked":True,"provider_calls":0}

    def start(self,project_id,session_id,reviewer_id,quote_id,consent_id,idempotency_key):
        """One C run per C consent, not a reusable wallet, never auto dispatch."""
        pid=_project(project_id);sid=_uuid(session_id);actor=_reviewer(reviewer_id)
        if (not isinstance(idempotency_key,str)
                or not 12<=len(idempotency_key)<=128):
            _fail("c_idempotency_key_invalid")
        with self.connection_factory() as conn:
            _auth(conn,pid,actor)
            q=_read_quote(conn,pid,quote_id,lock=True)
            self._verified(pid,sid,actor,q,fresh_model=True,conn=conn)
            row=conn.execute(
                """SELECT id,quote_id,consent_id
                   FROM knowledge_c_generation_runs
                   WHERE project_id=%s AND idempotency_key=%s FOR UPDATE""",
                (pid,idempotency_key),
            ).fetchone()
            if row:
                if str(row[1])!=q["id"] or str(row[2])!=_uuid(consent_id):
                    _fail("c_idempotency_conflict")
                return {"run_id":str(row[0]),"created":False,
                        "status":"existing","provider_calls":0}
            if datetime.now(timezone.utc)>=q["expires_at"]:
                _fail("c_quote_expired")
            c=conn.execute(
                """SELECT id,authorized_usd FROM knowledge_c_generation_consents
                   WHERE project_id=%s AND quote_id=%s AND id=%s FOR UPDATE""",
                (pid,q["id"],_uuid(consent_id)),
            ).fetchone()
            if c is None:
                _fail("c_wrong_or_missing_consent")
            if conn.execute(
                """SELECT 1 FROM knowledge_c_generation_revocations
                   WHERE project_id=%s AND consent_id=%s""",
                (pid,c[0]),
            ).fetchone():
                _fail("c_consent_revoked")
            if conn.execute(
                """SELECT 1 FROM knowledge_c_generation_runs
                   WHERE project_id=%s AND consent_id=%s""",
                (pid,c[0]),
            ).fetchone():
                _fail("c_consent_already_spent")
            run=conn.execute(
                """INSERT INTO knowledge_c_generation_runs(
                   project_id,quote_id,consent_id,snapshot_id,handoff_id,
                   idempotency_key,authorized_usd)
                   VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                (pid,q["id"],c[0],q["snapshot_id"],q["handoff_id"],
                 idempotency_key,c[1]),
            ).fetchone()
        return {"run_id":str(run[0]),"created":True,
                "status":"generation_pending","provider_calls":0}

    def inspect(self,project_id,run_id,reviewer_id):
        pid=_project(project_id);actor=_reviewer(reviewer_id)
        with self.connection_factory() as conn:
            _auth(conn,pid,actor)
            run=_run(conn,pid,run_id)
            attempt=conn.execute(
                """SELECT status,charged_usd,reservation_usd,output,usage,error_code
                  FROM knowledge_c_generation_attempts
                  WHERE project_id=%s AND run_id=%s""",
                (pid,run["id"]),
            ).fetchone()
            return {
                "run_id":run["id"],"status":run["status"],
                "spent_usd":str(run["spent_usd"]),
                "reserved_usd":str(run["reserved_usd"]),
                "authorized_usd":str(run["authorized_usd"]),
                "attempt":None if attempt is None else {
                    "status":attempt[0],
                    "charged_usd":str(attempt[1]) if attempt[1] is not None else None,
                    "reservation_usd":str(attempt[2]),
                    "output":attempt[3],
                    "usage":attempt[4],"error_code":attempt[5],
                },
                "provider_calls":0,
            }

    def advance(self,project_id,session_id,reviewer_id,run_id,*,sender=None,api_key=None):
        """Manual one-call dispatch AFTER durable reservation, with no retries.

        Only the caller's deliberate advance() can generate a bill. A missing
        credential does not create an attempt. Unknown usage retains budget.
        """
        pid=_project(project_id);sid=_uuid(session_id);actor=_reviewer(reviewer_id)
        if sender is None and api_key is None:
            from .provider import resolve_key
            api_key=resolve_key()
            if not api_key:
                _fail("c_no_provider_key")
        # Verify source before writing a reservation; also reverify after
        # acquiring the run lock against a pinned signed C snapshot.
        with self.connection_factory() as conn:
            _auth(conn,pid,actor)
            run=_run(conn,pid,run_id,lock=True)
            if run["status"]!="generation_pending":
                _fail("c_run_not_pending")
            q=_read_quote(conn,pid,run["quote_id"])
            preview=self._verified(pid,sid,actor,q,fresh_model=True,conn=conn)
            if run["snapshot_id"]!=q["snapshot_id"] or run["handoff_id"]!=q["handoff_id"]:
                _fail("c_run_wrong_source")
            if conn.execute(
                """SELECT 1 FROM knowledge_c_generation_revocations
                   WHERE project_id=%s AND consent_id=%s""",
                (pid,run["consent_id"]),
            ).fetchone():
                _fail("c_consent_revoked")
            if conn.execute(
                """SELECT 1 FROM knowledge_c_generation_attempts
                   WHERE project_id=%s AND run_id=%s""",
                (pid,run["id"]),
            ).fetchone():
                _fail("c_attempt_already_exists")
            payload,reserve=_request(q["stage"],q["model"],preview["generation_inputs"])
            if sha256(payload)!=q["request_sha256"] or reserve!=q["quoted_usd"]:
                _fail("c_paid_payload_changed")
            if reserve>run["authorized_usd"]-run["spent_usd"]-run["reserved_usd"]:
                conn.execute(
                    """UPDATE knowledge_c_generation_runs SET status='paused_budget'
                       WHERE project_id=%s AND id=%s""",(pid,run["id"]),
                )
                return {"status":"paused_budget","provider_calls":0}
            attempt=conn.execute(
                """INSERT INTO knowledge_c_generation_attempts(
                   project_id,run_id,status,request_sha256,reservation_usd)
                   VALUES(%s,%s,'reserved',%s,%s) RETURNING id""",
                (pid,run["id"],q["request_sha256"],reserve),
            ).fetchone()[0]
            conn.execute(
                """UPDATE knowledge_c_generation_runs
                    SET reserved_usd=reserved_usd+%s,status='generation_running',
                        updated_at=clock_timestamp() WHERE project_id=%s AND id=%s""",
                (reserve,pid,run["id"]),
            )
        # Network request is outside the committed reservation transaction.
        result=None
        known=False
        reason=None
        try:
            result=invoke(q["stage"],q["model"],preview["generation_inputs"],
                          sender=sender,key=api_key,timeout=300)
            known=True
        except (ProviderUncertain,PipelineError):
            reason="c_provider_charge_unknown"
        except Exception:
            reason="c_provider_outcome_uncertain"
        output=None
        if known:
            try:
                if result.get("model_id") not in (None,q["model_id"]):
                    raise PipelineError("Provider used another model")
                if result.get("finish_reason")!="stop":
                    raise PipelineError("Provider finished early")
                # Python topics have IDs, traceable approved B9 fact links and
                # mandatory second Human Review; never auto-publish.
                output=reviewable_topic_batch(
                    result["raw_json"],preview["generation_inputs"]["approved_facts"])
            except (PipelineError,ValueError,TypeError):
                reason="c_generation_output_invalid"
        with self.connection_factory() as conn:
            _auth(conn,pid,actor)
            record=_run(conn,pid,run_id,lock=True)
            if record["status"]!="generation_running":
                _fail("c_run_state_changed")
            if not known:
                conn.execute(
                    """UPDATE knowledge_c_generation_attempts
                        SET status='uncertain',error_code=%s,ended_at=clock_timestamp()
                        WHERE project_id=%s AND id=%s""",
                    (reason,pid,attempt),
                )
                conn.execute(
                    """UPDATE knowledge_c_generation_runs SET status='uncertain',
                        updated_at=clock_timestamp() WHERE project_id=%s AND id=%s""",
                    (pid,run["id"]),
                )
                return {"status":"uncertain","provider_calls":1,
                        "error_code":reason,"reservation_held":True}
            charge=result["charged_usd"]
            next_state=("paused_budget" if charge>reserve else
                        "failed" if output is None else "topic_review")
            conn.execute(
                """UPDATE knowledge_c_generation_attempts
                    SET status=%s,charged_usd=%s,usage=%s,output=%s,
                        response_id=%s,error_code=%s,ended_at=clock_timestamp()
                    WHERE project_id=%s AND id=%s""",
                ("completed" if next_state=="topic_review" else "failed",
                 charge,Jsonb(result["usage"]),Jsonb(output) if output else None,
                 result["response_id"],reason or (
                     "c_provider_over_reservation" if charge>reserve else None),
                 pid,attempt),
            )
            conn.execute(
                """UPDATE knowledge_c_generation_runs
                    SET status=%s,spent_usd=spent_usd+%s,
                        reserved_usd=reserved_usd-%s,
                        updated_at=clock_timestamp()
                    WHERE project_id=%s AND id=%s""",
                (next_state,charge,reserve,pid,run["id"]),
            )
        return {"status":next_state,"provider_calls":1,
                "charged_usd":str(charge),"error_code":reason}

    def submit_topics(self,project_id,session_id,reviewer_id,run_id,choices):
        """Separate explicit Human C topic review, immutable and never live publish."""
        pid=_project(project_id);sid=_uuid(session_id);actor=_reviewer(reviewer_id)
        with self.connection_factory() as conn:
            _auth(conn,pid,actor)
            run=_run(conn,pid,run_id,lock=True)
            if run["status"]!="topic_review":
                _fail("c_topics_not_reviewable")
            q=_read_quote(conn,pid,run["quote_id"])
            preview=self._verified(pid,sid,actor,q)
            attempt=conn.execute(
                """SELECT status,output FROM knowledge_c_generation_attempts
                    WHERE project_id=%s AND run_id=%s""",
                (pid,run["id"]),
            ).fetchone()
            if not attempt or attempt[0]!="completed" or not isinstance(attempt[1],dict):
                _fail("c_topics_not_grounded")
            result=review_topics(
                attempt[1]["topics"],choices,preview["generation_inputs"]["approved_facts"])
            doc={
                "schema_version":"knowledge-c-topic-review/v1",
                "input_mode":"knowledge_snapshot_v1","run_id":run["id"],
                "snapshot_id":q["snapshot_id"],"quote_id":run["quote_id"],
                "reviewer_id":actor,"approved_topics":result,
                "approved_count":len(result),"published_to_legacy_taxonomy":False,
                "requires_separate_taxonomy_publication":True,
            }
            digest=canonical_sha(doc)
            old=conn.execute(
                """SELECT id,review_sha256,review FROM knowledge_c_topic_reviews
                   WHERE project_id=%s AND run_id=%s""",
                (pid,run["id"]),
            ).fetchone()
            if old:
                if old[1]!=digest or old[2]!=doc:
                    _fail("c_topics_review_changed")
                return {"review_id":str(old[0]),"created":False,"provider_calls":0}
            saved=conn.execute(
                """INSERT INTO knowledge_c_topic_reviews(
                   project_id,run_id,reviewer_id,review_sha256,review)
                   VALUES(%s,%s,%s,%s,%s) RETURNING id""",
                (pid,run["id"],actor,digest,Jsonb(doc)),
            ).fetchone()[0]
            conn.execute(
                """UPDATE knowledge_c_generation_runs SET status='approved',
                    updated_at=clock_timestamp() WHERE project_id=%s AND id=%s""",
                (pid,run["id"]),
            )
        return {"review_id":str(saved),"created":True,"provider_calls":0,
                "approved_topics_count":len(result)}
