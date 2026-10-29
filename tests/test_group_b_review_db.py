"""GB-MOD-23 optional disposable PostgreSQL17 immutable human review tests.

Reuses synthetic GB22 source + fake permission checks. NO actual human,
signed model receipt, authoritative label/QA pass or production writes.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import uuid4
import os
import unittest

from intelligence.migrate import apply_migrations
from intelligence.group_b.review import (
    ReviewError,make_decision,append_review,read_effective_labels,
    load_gold_set,gold_sample_key,QualityPolicy,evaluate_quality,
)
from intelligence.group_b.persistence import read_group_b_result
import test_group_b_atomic_db as atomic_fixture


def disposable_dsn():
    from psycopg.conninfo import conninfo_to_dict
    value=os.environ["CM_TEST_PG_DSN"]
    db=conninfo_to_dict(value).get("dbname","").lower()
    if "test" not in db and "_ci" not in db:
        raise RuntimeError("Review tests require disposable PG17 database")
    return value


class FakeReviewerHost:
    def __init__(self,accept=True):
        self.accept=accept
        self.calls=[]
    def verify_human_review_admission(self,project_id,run_id,reviewer_id,
                                      decision,original,effective,events):
        self.calls.append(("admission",reviewer_id))
        return (self.accept is True and original is not None and
                reviewer_id.startswith("fixture-") and
                decision.verify_digest())
    def verify_human_review_effective(self,project_id,run_id,analysis_id,revision_sha):
        self.calls.append(("read",analysis_id))
        return self.accept is True
    def verify_gold_annotation(self,project_id,run_id,reviewer_id,decision,original):
        self.calls.append(("gold",reviewer_id))
        return self.accept is True and reviewer_id.startswith("fixture-") and original is not None


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"No disposable PostgreSQL17")
class TestReviewPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg=psycopg
        cls.dsn=disposable_dsn()
        with psycopg.connect(cls.dsn) as conn:
            apply_migrations(conn)

    def prepare(self):
        base=atomic_fixture.TestAtomicGroupBPG17("runTest")
        base.psycopg=self.psycopg
        base.dsn=self.dsn
        bundle=base.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            stored=base.write(conn,bundle)
        return bundle[0],stored

    def decide(self,m,stored,revision=0,previous_sha=None,*,kind="correction",
               reviewer="fixture-reviewer-A",value=None,gold_version=None):
        sample=(gold_sample_key(
            analysis_id=stored.analysis_id,block="B8",target="topic",
            index=None,field="subtopic_id") if kind=="gold_annotation" else None)
        return make_decision(
            kind=kind,analysis_id=stored.analysis_id,
            analysis_key=stored.analysis_key,
            manifest_sha256=m.manifest_sha256,
            previous_revision=revision,previous_sha256=previous_sha,
            block="B8",target="topic",index=None,field="subtopic_id",
            value=value,
            evidence_refs=[m.observations[0].ref_sha256],
            reason="Independently verified source review fixture",
            gold_version=gold_version,sample_key=sample,
        )

    def append(self,m,decision,reviewer="fixture-reviewer-A",host=None):
        with self.psycopg.connect(self.dsn) as conn:
            return append_review(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                reviewer_id=reviewer,
                expected_revision=decision.payload["previous_revision"],
                decision=decision,
                admission_verifier=host or FakeReviewerHost(),
            )

    def test_first_correction_overlay_sql005_baseline_immutable(self):
        m,saved=self.prepare()
        rev=self.decide(m,saved)
        result=self.append(m,rev)
        self.assertEqual(result.revision,1)
        with self.psycopg.connect(self.dsn) as conn:
            original=conn.execute(
                "SELECT subtopic_id FROM content_b_topic_assignments WHERE analysis_id=%s",
                (saved.analysis_id,)
            ).fetchone()[0]
            effective=read_effective_labels(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                admission_verifier=FakeReviewerHost(),
            )
            baseline=read_group_b_result(conn,project_id=m.project_id,
                                         run_id=m.b_run_id)
        self.assertIsNotNone(original)
        self.assertIsNone(effective["effective_rows"][("B8","topic",None)]["subtopic_id"])
        self.assertTrue(effective["human_corrected"])
        self.assertFalse(effective["qualified_for_downstream"])
        self.assertTrue(baseline["has_human_corrections"])
        self.assertFalse(baseline["qualified_for_downstream"])
        self.assertEqual(baseline["human_review_revision"],1)

    def test_replay_same_review_safe_but_stale_different_reviewer_denied(self):
        m,saved=self.prepare()
        decision=self.decide(m,saved)
        self.assertFalse(self.append(m,decision).replayed)
        self.assertTrue(self.append(m,decision).replayed)
        with self.assertRaises(ReviewError):
            self.append(m,decision,reviewer="fixture-reviewer-B")
        with self.psycopg.connect(self.dsn) as conn:
            count=conn.execute(
                "SELECT count(*) FROM group_b_review_events WHERE project_id=%s AND run_id=%s::uuid",
                (m.project_id,m.b_run_id)
            ).fetchone()[0]
        self.assertEqual(count,1)

    def test_sequential_versioned_gold_annotations_never_change_effective(self):
        m,saved=self.prepare()
        version="review-gold-2026-v1"
        first=self.decide(m,saved,kind="gold_annotation",
                          gold_version=version,value=None)
        one=self.append(m,first)
        second=self.decide(m,saved,revision=1,
                           previous_sha=one.decision_sha256,
                           kind="gold_annotation",gold_version=version,value=None)
        two=self.append(m,second,reviewer="fixture-reviewer-B")
        self.assertEqual(two.revision,2)
        with self.psycopg.connect(self.dsn) as conn:
            effective=read_effective_labels(
                conn,project_id=m.project_id,run_id=m.b_run_id,
                admission_verifier=FakeReviewerHost(),
            )
            gold=load_gold_set(conn,project_id=m.project_id,
                               gold_version=version,verifier=FakeReviewerHost())
        self.assertFalse(effective["human_corrected"])
        self.assertEqual(effective["effective_rows"],{})
        self.assertEqual(len(gold.samples),1)
        self.assertTrue(gold.verify_digest())
        self.assertEqual(len(gold.samples[0].annotations),2)
        # One example cannot accidentally satisfy production minimum support.
        q=evaluate_quality(gold,QualityPolicy("quality-minimum-v1"))
        self.assertEqual(q["quality_gate"],"blocked")

    def test_gold_same_reviewer_same_version_duplicate_refused(self):
        m,saved=self.prepare()
        version="review-gold-2026-v2"
        one=self.decide(m,saved,kind="gold_annotation",gold_version=version,
                        value=None)
        result=self.append(m,one)
        two=self.decide(m,saved,revision=1,
                        previous_sha=result.decision_sha256,
                        kind="gold_annotation",gold_version=version,value=None)
        with self.assertRaises(ReviewError):
            self.append(m,two)

    def test_immutable_review_rows_cannot_update_or_delete(self):
        m,saved=self.prepare()
        self.append(m,self.decide(m,saved))
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(Exception):
                with conn.transaction():
                    conn.execute(
                        """DELETE FROM group_b_review_events
                           WHERE project_id=%s AND run_id=%s::uuid""",
                        (m.project_id,m.b_run_id)
                    )
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM group_b_review_events WHERE project_id=%s AND run_id=%s::uuid",
                (m.project_id,m.b_run_id)
            ).fetchone()[0],1)

    def test_missing_independent_reviewer_authorization_denied(self):
        m,saved=self.prepare()
        with self.assertRaises(ReviewError):
            self.append(m,self.decide(m,saved),host=FakeReviewerHost(False))
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM group_b_review_events WHERE project_id=%s",
                (m.project_id,)
            ).fetchone()[0],0)

    def test_cross_project_cannot_read_or_forge_review_target(self):
        m,saved=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(ReviewError):
                read_effective_labels(
                    conn,project_id="other-project",run_id=m.b_run_id,
                    admission_verifier=FakeReviewerHost(),
                )
        wrong=self.decide(m,saved)
        from dataclasses import replace
        altered=dict(wrong.payload,analysis_id=saved.analysis_id+1000)
        from intelligence.group_b.contracts import canonical_hash
        wrong=replace(wrong,payload=altered,
                      decision_sha256=canonical_hash(altered))
        with self.assertRaises(ReviewError):
            self.append(m,wrong)

    def test_two_reviewers_same_revision_race_one_winner(self):
        m,saved=self.prepare()
        d=self.decide(m,saved)
        sync=Barrier(2)
        def contender(name):
            with self.psycopg.connect(self.dsn) as conn:
                sync.wait(timeout=20)
                return append_review(
                    conn,project_id=m.project_id,run_id=m.b_run_id,
                    reviewer_id=name,expected_revision=0,
                    decision=d,admission_verifier=FakeReviewerHost(),
                )
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(contender,"fixture-reviewer-A")
            b=pool.submit(contender,"fixture-reviewer-B")
            outcomes=[]
            for task in (a,b):
                try:
                    outcomes.append(("success",task.result(timeout=30)))
                except ReviewError:
                    outcomes.append(("conflict",None))
        self.assertEqual(sorted(x[0] for x in outcomes),
                         ["conflict","success"])


if __name__=="__main__":
    unittest.main()
