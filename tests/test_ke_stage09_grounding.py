"""B Stage09 independent source grounding; zero real model/provider calls."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from uuid import uuid4
import json
import unittest

from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb

from test_ke_stage02_intake_db import ReadOnlyIntakePG, DSN
from test_ke_stage06_request_db import Stage06RequestPG
from test_ke_stage07_execution import Stage07FactPG, successful_sender
from test_ke_stage08_normalization import Stage08PG, offline_inputs
from intelligence.knowledge_extraction.normalization import (
    FactNormalizationRepository, normalize_drafts,
)
from intelligence.knowledge_extraction.grounding import (
    FactGroundingRepository, ground_proposals,
)
from intelligence.knowledge_extraction.intake import IntakeError
from intelligence.project_sources.context_builder import sha as canonical_sha


def pure_source():
    """Fixture A-original is separately constructed from verified B2 fields."""
    inp = offline_inputs()
    normalized = normalize_drafts(**inp)
    _, payload = inp["source_packs"][0]
    lines = [json.loads(s) for s in payload.decode("utf-8").splitlines()][1:]
    originals = {x["fragment_id"]:x["text"] for x in lines}
    fragments = [{
        "fragment_id": x["fragment_id"],
        "origin_kind": x["origin_kind"],
        "source_id": x["source_id"],
        "source_version_id": x["source_version_id"],
        "output_id": x["output_id"],
        "field_path": x["field_path"],
        "segment_index": x["segment_index"],
        "locator": x["locator"],
        "source_role": x["source_role"],
        "subject_scope": x["subject_scope"],
        "subject_id": x["subject_id"],
        "trust_label": x["trust_label"],
        "evidence_ids": x["evidence_ids"],
        "text_sha256": sha256(x["text"].encode()).hexdigest(),
        "char_count": len(x["text"]),
        "output_sha256": "b"*64 if x["output_id"] is not None else None,
        "profile_revision":1, "profile_hash":"c"*64,
    } for x in lines]
    versions = {(f["source_id"],f["source_version_id"]):"d"*64
                for f in fragments if f["source_version_id"] is not None}
    return {
        "run_id": inp["run_id"],"batch_id":inp["batch_id"],
        "normalized":normalized["rows"],"source_packs":inp["source_packs"],
        "fragments":fragments,"original_texts":originals,
        "context_sha256":"a"*64,
        "source_sha_by_version":versions,
        "normalization_sha256":normalized["report_sha256"],
    }


class Stage09PureGrounding(unittest.TestCase):
    def test_exact_unicode_quote_from_original_a_and_hash_receipt(self):
        data=pure_source()
        a=ground_proposals(**data)
        b=ground_proposals(**data)
        self.assertEqual(a,b)
        self.assertEqual(a["report"]["total_count"],1)
        self.assertEqual(a["report"]["matched_count"],1)
        r=a["receipts"][0]
        self.assertEqual(r["status"],"source_quote_matched")
        self.assertEqual(r["receipt_sha256"],canonical_sha(r["receipt"]))
        provenance=r["receipt"]["original_provenance"]
        self.assertEqual(provenance["quote_sha256"],sha256(
            data["normalized"][0]["normalized"]["source_quote"].encode()).hexdigest())
        self.assertEqual(provenance["source_role"],data["normalized"][0]["normalized"]["source_role"])
        self.assertTrue(provenance["source_text_match_only"])
        self.assertFalse(provenance["website_independently_retrieved"])
        self.assertFalse(r["receipt"]["semantic_entailment_checked"])
        self.assertFalse(r["receipt"]["objective_truth_confirmed"])
        self.assertEqual(a["provider_calls"],0)

    def test_changed_original_a_text_is_a_hard_integrity_failure(self):
        data=pure_source()
        first=next(iter(data["original_texts"]))
        data["original_texts"][first]+=" changed"
        with self.assertRaises(IntakeError) as caught:
            ground_proposals(**data)
        self.assertEqual(caught.exception.code,"b5_a_text_differs_from_pack")

    def test_forged_lineage_in_pack_fails_before_grounding_any_claim(self):
        data=pure_source()
        data["fragments"][0]["source_role"]="own_product"
        with self.assertRaises(IntakeError) as caught:
            ground_proposals(**data)
        self.assertEqual(caught.exception.code,"b5_source_lineage_mismatch")

    def test_substring_mismatch_quarantined_not_promoted(self):
        data=pure_source()
        prop=data["normalized"][0]["normalized"]
        prop["source_quote"]="invented quote"
        result=ground_proposals(**data)
        self.assertEqual(result["report"]["matched_count"],0)
        self.assertEqual(result["report"]["rejected_count"],1)
        self.assertEqual(result["receipts"][0]["reason_code"],"b5_quote_not_in_original_a")
        self.assertIsNone(result["receipts"][0]["receipt"]["original_provenance"])

    def test_b4_quarantined_candidate_is_covered_without_promotion(self):
        data=pure_source()
        data["normalized"][0]["status"]="quarantined"
        data["normalized"][0]["reason_code"]="b4_facts_v3_schema_invalid"
        data["normalized"][0]["normalized"]=None
        result=ground_proposals(**data)
        self.assertEqual(result["report"]["skipped_count"],1)
        self.assertEqual(result["report"]["matched_count"],0)
        self.assertEqual(result["receipts"][0]["status"],"skipped_b4_quarantine")

    def test_wrong_source_chunk_and_subject_never_silently_repaired(self):
        for changes,reason in (
            ({"source_chunk_id":"z"*64},"b5_source_chunk_unknown"),
            ({"subject_scope":"own_product"},"b5_provenance_mismatch"),
            ({"source_version_id":str(uuid4())},"b5_provenance_mismatch"),
        ):
            data=pure_source()
            data["normalized"][0]["normalized"].update(changes)
            out=ground_proposals(**data)
            self.assertEqual(out["receipts"][0]["reason_code"],reason)

    def test_empty_proposals_do_not_invent_facts(self):
        data=pure_source()
        data["normalized"]=[]
        out=ground_proposals(**data)
        self.assertEqual(out["report"]["total_count"],0)
        self.assertEqual(out["receipts"],[])
        self.assertFalse(out["report"]["semantic_entailment_checked"])

    def test_duplicate_claims_remain_independent_until_stage10(self):
        data=pure_source()
        duplicate=deepcopy(data["normalized"][0])
        duplicate["candidate_index"]=1
        duplicate["proposal_id"]="e"*64
        duplicate["normalized"]["proposal_id"]=duplicate["proposal_id"]
        data["normalized"].append(duplicate)
        out=ground_proposals(**data)
        self.assertEqual(out["report"]["matched_count"],2)
        self.assertNotEqual(out["receipts"][0]["proposal_id"],out["receipts"][1]["proposal_id"])


@unittest.skipUnless(DSN,"PostgreSQL17 required for B5 original A source integration")
class Stage09GroundingPG(ReadOnlyIntakePG):
    _catalog=Stage06RequestPG._catalog
    _bundle=Stage06RequestPG._bundle
    _factory=Stage06RequestPG._factory
    _ready=Stage07FactPG._ready
    _completed=Stage08PG._completed

    def setUp(self):
        super().setUp()
        self._catalog(window=100000)

    def _grounder(self):
        return FactGroundingRepository(connection_factory=self._factory())

    def _ready_b5(self, *, multi=False):
        rid,bid,count=self._completed(multi=multi)
        normalized=FactNormalizationRepository(connection_factory=self._factory())
        saved=normalized.normalize(self.pid,rid)
        return rid,bid,count,saved

    def test_ground_full_pack_and_idempotent_historical_replay(self):
        rid,bid,count,saved=self._ready_b5()
        repo=self._grounder()
        first=repo.ground(self.pid,rid)
        second=repo.ground(self.pid,rid)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["grounding_id"],second["grounding_id"])
        self.assertEqual(first["matched_count"],1)
        self.assertEqual(first["rejected_count"],0)
        checked=repo.materialize(self.pid,rid)
        self.assertEqual(first["input_sha256"],checked["input_sha256"])
        self.assertEqual(checked["report_sha256"],canonical_sha(checked["report"]))
        source=checked["receipts"][0]["receipt"]["original_provenance"]
        self.assertIsNotNone(source["output_id"])
        self.assertEqual(source["original_text_verified_from"],"pinned_a_processing_output")
        self.assertEqual(source["source_role"],checked["receipts"][0]["receipt"]["original_provenance"]["source_role"])
        self.assertFalse(checked["receipts"][0]["receipt"]["objective_truth_confirmed"])

    def test_true_multi_pack_full_provenance_no_omitted_chunks(self):
        rid,bid,count,saved=self._ready_b5(multi=True)
        repo=self._grounder()
        first=repo.ground(self.pid,rid)
        self.assertEqual(first["matched_count"],count)
        inspected=repo.materialize(self.pid,rid)
        self.assertEqual(len(inspected["receipts"]),count)
        self.assertEqual({r["pack_index"] for r in inspected["receipts"]},set(range(count)))
        self.assertTrue(all(r["status"]=="source_quote_matched" for r in inspected["receipts"]))

    def test_grounding_requires_completed_b4_snapshot(self):
        rid,bid,count=self._completed()
        with self.assertRaises(IntakeError) as exc:
            self._grounder().ground(self.pid,rid)
        self.assertEqual(exc.exception.code,"b4_normalization_not_found")

    def test_foreign_project_cannot_read_write_grounding(self):
        rid,bid,count,saved=self._ready_b5()
        repo=self._grounder()
        repo.ground(self.pid,rid)
        with self.assertRaises(IntakeError):
            repo.materialize(self.other,rid)
        with self.assertRaises(IntakeError):
            repo.ground(self.other,rid)

    def test_immutable_receipts_and_batches_cannot_be_updated_or_deleted(self):
        rid,bid,count,saved=self._ready_b5()
        grounding=self._grounder().ground(self.pid,rid)["grounding_id"]
        for sql in (
            "DELETE FROM knowledge_fact_grounding_batches WHERE id=%s",
            "UPDATE knowledge_fact_grounding_batches SET report_sha256=repeat('0',64) WHERE id=%s",
            "DELETE FROM knowledge_fact_grounding_receipts WHERE grounding_id=%s",
            "UPDATE knowledge_fact_grounding_receipts SET status='rejected' WHERE grounding_id=%s",
        ):
            with self.subTest(sql=sql):
                with self.assertRaises(RaiseException):
                    with self.psycopg.connect(DSN) as c:
                        c.execute(sql,(grounding,))

    def test_original_a_output_tamper_blocks_replay(self):
        rid,bid,count,saved=self._ready_b5()
        repo=self._grounder()
        repo.ground(self.pid,rid)
        with self.psycopg.connect(DSN) as conn:
            output=conn.execute(
                """SELECT output_id FROM knowledge_input_fragments
                   WHERE project_id=%s AND bundle_id=(
                     SELECT bundle_id FROM knowledge_fact_execution_runs
                      WHERE project_id=%s AND id=%s)
                     AND output_id IS NOT NULL LIMIT 1""",
                (self.pid,self.pid,rid),
            ).fetchone()[0]
            record=conn.execute(
                "SELECT output FROM source_processing_outputs WHERE project_id=%s AND id=%s",
                (self.pid,output),
            ).fetchone()[0]
            record["status"]="tampered_without_hash"
            conn.execute(
                "UPDATE source_processing_outputs SET output=%s WHERE project_id=%s AND id=%s",
                (Jsonb(record),self.pid,output),
            )
        with self.assertRaises(IntakeError):
            repo.materialize(self.pid,rid)

    def test_historical_context_replay_after_new_profile_revision(self):
        rid,bid,count,saved=self._ready_b5()
        repo=self._grounder()
        first=repo.ground(self.pid,rid)
        with self.psycopg.connect(DSN) as c:
            c.execute(
                """INSERT INTO project_profile_versions(
                    project_id,revision,schema_version,profile_hash,profile)
                   VALUES(%s,2,1,%s,%s)""",
                (self.pid,"9"*64,Jsonb({"goal":"changed"})),
            )
            c.execute(
                "UPDATE project_registry SET profile_revision=2,profile_hash=%s WHERE id=%s",
                ("9"*64,self.pid),
            )
        self.assertEqual(repo.materialize(self.pid,rid)["report_sha256"],
                         canonical_sha(repo.materialize(self.pid,rid)["report"]))
        self.assertFalse(repo.ground(self.pid,rid)["created"])


if __name__=="__main__":
    unittest.main()
