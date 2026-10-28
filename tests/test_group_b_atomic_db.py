"""GB-MOD-22 disposable PostgreSQL17 end-to-end atomic B8 partial writer tests.

Synthetic per-block model/GA evidence and host receipts ONLY; not live rights,
paid model delivery or full B1-B8 completion. No non-disposable databases.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4
import os
import unittest

from intelligence.migrate import apply_migrations
from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.topic_registry import read_topic_registry
from intelligence.group_b.blocks.b8_topic import validate_b8_topic
from intelligence.group_b.validation import validate_bundle
from intelligence.group_b.persistence import (
    AtomicBError,persist_group_b_bundle,read_group_b_result,
)
from test_group_b_b8 import source,context,output,response,Receipt


def safe_dsn():
    from psycopg.conninfo import conninfo_to_dict
    dsn=os.environ["CM_TEST_PG_DSN"]
    db=conninfo_to_dict(dsn).get("dbname","").lower()
    if "test" not in db and "_ci" not in db:
        raise RuntimeError("B22 only permits a disposable PostgreSQL test DB")
    return dsn


class Trust:
    def __init__(self,allow=True):
        self.allow=allow
        self.calls=[]
    def verify_unified_block(self,block,manifest,candidate,packet,request):
        self.calls.append(("block",block))
        return (self.allow is True and block=="B8" and request=="host-proven-request"
                and packet.verify_digest())
    def verify_unified_completion(self,manifest,assessments,candidates):
        self.calls.append(("completion","denied"))
        return False
    def verify_bundle_persistence(self,manifest,assessment,candidates,key):
        self.calls.append(("persistence",key))
        return self.allow is True and key.startswith("group-b-atomic/v1:")
    def verify_topic_persistence(self,analysis_id,manifest,registry,proposal):
        self.calls.append(("topic",analysis_id))
        return self.allow is True and proposal.verify_digest() and registry.verify_digest()


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"No disposable PostgreSQL 17")
class TestAtomicGroupBPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg=psycopg
        cls.dsn=safe_dsn()
        with psycopg.connect(cls.dsn) as conn:
            apply_migrations(conn)

    def prepare(self,*,kind="video"):
        manifest=source(kind)
        project=uuid4().hex
        run=str(uuid4())
        qdp=str(uuid4())
        source_id="b22-source-"+uuid4().hex
        key=uuid4().hex
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                """INSERT INTO project_registry(
                      id,name,profile_revision,profile_hash,created_at,updated_at)
                   VALUES(%s,'Atomic B22 fixture',1,%s,now(),now())""",
                (project,"a"*64)
            )
            conn.execute(
                """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
                   VALUES(%s,'test','owned','enabled',TRUE)""",(source_id,)
            )
            video_id=conn.execute(
                """INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
                   VALUES('tiktok',%s,%s,%s) RETURNING id""",
                (key,"https://example.test/b22/"+key,kind)
            ).fetchone()[0]
            category=conn.execute(
                """INSERT INTO content_b_categories(slug,name)
                   VALUES(%s,%s) RETURNING id""",
                ("b22_cat_"+key,"B22 Fitness "+key)
            ).fetchone()[0]
            topic=conn.execute(
                """INSERT INTO content_b_topics(category_id,slug,name)
                   VALUES(%s,%s,%s) RETURNING id""",
                (category,"b22_topic_"+key,"B22 Strength "+key)
            ).fetchone()[0]
            sub=conn.execute(
                """INSERT INTO content_b_subtopics(topic_id,slug,name)
                   VALUES(%s,%s) RETURNING id""",
                (topic,"b22_sub_"+key,"B22 Squats "+key)
            ).fetchone()[0]
            inbox=conn.execute(
                """INSERT INTO group_b_inbox(project_id,video_id,source_id,
                     event_key,qdp_run_id,producer_schema,event_sha256,
                     preparation_sha256,status)
                   VALUES(%s,%s,%s,%s,%s::uuid,'qdp-handoff/v1',%s,%s,'accepted')
                   RETURNING id""",
                (project,video_id,source_id,key,qdp,"a"*64,manifest.qdp_input_sha256)
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO group_b_runs(id,project_id,inbox_id,video_id,source_id,
                     content_type,a_snapshot_sha256,media_manifest_sha256,
                     config_sha256,idempotency_key,state)
                   VALUES(%s::uuid,%s,%s,%s,%s,%s,%s,%s,%s,%s,'running')""",
                (run,project,inbox,video_id,source_id,
                 "video" if kind in ("video","slideshow_video") else "carousel",
                 manifest.ga_result_sha256,manifest.media_manifest_sha256,
                 "b"*64,uuid4().hex)
            )
            for block in ("B1","B2","B3","B4","B5","B6","B7","B8"):
                conn.execute(
                    """INSERT INTO group_b_block_states(project_id,run_id,block)
                       VALUES(%s,%s::uuid,%s)""",(project,run,block)
                )
        manifest=replace(manifest,project_id=project,b_run_id=run,qdp_run_id=qdp,
                         source_id=source_id,video_id=video_id,
                         source_policy_version_id=None)
        manifest=replace(manifest,manifest_sha256=canonical_hash(manifest.payload()))
        with self.psycopg.connect(self.dsn) as conn:
            registry=read_topic_registry(conn)
        packet=context(manifest)
        candidate=validate_b8_topic(
            manifest=manifest,packet=packet,registry=registry,
            response=response(output(
                manifest,category="B22 Fitness "+key,
                topic="B22 Strength "+key,subtopic="B22 Squats "+key)),
            receipt_verifier=Receipt(),
            delivered_image_refs=tuple(
                o.ref_sha256 for o in manifest.observations
                if o.kind in ("sampled_frame","slide_ref")
            ),
        )
        candidates={"B8":candidate}
        packets={"B8":packet}
        proofs={"B8":"host-proven-request"}
        host=Trust()
        assessment=validate_bundle(
            manifest=manifest,candidates=candidates,packets=packets,
            request_proofs=proofs,trusted_attestor=host,
        )
        self.assertTrue(assessment.block("B8").is_accepted)
        self.assertFalse(assessment.can_complete)
        return manifest,registry,assessment,candidates,packets,proofs,host

    def write(self,conn,prepared):
        m,reg,assessment,candidates,packets,proofs,host=prepared
        return persist_group_b_bundle(
            conn,manifest=m,registry=reg,assessment=assessment,
            candidates=candidates,packets=packets,request_proofs=proofs,
            admission_verifier=host,
        )

    def test_one_transaction_parent_one_child_and_eight_runtime_states(self):
        args=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            result=self.write(conn,args)
        self.assertEqual(result.state,"partial")
        self.assertFalse(result.replayed)
        self.assertEqual(result.block_statuses["B8"],"complete")
        m=args[0]
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute(
                """SELECT count(*) FROM content_b_topic_assignments
                   WHERE analysis_id=%s""",(result.analysis_id,)
            ).fetchone()[0],1)
            self.assertEqual(conn.execute(
                """SELECT state,needs_review FROM content_b_analyses
                   WHERE id=%s""",(result.analysis_id,)
            ).fetchone(),("partial",True))
            self.assertEqual(conn.execute(
                """SELECT count(*) FROM group_b_block_states
                   WHERE project_id=%s AND run_id=%s::uuid AND status='partial'""",
                (m.project_id,m.b_run_id)
            ).fetchone()[0],7)
            scoped=read_group_b_result(
                conn,project_id=m.project_id,run_id=m.b_run_id,
            )
            self.assertEqual(scoped["id"],result.analysis_id)
            self.assertIsNone(scoped["cta_summary"])
            self.assertIsNone(read_group_b_result(
                conn,project_id="other-project",run_id=m.b_run_id,
            ))

    def test_same_key_replay_is_idempotent_no_duplicate_analysis(self):
        prepared=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            first=self.write(conn,prepared)
        with self.psycopg.connect(self.dsn) as conn:
            again=self.write(conn,prepared)
            count=conn.execute(
                "SELECT count(*) FROM content_b_analyses WHERE analysis_key=%s",
                (first.analysis_key,)
            ).fetchone()[0]
        self.assertTrue(again.replayed)
        self.assertEqual(again.analysis_id,first.analysis_id)
        self.assertEqual(count,1)

    def test_missing_host_admission_before_any_sql_write(self):
        prepared=self.prepare()
        prepared=prepared[:-1]+(Trust(False),)
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(AtomicBError):
                self.write(conn,prepared)
            self.assertEqual(conn.execute(
                """SELECT count(*) FROM content_b_analyses
                   WHERE video_id=%s AND method_name='group_b_atomic'""",
                (prepared[0].video_id,)
            ).fetchone()[0],0)

    def test_stale_live_topic_registry_rolls_back_new_parent_and_states(self):
        prepared=self.prepare()
        m=prepared[0]
        with self.psycopg.connect(self.dsn) as conn:
            suffix=uuid4().hex
            conn.execute(
                """INSERT INTO content_b_categories(slug,name) VALUES(%s,%s)""",
                ("b22_extra_"+suffix,"B22 new category "+suffix)
            )
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(Exception):
                self.write(conn,prepared)
            self.assertEqual(conn.execute(
                """SELECT count(*) FROM content_b_analyses
                   WHERE video_id=%s AND method_name='group_b_atomic'""",
                (m.video_id,)
            ).fetchone()[0],0)
            self.assertIsNone(conn.execute(
                "SELECT result_analysis_id FROM group_b_runs WHERE id=%s::uuid",
                (m.b_run_id,)
            ).fetchone()[0])
            self.assertEqual(conn.execute(
                """SELECT count(*) FROM group_b_block_states
                   WHERE project_id=%s AND run_id=%s::uuid AND status='complete'""",
                (m.project_id,m.b_run_id)
            ).fetchone()[0],0)

    def test_arbitrary_failure_after_child_write_rolls_back_everything(self):
        prepared=self.prepare()
        m=prepared[0]
        with self.psycopg.connect(self.dsn) as conn:
            with patch("intelligence.group_b.persistence.repository._readback",
                       side_effect=AtomicBError("forced_failure_after_child")):
                with self.assertRaises(AtomicBError):
                    self.write(conn,prepared)
            self.assertEqual(conn.execute(
                """SELECT count(*) FROM content_b_analyses
                   WHERE video_id=%s AND method_name='group_b_atomic'""",
                (m.video_id,)
            ).fetchone()[0],0)
            self.assertIsNone(conn.execute(
                "SELECT result_analysis_id FROM group_b_runs WHERE id=%s::uuid",
                (m.b_run_id,)
            ).fetchone()[0])

    def test_two_writers_same_run_only_one_version(self):
        prepared=self.prepare()
        barrier=Barrier(2)
        def work():
            with self.psycopg.connect(self.dsn) as conn:
                barrier.wait(timeout=20)
                return self.write(conn,prepared)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(work)
            b=pool.submit(work)
            one,two=a.result(timeout=30),b.result(timeout=30)
        self.assertEqual(one.analysis_id,two.analysis_id)
        self.assertEqual(sorted([one.replayed,two.replayed]),[False,True])

    def test_completed_sql005_immutability_guard_after_partial(self):
        prepared=self.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            result=self.write(conn,prepared)
        with self.psycopg.connect(self.dsn) as conn:
            # Assert preexisting immutable SQL005 completed parent contract.
            conn.execute(
                "UPDATE content_b_analyses SET state='completed' WHERE id=%s",
                (result.analysis_id,)
            )
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(Exception):
                with conn.transaction():
                    conn.execute(
                        """UPDATE content_b_analyses SET method_version='spoofed'
                           WHERE id=%s""",(result.analysis_id,)
                    )


if __name__=="__main__":
    unittest.main()
