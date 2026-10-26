"""RTB-FIX-02-B2b/C (no dispatch): exact-pass1 Deep V2 quote, consent, start.

The quote reserves exact-pass1 request cost and conservative BOUNDED Pass2
capacity (Review 1 does not exist at quote time). A later paid-advance MUST
recompute the actual request/reservation within this budget and require its
own explicit action. No provider invocation occurs anywhere in this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING

from psycopg.types.json import Jsonb

from intelligence.knowledge_extraction.human_review import _auth, _reviewer
from intelligence.project_sources.context_builder import sha as canonical_sha, canonical
from .catalog import latest_verified
from .cost_estimator import amount, rates, check_budget, usd
from .deep_v2_binding_repository import RTBDeepV2BindingRepository
from .deep_v2_lifecycle import MODEL_ID, validate_frozen_input
from .deep_v2_transport import (
    MAX_H1_BYTES, MAX_GATE_BYTES, VERSION, v2_request,
)
from .pipeline_contracts import PipelineError
from .pipeline_repository import _connect
from .provider import reservation_cost
from .research_contracts_v2 import _project, _uuid

QUOTE_SCHEMA = "rtb-deep-v2-quote/v1"
DOLLAR_SCALE = Decimal("0.000000000001")
MODEL_MAX_OUTPUT = 8192


def _fail(code: str, status=409):
    raise PipelineError(code, status)


def _rounded(x):
    return x.quantize(DOLLAR_SCALE, rounding=ROUND_CEILING)


def _current_model(conn):
    """Only a current verified catalog refresh is eligible to quote/authorize."""
    return latest_verified(conn, {MODEL_ID})[MODEL_ID]


def compute_deep_v2_quote(binding: dict, model: dict, *,
                           max_output_tokens: int = MODEL_MAX_OUTPUT) -> dict:
    """Pure deterministic quote. Pass1 exactly serialized, Pass2 reserved cap.

    Pass2 varies with reviewed tree; do not misrepresent its reservation as an
    exact POST quotation. The runner must enforce MAX_H1_BYTES/MAX_GATE_BYTES.
    """
    source = validate_frozen_input(binding)
    stage, request = v2_request(
        source, model, role="deep_first", max_output_tokens=max_output_tokens)
    pass1 = reservation_cost(request, model)
    prices = rates(model["pricing"])
    # Same system/context plus bounded human tree and quality coverage.
    base_tokens = len(canonical(request["messages"])) * 2 + 2048
    pass2_upper = base_tokens + 2 * (MAX_H1_BYTES + MAX_GATE_BYTES) + 2048
    if pass2_upper + max_output_tokens > model["capabilities"]["context_length"]:
        _fail("rtb_v2_pass2_context_reservation_too_small", 422)
    pass2 = _rounded((
        Decimal(pass2_upper) * prices["prompt"]
        + Decimal(max_output_tokens) * (
            prices["completion"] + prices["internal_reasoning"])
        + prices["request"]
    ) * Decimal("1.20"))
    if pass1 <= 0 or pass2 <= 0:
        _fail("rtb_v2_quote_amount_invalid", 422)
    total = _rounded(pass1 + pass2)
    catalog = {
        "model_id": MODEL_ID,
        "catalog_version_id": model["catalog_version_id"],
        "catalog_sha256": model["catalog_sha256"],
        "capabilities": model["capabilities"],
        "pricing": model["pricing"],
    }
    request_sha = canonical_sha(request)
    prompt_sha = canonical_sha(request["messages"])
    body = {
        "schema_version": QUOTE_SCHEMA,
        "binding_id": None,  # Filled by the authenticated repository below.
        "research_input_sha256": source["research_input_sha256"],
        "request_sha256": request_sha,
        "prompt_sha256": prompt_sha,
        "prompt_version": VERSION,
        "model_id": MODEL_ID,
        "catalog_sha256": model["catalog_sha256"],
        "catalog_version_id": model["catalog_version_id"],
        "model_snapshot_sha256": canonical_sha(catalog),
        "stage_config_sha256": canonical_sha(stage),
        "quoted_usd": str(total),
        "required_pass1_reservation_usd": str(pass1),
        "required_pass2_reservation_usd": str(pass2),
        "pass1_priced_exact_payload": True,
        "pass2_priced_conservative_bounded_capacity": True,
        "max_human_review_bytes": MAX_H1_BYTES,
        "max_quality_report_bytes": MAX_GATE_BYTES,
        "max_output_tokens": max_output_tokens,
        "independent_explicit_consent_required": True,
        "optional_expansions_not_included": True,
        "provider_calls": 0,
        "authorized": False,
    }
    return {
        "quote_body": body, "stage": stage, "model": catalog,
        "request": request, "pass1": pass1, "pass2": pass2, "total": total,
    }


def _signed_quote(binding_id: str, result: dict) -> dict:
    body = {**result["quote_body"], "binding_id": binding_id}
    return {**body, "quote_sha256": canonical_sha(body)}


def _quoted(row):
    if not row:
        _fail("rtb_v2_quote_not_found", 404)
    return {
        "quote_id": str(row[0]), "binding_id": str(row[1]),
        "research_sha": row[2], "quote_sha": row[3],
        "request_sha": row[4], "prompt_sha": row[5],
        "prompt_version": row[6], "model_id": row[7],
        "catalog_version_id": row[8], "catalog_sha": row[9],
        "model": row[10], "stage": row[11], "amount": row[12],
        "body": row[13], "expires_at": row[14],
    }


class RTBDeepV2QuoteRepository:
    """Explicit operator quote -> consent -> idempotent unpaid start."""

    def __init__(self, connection_factory=None, *, bindings=None):
        self.connection_factory = connection_factory or _connect
        self.bindings = bindings or RTBDeepV2BindingRepository(
            connection_factory=self.connection_factory)

    def _binding(self, pid, bid, actor):
        source = self.bindings.get(pid, bid, actor)
        return source, validate_frozen_input(source["research_input"])

    def _read_quote(self, conn, pid, qid, *, lock=False):
        row = conn.execute(
            """SELECT id,binding_id,research_input_sha256,quote_sha256,
                      request_sha256,prompt_sha256,prompt_version,model_id,
                      catalog_version_id,catalog_sha256,model_snapshot,
                      stage_config,quoted_usd,quote,expires_at
                 FROM tb_deep_v2_quotes WHERE project_id=%s AND id=%s"""
            + (" FOR UPDATE" if lock else ""),
            (pid, qid),
        ).fetchone()
        return _quoted(row)

    @staticmethod
    def _compare_quote(q, prepared, bid):
        signed = _signed_quote(bid, prepared)
        if (q["body"] != signed
                or q["quote_sha"] != signed["quote_sha256"]
                or q["request_sha"] != signed["request_sha256"]
                or q["prompt_sha"] != signed["prompt_sha256"]
                or q["prompt_version"] != VERSION
                or q["research_sha"] != signed["research_input_sha256"]
                or q["model_id"] != MODEL_ID
                or q["catalog_sha"] != signed["catalog_sha256"]
                or q["catalog_version_id"] != signed["catalog_version_id"]
                or q["model"] != prepared["model"]
                or q["stage"] != prepared["stage"]
                or q["amount"] != prepared["total"]):
            _fail("rtb_v2_quote_replay_mismatch")

    def quote(self, project_id: str, binding_id: str, reviewer_id: str, *,
              max_output_tokens=MODEL_MAX_OUTPUT) -> dict:
        """Issue a *nonpaid* exact first-pass quote; does not grant C consent."""
        pid = _project(project_id)
        bid = _uuid(binding_id, "rtb_v2_binding_invalid")
        actor = _reviewer(reviewer_id)
        record, source = self._binding(pid, bid, actor)
        if record["research_input_sha256"] != source["research_input_sha256"]:
            _fail("rtb_v2_binding_sha_changed")
        with self.connection_factory() as conn:
            with conn.transaction():
                _auth(conn, pid, actor)
                model = _current_model(conn)
                data = compute_deep_v2_quote(
                    source, model, max_output_tokens=max_output_tokens)
                signed = _signed_quote(bid, data)
                row = conn.execute(
                    """INSERT INTO tb_deep_v2_quotes(
                      project_id,binding_id,research_input_sha256,quote_sha256,
                      request_sha256,prompt_sha256,prompt_version,model_id,
                      catalog_version_id,catalog_sha256,model_snapshot,
                      stage_config,quote,required_pass1_reservation_usd,
                      required_pass2_reservation_usd,quoted_usd,created_by)
                      VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                             %s,%s,%s,%s,%s) RETURNING id""",
                    (pid, bid, source["research_input_sha256"],
                     signed["quote_sha256"], signed["request_sha256"],
                     signed["prompt_sha256"], VERSION, MODEL_ID,
                     model["catalog_version_id"], model["catalog_sha256"],
                     Jsonb(data["model"]), Jsonb(data["stage"]), Jsonb(signed),
                     data["pass1"], data["pass2"], data["total"], actor),
                ).fetchone()
        return {
            "quote_id": str(row[0]), "quote_sha256": signed["quote_sha256"],
            "required_usd": usd(data["total"]),  # Display/consent rounds UP to supported 6 decimal places.
            "pass1_exact_reservation_usd": str(data["pass1"]),
            "pass2_bounded_reservation_usd": str(data["pass2"]),
            "consent_required": True, "provider_calls": 0, "authorized": False,
        }

    def _verified(self, conn, pid, q, actor):
        record, source = self._binding(pid, q["binding_id"], actor)
        if record["research_input_sha256"] != q["research_sha"]:
            _fail("rtb_v2_quote_input_changed")
        current = _current_model(conn)
        prepared = compute_deep_v2_quote(
            source, current, max_output_tokens=q["stage"]["max_output_tokens"])
        self._compare_quote(q, prepared, q["binding_id"])
        return prepared

    def authorize(self, project_id, quote_id, reviewer_id, authorized_usd,
                  *, acknowledged_quote_sha256):
        """One distinct Deep V2 consent; never reuses B3 or B Stage14 funds."""
        pid = _project(project_id)
        qid = _uuid(quote_id, "rtb_v2_quote_id")
        actor = _reviewer(reviewer_id)
        with self.connection_factory() as conn:
            with conn.transaction():
                _auth(conn, pid, actor)
                q = self._read_quote(conn, pid, qid, lock=True)
                self._verified(conn, pid, q, actor)
                if datetime.now(timezone.utc) >= q["expires_at"]:
                    _fail("rtb_v2_quote_expired")
                if acknowledged_quote_sha256 != q["quote_sha"]:
                    _fail("rtb_v2_quote_consent_hash_mismatch")
                budget = check_budget(authorized_usd, q["amount"])
                earlier = conn.execute(
                    """SELECT id,authorized_usd FROM tb_deep_v2_consents
                       WHERE project_id=%s AND quote_id=%s""",
                    (pid, qid),
                ).fetchone()
                if earlier:
                    if (earlier[1] != budget or conn.execute(
                        """SELECT 1 FROM tb_deep_v2_revocations
                           WHERE project_id=%s AND consent_id=%s""",
                        (pid, earlier[0]),
                    ).fetchone()):
                        _fail("rtb_v2_consent_changed_or_revoked")
                    return {"consent_id": str(earlier[0]), "created": False,
                            "authorized_usd": str(budget), "provider_calls": 0}
                row = conn.execute(
                    """INSERT INTO tb_deep_v2_consents(
                         project_id,quote_id,authorized_usd,approved_by,
                         acknowledged_quote_sha256)
                       VALUES(%s,%s,%s,%s,%s) RETURNING id""",
                    (pid, qid, budget, actor, acknowledged_quote_sha256),
                ).fetchone()
        return {"consent_id": str(row[0]), "created": True,
                "authorized_usd": str(budget), "provider_calls": 0}

    def revoke(self, project_id, consent_id, reviewer_id):
        pid = _project(project_id)
        cid = _uuid(consent_id, "rtb_v2_consent_id")
        actor = _reviewer(reviewer_id)
        with self.connection_factory() as conn:
            with conn.transaction():
                _auth(conn, pid, actor)
                row = conn.execute(
                    """SELECT id FROM tb_deep_v2_consents
                       WHERE project_id=%s AND id=%s FOR UPDATE""",
                    (pid, cid),
                ).fetchone()
                if not row:
                    _fail("rtb_v2_consent_not_found", 404)
                conn.execute(
                    """INSERT INTO tb_deep_v2_revocations(
                        project_id,consent_id,revoked_by)
                       VALUES(%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (pid, cid, actor),
                )
        return {"revoked": True, "provider_calls": 0}

    def start(self, project_id, quote_id, consent_id, reviewer_id,
              idempotency_key):
        """Start pass1_pending only; never dispatch LLM from this endpoint."""
        pid = _project(project_id)
        qid = _uuid(quote_id, "rtb_v2_quote_id")
        cid = _uuid(consent_id, "rtb_v2_consent_id")
        actor = _reviewer(reviewer_id)
        if not isinstance(idempotency_key, str) or not 12 <= len(idempotency_key) <= 128:
            _fail("rtb_v2_idempotency_key_invalid", 422)
        start_hash = canonical_sha({
            "mode": "knowledge_snapshot_v1/deep-v2",
            "project_id": pid, "quote_id": qid,
            "consent_id": cid, "idempotency_key": idempotency_key,
        })
        with self.connection_factory() as conn:
            with conn.transaction():
                _auth(conn, pid, actor)
                q = self._read_quote(conn, pid, qid, lock=True)
                self._verified(conn, pid, q, actor)
                existing = conn.execute(
                    """SELECT id,start_request_sha256 FROM tb_deep_v2_runs
                       WHERE project_id=%s AND idempotency_key=%s FOR UPDATE""",
                    (pid, idempotency_key),
                ).fetchone()
                if existing:
                    if existing[1] != start_hash:
                        _fail("rtb_v2_start_idempotency_conflict")
                    return {"run_id": str(existing[0]), "created": False,
                            "provider_calls": 0}
                if datetime.now(timezone.utc) >= q["expires_at"]:
                    _fail("rtb_v2_quote_expired")
                c = conn.execute(
                    """SELECT id,authorized_usd FROM tb_deep_v2_consents
                       WHERE project_id=%s AND id=%s AND quote_id=%s FOR UPDATE""",
                    (pid, cid, qid),
                ).fetchone()
                if not c:
                    _fail("rtb_v2_wrong_consent")
                if conn.execute(
                    """SELECT 1 FROM tb_deep_v2_revocations
                       WHERE project_id=%s AND consent_id=%s""",
                    (pid, cid),
                ).fetchone():
                    _fail("rtb_v2_consent_revoked")
                if conn.execute(
                    """SELECT 1 FROM tb_deep_v2_runs
                       WHERE project_id=%s AND consent_id=%s""",
                    (pid, cid),
                ).fetchone():
                    _fail("rtb_v2_consent_already_used")
                row = conn.execute(
                    """INSERT INTO tb_deep_v2_runs(
                         project_id,binding_id,quote_id,consent_id,
                         idempotency_key,start_request_sha256,authorized_usd)
                       VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (pid, q["binding_id"], qid, cid,
                     idempotency_key, start_hash, c[1]),
                ).fetchone()
        return {"run_id": str(row[0]), "created": True,
                "status": "pass1_pending", "provider_calls": 0}
