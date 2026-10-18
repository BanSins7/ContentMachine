"""D1 quality-gate regressions discovered during independent source review.

Run only against a disposable PostgreSQL test instance via CM_TEST_PG_DSN.
Never run stress fixtures against a user's personal ContentMachine database.
"""
from __future__ import annotations
import os
import sys
import unittest
from uuid import uuid4
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intelligence.discovery import service

@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"), "CM_TEST_PG_DSN not configured")
class D1QualityReviewPG(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.patch = patch.dict(os.environ, {
            "CM_DB_DSN": os.environ["CM_TEST_PG_DSN"], "CM_DB_ENABLED": "1"
        })
        cls.patch.start()
        cls.conn = service.connect()
        from intelligence.migrate import apply_migrations
        apply_migrations(cls.conn)

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.patch.stop()

    def setUp(self):
        self.runs = []
        self.ids = []

    def tearDown(self):
        # Narrow per-test cleanup: never remove an entire numerical ID range.
        self.conn.execute("UPDATE sources SET can_store_metadata=TRUE WHERE id=%s",
                          (service.SOURCE_ID,))
        for run in self.runs:
            self.conn.execute("""DELETE FROM jobs WHERE job_type='discovery.import'
                    AND (payload->>'run_id'=%s OR result->>'run_id'=%s)""",
                    (str(run), str(run)))
            self.conn.execute("DELETE FROM search_runs WHERE id=%s",(run,))
        for identity in self.ids:
            self.conn.execute(
                "DELETE FROM videos WHERE platform='youtube' AND platform_video_id=%s",
                (identity,))

    def _start(self, link, fmt="urls", topic=""):
        run = service.create_import(link,fmt,topic=topic, key="quality-"+uuid4().hex)
        self.runs.append(run["run_id"])
        return run["run_id"]

    def _process(self):
        for _ in range(20):
            if not service.work_once():
                break
        else:
            self.fail("Worker failed to quiesce within 20 jobs")

    def test_nan_duration_rejected_per_row_not_entire_batch(self):
        from intelligence.discovery.importers import parse_content
        from intelligence.discovery.normalize import validate_candidate
        data=("url,title,duration_seconds\n"
              "https://youtu.be/ABCDEFGHIJK,A,NaN\n"
              "https://youtu.be/ZYXWVUTSRQP,B,59.25\n")
        rows=parse_content(data,"csv")
        self.assertEqual(len(rows),2)
        self.assertEqual(validate_candidate(rows[0]).reasons,("invalid_duration",))
        self.assertEqual(validate_candidate(rows[1]).candidate.duration_seconds,59.25)
        blank=parse_content("url,platform\nhttps://youtu.be/ABCDEFGHIJK,\n","csv")
        self.assertEqual(validate_candidate(blank[0]).candidate.platform,"youtube")

    def test_reimport_url_does_not_erase_title_or_publication(self):
        video = uuid4().hex[:11]
        self.ids.append(video)
        url="https://www.youtube.com/shorts/"+video
        first=self._start("url,title,published_at\n"+url+",Original Title,2026-10-01T10:00:00Z\n","csv")
        self._process()
        self.assertEqual(service.run_detail(first)["state"],"completed")
        second=self._start(url,"urls")
        self._process()
        self.assertEqual(service.run_detail(second)["state"],"completed")
        rows=self.conn.execute("""SELECT m.title,m.published_at
             FROM source_video_metadata m JOIN videos v ON v.id=m.video_id
             WHERE v.platform='youtube' AND v.platform_video_id=%s
                  AND m.source_id=%s ORDER BY m.fetched_at DESC""",
             (video,service.SOURCE_ID)).fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0][0],"Original Title")
        self.assertEqual(rows[0][1].isoformat(),"2026-10-01T10:00:00+00:00")

    def test_source_rights_revocation_blocks_read_and_kpi(self):
        video=uuid4().hex[:11]
        self.ids.append(video)
        topic="Revocation-"+uuid4().hex
        self._start("url,title,published_at\nhttps://youtu.be/"+video+
                    ",Original,2026-10-01T00:00:00Z\n","csv",topic)
        self._process()
        video_id=self.conn.execute("SELECT id FROM videos WHERE platform_video_id=%s",(video,)).fetchone()[0]
        service.label_video(video_id,topic,"relevant")
        self.conn.execute("UPDATE sources SET can_store_metadata=FALSE WHERE id=%s",(service.SOURCE_ID,))
        self.assertEqual(service.list_videos()["videos"],[])
        self.assertTrue(service.list_videos()["source_disabled"])
        with self.assertRaises(service.DiscoveryError):
            service.video_trace(video_id)
        self.assertEqual(service.diagnostics()["monthly"]["discovered"],0)
        self.assertEqual(service.list_sources()["sources"][0]["enabled"],False)

    def test_exhausted_lease_marks_run_failed_instead_of_hanging(self):
        video=uuid4().hex[:11]
        self.ids.append(video)
        run=self._start("https://youtu.be/"+video)
        job=self.conn.execute("""SELECT id FROM jobs
               WHERE job_type='discovery.import' AND payload->>'run_id'=%s""",
               (str(run),)).fetchone()[0]
        self.conn.execute("""UPDATE jobs SET state='running', locked_by='dead-worker',
               attempts=max_attempts,lease_until=now()-interval '5 seconds',
               lease_generation=3 WHERE id=%s""",(job,))
        self.assertFalse(service.work_once())
        self.assertEqual(service.run_detail(run)["state"],"failed")
        self.assertEqual(self.conn.execute("SELECT state FROM jobs WHERE id=%s",(job,)).fetchone()[0],"failed")

    def test_rejection_reports_exact_input_row(self):
        run=self._start("https://youtu.be/ABCDEFGHIJK\ninvalid line")
        self._process()
        self.assertIn({"code":"invalid_url","row_number":2},
                      service.run_detail(run)["rejected_rows"])

    def test_source_disabled_status_blocks_catalog(self):
        video=uuid4().hex[:11]
        self.ids.append(video)
        self._start("https://youtu.be/"+video)
        self._process()
        self.conn.execute("UPDATE sources SET status='disabled' WHERE id=%s",
                          (service.SOURCE_ID,))
        try:
            self.assertTrue(service.list_videos()["source_disabled"])
            self.assertFalse(service.list_sources()["sources"][0]["enabled"])
        finally:
            self.conn.execute("UPDATE sources SET status='enabled' WHERE id=%s",
                              (service.SOURCE_ID,))

    def test_parallel_claims_do_not_duplicate_an_import(self):
        from concurrent.futures import ThreadPoolExecutor
        video=uuid4().hex[:11]
        self.ids.append(video)
        run=self._start("https://youtu.be/"+video)
        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes=list(executor.map(lambda _: service.work_once(),range(2)))
        self.assertEqual(outcomes.count(True),1)
        self.assertEqual(service.run_detail(run)["counts"]["new"],1)
        self.assertEqual(self.conn.execute(
            "SELECT count(*) FROM videos WHERE platform='youtube' AND platform_video_id=%s",
            (video,)).fetchone()[0],1)

    def test_mid_batch_db_failure_rolls_back_and_safe_retry(self):
        first=uuid4().hex[:11]
        second=uuid4().hex[:11]
        self.ids.extend([first,second])
        run=self._start("https://youtu.be/"+first+"\nhttps://youtu.be/"+second)
        original=service._ingest_one
        calls=[0]
        def break_after_first(*args,**kwargs):
            calls[0]+=1
            if calls[0]==2:
                raise RuntimeError("injected failure")
            return original(*args,**kwargs)
        with patch.object(service,"_ingest_one",side_effect=break_after_first):
            self.assertTrue(service.work_once())
        self.assertEqual(service.run_detail(run)["counts"]["new"],0)
        for video in (first,second):
            self.assertIsNone(self.conn.execute(
                "SELECT id FROM videos WHERE platform='youtube' AND platform_video_id=%s",
                (video,)).fetchone())
        self.conn.execute("""UPDATE jobs SET run_after=now()-interval '1 second'
            WHERE job_type='discovery.import' AND payload->>'run_id'=%s""",
            (str(run),))
        self.assertTrue(service.work_once())
        result=service.run_detail(run)
        self.assertEqual(result["state"],"completed")
        self.assertEqual(result["counts"]["new"],2)
        self.assertEqual(result["counts"]["rejected"],0)

    def test_completed_run_cannot_be_cancelled_retroactively(self):
        video=uuid4().hex[:11]
        self.ids.append(video)
        run=self._start("https://youtu.be/"+video)
        self._process()
        self.assertEqual(service.run_detail(run)["state"],"completed")
        with self.assertRaises(service.DiscoveryError):
            service.cancel_run(run)
        self.assertEqual(service.run_detail(run)["state"],"completed")
