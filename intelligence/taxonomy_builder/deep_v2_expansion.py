"""RTB FIX-05-C: independently consented, exactly once Opus expansion stages.

The initial Deep V2 consent pays for Pass1+Pass2 ONLY. Each expansion:
quote exact prompt → independent human approval → audited budget top-up →
one reserved attempt → one provider POST → known-cost settlement or uncertain.
No automatic expansion and no provider request from preview or consent.
"""
from __future__ import annotations

from decimal import Decimal
from datetime import datetime, timezone

from psycopg.types.json import Jsonb

from intelligence.knowledge_extraction.human_review import _auth, _reviewer
from intelligence.project_sources.context_builder import sha as canonical_sha
from .deep_contracts import quality_gate
from .deep_contracts_v2 import validate_deep_v2_tree
from .deep_v2_followup import validate_protected_followup
from .deep_v2_pass2 import RTBDeepV2Pass2Repository, _load_review_and_first
from .deep_v2_provider import single_v2_request
from .deep_v2_quote_repository import _current_model, _fail
from .deep_v2_transport import v2_request
from .cost_estimator import usd, check_budget
from .provider import ProviderUncertain, resolve_key, reservation_cost
from .research_contracts_v2 import _project, _uuid, project_v2_tree_to_v1_core


EXPANSION_SCHEMA = "rtb-deep-v2-expansion-quote/v1"
MAX_EXPANSIONS = 2


class RTBDeepV2ExpansionRepository(RTBDeepV2Pass2Repository):

    @staticmethod
    def _read_expansion_quote(conn, pid, qid, *, lock=False):
        row = conn.execute(
            """SELECT id,run_id,expansion_no,research_input_sha256,
                      previous_tree_sha256,quality_sha256,quote_sha256,
                      request_sha256,prompt_sha256,model_id,catalog_version_id,
                      catalog_sha256,max_output_tokens,reservation_usd,quote,
                      expires_at
                 FROM tb_deep_v2_expansion_quotes
                WHERE project_id=%s AND id=%s"""
            + (" FOR UPDATE" if lock else ""),
            (pid, qid),
        ).fetchone()
        if not row:
            _fail("rtb_v2_expansion_quote_not_found", 404)
        keys = ("id", "run_id", "expansion_no", "research_sha", "previous_sha",
                "quality_sha", "quote_sha", "request_sha", "prompt_sha",
                "model_id", "catalog_version_id", "catalog_sha",
                "max_output_tokens", "reservation", "body", "expires_at")
        result = dict(zip(keys, row))
        result["id"] = str(result["id"])
        result["run_id"] = str(result["run_id"])
        return result

    @staticmethod
    def _previous(conn, pid, rid, number):
        stage = "deep_second" if number == 1 else "deep_expand_1"
        row = conn.execute(
            """SELECT output FROM tb_deep_v2_attempts
               WHERE project_id=%s AND run_id=%s AND stage=%s
                 AND status='completed'""",
            (pid, rid, stage),
        ).fetchone()
        if (not row or not isinstance(row[0], dict)
                or not isinstance(row[0].get("nodes"), list)):
            _fail("rtb_v2_expansion_missing_prior_paid_tree")
        return row[0]["nodes"]

    def _materialize(self, conn, pid, run, actor, number, *, max_output_tokens=8192):
        if number not in (1, 2) or run["expansion_count"] != number - 1:
            _fail("rtb_v2_expansion_count_not_allowed")
        _, frozen = self._binding(pid, run["binding_id"], actor)
        h1, first = _load_review_and_first(conn, pid, run["run_id"], frozen)
        previous = self._previous(conn, pid, run["run_id"], number)
        validate_deep_v2_tree(previous, frozen)
        quality_row = conn.execute(
            """SELECT quality_report FROM tb_deep_v2_runs
               WHERE project_id=%s AND id=%s""", (pid, run["run_id"]),
        ).fetchone()
        quality = quality_row[0] if quality_row else None
        if not isinstance(quality, dict) or quality.get("passed") is not False:
            _fail("rtb_v2_expansion_requires_quality_gap")
        excluded = sorted({n["id"] for n in first} - {n["id"] for n in h1})
        review_coverage = {
            **quality,
            "rejected_pass1_node_ids": excluded,
            "h1_is_locked": True,
            "last_tree_is_locked": True,
            "explicit_expansion_number": number,
        }
        model = _current_model(conn)
        _, payload = v2_request(
            frozen, model, role="deep_expand",
            max_output_tokens=max_output_tokens,
            review=h1, coverage=review_coverage,
            expansion_base=previous,
        )
        reservation = reservation_cost(payload, model)
        body = {
            "schema_version": EXPANSION_SCHEMA,
            "run_id": run["run_id"],
            "expansion_no": number,
            "research_input_sha256": frozen["research_input_sha256"],
            "previous_tree_sha256": canonical_sha(previous),
            "quality_sha256": canonical_sha(quality),
            "request_sha256": canonical_sha(payload),
            "prompt_sha256": canonical_sha(payload["messages"]),
            "model_id": model["model_id"],
            "catalog_version_id": model["catalog_version_id"],
            "catalog_sha256": model["catalog_sha256"],
            "max_output_tokens": max_output_tokens,
            "reservation_usd": str(reservation),
            "required_usd": usd(reservation),
            "one_paid_attempt_only": True,
            "base_consent_does_not_authorize_expansion": True,
        }
        return {
            "signed": {**body, "quote_sha256": canonical_sha(body)},
            "model": model, "request": payload,
            "reservation": reservation, "source": frozen,
            "h1": h1, "first": first, "previous": previous,
        }

    @staticmethod
    def _matches(q, data):
        s = data["signed"]
        if (q["body"] != s
                or q["quote_sha"] != s["quote_sha256"]
                or q["request_sha"] != s["request_sha256"]
                or q["prompt_sha"] != s["prompt_sha256"]
                or q["research_sha"] != s["research_input_sha256"]
                or q["previous_sha"] != s["previous_tree_sha256"]
                or q["quality_sha"] != s["quality_sha256"]
                or q["catalog_version_id"] != s["catalog_version_id"]
                or q["catalog_sha"] != s["catalog_sha256"]
                or q["max_output_tokens"] != s["max_output_tokens"]
                or q["reservation"] != data["reservation"]):
            _fail("rtb_v2_expansion_quote_replay_mismatch")

    def quote_expansion(self, project_id, run_id, reviewer_id, *,
                        max_output_tokens=8192):
        """Zero-cost preparation; requires quality gap and expand_pending."""
        pid = _project(project_id)
        rid = _uuid(run_id, "rtb_v2_run_id")
        actor = _reviewer(reviewer_id)
        with self.connection_factory() as conn:
            with conn.transaction():
                _auth(conn, pid, actor)
                run = self._locked_run(conn, pid, rid)
                if run["status"] != "expand_pending" or run["expansion_count"] >= 2:
                    _fail("rtb_v2_expansion_not_pending")
                if self._revoked(conn, pid, run["consent_id"]):
                    _fail("rtb_v2_consent_revoked")
                number = run["expansion_count"] + 1
                if conn.execute(
                    """SELECT 1 FROM tb_deep_v2_expansion_consents
                       WHERE project_id=%s AND run_id=%s AND expansion_no=%s""",
                    (pid, rid, number),
                ).fetchone():
                    _fail("rtb_v2_expansion_already_authorized")
                priced = self._materialize(
                    conn, pid, run, actor, number,
                    max_output_tokens=max_output_tokens,
                )
                s = priced["signed"]
                qid = str(conn.execute(
                    """INSERT INTO tb_deep_v2_expansion_quotes(
                        project_id,run_id,expansion_no,research_input_sha256,
                        previous_tree_sha256,quality_sha256,quote_sha256,
                        request_sha256,prompt_sha256,model_id,
                        catalog_version_id,catalog_sha256,max_output_tokens,
                        reservation_usd,quote,created_by)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING id""",
                    (pid, rid, number, s["research_input_sha256"],
                     s["previous_tree_sha256"], s["quality_sha256"],
                     s["quote_sha256"], s["request_sha256"],
                     s["prompt_sha256"], s["model_id"],
                     s["catalog_version_id"], s["catalog_sha256"],
                     s["max_output_tokens"], priced["reservation"],
                     Jsonb(s), actor),
                ).fetchone()[0])
        return {
            "quote_id": qid, "run_id": rid, "expansion_no": number,
            "quote_sha256": s["quote_sha256"],
            "required_usd": usd(priced["reservation"]),
            "explicit_consent_required": True, "provider_calls": 0,
        }

    def authorize_expansion(self, project_id, quote_id, reviewer_id,
                            authorized_usd, *, acknowledged_quote_sha256):
        """Explicit human grant; SQL AFTER INSERT increases run budget once."""
        pid = _project(project_id)
        qid = _uuid(quote_id, "rtb_v2_expansion_quote_id")
        actor = _reviewer(reviewer_id)
        with self.connection_factory() as conn:
            with conn.transaction():
                _auth(conn, pid, actor)
                q = self._read_expansion_quote(conn, pid, qid, lock=True)
                run = self._locked_run(conn, pid, q["run_id"])
                if run["status"] != "expand_pending" or run["expansion_count"] != q["expansion_no"] - 1:
                    _fail("rtb_v2_expansion_not_pending")
                if self._revoked(conn, pid, run["consent_id"]):
                    _fail("rtb_v2_consent_revoked")
                if datetime.now(timezone.utc) >= q["expires_at"]:
                    _fail("rtb_v2_expansion_quote_expired")
                if acknowledged_quote_sha256 != q["quote_sha"]:
                    _fail("rtb_v2_expansion_consent_hash_mismatch")
                approved_budget = check_budget(authorized_usd, q["reservation"])
                if approved_budget != Decimal(usd(q["reservation"])):
                    _fail("rtb_v2_expansion_budget_must_match_quote")
                if conn.execute(
                    """SELECT 1 FROM tb_deep_v2_expansion_consents
                       WHERE project_id=%s AND run_id=%s AND expansion_no=%s""",
                    (pid, run["run_id"], q["expansion_no"]),
                ).fetchone():
                    _fail("rtb_v2_expansion_already_authorized")
                data = self._materialize(
                    conn, pid, run, actor, q["expansion_no"],
                    max_output_tokens=q["max_output_tokens"],
                )
                self._matches(q, data)
                cid = str(conn.execute(
                    """INSERT INTO tb_deep_v2_expansion_consents(
                        project_id,quote_id,run_id,expansion_no,approved_by,
                        authorized_usd,acknowledged_quote_sha256)
                       VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (pid, qid, run["run_id"], q["expansion_no"], actor,
                     approved_budget, q["quote_sha"]),
                ).fetchone()[0])
        return {
            "consent_id": cid, "run_id": q["run_id"],
            "expansion_no": q["expansion_no"],
            "newly_authorized_usd": str(approved_budget),
            "provider_calls": 0, "consent_recorded": True,
        }

    def revoke_expansion(self, project_id, consent_id, reviewer_id):
        pid = _project(project_id)
        cid = _uuid(consent_id, "rtb_v2_expansion_consent_id")
        actor = _reviewer(reviewer_id)
        with self.connection_factory() as conn:
            with conn.transaction():
                _auth(conn, pid, actor)
                row = conn.execute(
                    """SELECT id FROM tb_deep_v2_expansion_consents
                       WHERE project_id=%s AND id=%s FOR UPDATE""",
                    (pid, cid),
                ).fetchone()
                if not row:
                    _fail("rtb_v2_expansion_consent_not_found", 404)
                conn.execute(
                    """INSERT INTO tb_deep_v2_expansion_revocations(
                        project_id,consent_id,revoked_by)
                       VALUES(%s,%s,%s) ON CONFLICT DO NOTHING""",
                    (pid, cid, actor),
                )
        return {"revoked": True, "provider_calls": 0}

    def advance_expansion(self, project_id, run_id, consent_id, reviewer_id,
                          *, sender=None, api_key=None):
        """One user-triggered paid expansion; never implicit retry."""
        pid = _project(project_id)
        rid = _uuid(run_id, "rtb_v2_run_id")
        cid = _uuid(consent_id, "rtb_v2_expansion_consent_id")
        actor = _reviewer(reviewer_id)
        if sender is None and api_key is None:
            api_key = resolve_key()
        if sender is None and not api_key:
            _fail("rtb_v2_provider_key_missing", 503)
        with self.connection_factory() as conn:
            with conn.transaction():
                _auth(conn, pid, actor)
                link = conn.execute(
                    """SELECT quote_id FROM tb_deep_v2_expansion_consents
                       WHERE project_id=%s AND id=%s AND run_id=%s""",
                    (pid, cid, rid),
                ).fetchone()
                if not link:
                    _fail("rtb_v2_expansion_consent_not_found", 404)
                q = self._read_expansion_quote(conn, pid, str(link[0]), lock=True)
                run = self._locked_run(conn, pid, rid)
                if run["status"] != "expand_pending" or run["expansion_count"] != q["expansion_no"] - 1:
                    _fail("rtb_v2_expansion_not_pending")
                if self._revoked(conn, pid, run["consent_id"]):
                    _fail("rtb_v2_consent_revoked")
                consent = conn.execute(
                    """SELECT authorized_usd FROM tb_deep_v2_expansion_consents
                       WHERE project_id=%s AND id=%s AND run_id=%s
                         AND quote_id=%s FOR UPDATE""",
                    (pid, cid, rid, q["id"]),
                ).fetchone()
                if not consent or consent[0] != Decimal(usd(q["reservation"])):
                    _fail("rtb_v2_expansion_consent_budget_drift")
                if conn.execute(
                    """SELECT 1 FROM tb_deep_v2_expansion_revocations
                       WHERE project_id=%s AND consent_id=%s""",
                    (pid, cid),
                ).fetchone():
                    _fail("rtb_v2_expansion_revoked")
                data = self._materialize(
                    conn, pid, run, actor, q["expansion_no"],
                    max_output_tokens=q["max_output_tokens"],
                )
                self._matches(q, data)
                reserve = reservation_cost(data["request"], data["model"])
                if reserve > q["reservation"] or (
                    reserve > run["authorized"] - run["spent"] - run["reserved"]
                ):
                    _fail("rtb_v2_expansion_budget_exhausted")
                stage = f"deep_expand_{q['expansion_no']}"
                if conn.execute(
                    """SELECT 1 FROM tb_deep_v2_attempts
                       WHERE project_id=%s AND run_id=%s AND stage=%s""",
                    (pid, rid, stage),
                ).fetchone():
                    _fail("rtb_v2_expansion_stage_already_attempted")
                conn.execute(
                    """UPDATE tb_deep_v2_runs
                       SET status='expand_running',
                           reserved_usd=reserved_usd+%s,
                           updated_at=clock_timestamp()
                       WHERE project_id=%s AND id=%s""",
                    (reserve, pid, rid),
                )
                aid = str(conn.execute(
                    """INSERT INTO tb_deep_v2_attempts(
                        project_id,run_id,stage,status,request_sha256,
                        prompt_sha256,reservation_usd)
                       VALUES(%s,%s,%s,'reserved',%s,%s,%s) RETURNING id""",
                    (pid, rid, stage, q["request_sha"],
                     q["prompt_sha"], reserve),
                ).fetchone()[0])
        result, known, error, output, quality = None, False, None, None, None
        try:
            result = single_v2_request(
                data["request"], sender=sender, api_key=api_key, timeout=300)
            known = True
        except ProviderUncertain:
            error = "rtb_v2_expansion_cost_uncertain"
        except Exception:
            error = "rtb_v2_expansion_transport_uncertain"
        if known:
            try:
                if result["model_id"] != q["model_id"] or result.get("finish_reason") != "stop":
                    _fail("rtb_v2_expansion_provider_drift_or_truncated")
                output = validate_protected_followup(
                    result["raw_json"], data["source"], data["previous"],
                    data["first"], role="deep_expand",
                )
                facts = [
                    {"id": f["rtb_fact_id"], "kind": f["kind"],
                     "text": f["reviewed_text"]}
                    for f in data["source"]["c1_approved_facts"]
                ]
                quality = quality_gate(
                    project_v2_tree_to_v1_core(output["nodes"]), facts,
                    previous=project_v2_tree_to_v1_core(data["previous"]),
                    model_duplicates=output["duplicates"],
                )
                output["quality_report"] = quality
            except (PipelineError, KeyError, ValueError, TypeError):
                error = "rtb_v2_expansion_charged_result_invalid"
        with self.connection_factory() as conn:
            with conn.transaction():
                run = self._locked_run(conn, pid, rid)
                if run["status"] != "expand_running":
                    _fail("rtb_v2_expansion_settlement_state_drift")
                saved = conn.execute(
                    """SELECT status,reservation_usd,request_sha256
                       FROM tb_deep_v2_attempts
                       WHERE project_id=%s AND run_id=%s AND id=%s
                       FOR UPDATE""", (pid, rid, aid),
                ).fetchone()
                if (not saved or saved[0] != "reserved" or saved[1] != reserve
                        or saved[2] != q["request_sha"]):
                    _fail("rtb_v2_expansion_attempt_drift")
                if not known:
                    conn.execute(
                        """UPDATE tb_deep_v2_attempts
                           SET status='uncertain',error_code=%s,
                               finished_at=clock_timestamp()
                           WHERE project_id=%s AND id=%s""",
                        (error, pid, aid),
                    )
                    conn.execute(
                        """UPDATE tb_deep_v2_runs
                           SET status='uncertain',updated_at=clock_timestamp()
                           WHERE project_id=%s AND id=%s""",
                        (pid, rid),
                    )
                    return {"run_id": rid, "status": "uncertain",
                            "provider_calls": 1, "automatic_retry_allowed": False}
                charged = result["charged_usd"]
                over_budget = run["spent"] + charged > run["authorized"]
                revoked = self._revoked(conn, pid, run["consent_id"]) or bool(
                    conn.execute(
                        """SELECT 1 FROM tb_deep_v2_expansion_revocations
                           WHERE project_id=%s AND consent_id=%s""",
                        (pid, cid),
                    ).fetchone()
                )
                status = (
                    "paused_budget" if over_budget else
                    "cancelled" if revoked else
                    "failed" if error else
                    "review2" if quality["passed"] else
                    "blocked_quality" if q["expansion_no"] == MAX_EXPANSIONS else
                    "expand_pending"
                )
                conn.execute(
                    """UPDATE tb_deep_v2_attempts
                       SET status=%s,charged_usd=%s,usage=%s,response_id=%s,
                           output=%s,error_code=%s,finished_at=clock_timestamp()
                       WHERE project_id=%s AND id=%s""",
                    ("error" if error else "completed", charged,
                     Jsonb(result["usage"]), result.get("response_id"),
                     Jsonb(output) if output is not None else None,
                     error, pid, aid),
                )
                conn.execute(
                    """UPDATE tb_deep_v2_runs
                       SET status=%s,expansion_count=expansion_count+1,
                           reserved_usd=reserved_usd-%s,
                           spent_usd=spent_usd+%s,quality_report=%s,
                           updated_at=clock_timestamp()
                       WHERE project_id=%s AND id=%s""",
                    (status, reserve, charged,
                     Jsonb(quality) if quality is not None else None,
                     pid, rid),
                )
                return {
                    "run_id": rid, "status": status,
                    "expansion_count": q["expansion_no"],
                    "charged_usd": str(charged), "quality": quality,
                    "provider_calls": 1, "automatic_retry_allowed": False,
                }
