"""B Stage08: deterministic Python-only normalization and PG17 immutability QA.

Every provider response in this file is a local synthetic fixture. No network.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from uuid import uuid4
import json
import unittest

from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb

from test_ke_stage02_intake_db import ReadOnlyIntakePG, DSN
from test_ke_stage06_fact_contracts import full_fixture, multi_fixture, fact_for
from test_ke_stage06_request_db import Stage06RequestPG
from test_ke_stage07_execution import Stage07FactPG, successful_sender
from intelligence.knowledge_extraction.fact_contracts import (
    MODEL_ID, prepare_request, validate_candidate_response
)
from intelligence.knowledge_extraction.fact_execution import FactExecutionRepository
from intelligence.knowledge_extraction.normalization import (
    FactNormalizationRepository, NORMALIZER_VERSION, normalize_drafts
)
from intelligence.knowledge_extraction.intake import IntakeError
from intelligence.project_sources.context_builder import sha


def offline_inputs(*, facts=None):
    manifest, payload, snap = full_fixture()
    part = prepare_request(manifest, payload, snap)
    if facts is None:
        facts = [fact_for(manifest, payload)]
    draft = validate_candidate_response({"facts": facts}, manifest, payload)
    record = {
        "schema_version": "facts-v3-request-batch/v1",
        "model_id": MODEL_ID, "prompt_version": "facts-v3",
        "parts": [{"request_sha256": part["request_sha256"],
                   "pack_payload_sha256": manifest["payload_sha256"],
                   "pack_manifest_sha256": sha(manifest)}],
    }
    attempt = {
        "pack_index": 0, "status": "completed",
        "request_sha256": part["request_sha256"],
        "charged_usd": Decimal("0.0005"), "usage": {"cost_usd": "0.0005"},
        "draft_candidates": draft, "candidate_count": len(facts),
        "candidate_limit_reached": len(facts) == 80,
    }
    return {"run_id": str(uuid4()), "batch_id": str(uuid4()),
            "blueprint": record, "source_packs": [(manifest,payload)],
            "attempts": [attempt]}


class Stage08PureNormalization(unittest.TestCase):
    def test_exact_source_binding_whitespace_only_and_pending_grounding(self):
        inp = offline_inputs()
        candidate = inp["attempts"][0]["draft_candidates"]["candidates"][0]
        candidate["text"] = "  Функция\tупомянута  в  тексте 10% \n"
        calculated = normalize_drafts(**inp)
        self.assertEqual(calculated["report"]["normalized_count"], 1)
        self.assertEqual(calculated["report"]["quarantined_count"], 0)
        self.assertEqual(calculated["provider_calls"], 0)
        row = calculated["rows"][0]
        self.assertEqual(row["status"], "pending_grounding")
        fact = row["normalized"]
        self.assertEqual(fact["original_text"], candidate["text"])
        self.assertEqual(fact["text"], "Функция упомянута в тексте 10%")
        self.assertEqual(fact["source_quote"], candidate["source_quote"])
        self.assertEqual(fact["source_locator"], candidate["source_locator"])
        self.assertEqual(fact["evidence_ids"], candidate["evidence_ids"])
        self.assertEqual(fact["subject_scope"], candidate["subject_scope"])
        self.assertEqual(fact["source_role"], candidate["source_role"])
        self.assertEqual(fact["grounding_status"], "pending_b5")
        self.assertEqual(fact["human_review_status"], "unreviewed")
        self.assertTrue(fact["model_proposal_only"])
        self.assertNotIn("approved", fact)
        self.assertEqual(calculated, normalize_drafts(**inp))

    def test_quarantine_invalid_schema_unknown_id_quote_and_forged_subject(self):
        actions = [
            (lambda c: c.update({"kind": "invented"}), "b4_facts_v3_schema_invalid"),
            (lambda c: c.update({"source_chunk_id": "f"*64}), "b4_chunk_not_pinned"),
            (lambda c: c.update({"quote_start": 2}), "b4_quote_not_verbatim"),
            (lambda c: c.update({"source_quote": "unsourced material"}), "b4_quote_not_verbatim"),
            (lambda c: c.update({"subject_scope": "own_product"}), "b4_server_provenance_mismatch"),
            (lambda c: c.update({"subject_id": "fake-subject"}), "b4_server_provenance_mismatch"),
            (lambda c: c.update({"evidence_ids": ["forged"]}), "b4_server_provenance_mismatch"),
            (lambda c: c.update({"extra_claim": "injected"}), "b4_server_provenance_mismatch"),
            (lambda c: c.update({"text": " \n "}), "b4_normalized_text_invalid"),
        ]
        for mutate, reason in actions:
            with self.subTest(reason=reason):
                inp = offline_inputs()
                c = inp["attempts"][0]["draft_candidates"]["candidates"][0]
                mutate(c)
                out = normalize_drafts(**inp)
                self.assertEqual(out["report"]["quarantined_count"], 1)
                self.assertEqual(out["report"]["normalized_count"], 0)
                self.assertEqual(out["rows"][0]["reason_code"], reason)
                self.assertIsNone(out["rows"][0]["normalized"])
                self.assertEqual(out["report"]["status"], "normalized_with_quarantine")
                self.assertEqual(out["report"]["grounding_status"], "pending_b5")

    def test_null_or_not_dict_candidate_is_quarantined_not_dropped(self):
        inp = offline_inputs()
        inp["attempts"][0]["draft_candidates"]["candidates"] = [None]
        out = normalize_drafts(**inp)
        self.assertEqual(out["rows"][0]["reason_code"], "b4_candidate_not_object")
        self.assertEqual(out["report"]["proposal_count"], 1)

    def test_no_candidates_no_automatic_inference_and_stable_replay(self):
        inp = offline_inputs(facts=[])
        result = normalize_drafts(**inp)
        self.assertEqual(result["rows"], [])
        self.assertEqual(result["report"]["proposal_count"], 0)
        self.assertEqual(result["report"]["status"], "normalized")
        self.assertFalse(result["report"]["semantic_completeness_proven"])

    def test_duplicate_raw_claims_remain_distinct_until_B6(self):
        inp = offline_inputs()
        draft = inp["attempts"][0]["draft_candidates"]
        copy = {**draft["candidates"][0], "candidate_index": 1}
        draft["candidates"].append(copy)
        draft["candidate_count"] = 2
        inp["attempts"][0]["candidate_count"] = 2
        rows = normalize_drafts(**inp)["rows"]
        self.assertEqual(len(rows), 2)
        self.assertNotEqual(rows[0]["proposal_id"], rows[1]["proposal_id"])
        self.assertEqual(rows[0]["normalized"]["text"], rows[1]["normalized"]["text"])

    def test_hard_incomplete_or_corrupt_attempt_blocks_all_persistence(self):
        variants = [
            lambda x: x["attempts"].clear(),
            lambda x: x["attempts"][0].update({"status":"uncertain"}),
            lambda x: x["attempts"][0].update({"request_sha256":"0"*64}),
            lambda x: x["attempts"][0].update({"candidate_count": 79}),
            lambda x: x["attempts"][0]["draft_candidates"].update({"pack_payload_sha256":"1"*64}),
            lambda x: x["attempts"][0]["draft_candidates"].update({"candidate_limit_reached":True}),
            lambda x: x["attempts"][0].update({"charged_usd":None}),
            lambda x: x["attempts"][0].update({"usage":None}),
        ]
        for mutate in variants:
            inp = offline_inputs()
            mutate(inp)
            with self.assertRaises(IntakeError):
                normalize_drafts(**inp)

    def test_real_multi_pack_drafts_are_all_covered_and_stable(self):
        plan, parts, snap = multi_fixture()
        self.assertGreater(len(parts), 1)
        source_packs, blueprint_parts, attempts = [], [], []
        for i, (manifest, payload) in enumerate(parts):
            req = prepare_request(manifest, payload, snap, multi_plan=plan)
            draft = validate_candidate_response(
                {"facts":[fact_for(manifest,payload)]}, manifest, payload)
            source_packs.append((manifest,payload))
            blueprint_parts.append({
                "request_sha256": req["request_sha256"],
                "pack_payload_sha256": manifest["payload_sha256"],
                "pack_manifest_sha256": sha(manifest),
            })
            attempts.append({
                "pack_index": i, "status": "completed",
                "request_sha256": req["request_sha256"], "charged_usd": Decimal(".0001"),
                "usage":{"prompt_tokens":100}, "draft_candidates": draft,
                "candidate_count":1, "candidate_limit_reached":False,
            })
        result = normalize_drafts(
            run_id=str(uuid4()), batch_id=str(uuid4()),
            blueprint={"schema_version":"facts-v3-request-batch/v1",
                       "model_id":MODEL_ID,"prompt_version":"facts-v3",
                       "parts":blueprint_parts},
            source_packs=source_packs, attempts=attempts)
        self.assertEqual(result["report"]["normalized_count"],len(parts))
        self.assertEqual(result["report"]["quarantined_count"],0)
        self.assertEqual({r["pack_index"] for r in result["rows"]},set(range(len(parts))))
        self.assertEqual(result["report"]["video_mimo_calls"],0)


@unittest.skipUnless(DSN,"PostgreSQL17 required for B4 Stage08 ledger integration")
class Stage08PG(ReadOnlyIntakePG):
    _catalog = Stage06RequestPG._catalog
    _bundle = Stage06RequestPG._bundle
    _factory = Stage06RequestPG._factory
    _ready = Stage07FactPG._ready

    def setUp(self):
        super().setUp()
        self._catalog(window=100000)

    def _completed(self, *, multi=False, sender=successful_sender):
        repo, bid, quote, consent, run = self._ready(multi=multi)
        count = repo.requests.materialize(self.pid,bid)["blueprint"]["request_count"]
        for i in range(count):
            result=repo.advance(self.pid,run["run_id"],sender=sender)
        self.assertEqual(result["status"],"ready_for_normalization")
        return run["run_id"], bid, count

    def _normalizer(self):
        return FactNormalizationRepository(connection_factory=self._factory())

    def test_persist_replay_idempotent_b4_grounding_still_pending(self):
        rid,bid,_ = self._completed()
        repo=self._normalizer()
        first=repo.normalize(self.pid,rid)
        second=repo.normalize(self.pid,rid)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["normalization_id"],second["normalization_id"])
        self.assertEqual(first["normalized_count"],1)
        self.assertEqual(first["quarantined_count"],0)
        replay=repo.materialize(self.pid,rid)
        self.assertEqual(replay["report"],{k:v for k,v in first.items()
            if k not in ("created","normalization_id")})
        self.assertEqual(replay["rows"][0]["normalized"]["grounding_status"],"pending_b5")
        self.assertFalse(replay["report"]["semantic_completeness_proven"])
        with self.psycopg.connect(DSN) as c:
            self.assertEqual(c.execute(
                "SELECT count(*) FROM knowledge_fact_normalized_proposals WHERE project_id=%s AND run_id=%s",
                (self.pid,rid)).fetchone()[0],1)

    def test_multi_pack_no_missing_proposals_and_server_provenance(self):
        rid,bid,count=self._completed(multi=True)
        repo=self._normalizer()
        out=repo.normalize(self.pid,rid)
        self.assertEqual(out["normalized_count"],count)
        replay=repo.materialize(self.pid,rid)
        self.assertEqual(len(replay["rows"]),count)
        self.assertEqual({r["pack_index"] for r in replay["rows"]},set(range(count)))
        self.assertTrue(all(x["normalized"]["model_id"]==MODEL_ID for x in replay["rows"]))

    def test_unfinished_and_uncertain_runs_rejected_without_snapshot(self):
        repo, bid, _, _, run = self._ready()
        normal=self._normalizer()
        with self.assertRaises(IntakeError) as caught:
            normal.normalize(self.pid,run["run_id"])
        self.assertEqual(caught.exception.code,"b4_run_not_complete")
        repo.advance(self.pid,run["run_id"],sender=lambda _:
                     (_ for _ in ()).throw(TimeoutError("perhaps billed")))
        with self.assertRaises(IntakeError):
            normal.normalize(self.pid,run["run_id"])
        with self.psycopg.connect(DSN) as c:
            self.assertEqual(c.execute(
                "SELECT count(*) FROM knowledge_fact_normalization_batches WHERE project_id=%s AND run_id=%s",
                (self.pid,run["run_id"])).fetchone()[0],0)

    def test_malicious_stored_draft_quarantined_without_source_reassignment(self):
        rid,bid,_=self._completed()
        with self.psycopg.connect(DSN) as c:
            draft=c.execute(
                """SELECT draft_candidates FROM knowledge_fact_execution_attempts
                   WHERE project_id=%s AND run_id=%s""",(self.pid,rid)
            ).fetchone()[0]
            draft["candidates"][0]["subject_scope"]="own_product"
            c.execute(
                """UPDATE knowledge_fact_execution_attempts SET draft_candidates=%s
                   WHERE project_id=%s AND run_id=%s""",
                (Jsonb(draft),self.pid,rid),
            )
        normal=self._normalizer()
        out=normal.normalize(self.pid,rid)
        self.assertEqual(out["normalized_count"],0)
        self.assertEqual(out["quarantined_count"],1)
        replay=normal.materialize(self.pid,rid)
        self.assertEqual(replay["rows"][0]["reason_code"],"b4_server_provenance_mismatch")
        self.assertIsNone(replay["rows"][0]["normalized"])

    def test_mutations_of_immutable_b4_snapshot_are_rejected(self):
        rid,bid,_=self._completed()
        normal=self._normalizer()
        first=normal.normalize(self.pid,rid)
        nid=first["normalization_id"]
        for query in (
            "DELETE FROM knowledge_fact_normalization_batches WHERE id=%s",
            "UPDATE knowledge_fact_normalization_batches SET report_sha256=repeat('0',64) WHERE id=%s",
            "DELETE FROM knowledge_fact_normalized_proposals WHERE normalization_id=%s",
            "UPDATE knowledge_fact_normalized_proposals SET status='quarantined' WHERE normalization_id=%s",
        ):
            with self.subTest(sql=query):
                with self.assertRaises(RaiseException):
                    with self.psycopg.connect(DSN) as c:
                        c.execute(query,(nid,))

    def test_source_profile_revision_changed_historical_b4_replay(self):
        rid,bid,_=self._completed()
        normal=self._normalizer()
        saved=normal.normalize(self.pid,rid)
        with self.psycopg.connect(DSN) as c:
            c.execute(
                """INSERT INTO project_profile_versions(
                     project_id,revision,schema_version,profile_hash,profile)
                   VALUES(%s,2,1,%s,%s)""",
                (self.pid,"9"*64,Jsonb({"goal":"new goal"})),
            )
            c.execute(
                """UPDATE project_registry SET profile_revision=2,profile_hash=%s
                   WHERE id=%s""",("9"*64,self.pid),
            )
        replay=normal.materialize(self.pid,rid)
        self.assertEqual(replay["report_sha256"],sha(replay["report"]))
        self.assertEqual(replay["report"]["normalized_count"],saved["normalized_count"])

    def test_replay_detects_tampered_stage07_raw_candidate(self):
        rid,bid,_=self._completed()
        normal=self._normalizer()
        normal.normalize(self.pid,rid)
        with self.psycopg.connect(DSN) as c:
            stored=c.execute(
                "SELECT draft_candidates FROM knowledge_fact_execution_attempts WHERE project_id=%s AND run_id=%s",
                (self.pid,rid)).fetchone()[0]
            stored["candidates"][0]["text"]="changed after normalization"
            c.execute(
                "UPDATE knowledge_fact_execution_attempts SET draft_candidates=%s WHERE project_id=%s AND run_id=%s",
                (Jsonb(stored),self.pid,rid),
            )
        with self.assertRaises(IntakeError) as caught:
            normal.materialize(self.pid,rid)
        self.assertEqual(caught.exception.code,"b4_immutable_replay_mismatch")

    def test_wrong_project_cannot_read_or_normalize(self):
        rid,bid,_=self._completed()
        normal=self._normalizer()
        normal.normalize(self.pid,rid)
        with self.assertRaises(IntakeError):
            normal.materialize(self.other,rid)
        with self.assertRaises(IntakeError):
            normal.normalize(self.other,rid)


if __name__=="__main__":
    unittest.main()
