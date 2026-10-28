"""VC-MOD-10: D1 receipt-gated Worker completion and crash/restart QA.

Offline fake SQL. True concurrent PostgreSQL17 requires separately reported CI.
"""
from __future__ import annotations

from unittest import TestCase
from unittest.mock import patch
from intelligence.collector.contracts import CollectorError
from intelligence.collector import worker
from intelligence.collector.completion import finish_from_checkpoint
from test_video_collector_inspector_stage9 import TASK_ID, item, answer, search_task

CTX={"id":31,"run_id":77,"job_id":91,"request_id":61,
     "generation":5,"worker_id":"worker-17","provider_task_id":TASK_ID,
     "state":"submitted","source_task":search_task(),
     "prepared":{"params":{"keyword":"home fitness"}}}
PROJECT="a"*32
SHA="c"*64


class Cursor:
    def __init__(self,row=None,rows=None):
        self.row=row;self.rows=rows
    def fetchone(self): return self.row
    def fetchall(self): return self.rows or []


class DB:
    def __init__(self,*,checkpoint=True,fail_write=None,links=1,events=1,
                 page_videos=1,job_owner="worker-17",cancelled=False,
                 stage="released",job_state="running"):
        self.checkpoint=checkpoint
        self.fail_write=fail_write
        self.links=links;self.events=events;self.page_videos=page_videos
        self.job_owner=job_owner;self.cancelled=cancelled
        self.stage=stage;self.job_state=job_state
        self.calls=[];self.results=[]
        self.writes=[]
        self.page=(500,PROJECT,77,61,TASK_ID,SHA,1,0,1,0,1,{"overflow_count":0})
    def __enter__(self):return self
    def __exit__(self,*args):return False
    def transaction(self):
        class Tx:
            def __init__(self,db): self.db=db
            def __enter__(self):
                self.count=len(self.db.writes)
                return self.db
            def __exit__(self,t,e,tb):
                if t is not None:
                    del self.db.writes[self.count:]
                return False
        return Tx(self)
    def execute(self,sql,params=None):
        self.calls.append((sql,params))
        if self.fail_write and self.fail_write in sql:
            raise RuntimeError("postgres failed")
        if "SELECT cancel_requested FROM search_runs" in sql:
            return Cursor((self.cancelled,))
        if "SELECT project_id,stage FROM collector_runs" in sql:
            return Cursor((PROJECT,self.stage))
        if "SELECT t.state,t.provider_task_id,j.state,j.locked_by" in sql:
            return Cursor(("submitted",TASK_ID,self.job_state,
                           self.job_owner,5))
        if "SELECT id FROM jobs WHERE id=%s AND state='running'" in sql:
            return Cursor((91,) if self.job_owner=="worker-17" and self.job_state=="running"
                          else None)
        if "FROM collector_ingest_pages" in sql:
            return Cursor(rows=[self.page] if self.checkpoint else [])
        if "SELECT\n                  (SELECT count(*) FROM collector_ingest_page_videos" in sql:
            return Cursor((self.page_videos,self.links,self.events,1))
        if "UPDATE collector_provider_tasks SET state='completed'" in sql:
            self.writes.append(("provider",params))
            return Cursor((31,))
        if "UPDATE source_requests SET state='succeeded'" in sql:
            self.writes.append(("source",params))
            return Cursor((61,))
        if "UPDATE jobs SET state='completed'" in sql:
            self.writes.append(("job",params))
            return Cursor((91,))
        raise AssertionError("Unexpected SQL "+sql[:180])


def receipt():
    return {"d1_persisted":True,"provider_completed":False,"run_id":77,
            "source_request_id":61,"project_id":PROJECT,"page_id":500,
            "page_sha256":SHA,"saved_count":1}


class CompletionTests(TestCase):
    def complete(self,db,rcpt=None):
        with patch("intelligence.collector.worker._finalize") as final:
            result=finish_from_checkpoint(CTX,receipt=rcpt,connect=lambda:db)
        return result,final

    def test_missing_checkpoint_keeps_provider_submitted_without_any_completed_write(self):
        db=DB(checkpoint=False)
        result,final=self.complete(db)
        self.assertIsNone(result)
        self.assertEqual(db.writes,[])
        final.assert_not_called()

    def test_discovery_receipt_required_before_any_completion(self):
        db=DB(checkpoint=False)
        with self.assertRaises(CollectorError) as ex:
            self.complete(db,receipt())
        self.assertEqual(ex.exception.code,"ingest_checkpoint_missing")
        self.assertEqual(db.writes,[])

    def test_finish_updates_all_three_entities_atomically_only_after_receipt(self):
        db=DB()
        result,final=self.complete(db,receipt())
        self.assertEqual(result["status"],"completed")
        self.assertTrue(result["d1_persisted"])
        self.assertFalse(result["ingestion_pending"])
        self.assertFalse(result["replayed_checkpoint"])
        self.assertEqual(result["page_id"],500)
        self.assertEqual([key for key,_ in db.writes],["provider","source","job"])
        params=db.writes[0][1][0]
        self.assertIn('"metadata_persisted":true',params)
        self.assertIn('"ingestion_pending":false',params)
        self.assertIn('"page_id":500',params)
        self.assertNotIn('"raw"',params)
        final.assert_called_once()

    def test_crash_after_d1_commit_recovers_from_receipt_without_provider_get(self):
        class DTK:
            def __init__(self):self.gets=0;self.posts=0
            def task(self,*args):
                self.gets+=1
                raise AssertionError("Recovery must not GET a different page SHA")
            def submit(self,*args):
                self.posts+=1
                raise AssertionError("No second POST")
        api=DTK()
        db=DB()
        with patch("intelligence.collector.worker._assert_worker_config"),patch(
             "intelligence.collector.worker._claim_next",return_value=dict(CTX)),patch(
             "intelligence.collector.worker._finalize"):
            result=worker.process_one(worker_id="worker-17",provider=api,connect=lambda:db)
        self.assertEqual(result["status"],"completed")
        self.assertTrue(result["replayed_checkpoint"])
        self.assertEqual(api.gets,0)
        self.assertEqual(api.posts,0)
        self.assertEqual(len(db.writes),3)

    def test_cancelled_or_unreleased_run_never_completed(self):
        for spec in ({"cancelled":True},{"stage":"cancelled"},{"stage":"planned_held"}):
            with self.subTest(spec=spec):
                db=DB(**spec)
                with self.assertRaises(CollectorError):
                    self.complete(db,receipt())
                self.assertEqual(db.writes,[])

    def test_expired_or_reassigned_worker_lease_cannot_ack_committed_page(self):
        for spec in ({"job_owner":"new_owner"},{"job_state":"queued"}):
            with self.subTest(spec=spec):
                db=DB(**spec)
                with self.assertRaises(CollectorError) as e:
                    self.complete(db,receipt())
                self.assertEqual(e.exception.code,"collector_lost_lease")
                self.assertEqual(db.writes,[])

    def test_missing_video_or_qv_or_outbox_prevents_completed(self):
        for spec in ({"page_videos":0},{"links":0},{"links":2},{"events":0}):
            with self.subTest(spec=spec):
                db=DB(**spec)
                with self.assertRaises(CollectorError) as e:
                    self.complete(db,receipt())
                self.assertEqual(e.exception.code,"ingest_checkpoint_incomplete")
                self.assertEqual(db.writes,[])

    def test_digest_or_project_or_count_mismatch_is_not_replayed(self):
        for change in (
            {"page_sha256":"f"*64},{"page_id":91},
            {"project_id":"b"*32},{"saved_count":2},
        ):
            with self.subTest(change=change):
                db=DB()
                with self.assertRaises(CollectorError) as e:
                    self.complete(db,{**receipt(),**change})
                self.assertEqual(e.exception.code,"ingest_checkpoint_conflict")
                self.assertEqual(db.writes,[])

    def test_source_or_job_update_failure_rolls_back_all_terminal_state(self):
        for failure in (
            "UPDATE source_requests SET state='succeeded'",
            "UPDATE jobs SET state='completed'",
        ):
            with self.subTest(failure=failure):
                db=DB(fail_write=failure)
                with self.assertRaisesRegex(RuntimeError,"postgres failed"):
                    self.complete(db,receipt())
                self.assertEqual(db.writes,[])

    def test_direct_legacy_provider_done_is_forbidden_to_advance_status(self):
        with self.assertRaises(CollectorError) as ex:
            worker._update_after_provider(CTX,next_state="completed",
                                          summary={"accepted_count":99},
                                          connect=lambda:DB())
        self.assertEqual(ex.exception.code,"d1_receipt_required")

    def test_worker_inspection_error_does_not_call_d1_or_complete(self):
        class DTK:
            def submit(self,*args):raise AssertionError("No POST")
            def task(self,*args):
                return {"state":"done","task_id":TASK_ID,
                        "data":{"not_items":[]}}
        with patch("intelligence.collector.worker._assert_worker_config"),patch(
             "intelligence.collector.worker._claim_next",return_value=dict(CTX)),patch(
             "intelligence.collector.completion.finish_from_checkpoint",return_value=None),patch(
             "intelligence.collector.worker._update_after_provider",
             return_value=True) as terminal,patch(
             "intelligence.collector.ingest.ingest_page",
             side_effect=AssertionError("Invalid Inspector cannot ingest")):
            result=worker.process_one(worker_id="worker-17",provider=DTK())
        self.assertEqual(result["status"],"inspection_failed")
        self.assertEqual(terminal.call_args.kwargs["next_state"],"failed")

    def test_ingest_error_leaves_job_pending_for_get_only_retry(self):
        class DTK:
            def __init__(self):self.gets=0
            def submit(self,*a):raise AssertionError("No POST")
            def task(self,*a):
                self.gets+=1
                return answer([item()])
        api=DTK()
        with patch("intelligence.collector.worker._assert_worker_config"),patch(
             "intelligence.collector.worker._claim_next",return_value=dict(CTX)),patch(
             "intelligence.collector.completion.finish_from_checkpoint",
             return_value=None) as completion,patch(
             "intelligence.collector.ingest.ingest_page",
             side_effect=RuntimeError("PG transaction aborted")),patch(
             "intelligence.collector.worker._defer_poll",return_value=True) as defer:
            result=worker.process_one(worker_id="worker-17",provider=api)
        self.assertEqual(result["status"],"ingest_pending")
        self.assertFalse(result["d1_persisted"])
        self.assertEqual(api.gets,1)
        completion.assert_called_once()
        self.assertEqual(defer.call_args.kwargs["error_code"],"ingest_pending")

    def test_d1_committed_then_terminal_ack_lost_is_not_falsely_completed(self):
        class DTK:
            def submit(self,*a):raise AssertionError("No POST")
            def task(self,*a):return answer([item()])
        rcpt=receipt()
        with patch("intelligence.collector.worker._assert_worker_config"),patch(
             "intelligence.collector.worker._claim_next",return_value=dict(CTX)),patch(
             "intelligence.collector.completion.finish_from_checkpoint",
             side_effect=[None,RuntimeError("lost DB ack")]),patch(
             "intelligence.collector.ingest.ingest_page",return_value=rcpt):
            result=worker.process_one(worker_id="worker-17",provider=DTK())
        self.assertEqual(result["status"],"ingest_pending")
        self.assertTrue(result["d1_persisted"])
        self.assertTrue(result["ingestion_pending"])

    def test_falsely_completed_historical_rows_without_d1_receipts_are_blocked(self):
        class DB:
            def __init__(self): self.calls=[]
            def execute(self,sql,args=None):
                self.calls.append((sql,args))
                if "GROUP BY state" in sql:return Cursor(rows=[("completed",1)])
                if "NOT EXISTS" in sql:return Cursor((1,))
                if "cancel_requested" in sql:return Cursor((False,))
                return Cursor((1,))
        db=DB()
        worker._finalize(db,77)
        self.assertTrue(any("SET state='blocked'" in sql for sql,_ in db.calls))
        self.assertFalse(any("SET state='completed'" in sql for sql,_ in db.calls))

if __name__=="__main__":
    import unittest
    unittest.main()
