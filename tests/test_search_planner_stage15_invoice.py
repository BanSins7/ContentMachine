"""SP-15D: fail-closed external evidence + atomic billing settlement offline QA."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import unittest
from unittest import mock

from intelligence.search_planner.invoice_reconciliation import (
    ACTION, SCHEMA, InvoiceReconciliationController,
    apply_verified_invoice, list_uncertain_invoices, read_uncertain_attempt,
    validate_verified_invoice,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

PID = "a" * 32
GID = "11111111-1111-4111-8111-111111111111"
AID = "22222222-2222-4222-8222-222222222222"
DIGEST = "b" * 64
INVOICE_HASH = "c" * 64
ACCOUNT_HASH = "d" * 64
DISPATCHED = datetime(2026, 10, 25, 13, 0, tzinfo=timezone.utc)
VERIFIED = datetime(2026, 10, 26, 15, 0, tzinfo=timezone.utc)


def context():
    return {
        "project_id": PID, "generation_id": GID, "attempt_id": AID,
        "request_sha256": DIGEST, "provider_response_id": "gen-verified-1",
        "dispatch_intent_at": DISPATCHED, "reservation_usd": Decimal("1.50000000"),
    }


def invoice(**overrides):
    raw = {
        "schema_version": SCHEMA, "project_id": PID,
        "generation_id": GID, "attempt_id": AID,
        "request_sha256": DIGEST, "provider_response_id": "gen-verified-1",
        "invoice_line_id": "bill-line-0001", "invoice_sha256": INVOICE_HASH,
        "provider_account_sha256": ACCOUNT_HASH,
        "verification_method": "account_billing_export_exact_request",
        "charge_usd": "0.12000000",
        "provider_verified_at": VERIFIED.isoformat(),
    }
    return {**raw, **overrides}


class _Connection:
    def __init__(self, rows):
        self.rows = list(rows)
        self.queries = []
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False
    def transaction(self):
        return nullcontext()
    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        return self
    def fetchone(self):
        return self.rows.pop(0)
    def fetchall(self):
        return self.rows.pop(0)


class _Port:
    def __init__(self, value):
        self.value = value
        self.calls = []
    def verify_attempt_invoice(self, pid, gid, aid, sha, response_id):
        self.calls.append((pid, gid, aid, sha, response_id))
        return self.value


class SP15DInvoiceTests(unittest.TestCase):
    def test_additive_immutable_evidence_sql_shape(self):
        migration = (
            Path(__file__).resolve().parents[1] /
            "intelligence/migrations/058_sp_invoice_reconciliations.sql"
        ).read_text()
        self.assertIn("CREATE TABLE sp_invoice_reconciliations", migration)
        self.assertIn("UNIQUE(project_id,invoice_line_id)", migration)
        self.assertIn("attempt_id UUID NOT NULL UNIQUE", migration)
        self.assertIn("BEFORE UPDATE OR DELETE", migration)
        self.assertIn("FOREIGN KEY(generation_id,project_id)", migration)
        self.assertNotIn("DROP TABLE", migration)

    def test_billing_invoice_is_globally_unique_across_projects(self):
        ddl = (
            Path(__file__).resolve().parents[1] /
            "intelligence/migrations/058_sp_invoice_reconciliations.sql"
        ).read_text()
        self.assertIn("UNIQUE(provider_account_sha256,invoice_line_id)", ddl)
        self.assertIn("UNIQUE(provider_account_sha256,provider_response_id)", ddl)
        self.assertIn("UNIQUE(project_id,invoice_line_id)", ddl)

    def test_valid_provider_attestation_is_strict_metadata_not_invoice_document(self):
        result = validate_verified_invoice(invoice(), context=context(),
                                           observed_at=VERIFIED + timedelta(days=1))
        self.assertEqual(result["charge_usd"], Decimal("0.12000000"))
        self.assertEqual(result["provider_response_id"], "gen-verified-1")
        self.assertEqual(result["provider_verified_at"], VERIFIED)
        self.assertNotIn("raw_invoice", result)

    def test_wrong_cross_project_attempt_digest_or_response_id_rejected(self):
        for key, value in (
            ("project_id", "e" * 32), ("generation_id", AID),
            ("attempt_id", GID), ("request_sha256", "f" * 64),
            ("provider_response_id", "other"), ("verification_method", "screenshot"),
        ):
            with self.subTest(key=key), self.assertRaises(PipelineError):
                validate_verified_invoice(invoice(**{key:value}), context=context())

    def test_unknown_account_invoice_or_untrusted_partial_evidence_rejected(self):
        for key, value in (
            ("invoice_line_id", "../unsafe"),
            ("invoice_sha256", "not-a-digest"),
            ("provider_account_sha256", None),
            ("provider_verified_at", "2026-10-26"),
            ("provider_verified_at", (DISPATCHED-timedelta(minutes=1)).isoformat()),
            ("provider_verified_at", (datetime.now(timezone.utc)+timedelta(days=1)).isoformat()),
            ("charge_usd", "1.500000001"), ("charge_usd", "-1"),
            ("charge_usd", "1.50000001"), ("charge_usd", 0),
            ("charge_usd", "NaN"), ("charge_usd", "Infinity"),
        ):
            with self.subTest(key=key, value=value), self.assertRaises(PipelineError):
                validate_verified_invoice(invoice(**{key:value}), context=context())
        with self.assertRaises(PipelineError):
            validate_verified_invoice({**invoice(), "raw_invoice": "untrusted"},
                                      context=context())

    def test_zero_amount_requires_per_request_verified_invoice_line(self):
        proof = validate_verified_invoice(invoice(charge_usd="0"),
                                          context=context())
        self.assertEqual(proof["charge_usd"], Decimal("0"))
        self.assertEqual(proof["invoice_line_id"], "bill-line-0001")

    def test_no_guard_fails_before_verifier_or_db(self):
        port = _Port(invoice())
        controller = InvoiceReconciliationController(
            verified_invoice_port=port, attempt_reader=lambda *a: self.fail("db read"))
        with self.assertRaises(PipelineError) as e:
            controller.reconcile(PID, GID, AID)
        self.assertEqual(e.exception.http_status, 403)
        self.assertEqual(port.calls, [])

    def test_no_provider_adapter_denies_money_mutation_even_with_owner(self):
        controller = InvoiceReconciliationController(
            owner_action_guard=lambda *args: True,
            attempt_reader=lambda *a: self.fail("db read"),
            settlement_writer=lambda *a: self.fail("ledger write"),
        )
        with self.assertRaises(PipelineError) as e:
            controller.reconcile(PID, GID, AID)
        self.assertEqual(e.exception.http_status, 503)

    def test_fake_client_approval_does_not_pass(self):
        controller = InvoiceReconciliationController(owner_action_guard=lambda *a: "True")
        with self.assertRaises(PipelineError):
            controller.reconcile(PID, GID, AID)

    def test_owner_verified_port_pins_id_and_passes_decimal_only_to_writer(self):
        captured = []
        port = _Port(invoice())
        def writer(pid, gid, aid, evidence):
            captured.append((pid, gid, aid, evidence))
            return {"status": "reconciled_failed"}
        controller = InvoiceReconciliationController(
            owner_action_guard=lambda action, pid, gid:
                action == ACTION and pid == PID and gid == GID,
            verified_invoice_port=port,
            trusted_billing_account_sha256=ACCOUNT_HASH,
            attempt_reader=lambda *a: context(),
            settlement_writer=writer,
        )
        self.assertEqual(controller.reconcile(PID, GID, AID)["status"],
                         "reconciled_failed")
        self.assertEqual(port.calls, [(PID, GID, AID, DIGEST, "gen-verified-1")])
        self.assertEqual(captured[0][3]["charge_usd"], Decimal("0.12000000"))

    def test_cross_account_invoice_fails_after_real_account_pin(self):
        port = _Port(invoice(provider_account_sha256="e" * 64))
        controller = InvoiceReconciliationController(
            owner_action_guard=lambda *a: True,
            verified_invoice_port=port,
            trusted_billing_account_sha256=ACCOUNT_HASH,
            attempt_reader=lambda *a: context(),
            settlement_writer=lambda *a: self.fail("no write on another account"),
        )
        with self.assertRaisesRegex(PipelineError, "another billing account"):
            controller.reconcile(PID, GID, AID)

    def test_no_server_pinned_billing_account_disables_reconciliation(self):
        port = _Port(invoice())
        controller = InvoiceReconciliationController(
            owner_action_guard=lambda *a: True,
            verified_invoice_port=port,
            attempt_reader=lambda *a: self.fail("must not read DB"),
            settlement_writer=lambda *a: self.fail("must not settle"),
        )
        with self.assertRaisesRegex(PipelineError, "billing account not configured"):
            controller.reconcile(PID, GID, AID)
        self.assertEqual(port.calls, [])

    def test_provider_scope_tamper_cannot_call_settlement(self):
        port = _Port(invoice(project_id="f"*32))
        controller = InvoiceReconciliationController(
            owner_action_guard=lambda *a: True,
            verified_invoice_port=port,
            trusted_billing_account_sha256=ACCOUNT_HASH,
            attempt_reader=lambda *a: context(),
            settlement_writer=lambda *a: self.fail("not allowed"),
        )
        with self.assertRaises(PipelineError):
            controller.reconcile(PID, GID, AID)

    def test_database_read_enforces_project_generation_and_no_response_id_denies(self):
        conn = _Connection([("uncertain", DIGEST, None,
                             DISPATCHED, Decimal("1.50000000"))])
        with self.assertRaisesRegex(PipelineError, "response ID"):
            read_uncertain_attempt(PID, GID, AID, connect=lambda: conn)
        self.assertIn("SET TRANSACTION", conn.queries[0][0])
        self.assertIn("g.project_id=%s", conn.queries[1][0])
        self.assertEqual(conn.queries[1][1], (PID,GID,AID))

    def test_read_only_worklist_never_asserts_invoice_verified(self):
        conn = _Connection([[(AID, Decimal("1.5"), False)]])
        result = list_uncertain_invoices(PID, GID, connect=lambda: conn)
        self.assertEqual(result["attempts"][0]["invoice_verified"], False)
        self.assertEqual(result["attempts"][0]["has_provider_response_id"], False)
        self.assertEqual(result["paid_requests"], 0)
        self.assertIn("a.status='uncertain'", conn.queries[1][0])

    def test_atomic_apply_persists_metadata_and_releases_only_exact_reserve(self):
        state = {
            "gid": GID, "bid": "33333333-3333-4333-8333-333333333333",
            "consent_id": "44444444-4444-4444-8444-444444444444",
            "state": "uncertain", "reservation": Decimal("1.50000000"),
            "charged": None,
        }
        conn = _Connection([
            (DIGEST, "gen-verified-1", DISPATCHED),
            (Decimal("5"), Decimal("0.2"), Decimal("1.5")),
        ])
        proof = validate_verified_invoice(invoice(), context=context())
        with mock.patch(
            "intelligence.search_planner.invoice_reconciliation._load_attempt_locked",
            return_value=state
        ):
            r = apply_verified_invoice(PID, GID, AID, proof, connect=lambda: conn)
        self.assertEqual(r["charged_usd"], "0.12000000")
        self.assertFalse(r["reused"])
        cmds = [x[0] for x in conn.queries]
        self.assertTrue(any("INSERT INTO sp_invoice_reconciliations" in c for c in cmds))
        self.assertTrue(any("reserved_usd=reserved_usd-%s" in c and
                            "spent_usd=spent_usd+%s" in c for c in cmds))
        self.assertTrue(any("UPDATE sqg_attempts" in c and
                            "reconciled_failed" in c for c in cmds))
        self.assertTrue(any("INSERT INTO sqg_events" in c for c in cmds))
        self.assertFalse(any("sqg_candidates" in c or "sqg_outbox" in c for c in cmds))

    def test_ledger_shortfall_does_not_insert_invoice_or_release_money(self):
        state = {
            "gid": GID, "bid": "33333333-3333-4333-8333-333333333333",
            "consent_id": "44444444-4444-4444-8444-444444444444",
            "state": "uncertain", "reservation": Decimal("1.50000000"),
            "charged": None,
        }
        conn = _Connection([
            (DIGEST, "gen-verified-1", DISPATCHED),
            (Decimal("5"), Decimal("0.2"), Decimal("0.8")),
        ])
        proof = validate_verified_invoice(invoice(), context=context())
        with mock.patch(
            "intelligence.search_planner.invoice_reconciliation._load_attempt_locked",
            return_value=state
        ), self.assertRaises(PipelineError):
            apply_verified_invoice(PID, GID, AID, proof, connect=lambda: conn)
        self.assertFalse(any("INSERT INTO sp_invoice" in sql for sql,_ in conn.queries))

    def test_idempotent_replay_requires_matching_immutable_invoice(self):
        state = {
            "gid": GID, "bid": "33333333-3333-4333-8333-333333333333",
            "consent_id": "44444444-4444-4444-8444-444444444444",
            "state": "reconciled_failed",
            "reservation": Decimal("1.50000000"),
            "charged": Decimal("0.12000000"),
        }
        conn = _Connection([("bill-line-0001", INVOICE_HASH, Decimal("0.12000000"),
                             "gen-verified-1", DIGEST, ACCOUNT_HASH,
                             "account_billing_export_exact_request", VERIFIED)])
        proof = validate_verified_invoice(invoice(), context=context())
        with mock.patch(
            "intelligence.search_planner.invoice_reconciliation._load_attempt_locked",
            return_value=state
        ):
            r = apply_verified_invoice(PID, GID, AID, proof, connect=lambda: conn)
        self.assertTrue(r["reused"])
        self.assertFalse(any("UPDATE" in sql for sql,_ in conn.queries))

    def test_same_settled_attempt_can_be_read_only_reverified(self):
        conn = _Connection([("reconciled_failed", DIGEST, "gen-verified-1",
                             DISPATCHED, Decimal("1.5"))])
        result = read_uncertain_attempt(PID, GID, AID, connect=lambda: conn)
        self.assertEqual(result["provider_response_id"], "gen-verified-1")

    def test_replay_altered_account_evidence_conflicts(self):
        state = {
            "gid": GID, "bid": "33333333-3333-4333-8333-333333333333",
            "consent_id": "44444444-4444-4444-8444-444444444444",
            "state": "reconciled_failed",
            "reservation": Decimal("1.50000000"),
            "charged": Decimal("0.12000000"),
        }
        conn = _Connection([("bill-line-0001", INVOICE_HASH, Decimal("0.12000000"),
                             "gen-verified-1", DIGEST, "e"*64,
                             "account_billing_export_exact_request", VERIFIED)])
        proof = validate_verified_invoice(invoice(), context=context())
        with mock.patch(
            "intelligence.search_planner.invoice_reconciliation._load_attempt_locked",
            return_value=state
        ), self.assertRaisesRegex(PipelineError, "already settled differently"):
            apply_verified_invoice(PID, GID, AID, proof, connect=lambda: conn)
        self.assertFalse(any("UPDATE" in sql for sql,_ in conn.queries))

    def test_stale_evidence_fails_closed_after_locked_recheck(self):
        state = {
            "gid": GID, "bid": "33333333-3333-4333-8333-333333333333",
            "consent_id": "44444444-4444-4444-8444-444444444444",
            "state": "uncertain", "reservation": Decimal("1.50000000"),
            "charged": None,
        }
        conn = _Connection([("changed"*9, "gen-verified-1", DISPATCHED)])
        proof = validate_verified_invoice(invoice(), context=context())
        with mock.patch(
            "intelligence.search_planner.invoice_reconciliation._load_attempt_locked",
            return_value=state
        ), self.assertRaises(PipelineError):
            apply_verified_invoice(PID, GID, AID, proof, connect=lambda: conn)
        self.assertFalse(any("UPDATE" in sql for sql,_ in conn.queries))


if __name__ == "__main__":
    unittest.main()
