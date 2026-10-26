"""M04 black-box localhost SRE mount and inert static entry tests.

The host server is the same app.Handler used by the existing D1/PSI routes.
No PostgreSQL, OpenRouter call, provider, collector or QDP fixture is needed.
"""
from __future__ import annotations

import json
import os
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import app


class LocalSREBootstrapTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        cls.thread.join(timeout=5)

    def get(self, path, *, method="GET", headers=None, body=None):
        url="http://127.0.0.1:"+str(self.server.server_port)+path
        request=Request(url,data=body,method=method,
                        headers={"Host":"localhost:8765",**(headers or {})})
        try:
            with urlopen(request, timeout=5) as response:
                return response.status,response.headers, response.read()
        except HTTPError as ex:
            return ex.code,ex.headers,ex.read()

    def json_get(self,path,**kwargs):
        code,headers,payload=self.get(path,**kwargs)
        return code,headers,json.loads(payload)

    def test_default_off_manifest_discoverable_without_db_or_paid_call(self):
        with patch.dict(os.environ, {"CM_SRE_ENABLED":"0","CM_DB_ENABLED":"0"},
                        clear=True):
            status,headers,obj=self.json_get("/api/sre/v1/manifest")
            self.assertEqual(status,200)
            self.assertTrue(obj["ok"])
            self.assertEqual(obj["schema_version"],"sre-module/v1")
            self.assertEqual(obj["deployment_readiness"],"disabled")
            self.assertFalse(any(obj["features"].values()))
            self.assertEqual(obj["requires_external_feature_branches"],[])
            self.assertEqual(obj["live_model_policy"]["model_id"],
                             "anthropic/claude-opus-5.5")
            self.assertFalse(obj["live_model_policy"]["allow_fallback"])
            self.assertEqual(headers.get("Cache-Control"),"no-store")

    def test_default_off_health_503_without_source_claims(self):
        with patch.dict(os.environ,{"CM_SRE_ENABLED":"0"},clear=True):
            code,_,obj=self.json_get("/api/sre/v1/health")
            self.assertEqual(code,503)
            self.assertFalse(obj["ok"])
            self.assertEqual(obj["status"],"disabled")
            self.assertFalse(obj["can_dispatch_provider"])
            self.assertFalse(obj["worker_enabled"])

    def test_enabled_flag_cannot_claim_available_or_start(self):
        with patch.dict(os.environ,{"CM_SRE_ENABLED":"1",
                                   "CM_SRE_OFFLINE_WORKER":"1",
                                   "CM_DB_ENABLED":"0"},clear=True):
            code,_,obj=self.json_get("/api/sre/v1/health")
            self.assertEqual(code,503)
            self.assertEqual(obj["status"],"blocked_config")
            self.assertFalse(obj["worker_enabled"])
            self.assertFalse(obj["can_dispatch_provider"])

    def test_error_redacts_invalid_env_and_secrets(self):
        secret="sk-or-SRE_DO_NOT_LEAK_MY_KEY_8f3"
        with patch.dict(os.environ,{"CM_SRE_ENABLED":"invalid_"+secret,
                                   "CM_SRE_OPENROUTER_API_KEY":secret},clear=True):
            code,_,data=self.json_get("/api/sre/v1/manifest")
            self.assertEqual(code,503)
            self.assertNotIn(secret,str(data))
            self.assertEqual(data["error"],"SRE configuration unavailable")

    def test_reject_unrelated_routes_unknown_and_query_parameters(self):
        with patch.dict(os.environ,{"CM_SRE_ENABLED":"0"},clear=True):
            code,_,_=self.json_get("/api/sre/v1/unimplemented")
            self.assertEqual(code,404)
            code,_,_=self.json_get("/api/sre/v1/manifest?foo=bar")
            self.assertEqual(code,400)
            code,_,data=self.json_get("/api/projects")
            self.assertEqual(code,200)
            self.assertIn("projects",data)

    def test_get_host_and_origin_checks_apply_to_sre(self):
        for headers in ({"Host":"evil.example:8765"},
                        {"Origin":"https://evil.example"}):
            with self.subTest(headers=headers):
                code,_,_=self.get("/api/sre/v1/manifest",headers=headers)
                self.assertEqual(code,403)

    def test_mutation_paths_never_dispatch(self):
        with patch.dict(os.environ,{"CM_SRE_ENABLED":"1",
                                   "CM_DB_ENABLED":"0"},clear=True):
            code,_,_=self.get("/api/sre/v1/runs",method="POST",
                              headers={"X-ContentMachine":"1"},body=b"{}")
            self.assertEqual(code,405)
            code,_,_=self.get("/api/sre/v1/runs",method="POST",body=b"{}")
            self.assertEqual(code,403)
            code,_,_=self.get("/api/sre/v1/runs",method="POST",
                              headers={"X-ContentMachine":"1",
                                       "Origin":"https://evil.example"},
                              body=b"{}")
            self.assertEqual(code,403)

    def test_static_studio_and_assets_are_same_origin_and_inert(self):
        for name,mime in [("/sre-studio.html","text/html"),
                          ("/sre-studio.js","text/javascript"),
                          ("/sre-studio.css","text/css")]:
            with self.subTest(name=name):
                code,headers,body=self.get(name)
                self.assertEqual(code,200)
                self.assertIn(mime,headers.get("Content-Type"))
                self.assertGreater(len(body),100)
        code,_,index=self.get("/")
        self.assertEqual(code,200)
        self.assertIn(b'href="/sre-studio.html"',index)
        _,_,script=self.get("/sre-studio.js")
        self.assertIn(b"/api/sre/v1/manifest",script)
        self.assertIn(b"/api/sre/v1/health",script)
        self.assertNotIn(b"method: 'POST'",script)
        self.assertNotIn(b"openrouter.ai",script)
        self.assertNotIn(b"api.openrouter",script)


if __name__=="__main__":
    unittest.main()
