"""No network, db or paid model calls. HTTP routing tests for stage 07."""
import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from intelligence.project_sources import transcript_http as api

PID='a'*32
SID='c86f82bf-2f59-428f-b106-098e9a3b05c3'
JID='11111111-1111-4111-8111-111111111111'
BASE=f'/api/projects/{PID}/sources/{SID}/transcription'
class Handler:
    def __init__(self,data=None):
        raw=json.dumps(data if data is not None else {}).encode()
        self.headers={'Content-Length':str(len(raw))};self.rfile=io.BytesIO(raw)
        self.code=None;self.body=None
    def json_response(self,status,payload):self.code=status;self.body=payload

class TranscriptHTTP(unittest.TestCase):
    def test_post_only_enqueues_and_retry_no_extra_fields(self):
        with patch.object(api.jobs,'enqueue',return_value={'job_id':JID,'state':'queued'}) as f:
            h=Handler({'mode':'reuse_sidecar'});self.assertTrue(api.handle_post(h,BASE+'/start'))
            self.assertEqual(h.code,202);f.assert_called_once_with(PID,SID,{'mode':'reuse_sidecar'})
        with patch.object(api.jobs,'retry',return_value={'state':'queued'}):
            h=Handler();api.handle_post(h,BASE+'/jobs/'+JID+'/retry');self.assertEqual(h.code,202)
        h=Handler({'unexpected':True});api.handle_post(h,BASE+'/jobs/'+JID+'/retry');self.assertEqual(h.code,400)

    def test_get_scoped_list_results_pagination(self):
        with patch.object(api.jobs,'list_jobs',return_value={'jobs':[]}) as f:
            h=Handler();api.handle_get(h,urlsplit(BASE+'/jobs'));self.assertEqual(h.code,200)
            f.assert_called_once_with(PID,SID)
        with patch.object(api.jobs,'result',return_value={'state':'completed'}) as f:
            h=Handler();api.handle_get(h,urlsplit(BASE+'/jobs/'+JID+'?offset=4&limit=2'))
            self.assertEqual(h.code,200);f.assert_called_once_with(PID,SID,JID,offset=4,limit=2)
        for suffix in ['/jobs?something=x','/jobs/'+JID+'?limit=x','/jobs/'+JID+'?limit=3&limit=4']:
            h=Handler();api.handle_get(h,urlsplit(BASE+suffix));self.assertEqual(h.code,400)

    def test_other_routes_unaffected(self):
        h=Handler();self.assertFalse(api.handle_get(h,urlsplit('/api/projects/'+PID+'/research-files')))
        self.assertFalse(api.handle_post(h,'/api/projects/'+PID+'/taxonomy-builder/runs'))

if __name__=='__main__':unittest.main()
