"""VC-MOD-03: complete offline trusted-source reader/converter contract QA.

No external PostgreSQL or paid network use: emulate SQL results with immutable
generation and QV/source records; a separate PG17 runner tests actual schema.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import unittest
from unittest.mock import patch

from intelligence.taxonomy_builder.pipeline_contracts import sha256
from intelligence.collector.contracts import CollectorError
from intelligence.collector.qv_reader import (
    read_trusted_qv, convert_trusted_qv, TrustedQVSource,
)

PID = "a" * 32
OTHER = "f" * 32
GID = "11111111-1111-4111-8111-111111111111"
RID = "22222222-2222-4222-8222-222222222222"
OID = "33333333-3333-4333-8333-333333333333"
PUB = "44444444-4444-4444-8444-444444444444"
RTB = "55555555-5555-4555-8555-555555555555"
SOURCE_EVENT = "66666666-6666-4666-8666-666666666666"
CID = "77777777-7777-4777-8777-777777777777"
HYP = "hyp_home"
DIR = "dir_home"
FACT = "fact_fitness"
TAX_HASH = "b" * 64
CONFIG_SHA = "c" * 64
INPUT_SHA = "d" * 64


class _Result:
    def __init__(self, rows):
        self.rows = rows
    def fetchone(self):
        return self.rows[0] if self.rows else None
    def fetchall(self):
        return list(self.rows)


class _FakePg:
    def __init__(self, *, selected=True):
        self.selected = selected
        self.queries = []
        self.pointer = {
            "validation_run_id": RID, "generation_id": GID,
            "schema_version": "validated_queries/v1",
            "embedding_mode": "semantic_local",
            "selected_count": 1 if selected else 0,
        }
        self.source_pointer = {
            "generation_id": GID, "schema_version": "query_candidates/v1",
            "input_sha256": INPUT_SHA,
        }
        self.candidate = (
            CID, HYP, DIR, None, [FACT], "home workout", "keyword",
            "direct", "en", "neutral", "Approved research",
        )
        self.sqg_coverage = (
            HYP, "generated" if selected else "no_viable_queries",
            1 if selected else 0, None if selected else "No source candidates",
        )
        self.ctx = {
            "hypothesis": {"id": HYP}, "direction": {"id": DIR},
            "branch": None, "approved_facts": [{"id": FACT}],
        }
        self.ctx_hash = sha256(self.ctx)
        self.approved = {
            "project_id": PID, "taxonomy_publication_id": PUB,
            "rtb_run_id": RTB, "taxonomy_sha256": TAX_HASH, "taxonomy_version": 1,
            "tree": [
                {"id": DIR, "kind": "direction", "parent_id": None},
                {"id": HYP, "kind": "hypothesis", "parent_id": DIR},
            ],
            "approved_facts": [{"id": FACT}],
        }
        self.source_sha = sha256(self.source_bundle())
        self.acked = False
        self.source_ack = True
        self.embedding_mode = "semantic_local"
        self.gen_status = "completed"
        self.owner = PID
        self.gen_owner = PID
        self.tax_hash = TAX_HASH
        self.policy_sha = CONFIG_SHA
        self.source_candidates = 1 if selected else 0

    def source_bundle(self):
        candidates = []
        if self.selected:
            r = self.candidate
            candidates.append({
                "candidate_id": r[0], "hypothesis_id": r[1],
                "direction_id": r[2], "branch_id": r[3],
                "source_fact_ids": r[4], "text": r[5],
                "query_mode": r[6], "angle": r[7], "language": r[8],
                "research_stance": r[9], "rationale": r[10],
            })
        c = self.sqg_coverage
        return {
            "schema_version": "query_candidates/v1", "generation_id": GID,
            "project_id": PID, "taxonomy_publication_id": PUB,
            "taxonomy_version": 1, "input_sha256": INPUT_SHA,
            "prompt_version": "prompt_v1", "model_id": "test-model",
            "status": "completed",
            "candidates": candidates,
            "hypothesis_coverage": [
                {"hypothesis_id": c[0], "state": c[1],
                 "candidate_count": c[2], "reason": c[3]},
            ],
        }

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        if sql.startswith("SET TRANSACTION"):
            return _Result([])
        if "FROM qv_outbox o" in sql:
            if params != (OID, PID):
                return _Result([])
            return _Result([(
                RID, "validated_queries_ready/v1", self.pointer,
                "delivered" if self.acked else None,
                GID, self.owner, self.embedding_mode, "completed",
                1 if self.selected else 0, self.source_candidates,
                {"policy": True}, self.policy_sha, self.source_sha, "model",
                self.gen_status, self.gen_owner, PUB, 1, self.tax_hash,
                RTB, INPUT_SHA, {"model": {}}, "test-model", SOURCE_EVENT,
                "query_candidates_ready/v1", self.source_pointer,
                "delivered" if self.source_ack else None,
            )])
        if "FROM sqg_candidates c JOIN sqg_batches b" in sql:
            return _Result([self.candidate] if self.selected else [])
        if "FROM sqg_hypothesis_coverage" in sql:
            return _Result([self.sqg_coverage])
        if "FROM sqg_contexts" in sql:
            return _Result([(HYP, self.ctx, self.ctx_hash)])
        if "FROM qv_decisions d" in sql:
            return _Result([(
                CID, HYP, "selected", "selected", None,
                0.91, 0.0, 0.91, 0.75, 1,
            )] if self.selected else [])
        if "FROM qv_coverage" in sql:
            return _Result([(
                HYP, 1 if self.selected else 0, 1 if self.selected else 0,
                "selected" if self.selected else "no_source_candidates",
                None if self.selected else "No source candidates",
            )])
        raise AssertionError("Unexpected SQL " + sql)

    def transaction(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


class TrustedQVReaderTests(unittest.TestCase):
    def load(self, db):
        with patch("intelligence.collector.qv_reader.read_published_snapshot",
                   return_value=db.approved) as approved, patch(
             "intelligence.collector.qv_reader.validate_generation_config",
             return_value={"model": {"prompt_version": "prompt_v1"}}), patch(
             "intelligence.collector.qv_reader.validate_policy",
             side_effect=lambda cfg: cfg), patch(
             "intelligence.collector.qv_reader.policy_fingerprint",
             return_value=CONFIG_SHA):
            result = read_trusted_qv(PID, OID, connect=lambda: db)
            self.assertEqual(approved.call_count, 1)
        return result

    def reject(self, expected, db):
        with self.assertRaises(CollectorError) as caught:
            self.load(db)
        self.assertEqual(caught.exception.code, expected)

    def test_real_source_shape_converts_only_selected_uuid(self):
        db = _FakePg()
        trusted = self.load(db)
        self.assertEqual(trusted.selected_count, 1)
        self.assertEqual(trusted.publication_id, PUB)
        payload = convert_trusted_qv(trusted)
        self.assertEqual(payload["schema_version"], "validated_query_set/v1")
        self.assertEqual(payload["queries"][0]["query_id"], CID)
        self.assertEqual(payload["queries"][0]["validation_state"], "accepted")
        self.assertEqual(payload["taxonomy_sha256"], TAX_HASH)
        self.assertEqual(trusted.selected[0]["source_fact_ids"], [FACT])
        self.assertEqual(trusted.selected[0]["angle"], "direct")
        self.assertEqual(trusted.selected[0]["relevance_score"], 0.91)
        self.assertTrue(db.queries[0][0].startswith("SET TRANSACTION"))
        self.assertTrue(all(
            not sql.lstrip().upper().startswith(("INSERT ", "UPDATE ", "DELETE "))
            for sql, _ in db.queries
        ))

    def test_source_is_effectively_immutable_even_nested(self):
        q = self.load(_FakePg())
        copy = q.selected
        copy[0]["text"] = "attacker"
        self.assertEqual(q.selected[0]["text"], "home workout")
        bad = replace(q, _queries_json=q._queries_json.replace(
            "home workout", "changed workout"))
        with self.assertRaises(CollectorError) as e:
            convert_trusted_qv(bad)
        self.assertEqual(e.exception.code, "qv_source_changed")

    def test_zero_selected_is_not_fake_query_set(self):
        db = _FakePg(selected=False)
        trusted = self.load(db)
        self.assertEqual(trusted.selected, [])
        self.assertEqual(trusted.coverage[0]["outcome"], "no_source_candidates")
        with self.assertRaises(CollectorError) as exc:
            convert_trusted_qv(trusted)
        self.assertEqual(exc.exception.code, "invalid_query_count")

    def test_wrong_project_is_not_authorized(self):
        db = _FakePg()
        with self.assertRaises(CollectorError):
            read_trusted_qv(OTHER, OID, connect=lambda: db)
        db = _FakePg()
        db.owner = OTHER
        self.reject("qv_not_production", db)

    def test_bad_source_embedding_and_incomplete_generation(self):
        for field, value in (("embedding_mode", "test_fixture"),
                             ("gen_status", "running"),
                             ("gen_owner", OTHER)):
            with self.subTest(field=field):
                db = _FakePg()
                setattr(db, field, value)
                self.reject("qv_not_production", db)

    def test_qv_event_and_generation_pointer_must_match(self):
        db = _FakePg()
        db.pointer["generation_id"] = RTB
        self.reject("qv_source_changed", db)
        db = _FakePg()
        db.source_pointer["input_sha256"] = "0" * 64
        self.reject("qv_source_changed", db)
        db = _FakePg()
        db.source_ack = False
        self.reject("qv_source_changed", db)

    def test_unacknowledged_source_and_replay(self):
        db = _FakePg()
        db.acked = True
        self.reject("qv_already_delivered", db)
        with patch("intelligence.collector.qv_reader.read_published_snapshot",
                   return_value=db.approved), patch(
             "intelligence.collector.qv_reader.validate_generation_config",
             return_value={"model": {"prompt_version": "prompt_v1"}}), patch(
             "intelligence.collector.qv_reader.validate_policy",
             side_effect=lambda cfg: cfg), patch(
             "intelligence.collector.qv_reader.policy_fingerprint",
             return_value=CONFIG_SHA):
            trusted = read_trusted_qv(PID, OID, connect=lambda: db,
                                      allow_acknowledged=True)
        self.assertEqual(trusted.outbox_id, OID)

    def test_generation_taxonomy_version_sha_and_parent_pinned(self):
        db = _FakePg()
        db.tax_hash = "0" * 64
        self.reject("qv_taxonomy_lineage", db)
        db = _FakePg()
        db.approved["rtb_run_id"] = OID
        self.reject("qv_taxonomy_lineage", db)
        db = _FakePg()
        db.approved["taxonomy_version"] = 3
        self.reject("qv_taxonomy_lineage", db)

    def test_source_bundle_tampering_detected(self):
        db = _FakePg()
        db.source_sha = "0" * 64
        self.reject("qv_source_changed", db)
        db = _FakePg()
        db.source_candidates = 2
        self.reject("qv_source_changed", db)

    def test_query_context_checksum_and_hierarchy_fail_closed(self):
        db = _FakePg()
        db.ctx_hash = "f" * 64
        self.reject("qv_source_changed", db)
        db = _FakePg()
        db.candidate = (*db.candidate[:2], "wrong-direction",
                        *db.candidate[3:])
        db.source_sha = sha256(db.source_bundle())
        self.reject("qv_taxonomy_lineage", db)

    def test_source_facts_must_match_approved_context(self):
        db = _FakePg()
        raw = list(db.candidate)
        raw[4] = ["unapproved"]
        db.candidate = tuple(raw)
        db.source_sha = sha256(db.source_bundle())
        self.reject("qv_taxonomy_lineage", db)

    def test_config_fingerprint_must_match_current_policy(self):
        db = _FakePg()
        db.policy_sha = "f" * 64
        self.reject("qv_config_changed", db)

    def test_no_client_json_or_untrusted_dataclass_authorized(self):
        self.assertFalse(hasattr(__import__(
            "intelligence.collector.http_api", fromlist=["handle_post"]), "publish_qv"))
        trusted = self.load(_FakePg())
        unauth = replace(trusted, _source_seal=None)
        with self.assertRaises(CollectorError) as e:
            convert_trusted_qv(unauth)
        self.assertEqual(e.exception.code, "untrusted_qv_source")

    def test_not_selected_ids_cannot_be_manually_promoted(self):
        trusted = self.load(_FakePg())
        with self.assertRaises(CollectorError) as e:
            convert_trusted_qv(trusted, candidate_ids=[RTB])
        self.assertEqual(e.exception.code, "query_not_accepted")
        with self.assertRaises(CollectorError):
            convert_trusted_qv(trusted, candidate_ids=[CID, CID])

    def test_invalid_input_rejected_before_database(self):
        db = _FakePg()
        for pid, oid in ((OTHER, "bad"), ("not-a-project", OID), (PID, "bad")):
            with self.subTest(pid=pid, oid=oid), self.assertRaises(CollectorError):
                read_trusted_qv(pid, oid, connect=lambda: db)
        self.assertEqual(db.queries, [])


if __name__ == "__main__":
    unittest.main()
