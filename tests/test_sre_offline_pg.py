"""S4 real PG17 E2E: two-review RTB -> D1 -> queued offline fake -> results.

No network or paid SRE calls. Reuses deterministic RTB provider fixture only.
Run only with disposable CM_TEST_PG_DSN.
"""
from __future__ import annotations
import os
import unittest
from uuid import uuid4

from intelligence import migrate
from intelligence.relevance import repository
from intelligence.relevance.intake import attach_discovery
from intelligence.relevance.offline_runs import (
    create_offline_run, get_offline_run, cancel_offline_run,
)
from intelligence.relevance.offline_worker import (
    claim_item, finish_item, work_once, heartbeat, reap_exhausted,
)
from intelligence.relevance.presets import strong_only
from intelligence.relevance.v1_contracts import SREContractError


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"disposable PostgreSQL required")
class S4OfflinePostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.dsn=os.environ["CM_TEST_PG_DSN"]
        with psycopg.connect(cls.dsn,autocommit=True) as conn:
            migrate.apply_migrations(conn)

    def setUp(self):
        import psycopg
        from test_taxonomy_deep_db import DeepDB
        from intelligence.taxonomy_builder.deep_repository import (
            advance_deep, first_review, final_review, get_deep,
        )
        self.fixture=DeepDB("test_complete_two_human_reviews_saturation_and_atomic_sre_tree")
        self.fixture.dsn=self.dsn
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.pid=self.fixture.project["id"]
        self.fixture.prepare()
        rid=self.fixture.rid
        self.assertEqual(advance_deep(self.pid,rid,sender=self.fixture.sender)["status"],"review1")
        first=get_deep(self.pid,rid)["nodes"]
        first_review(self.pid,rid,[{"id":n["id"],"action":"approve"} for n in first])
        self.assertEqual(advance_deep(self.pid,rid,sender=self.fixture.sender)["status"],"expand_pending")
        self.assertEqual(advance_deep(self.pid,rid,sender=self.fixture.sender)["status"],"review2")
        final=get_deep(self.pid,rid)["nodes"]
        self.assertEqual(final_review(self.pid,rid,[{"id":n["id"],"action":"approve"} for n in final])
                         ["status"],"approved")
        self.conn=psycopg.connect(self.dsn)
        self.addCleanup(self.conn.close)
        self.pub=str(self.conn.execute(
            "SELECT builder_publication_id FROM tb_deep_publications WHERE project_id=%s AND run_id=%s::uuid",
            (self.pid,rid)).fetchone()[0])
        self.pipeline=repository.save_pipeline(self.conn,self.pid,"offline-strong",strong_only(),
                                               "owner",activate=False)
        self.source="s4-local-"+uuid4().hex
        self.conn.execute(
            """INSERT INTO sources(id,provider,source_type,status,can_store_metadata,
               can_analyze_media,retention_days)
               VALUES(%s,'offline_test','manual','enabled',TRUE,FALSE,30)""",
            (self.source,))
        self.policy=self.conn.execute(
            """INSERT INTO source_policy_versions
               (source_id,version,is_active,permissions,retention_policy,reviewed_at)
               VALUES (%s,1,TRUE,
               '{"can_store_metadata":true,"can_semantic_classify":true}'::jsonb,
               '{"retention_days":20}'::jsonb,now()) RETURNING id""",
            (self.source,)).fetchone()[0]
        self.conn.commit()

    def add_video(self, title):
        name=uuid4().hex[:11]
        vid=self.conn.execute(
            """INSERT INTO videos(platform,platform_video_id,canonical_url)
               VALUES ('youtube',%s,%s) RETURNING id""",
            (name,"https://www.youtube.com/watch?v="+name)).fetchone()[0]
        did=self.conn.execute(
            "INSERT INTO discoveries(video_id,source_id,method) VALUES(%s,%s,'manual_import') RETURNING id",
            (vid,self.source)).fetchone()[0]
        self.conn.execute(
            """INSERT INTO source_video_metadata
               (video_id,source_id,source_policy_version_id,fetched_at,title,is_current)
               VALUES (%s,%s,%s,now(),%s,TRUE)""",
            (vid,self.source,self.policy,title))
        self.conn.commit()
        return attach_discovery(self.conn,self.pid,did)

    def test_full_pinned_run_replay_multi_label_and_run_immutability(self):
        one=self.add_video("Проверка понятности фотоучёта")
        two=self.add_video("Обманчивые лечебные обещания")
        ids=[one["content_version_id"],two["content_version_id"]]
        new=create_offline_run(self.conn,self.pid,self.pub,ids,self.pipeline,"run-qa-1")
        run=new["run_id"]
        self.assertEqual(new["items"],2)
        replay=create_offline_run(self.conn,self.pid,self.pub,ids,self.pipeline,"run-qa-1")
        self.assertEqual(replay["run_id"],run)
        self.assertTrue(replay["replayed"])
        with self.assertRaises(SREContractError):
            create_offline_run(self.conn,self.pid,self.pub,ids[:1],self.pipeline,"run-qa-1")
        while work_once(self.conn):
            pass
        detail=get_offline_run(self.conn,self.pid,run)
        self.assertEqual(detail["status"],"completed")
        self.assertEqual({i["state"] for i in detail["items"]},{"completed"})
        rows=self.conn.execute(
            """SELECT video_id,decision,evidence,stage_trace
               FROM sre_classification_results
               WHERE run_id=%s::uuid ORDER BY video_id,category_key""",
            (run,)).fetchall()
        self.assertEqual(len(rows),6)
        self.assertEqual(sum(x[1]=="match" for x in rows),1)
        self.assertEqual(sum(x[1]=="no_match" for x in rows),3)
        self.assertEqual(sum(x[1]=="uncertain" for x in rows),2)
        self.assertTrue(all(row[3][0]["synthetic"] is True for row in rows))
        self.assertTrue(all(row[3][0]["provider"]=="offline_fixture" for row in rows))
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_stage_executions WHERE run_id=%s::uuid",(run,)
        ).fetchone()[0],2)
        self.assertEqual(str(self.conn.execute(
            "SELECT cost_usd FROM sre_classification_runs WHERE id=%s::uuid",(run,)
        ).fetchone()[0]),"0.000000")
        self.assertFalse(work_once(self.conn))
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_classification_results WHERE run_id=%s::uuid",
            (run,)).fetchone()[0],6)
        self.assertEqual(cancel_offline_run(self.conn,self.pid,run),"completed")
        with self.assertRaises(SREContractError):
            get_offline_run(self.conn,self.fixture.other["id"],run)
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    "UPDATE sre_offline_runs SET taxonomy_sha256=%s WHERE run_id=%s::uuid",
                    ("0"*64,run))
        with self.assertRaises(Exception):
            with self.conn.transaction():
                self.conn.execute(
                    "UPDATE sre_offline_items SET content_sha256=%s WHERE run_id=%s::uuid",
                    ("0"*64,run))

    def test_revocation_partial_denial_and_rights_block_after_claim(self):
        a=self.add_video("Проверка понятности фотоучёта")
        b=self.add_video("Только текст о еде")
        r=create_offline_run(self.conn,self.pid,self.pub,[a["content_version_id"],b["content_version_id"]],
                             self.pipeline,"run-rights")["run_id"]
        first=claim_item(self.conn)
        self.assertTrue(finish_item(self.conn,first))
        second=claim_item(self.conn)
        # Revoke a selected source observation while another video is queued;
        # use a separate policy source in more granular scenarios.
        self.conn.execute(
            "UPDATE source_policy_versions SET is_active=FALSE WHERE id=%s",
            (self.policy,))
        self.assertTrue(finish_item(self.conn,second))
        self.assertEqual(get_offline_run(self.conn,self.pid,r)["status"],"partial")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_classification_results WHERE run_id=%s::uuid",
            (r,)).fetchone()[0],3)
        self.assertEqual(get_offline_run(self.conn,self.pid,r)["items"][1]["state"],
                         "blocked_rights")

    def test_crash_rollback_retry_and_stale_worker_fencing(self):
        content=self.add_video("Проверка понятности фотоучёта")
        r=create_offline_run(self.conn,self.pid,self.pub,[content["content_version_id"]],
                             self.pipeline,"run-recover")["run_id"]
        first=claim_item(self.conn)
        self.assertTrue(heartbeat(self.conn,first))
        with self.assertRaises(RuntimeError):
            work_fault = finish_item(self.conn,first,fault="before_commit")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_classification_results WHERE run_id=%s::uuid",
            (r,)).fetchone()[0],0)
        # Connection failed inside a transaction only; rollback permits retry.
        self.assertTrue(heartbeat(self.conn,first))
        self.assertTrue(finish_item(self.conn,first))
        self.assertFalse(finish_item(self.conn,first))
        self.assertEqual(get_offline_run(self.conn,self.pid,r)["status"],"completed")

        second=self.add_video("Тема без явного совпадения")
        other=create_offline_run(self.conn,self.pid,self.pub,[second["content_version_id"]],
                                 self.pipeline,"run-fence")["run_id"]
        old=claim_item(self.conn)
        self.conn.execute(
            "UPDATE sre_offline_items SET lease_until=now()-interval '2 seconds' WHERE id=%s",
            (old["item_id"],))
        new=claim_item(self.conn)
        self.assertNotEqual(old["worker_token"],new["worker_token"])
        self.assertGreater(new["generation"],old["generation"])
        self.assertFalse(heartbeat(self.conn,old))
        self.assertFalse(finish_item(self.conn,old))
        self.assertTrue(finish_item(self.conn,new))
        self.assertEqual(get_offline_run(self.conn,self.pid,other)["status"],"completed")

    def test_cancel_while_claimed_prevents_any_commit(self):
        a=self.add_video("Тема")
        r=create_offline_run(self.conn,self.pid,self.pub,[a["content_version_id"]],
                             self.pipeline,"run-cancel")["run_id"]
        claim=claim_item(self.conn)
        self.assertEqual(cancel_offline_run(self.conn,self.pid,r),"cancelled")
        self.assertFalse(finish_item(self.conn,claim))
        self.assertEqual(get_offline_run(self.conn,self.pid,r)["status"],"cancelled")
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM sre_classification_results WHERE run_id=%s::uuid",
            (r,)).fetchone()[0],0)


if __name__=="__main__":
    unittest.main()
