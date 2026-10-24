import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlparse
from intelligence.project_sources import timeline_http as api

PID='a'*32;SID='00000000-0000-4000-8000-000000000001';JID='00000000-0000-4000-8000-000000000002'
URL=f'/api/projects/{PID}/sources/{SID}/fusion'

class Handler:
    def __init__(self,data=None):
        body=json.dumps(data or {}).encode();self.headers={'Content-Length':str(len(body))};self.rfile=io.BytesIO(body);self.status=None;self.payload=None
    def json_response(self,status,obj):self.status=status;self.payload=obj

class HTTP(unittest.TestCase):
    def test_enqueue_does_not_call_runner(self):
        h=Handler({})
        with patch.object(api.jobs,'enqueue',return_value={'job_id':JID,'created':True}) as q:
            with patch.object(api.jobs,'run_one',side_effect=AssertionError('HTTP runs no workers')):
                self.assertTrue(api.handle_post(h,URL+'/start'))
        self.assertEqual(h.status,202);q.assert_called_once_with(PID,SID,{})
    def test_get_paged_and_export(self):
        with patch.object(api.jobs,'result',return_value={'state':'completed'}) as q:
            h=Handler();self.assertTrue(api.handle_get(h,urlparse(URL+'/jobs/'+JID+'?offset=2&limit=4')))
            self.assertEqual(h.status,200);self.assertEqual(q.call_args.kwargs['offset'],2)
            h=Handler();self.assertTrue(api.handle_get(h,urlparse(URL+'/jobs/'+JID+'/export')))
            self.assertTrue(q.call_args.kwargs['export'])
    def test_reject_route_and_query(self):
        self.assertFalse(api.handle_get(Handler(),urlparse('/api/other/')))
        h=Handler();self.assertTrue(api.handle_get(h,urlparse(URL+'/jobs/'+JID+'?limit=1&limit=2')))
        self.assertEqual(h.status,400)
        h=Handler();self.assertTrue(api.handle_get(h,urlparse(URL+'/jobs/'+JID+'/export?offset=0')))
        self.assertEqual(h.status,400)
        h=Handler({'unexpected':1});self.assertTrue(api.handle_post(h,URL+'/jobs/'+JID+'/retry'))
        self.assertEqual(h.status,400)
    def test_retry_is_explicit(self):
        h=Handler({})
        with patch.object(api.jobs,'retry',return_value={'state':'queued'}) as q:
            self.assertTrue(api.handle_post(h,URL+'/jobs/'+JID+'/retry'))
            q.assert_called_once_with(PID,SID,JID)
        self.assertEqual(h.status,202)

if __name__=='__main__':unittest.main()
