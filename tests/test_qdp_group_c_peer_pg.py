"""FIX-14 real pinned Group C consumer, only on disposable PG17 with C 056."""
import unittest
from tests.test_qualified_data_db import QualifiedDataDatabaseTests
from intelligence.qualified_data.repository import create_run,work_once
from intelligence.qualified_data.outbox_delivery import dispatch_once
from intelligence.qualified_data.integrations.group_c import from_installed_group_c
from intelligence.qualified_data.integrations.contracts import QDPHandoffPointer


class RealGroupCInbox(QualifiedDataDatabaseTests):
    def test_real_group_c_one_inbox_one_score_job_and_producer_ack(self):
        ids,config=self._insert_c_metric_inputs()
        run=create_run(self.pid,self.sre_run,self.key,connect=self.factory)
        result=work_once(connect=self.factory)
        self.assertIsNotNone(result)
        with self.factory() as conn:
            e=conn.execute(
                """SELECT o.event_id,o.payload_sha256,p.input_sha256
                   FROM qdp_outbox o JOIN qdp_preparations p
                    ON p.run_id=o.run_id AND p.project_id=o.project_id
                    AND p.video_id=o.video_id AND p.analysis_group='C'
                  WHERE o.run_id=%s::uuid AND o.project_id=%s
                    AND o.analysis_group='C' AND o.event_type='preparation_ready'""",
                (run["run_id"],self.pid)).fetchone()
            self.assertIsNotNone(e)
            ptr=QDPHandoffPointer(
                event_id=str(e["event_id"]),project_id=self.pid,
                qdp_run_id=run["run_id"],video_id=self.vid,
                analysis_group="C",input_sha256=e["input_sha256"])
        receiver=from_installed_group_c(self.factory,enabled=True)
        ack=dispatch_once("group_c",receiver=receiver,connect=self.factory)
        self.assertEqual(ack["state"],"acknowledged")
        self.assertTrue(ack["receipt_key"].startswith("gc:"))
        with self.factory() as conn:
            count=lambda sql: conn.execute(sql,(self.pid,)).fetchone()["n"]
            self.assertEqual(count(
                "SELECT count(*) AS n FROM group_c_inbox WHERE project_id=%s"),1)
            self.assertEqual(count(
                "SELECT count(*) AS n FROM group_c_runs WHERE project_id=%s"),1)
            self.assertEqual(count(
                "SELECT count(*) AS n FROM group_c_jobs WHERE project_id=%s"),1)
            state=conn.execute(
                """SELECT state,calculated_metric_id FROM group_c_runs
                   WHERE project_id=%s""",(self.pid,)).fetchone()
            self.assertEqual(state["state"],"queued")
            self.assertIsNone(state["calculated_metric_id"])
            self.assertEqual(conn.execute(
                """SELECT count(*) AS n FROM qdp_delivery_receipts
                    WHERE event_id=%s::uuid AND consumer='group_c'""",
                (ptr.event_id,)).fetchone()["n"],1)
        for i in range(30):
            same=receiver.receive(ptr,e["payload_sha256"])
            self.assertEqual(same["state"],"committed")
            self.assertEqual(same["receipt_key"],ack["receipt_key"])
        with self.factory() as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) AS n FROM group_c_jobs WHERE project_id=%s",
                (self.pid,)).fetchone()["n"],1)
        self.assertIsNone(dispatch_once("group_c",receiver=receiver,connect=self.factory))
        with self.factory() as conn:
            conn.execute("UPDATE sources SET can_derive_scores=false WHERE id=%s",
                         (self.source,))
        with self.assertRaises(Exception):
            receiver.receive(ptr,e["payload_sha256"])
        with self.factory() as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) AS n FROM group_c_jobs WHERE project_id=%s",
                (self.pid,)).fetchone()["n"],1)
