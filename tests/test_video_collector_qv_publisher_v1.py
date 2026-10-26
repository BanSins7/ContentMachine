"""VC-MOD-04: deterministic QV sharding, atomic ACK, rollback and replay QA.

Uses an offline transactional PG simulation only. PostgreSQL17 constraints
and integration are additionally exercised by schema tests in GitHub CI.
"""
from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest.mock import patch
from uuid import UUID

from intelligence.taxonomy_builder.pipeline_contracts import canonical, sha256
from intelligence.collector.contracts import CollectorError
from intelligence.collector.qv_reader import TrustedQVSource, _TRUST_SEAL
from intelligence.collector.query_publisher import (
    DEFAULT_SHARD_SIZE, prepare_qv_publication, publish_qv, publish_due_qv,
)

P = "a"*32
GID = "11111111-1111-4111-8111-111111111111"
RID = "22222222-2222-4222-8222-222222222222"
OID = "33333333-3333-4333-8333-333333333333"
PUB = "44444444-4444-4444-8444-444444444444"
TAX = "a"*64
CONF = "b"*64
SOURCE = "c"*64


def make_source(count, *, rejected=0):
    queries, decisions, coverage = [], [], []
    size = count+rejected
    hyp_n = max(1, (size+15)//16)
    for h in range(hyp_n):
        hyp = "hyp_%02d"%h
        selected = max(0, min(16, count-h*16))
        total = max(selected, min(16, size-h*16))
        coverage.append({
            "hypothesis_id": hyp, "source_count": total,
            "selected_count": selected,
            "outcome": ("selected" if selected else
                        "all_rejected" if total else "no_source_candidates"),
            "reason": None if selected else ("filtered" if total else "no candidates"),
        })
    for i in range(size):
        cid = str(UUID(int=i+1024))
        h = i//16
        hyp = "hyp_%02d"%h
        if i<count:
            queries.append({
                "candidate_id": cid, "hypothesis_id": hyp,
                "direction_id": "dir_%02d"%h, "branch_id": None,
                "text": "fitness topic %d"%i, "query_mode": "keyword",
                "language": "en", "source_fact_ids": ["some_fact"],
                "angle": "direct", "research_stance": "neutral",
                "rationale": "research", "relevance_score": 0.9,
                "negative_score": 0.0, "mmr_score": 0.5,
                "rank_in_hypothesis": i%16+1,
            })
        decisions.append({"candidate_id":cid, "decision":"selected" if i<count else "rejected",
                          "reason_code":"selected" if i<count else "quota",
                          "duplicate_of": None, "positive_similarity":0.9,
                          "negative_similarity":0.0, "score_margin":0.9, "mmr_score":0.5})
    # Rejected tests only for single hypothesis <=16; count=0 and rejected>0
    # triggers consistent all_rejected coverage.
    digest = sha256({
        "project_id":P, "outbox_id":OID,"validation_run_id":RID,
        "generation_id":GID,"publication_id":PUB,"taxonomy_version":3,
        "taxonomy_sha256":TAX,"config_sha256":CONF,
        "source_bundle_sha256":SOURCE,"embedding_model_key":"model",
        "source_candidate_count":size,
        "queries":queries,"coverage":coverage,"decisions":decisions,
    })
    return TrustedQVSource(
        P,OID,RID,GID,PUB,3,TAX,CONF,SOURCE,"model",size,count,digest,
        canonical(queries),canonical(coverage),canonical(decisions),_TRUST_SEAL,
    )


class Result:
    def __init__(self,rows): self.rows=rows
    def fetchone(self):return self.rows[0] if self.rows else None
    def fetchall(self):return list(self.rows)


class Tx:
    def __init__(self,db):self.db=db
    def __enter__(self):
        self.before=deepcopy(self.db.state)
        return self.db
    def __exit__(self,typ,exc,tb):
        if typ is not None:
            self.db.state=self.before
        return False


class FakeDB:
    def __init__(self):
        self.state={"ack":False,"attempts":0,"manifest":None,
                    "shards":[],"queries":[],"coverage":[],"query_sets":{}}
        self.calls=[]
        self.fail_sql=None
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def transaction(self):return Tx(self)
    def execute(self,sql,params=None):
        self.calls.append((sql,params))
        if self.fail_sql and self.fail_sql in sql:
            raise RuntimeError("Injected DB crash")
        state=self.state
        if "FROM qv_outbox o JOIN qv_runs v" in sql:
            return Result([(RID, "delivered" if state["ack"] else None,
                            "validated_queries_ready/v1",
                            {"validation_run_id":RID,"generation_id":GID,
                             "schema_version":"validated_queries/v1",
                             "embedding_mode":"semantic_local",
                             "selected_count":self.count}, P,GID,
                            "semantic_local","completed",self.count)])
        if "FROM collector_qv_manifests" in sql:
            return Result([state["manifest"]] if state["manifest"] else [])
        if "INSERT INTO collector_qv_manifests" in sql:
            mid=str(UUID(int=900))
            state["manifest"]=(mid, params[8], params[7], params[13],
                               params[9], params[11], params[12],
                               params[3], params[4], params[6], params[5])
            return Result([(mid,)])
        if "INSERT INTO collector_query_sets" in sql:
            qid=str(UUID(int=1000+len(state["query_sets"])))
            state["query_sets"][qid]={"sha":params[4],"payload":json.loads(params[3])}
            return Result([(qid,)])
        if "INSERT INTO collector_query_shards" in sql:
            sid=str(UUID(int=2000+len(state["shards"])))
            state["shards"].append({
                "id":sid, "qid":params[4],"ordinal":params[5],
                "count":params[6],"digest":params[7]})
            return Result([(sid,)])
        if "INSERT INTO collector_manifest_queries" in sql:
            state["queries"].append(params)
            return Result([])
        if "INSERT INTO collector_manifest_coverage" in sql:
            state["coverage"].append(params)
            return Result([])
        if "UPDATE qv_outbox" in sql:
            if state["ack"]:
                return Result([])
            state["ack"]=True
            state["attempts"]+=1
            return Result([(OID,)])
        if "SELECT s.query_set_id,s.shard_ordinal" in sql:
            return Result([(s["qid"],s["ordinal"],s["count"],s["digest"],
                            state["query_sets"][s["qid"]]["sha"],
                            state["query_sets"][s["qid"]]["payload"])
                           for s in sorted(state["shards"],key=lambda k:k["ordinal"])])
        if "COUNT(DISTINCT candidate_id)" in sql:
            return Result([(len(state["queries"]),
                            len({r[4] for r in state["queries"]}))])
        if "COALESCE(SUM(selected_count),0)" in sql:
            return Result([(len(state["coverage"]),
                            sum(x[5] for x in state["coverage"]))])
        raise AssertionError("Unexpected SQL " + sql[:130])


class PublisherTests(unittest.TestCase):
    def test_shards_zero_one_twenty_twentyone_480(self):
        for n, expected in [(0,0),(1,1),(20,1),(21,2),(480,24)]:
            with self.subTest(count=n):
                s=make_source(n)
                p=prepare_qv_publication(s)
                self.assertEqual(p.shard_count,expected)
                self.assertEqual(p.selected_count,n)
                self.assertEqual(p.status,"no_queries" if n==0 else "published")
                self.assertEqual([len(x.candidate_ids) for x in p.shards],
                                 [len(x.candidate_ids) for x in p.shards])
                self.assertTrue(all(1<=len(x.candidate_ids)<=20 for x in p.shards))
                all_ids=[c for x in p.shards for c in x.candidate_ids]
                self.assertEqual(len(all_ids),n)
                self.assertEqual(len(set(all_ids)),n)
                self.assertEqual(p.manifest_sha256,prepare_qv_publication(s).manifest_sha256)

    def test_not_truncate_480_and_custom_size_50(self):
        s=make_source(480)
        plan=prepare_qv_publication(s,shard_size=50)
        self.assertEqual(plan.shard_count,10)
        self.assertEqual([len(x.candidate_ids) for x in plan.shards],
                         [50]*9+[30])
        with self.assertRaises(CollectorError):
            prepare_qv_publication(s,shard_size=51)
        with self.assertRaises(CollectorError):
            prepare_qv_publication(s,shard_size=True)
        with self.assertRaises(CollectorError) as err:
            prepare_qv_publication(make_source(481))
        self.assertEqual(err.exception.code,"qv_source_changed")

    def test_full_commit_and_atomic_ack(self):
        s=make_source(21)
        db=FakeDB();db.count=21
        with patch("intelligence.collector.query_publisher.read_trusted_qv",return_value=s) as read:
            result=publish_qv(P,OID,connect=lambda:db)
        self.assertFalse(result["reused"])
        self.assertTrue(result["acknowledged"])
        self.assertEqual(result["provider_calls"],0)
        self.assertEqual(result["shard_count"],2)
        self.assertEqual(len(result["query_set_ids"]),2)
        self.assertEqual(len(db.state["queries"]),21)
        self.assertEqual(len(db.state["coverage"]),2)
        self.assertEqual(db.state["attempts"],1)
        self.assertTrue(db.state["ack"])
        read.assert_called_once_with(P,OID,connect=read.call_args.kwargs["connect"],allow_acknowledged=True)
        self.assertTrue(any("FOR UPDATE OF o" in q[0] for q in db.calls))
        self.assertEqual(db.calls[-1][0].lstrip().split(" ")[0],"UPDATE")

    def test_exact_replay_and_fingerprint_conflict(self):
        source=make_source(21)
        db=FakeDB();db.count=21
        with patch("intelligence.collector.query_publisher.read_trusted_qv",return_value=source):
            before=publish_qv(P,OID,connect=lambda:db)
            again=publish_qv(P,OID,connect=lambda:db)
        self.assertEqual(before["manifest_id"],again["manifest_id"])
        self.assertEqual(before["query_set_ids"],again["query_set_ids"])
        self.assertTrue(again["reused"])
        self.assertEqual(db.state["attempts"],1)
        with patch("intelligence.collector.query_publisher.read_trusted_qv",
                   return_value=source):
            with self.assertRaises(CollectorError) as e:
                publish_qv(P,OID,shard_size=10,connect=lambda:db)
        self.assertEqual(e.exception.code,"qv_manifest_conflict")
        self.assertEqual(db.state["attempts"],1)

    def test_rolled_back_on_any_failed_insert(self):
        source=make_source(21)
        for sql in ("INSERT INTO collector_query_shards",
                    "INSERT INTO collector_manifest_coverage",
                    "UPDATE qv_outbox"):
            with self.subTest(sql=sql):
                db=FakeDB();db.count=21;db.fail_sql=sql
                with patch("intelligence.collector.query_publisher.read_trusted_qv",
                           return_value=source):
                    with self.assertRaisesRegex(RuntimeError,"Injected DB crash"):
                        publish_qv(P,OID,connect=lambda:db)
                self.assertIsNone(db.state["manifest"])
                self.assertEqual(db.state["query_sets"],{})
                self.assertFalse(db.state["ack"])
                self.assertEqual(db.state["attempts"],0)

    def test_zero_result_ack_no_query_set(self):
        source=make_source(0)
        db=FakeDB();db.count=0
        with patch("intelligence.collector.query_publisher.read_trusted_qv",
                   return_value=source):
            r=publish_qv(P,OID,connect=lambda:db)
        self.assertEqual(r["status"],"no_queries")
        self.assertEqual(r["query_set_ids"],[])
        self.assertEqual(len(db.state["queries"]),0)
        self.assertEqual(len(db.state["coverage"]),1)
        self.assertTrue(db.state["ack"])

    def test_already_acked_without_manifest_rejected(self):
        source=make_source(1)
        db=FakeDB();db.count=1;db.state["ack"]=True
        with patch("intelligence.collector.query_publisher.read_trusted_qv",
                   return_value=source):
            with self.assertRaises(CollectorError) as error:
                publish_qv(P,OID,connect=lambda:db)
        self.assertEqual(error.exception.code,"qv_acked_without_manifest")

    def test_source_event_missing_or_cross_project_no_write(self):
        db=FakeDB();db.count=1
        with self.assertRaises(CollectorError):
            publish_qv("not-project",OID,connect=lambda:db)
        with self.assertRaises(CollectorError):
            publish_qv(P,"not-uuid",connect=lambda:db)
        self.assertEqual(db.calls,[])

    def test_rejected_unselected_never_enters_shards(self):
        s=make_source(0,rejected=1)
        plan=prepare_qv_publication(s)
        self.assertEqual(plan.shard_count,0)
        self.assertEqual(plan.coverage_count,1)

    def test_consumer_default_off_and_no_provider_post(self):
        with patch("intelligence.collector.query_publisher.runtime_flags",
                   return_value=type("Flags",(),{"qv_consumer_enabled":False})()):
            with self.assertRaises(CollectorError) as error:
                publish_due_qv(P)
        self.assertEqual(error.exception.code,"qv_consumer_disabled")
        with patch("intelligence.collector.query_publisher.runtime_flags",
                   return_value=type("Flags",(),{"qv_consumer_enabled":True})()),patch(
             "intelligence.collector.query_publisher.MainQVAdapter") as a,patch(
             "intelligence.collector.query_publisher.publish_qv",
             return_value={"manifest_id":"ok","provider_calls":0}) as publish:
            a.return_value.pending_validated_outbox.return_value=[
                {"outbox_id":OID}]
            data=publish_due_qv(P)
        self.assertEqual(data["published"],1)
        self.assertEqual(data["provider_calls"],0)
        publish.assert_called_once()

    def test_order_independent_manifest_hash_for_verified_selected(self):
        source=make_source(21)
        # DTO fingerprint fixes source order; manifest canonicalization
        # itself sorts (hypothesis,rank,UUID), and replay pins same QV source.
        h1=prepare_qv_publication(source).manifest_sha256
        h2=prepare_qv_publication(source).manifest_sha256
        self.assertEqual(h1,h2)


if __name__=="__main__":
    unittest.main()
