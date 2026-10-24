import io
import json
import unittest
from unittest.mock import patch
from urllib.parse import urlparse
from intelligence.project_sources import evidence_http as api

PID='a'*32;SID='00000000-0000-4000-8000-000000000001';EID='00000000-0000-4000-8000-000000000002'
P=f'/api/projects/{PID}/evidence';S=f'/api/projects/{PID}/sources/{SID}/evidence'

class Handler:
    def __init__(self,payload=None):
        data=json.dumps(payload if payload is not None else {}).encode()
        self.headers={'Content-Length':str(len(data))};self.rfile=io.BytesIO(data)
        self.status=None;self.response=None
    def json_response(self,status,obj):self.status=status;self.response=obj

class HTTP(unittest.TestCase):
    def test_enqueue_offline_not_run_in_handler(self):
        h=Handler({'output_id':EID})
        with patch.object(api.jobs,'enqueue',return_value={'job_id':EID,'state':'queued'}) as e:
            with patch.object(api.jobs,'run_one',side_effect=AssertionError('inline work forbidden')):
                self.assertTrue(api.handle_post(h,S+'/enqueue'))
        self.assertEqual(h.status,202);e.assert_called_once_with(PID,SID,{'output_id':EID})
    def test_profile_import_explicit_and_dry_run(self):
        with patch.object(api.jobs,'import_profile',return_value={'fields':4}) as f:
            h=Handler({'dry_run':True});self.assertTrue(api.handle_post(h,P+'/profile/import'))
            self.assertEqual(h.status,200)
            h=Handler({});self.assertTrue(api.handle_post(h,P+'/profile/import'))
            self.assertEqual(h.status,202)
            self.assertEqual(f.call_count,2)
    def test_review_and_verify(self):
        with patch.object(api.jobs,'verify',return_value={'valid':True}) as v:
            h=Handler();self.assertTrue(api.handle_get(h,urlparse(P+'/'+EID+'/verify')))
            self.assertEqual(h.status,200);v.assert_called_once_with(PID,EID)
        with patch.object(api.jobs,'review',return_value={'review_saved':True}):
            h=Handler({'decision':'approved'});self.assertTrue(api.handle_post(h,P+'/'+EID+'/review'))
            self.assertEqual(h.status,201)
    def test_page_validation_and_unknown_routes(self):
        self.assertFalse(api.handle_get(Handler(),urlparse('/api/unrelated')))
        h=Handler();self.assertTrue(api.handle_get(h,urlparse(P+'/items?limit=3&limit=4')));self.assertEqual(h.status,400)
        h=Handler();self.assertTrue(api.handle_get(h,urlparse(P+'/missing')));self.assertEqual(h.status,404)
        h=Handler({'decision':'whatever'});self.assertTrue(api.handle_post(h,P+'/garbage'))
        self.assertEqual(h.status,404)
    def test_get_source_items_and_retry(self):
        with patch.object(api.jobs,'list_evidence',return_value={'items':[]}) as f:
            h=Handler();self.assertTrue(api.handle_get(h,urlparse(S+'/items?offset=3&limit=10')))
            self.assertEqual(h.status,200);self.assertEqual(f.call_args.kwargs['offset'],3)
        with patch.object(api.jobs,'retry',return_value={'state':'queued'}) as r:
            h=Handler({});self.assertTrue(api.handle_post(h,S+'/jobs/'+EID+'/retry'))
            self.assertEqual(h.status,202);r.assert_called_once_with(PID,SID,EID)

if __name__=='__main__':unittest.main()
