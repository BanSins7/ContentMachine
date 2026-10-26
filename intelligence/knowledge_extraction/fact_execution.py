"""B Stage07: explicit, one-run MiMo execution with durable cost accounting.

NO implicit provider calls. A caller must create a cost quote, explicitly
authorize it, start a uniquely keyed run, then call advance() for each pack.
Every attempt is durably reserved BEFORE the network call; uncertain attempts
are terminal and never automatically retried. The draft is NOT approved truth.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
import json
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from psycopg.types.json import Jsonb

from intelligence.taxonomy_builder.catalog import latest_verified
from intelligence.taxonomy_builder.cost_estimator import amount
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.provider import URL, resolve_key
from .fact_contracts import MODEL_ID, MAX_RESPONSE_BYTES, validate_candidate_response
from .fact_request_repository import FactRequestRepository
from .full_pack_repository import _load
from .intake import IntakeError, _project, _uuid

CENT = Decimal("0.000000000001")
MAX_AUTHORIZATION = Decimal("10000")


def _usd(value, name):
    try:
        result = amount(value, name)
        return result.quantize(CENT, rounding=ROUND_CEILING)
    except (PipelineError, ValueError, TypeError, ArithmeticError) as exc:
        raise IntakeError("facts_invalid_usd") from exc


def _fresh(repo, project_id, batch_id):
    """Replay original request and check current immutable A+model lineage."""
    info = repo.materialize(project_id, batch_id)
    batch = info["blueprint"]
    pins = {
        "full_pack_id": batch["source_pack_id"],
    } if batch["pack_kind"] == "full" else {
        "multi_plan_id": batch["source_pack_id"],
    }
    current = repo.prepare(project_id, **pins)
    if current["batch_id"] != str(batch_id) or current["batch_sha256"] != info["batch_sha256"]:
        raise IntakeError("facts_execution_stale_batch")
    return info


def _guard(conn, pid, info, resolver):
    if conn.execute("SELECT id FROM project_registry WHERE id=%s FOR UPDATE", (pid,)).fetchone() is None:
        raise IntakeError("project_not_found")
    bundle_id = info["blueprint"]["bundle_id"]
    _load(conn, pid, bundle_id, resolver, require_current=True)
    try:
        snap = latest_verified(conn, {MODEL_ID})[MODEL_ID]
    except (PipelineError, KeyError) as exc:
        raise IntakeError("facts_execution_catalog_stale") from exc
    if (snap["catalog_version_id"] != info["blueprint"]["catalog_version_id"]
            or snap["catalog_sha256"] != info["blueprint"]["catalog_sha256"]):
        raise IntakeError("facts_execution_catalog_stale")


def _provider_response(request_body, *, sender=None, api_key=None, timeout=180):
    """One provider request, no retries. Never expose provider error body."""
    if sender is None:
        if not api_key:
            raise IntakeError("facts_openrouter_key_missing")
        def sender(body):
            data = json.dumps(body, ensure_ascii=False).encode("utf-8")
            req = Request(URL, method="POST", data=data, headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json", "Accept": "application/json",
                "HTTP-Referer": "https://github.com/BanSins7/ContentMachine",
                "X-Title": "ContentMachine Knowledge Extraction",
            })
            try:
                with urlopen(req, timeout=timeout) as response:
                    raw = response.read(MAX_RESPONSE_BYTES + 1)
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise ValueError("oversized response")
                return json.loads(raw)
            except (HTTPError, URLError, OSError, TimeoutError, ValueError) as exc:
                raise RuntimeError("facts_provider_outcome_uncertain") from exc
    try:
        result = sender(request_body)
    except Exception as exc:
        raise IntakeError("facts_provider_outcome_uncertain") from exc
    if not isinstance(result, dict):
        raise IntakeError("facts_provider_outcome_uncertain")
    usage = result.get("usage")
    if not isinstance(usage, dict) or usage.get("cost") is None:
        raise IntakeError("facts_provider_usage_uncertain")
    try:
        charge = _usd(usage["cost"], "provider charge")
        input_tokens = usage["prompt_tokens"]
        output_tokens = usage["completion_tokens"]
        if (type(input_tokens) is not int or type(output_tokens) is not int
                or input_tokens < 0 or output_tokens < 0):
            raise ValueError("bad usage")
    except (KeyError, ValueError, IntakeError) as exc:
        raise IntakeError("facts_provider_usage_uncertain") from exc
    if charge < 0:
        raise IntakeError("facts_provider_usage_uncertain")
    safe_usage = {
        "cost_usd": str(charge),
        "prompt_tokens": input_tokens,
        "completion_tokens": output_tokens,
    }
    choices = result.get("choices")
    choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
    message = choice.get("message") if isinstance(choice.get("message"), dict) else {}
    raw_content = message.get("content")
    if isinstance(raw_content, list):
        raw_content = "".join(part.get("text", "") for part in raw_content
                              if isinstance(part, dict) and isinstance(part.get("text"), str))
    response_id = result.get("id")
    return {
        "charged": charge, "usage": safe_usage,
        "response_id": response_id[:180] if isinstance(response_id, str) else None,
        "model": result.get("model"),
        "finish_reason": choice.get("finish_reason"),
        "content": raw_content,
    }


class FactExecutionRepository:
    def __init__(self, connection_factory=None, *, verify_local_files=True):
        self.requests = FactRequestRepository(connection_factory,
                                             verify_local_files=verify_local_files)
        self.connection_factory = self.requests.connection_factory

    def quote(self, project_id, batch_id):
        """Explicit preflight. Quoting authorizes nothing and sends nothing."""
        pid = _project(project_id)
        bid = _uuid(batch_id, "facts_batch_id_invalid")
        info = _fresh(self.requests, pid, bid)
        batch = info["blueprint"]
        price = _usd(batch["total_indicative_reservation_usd"], "batch high reservation")
        if price <= 0 or price > MAX_AUTHORIZATION:
            raise IntakeError("facts_budget_out_of_range")
        with self.connection_factory() as conn:
            _guard(conn, pid, info, self.requests.resolver)
            prior = conn.execute(
                """SELECT id,expires_at FROM knowledge_fact_cost_quotes
                   WHERE project_id=%s AND batch_id=%s AND batch_sha256=%s
                     AND catalog_sha256=%s AND expires_at>clock_timestamp()
                     AND NOT EXISTS (
                       SELECT 1 FROM knowledge_fact_spending_consents c
                        WHERE c.project_id=knowledge_fact_cost_quotes.project_id
                          AND c.quote_id=knowledge_fact_cost_quotes.id)
                   ORDER BY created_at DESC LIMIT 1""",
                (pid, bid, info["batch_sha256"], batch["catalog_sha256"]),
            ).fetchone()
            if prior:
                return {"quote_id": str(prior[0]), "expires_at": prior[1].isoformat(),
                        "quoted_usd": str(price), "created": False, "provider_calls": 0}
            row = conn.execute(
                """INSERT INTO knowledge_fact_cost_quotes(
                     project_id,batch_id,batch_sha256,catalog_sha256,quoted_usd,expires_at)
                   VALUES(%s,%s,%s,%s,%s,clock_timestamp()+interval '30 minutes')
                   RETURNING id,expires_at""",
                (pid, bid, info["batch_sha256"], batch["catalog_sha256"], price),
            ).fetchone()
            return {"quote_id": str(row[0]), "expires_at": row[1].isoformat(),
                    "quoted_usd": str(price), "created": True, "provider_calls": 0}

    def authorize(self, project_id, quote_id, authorized_usd, *, approved_by):
        """Explicit one-run cost consent, independent of legacy TB consents."""
        pid = _project(project_id)
        qid = _uuid(quote_id, "facts_quote_id_invalid")
        if not isinstance(approved_by, str) or not approved_by.strip() or len(approved_by) > 120:
            raise IntakeError("facts_approver_invalid")
        approved = _usd(authorized_usd, "authorized_usd")
        if approved <= 0 or approved > MAX_AUTHORIZATION:
            raise IntakeError("facts_budget_out_of_range")
        with self.connection_factory() as conn:
            row = conn.execute(
                """SELECT batch_id,quoted_usd,expires_at,batch_sha256,catalog_sha256
                   FROM knowledge_fact_cost_quotes WHERE project_id=%s AND id=%s""",
                (pid, qid),
            ).fetchone()
        if not row:
            raise IntakeError("facts_quote_not_found")
        batch_id, minimum, expiration, digest, catalog = row
        if approved < minimum:
            raise IntakeError("facts_budget_below_quote")
        if datetime.now(timezone.utc) >= expiration:
            raise IntakeError("facts_quote_expired")
        info = _fresh(self.requests, pid, str(batch_id))
        if info["batch_sha256"] != digest or info["blueprint"]["catalog_sha256"] != catalog:
            raise IntakeError("facts_quote_pin_changed")
        with self.connection_factory() as conn:
            _guard(conn, pid, info, self.requests.resolver)
            row = conn.execute(
                """SELECT id FROM knowledge_fact_spending_consents
                   WHERE project_id=%s AND quote_id=%s""",
                (pid, qid),
            ).fetchone()
            if row:
                raise IntakeError("facts_quote_already_authorized")
            if conn.execute(
                """SELECT 1 FROM knowledge_fact_cost_quotes
                   WHERE project_id=%s AND id=%s AND expires_at>clock_timestamp()""",
                (pid, qid),
            ).fetchone() is None:
                raise IntakeError("facts_quote_expired")
            ident = conn.execute(
                """INSERT INTO knowledge_fact_spending_consents(
                     project_id,batch_id,quote_id,authorized_usd,authorized_by)
                   VALUES(%s,%s,%s,%s,%s) RETURNING id""",
                (pid, batch_id, qid, approved, approved_by.strip()),
            ).fetchone()[0]
            return {"consent_id": str(ident), "authorized_usd": str(approved),
                    "provider_calls": 0}

    def revoke(self, project_id, consent_id, *, revoked_by):
        pid = _project(project_id)
        cid = _uuid(consent_id, "facts_consent_id_invalid")
        if not isinstance(revoked_by, str) or not revoked_by.strip():
            raise IntakeError("facts_revoker_invalid")
        with self.connection_factory() as conn:
            found = conn.execute(
                """SELECT id FROM knowledge_fact_spending_consents
                   WHERE project_id=%s AND id=%s""", (pid, cid),
            ).fetchone()
            if not found:
                raise IntakeError("facts_consent_not_found")
            conn.execute(
                """INSERT INTO knowledge_fact_consent_revocations(consent_id,project_id,revoked_by)
                   VALUES(%s,%s,%s) ON CONFLICT(consent_id) DO NOTHING""",
                (cid, pid, revoked_by.strip()[:120]),
            )
        return {"revoked": True, "provider_calls": 0}

    def start(self, project_id, quote_id, consent_id, idempotency_key):
        pid = _project(project_id)
        qid = _uuid(quote_id, "facts_quote_id_invalid")
        cid = _uuid(consent_id, "facts_consent_id_invalid")
        if not isinstance(idempotency_key, str) or not 12 <= len(idempotency_key) <= 128:
            raise IntakeError("facts_idempotency_key_invalid")
        with self.connection_factory() as conn:
            reference = conn.execute(
                """SELECT batch_id FROM knowledge_fact_cost_quotes
                   WHERE project_id=%s AND id=%s""", (pid, qid),
            ).fetchone()
        if not reference:
            raise IntakeError("facts_quote_not_found")
        bid = str(reference[0])
        info = _fresh(self.requests, pid, bid)
        with self.connection_factory() as conn:
            _guard(conn, pid, info, self.requests.resolver)
            stored = conn.execute(
                """SELECT id,quote_id,consent_id,batch_id,status
                   FROM knowledge_fact_execution_runs
                   WHERE project_id=%s AND idempotency_key=%s FOR UPDATE""",
                (pid, idempotency_key),
            ).fetchone()
            if stored:
                if (str(stored[1]), str(stored[2]), str(stored[3])) != (qid, cid, bid):
                    raise IntakeError("facts_idempotency_conflict")
                return {"run_id": str(stored[0]), "status": stored[4], "created": False,
                        "provider_calls": 0}
            accepted = conn.execute(
                """SELECT c.authorized_usd,q.quoted_usd,q.batch_sha256,q.catalog_sha256
                   FROM knowledge_fact_spending_consents c
                   JOIN knowledge_fact_cost_quotes q ON q.id=c.quote_id
                     AND q.project_id=c.project_id AND q.batch_id=c.batch_id
                   WHERE c.project_id=%s AND c.id=%s AND c.quote_id=%s
                     AND c.batch_id=%s AND q.expires_at>clock_timestamp()""",
                (pid, cid, qid, bid),
            ).fetchone()
            if not accepted:
                raise IntakeError("facts_active_consent_required")
            if accepted[0] < accepted[1]:
                raise IntakeError("facts_budget_below_quote")
            if (accepted[2] != info["batch_sha256"]
                    or accepted[3] != info["blueprint"]["catalog_sha256"]):
                raise IntakeError("facts_consent_pin_mismatch")
            if conn.execute(
                "SELECT 1 FROM knowledge_fact_consent_revocations WHERE project_id=%s AND consent_id=%s",
                (pid, cid),
            ).fetchone():
                raise IntakeError("facts_consent_revoked")
            if conn.execute(
                "SELECT 1 FROM knowledge_fact_execution_runs WHERE consent_id=%s",
                (cid,),
            ).fetchone():
                raise IntakeError("facts_consent_already_used")
            ident = conn.execute(
                """INSERT INTO knowledge_fact_execution_runs(
                     project_id,batch_id,quote_id,consent_id,idempotency_key,authorized_usd)
                   VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",
                (pid, bid, qid, cid, idempotency_key, accepted[0]),
            ).fetchone()[0]
            return {"run_id": str(ident), "status": "pending", "created": True,
                    "provider_calls": 0}

    def advance(self, project_id, run_id, *, sender=None, api_key=None):
        """Reserve, commit, then make exactly ONE provider request for next pack."""
        pid = _project(project_id)
        rid = _uuid(run_id, "facts_run_id_invalid")
        # A missing credential MUST NOT reserve or create an uncertain attempt.
        if sender is None:
            api_key = api_key or resolve_key()
            if not api_key:
                raise IntakeError("facts_openrouter_key_missing")
        with self.connection_factory() as conn:
            row = conn.execute(
                """SELECT batch_id,status FROM knowledge_fact_execution_runs
                   WHERE project_id=%s AND id=%s""", (pid, rid),
            ).fetchone()
        if not row:
            raise IntakeError("facts_run_not_found")
        if row[1] != "pending":
            raise IntakeError("facts_run_not_pending")
        bid = str(row[0])
        info = _fresh(self.requests, pid, bid)
        with self.connection_factory() as conn:
            _guard(conn, pid, info, self.requests.resolver)
            row = conn.execute(
                """SELECT batch_id,consent_id,status,authorized_usd,spent_usd,
                          reserved_usd,next_pack_index
                   FROM knowledge_fact_execution_runs
                   WHERE project_id=%s AND id=%s FOR UPDATE""",
                (pid, rid),
            ).fetchone()
            if not row or str(row[0]) != bid or row[2] != "pending":
                raise IntakeError("facts_run_not_pending")
            if conn.execute(
                "SELECT 1 FROM knowledge_fact_consent_revocations WHERE project_id=%s AND consent_id=%s",
                (pid, row[1]),
            ).fetchone():
                conn.execute(
                    """UPDATE knowledge_fact_execution_runs
                       SET status='revoked',updated_at=clock_timestamp()
                       WHERE project_id=%s AND id=%s""", (pid, rid),
                )
                return {"status": "revoked", "provider_calls": 0}
            idx = row[6]
            if idx >= len(info["parts"]):
                raise IntakeError("facts_run_index_corrupt")
            part = info["parts"][idx]
            original = info["blueprint"]["parts"][idx]
            reserve = _usd(part["indicative_reservation_usd"], "part reservation")
            if part["request_sha256"] != original["request_sha256"] or reserve != _usd(
                original["quoted_reservation_usd"], "pinned reservation"
            ):
                raise IntakeError("facts_request_pin_changed")
            if conn.execute(
                """SELECT 1 FROM knowledge_fact_execution_attempts
                   WHERE project_id=%s AND run_id=%s AND pack_index=%s""",
                (pid, rid, idx),
            ).fetchone():
                raise IntakeError("facts_paid_retry_prohibited")
            if reserve > row[3] - row[4] - row[5]:
                conn.execute(
                    """UPDATE knowledge_fact_execution_runs
                       SET status='paused_budget',updated_at=clock_timestamp()
                       WHERE project_id=%s AND id=%s""", (pid, rid),
                )
                return {"status": "paused_budget", "provider_calls": 0,
                        "needed_usd": str(reserve)}
            attempt_id = conn.execute(
                """INSERT INTO knowledge_fact_execution_attempts(
                     project_id,run_id,batch_id,pack_index,request_sha256,status,reservation_usd)
                   VALUES(%s,%s,%s,%s,%s,'reserved',%s) RETURNING id""",
                (pid, rid, bid, idx, part["request_sha256"], reserve),
            ).fetchone()[0]
            conn.execute(
                """UPDATE knowledge_fact_execution_runs
                   SET reserved_usd=reserved_usd+%s,status='running',
                       updated_at=clock_timestamp()
                   WHERE project_id=%s AND id=%s""", (reserve, pid, rid),
            )
        # Committed reservation is a no-retry fence across crashes and workers.
        provider = None
        provider_error = None
        try:
            provider = _provider_response(part["request"], sender=sender, api_key=api_key)
        except Exception as exc:
            provider_error = (exc.code if isinstance(exc, IntakeError)
                              else "facts_provider_outcome_uncertain")
        draft = None
        error = provider_error
        charge = provider["charged"] if provider is not None else None
        if provider is not None:
            if provider["model"] != MODEL_ID:
                error = "facts_provider_model_mismatch"
            elif provider["finish_reason"] != "stop":
                error = "facts_provider_incomplete"
            else:
                manifest, payload = info["source_packs"][idx]
                try:
                    draft = validate_candidate_response(provider["content"], manifest, payload)
                except (IntakeError, ValueError, TypeError):
                    error = "facts_provider_draft_invalid"
            if charge > reserve:
                error = "facts_provider_over_reserved"
        with self.connection_factory() as conn:
            row = conn.execute(
                """SELECT status,next_pack_index FROM knowledge_fact_execution_runs
                   WHERE project_id=%s AND id=%s FOR UPDATE""", (pid, rid),
            ).fetchone()
            if row != ("running", idx):
                raise IntakeError("facts_attempt_reconciliation_required")
            if charge is None:
                # Unknown possible billing: keep reservation, stop run.
                conn.execute(
                    """UPDATE knowledge_fact_execution_attempts
                       SET status='uncertain',error_code=%s,ended_at=clock_timestamp()
                       WHERE id=%s AND project_id=%s""",
                    (error or "facts_provider_outcome_uncertain", attempt_id, pid),
                )
                conn.execute(
                    """UPDATE knowledge_fact_execution_runs
                       SET status='uncertain',updated_at=clock_timestamp()
                       WHERE project_id=%s AND id=%s""", (pid, rid),
                )
                return {"status": "uncertain", "provider_calls": 1,
                        "reservation_usd": str(reserve), "error_code": error}
            if error:
                next_status = "paused_budget" if charge > reserve else "failed"
            elif idx + 1 == len(info["parts"]):
                next_status = "ready_for_normalization"
            else:
                next_status = "pending"
            conn.execute(
                """UPDATE knowledge_fact_execution_attempts
                   SET status=%s,charged_usd=%s,provider_response_id=%s,usage=%s,
                       draft_candidates=%s,candidate_count=%s,candidate_limit_reached=%s,
                       error_code=%s,ended_at=clock_timestamp()
                   WHERE project_id=%s AND id=%s""",
                ("error" if error else "completed", charge,
                 provider["response_id"], Jsonb(provider["usage"]),
                 Jsonb(draft) if draft is not None else None,
                 draft["candidate_count"] if draft else None,
                 draft["candidate_limit_reached"] if draft else None,
                 error, pid, attempt_id),
            )
            conn.execute(
                """UPDATE knowledge_fact_execution_runs
                   SET spent_usd=spent_usd+%s,reserved_usd=reserved_usd-%s,
                       next_pack_index=next_pack_index+1,status=%s,
                       updated_at=clock_timestamp()
                   WHERE project_id=%s AND id=%s""",
                (charge, reserve, next_status, pid, rid),
            )
            return {"status": next_status, "pack_index": idx,
                    "charged_usd": str(charge), "provider_calls": 1,
                    "candidate_count": draft["candidate_count"] if draft else 0,
                    "error_code": error}

    def inspect(self, project_id, run_id, *, include_drafts=False):
        """Project-scoped ledger. Drafts stay pending B4/B5 and B8 review."""
        pid = _project(project_id)
        rid = _uuid(run_id, "facts_run_id_invalid")
        with self.connection_factory() as conn:
            row = conn.execute(
                """SELECT batch_id,status,authorized_usd,spent_usd,reserved_usd,
                          next_pack_index FROM knowledge_fact_execution_runs
                   WHERE project_id=%s AND id=%s""", (pid, rid),
            ).fetchone()
            if row is None:
                raise IntakeError("facts_run_not_found")
            attempts = conn.execute(
                """SELECT pack_index,status,reservation_usd,charged_usd,
                          provider_response_id,usage,candidate_count,
                          candidate_limit_reached,error_code,draft_candidates
                   FROM knowledge_fact_execution_attempts
                   WHERE project_id=%s AND run_id=%s ORDER BY pack_index""",
                (pid, rid),
            ).fetchall()
        return {
            "run_id": rid, "batch_id": str(row[0]), "status": row[1],
            "authorized_usd": str(row[2]), "spent_usd": str(row[3]),
            "reserved_usd": str(row[4]), "next_pack_index": row[5],
            "attempts": [{
                "pack_index": a[0], "status": a[1],
                "reservation_usd": str(a[2]),
                "charged_usd": str(a[3]) if a[3] is not None else None,
                "response_id": a[4], "usage": a[5],
                "candidate_count": a[6], "candidate_limit_reached": a[7],
                "error_code": a[8],
                **({"draft": a[9]} if include_drafts else {}),
            } for a in attempts],
            "approved": False, "provider_calls": len(attempts),
        }
