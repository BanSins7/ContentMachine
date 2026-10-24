"""Actual localhost HTTP routing smoke. No external network/provider access."""
from __future__ import annotations
import http.client
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import app

PID="a"*32
BASE="/api/projects/"+PID+"/analytics"

class AnalyticsHttpRoutes(unittest.TestCase):
    def setUp(self):
        self.server=ThreadingHTTPServer(("127.0.0.1",0),app.Handler)
        self.previous_port=app.PORT
        app.PORT=self.server.server_address[1]
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        app.PORT=self.previous_port

    def request(self,method,path,body=None,headers=None):
        con=http.client.HTTPConnection("127.0.0.1",app.PORT,timeout=5)
        h={"Host":"127.0.0.1:"+str(app.PORT)}
        if method=="POST":
            h.update({"Content-Type":"application/json","X-ContentMachine":"1"})
        if headers:
            h.update(headers)
        payload=None if body is None else json.dumps(body)
        try:
            con.request(method,path,body=payload,headers=h)
            res=con.getresponse()
            return res.status,json.loads(res.read())
        finally:
            con.close()

    def test_all_six_public_analytics_routes_go_through_actual_server(self):
        with patch("intelligence.analytics.http_api.run_qualification",
                   return_value={"id":1,"status":"partial","blockers":["a_missing_or_incomplete"]}) as qualify,\
             patch("intelligence.analytics.http_api.enqueue_batch",
                   return_value=[{"id":2,"state":"queued"}]) as batch,\
             patch("intelligence.analytics.http_api.process_one",
                   side_effect=[{"id":2,"project_id":PID,"state":"partial"},None]) as work,\
             patch("intelligence.analytics.http_api.list_unified",
                   return_value=[]) as items,\
             patch("intelligence.analytics.http_api.list_jobs",
                   return_value=[{"id":2,"state":"queued"}]) as jobs,\
             patch("intelligence.analytics.http_api.aggregate_project",
                   return_value={"segments":[]}) as agg:
            status,result=self.request("POST",BASE+"/qualify",
                {"video_id":7,"source_id":"legal","request_key":"qualified-007"})
            self.assertEqual(status,200,result)
            self.assertEqual(result["snapshot"]["status"],"partial")
            status,result=self.request("POST",BASE+"/batch",
                {"video_ids":[7],"source_id":"legal","batch_key":"batch-007"})
            self.assertEqual(status,202,result)
            status,result=self.request("POST",BASE+"/jobs/work",{"max_jobs":2})
            self.assertEqual(status,200,result)
            self.assertEqual(result["processed"],1)
            status,result=self.request("GET",BASE+"?include_partial=1&limit=25")
            self.assertEqual(status,200,result)
            status,result=self.request("GET",BASE+"/jobs?limit=25")
            self.assertEqual(status,200,result)
            self.assertEqual(len(result["jobs"]),1)
            status,result=self.request("GET",BASE+
                "/aggregate?source_id=legal&config_id=1&dimension=video_format&min_sample=2")
            self.assertEqual(status,200,result)
            self.assertEqual(result["summary"]["segments"],[])
            for mocked in (qualify,batch,work,items,jobs,agg):
                self.assertTrue(mocked.called,mocked)

    def test_unauthorized_mutation_origin_and_invalid_body_rejected(self):
        for headers in ({"X-ContentMachine":"incorrect"},
                        {"Origin":"https://evil.example"},
                        {"Host":"evil.example"}):
            status,_=self.request("POST",BASE+"/batch",
                                  {"source_id":"legal","video_ids":[7],"batch_key":"batch-007"},
                                  headers)
            self.assertEqual(status,403)
        status,data=self.request("POST",BASE+"/batch",{"unexpected":1})
        self.assertEqual(status,422,data)
        status,data=self.request("GET",BASE+"/aggregate?dimension=hook_mechanism")
        self.assertEqual(status,422,data)

if __name__=="__main__":
    unittest.main()
