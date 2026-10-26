"""Offline DTK connector contract tests. Never launch DTK or contact TikTok."""
from __future__ import annotations

import json
import os
import sys
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from intelligence.connectors import dtk
from intelligence import config
import app


class Reply:
    status = 202
    def __init__(self, payload):
        self.data = json.dumps(payload).encode()
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, size):
        return self.data[:size]


class Opener:
    def __init__(self, payload):
        self.payload = payload
        self.request = None
    def open(self, request, timeout):
        self.request = request
        assert timeout <= 10
        return Reply(self.payload)


class DTKContractTests(unittest.TestCase):
    def test_operations(self):
        self.assertEqual(dtk.validate_action("search_posts", {"keyword":"home workout"})[0],
                         "/api/v1/tiktok/search/posts")
        self.assertEqual(dtk.validate_action("user_posts", {"sec_user_id":"MS4wLjABAAAA"})[1]["sec_user_id"],"MS4wLjABAAAA")
        self.assertEqual(dtk.validate_action("comments", {"aweme_id":"7234567890123456789"})[1]["aweme_id"],"7234567890123456789")
        self.assertEqual(dtk.validate_action("comment_replies",
              {"aweme_id":"7234567890123456789","comment_id":"1234567890123456789"})[1]["comment_id"],"1234567890123456789")
        for a,p in [
            ("random",{"keyword":"x"}),("search_posts",{"keyword":"x","token":"secret"}),
            ("search_posts",{"keyword":""}),("search_posts",{"keyword":"x","cursor":"\nSECRET"}),
            ("video",{"aweme_id":"?http://evil"}),("user",{"sec_user_id":""})
        ]:
            with self.subTest(a=a,p=p),self.assertRaises(dtk.DTKError):
                dtk.validate_action(a,p)

    def test_disabled_by_default_and_localhost_only(self):
        with patch.dict(os.environ, {"CM_DTK_ENABLED":"0","CM_DTK_API_KEY":"dtk_123456789012_valid"}):
            with self.assertRaises(dtk.DTKError) as err:
                dtk.settings()
            self.assertEqual(err.exception.code,"disabled")
        for base in ("https://127.0.0.1:8000","http://localhost:8000",
                     "http://127.0.0.1:8000/evil", "http://127.0.0.1:8000@evil.test",
                     "http://169.254.169.254:80","http://127.0.0.1:abc"):
            with self.subTest(base=base),patch.dict(os.environ, {
                "CM_DTK_ENABLED":"1","CM_DTK_BASE_URL":base,"CM_DTK_API_KEY":"dtk_123456789012_valid"}):
                with self.assertRaises(dtk.DTKError) as err:
                    dtk.settings()
                self.assertEqual(err.exception.code,"invalid_config")

    def test_successful_submit_async_and_task_result(self):
        upstream = Opener({"success":True,"data":{"task_id":"12345678-1234-1234-1234-123456789abc","state":"queued"},
                           "error":None,"meta":{"request_id":"request-01"}})
        with patch("intelligence.connectors.dtk.settings",return_value=("http://127.0.0.1:8000","dtk_dummy_valid_key")),patch(
            "intelligence.connectors.dtk.build_opener",return_value=upstream):
            result=dtk.submit("search_posts",{"keyword":"home workout"})
        self.assertEqual(result["state"],"queued")
        self.assertEqual(result["task_id"],"12345678-1234-1234-1234-123456789abc")
        self.assertEqual(upstream.request.get_header("Authorization"),"Bearer dtk_dummy_valid_key")
        self.assertIn("keyword=home+workout",upstream.request.full_url)
        self.assertNotIn("dtk_dummy_valid_key",upstream.request.full_url)
        with patch("intelligence.connectors.dtk.request_upstream",return_value={
            "success":True,"data":{"task_id":result["task_id"],"state":"done",
            "data":{"items":[{"id":"1","caption":"hello"}]},"result_meta":{"cursor":"next"}}}):
            output=dtk.task(result["task_id"])
        self.assertEqual(output["data"]["items"][0]["id"],"1")

    def test_dtk_errors_are_code_only(self):
        class FailingOpener:
            def open(self, req, timeout):
                raise HTTPError(req.full_url,401,"TOKEN LEAK",{},None)
        with patch("intelligence.connectors.dtk.settings",return_value=("http://127.0.0.1:8000","dtk_dummy_secret")),patch(
            "intelligence.connectors.dtk.build_opener",return_value=FailingOpener()):
            with self.assertRaises(dtk.DTKError) as ctx:
                dtk.request_upstream("/api/v1/auth/me")
        self.assertEqual(ctx.exception.code,"upstream_auth")
        self.assertNotIn("secret",str(ctx.exception))

    def test_status_never_exposes_key(self):
        with patch("intelligence.connectors.dtk.settings",return_value=("http://127.0.0.1:8000","very-secret-key")),patch(
            "intelligence.connectors.dtk.request_upstream",return_value={
             "success":True,"data":{"scopes":["tiktok:read"]}}):
            info=dtk.status()
        self.assertTrue(info["supports_keyword_search"])
        self.assertNotIn("very-secret-key",json.dumps(info))

    def test_local_env_reads_only_configured_values(self):
        with patch.dict(os.environ,{},clear=True),patch.object(config,"local_env",return_value={
            "CM_DTK_ENABLED":"1","CM_DTK_BASE_URL":"http://127.0.0.1:8000",
            "CM_DTK_API_KEY":"dtk_123456789012_valid"}):
            self.assertEqual(dtk.settings()[1],"dtk_123456789012_valid")


class DTKHttpTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()
    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=3)
    def call(self,path,method="GET",data=None,extra=None):
        req=Request("http://127.0.0.1:"+str(self.server.server_port)+path,
             data=json.dumps(data).encode() if data is not None else None, method=method,
             headers={"Host":"localhost:8765",**(extra or {})})
        from urllib.error import HTTPError
        try:
            with urlopen(req,timeout=5) as response:
                return response.status,json.loads(response.read())
        except HTTPError as error:
            return error.code,json.loads(error.read())
    def test_get_status_and_post_header(self):
        with patch("intelligence.connectors.dtk.status",return_value={"tool_id":"dtk_tiktok","configured":False}):
            code,value=self.call("/api/tools/dtk/status")
        self.assertEqual(code,200)
        self.assertEqual(value["tool_id"],"dtk_tiktok")
        code,_=self.call("/api/tools/dtk/run","POST",{"action":"search_posts","params":{"keyword":"hello"}})
        self.assertEqual(code,403)
        with patch("intelligence.connectors.dtk.submit",return_value={
            "task_id":"12345678-1234-1234-1234-123456789abc","state":"queued"}):
            code,value=self.call("/api/tools/dtk/run","POST",
                {"action":"search_posts","params":{"keyword":"hello"}},{"X-ContentMachine":"1"})
        self.assertEqual(code,202)
        self.assertEqual(value["state"],"queued")
    def test_get_task(self):
        tid="12345678-1234-1234-1234-123456789abc"
        with patch("intelligence.connectors.dtk.task",return_value={"task_id":tid,"state":"done","data":{}}):
            code,value=self.call("/api/tools/dtk/tasks/"+tid)
        self.assertEqual(code,200)
        self.assertEqual(value["state"],"done")
        code,_=self.call("/api/tools/dtk/tasks/not-a-task")
        self.assertEqual(code,404)


if __name__=="__main__":
    unittest.main()
