"""Stage13 B9: append-only KnowledgeSnapshot and non-paid C taxonomy adapter."""
from __future__ import annotations

from copy import deepcopy
from hashlib import sha256
from uuid import uuid4
import unittest

from psycopg.errors import RaiseException

from test_ke_stage02_intake_db import ReadOnlyIntakePG, DSN
from test_ke_stage06_request_db import Stage06RequestPG
from test_ke_stage07_execution import Stage07FactPG
from test_ke_stage08_normalization import Stage08PG
from test_ke_stage10_deduplication import Stage10PG, double_sender
from test_ke_stage11_quality_gate import Stage11PG
from test_ke_stage12_human_review import Stage12PG
from intelligence.knowledge_extraction.human_review import FactHumanReviewRepository
from intelligence.knowledge_extraction.knowledge_snapshot import (
    build_snapshot,build_c_handoff,KnowledgeSnapshotRepository
)
from intelligence.taxonomy_builder.knowledge_binding import (
    to_taxonomy_generation_payload,preview_knowledge_for_c,
)
from intelligence.project_sources.context_builder import sha
from intelligence.knowledge_extraction.intake import IntakeError
from psycopg.types.json import Jsonb


def pure_fixture():
    pid="a"*32
    session,gate,run,batch,context=(str(uuid4()) for _ in range(5))
    sub_id=str(uuid4())
    source_id=str(uuid4())
    version_id=str(uuid4())
    output_id=str(uuid4())
    quote="Поддерживает быстрый ввод"
    approved={
        "group_id":"b"*64,"kind":"function",
        "source_role":"own_product","subject_scope":"own_product","subject_id":pid,
        "original_text":"Поддерживает быстрый ввод",
        "reviewed_text":"Поддерживает быстрый ввод",
        "human_edited":False,"review_event_sha256":"c"*64,
        "proposal_ids":["d"*64],
        "citations":[{
            "proposal_id":"d"*64,"source_quote":quote,
            "source_chunk_id":"f"*64,"b5_receipt_sha256":"e"*64,
            "original_provenance":{
                "source_text_match_only":True,
                "source_role":"own_product","subject_scope":"own_product",
                "subject_id":pid,"trust_label":"user_provided_profile",
                "quote_sha256":sha256(quote.encode()).hexdigest(),
                "locator_sha256":"3"*64,"source_id":source_id,
                "source_version_id":version_id,
                "output_id":output_id,
                "original_locator":{"kind":"questionnaire"},
            },
        }],
        "affirmed_source_support_by_reviewer":True,
        "affirmed_subject_scope_by_reviewer":True,
    }
    bundle={"project_id":pid,"context_id":context,"context_sha256":"6"*64,
            "video_analysis_refs":[]}
    ctx={"project_id":pid,"context_id":context,"context_sha256":"6"*64,
         "profile_revision":1,"profile_hash":"7"*64}
    gate_report={
        "run_id":run,"batch_id":batch,"decision":"ready_for_review",
        "handoff_allowed":True,"publishing_allowed":False,
        "counts":{"unique_groups":1,"video_refs_available":0,"video_refs_missing":0},
    }
    submission={
        "schema_version":"knowledge-human-review/v1","project_id":pid,
        "session_id":session,"gate_id":gate,
        "gate_report_sha256":sha(gate_report),
        "run_id":run,"batch_id":batch,"reviewer_id":"human",
        "publication_eligible":True,"publication_performed":False,
        "review_status":"human_submitted","approved_count":1,
        "rejected_count":0,"conflict_count":0,
        "approved_groups":[approved],"rejected_groups":[],
        "conflict_resolutions":[],
    }
    return {"project_id":pid,"submission_id":sub_id,"submission":submission,
            "context":ctx,"bundle":bundle,"quality_report":gate_report,
            "a_video_refs":[]}


class Stage13Pure(unittest.TestCase):
    def test_snapshot_exact_provenance_and_native_c_prompt_no_second_extraction(self):
        args=pure_fixture()
        a=build_snapshot(**args)
        b=build_snapshot(**args)
        self.assertEqual(a,b)
        snap=a["snapshot"]
        self.assertEqual(snap["approved_count"],1)
        self.assertEqual(snap["facts"][0]["citations"][0]["source_quote"],
                         "Поддерживает быстрый ввод")
        self.assertEqual(snap["facts"][0]["source_role"],"own_product")
        self.assertEqual(snap["submission_sha256"],sha(args["submission"]))
        self.assertEqual(a["snapshot_sha256"],sha(snap))
        self.assertFalse(snap["paid_c_authorized"])
        self.assertFalse(snap["objective_truth_guaranteed"])
        handoff=build_c_handoff(snap,str(uuid4()),a["snapshot_sha256"])
        payload=to_taxonomy_generation_payload(handoff,snap)
        self.assertEqual(payload["role"],"taxonomy_generation")
        self.assertEqual(payload["prompt_version"],"taxonomy-v2")
        self.assertTrue(payload["independent_c_quote_required"])
        self.assertFalse(payload["execution_authorized"])
        self.assertEqual(payload["approved_fact_count"],1)
        self.assertIn("approved_fact_ids_by_kind",payload["prompt_messages"][1]["content"])
        self.assertNotIn("fact_extraction",str(payload["role"]))

    def test_reject_all_not_publishable(self):
        args=pure_fixture()
        args["submission"]["approved_groups"]=[]
        args["submission"]["approved_count"]=0
        args["submission"]["publication_eligible"]=False
        args["quality_report"]["counts"]["unique_groups"]=0
        args["submission"]["gate_report_sha256"]=sha(args["quality_report"])
        with self.assertRaises(IntakeError):
            build_snapshot(**args)

    def test_blocked_gate_or_submission_sha_mismatch_rejected(self):
        for mutate in (
            lambda a:a["quality_report"].update({"decision":"blocked"}),
            lambda a:a["submission"].update({"gate_report_sha256":"0"*64}),
            lambda a:a["context"].update({"context_sha256":"5"*64}),
            lambda a:a["submission"]["approved_groups"][0].update({
                "affirmed_source_support_by_reviewer":False}),
        ):
            with self.subTest(change=str(mutate)):
                args=pure_fixture()
                mutate(args)
                with self.assertRaises(IntakeError):
                    build_snapshot(**args)

    def test_tampered_literal_quote_or_role_never_published(self):
        for change in (
            {"source_quote":"fabricated quote"},
            {"b5_receipt_sha256":"wrong"},
        ):
            with self.subTest(change=change):
                args=pure_fixture()
                args["submission"]["approved_groups"][0]["citations"][0].update(change)
                with self.assertRaises(IntakeError):
                    build_snapshot(**args)
        args=pure_fixture()
        args["submission"]["approved_groups"][0]["source_role"]="competitor"
        with self.assertRaises(IntakeError):
            build_snapshot(**args)

    def test_video_pins_are_separate_not_sent_to_mimo(self):
        args=pure_fixture()
        pin={
            "source_id":str(uuid4()),"source_version_id":str(uuid4()),
            "output_id":str(uuid4()),"schema_version":"video-intelligence/v1",
            "output_sha256":"f"*64,
        }
        args["bundle"]["video_analysis_refs"]=[pin]
        args["a_video_refs"]=[deepcopy(pin)]
        args["quality_report"]["counts"]["video_refs_available"]=1
        args["submission"]["gate_report_sha256"]=sha(args["quality_report"])
        out=build_snapshot(**args)
        self.assertEqual(out["snapshot"]["video_analysis_refs"],[pin])
        c=build_c_handoff(out["snapshot"],str(uuid4()),out["snapshot_sha256"])
        self.assertFalse(c["video_to_mimo"])
        self.assertEqual(c["video_analysis_refs"],[pin])
        args["a_video_refs"][0]["output_sha256"]="0"*64
        with self.assertRaises(IntakeError):
            build_snapshot(**args)

    def test_all_original_citations_preserved_and_no_forged_duplicates(self):
        args=pure_fixture()
        g=args["submission"]["approved_groups"][0]
        duplicate=deepcopy(g["citations"][0])
        duplicate["proposal_id"]="9"*64
        g["citations"].append(duplicate)
        g["proposal_ids"].append("9"*64)
        snap=build_snapshot(**args)["snapshot"]
        self.assertEqual(len(snap["facts"][0]["citations"]),2)
        g["proposal_ids"].append("9"*64)
        with self.assertRaises(IntakeError):
            build_snapshot(**args)

    def test_competitor_claim_never_becomes_own_product_fact_in_c(self):
        args=pure_fixture()
        approved=deepcopy(args["submission"]["approved_groups"][0])
        approved["group_id"]="8"*64
        approved["proposal_ids"]=["9"*64]
        approved["citations"][0]["proposal_id"]="9"*64
        approved["source_role"]="competitor"
        approved["subject_scope"]="competitor"
        approved["subject_id"]="other-brand"
        approved["citations"][0]["original_provenance"]["source_role"]="competitor"
        approved["citations"][0]["original_provenance"]["subject_scope"]="competitor"
        approved["citations"][0]["original_provenance"]["subject_id"]="other-brand"
        args["submission"]["approved_groups"].append(approved)
        args["submission"]["approved_count"]=2
        args["quality_report"]["counts"]["unique_groups"]=2
        args["submission"]["gate_report_sha256"]=sha(args["quality_report"])
        out=build_snapshot(**args)
        handoff=build_c_handoff(out["snapshot"],str(uuid4()),out["snapshot_sha256"])
        c=to_taxonomy_generation_payload(handoff,out["snapshot"])
        self.assertEqual(c["approved_fact_count"],1)
        self.assertEqual(c["excluded_for_unsafe_source_role_count"],1)
        self.assertNotIn("other-brand",str(c["prompt_messages"]))

    def test_c_refuses_unsigned_snapshot_or_unapproved_kind(self):
        args=pure_fixture()
        out=build_snapshot(**args)
        c=build_c_handoff(out["snapshot"],str(uuid4()),out["snapshot_sha256"])
        changed=deepcopy(c)
        changed["facts"][0]["text"]="fabricated C claim"
        with self.assertRaises(ValueError):
            to_taxonomy_generation_payload(changed,out["snapshot"])
        bad=deepcopy(out["snapshot"])
        bad["facts"][0]["review_status"]="pending"
        with self.assertRaises(ValueError):
            to_taxonomy_generation_payload(c,bad)
        with self.assertRaises(IntakeError):
            build_c_handoff(out["snapshot"],str(uuid4()),"0"*64)


@unittest.skipUnless(DSN,"PostgreSQL17 required for B9 publication and C adapter QA")
class Stage13PG(ReadOnlyIntakePG):
    _catalog=Stage06RequestPG._catalog
    _bundle=Stage06RequestPG._bundle
    _factory=Stage06RequestPG._factory
    _ready=Stage07FactPG._ready
    _completed=Stage08PG._completed
    _complete_b6=Stage10PG._complete_b6
    _completed_b6=Stage11PG._completed_b6
    _grant=Stage12PG._grant
    _vote_all=Stage12PG._vote_all

    def setUp(self):
        super().setUp()
        self._catalog(window=100000)
        self.reviewer="stage12-reviewer"

    def _repo(self):
        return KnowledgeSnapshotRepository(connection_factory=self._factory())

    def _ready_review(self, *, multi=False, sender=None):
        params={"multi":multi}
        if sender is not None:
            params["sender"]=sender
        rid,bid,count=self._completed_b6(**params)
        from intelligence.knowledge_extraction.quality_gate import FactQualityGateRepository
        gate=FactQualityGateRepository(connection_factory=self._factory())
        gate.assess(self.pid,rid)
        self._grant()
        reviews=FactHumanReviewRepository(connection_factory=self._factory())
        session=reviews.open(self.pid,rid,self.reviewer)
        return reviews,rid,bid,count,session

    def _published(self,*,sender=None,multi=False,reject=False,edited=False):
        params={"multi":multi}
        if sender is not None:
            params["sender"]=sender
        reviews,_,_,_,session=self._ready_review(**params)
        sid=session["session_id"]
        rev=self._vote_all(reviews,sid,reject=reject,edited=edited)
        reviews.submit(self.pid,sid,self.reviewer,expected_revision=rev)
        return sid

    def test_published_knowledge_and_c_handoff_are_immutable_idempotent(self):
        sid=self._published(sender=double_sender,edited=True)
        repo=self._repo()
        first=repo.publish(self.pid,sid,self.reviewer)
        second=repo.publish(self.pid,sid,self.reviewer)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["snapshot_id"],second["snapshot_id"])
        saved=repo.materialize(self.pid,sid,self.reviewer)
        self.assertEqual(saved["snapshot_sha256"],sha(saved["snapshot"]))
        self.assertEqual(saved["snapshot"]["approved_count"],1)
        self.assertEqual(len(saved["snapshot"]["facts"][0]["citations"]),2)
        self.assertTrue(saved["snapshot"]["facts"][0]["human_edited"])
        handoff=repo.prepare_c_handoff(self.pid,sid,self.reviewer)
        replay=repo.prepare_c_handoff(self.pid,sid,self.reviewer)
        self.assertTrue(handoff["created"])
        self.assertFalse(replay["created"])
        self.assertEqual(handoff["handoff_id"],replay["handoff_id"])
        c=repo.materialize_c_handoff(self.pid,sid,self.reviewer)
        self.assertEqual(c["handoff_sha256"],sha(c["handoff"]))
        # Synthetic A fixture is intentionally source_role=other. It may
        # become human-approved B9 knowledge, but cannot be reclassified as
        # this project's own-product function by legacy C taxonomy.
        self.assertEqual(saved["snapshot"]["facts"][0]["source_role"],"other")
        with self.assertRaises(ValueError):
            preview_knowledge_for_c(
                self.pid,sid,self.reviewer,connection_factory=self._factory())
        self.assertTrue(c["handoff"]["independent_c_quote_required"])
        self.assertEqual(c["handoff"]["execution_status"],"not_started")

    def test_cannot_publish_zero_approved_human_decisions(self):
        sid=self._published(reject=True)
        repo=self._repo()
        with self.assertRaises(IntakeError):
            repo.publish(self.pid,sid,self.reviewer)
        with self.psycopg.connect(DSN) as conn:
            count=conn.execute(
                """SELECT count(*) FROM knowledge_approved_snapshots
                   WHERE project_id=%s""",(self.pid,),
            ).fetchone()[0]
        self.assertEqual(count,0)

    def test_unsubmitted_review_or_missing_snapshot_refuses_c(self):
        reviews,_,_,_,session=self._ready_review()
        sid=session["session_id"]
        with self.assertRaises(IntakeError):
            self._repo().publish(self.pid,sid,self.reviewer)
        rev=self._vote_all(reviews,sid)
        reviews.submit(self.pid,sid,self.reviewer,expected_revision=rev)
        with self.assertRaises(IntakeError):
            self._repo().materialize(self.pid,sid,self.reviewer)

    def test_cross_project_and_ungranted_user_denied(self):
        sid=self._published()
        repo=self._repo()
        repo.publish(self.pid,sid,self.reviewer)
        with self.assertRaises(IntakeError):
            repo.materialize(self.other,sid,self.reviewer)
        with self.assertRaises(IntakeError):
            repo.publish(self.other,sid,self.reviewer)
        with self.assertRaises(IntakeError):
            repo.materialize(self.pid,sid,"not-authorized")

    def test_sql_snapshot_and_c_handoff_immutability(self):
        sid=self._published()
        repo=self._repo()
        ident=repo.publish(self.pid,sid,self.reviewer)["snapshot_id"]
        cid=repo.prepare_c_handoff(self.pid,sid,self.reviewer)["handoff_id"]
        for statement,pk in (
            ("DELETE FROM knowledge_approved_snapshots WHERE id=%s",ident),
            ("UPDATE knowledge_approved_snapshots SET approved_count=0 WHERE id=%s",ident),
            ("DELETE FROM knowledge_c_handoffs WHERE id=%s",cid),
            ("UPDATE knowledge_c_handoffs SET handoff_sha256=repeat('0',64) WHERE id=%s",cid),
        ):
            with self.subTest(sql=statement):
                with self.assertRaises(RaiseException):
                    with self.psycopg.connect(DSN) as conn:
                        conn.execute(statement,(pk,))

    def test_historical_snapshot_replay_survives_new_profile_revision(self):
        sid=self._published()
        repo=self._repo()
        pub=repo.publish(self.pid,sid,self.reviewer)
        repo.prepare_c_handoff(self.pid,sid,self.reviewer)
        with self.psycopg.connect(DSN) as conn:
            conn.execute(
                """INSERT INTO project_profile_versions(
                   project_id,revision,schema_version,profile_hash,profile)
                   VALUES(%s,2,1,%s,%s)""",
                (self.pid,"9"*64,Jsonb({"goal":"changed"})),
            )
            conn.execute(
                "UPDATE project_registry SET profile_revision=2,profile_hash=%s WHERE id=%s",
                ("9"*64,self.pid),
            )
        self.assertEqual(repo.materialize(self.pid,sid,self.reviewer)["snapshot_sha256"],
                         pub["snapshot_sha256"])
        self.assertEqual(repo.materialize_c_handoff(
            self.pid,sid,self.reviewer)["handoff"]["execution_status"],"not_started")

    def test_multi_pack_citations_survive_publication(self):
        sid=self._published(multi=True)
        repo=self._repo()
        saved=repo.publish(self.pid,sid,self.reviewer)
        material=repo.materialize(self.pid,sid,self.reviewer)["snapshot"]
        self.assertEqual(material["approved_count"],saved["approved_count"])
        self.assertEqual(sum(len(g["citations"]) for g in material["facts"]),
                         len({c["proposal_id"] for g in material["facts"]
                              for c in g["citations"]}))


if __name__=="__main__":
    unittest.main()
