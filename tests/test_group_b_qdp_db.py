"""GB-MOD-04: disposable PG17 transaction tests with a stub QDP reader.

These verify REAL Group B tables, FK constraints, rollback and ACK ordering.
They DO NOT simulate that an actual QDP producer/dispatcher is operational.
"""
from __future__ import annotations

import os
import unittest
from unittest.mock import patch
from uuid import uuid4

from intelligence.group_b.adapters.qdp import VerifiedPreparation
from intelligence.group_b.contracts import ContractError, canonical_hash
from intelligence.group_b.errors import DependencyUnavailable, DependencyProbeFailed
from intelligence.group_b.intake import receive_qdp_delivery
from intelligence.migrate import apply_migrations


def _test_dsn():
    from psycopg.conninfo import conninfo_to_dict
    uri = os.environ["CM_TEST_PG_DSN"]
    dbname = conninfo_to_dict(uri).get("dbname","").lower()
    if "test" not in dbname and "_ci" not in dbname:
        raise RuntimeError("Refusing non-disposable database")
    return uri


class Ack:
    def __init__(self, dsn, fail=False):
        self.dsn, self.fail, self.calls = dsn, fail, []
    def acknowledge(self, *, event_id, durable_receipt):
        import psycopg
        # An ACK must see a COMMITTED receipt from a SEPARATE connection.
        with psycopg.connect(self.dsn) as conn:
            row = conn.execute(
                "SELECT count(*) FROM group_b_inbox_receipts "
                "WHERE event_id=%s::uuid AND receipt_key=%s",
                (event_id, durable_receipt)
            ).fetchone()
        assert row == (1,), "ACK attempted before receipt commit"
        self.calls.append((event_id, durable_receipt))
        if self.fail:
            raise RuntimeError("sensitive transport response")


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"), "No disposable PG17 DSN")
class GroupBQDPPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg = psycopg
        cls.dsn = _test_dsn()
        with psycopg.connect(cls.dsn) as conn:
            apply_migrations(conn)

    def setUp(self):
        self.project = uuid4().hex
        self.source = "bqdp-test-" + uuid4().hex
        self.video_key = uuid4().hex
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                """INSERT INTO project_registry(
                   id,name,profile_revision,profile_hash,created_at,updated_at)
                   VALUES (%s,'Group B QDP test',1,%s,now(),now())""",
                (self.project,"a"*64))
            conn.execute(
                """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
                   VALUES (%s,'test','owned','enabled',TRUE)""",
                (self.source,))
            self.policy = conn.execute(
                """INSERT INTO source_policy_versions(source_id,version,is_active,permissions)
                   VALUES (%s,1,TRUE,'{"can_analyze_media":true}'::jsonb)
                   RETURNING id""",(self.source,)
            ).fetchone()[0]
            self.video = conn.execute(
                """INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
                   VALUES ('tiktok',%s,%s,'video') RETURNING id""",
                (self.video_key,"https://example.test/video/"+self.video_key)
            ).fetchone()[0]
            self.asset = conn.execute(
                """INSERT INTO media_assets(video_id,source_id,media_kind,local_path,sha256,permission)
                   VALUES (%s,%s,'video','/nonexistent/mock-not-used.mp4',%s,'analysis')
                   RETURNING id""",
                (self.video,self.source,"c"*64)
            ).fetchone()[0]
        self.event = {
            "event_id":str(uuid4()),"project_id":self.project,
            "qdp_run_id":str(uuid4()),"video_id":self.video,"analysis_group":"B",
            "input_sha256":"d"*64,"schema_version":"qdp-handoff/v1",
        }
        self.announced = canonical_hash(self.event)
        self.prep = VerifiedPreparation(
            self.project,self.event["event_id"],self.event["qdp_run_id"],
            self.video,self.source,"video","video",self.event["input_sha256"],
            self.announced,self.policy,self.asset,"c"*64
        )

    def receive(self, *, payload=None, digest=None, ack=None, verified=None):
        with patch("intelligence.group_b.intake.read_current_preparation",
                   return_value=verified or self.prep):
            return receive_qdp_delivery(
                lambda:self.psycopg.connect(self.dsn),
                payload or self.event, digest or self.announced, acknowledger=ack,
            )

    def test_durable_inbox_eight_blocks_replay_and_post_commit_ack(self):
        ack=Ack(self.dsn)
        first=self.receive(ack=ack)
        self.assertTrue(first.newly_created)
        self.assertTrue(first.acknowledged)
        self.assertFalse(first.execution_ready)
        again=self.receive(ack=ack)
        self.assertFalse(again.newly_created)
        self.assertEqual(first.run_id,again.run_id)
        self.assertEqual(first.inbox_id,again.inbox_id)
        self.assertEqual(len(ack.calls),2)
        with self.psycopg.connect(self.dsn) as conn:
            run=conn.execute(
                "SELECT state,video_a_analysis_id,carousel_a_analysis_id "
                "FROM group_b_runs WHERE id=%s::uuid",(first.run_id,)
            ).fetchone()
            self.assertEqual(run,("draft",None,None))
            blocks=conn.execute(
                "SELECT block,status FROM group_b_block_states "
                "WHERE project_id=%s AND run_id=%s::uuid ORDER BY block",
                (self.project,first.run_id)
            ).fetchall()
            self.assertEqual(blocks,[(f"B{i}","not_run") for i in range(1,9)])
            rows=conn.execute(
                "SELECT count(*) FROM group_b_inbox_receipts WHERE event_id=%s::uuid",
                (self.event["event_id"],)
            ).fetchone()[0]
            self.assertEqual(rows,1)

    def test_changed_hash_quarantine_never_ack(self):
        first=self.receive()
        ack=Ack(self.dsn)
        with self.assertRaises(ContractError) as err:
            self.receive(digest="e"*64,ack=ack)
        self.assertEqual(err.exception.code,"event_hash_conflict")
        self.assertEqual(ack.calls,[])
        with self.psycopg.connect(self.dsn) as conn:
            row=conn.execute(
                "SELECT reason_code,expected_sha256,received_sha256 "
                "FROM group_b_inbox_conflicts WHERE project_id=%s",
                (self.project,)
            ).fetchone()
        self.assertEqual(row,("event_hash_conflict",self.announced,"e"*64))

    def test_invalid_rights_rollback_creates_no_inbox_or_ack(self):
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute("UPDATE sources SET can_analyze_media=FALSE WHERE id=%s",(self.source,))
        ack=Ack(self.dsn)
        with self.assertRaises(DependencyUnavailable):
            self.receive(ack=ack)
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM group_b_inbox WHERE project_id=%s",
                (self.project,)
            ).fetchone()[0],0)
        self.assertEqual(ack.calls,[])

    def test_identity_substitution_rollback(self):
        from dataclasses import replace
        wrong=replace(self.prep,project_id=uuid4().hex)
        with self.assertRaises(ContractError) as err:
            self.receive(verified=wrong)
        self.assertEqual(err.exception.code,"qdp_identity_mismatch")
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute(
                "SELECT count(*) FROM group_b_inbox WHERE project_id=%s",
                (self.project,)
            ).fetchone()[0],0)

    def test_upstream_ack_failure_retains_local_receipt(self):
        ack=Ack(self.dsn,fail=True)
        with self.assertRaises(DependencyProbeFailed):
            self.receive(ack=ack)
        self.assertEqual(len(ack.calls),1)
        again=self.receive()
        self.assertFalse(again.newly_created)
        self.assertFalse(again.acknowledged)

    def test_receipt_cannot_be_mutated_or_moved_across_projects(self):
        first=self.receive()
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(Exception):
                with conn.transaction():
                    conn.execute(
                        "DELETE FROM group_b_inbox_receipts WHERE project_id=%s",
                        (self.project,))
            with self.assertRaises(Exception):
                with conn.transaction():
                    conn.execute(
                        "UPDATE group_b_inbox_receipts SET receipt_key='hijacked!' "
                        "WHERE project_id=%s",(self.project,))


if __name__=="__main__":
    unittest.main()
