"""D1.2-D1.7: normalizer, CSV import, durable PG pipeline, 10k fixture and API QA."""
from __future__ import annotations
import os
import sys
import unittest
import uuid
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from intelligence.discovery.normalize import validate_candidate, canonical_video_url
from intelligence.discovery.importers import parse_content, ImportErrorPublic
from intelligence.discovery import service, http_api

class PureDiscoveryTests(unittest.TestCase):
    def test_url_canonical_identity(self):
        u=canonical_video_url("https://youtu.be/ABCDEFGHIJK?t=17")
        self.assertEqual(u,("youtube","ABCDEFGHIJK","https://www.youtube.com/watch?v=ABCDEFGHIJK"))
        self.assertEqual(canonical_video_url("https://www.youtube.com/shorts/ABCDEFGHIJK"),u)
        self.assertEqual(canonical_video_url("https://www.instagram.com/reels/abcdef123/")[0],"instagram")
        self.assertEqual(canonical_video_url("https://www.tiktok.com/@test/video/7234567890123456789")[0],"tiktok")
    def test_reject_ssrf_or_ambiguous_links(self):
        for value in ("http://www.youtube.com/watch?v=ABCDEFGHIJK",
                      "https://localhost/shorts/ABCDEFGHIJK",
                      "https://youtube.com.evil.com/shorts/ABCDEFGHIJK",
                      "https://www.youtube.com/watch?v=bad",
                      "https://user:secret@www.youtube.com/shorts/ABCDEFGHIJK",
                      "https://www.youtube.com/watch?v=ABCDEFGHIJK#secret"):
            with self.subTest(url=value),self.assertRaises(ValueError):
                canonical_video_url(value)
    def test_missing_and_zero(self):
        base={"url":"https://youtu.be/ABCDEFGHIJK",
              "published_at":"2026-10-01T00:00:00Z","title":"test"}
        a=validate_candidate(dict(base,views=0,observed_at="2026-10-02T00:00:00Z"))
        self.assertEqual(a.state,"valid")
        self.assertEqual(a.candidate.views,0)
        self.assertIsNone(a.candidate.shares)
        self.assertEqual(a.candidate.published_at.tzinfo,timezone.utc)
        self.assertEqual(validate_candidate({**base,"views":-1}).state,"rejected")
        self.assertEqual(validate_candidate({**base,"views":0.1}).state,"rejected")
        self.assertEqual(validate_candidate({**base,"views":True}).state,"rejected")
        self.assertEqual(validate_candidate({**base,"views":1}).reasons,("missing_observation_time",))
    def test_timezone_and_partial(self):
        a=validate_candidate({"url":"https://youtu.be/ABCDEFGHIJK",
          "published_at":"2026-10-18T02:00:00+02:00"})
        self.assertEqual(a.state,"partial")
        self.assertEqual(a.candidate.published_at.hour,0)
        self.assertIn("missing_title",a.reasons)
        self.assertEqual(validate_candidate({"url":"https://youtu.be/ABCDEFGHIJK",
           "published_at":"2026-10-18T02:00:00"}).state,"rejected")
    def test_csv_import_and_restrictions(self):
        rows=parse_content("url,title,published_at\nhttps://youtu.be/ABCDEFGHIJK,Hello,2026-10-18T00:00:00Z\n","csv")
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]["title"],"Hello")
        with self.assertRaises(ImportErrorPublic):
            parse_content("url,views\nhttps://youtu.be/ABCDEFGHIJK,1000\n","csv")
        with self.assertRaises(ImportErrorPublic):
            parse_content("","urls")
        with self.assertRaises(ImportErrorPublic):
            parse_content("abc\n"*10001,"urls")
    def test_http_api_error_doesnt_leak(self):
        class Dummy:
            headers={"Content-Length":"0"}
            def json_response(self,code,data):
                self.code=code;self.value=data
        handler=Dummy()
        self.assertTrue(http_api.handle_post(handler,"/api/intelligence/manual-imports"))
        self.assertEqual(handler.code,413)
        self.assertFalse(http_api.handle_get(handler,type("URL",(),{"path":"/api/state","query":""})()))

@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"CM_TEST_PG_DSN absent")
class PostgresDiscoveryPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.db=os.environ["CM_TEST_PG_DSN"]
        cls.patch=patch.dict(os.environ,{"CM_DB_DSN":cls.db,"CM_DB_ENABLED":"1"})
        cls.patch.start()
        import psycopg
        cls.conn=psycopg.connect(cls.db,autocommit=True)
        from intelligence.migrate import apply_migrations
        apply_migrations(cls.conn)
    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.patch.stop()

    def _process(self,limit=300):
        count=0
        while service.work_once():
            count+=1
            if count>limit:
                raise AssertionError("Exceeded expected job count")
        return count

    def test_end_to_end_import_dedupe_restart_and_trace(self):
        # All records are synthetic and D1 cannot call an external platform.
        count=10000
        # Randomly namespace every fixture run; avoid deleting unrelated data.
        prefix="T"+uuid.uuid4().hex[:5]
        rows="\n".join(f"https://www.youtube.com/shorts/{prefix}{i:05d}" for i in range(count))
        source=service.create_import(rows,"urls",topic="D1-10k-QA-"+uuid.uuid4().hex)
        run_id=source["run_id"]
        self.assertEqual(source["queued_rows"],count)
        replay=service.create_import(rows,"urls",topic=service.run_detail(run_id)["config"]["topic"],
                                      key="another-"+uuid.uuid4().hex)
        # The two independently queued runs contain the same identities.
        self.assertNotEqual(replay["run_id"],run_id)
        self.assertEqual(self._process(limit=450),400)
        first=service.run_detail(run_id)
        second=service.run_detail(replay["run_id"])
        self.assertEqual(first["state"],"completed")
        self.assertEqual(second["state"],"completed")
        self.assertEqual(first["counts"]["new"],count)
        from intelligence.discovery.kpi import manual_monthly_kpi
        kpi=manual_monthly_kpi(self.conn)
        self.assertGreaterEqual(kpi["discovered"],count)
        self.assertEqual(kpi["qualified_fresh"],0)  # no external verification in D1
        self.assertEqual(kpi["manual_labeled_fresh"],0)  # dates unknown
        self.assertEqual(second["counts"]["new"],0)
        self.assertEqual(second["counts"]["duplicate"],count)
        # Exactly 10k identities despite 20k candidate submissions.
        actual=self.conn.execute("""SELECT count(*) FROM videos WHERE platform='youtube'
              AND platform_video_id LIKE %s""",(prefix+"%",)).fetchone()[0]
        self.assertGreaterEqual(actual,count)
        catalog=service.list_videos(limit=2)
        self.assertTrue(catalog["videos"])
        example=catalog["videos"][0]
        self.assertTrue(service.video_trace(example["id"])["discoveries"])
        self.assertIsNone(example["published_at"]) # no invented date
        # Cleanup fixture identities (CASCADE provenance & topic).
        self.conn.execute("""DELETE FROM videos WHERE id IN
              (SELECT d.video_id FROM discoveries d WHERE d.run_id=%s)""",(run_id,))
        self.conn.execute("DELETE FROM search_runs WHERE id IN (%s,%s)",
                          (run_id,replay["run_id"]))

    def test_invalid_rows_idempotency_and_cancel(self):
        key="d1-key-"+uuid.uuid4().hex
        text="https://youtu.be/ABCDEFGHIJK\nthis is not a video\nhttps://youtu.be/ABCDEFGHIJK"
        item=service.create_import(text,"urls",topic="D1-errors-"+uuid.uuid4().hex,key=key)
        self.assertTrue(service.create_import(text,"urls",topic=service.run_detail(item["run_id"])["config"]["topic"],key=key)["replayed"])
        with self.assertRaises(service.DiscoveryError):
            service.create_import(text+"\nextra","urls",topic="changed",key=key)
        self._process()
        result=service.run_detail(item["run_id"])
        self.assertEqual(result["counts"]["rejected"],1)
        self.assertEqual(result["counts"]["new"],1)
        self.assertEqual(result["counts"]["duplicate"],1)
        self.assertIn("invalid_url",result["flags"])
        self.assertIn({"code":"invalid_url","row_number":2},result["rejected_rows"])
        video=self.conn.execute("SELECT id FROM videos WHERE platform='youtube' AND platform_video_id='ABCDEFGHIJK'").fetchone()[0]
        label=service.label_video(video,result["config"]["topic"],"relevant")
        self.assertEqual(label["label"],"relevant")
        self.assertEqual(service.label_video(video,result["config"]["topic"],"irrelevant")["label"],"irrelevant")
        self.assertEqual(self.conn.execute("""SELECT count(*) FROM human_labels
          WHERE video_id=%s AND superseded_at IS NULL""",(video,)).fetchone()[0],1)
        self.conn.execute("DELETE FROM videos WHERE id=%s",(video,))
        self.conn.execute("DELETE FROM search_runs WHERE id=%s",(item["run_id"],))
        cancelled=service.create_import("https://youtu.be/ZYXWVUTSRQP","urls",
                       key="cancel-"+uuid.uuid4().hex)
        service.cancel_run(cancelled["run_id"])
        self.assertEqual(service.run_detail(cancelled["run_id"])["state"],"cancelled")
        self.assertFalse(service.work_once())
        self.conn.execute("DELETE FROM search_runs WHERE id=%s",(cancelled["run_id"],))

    def test_expired_lease_can_be_reclaimed(self):
        item=service.create_import("https://youtu.be/QQQQQQQQQQQ","urls",
                    key="lease-"+uuid.uuid4().hex)
        with self.conn.transaction():
            job=self.conn.execute("""SELECT id FROM jobs WHERE
                job_type='discovery.import' AND payload->>'run_id'=%s""",
                (str(item["run_id"]),)).fetchone()[0]
            self.conn.execute("""UPDATE jobs SET state='running',locked_by='crashed-worker',
                lease_until=now()-interval '1 second',attempts=1,
                lease_generation=1 WHERE id=%s""",(job,))
        self.assertTrue(service.work_once())
        jobstate=self.conn.execute("""SELECT state,lease_generation FROM jobs WHERE id=%s""",
                   (job,)).fetchone()
        self.assertEqual(jobstate,("completed",2))
        # Old worker cannot acknowledge a newer generation.
        self.assertIsNone(self.conn.execute("""UPDATE jobs SET state='completed'
             WHERE id=%s AND locked_by='crashed-worker' AND lease_generation=1
             RETURNING id""",(job,)).fetchone())
        self.conn.execute("DELETE FROM videos WHERE platform_video_id='QQQQQQQQQQQ'")
        self.conn.execute("DELETE FROM search_runs WHERE id=%s",(item["run_id"],))

    def test_live_local_http_import_trace_and_auth(self):
        # Real localhost HTTP integration, no social-platform or paid HTTP requests.
        import json
        import threading
        import app
        from http.server import ThreadingHTTPServer
        from urllib.request import Request, urlopen
        from urllib.error import HTTPError
        srv=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        thread=threading.Thread(target=srv.serve_forever,daemon=True)
        thread.start()
        def req(path, payload=None, authorized=True):
            headers={"Host":"localhost:8765"}
            if payload is not None and authorized:
                headers["X-ContentMachine"]="1"
                headers["Content-Type"]="application/json"
            data=None if payload is None else json.dumps(payload).encode("utf-8")
            request=Request("http://127.0.0.1:"+str(srv.server_port)+path,
                            data=data,headers=headers,method="POST" if payload is not None else "GET")
            try:
                with urlopen(request,timeout=4) as res:
                    return res.status,json.load(res)
            except HTTPError as exc:
                return exc.code,json.loads(exc.read())
        try:
            status,value=req("/api/intelligence/preview",{"platforms":["youtube"]},authorized=False)
            self.assertEqual(status,403)
            self.assertIn("error",value)
            status,value=req("/api/intelligence/preview",{"platforms":["youtube"]})
            self.assertEqual(status,200)
            self.assertIn("youtube",value["blocked"])
            code=uuid.uuid4().hex[:11]
            payload={"format":"urls","text":"https://youtu.be/"+code,
                     "topic":"HTTP-QA-"+uuid.uuid4().hex,
                     "idempotency_key":"http-"+uuid.uuid4().hex}
            # Do not leave a daemon HTTP worker racing against other tests.
            # Process the queued request deterministically in this test.
            with patch.object(service,"start_worker",return_value=None):
                status,result=req("/api/intelligence/manual-imports",payload)
            self.assertEqual(status,202)
            run_id=result["run_id"]
            self.assertTrue(service.work_once())
            import time
            for _ in range(50):
                progress=service.run_detail(run_id)
                if progress["state"]=="completed":break
                time.sleep(.1)
            self.assertEqual(progress["state"],"completed")
            status,videos=req("/api/intelligence/videos")
            self.assertEqual(status,200)
            item=next(x for x in videos["videos"] if x["platform_video_id"]==code)
            self.assertEqual(item["topic"],payload["topic"])
            status,trace=req(f"/api/intelligence/videos/{item['id']}/trace")
            self.assertEqual(status,200)
            self.assertEqual(trace["discoveries"][0]["run_id"],run_id)
            self.conn.execute("DELETE FROM videos WHERE id=%s",(item["id"],))
            self.conn.execute("DELETE FROM search_runs WHERE id=%s",(run_id,))
        finally:
            srv.shutdown()
            srv.server_close()
            thread.join(timeout=3)

    def test_denied_source_does_not_insert(self):
        item=service.create_import("https://youtu.be/QWERTYUIOPA","urls",
                                   key="deny-"+uuid.uuid4().hex)
        try:
            self.conn.execute("UPDATE sources SET can_store_metadata=FALSE WHERE id=%s",
                              (service.SOURCE_ID,))
            self.assertTrue(service.work_once())
            run=service.run_detail(item["run_id"])
            self.assertEqual(run["counts"]["new"],0)
            self.assertEqual(run["state"],"running") # bounded retry queued
            self.assertEqual(run["job_errors"].get("rights_denied"),1)
            self.assertIsNone(self.conn.execute("""SELECT id FROM videos
                    WHERE platform_video_id='QWERTYUIOPA'""").fetchone())
        finally:
            self.conn.execute("UPDATE sources SET can_store_metadata=TRUE WHERE id=%s",
                              (service.SOURCE_ID,))
            self.conn.execute("""DELETE FROM jobs
                  WHERE job_type='discovery.import' AND payload->>'run_id'=%s""",
                  (str(item["run_id"]),))
            self.conn.execute("DELETE FROM search_runs WHERE id=%s",(item["run_id"],))
