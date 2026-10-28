"""SP-08 offline atomic-QV ledger contracts and fail-closed receipts.

No PostgreSQL or model required for these *contract* checks. Race, SQL isolation
and real transactional rollback must still be tested with PG17 in SP-16.
"""
from __future__ import annotations

import copy
import unittest
from uuid import UUID

from intelligence.search_planner.query_validation.integrity import (
    verify_validated_bundle, verify_outbox_event,
)
from intelligence.search_planner.query_validation.service import (
    get_validated_bundle, pending_validated_outbox, ack_validated_outbox,
    _require_committed_qv_outbox,
)
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

from test_search_planner_service_stage01 import valid_bundle, PID, QV, GEN, CAND
from test_search_planner_foundation import PUB

OID = "88888888-8888-4888-8888-888888888888"
ALIAS = "99999999-9999-4999-8999-999999999999"


def one_result(x):
    return SimpleResult(one=x)


class SimpleResult:
    def __init__(self, *, one=None, many=()):
        self.one, self.many = one, many
    def fetchone(self):
        return self.one
    def fetchall(self):
        return self.many


class FakeConnection:
    def __init__(self, handler):
        self.handler = handler
        self.queries = []
        self.rollback = False
        self.begin = 0
    def __enter__(self):
        return self
    def __exit__(self, typ, exc, tb):
        if typ is not None:
            self.rollback = True
        return False
    def transaction(self):
        self.begin += 1
        return self
    def execute(self, sql, args=None):
        self.queries.append((sql, args))
        if sql.startswith("SET TRANSACTION"):
            return SimpleResult()
        return self.handler(sql, args)


def base_bundle():
    b = valid_bundle()
    # Stage01 fixture itself contains proper selected QV candidate/coverage
    # and per-candidate selected decision, production-local mode.
    return copy.deepcopy(b)


def make_read_conn(*, selected_count=1, source_count=1, coverage=None, decisions=None):
    query = base_bundle()["queries"][0]
    out = base_bundle()
    cov = coverage if coverage is not None else out["coverage"]
    dec = decisions if decisions is not None else out["decisions"]
    def handler(sql, args):
        if "FROM qv_runs WHERE" in sql:
            return one_result((QV, GEN, "st-local-sha256:" + "a"*64,
                               "semantic_local", "b"*64, "c"*64,
                               selected_count, source_count))
        if "FROM qv_decisions d JOIN sqg_candidates c" in sql:
            return SimpleResult(many=[
                (query["candidate_id"], query["hypothesis_id"],
                 query["direction_id"], query["branch_id"],
                 query["source_fact_ids"], query["text"], query["query_mode"],
                 query["angle"], query["language"], query["research_stance"],
                 query["rationale"], query["relevance_score"],
                 query["negative_score"], query["mmr_score"],
                 query["rank_in_hypothesis"])
            ])
        if "FROM qv_coverage WHERE" in sql:
            return SimpleResult(many=[
                (q["hypothesis_id"], q["source_count"], q["selected_count"],
                 q["outcome"], q["reason"]) for q in cov
            ])
        if "FROM qv_decisions WHERE" in sql:
            return SimpleResult(many=[
                (q["candidate_id"], q["decision"], q["reason_code"],
                 q["duplicate_of"], q["positive_similarity"],
                 q["negative_similarity"], q["score_margin"], q["mmr_score"])
                for q in dec
            ])
        raise AssertionError("Unexpected SQL " + sql)
    return FakeConnection(handler)


def outbox_row(*, outbox=OID, selected=1, generation=GEN,
               mode="semantic_local", status="validated_queries_ready/v1"):
    return (
        outbox, QV, status,
        {"schema_version": "validated_queries/v1",
         "validation_run_id": QV,
         "generation_id": generation,
         "embedding_mode": mode,
         "selected_count": selected},
        GEN, "semantic_local", 1,
    )


class QVStage08Registry(unittest.TestCase):
    def test_complete_selected_and_coverage_verifier(self):
        b = base_bundle()
        self.assertIs(
            verify_validated_bundle(b, expected_selected_count=1,
                                    expected_source_count=1), b
        )

    def test_corrupt_qv_registry_counts_fails_closed(self):
        for n, source in ((0,1), (1,0), (1,2)):
            b = base_bundle()
            with self.subTest(selected=n, source=source):
                if n != 1 or source != 1:
                    with self.assertRaises(PipelineError):
                        verify_validated_bundle(
                            b, expected_selected_count=n,
                            expected_source_count=source,
                        )

    def test_missing_selected_query_or_decision_is_not_released(self):
        b = base_bundle()
        b["queries"] = []
        with self.assertRaises(PipelineError):
            verify_validated_bundle(b)
        b = base_bundle()
        b["decisions"] = []
        with self.assertRaises(PipelineError):
            verify_validated_bundle(b)
        b = base_bundle()
        b["queries"][0]["candidate_id"] = ALIAS
        with self.assertRaises(PipelineError):
            verify_validated_bundle(b)

    def test_duplicate_rank_and_candidate_ids_rejected(self):
        b = base_bundle()
        b["queries"].append(copy.deepcopy(b["queries"][0]))
        with self.assertRaises(PipelineError):
            verify_validated_bundle(b)
        b = base_bundle()
        b["decisions"].append(copy.deepcopy(b["decisions"][0]))
        with self.assertRaises(PipelineError):
            verify_validated_bundle(b)

    def test_coverage_rejects_false_no_work_or_selected(self):
        b = base_bundle()
        for change in (
            lambda x: x["coverage"][0].__setitem__("selected_count", 0),
            lambda x: x["coverage"][0].__setitem__("source_count", 0),
            lambda x: x["coverage"][0].__setitem__("outcome", "no_source_candidates"),
            lambda x: x["coverage"][0].__setitem__("reason", "fabricated"),
        ):
            corrupt = copy.deepcopy(b)
            change(corrupt)
            with self.assertRaises(PipelineError):
                verify_validated_bundle(corrupt)

    def test_true_zero_work_qv_registry_does_not_fabricate_queries(self):
        b = base_bundle()
        b["queries"].clear()
        b["decisions"].clear()
        b["coverage"][0].update(
            source_count=0, selected_count=0,
            outcome="no_source_candidates", reason="no source candidate",
        )
        self.assertIs(verify_validated_bundle(b, expected_source_count=0,
                                              expected_selected_count=0), b)

    def test_cross_hyp_alias_preserves_source_accounting(self):
        b = base_bundle()
        alias_decision = {
            "candidate_id": ALIAS, "decision": "rejected",
            "reason_code": "exact_duplicate", "duplicate_of": CAND,
            "positive_similarity": 0.9, "negative_similarity": 0.1,
            "score_margin": 0.8, "mmr_score": None,
        }
        b["decisions"].append(alias_decision)
        b["coverage"].append({
            "hypothesis_id": "second", "source_count": 1,
            "selected_count": 0, "outcome": "all_rejected",
            "reason": "Equivalent query selected under another hypothesis",
        })
        self.assertIs(verify_validated_bundle(b, expected_source_count=2,
                                              expected_selected_count=1), b)

    def test_outbox_schema_projected_mode_and_size_are_checked(self):
        row = outbox_row()
        ev = {"outbox_id": row[0], "validation_run_id": row[1],
              "event_type": row[2], "payload_pointer": row[3]}
        verify_outbox_event(ev, expected_run_id=QV)
        for field, val in (
            ("event_type", "untrusted_event"),
            ("validation_run_id", GEN),
            ("outbox_id", "invalid_uuid"),
        ):
            invalid = copy.deepcopy(ev)
            invalid[field] = val
            with self.subTest(field=field), self.assertRaises(PipelineError):
                verify_outbox_event(invalid)
        bad = copy.deepcopy(ev)
        bad["payload_pointer"]["selected_count"] = True
        with self.assertRaises(PipelineError):
            verify_outbox_event(bad)

    def test_qv_read_sql_uses_snapshot_and_generation_scoped_candidate_join(self):
        conn = make_read_conn()
        got = get_validated_bundle(PID, QV, connect=lambda: conn)
        self.assertEqual(len(got["queries"]), 1)
        self.assertEqual(len(got["decisions"]), 1)
        self.assertEqual(got["queries"][0]["candidate_id"], CAND)
        self.assertIn("READ ONLY", conn.queries[0][0])
        self.assertEqual(conn.begin, 1)
        self.assertEqual(conn.queries[1][1], (QV, PID))
        self.assertIn("c.generation_id=d.generation_id", conn.queries[2][0])
        self.assertIn("c.hypothesis_id=d.hypothesis_id", conn.queries[2][0])

    def test_qv_read_rejects_corrupt_authoritative_run_count(self):
        with self.assertRaises(PipelineError):
            get_validated_bundle(PID, QV,
                                 connect=lambda: make_read_conn(selected_count=0))
        with self.assertRaises(PipelineError):
            get_validated_bundle(PID, QV,
                                 connect=lambda: make_read_conn(source_count=0))

    def test_pending_outbox_filters_project_and_validates_pointer(self):
        conn = FakeConnection(lambda sql, args: SimpleResult(many=[outbox_row()]))
        got = pending_validated_outbox(PID, connect=lambda: conn)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["outbox_id"], OID)
        self.assertEqual(conn.queries[0][1], (PID, False, 50))
        self.assertIn("r.project_id=%s", conn.queries[0][0])
        self.assertIn("r.embedding_mode='semantic_local'", conn.queries[0][0])

    def test_pending_outbox_corrupt_saved_run_pointer_rejected(self):
        for kw in (
            {"selected": 2}, {"generation": PUB},
            {"status": "fake_provider_accepted"}, {"mode": "test_fixture"},
        ):
            conn = FakeConnection(
                lambda *_ , kw=kw: SimpleResult(many=[outbox_row(**kw)])
            )
            with self.subTest(kw=kw), self.assertRaises(PipelineError):
                pending_validated_outbox(PID, connect=lambda: conn)

    def test_ack_denied_without_durable_ingestion_proof(self):
        triggered = []
        conn = FakeConnection(lambda *args: triggered.append(args) or SimpleResult())
        with self.assertRaisesRegex(PipelineError, "receipt verification required"):
            ack_validated_outbox(PID, OID, connect=lambda: conn)
        self.assertEqual(triggered, [])

    def test_ack_is_atomic_same_transaction_and_idempotent(self):
        stored = {"delivered": False, "attempts": 0}
        def handler(sql,args):
            if "SELECT o.id,o.delivered_at,o.run_id" in sql:
                return one_result((OID, "old date" if stored["delivered"] else None, QV))
            if "UPDATE qv_outbox" in sql:
                stored["delivered"] = True
                stored["attempts"] += 1
                return SimpleResult()
            raise AssertionError(sql)
        conn = FakeConnection(handler)
        seen = []
        def verify(c,pid,oid,run):
            seen.append((c,pid,oid,run))
            return True
        first = ack_validated_outbox(PID, OID, verified_ingestion=verify,connect=lambda: conn)
        second = ack_validated_outbox(PID, OID, verified_ingestion=verify,connect=lambda: conn)
        self.assertTrue(first["acknowledged"])
        self.assertFalse(first["reused"])
        self.assertTrue(second["reused"])
        self.assertEqual(stored["attempts"], 1)
        self.assertEqual(len(seen),2)
        self.assertTrue(all(item[0] is conn for item in seen))
        self.assertEqual([item[1:] for item in seen], [(PID,OID,QV),(PID,OID,QV)])
        self.assertEqual(conn.begin, 2)

    def test_receipt_rejection_rolls_back_without_ack(self):
        conn = FakeConnection(lambda *args: one_result((OID,None,QV)))
        with self.assertRaisesRegex(PipelineError, "not confirmed"):
            ack_validated_outbox(PID, OID, verified_ingestion=lambda *_: False,
                                 connect=lambda: conn)
        self.assertTrue(conn.rollback)
        self.assertFalse(any("UPDATE qv_outbox" in x[0] for x in conn.queries))

    def test_cached_qv_replay_must_have_durable_event(self):
        missing = FakeConnection(lambda *_: one_result(None))
        with self.assertRaisesRegex(PipelineError, "without durable"):
            _require_committed_qv_outbox(missing, OID, QV, GEN, "semantic_local", 1)
        good = FakeConnection(lambda *_: one_result(outbox_row()[:4][:1] +
                         (outbox_row()[2],outbox_row()[3])))
        _require_committed_qv_outbox(good, OID, QV, GEN, "semantic_local", 1)
        with self.assertRaises(PipelineError):
            _require_committed_qv_outbox(good, OID, QV, GEN, "test_fixture", 1)


if __name__ == "__main__":
    unittest.main()
