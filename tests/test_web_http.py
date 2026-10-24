"""Adapter tests require no DB or network; state-changing requests are POST only."""
import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from intelligence.project_sources import web_http

PID='a'*32
SID='11111111-2222-4333-8444-555555555555'
JID='55555555-2222-4333-8444-555555555555'
BASE=f'/api/projects/{PID}/sources/{SID}/web'

class Handler:
    def __init__(self,data=None):
        payload=json.dumps({} if data is None else data).encode()
        self.headers={'Content-Length':str(len(payload))};self.rfile=io.BytesIO(payload)
        self.code=None;self.data=None
    def json_response(self,status,data):self.code=status;self.data=data

class WebHTTPTests(unittest.TestCase):
    def test_get_jobs_and_result(self):
        with patch.object(web_http.jobs,'list_runs',return_value={'jobs':[]}) as fn:
            h=Handler();self.assertTrue(web_http.handle_get(h,urlsplit(BASE+'/jobs')))
            self.assertEqual(h.code,200);fn.assert_called_once_with(PID,SID)
        with patch.object(web_http.jobs,'result',return_value={'state':'partial'}) as fn:
            h=Handler();web_http.handle_get(h,urlsplit(BASE+'/jobs/'+JID));self.assertEqual(h.code,200)
            fn.assert_called_once_with(PID,SID,JID)

    def test_quote_and_start(self):
        with patch.object(web_http.jobs,'cost_quote',return_value={'quote_id':'123'}) as fn:
            h=Handler();web_http.handle_post(h,BASE+'/quote');self.assertEqual(h.code,201)
            fn.assert_called_once_with(PID,SID)
        with patch.object(web_http.jobs,'enqueue',return_value={'job_id':JID}) as fn:
            h=Handler({'quote_id':'x','approve_estimate_usd':'0.15'})
            web_http.handle_post(h,BASE+'/start');self.assertEqual(h.code,202)
            fn.assert_called_once()

    def test_wrong_path_query_invalid_payload(self):
        self.assertFalse(web_http.handle_post(Handler(),'/api/projects/'+PID+'/sources'))
        h=Handler();web_http.handle_get(h,urlsplit(BASE+'?x=1'));self.assertEqual(h.code,400)
        h=Handler([1,2]);web_http.handle_post(h,BASE+'/quote');self.assertEqual(h.code,400)
        h=Handler({'x':1});web_http.handle_post(h,BASE+'/quote');self.assertEqual(h.code,400)
        h=Handler();h.headers['Content-Length']='500000';web_http.handle_post(h,BASE+'/start');self.assertEqual(h.code,413)

if __name__=='__main__':unittest.main()
