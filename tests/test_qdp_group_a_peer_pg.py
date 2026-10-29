"""FIX-13 actual Group A GA-MOD-04 durable inbox in a COMBINED disposable DB.

This is an opt-in GitHub CI peer job. Never run on a production DB, and never
copy peer code into QDP's owned sources. Workflow temporarily checks out GA's
exact commit in a separate directory and installs its own 054/055 migrations.

Run specifically:
 python -m unittest tests.test_qdp_group_a_peer_pg.PeerQDPGroupAHandoff.test_real_peer_inbox_job_ack_and_fifty_replays -v
"""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tests.test_qualified_data_db import QualifiedDataDatabaseTests
from intelligence.qualified_data.integrations.group_a import from_installed_group_a
from intelligence.qualified_data.outbox_delivery import dispatch_once
from intelligence.qualified_data.repository import create_run,work_once

class PeerQDPGroupAHandoff(QualifiedDataDatabaseTests):
    def test_real_peer_inbox_job_ack_and_fifty_replays(self):
        # The consumer is GA's actual GroupAInboxReceiver+VerifiedMediaCatalog,
        # not the qdp_test_consumer_jobs simulation from FIX-12.
        import psycopg
        self._media_video_source("video")
        with tempfile.TemporaryDirectory() as root:
            local=Path(root)/"real-asset.mp4"
            content=b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00"+bytes(range(64))
            local.write_bytes(content)
            asset_id=self._media_asset(local,permission="analysis_allowed")
            # GA's cursor contract uses tuple rows while the QDP worker
            # uses dict_row. Both factories target the SAME disposable DB.
            ga_factory=lambda:psycopg.connect(
                os.environ["CM_TEST_PG_DSN"],autocommit=True)
            ga_receiver=from_installed_group_a(
                ga_factory,media_roots=[root],enabled=True)
            with patch.dict(os.environ,{"CM_QDP_MEDIA_ROOTS":root}):
                q=create_run(self.pid,self.sre_run,self.key,connect=self.factory)
                completed=work_once(connect=self.factory)
                self.assertIsNotNone(completed)
                with self.factory() as conn:
                    prepared=conn.execute(
                        """SELECT state,inputs FROM qdp_preparations
                           WHERE run_id=%s::uuid AND analysis_group='A'""",
                        (q["run_id"],)).fetchone()
                    self.assertEqual(prepared["state"],"ready")
                    self.assertEqual(prepared["inputs"]["media_asset_id"],asset_id)
                actual=dispatch_once(
                    "group_a",receiver=ga_receiver,connect=self.factory,
                    worker_id="ga-real-peer-accept-1")
                self.assertEqual(actual["state"],"acknowledged")
                self.assertTrue(actual["receipt_key"].startswith("ga:"))
                with self.factory() as conn:
                    out=conn.execute(
                        """SELECT o.event_id,o.payload_sha256,p.input_sha256
                           FROM qdp_outbox o JOIN qdp_preparations p
                           ON p.run_id=o.run_id AND p.video_id=o.video_id
                              AND p.analysis_group=o.analysis_group
                           WHERE o.run_id=%s::uuid AND o.analysis_group='A'
                             AND o.event_type='preparation_ready'""",
                        (q["run_id"],)).fetchone()
                    ga_rows=conn.execute(
                        """SELECT count(*) AS n FROM ga_inbox WHERE project_id=%s""",
                        (self.pid,)).fetchone()["n"]
                    ga_steps=conn.execute(
                        """SELECT count(*) AS n FROM ga_steps
                           WHERE project_id=%s""",(self.pid,)).fetchone()["n"]
                    ga_runs=conn.execute(
                        """SELECT count(*) AS n FROM ga_runs WHERE project_id=%s""",
                        (self.pid,)).fetchone()["n"]
                    self.assertEqual((ga_rows,ga_steps,ga_runs),(1,1,1))
                    receipt_count=conn.execute(
                        """SELECT count(*) AS n FROM qdp_delivery_receipts
                           WHERE event_id=%s::uuid AND consumer='group_a'""",
                        (str(out["event_id"]),)).fetchone()["n"]
                    self.assertEqual(receipt_count,1)
                from intelligence.qualified_data.integrations.contracts import QDPHandoffPointer
                pointer=QDPHandoffPointer(
                    event_id=str(out["event_id"]),project_id=self.pid,
                    qdp_run_id=q["run_id"],video_id=self.vid,
                    analysis_group="A",input_sha256=out["input_sha256"])
                for i in range(50):
                    same=ga_receiver.receive(pointer,out["payload_sha256"])
                    self.assertEqual(same["state"],"committed")
                    self.assertEqual(same["receipt_key"],actual["receipt_key"])
                self.assertIsNone(dispatch_once(
                    "group_a",receiver=ga_receiver,connect=self.factory))
                with self.factory() as conn:
                    self.assertEqual(conn.execute(
                        """SELECT count(*) AS n FROM ga_inbox WHERE project_id=%s""",
                        (self.pid,)).fetchone()["n"],1)
                    self.assertEqual(conn.execute(
                        """SELECT count(*) AS n FROM ga_steps WHERE project_id=%s""",
                        (self.pid,)).fetchone()["n"],1)

if __name__=="__main__":
    unittest.main()
