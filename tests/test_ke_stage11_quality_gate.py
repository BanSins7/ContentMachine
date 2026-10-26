"""B Stage11: explainable Quality Gate, pure invariants and PostgreSQL17 QA.

All B3 calls use synthetic local sender. No real MiMo/OpenRouter traffic.
"""
from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
from uuid import uuid4
import unittest

from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb

from test_ke_stage02_intake_db import ReadOnlyIntakePG, DSN
from test_ke_stage06_request_db import Stage06RequestPG
from test_ke_stage07_execution import Stage07FactPG
from test_ke_stage08_normalization import Stage08PG
from test_ke_stage09_grounding import pure_source
from test_ke_stage10_deduplication import Stage10PG, double_sender
from intelligence.knowledge_extraction.deduplication import (
    FactDeduplicationRepository, analyze_grounding,
)
from intelligence.knowledge_extraction.grounding import ground_proposals
from intelligence.knowledge_extraction.quality_gate import (
    evaluate_quality,FactQualityGateRepository,
)
from intelligence.knowledge_extraction.intake import IntakeError
from intelligence.project_sources.context_builder import sha


def pure_fixture(*, texts=None, quarantine=False, b5_reject=False):
    data=pure_source()
    if texts is None:
        texts=["Проверяемый текст из документа"]
    original=deepcopy(data["normalized"][0])
    data["normalized"]=[]
    for idx,txt in enumerate(texts):
        proposal=deepcopy(original)
        pid=sha({"test_index":idx,"text":txt})
        proposal["pack_index"]=0
        proposal["candidate_index"]=idx
        proposal["proposal_id"]=pid
        proposal["normalized"]["proposal_id"]=pid
        proposal["normalized"]["candidate_index"]=idx
        proposal["normalized"]["text"]=txt
        proposal["normalized"]["original_text"]=txt
        data["normalized"].append(proposal)
    if quarantine and data["normalized"]:
        proposal=data["normalized"][0]
        proposal["status"]="quarantined"
        proposal["reason_code"]="b4_server_provenance_mismatch"
        proposal["normalized"]=None
    nr={
        "schema_version":"knowledge-normalization/v1",
        "run_id":data["run_id"],"batch_id":data["batch_id"],
        "proposal_count":len(texts),
        "normalized_count":len(texts)-(1 if quarantine and texts else 0),
        "quarantined_count":int(bool(quarantine and texts)),
        "packs":[{
            "pack_index":0,"candidate_count":len(texts),
            "pending_grounding_count":len(texts)-(1 if quarantine and texts else 0),
            "quarantined_count":int(bool(quarantine and texts)),
            "candidate_limit_reached":len(texts)==80,
        }],
    }
    n_sha=sha(nr)
    data["normalization_sha256"]=n_sha
    grounded=ground_proposals(**data)
    if b5_reject and texts:
        r=grounded["receipts"][-1]
        r["status"]="rejected"
        r["reason_code"]="b5_quote_not_in_original_a"
        r["receipt"]["status"]="rejected"
        r["receipt"]["reason_code"]=r["reason_code"]
        r["receipt"]["original_provenance"]=None
        r["receipt_sha256"]=sha(r["receipt"])
        gr=grounded["report"]
        gr["matched_count"]-=1
        gr["rejected_count"]+=1
        gr["reason_counts"]={"b5_quote_not_in_original_a":1}
        grounded["report_sha256"]=sha(gr)
    b6=analyze_grounding(
        run_id=data["run_id"],batch_id=data["batch_id"],
        normalized_rows=data["normalized"],
        grounding_receipts=grounded["receipts"],
        b4_report_sha256=n_sha,b5_report_sha256=grounded["report_sha256"])
    manifest,payload=data["source_packs"][0]
    bundle={
        "schema_version":"knowledge-input-bundle/v1",
        "project_id":"project-fixture",
        "readiness":"ready",
        "text_coverage_complete":True,
        "fragments":data["fragments"],
        "excluded_sources":[],"uncovered_required":[],
        "video_analysis_refs":[],
        "coverage":{
            "fragments_indexed":len(data["fragments"]),
            "uncovered_required":0,
        },
    }
    manifest["bundle_sha256"]=sha(bundle)
    blueprint={
        "schema_version":"facts-v3-request-batch/v1",
        "bundle_id":str(uuid4()),
        "pack_kind":"full","request_count":1,
        "parts":[{
            "pack_index":0,"request_sha256":"a"*64,
            "pack_payload_sha256":manifest["payload_sha256"],
            "pack_manifest_sha256":sha(manifest),
            "source_chunk_count":len(manifest["entries"]),
        }],
    }
    execution={
        "run_id":data["run_id"],"batch_id":data["batch_id"],
        "status":"ready_for_normalization",
        "next_pack_index":1,
        "authorized_usd":"2.00",
        "spent_usd":"0.0005","reserved_usd":"0",
        "approved":False,
        "attempts":[{
            "pack_index":0,"status":"completed",
            "candidate_count":len(texts),
            "candidate_limit_reached":len(texts)==80,
            "charged_usd":"0.0005",
            "usage":{"cost_usd":"0.0005","prompt_tokens":100},
        }],
    }
    return {
        "run_id":data["run_id"],"batch_id":data["batch_id"],
        "blueprint":blueprint,"source_packs":data["source_packs"],
        "bundle":bundle,"execution":execution,
        "normalization":{"report":nr,"report_sha256":n_sha},
        "grounding":{"report":grounded["report"],
                     "report_sha256":grounded["report_sha256"]},
        "dedup":{"report":b6["report"],"report_sha256":b6["report_sha256"],
                 "members":b6["members"],"groups":b6["groups"],
                 "conflicts":b6["conflicts"]},
        "multi_plan":None,"a_analysis_refs":[],
    }


def with_video_reference(payload, *, available=True, hash_match=True):
    """A video is an export-only, immutable pin; never part of MiMo text."""
    ref={
        "source_id":str(uuid4()),"source_version_id":str(uuid4()),
        "output_id":str(uuid4()),"output_sha256":"d"*64,
        "schema_version":"video-intelligence/v1",
    }
    payload["bundle"]["video_analysis_refs"]=[ref]
    payload["source_packs"][0][0]["bundle_sha256"]=sha(payload["bundle"])
    payload["blueprint"]["parts"][0]["pack_manifest_sha256"]=sha(
        payload["source_packs"][0][0])
    if available:
        from_a=deepcopy(ref)
        if not hash_match:
            from_a["output_sha256"]="e"*64
        payload["a_analysis_refs"]=[from_a]
    return payload


class Stage11Pure(unittest.TestCase):
    def test_deterministic_warning_gate_never_approves_claims(self):
        data=pure_fixture()
        a=evaluate_quality(**data)
        b=evaluate_quality(**data)
        self.assertEqual(a,b)
        report=a["report"]
        self.assertEqual(report["decision"],"partial_ready")
        self.assertTrue(report["handoff_allowed"])
        self.assertEqual(report["blocker_count"],0)
        self.assertIn("ambiguous_subject",{f["code"] for f in report["findings"]})
        self.assertEqual(report["counts"]["unique_groups"],1)
        self.assertEqual(report["approved_facts"],0)
        self.assertFalse(report["publishing_allowed"])
        self.assertFalse(report["semantic_entailment_checked"])
        self.assertEqual(report["human_review_status"],"not_started")
        self.assertEqual(a["report_sha256"],sha(report))
        self.assertEqual(a["provider_calls"],0)

    def test_no_facts_or_only_b4_quarantine_blocks_review(self):
        for opts in ({"texts":[]},{"texts":["unknown"],"quarantine":True}):
            with self.subTest(opts=opts):
                report=evaluate_quality(**pure_fixture(**opts))["report"]
                self.assertEqual(report["decision"],"blocked")
                self.assertFalse(report["handoff_allowed"])
                self.assertIn("no_grounded_fact_groups",
                              {f["code"] for f in report["findings"]})
        report=evaluate_quality(**pure_fixture(texts=["other"],
                                                quarantine=True))["report"]
        self.assertEqual(report["counts"]["b4_quarantined"],1)
        self.assertEqual(report["counts"]["unique_groups"],0)

    def test_partial_with_explicit_acknowledgement_warns_and_without_blocks(self):
        payload=pure_fixture()
        payload["bundle"]["readiness"]="approved_partial"
        payload["bundle"]["excluded_sources"]=[{"source_id":"missing-source"}]
        # Refresh pinned pack SHA when modeling legitimate source selection.
        payload["source_packs"][0][0]["bundle_sha256"]=sha(payload["bundle"])
        payload["blueprint"]["parts"][0]["pack_manifest_sha256"]=sha(
            payload["source_packs"][0][0])
        payload["source_packs"][0][0]["allow_partial"]=True
        payload["blueprint"]["parts"][0]["pack_manifest_sha256"]=sha(
            payload["source_packs"][0][0])
        out=evaluate_quality(**payload)["report"]
        self.assertTrue(out["handoff_allowed"])
        self.assertIn("partial_approved_context",
                      {r["code"] for r in out["findings"]})
        payload["source_packs"][0][0]["allow_partial"]=False
        payload["blueprint"]["parts"][0]["pack_manifest_sha256"]=sha(
            payload["source_packs"][0][0])
        out=evaluate_quality(**payload)["report"]
        self.assertFalse(out["handoff_allowed"])
        self.assertIn("partial_context_not_acknowledged",
                      {r["code"] for r in out["findings"]})

    def test_competitor_and_model_web_claims_are_warnings_not_own_product(self):
        payload=pure_fixture()
        member=payload["dedup"]["members"][0]
        member["provenance"]["trust_label"]="model_reported_unverified"
        # Pure gate expects a fully verified B6 report; it does not recalc B6
        # content hashes, because repository materialize() owns those checks.
        report=evaluate_quality(**payload)["report"]
        self.assertIn("model_reported_web_source",
                      {f["code"] for f in report["findings"]})
        self.assertFalse(report["publishing_allowed"])

    def test_video_export_ref_is_not_mimo_text_or_fact(self):
        payload=with_video_reference(pure_fixture())
        report=evaluate_quality(**payload)["report"]
        self.assertTrue(report["handoff_allowed"])
        self.assertEqual(report["counts"]["video_refs_total"],1)
        self.assertEqual(report["counts"]["video_refs_available"],1)
        self.assertEqual(report["counts"]["video_refs_missing"],0)
        self.assertEqual(report["counts"]["source_chunks"],
                         payload["blueprint"]["parts"][0]["source_chunk_count"])
        self.assertTrue(report["video_refs_not_counted_as_mimo_text"])
        self.assertEqual(report["video_audit"][0]["status"],"available")
        self.assertEqual(report["approved_facts"],0)

    def test_missing_video_analysis_is_explicit_hard_blocker(self):
        for opts in ({"available":False}, {"available":True,"hash_match":False}):
            with self.subTest(opts=opts):
                payload=with_video_reference(pure_fixture(),**opts)
                result=evaluate_quality(**payload)["report"]
                self.assertEqual(result["decision"],"blocked")
                self.assertFalse(result["handoff_allowed"])
                self.assertEqual(result["counts"]["video_refs_missing"],1)
                self.assertIn("video_analysis_refs_unavailable",
                              {f["code"] for f in result["findings"]})
                self.assertTrue(result["video_refs_not_counted_as_mimo_text"])

    def test_duplicate_or_invalid_video_refs_fail_closed(self):
        for mutation in (
            lambda p:p["bundle"]["video_analysis_refs"].append(
                deepcopy(p["bundle"]["video_analysis_refs"][0])),
            lambda p:p["bundle"]["video_analysis_refs"][0].update(
                {"schema_version":"video-caption/v1"}),
        ):
            with self.subTest(mutation=str(mutation)):
                payload=with_video_reference(pure_fixture())
                mutation(payload)
                with self.assertRaises(IntakeError):
                    evaluate_quality(**payload)

    def test_candidate_cap_is_a_hard_coverage_blocker_not_silent_truncation(self):
        payload=pure_fixture(texts=["value"]*80)
        report=evaluate_quality(**payload)["report"]
        self.assertEqual(report["counts"]["candidate_count"],80)
        self.assertEqual(report["decision"],"blocked")
        self.assertIn("candidate_limit_reached",
                      {f["code"] for f in report["findings"]})

    def test_b5_rejection_is_warning_and_excluded_from_group_count(self):
        payload=pure_fixture(texts=["a","b"],b5_reject=True)
        report=evaluate_quality(**payload)["report"]
        self.assertEqual(report["counts"]["b5_rejected"],1)
        self.assertEqual(report["counts"]["unique_groups"],1)
        self.assertTrue(report["handoff_allowed"])
        self.assertIn("b5_rejections",
                      {f["code"] for f in report["findings"]})

    def test_mismatched_cost_counts_packs_and_sha_fail_closed(self):
        transforms=[
            lambda p:p["execution"].update({"status":"uncertain"}),
            lambda p:p["execution"].update({"reserved_usd":"0.0005"}),
            lambda p:p["execution"].update({"spent_usd":"1.000"}),
            lambda p:p["execution"]["attempts"].clear(),
            lambda p:p["execution"]["attempts"][0].update({"candidate_count":79}),
            lambda p:p["blueprint"]["parts"][0].update({"source_chunk_count":0}),
            lambda p:p["source_packs"][0][0].update({"bundle_sha256":"1"*64}),
            lambda p:p["dedup"]["report"].update({"eligible_count":999}),
            lambda p:p["dedup"]["members"][0]["provenance"].update({"quote_sha256":None}),
            lambda p:p["dedup"]["members"][0].update({"status":"skipped_b4"}),
        ]
        for transform in transforms:
            with self.subTest(transform=str(transform)):
                payload=pure_fixture()
                transform(payload)
                with self.assertRaises(IntakeError):
                    evaluate_quality(**payload)

    def test_full_pack_missing_original_fragment_fails_closed(self):
        payload=pure_fixture()
        payload["bundle"]["fragments"].pop()
        with self.assertRaises(IntakeError):
            evaluate_quality(**payload)

    def test_unreviewed_clean_source_can_pass_without_warnings(self):
        payload=pure_fixture()
        member=payload["dedup"]["members"][0]
        member["provenance"]["subject_scope"]="own_product"
        report=evaluate_quality(**payload)["report"]
        self.assertEqual(report["decision"],"ready_for_review")
        self.assertTrue(report["handoff_allowed"])
        self.assertEqual(report["warning_count"],0)
        self.assertFalse(report["publishing_allowed"])


@unittest.skipUnless(DSN,"PostgreSQL17 required for B7 Stage11 quality gate QA")
class Stage11PG(ReadOnlyIntakePG):
    _catalog=Stage06RequestPG._catalog
    _bundle=Stage06RequestPG._bundle
    _factory=Stage06RequestPG._factory
    _ready=Stage07FactPG._ready
    _completed=Stage08PG._completed
    _complete_b6=Stage10PG._complete_b6

    def setUp(self):
        super().setUp()
        self._catalog(window=100000)

    def _repo(self):
        return FactQualityGateRepository(connection_factory=self._factory())

    def _completed_b6(self, *, multi=False, sender=None):
        rid,bid,count=self._complete_b6(multi=multi,**(
            {"sender":sender} if sender is not None else {}))
        FactDeduplicationRepository(
            connection_factory=self._factory()).analyze(self.pid,rid)
        return rid,bid,count

    def test_ready_warning_and_historical_idempotence(self):
        rid,bid,_=self._completed_b6(sender=double_sender)
        repo=self._repo()
        first=repo.assess(self.pid,rid)
        second=repo.assess(self.pid,rid)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["quality_gate_id"],second["quality_gate_id"])
        self.assertEqual(first["decision"],"partial_ready")
        self.assertTrue(first["handoff_allowed"])
        self.assertEqual(first["counts"]["exact_duplicates"],1)
        self.assertEqual(first["approved_facts"],0)
        self.assertFalse(first["publishing_allowed"])
        replay=repo.materialize(self.pid,rid)
        self.assertEqual(replay["report"],{k:v for k,v in first.items()
            if k not in ("created","quality_gate_id")})
        self.assertEqual(replay["report_sha256"],sha(replay["report"]))

    def test_multi_pack_all_packs_and_original_sources_accounted(self):
        rid,bid,pack_count=self._completed_b6(multi=True)
        result=self._repo().assess(self.pid,rid)
        self.assertTrue(result["handoff_allowed"])
        self.assertEqual(result["counts"]["request_packs"],pack_count)
        self.assertEqual(result["counts"]["completed_paid_packs"],pack_count)
        self.assertEqual(result["counts"]["candidate_count"],pack_count)
        self.assertEqual(len(result["pack_audit"]),pack_count)
        self.assertEqual(result["provider_calls"],0)

    def test_missing_b6_blocks_gate_with_no_persisted_snapshot(self):
        rid,bid,_=self._complete_b6()
        with self.assertRaises(IntakeError):
            self._repo().assess(self.pid,rid)
        with self.psycopg.connect(DSN) as conn:
            n=conn.execute(
                "SELECT count(*) FROM knowledge_fact_quality_gates WHERE project_id=%s",
                (self.pid,),
            ).fetchone()[0]
        self.assertEqual(n,0)

    def test_blocked_all_quarantine_is_stored_not_auto_accepted(self):
        rid,bid,_=self._completed_b6(
            sender=lambda body:{
                **double_sender(body),
                "choices":[{"finish_reason":"stop",
                            "message":{"content":'{"facts":[]}'}}],
            }
        )
        report=self._repo().assess(self.pid,rid)
        self.assertEqual(report["decision"],"blocked")
        self.assertFalse(report["handoff_allowed"])
        self.assertEqual(report["approved_facts"],0)

    def test_immutability_and_project_isolation(self):
        rid,bid,_=self._completed_b6()
        repo=self._repo()
        gate=repo.assess(self.pid,rid)["quality_gate_id"]
        for sql in (
            "DELETE FROM knowledge_fact_quality_gates WHERE id=%s",
            "UPDATE knowledge_fact_quality_gates SET decision='blocked' WHERE id=%s",
        ):
            with self.subTest(sql=sql):
                with self.assertRaises(RaiseException):
                    with self.psycopg.connect(DSN) as conn:
                        conn.execute(sql,(gate,))
        with self.assertRaises(IntakeError):
            repo.assess(self.other,rid)
        with self.assertRaises(IntakeError):
            repo.materialize(self.other,rid)

    def test_historical_replay_after_current_profile_revision(self):
        rid,bid,_=self._completed_b6()
        repo=self._repo()
        result=repo.assess(self.pid,rid)
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
        saved=repo.materialize(self.pid,rid)
        self.assertEqual(saved["report_sha256"],sha(saved["report"]))
        self.assertEqual(saved["report"]["decision"],result["decision"])


if __name__=="__main__":
    unittest.main()
