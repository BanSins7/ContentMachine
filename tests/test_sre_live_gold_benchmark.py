"""M33 zero-network deterministic gold benchmark and metric denominator tests."""
from __future__ import annotations

from contextlib import nullcontext
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from intelligence.relevance.live_gold_benchmark import (
    VERSION,_samples,_split,_metrics,_manifest,_frozen_sha,
    preview_live_sre_gold_benchmark,record_live_sre_gold_benchmark,
    read_saved_sre_gold_benchmark,compare_saved_sre_gold_benchmarks,
    SQL_WORKSPACE_SHARE,SQL_WORKSPACE_EXCLUSIVE,SQL_SOURCE,SQL_RUN,
    SQL_INSERT,SQL_EXISTING,SQL_READ,
)
from intelligence.relevance.v1_contracts import SREContractError

PID="a"*32
RID="11111111-1111-4111-8111-111111111111"
RID2="99999999-9999-4999-8999-999999999999"
KEY="22222222-2222-4222-8222-222222222222"
SHA="f"*64
SHA2="e"*64
WHEN=datetime(2026,10,28,7,30,tzinfo=timezone.utc)
ACTIONS={"read_sre_gold_benchmark","record_sre_gold_benchmark",
         "compare_sre_gold_benchmarks"}

def allowed(project,actor,permission):
    return project==PID and actor=="human:7" and permission in ACTIONS

def row(i,model,gold,*,video=None,key=KEY,review_version=1):
    identity=i if key==KEY else i+100000
    return (i,key,f"33333333-3333-4333-8333-{identity:012d}",
            model,video or i,100+i,SHA,
            f"44444444-4444-4444-8444-{identity+200000*(review_version-1):012d}",
            review_version,gold,
            SHA,SHA2,"anthropic-opus-revision-pinned",SHA)

class Cursor:
    def __init__(self,value=None,rows=None):
        self.value=value
        self.rows=[] if rows is None else rows
    def fetchone(self):return self.value
    def fetchall(self):return self.rows

class DB:
    autocommit=True
    class Info:
        class Status:
            name="IDLE"
        transaction_status=Status()
    info=Info()
    def __init__(self,rows):
        self.rows=rows
        self.saved={}
        self.commands=[]
        self.revoked=False
        self.denied_run=False
    def transaction(self):return nullcontext()
    def execute(self,q,params=()):
        self.commands.append(q)
        if q=="SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY":
            return Cursor()
        if q in (SQL_WORKSPACE_SHARE,SQL_WORKSPACE_EXCLUSIVE):
            return Cursor((1,))
        if q==SQL_RUN:
            return Cursor(("cancelled" if self.denied_run else "running",
                           self.denied_run,"live"))
        if q==SQL_SOURCE:
            return Cursor(rows=self.rows)
        if q==SQL_INSERT:
            (bid,pid,rid,sha,seed,pct,minimum,raw,actor)=params
            if sha in [v[2] for v in self.saved.values()]:
                return Cursor(None)
            self.saved[bid]=(bid,rid,sha,seed,pct,minimum,json.loads(raw),WHEN)
            return Cursor((bid,))
        if q==SQL_EXISTING:
            for saved in self.saved.values():
                if saved[2]==params[2]:return Cursor((saved[0],))
            return Cursor(None)
        if q==SQL_READ:
            return Cursor(self.saved.get(params[1]))
        raise AssertionError(q[:140])

class M33GoldBenchmarkContracts(unittest.TestCase):
    def setUp(self):
        self.guard=patch(
            "intelligence.relevance.live_gold_benchmark._verify_current_d1"
        )
        self.d1=self.guard.start()
    def tearDown(self):
        self.guard.stop()

    def test_precision_recall_f1_denominators_and_uncertain_not_no_match(self):
        rows=_split(_samples([
            row(1,"match","match"),
            row(2,"uncertain","match"),
            row(3,"match","no_match"),
            row(4,"no_match","no_match"),
            row(5,"uncertain","no_match"),
            row(6,"match","uncertain"),
        ]),PID,"safe-seed",50)
        rows=[{**x,"split":"eval"} for x in rows]
        m=_metrics(rows,5)
        self.assertEqual(m["binary_gold_denominator"],5)
        self.assertEqual(m["evaluation_gold_counts"],
                         {"match":2,"no_match":3,"uncertain":1})
        self.assertEqual(m["confusion_matrix"],
                         {"tp":1,"fp":1,"fn":1,"tn":1})
        self.assertEqual(m["model_abstained_on_gold_positive_count"],1)
        self.assertEqual(m["model_abstained_on_gold_negative_count"],1)
        self.assertEqual(m["excluded_human_uncertain_count"],1)
        self.assertEqual(m["precision_match"],"0.500000")
        self.assertEqual(m["recall_match"],"0.500000")
        self.assertEqual(m["f1_match"],"0.500000")
        self.assertEqual(m["strict_known_gold_accuracy"],"0.400000")
        self.assertEqual(m["prediction_coverage_known_gold"],"0.600000")
        self.assertIsNone(m["brier_score"])
        self.assertIsNone(m["expected_calibration_error"])
        self.assertFalse(m["claim_of_model_generalization"])

    def test_only_human_uncertain_never_invents_quality(self):
        rows=_split(_samples([
            row(1,"match","uncertain"),
            row(2,"uncertain","uncertain"),
        ]),PID,"seed",50)
        m=_metrics(rows,1)
        self.assertEqual(m["binary_gold_denominator"],0)
        self.assertIsNone(m["precision_match"])
        self.assertIsNone(m["recall_match"])
        self.assertIsNone(m["f1_match"])
        self.assertIsNone(m["strict_known_gold_accuracy"])
        self.assertFalse(m["meets_minimum_sample_and_both_classes"])
        self.assertEqual(m["status"],"insufficient_gold_or_class_coverage")

    def test_deterministic_stratified_grouped_holdout_has_no_video_leakage(self):
        rows=[
            row(1,"match","match",video=100),
            row(1,"no_match","no_match",video=100,
                key="55555555-5555-4555-8555-555555555555"),
            row(3,"match","match",video=101),
            row(4,"match","match",video=102),
            row(5,"match","match",video=103),
            row(6,"no_match","no_match",video=104),
            row(7,"no_match","no_match",video=105),
            row(8,"no_match","no_match",video=106),
        ]
        a=_manifest(rows,PID,RID,"split-v2",40)
        b=_manifest(list(reversed(rows)),PID,RID,"split-v2",40)
        self.assertEqual(a,b)
        train={r["video_id"] for r in a if r["split"]=="train"}
        ev={r["video_id"] for r in a if r["split"]=="eval"}
        self.assertFalse(train & ev)
        self.assertTrue(train)
        self.assertTrue(ev)
        self.assertEqual(len(a),8)
        self.assertTrue(all("prompt" not in x for x in a))

    def test_missing_real_gold_returns_null_not_accuracy(self):
        db=DB([])
        out=preview_live_sre_gold_benchmark(
            db,PID,RID,actor_id="human:7",actor_authorizer=allowed)
        self.assertEqual(out["status"],"no_authenticated_human_gold")
        self.assertEqual(out["binary_gold_denominator"],0)
        self.assertIsNone(out["f1_match"])
        self.assertFalse(out["claim_of_model_generalization"])
        with self.assertRaises(SREContractError):
            record_live_sre_gold_benchmark(
                db,PID,RID,actor_id="human:7",actor_authorizer=allowed)

    def test_deterministic_snapshot_idempotent_and_historical_after_revision(self):
        db=DB([row(1,"match","no_match"),row(2,"no_match","match")])
        first=record_live_sre_gold_benchmark(
            db,PID,RID,actor_id="human:7",actor_authorizer=allowed,
            min_evaluation_examples=2)
        again=record_live_sre_gold_benchmark(
            db,PID,RID,actor_id="human:7",actor_authorizer=allowed,
            min_evaluation_examples=2)
        self.assertTrue(first["created"])
        self.assertFalse(again["created"])
        self.assertEqual(first["benchmark_id"],again["benchmark_id"])
        self.assertEqual(first["snapshot_sha256"],again["snapshot_sha256"])
        self.assertEqual(len(db.saved),1)
        db.rows=[row(1,"match","match",review_version=2),
                 row(2,"no_match","match",review_version=2)]
        updated=record_live_sre_gold_benchmark(
            db,PID,RID,actor_id="human:7",actor_authorizer=allowed)
        self.assertNotEqual(first["snapshot_sha256"],updated["snapshot_sha256"])
        self.assertEqual(len(db.saved),2)
        old=read_saved_sre_gold_benchmark(
            db,PID,first["benchmark_id"],actor_id="human:7",
            actor_authorizer=allowed)
        self.assertTrue(old["historical_review_ids_frozen"])
        self.assertEqual(old["snapshot_sha256"],first["snapshot_sha256"])
        self.assertTrue(old["may_differ_from_latest_human_review"])
        self.assertEqual(old["human_reviewed_sample_count"],2)
        self.assertFalse(old["training_occurred"])
        self.assertTrue(all("UPDATE" not in q and "DELETE" not in q
                            for q in db.commands))

    def test_foreign_actor_cancel_and_db_snapshot_forgery_denied(self):
        db=DB([row(1,"match","no_match")])
        with self.assertRaises(SREContractError) as actor:
            preview_live_sre_gold_benchmark(
                db,PID,RID,actor_id="bot:2",actor_authorizer=allowed)
        self.assertEqual(actor.exception.code,"actor_not_authorized")
        db.denied_run=True
        with self.assertRaises(SREContractError):
            preview_live_sre_gold_benchmark(
                db,PID,RID,actor_id="human:7",actor_authorizer=allowed)
        db.denied_run=False
        snapshot=record_live_sre_gold_benchmark(
            db,PID,RID,actor_id="human:7",actor_authorizer=allowed)
        saved=list(db.saved[snapshot["benchmark_id"]])
        changed=list(saved[6])
        changed[0]["model_decision"]="no_match"
        db.saved[snapshot["benchmark_id"]]=tuple(
            saved[:6]+[changed]+saved[7:]
        )
        with self.assertRaises(SREContractError):
            read_saved_sre_gold_benchmark(
                db,PID,snapshot["benchmark_id"],
                actor_id="human:7",actor_authorizer=allowed)

    def test_comparisons_require_identical_paired_gold_and_holdout(self):
        db=DB([row(1,"match","match"),row(2,"no_match","no_match")])
        a=record_live_sre_gold_benchmark(
            db,PID,RID,actor_id="human:7",actor_authorizer=allowed,
            min_evaluation_examples=2)
        # A different pinned model/run on the same source videos & C9 labels.
        db.rows=[row(1,"no_match","match"),row(2,"no_match","no_match")]
        b=record_live_sre_gold_benchmark(
            db,PID,RID2,actor_id="human:7",actor_authorizer=allowed,
            min_evaluation_examples=2)
        result=compare_saved_sre_gold_benchmarks(
            db,PID,a["benchmark_id"],b["benchmark_id"],
            actor_id="human:7",actor_authorizer=allowed)
        self.assertEqual(result["status"],"paired_descriptive_only")
        self.assertFalse(result["superiority_claimed"])
        self.assertIsNone(result["significance_test"])
        self.assertEqual(result["paired_evaluation_count"],2)
        db.rows=[row(1,"no_match","no_match",review_version=2),
                 row(2,"no_match","no_match")]
        c=record_live_sre_gold_benchmark(
            db,PID,RID2,actor_id="human:7",actor_authorizer=allowed)
        nonpaired=compare_saved_sre_gold_benchmarks(
            db,PID,a["benchmark_id"],c["benchmark_id"],
            actor_id="human:7",actor_authorizer=allowed)
        self.assertEqual(nonpaired["status"],
                         "not_comparable_unpaired_or_label_drift")
        self.assertFalse(nonpaired["superiority_claimed"])

    def test_policy_fails_closed_on_bad_seed_percent_float_and_duplicates(self):
        db=DB([row(1,"match","match")])
        for bad in ("../bad","x"*65,"key with space"):
            with self.subTest(seed=bad),self.assertRaises(SREContractError):
                preview_live_sre_gold_benchmark(
                    db,PID,RID,split_seed=bad,
                    actor_id="human:7",actor_authorizer=allowed)
        with self.assertRaises(SREContractError):
            preview_live_sre_gold_benchmark(
                db,PID,RID,holdout_percent=30.0,
                actor_id="human:7",actor_authorizer=allowed)
        with self.assertRaises(SREContractError):
            _samples([row(1,"match","match"),row(1,"match","match")])
        with self.assertRaises(SREContractError):
            _samples([row(1,"match","match")[:-1]+("x",)])

if __name__=="__main__":
    unittest.main()
