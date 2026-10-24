"""Stage13 same-origin, read-only RTB Context preflight route tests (no DB/LLM)."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from uuid import uuid4

import app
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

class ContextReadinessHTTP(unittest.TestCase):
    def setUp(self):
        self.pid=uuid4().hex
        self.cid=str(uuid4())
        self.server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join,3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)

    def call(self,suffix):
        url="http://127.0.0.1:"+str(self.server.server_port)+"/api/projects/"+self.pid+"/taxonomy-builder/context-preview"+suffix
        req=Request(url,headers={"Host":"localhost:8765"})
        try:
            with urlopen(req,timeout=5) as response:
                return response.status,json.loads(response.read())
        except HTTPError as exc:
            return exc.code,json.loads(exc.read())

    def test_readonly_preview_explicit_binding_and_no_paid_call(self):
        out={"input_mode":"approved_project_context_v1",
             "context_binding":{"context_id":self.cid,"selection_sha256":"f"*64},
             "selected_count":1,"evidence":[{"evidence_id":str(uuid4())}],
             "no_provider_call":True,"no_quote_created":True}
        with patch("intelligence.taxonomy_builder.context_binding.ui_preflight",return_value=out) as service:
            status,data=self.call("?context_id="+self.cid+"&token_budget=1200")
            self.assertEqual(status,200)
            self.assertTrue(data["ok"])
            self.assertEqual(data["context_binding"]["context_id"],self.cid)
            service.assert_called_once_with(self.pid,self.cid,1200)

    def test_fail_closed_invalid_queries(self):
        with patch("intelligence.taxonomy_builder.context_binding.ui_preflight") as service:
            for suffix in ("","?context_id="+self.cid,
                          "?context_id="+self.cid+"&token_budget=abc",
                          "?context_id="+self.cid+"&token_budget=1200&token_budget=1500",
                          "?context_id="+self.cid+"&token_budget=1200&hidden=1",
                          "?context_id="+self.cid+"&token_budget=-20"):
                status,data=self.call(suffix)
                self.assertEqual(status,400,(suffix,data))
            service.assert_not_called()

    def test_rejected_context_and_cross_project_cannot_be_preflighted(self):
        with patch("intelligence.taxonomy_builder.context_binding.ui_preflight",
                   side_effect=PipelineError("Unapproved or wrong project",409)):
            status,body=self.call("?context_id="+self.cid+"&token_budget=500")
            self.assertEqual(status,409)
            self.assertNotIn("traceback",str(body).lower())

if __name__=="__main__":unittest.main()
