"""FIX-16 local-only Studio API routing, scope and DOM safety tests."""
import io
import unittest
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlsplit
from unittest.mock import patch

from intelligence.qualified_data import http_api as api
from intelligence.qualified_data.contracts import QDPError

PID="a"*32
RID="11111111-1111-4111-8111-111111111111"
SRE="22222222-2222-4222-8222-222222222222"
ROOT="/api/projects/"+PID+"/qualified-data"

class Handler:
    def __init__(self,body=b"{}"):
        self.rfile=io.BytesIO(body)
        self.headers={"Content-Length":str(len(body))}
        self.responses=[]
    def json_response(self,status,value):
        self.responses.append((status,value))

class QDPStudioApiTests(unittest.TestCase):
    def test_live_picker_get_only_verified_adapter(self):
        with patch.object(api.studio_read,"eligible_sre_runs",
                          return_value={"ok":True,"runs":[]}) as read:
            h=Handler()
            self.assertTrue(api.handle_get(h,urlsplit(ROOT+"/eligible-sre-runs?limit=20")))
            self.assertEqual(h.responses[0][0],200)
            read.assert_called_once_with(PID,limit=20,cursor=None)

    def test_live_picker_rejects_unknown_duplicate_and_bad_paging(self):
        for q in ("limit=0","limit=101","limit=NaN","limit=1&limit=2",
                  "foo=bar","cursor=","cursor=aaa&cursor=bbb",
                  "limit=9&x=1&y=2&z=3&k=4&l=5&m=6&n=7",
                  "limit="+"9"*5000):
            with self.subTest(query=q):
                h=Handler()
                api.handle_get(h,urlsplit(ROOT+"/eligible-sre-runs?"+q))
                self.assertEqual(h.responses[0][0],422)

    def test_run_history_and_video_cursor_are_forwarded(self):
        with patch.object(api.repository,"list_runs",
                          return_value={"ok":True,"runs":[]}) as read:
            h=Handler()
            api.handle_get(h,urlsplit(ROOT+"/runs?limit=5&cursor="+RID))
            self.assertEqual(h.responses[0][0],200)
            read.assert_called_once_with(PID,limit=5,cursor=RID)
        with patch.object(api.repository,"get_run",
                          return_value={"ok":True,"items":[]}) as read:
            h=Handler()
            api.handle_get(h,urlsplit(ROOT+"/runs/"+RID+"?after_video_id=12&limit=30"))
            self.assertEqual(h.responses[0][0],200)
            read.assert_called_once_with(PID,RID,limit=30,after_video_id=12)

    def test_legacy_bare_run_details_keeps_100_item_default(self):
        with patch.object(api.repository,"get_run",
                          return_value={"ok":True,"items":[]}) as read:
            h=Handler()
            api.handle_get(h,urlsplit(ROOT+"/runs/"+RID))
            self.assertEqual(h.responses[0][0],200)
            read.assert_called_once_with(PID,RID,limit=100,after_video_id=None)

    def test_no_project_bypass_or_nonlocal_fallback(self):
        h=Handler()
        self.assertFalse(api.handle_get(h,urlsplit("/api/projects/bad/qualified-data/runs")))
        self.assertFalse(h.responses)
        for suffix in ("/admin","/runs/"+RID+"/paid","/runs/"+RID+"/revoke"):
            h=Handler()
            api.handle_get(h,urlsplit(ROOT+suffix))
            self.assertEqual(h.responses[0][0],404)

    def test_lineage_only_positive_video_id(self):
        with patch.object(api.studio_read,"lineage",return_value={"ok":True}) as read:
            h=Handler()
            api.handle_get(h,urlsplit(ROOT+"/runs/"+RID+"/lineage?video_id=9"))
            self.assertEqual(h.responses[0][0],200)
            read.assert_called_once_with(PID,RID,9)
        for q in ("video_id=0","video_id=-9","video_id=NaN",
                  "video_id=9&video_id=8","video_id=18446744073709551616"):
            h=Handler()
            api.handle_get(h,urlsplit(ROOT+"/runs/"+RID+"/lineage?"+q))
            self.assertEqual(h.responses[0][0],422)

    def test_manifest_diagnostics_and_events_routes_read_only(self):
        for endpoint,method in (("manifest","manifest_summary"),
                                ("diagnostics","diagnostics"),
                                ("events","events")):
            with self.subTest(endpoint=endpoint),patch.object(
                    api.studio_read,method,return_value={"ok":True}) as read:
                h=Handler()
                api.handle_get(h,urlsplit(ROOT+"/runs/"+RID+"/"+endpoint))
                self.assertEqual(h.responses[0][0],200)
                self.assertEqual(read.call_count,1)

    def test_preview_must_supply_one_sre_run(self):
        for suffix in ("preview","preview?unused=true",
                       "preview?sre_run_id="+SRE+"&sre_run_id="+SRE):
            h=Handler()
            api.handle_get(h,urlsplit(ROOT+"/"+suffix))
            self.assertEqual(h.responses[0][0],422)
        with patch.object(api.repository,"preview",return_value={"ok":True}) as read:
            h=Handler()
            api.handle_get(h,urlsplit(ROOT+"/preview?sre_run_id="+SRE))
            read.assert_called_once_with(PID,SRE)

    def test_refresh_requires_explicit_new_key_config_and_starts_local_worker(self):
        h=Handler(b'{"idempotency_key":"qdp-studio-new-123","config":{}}')
        with patch("intelligence.qualified_data.invalidation.explicit_refresh",
                   return_value={"ok":True,"new_run_id":RID}) as refresh,patch(
                   "intelligence.qualified_data.worker.start_worker") as worker:
            api.handle_post(h,ROOT+"/runs/"+RID+"/refresh")
            self.assertEqual(h.responses[0][0],202)
            refresh.assert_called_once_with(
                PID,RID,"qdp-studio-new-123",config={})
            worker.assert_called_once()

    def test_post_disallows_browser_revocation_and_unknown_fields(self):
        for suffix,body in (("/runs/"+RID+"/revoke",b"{}"),
                            ("/runs/"+RID+"/refresh",b'{"origin":"operator"}'),
                            ("/runs",b'{"sre_run_id":"abc","paid_calls":1}'),
                            ("/runs/"+RID+"/cancel",b'{"force":true}')):
            with self.subTest(suffix=suffix):
                h=Handler(body)
                api.handle_post(h,ROOT+suffix)
                self.assertIn(h.responses[0][0],(404,422))

    def test_mutations_reject_invalid_length_and_malformed_json(self):
        for body,content_length in ((b"{","1"),(b"{}","100000"),(b"bad","3")):
            h=Handler(body)
            h.headers["Content-Length"]=content_length
            api.handle_post(h,ROOT+"/runs")
            self.assertIn(h.responses[0][0],(400,413))

    def test_qdp_error_status_not_converted_to_generic_database_error(self):
        with patch.object(api.repository,"list_runs",
                          side_effect=QDPError("run_cursor_not_found","missing",404)):
            h=Handler()
            api.handle_get(h,urlsplit(ROOT+"/runs"))
            self.assertEqual(h.responses[0][0],404)
            self.assertEqual(h.responses[0][1]["error"]["code"],"run_cursor_not_found")

    def test_studio_dom_has_no_raw_html_and_no_uuid_paste(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/"site/index.html").read_text(encoding="utf-8")
        script=(root/"site/qualified-data.js").read_text(encoding="utf-8")
        self.assertIn('<select id="qdpSreRun"',html)
        self.assertNotIn('<input id="qdpSreRun"',html)
        self.assertNotIn("innerHTML",script)
        for name in ("qdpMoreSre","qdpMoreRuns","qdpMoreItems",
                     "qdpMoreEvents","qdpRefreshRun","qdpLineage","qdpDiagnostics"):
            self.assertIn('id="'+name+'"',html)
        self.assertIn("textContent",script)
        self.assertNotIn("openrouter",script.lower())
        self.assertIn("X-ContentMachine",script)
        host=(root/"app.py").read_text(encoding="utf-8")
        self.assertLess(host.index("if not self.authorized(mutation=True):"),
                        host.index("if qdp_post(self, route):"))

if __name__=="__main__":
    unittest.main()
