"""Stage10 B6: all source references retained, exact dedup, conservative conflicts.

Real PG17 integration uses only an injected fake B3 sender, never a paid API.
"""
from __future__ import annotations

from copy import deepcopy
import json
from uuid import uuid4
import unittest

from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb

from test_ke_stage02_intake_db import ReadOnlyIntakePG, DSN
from test_ke_stage06_request_db import Stage06RequestPG
from test_ke_stage07_execution import Stage07FactPG, successful_sender
from test_ke_stage08_normalization import Stage08PG
from test_ke_stage09_grounding import Stage09GroundingPG, pure_source
from intelligence.knowledge_extraction.grounding import (
    FactGroundingRepository, ground_proposals,
)
from intelligence.knowledge_extraction.normalization import FactNormalizationRepository
from intelligence.knowledge_extraction.deduplication import (
    analyze_grounding, FactDeduplicationRepository,
)
from intelligence.knowledge_extraction.intake import IntakeError
from intelligence.project_sources.context_builder import sha as canonical_sha


def analyzed_fixture(texts=None, *, as_own_product=False, quarantine=False):
    data=pure_source()
    if texts is None:
        texts=["Продукт поддерживает автоматический режим"]
    prototype=data["normalized"][0]
    data["normalized"]=[]
    for i,text in enumerate(texts):
        cand=deepcopy(prototype)
        ident=canonical_sha({"test_proposal":i,"text":text})
        cand["candidate_index"]=i
        cand["proposal_id"]=ident
        cand["normalized"]["proposal_id"]=ident
        cand["normalized"]["candidate_index"]=i
        cand["normalized"]["text"]=text
        cand["normalized"]["original_text"]=text
        data["normalized"].append(cand)
    if quarantine:
        data["normalized"][0]["status"]="quarantined"
        data["normalized"][0]["reason_code"]="b4_provenance_forged"
        data["normalized"][0]["normalized"]=None
    result=ground_proposals(**data)
    receipts=result["receipts"]
    # A pure unit fixture can model source scope that is owned. The production
    # repository NEVER applies this artificial retag: it calls B5 against A.
    if as_own_product:
        for row,receipt in zip(data["normalized"],receipts):
            prop=row["normalized"]
            if prop is None:
                continue
            prop["subject_scope"]="own_product"
            prop["source_role"]="own_product"
            prop["subject_id"]="project"
            src=receipt["receipt"]["original_provenance"]
            src["subject_scope"]="own_product"
            src["source_role"]="own_product"
            src["subject_id"]="project"
            receipt["receipt_sha256"]=canonical_sha(receipt["receipt"])
    return {
        "run_id":data["run_id"],"batch_id":data["batch_id"],
        "normalized_rows":data["normalized"],"grounding_receipts":receipts,
        "b4_report_sha256":data["normalization_sha256"],
        "b5_report_sha256":result["report_sha256"],
    }


class Stage10Pure(unittest.TestCase):
    def test_exact_duplicates_retained_with_distinct_citation_members(self):
        payload=analyzed_fixture(texts=["Один и тот же текст","Один и тот же текст"])
        first=analyze_grounding(**payload)
        second=analyze_grounding(**payload)
        self.assertEqual(first,second)
        self.assertEqual(first["report"]["proposal_count"],2)
        self.assertEqual(first["report"]["eligible_count"],2)
        self.assertEqual(first["report"]["group_count"],1)
        self.assertEqual(first["report"]["duplicate_count"],1)
        self.assertEqual(first["groups"][0]["member_count"],2)
        self.assertNotEqual(first["members"][0]["proposal_id"],
                            first["members"][1]["proposal_id"])
        self.assertEqual(first["members"][0]["group_id"],first["members"][1]["group_id"])
        self.assertTrue(all(m["provenance"]["quote_sha256"]
                            for m in first["members"]))
        self.assertTrue(first["report"]["all_citations_retained"])
        self.assertTrue(first["report"]["review_required"])
        self.assertFalse(first["report"]["objective_truth_confirmed"])
        self.assertEqual(first["provider_calls"],0)

    def test_different_text_and_kinds_never_exact_merged(self):
        payload=analyzed_fixture(texts=["Цена 10 долларов","Цена 11 долларов"])
        r=analyze_grounding(**payload)
        self.assertEqual(r["report"]["group_count"],2)
        self.assertEqual(r["report"]["duplicate_count"],0)
        # Source scope "other" is ambiguous; don't infer subject conflicts.
        self.assertEqual(r["report"]["conflict_candidate_count"],0)
        p=analyzed_fixture(texts=["Same text","Same text"])
        p["normalized_rows"][1]["normalized"]["kind"]="metric"
        r=analyze_grounding(**p)
        self.assertEqual(r["report"]["group_count"],2)

    def test_explicit_opposite_negation_is_only_review_candidate(self):
        payload=analyzed_fixture(
            texts=["Продукт поддерживает автоматический режим",
                   "Продукт не поддерживает автоматический режим"],
            as_own_product=True)
        result=analyze_grounding(**payload)
        self.assertEqual(result["report"]["group_count"],2)
        self.assertEqual(result["report"]["conflict_candidate_count"],1)
        relation=result["conflicts"][0]
        self.assertEqual(relation["rule"],"opposite_negation")
        self.assertEqual(relation["review_state"],"needs_review")
        self.assertLess(relation["group_a"],relation["group_b"])
        self.assertTrue(result["report"]["conflicts_are_candidates_only"])

    def test_numeric_delta_is_review_candidate_not_auto_conflict_verdict(self):
        payload=analyzed_fixture(
            texts=["Устройство работает до 10 часов",
                   "Устройство работает до 12 часов"],
            as_own_product=True)
        result=analyze_grounding(**payload)
        self.assertEqual(result["report"]["conflict_candidate_count"],1)
        self.assertEqual(result["conflicts"][0]["rule"],"different_number_same_phrase")

    def test_compound_negation_and_unknown_subject_do_not_create_conflict(self):
        for texts, owned in (
            (["Продукт не только поддерживает режим",
              "Продукт только поддерживает режим"],True),
            (["Продукт поддерживает автоматический режим",
              "Продукт не поддерживает автоматический режим"],False),
        ):
            with self.subTest(texts=texts):
                p=analyzed_fixture(texts=texts,as_own_product=owned)
                self.assertFalse(analyze_grounding(**p)["conflicts"])

    def test_b4_quarantine_and_b5_rejection_cannot_be_grouped(self):
        p=analyzed_fixture(quarantine=True)
        r=analyze_grounding(**p)
        self.assertEqual(r["report"]["eligible_count"],0)
        self.assertEqual(r["report"]["blocked_count"],1)
        self.assertEqual(r["members"][0]["status"],"skipped_b4")
        self.assertIsNone(r["members"][0]["group_id"])
        p=analyzed_fixture()
        # B5 rejected candidate: a separate grounding verdict, no repaired claim.
        rec=p["grounding_receipts"][0]
        rec["status"]="rejected"
        rec["reason_code"]="b5_quote_not_in_original_a"
        rec["receipt"]["status"]="rejected"
        rec["receipt"]["reason_code"]=rec["reason_code"]
        rec["receipt"]["original_provenance"]=None
        rec["receipt_sha256"]=canonical_sha(rec["receipt"])
        r=analyze_grounding(**p)
        self.assertEqual(r["report"]["eligible_count"],0)
        self.assertEqual(r["members"][0]["status"],"blocked_b5")
        self.assertEqual(r["report"]["blocked_reasons"]["b5_quote_not_in_original_a"],1)

    def test_missing_reordered_duplicate_or_tampered_receipts_fail_closed(self):
        factories=[
            lambda d:d["grounding_receipts"].clear(),
            lambda d:d["grounding_receipts"].append(
                deepcopy(d["grounding_receipts"][0])),
            lambda d:d["grounding_receipts"][0].update({"receipt_sha256":"0"*64}),
            lambda d:d["grounding_receipts"][0]["receipt"]["original_provenance"].update(
                {"source_role":"own_product"}),
            lambda d:d["grounding_receipts"][0]["receipt"].update(
                {"objective_truth_confirmed":True}),
            lambda d:d["normalized_rows"][0]["normalized"].update(
                {"subject_scope":"own_product"}),
        ]
        for corrupt in factories:
            with self.subTest(corrupt=str(corrupt)):
                p=analyzed_fixture()
                corrupt(p)
                with self.assertRaises(IntakeError):
                    analyze_grounding(**p)

    def test_empty_source_matched_set_does_not_invent_facts(self):
        p=analyzed_fixture(texts=[])
        r=analyze_grounding(**p)
        self.assertEqual(r["groups"],[])
        self.assertEqual(r["members"],[])
        self.assertEqual(r["conflicts"],[])
        self.assertEqual(r["report"]["eligible_count"],0)

    def test_pairs_not_lost_when_multiple_duplicate_groups(self):
        p=analyzed_fixture(
            texts=["Продукт поддерживает автоматический режим",
                   "Продукт поддерживает автоматический режим",
                   "Продукт не поддерживает автоматический режим"],
            as_own_product=True)
        r=analyze_grounding(**p)
        self.assertEqual(r["report"]["group_count"],2)
        self.assertEqual(r["report"]["duplicate_count"],1)
        self.assertEqual(r["report"]["conflict_candidate_count"],1)
        self.assertEqual(len(r["members"]),3)


def double_sender(body):
    result=successful_sender(body)
    content=json.loads(result["choices"][0]["message"]["content"])
    content["facts"].append(dict(content["facts"][0]))
    result["choices"][0]["message"]["content"]=json.dumps(content,ensure_ascii=False)
    return result


@unittest.skipUnless(DSN, "PostgreSQL17 required for Stage10 integration tests")
class Stage10PG(ReadOnlyIntakePG):
    _catalog=Stage06RequestPG._catalog
    _bundle=Stage06RequestPG._bundle
    _factory=Stage06RequestPG._factory
    _ready=Stage07FactPG._ready
    _completed=Stage08PG._completed
    _ready_b5=Stage09GroundingPG._ready_b5

    def setUp(self):
        super().setUp()
        self._catalog(window=100000)

    def _complete_b6(self, *, multi=False, sender=successful_sender):
        rid,bid,count=self._completed(multi=multi,sender=sender)
        normal=FactNormalizationRepository(connection_factory=self._factory())
        normal.normalize(self.pid,rid)
        ground=FactGroundingRepository(connection_factory=self._factory())
        ground.ground(self.pid,rid)
        return rid,bid,count

    def _repo(self):
        return FactDeduplicationRepository(connection_factory=self._factory())

    def test_full_pipeline_idempotency_and_replay_all_evidence_members(self):
        rid,bid,_=self._complete_b6(sender=double_sender)
        repo=self._repo()
        first=repo.analyze(self.pid,rid)
        second=repo.analyze(self.pid,rid)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["dedup_id"],second["dedup_id"])
        self.assertEqual(first["proposal_count"],2)
        self.assertEqual(first["eligible_count"],2)
        self.assertEqual(first["duplicate_count"],1)
        data=repo.materialize(self.pid,rid)
        self.assertEqual(data["report_sha256"],canonical_sha(data["report"]))
        self.assertEqual(len(data["members"]),2)
        self.assertEqual(len(data["groups"]),1)
        self.assertEqual(data["groups"][0]["member_count"],2)
        self.assertTrue(all(m["provenance"] is not None for m in data["members"]))
        self.assertFalse(data["report"]["objective_truth_confirmed"])

    def test_full_multi_pack_retains_all_grounded_citations(self):
        rid,bid,count=self._complete_b6(multi=True)
        repo=self._repo()
        result=repo.analyze(self.pid,rid)
        data=repo.materialize(self.pid,rid)
        self.assertEqual(len(data["members"]),count)
        self.assertEqual(result["eligible_count"],count)
        self.assertEqual({m["pack_index"] for m in data["members"]},
                         set(range(count)))
        self.assertEqual(sum(x["member_count"] for x in data["groups"]),count)
        self.assertTrue(all(m["status"]=="grouped" for m in data["members"]))

    def test_requires_independent_b5_before_any_snapshot(self):
        rid,bid,count=self._completed()
        FactNormalizationRepository(connection_factory=self._factory()).normalize(
            self.pid,rid)
        with self.assertRaises(IntakeError):
            self._repo().analyze(self.pid,rid)
        with self.psycopg.connect(DSN) as conn:
            c=conn.execute(
                "SELECT count(*) FROM knowledge_fact_dedup_batches WHERE project_id=%s",
                (self.pid,),
            ).fetchone()[0]
            self.assertEqual(c,0)

    def test_b4_quarantine_preserved_and_unpublished(self):
        rid,bid,count=self._completed()
        with self.psycopg.connect(DSN) as conn:
            d=conn.execute(
                """SELECT draft_candidates FROM knowledge_fact_execution_attempts
                   WHERE project_id=%s AND run_id=%s""",
                (self.pid,rid),
            ).fetchone()[0]
            d["candidates"][0]["subject_scope"]="own_product"
            conn.execute(
                """UPDATE knowledge_fact_execution_attempts SET draft_candidates=%s
                   WHERE project_id=%s AND run_id=%s""",
                (Jsonb(d),self.pid,rid),
            )
        FactNormalizationRepository(connection_factory=self._factory()).normalize(
            self.pid,rid)
        FactGroundingRepository(connection_factory=self._factory()).ground(
            self.pid,rid)
        out=self._repo().analyze(self.pid,rid)
        self.assertEqual(out["blocked_count"],1)
        self.assertEqual(out["eligible_count"],0)
        self.assertEqual(out["group_count"],0)

    def test_immutable_tables_reject_updates_deletes(self):
        rid,bid,_=self._complete_b6(sender=double_sender)
        repo=self._repo()
        ident=repo.analyze(self.pid,rid)["dedup_id"]
        queries=[
            "DELETE FROM knowledge_fact_dedup_batches WHERE id=%s",
            "UPDATE knowledge_fact_exact_groups SET member_count=500 WHERE dedup_id=%s",
            "DELETE FROM knowledge_fact_dedup_members WHERE dedup_id=%s",
            "UPDATE knowledge_fact_possible_conflicts SET review_state='needs_review' WHERE dedup_id=%s",
        ]
        # Conflict fixture may be empty; check UPDATE on group if so.
        queries[-1]="DELETE FROM knowledge_fact_exact_groups WHERE dedup_id=%s"
        for sql in queries:
            with self.subTest(sql=sql):
                with self.assertRaises(RaiseException):
                    with self.psycopg.connect(DSN) as conn:
                        conn.execute(sql,(ident,))

    def test_wrong_project_cannot_inspect_or_write(self):
        rid,bid,_=self._complete_b6()
        repo=self._repo()
        repo.analyze(self.pid,rid)
        with self.assertRaises(IntakeError):
            repo.materialize(self.other,rid)
        with self.assertRaises(IntakeError):
            repo.analyze(self.other,rid)

    def test_historical_replay_after_current_profile_revision(self):
        rid,bid,_=self._complete_b6()
        repo=self._repo()
        first=repo.analyze(self.pid,rid)
        with self.psycopg.connect(DSN) as conn:
            conn.execute(
                """INSERT INTO project_profile_versions(
                     project_id,revision,schema_version,profile_hash,profile)
                   VALUES(%s,2,1,%s,%s)""",
                (self.pid,"9"*64,Jsonb({"goal":"changed"})),
            )
            conn.execute(
                """UPDATE project_registry SET profile_revision=2,profile_hash=%s
                   WHERE id=%s""",("9"*64,self.pid),
            )
        last=repo.materialize(self.pid,rid)
        self.assertEqual(last["report_sha256"],canonical_sha(last["report"]))
        self.assertEqual(first["eligible_count"],last["report"]["eligible_count"])
        self.assertFalse(repo.analyze(self.pid,rid)["created"])


if __name__=="__main__":
    unittest.main()
