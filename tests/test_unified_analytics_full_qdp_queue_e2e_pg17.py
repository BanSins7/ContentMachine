"""UA30 real disposable PG17 QDP A/B/C receipt -> UAE queue admission gates.

Rows are synthetic *handoff receipts*, not independently approved QDP/SRE
classifications or live Group A/B/C results. Queue admission and leasing
must not create a qualified UAE snapshot; worker has no trusted resolver.
"""
from __future__ import annotations

import os
from uuid import uuid4
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics.job_queue import QualificationJobQueue,QueueSpec
from intelligence.unified_analytics.worker import QualificationWorker,WorkerLimits
from test_unified_analytics_job_queue_pg17 import QueueRealPG17


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "Disposable PostgreSQL17 CM_TEST_PG_DSN required")
class FullQDPQueuePG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        QueueRealPG17.setUpClass()

    def setUp(self):
        self.f=QueueRealPG17(methodName=
            "test_real_pg17_duplicate_batch_and_claim_race")
        self.f.setUp()
        self.extra_video_ids=[]

    def tearDown(self):
        self.f.tearDown()
        if self.extra_video_ids:
            with self.f.connect() as db:
                with db.transaction():
                    for video in self.extra_video_ids:
                        db.execute("DELETE FROM videos WHERE id=%s",(video,))

    def add_carousel(self):
        f=self.f
        with f.connect() as db:
            with db.transaction():
                video=db.execute(
                    "INSERT INTO videos("
                    "platform,platform_video_id,canonical_url,content_kind)"
                    " VALUES('youtube',%s,'https://example.invalid/uae30-queue',"
                    "'photo_carousel') RETURNING id",
                    (uuid4().hex,),
                ).fetchone()["id"]
                self.extra_video_ids.append(video)
                db.execute(
                    "INSERT INTO sre_project_videos(project_id,video_id)"
                    " VALUES(%s,%s)",(f.project,video)
                )
                request=str(db.execute(
                    "INSERT INTO uae_qualification_requests("
                    "project_id,video_id,source_id,source_policy_version_id,"
                    "qdp_run_id,qdp_input_sha256,content_kind,"
                    "request_key,request_sha256)"
                    " VALUES(%s,%s,%s,%s,%s::uuid,%s,'photo_carousel',%s,%s)"
                    " RETURNING id",
                    (f.project,video,f.source,f.policy,f.run,f.sha,
                     "uae30.carousel."+uuid4().hex,f.sha),
                ).fetchone()["id"])
                for kind in ("A","B","C"):
                    db.execute(
                        "INSERT INTO uae_qdp_handoff_inbox("
                        "event_id,project_id,qdp_run_id,video_id,"
                        "analysis_group,input_sha256,payload_sha256,"
                        "verified_reference_sha256)"
                        " VALUES(%s::uuid,%s,%s::uuid,%s,%s,%s,%s,%s)",
                        (str(uuid4()),f.project,f.run,video,kind,
                         f.sha,f.sha,f.sha),
                    )
        return video,request

    def test_existing_video_three_handoffs_are_queue_admissible_not_qualified(self):
        f=self.f
        with f.connect() as db:
            queue=QualificationJobQueue(db)
            receipt=queue.enqueue(f.spec("ua30"))
            self.assertEqual(receipt.state,"created")
            self.assertEqual(queue.enqueue(f.spec("ua30")).state,"replayed")
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_unified_snapshots "
                "WHERE project_id=%s",(f.project,)
            ).fetchone()["n"],0)
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_qualification_jobs "
                "WHERE project_id=%s AND state='pending'",(f.project,)
            ).fetchone()["n"],1)

    def test_carousel_three_handoffs_queue_then_missing_c_blocks_claim(self):
        f=self.f
        video,request=self.add_carousel()
        spec=QueueSpec(f.project,request,f.sha,
                       "ua30.idem."+uuid4().hex,"ua30.carousel.batch")
        with f.connect() as db:
            q=QualificationJobQueue(db)
            result=q.enqueue(spec)
            self.assertEqual(result.state,"created")
            with db.transaction():
                db.execute(
                    "DELETE FROM uae_qdp_handoff_inbox "
                    "WHERE project_id=%s AND video_id=%s "
                    "AND analysis_group='C'",
                    (f.project,video),
                )
            leases=q.claim(f.project,"ua30.worker")
            self.assertEqual(leases,())
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_unified_snapshots "
                "WHERE project_id=%s",(f.project,)
            ).fetchone()["n"],0)

    def test_source_revoked_after_handoffs_prevents_new_queue_admission(self):
        f=self.f
        with f.connect() as db:
            with db.transaction():
                db.execute(
                    "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
                    (f.source,),
                )
            with self.assertRaises(AnalyticsBoundaryError):
                QualificationJobQueue(db).enqueue(f.spec("ua30"))
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_qualification_jobs "
                "WHERE project_id=%s",(f.project,)
            ).fetchone()["n"],0)

    def test_missing_candidate_resolver_does_not_consume_a_queue_job(self):
        f=self.f
        with f.connect() as db:
            q=QualificationJobQueue(db)
            q.enqueue(f.spec("ua30"))
        worker=QualificationWorker(
            f.connect,WorkerLimits(f.project,"ua30.worker"),
        )
        with self.assertRaises(AnalyticsBoundaryError) as err:
            worker.run(watch=False)
        self.assertEqual(err.exception.code,
                         "worker_candidate_discovery_unavailable")
        with f.connect() as db:
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_qualification_jobs "
                "WHERE project_id=%s AND state='pending'",(f.project,)
            ).fetchone()["n"],1)
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_unified_snapshots "
                "WHERE project_id=%s",(f.project,)
            ).fetchone()["n"],0)


if __name__=="__main__":
    unittest.main()
