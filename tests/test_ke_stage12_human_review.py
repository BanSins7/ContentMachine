"""Stage12 B8 human-only reviews: immutable audit, CAS, source & conflict checks.

All B3 provider responses use synthetic local fixtures; no paid calls.
"""
from __future__ import annotations

from copy import deepcopy
import json
import unittest

from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb

from test_ke_stage02_intake_db import ReadOnlyIntakePG, DSN
from test_ke_stage06_request_db import Stage06RequestPG
from test_ke_stage07_execution import Stage07FactPG, successful_sender
from test_ke_stage08_normalization import Stage08PG
from test_ke_stage10_deduplication import Stage10PG, double_sender
from test_ke_stage11_quality_gate import Stage11PG
from intelligence.knowledge_extraction.quality_gate import FactQualityGateRepository
from intelligence.knowledge_extraction.deduplication import FactDeduplicationRepository
from intelligence.knowledge_extraction.human_review import (
    FactHumanReviewRepository,_approved_review,REVIEW_ENGINE,GENESIS,
)
from intelligence.knowledge_extraction.intake import IntakeError
from intelligence.project_sources.context_builder import sha


def synthetic_event(kind, target, decision, *, revision=1, reviewed_text=None):
    return {
        "schema_version":"knowledge-review-event/v1",
        "engine_version":REVIEW_ENGINE,"session_id":"example","revision":revision,
        "reviewer_id":"human","target_kind":kind,"target_id":target,
        "decision":decision,"reviewed_text":reviewed_text,"note":"explicit review",
        "source_support_affirmed":decision=="approve",
        "subject_scope_affirmed":decision=="approve",
        "prev_sha256":GENESIS,"event_sha256":"b"*64,
    }


class Stage12PureConflictRules(unittest.TestCase):
    def _packet(self):
        return {
            "groups":[{
                "group_id":"a"*64,"original_text":"Функция включена","fact_kind":"function",
                "source_role":"own_product","subject_scope":"own_product",
                "subject_id":"project","members":[{
                    "proposal_id":"c"*64,"source_quote":"Функция включена",
                    "source_chunk_id":"e"*64,"b5_receipt_sha256":"f"*64,
                    "provenance":{"original_locator":{"kind":"context_profile"}},
                }],
            },{
                "group_id":"b"*64,"original_text":"Функция не включена","fact_kind":"function",
                "source_role":"own_product","subject_scope":"own_product",
                "subject_id":"project","members":[{
                    "proposal_id":"d"*64,"source_quote":"Функция не включена",
                    "source_chunk_id":"e"*64,"b5_receipt_sha256":"f"*64,
                    "provenance":{"original_locator":{"kind":"context_profile"}},
                }],
            }],
            "conflicts":[{"conflict_id":"1"*64,"group_a":"a"*64,
                         "group_b":"b"*64,"rule":"opposite_negation"}],
            "warnings":[{"code":"b6_possible_conflicts","severity":"warning","count":1}],
        }

    def test_conflict_selection_must_agree_with_both_group_votes(self):
        packet=self._packet()
        group_actions={
            "a"*64:synthetic_event("group","a"*64,"approve",reviewed_text="Функция включена"),
            "b"*64:synthetic_event("group","b"*64,"reject",reviewed_text="Функция не включена"),
        }
        conflict={"1"*64:synthetic_event("conflict","1"*64,"select_a")}
        ack=synthetic_event("warnings","gate_warnings","acknowledge")
        approved,rejected=_approved_review(packet,group_actions,conflict,ack)
        self.assertEqual(len(approved),1)
        self.assertEqual(len(rejected),1)
        self.assertEqual(approved[0]["group_id"],"a"*64)
        for change in ("select_b","reject_both","defer"):
            conflict["1"*64]["decision"]=change
            with self.subTest(change=change),self.assertRaises(IntakeError):
                _approved_review(packet,group_actions,conflict,ack)
        conflict["1"*64]["decision"]="select_a"
        with self.assertRaises(IntakeError):
            _approved_review(packet,group_actions,conflict,None)

    def test_every_group_decision_is_mandatory_even_when_conflict_ignored(self):
        packet=self._packet()
        one={"a"*64:synthetic_event("group","a"*64,"approve",reviewed_text="Функция включена")}
        conflict={"1"*64:synthetic_event("conflict","1"*64,"not_conflicting")}
        with self.assertRaises(IntakeError):
            _approved_review(packet,one,conflict,
                             synthetic_event("warnings","gate_warnings","acknowledge"))

    def test_unedited_and_edited_are_separate_human_assertions(self):
        packet=self._packet()
        originals={
            "a"*64:synthetic_event("group","a"*64,"approve",
                                    reviewed_text="Функция включена"),
            "b"*64:synthetic_event("group","b"*64,"approve",
                                    reviewed_text="Функция реально работает"),
        }
        conflicts={"1"*64:synthetic_event("conflict","1"*64,"not_conflicting")}
        approved,_=_approved_review(
            packet,originals,conflicts,synthetic_event("warnings","gate_warnings","acknowledge"))
        self.assertFalse(approved[0]["human_edited"])
        self.assertTrue(approved[1]["human_edited"])
        self.assertEqual(approved[1]["original_text"],"Функция не включена")
        self.assertEqual(approved[1]["reviewed_text"],"Функция реально работает")
        self.assertEqual(approved[1]["citations"][0]["source_quote"],"Функция не включена")


@unittest.skipUnless(DSN,"PostgreSQL17 required for B8 reviewer integration")
class Stage12PG(ReadOnlyIntakePG):
    _catalog=Stage06RequestPG._catalog
    _bundle=Stage06RequestPG._bundle
    _factory=Stage06RequestPG._factory
    _ready=Stage07FactPG._ready
    _completed=Stage08PG._completed
    _complete_b6=Stage10PG._complete_b6
    _completed_b6=Stage11PG._completed_b6

    def setUp(self):
        super().setUp()
        self._catalog(window=100000)
        self.reviewer="stage12-reviewer"

    def _repo(self):
        return FactHumanReviewRepository(connection_factory=self._factory())

    def _grant(self):
        with self.psycopg.connect(DSN) as conn:
            conn.execute(
                """INSERT INTO knowledge_fact_review_authorizations(
                   project_id,reviewer_id,granted_by)
                   VALUES(%s,%s,%s)""",
                (self.pid,self.reviewer,"trusted-admin"),
            )

    def _ready_review(self, *, multi=False, sender=None):
        params={"multi":multi}
        if sender is not None:
            params["sender"]=sender
        rid,bid,count=self._completed_b6(**params)
        gate=FactQualityGateRepository(connection_factory=self._factory())
        gate.assess(self.pid,rid)
        self._grant()
        repo=self._repo()
        session=repo.open(self.pid,rid,self.reviewer)
        return repo,rid,bid,count,session

    def _vote_all(self,repo,sid, *, reject=False, edited=False):
        packet=repo.inspect(self.pid,sid,self.reviewer)
        rev=packet["revision"]
        for group in packet["groups"]:
            kw={
                "expected_revision":rev,"target_kind":"group",
                "target_id":group["group_id"],
                "decision":"reject" if reject else "approve",
            }
            if not reject:
                kw["source_support_affirmed"]=True
                kw["subject_scope_affirmed"]=True
                if edited:
                    kw["reviewed_text"]=group["original_text"]+" (проверено человеком)"
                    kw["note"]="Я подтвердил исправленное утверждение"
            result=repo.record(self.pid,sid,self.reviewer,**kw)
            rev=result["revision"]
        for conflict in packet["conflicts"]:
            rev=repo.record(
                self.pid,sid,self.reviewer,expected_revision=rev,
                target_kind="conflict",target_id=conflict["conflict_id"],
                decision="reject_both" if reject else "not_conflicting",
                note="Проверено вручную по первоисточнику",
            )["revision"]
        if packet["warnings"]:
            rev=repo.record(
                self.pid,sid,self.reviewer,expected_revision=rev,
                target_kind="warnings",target_id="gate_warnings",
                decision="acknowledge",
                note="Ознакомлен с предупреждениями, материалами и ограничениями",
            )["revision"]
        return rev

    def test_review_packet_includes_evidence_warnings_and_duplicate_members(self):
        repo,rid,bid,count,session=self._ready_review(sender=double_sender)
        self.assertTrue(session["created"])
        pid=session["session_id"]
        packet=repo.inspect(self.pid,pid,self.reviewer)
        self.assertEqual(packet["status"],"open")
        self.assertEqual(packet["revision"],0)
        self.assertTrue(packet["groups"])
        self.assertEqual(sum(g["member_count"] for g in packet["groups"]),2)
        self.assertEqual(len(packet["groups"][0]["members"]),2)
        self.assertTrue(packet["groups"][0]["members"][0]["source_quote"])
        self.assertTrue(packet["groups"][0]["members"][0]["provenance"]["quote_sha256"])
        self.assertTrue(packet["warnings"])
        self.assertFalse(packet["groups"][0].get("approved",False))
        second=repo.open(self.pid,rid,self.reviewer)
        self.assertFalse(second["created"])
        self.assertEqual(second["session_id"],pid)

    def test_full_reviewer_submit_idempotence_historical_source_citations(self):
        repo,rid,bid,count,session=self._ready_review(sender=double_sender)
        sid=session["session_id"]
        last=self._vote_all(repo,sid)
        saved=repo.submit(self.pid,sid,self.reviewer,expected_revision=last)
        self.assertTrue(saved["created"])
        self.assertTrue(saved["publication_eligible"])
        repeated=repo.submit(self.pid,sid,self.reviewer,expected_revision=last)
        self.assertFalse(repeated["created"])
        self.assertEqual(saved["submission_id"],repeated["submission_id"])
        replay=repo.materialize_submission(self.pid,sid,self.reviewer)
        sub=replay["submission"]
        self.assertEqual(replay["submission_sha256"],sha(sub))
        self.assertEqual(sub["approved_count"],1)
        self.assertEqual(len(sub["approved_groups"][0]["proposal_ids"]),2)
        self.assertEqual(len(sub["approved_groups"][0]["citations"]),2)
        self.assertEqual(sub["reviewer_id"],self.reviewer)
        self.assertFalse(sub["publication_performed"])
        self.assertFalse(sub["objective_truth_guaranteed"])
        self.assertEqual(repo.inspect(self.pid,sid,self.reviewer)["status"],"submitted")

    def test_cas_requires_current_revision_and_explicit_assertions(self):
        repo,rid,bid,count,session=self._ready_review()
        sid=session["session_id"]
        group=repo.inspect(self.pid,sid,self.reviewer)["groups"][0]
        vote={"expected_revision":0,"target_kind":"group",
              "target_id":group["group_id"],"decision":"approve"}
        with self.assertRaises(IntakeError):
            repo.record(self.pid,sid,self.reviewer,**vote)
        with self.assertRaises(IntakeError):
            repo.record(self.pid,sid,self.reviewer,**{
                **vote,"source_support_affirmed":True,
            })
        answer=repo.record(self.pid,sid,self.reviewer,**{
            **vote,"source_support_affirmed":True,"subject_scope_affirmed":True,
        })
        self.assertEqual(answer["revision"],1)
        with self.assertRaises(IntakeError):
            repo.record(self.pid,sid,self.reviewer,**{
                **vote,"source_support_affirmed":True,
                "subject_scope_affirmed":True,
            })
        with self.assertRaises(IntakeError):
            repo.submit(self.pid,sid,self.reviewer,expected_revision=0)
        with self.assertRaises(IntakeError):
            repo.record(self.pid,sid,self.reviewer,expected_revision=1,
                        target_kind="group",target_id="f"*64,decision="reject")

    def test_missing_review_or_warning_ack_cannot_finalize(self):
        repo,rid,bid,count,session=self._ready_review()
        sid=session["session_id"]
        with self.assertRaises(IntakeError):
            repo.submit(self.pid,sid,self.reviewer,expected_revision=0)
        packet=repo.inspect(self.pid,sid,self.reviewer)
        rev=0
        for group in packet["groups"]:
            rev=repo.record(self.pid,sid,self.reviewer,expected_revision=rev,
                            target_kind="group",target_id=group["group_id"],
                            decision="reject")["revision"]
        if packet["warnings"]:
            with self.assertRaises(IntakeError):
                repo.submit(self.pid,sid,self.reviewer,expected_revision=rev)
        self._ack_only_if_needed(repo,sid,rev)

    def _ack_only_if_needed(self,repo,sid,revision):
        data=repo.inspect(self.pid,sid,self.reviewer)
        if data["warnings"]:
            revision=repo.record(self.pid,sid,self.reviewer,expected_revision=revision,
                                target_kind="warnings",target_id="gate_warnings",
                                decision="acknowledge",note="Прочитал предупреждения")["revision"]
        return revision

    def test_all_rejections_are_valid_human_result_but_not_publishable(self):
        repo,rid,bid,count,session=self._ready_review()
        sid=session["session_id"]
        rev=self._vote_all(repo,sid,reject=True)
        done=repo.submit(self.pid,sid,self.reviewer,expected_revision=rev)
        self.assertFalse(done["publication_eligible"])
        content=repo.materialize_submission(self.pid,sid,self.reviewer)["submission"]
        self.assertEqual(content["approved_count"],0)
        self.assertEqual(content["rejected_count"],len(repo.inspect(
            self.pid,sid,self.reviewer)["groups"]))
        self.assertFalse(content["publication_performed"])

    def test_human_edit_requires_reason_and_preserves_original_claim(self):
        repo,rid,bid,count,session=self._ready_review()
        sid=session["session_id"]
        packet=repo.inspect(self.pid,sid,self.reviewer)
        group=packet["groups"][0]
        with self.assertRaises(IntakeError):
            repo.record(self.pid,sid,self.reviewer,expected_revision=0,
                        target_kind="group",target_id=group["group_id"],
                        decision="approve",reviewed_text="Исправлено человеком",
                        source_support_affirmed=True,subject_scope_affirmed=True)
        rev=self._vote_all(repo,sid,edited=True)
        repo.submit(self.pid,sid,self.reviewer,expected_revision=rev)
        fact=repo.materialize_submission(self.pid,sid,self.reviewer)[
            "submission"]["approved_groups"][0]
        self.assertTrue(fact["human_edited"])
        self.assertNotEqual(fact["original_text"],fact["reviewed_text"])
        self.assertTrue(fact["citations"][0]["source_quote"])

    def test_no_grant_or_disabled_grant_rejects_review_operations(self):
        rid,bid,count=self._completed_b6()
        FactQualityGateRepository(connection_factory=self._factory()).assess(
            self.pid,rid)
        repo=self._repo()
        with self.assertRaises(IntakeError):
            repo.open(self.pid,rid,self.reviewer)
        self._grant()
        sid=repo.open(self.pid,rid,self.reviewer)["session_id"]
        with self.psycopg.connect(DSN) as conn:
            conn.execute(
                """UPDATE knowledge_fact_review_authorizations
                   SET enabled=FALSE WHERE project_id=%s AND reviewer_id=%s""",
                (self.pid,self.reviewer),
            )
        with self.assertRaises(IntakeError):
            repo.inspect(self.pid,sid,self.reviewer)
        with self.assertRaises(IntakeError):
            repo.record(self.pid,sid,self.reviewer,expected_revision=0,
                        target_kind="warnings",target_id="gate_warnings",
                        decision="acknowledge",note="Should be denied")

    def test_review_is_project_scoped_and_reviewer_cannot_take_over_session(self):
        repo,rid,bid,count,session=self._ready_review()
        sid=session["session_id"]
        with self.assertRaises(IntakeError):
            repo.inspect(self.other,sid,self.reviewer)
        with self.assertRaises(IntakeError):
            repo.open(self.other,rid,self.reviewer)
        with self.psycopg.connect(DSN) as conn:
            conn.execute(
                """INSERT INTO knowledge_fact_review_authorizations(
                   project_id,reviewer_id,granted_by) VALUES(%s,%s,%s)""",
                (self.pid,"other-human","trusted-admin"),
            )
        with self.assertRaises(IntakeError):
            repo.open(self.pid,rid,"other-human")
        with self.assertRaises(IntakeError):
            repo.inspect(self.pid,sid,"other-human")

    def test_blocked_b7_quality_gate_cannot_open_review(self):
        def empty_sender(body):
            res=successful_sender(body)
            res["choices"][0]["message"]["content"]='{"facts":[]}'
            return res
        rid,bid,count=self._completed_b6(sender=empty_sender)
        gate=FactQualityGateRepository(connection_factory=self._factory())
        self.assertEqual(gate.assess(self.pid,rid)["decision"],"blocked")
        self._grant()
        with self.assertRaises(IntakeError):
            self._repo().open(self.pid,rid,self.reviewer)

    def test_submitted_state_is_immutable_and_cannot_append_more_events(self):
        repo,rid,bid,count,session=self._ready_review()
        sid=session["session_id"]
        rev=self._vote_all(repo,sid)
        repo.submit(self.pid,sid,self.reviewer,expected_revision=rev)
        queries=(
            ("DELETE FROM knowledge_fact_review_events WHERE project_id=%s AND session_id=%s",
             (self.pid,sid)),
            ("UPDATE knowledge_fact_review_events SET decision='reject' WHERE project_id=%s AND session_id=%s",
             (self.pid,sid)),
            ("DELETE FROM knowledge_fact_review_submissions WHERE project_id=%s AND session_id=%s",
             (self.pid,sid)),
            ("UPDATE knowledge_fact_review_sessions SET status='open' WHERE project_id=%s AND id=%s",
             (self.pid,sid)),
        )
        for sql,args in queries:
            with self.subTest(sql=sql):
                with self.assertRaises(RaiseException):
                    with self.psycopg.connect(DSN) as conn:
                        conn.execute(sql,args)
        with self.assertRaises(IntakeError):
            repo.record(self.pid,sid,self.reviewer,expected_revision=rev,
                        target_kind="warnings",target_id="gate_warnings",
                        decision="acknowledge",note="Already submitted")

    def test_historical_submission_survives_profile_revision_change(self):
        repo,rid,bid,count,session=self._ready_review()
        sid=session["session_id"]
        rev=self._vote_all(repo,sid)
        saved=repo.submit(self.pid,sid,self.reviewer,expected_revision=rev)
        with self.psycopg.connect(DSN) as conn:
            conn.execute(
                """INSERT INTO project_profile_versions(
                   project_id,revision,schema_version,profile_hash,profile)
                   VALUES(%s,2,1,%s,%s)""",
                (self.pid,"9"*64,Jsonb({"goal":"new goal"})),
            )
            conn.execute(
                "UPDATE project_registry SET profile_revision=2,profile_hash=%s WHERE id=%s",
                ("9"*64,self.pid),
            )
        original=repo.materialize_submission(self.pid,sid,self.reviewer)
        self.assertEqual(saved["submission_id"],original["submission_id"])
        self.assertEqual(original["submission_sha256"],sha(original["submission"]))


if __name__=="__main__":
    unittest.main()
