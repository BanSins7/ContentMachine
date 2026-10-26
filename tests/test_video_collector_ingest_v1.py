"""VC-MOD-09: atomic canonical D1 page transactions (offline DB simulation).

The fake models transaction rollback and unique keys. It is NOT a substitute
for PostgreSQL17 integration/concurrent writer tests; exact-HEAD CI pending.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from intelligence.collector.contracts import CollectorError
from intelligence.collector.ingest import ingest_page
from test_video_collector_inspector_stage9 import (
    TASK_ID, FIXED_NOW, ITEM_ID, inspect, item, answer,
)
from intelligence.collector.provider_inspector import InspectedPage

PID="a"*32
RUN=77
JOB=91
REQUEST=61
TASK=31
OTHER="b"*32


def worker_context():
    return {"id":TASK,"run_id":RUN,"job_id":JOB,"request_id":REQUEST,
            "worker_id":"collector-test","generation":5,
            "state":"submitted","provider_task_id":TASK_ID}


class Rows:
    def __init__(self,one=None,many=None):
        self.one=one
        self.many=many or []
    def fetchone(self):
        return self.one
    def fetchall(self):
        return list(self.many)


class Transaction:
    def __init__(self,db):self.db=db
    def __enter__(self):
        self.before=deepcopy(self.db.store)
        return self.db
    def __exit__(self,kind,exc,tb):
        if kind is not None:
            self.db.store=self.before
        return False


class FakeDB:
    def __init__(self):
        self.project=PID
        self.cancel=False
        self.lease=True
        self.policy=True
        self.fail=None
        self.calls=[]
        self.store={
            "videos":{},"metadata":{},"discoveries":{},"pages":{},
            "page_videos":[],"hypotheses":[],"page_queries":[],
            "sre_events":[],
        }
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def transaction(self):return Transaction(self)
    def execute(self,sql,params=None):
        self.calls.append((sql,params))
        if self.fail and self.fail in sql:
            raise RuntimeError("Simulated crash before commit")
        d=self.store
        if "FROM search_runs WHERE id=%s FOR UPDATE" in sql:
            return Rows((RUN,self.cancel))
        if "SELECT project_id,stage,policy_version_id" in sql:
            return Rows((self.project,"released",12))
        if "SELECT t.provider_task_id,t.state,j.state" in sql:
            return Rows((TASK_ID,"submitted","running","collector-test",5,
                datetime(2026,10,26,tzinfo=timezone.utc),12,"dtk_tiktok",
                "11111111-1111-4111-8111-111111111111",
                "22222222-2222-4222-8222-222222222222",
                "11111111-1111-4111-8111-111111111111",
                "c"*64,"dtk_tiktok",True,"enabled",
                True,FIXED_NOW,{"can_store_metadata":self.policy,
                                "automated_collection_authorized":True,
                                "upstream_archive_disabled_confirmed":True}))
        if "SELECT 1 FROM jobs WHERE" in sql:
            return Rows((1,) if self.lease else None)
        if "FROM collector_task_queries" in sql:
            return Rows(many=[
                ("query_home","hyp_home","dir_home"),
            ])
        if "FROM collector_ingest_pages" in sql and "provider_task_row_id" in sql:
            value=d["pages"].get(TASK)
            return Rows(value)
        if "COUNT(*) FROM collector_ingest_page_videos" in sql:
            return Rows((len(d["page_videos"]),))
        if "COUNT(*) FROM collector_page_video_queries" in sql:
            return Rows((len(d["page_queries"]),))
        if "COUNT(*) FROM collector_sre_outbox" in sql:
            return Rows((len(d["sre_events"]),))
        if "INSERT INTO videos" in sql:
            vid=params[0]
            if vid in d["videos"]:
                return Rows()
            next_id=100+len(d["videos"])
            d["videos"][vid]=next_id
            return Rows((next_id,))
        if "SELECT id FROM videos WHERE" in sql:
            vid=d["videos"].get(params[0])
            return Rows((vid,) if vid is not None else None)
        if "INSERT INTO collector_ingest_pages" in sql:
            # 057 accepts immutable counters in the initial INSERT.
            page_id=700+len(d["pages"])
            d["pages"][TASK]=(
                page_id,self.project,REQUEST,TASK_ID,params[6],params[7],
                params[9],params[14],params[15],params[17],
                params[16],params[18],
            )
            d["receipt_params"]=params
            return Rows((page_id,))
        if "INSERT INTO source_video_metadata" in sql:
            key=(params[0],params[2])
            if key in d["metadata"]:
                return Rows()
            mid=500+len(d["metadata"])
            d["metadata"][key]=mid
            return Rows((mid,))
        if "SELECT id FROM source_video_metadata" in sql:
            mid=d["metadata"].get((params[0],params[1]))
            return Rows((mid,) if mid else None)
        if "INSERT INTO discoveries" in sql:
            key=params[4]
            if key in d["discoveries"]:
                return Rows()
            did=600+len(d["discoveries"])
            d["discoveries"][key]=(did,params[0])
            return Rows((did,))
        if "SELECT id,video_id FROM discoveries" in sql:
            return Rows(d["discoveries"].get(params[0]))
        if "INSERT INTO collector_ingest_page_videos" in sql:
            d["page_videos"].append(params)
            return Rows()
        if "INSERT INTO collector_video_hypotheses" in sql:
            if params not in d["hypotheses"]:d["hypotheses"].append(params)
            return Rows()
        if "INSERT INTO collector_page_video_queries" in sql:
            d["page_queries"].append(params)
            return Rows()
        if "INSERT INTO collector_sre_outbox" in sql:
            payload=json.loads(params[-1])
            d["sre_events"].append(payload)
            return Rows()
        raise AssertionError("Unrecognized SQL "+sql[:130])


class IngestTests(unittest.TestCase):
    def test_page_commits_all_d1_associations_and_pending_sre(self):
        db=FakeDB()
        with patch("intelligence.connectors.dtk.submit",
                   side_effect=AssertionError("DTK POST forbidden")):
            result=ingest_page(worker_context(),inspect(),connect=lambda:db)
        self.assertEqual(result["schema_version"],"collector.ingest_receipt/v1")
        self.assertEqual(result["accepted_count"],1)
        self.assertEqual(result["saved_count"],1)
        self.assertEqual(result["inserted_count"],1)
        self.assertFalse(result["replayed"])
        self.assertFalse(result["provider_completed"])
        self.assertFalse(result["metrics_persisted"])
        self.assertEqual(len(db.store["videos"]),1)
        self.assertEqual(len(db.store["metadata"]),1)
        self.assertEqual(len(db.store["discoveries"]),1)
        self.assertEqual(len(db.store["page_videos"]),1)
        self.assertEqual(len(db.store["hypotheses"]),1)
        self.assertEqual(len(db.store["page_queries"]),1)
        self.assertEqual(len(db.store["sre_events"]),1)
        event=db.store["sre_events"][0]
        self.assertEqual(event["relevance_state"],"not_evaluated")
        self.assertEqual(event["evidence_scope"],"metadata_only")
        self.assertEqual(event["candidate_ids"],["query_home"])
        self.assertEqual(event["hypothesis_ids"],["hyp_home"])
        self.assertEqual(event["page_sha256"],result["page_sha256"])
        self.assertFalse(any("INSERT INTO metric_snapshots" in sql for sql,_ in db.calls))
        self.assertFalse(any("UPDATE collector_ingest_pages" in sql for sql,_ in db.calls))
        self.assertFalse(any("UPDATE collector_provider_tasks" in sql for sql,_ in db.calls))

    def test_identical_replay_yields_receipt_no_writes(self):
        db=FakeDB()
        first=ingest_page(worker_context(),inspect(),connect=lambda:db)
        count=len(db.calls)
        again=ingest_page(worker_context(),inspect(),connect=lambda:db)
        self.assertTrue(again["replayed"])
        self.assertEqual(again["page_id"],first["page_id"])
        self.assertEqual(again["page_sha256"],first["page_sha256"])
        self.assertEqual(len(db.store["videos"]),1)
        self.assertEqual(len(db.store["sre_events"]),1)
        self.assertFalse(any("INSERT INTO " in sql for sql,_ in db.calls[count:]))

    def test_same_task_changed_page_sha_rejected_without_touching_d1(self):
        db=FakeDB()
        ingest_page(worker_context(),inspect(),connect=lambda:db)
        different=item()
        different["title"]="Changed provider result"
        with self.assertRaises(CollectorError) as e:
            ingest_page(worker_context(),inspect(answer([different])),connect=lambda:db)
        self.assertEqual(e.exception.code,"ingest_checkpoint_conflict")
        self.assertEqual(len(db.store["videos"]),1)
        self.assertEqual(len(db.store["sre_events"]),1)

    def test_empty_and_nonvideo_only_page_has_immutable_zero_receipt(self):
        for payload in (answer([]),answer([dict(item(),kind="live")])):
            with self.subTest(empty=not payload["data"]["items"]):
                db=FakeDB()
                result=ingest_page(worker_context(),inspect(payload),connect=lambda:db)
                self.assertEqual(result["saved_count"],0)
                self.assertEqual(result["inserted_count"],0)
                self.assertEqual(result["sre_events"],0)
                self.assertEqual(db.store["videos"],{})
                self.assertEqual(db.store["sre_events"],[])

    def test_same_video_preexisting_in_d1_is_duplicate_without_overwrite(self):
        db=FakeDB()
        db.store["videos"][ITEM_ID]=20
        result=ingest_page(worker_context(),inspect(),connect=lambda:db)
        self.assertEqual(result["inserted_count"],0)
        self.assertEqual(result["duplicate_count"],1)
        self.assertEqual(len(db.store["page_videos"]),1)
        self.assertEqual(len(db.store["sre_events"]),1)

    def test_transaction_rollback_on_metadata_or_outbox_error(self):
        for failure in ("INSERT INTO source_video_metadata",
                        "INSERT INTO collector_sre_outbox",
                        "INSERT INTO collector_page_video_queries"):
            with self.subTest(failure=failure):
                db=FakeDB()
                db.fail=failure
                with self.assertRaisesRegex(RuntimeError,"Simulated crash"):
                    ingest_page(worker_context(),inspect(),connect=lambda:db)
                for key in ("videos","metadata","discoveries","pages"):
                    self.assertFalse(db.store[key], key)
                self.assertEqual(db.store["sre_events"],[])

    def test_project_cancellation_policy_and_lease_gate_before_canonical_sql(self):
        for reason in ("cancel","project","policy","lease"):
            with self.subTest(reason=reason):
                db=FakeDB()
                if reason=="cancel":db.cancel=True
                elif reason=="project":db.project=OTHER
                elif reason=="policy":db.policy=False
                else:db.lease=False
                with self.assertRaises(CollectorError):
                    ingest_page(worker_context(),inspect(),connect=lambda:db)
                self.assertEqual(db.store["videos"],{})
                self.assertFalse(any("INSERT INTO videos" in s for s,_ in db.calls))

    def test_page_ordinal_and_untrusted_inputs_rejected_before_database(self):
        with self.assertRaises(CollectorError):
            ingest_page(worker_context(),{"data":"raw"},connect=lambda:FakeDB())
        with self.assertRaises(CollectorError):
            ingest_page(worker_context(),inspect(),page_ordinal=1,connect=lambda:FakeDB())
        with self.assertRaises(CollectorError):
            ingest_page(dict(worker_context(),state="not_submitted"),inspect(),
                        connect=lambda:FakeDB())
        with self.assertRaises(CollectorError):
            ingest_page(dict(worker_context(),provider_task_id="fake"),inspect(),
                        connect=lambda:FakeDB())

    def test_overflow_rejected_excludes_unexamined_from_d1_sql_counter(self):
        db=FakeDB()
        rows=[item(str(7250000000000000000+i)) for i in range(3)]
        from test_video_collector_inspector_stage9 import search_task
        outcome=inspect(answer(rows),task=search_task(max_results_per_page=1))
        result=ingest_page(worker_context(),outcome,connect=lambda:db)
        self.assertEqual(result["accepted_count"],1)
        self.assertEqual(result["rejected_count"],0)
        self.assertEqual(result["overflow_count"],2)
        params=db.store["receipt_params"]
        self.assertEqual(params[12],3) # raw count
        self.assertEqual(params[13],1) # examined
        self.assertEqual(params[14],1) # accepted
        self.assertEqual(params[15],0) # rejected from examined, excludes overflow

    def test_all_video_rows_have_exact_qv_provenance_without_uuid_to_bigint(self):
        db=FakeDB()
        result=ingest_page(worker_context(),inspect(),connect=lambda:db)
        q=db.store["page_queries"][0]
        self.assertEqual(q[-1],"query_home")
        h=db.store["hypotheses"][0]
        self.assertEqual(h[2],"query_home")
        self.assertEqual(h[3],"hyp_home")
        self.assertEqual(result["source_request_id"],REQUEST)
        self.assertEqual(db.store["sre_events"][0]["source_request_id"],str(REQUEST))


if __name__=="__main__":
    unittest.main()
