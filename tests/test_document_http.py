import io
import json
import unittest
from urllib.parse import urlparse
from unittest.mock import patch
from intelligence.project_sources import document_http as h

PID='a'*32
FID='11111111-2222-4333-8444-555555555555'
JID='00000000-1111-4222-8333-444444444444'
URL=f'/api/projects/{PID}/research-files/{FID}'

class Fake:
    def __init__(self,data=None):
        b=json.dumps(data if data is not None else {}).encode()
        self.headers={'Content-Length':str(len(b))}
        self.rfile=io.BytesIO(b)
        self.code=None;self.data=None
    def json_response(self,code,data):self.code=code;self.data=data

class DocumentHTTP(unittest.TestCase):
    def test_enqueue_no_inline_run(self):
        with patch.object(h.jobs,'enqueue',return_value={'job_id':JID,'state':'queued'}) as enqueue:
            f=Fake({'config':{'ocr':False}})
            self.assertTrue(h.handle_post(f,URL+'/extract'))
            self.assertEqual(f.code,202)
            enqueue.assert_called_once_with(PID,FID,{'config':{'ocr':False}})
        self.assertFalse(h.handle_post(Fake(),URL+'/download'))

    def test_get_jobs_and_paged_result(self):
        with patch.object(h.jobs,'list_jobs',return_value={'jobs':[]}) as list_jobs:
            f=Fake();self.assertTrue(h.handle_get(f,urlparse(URL+'/extractions')))
            self.assertEqual(f.code,200)
            list_jobs.assert_called_once_with(PID,FID)
        with patch.object(h.jobs,'get_result',return_value={'state':'completed'}) as result:
            f=Fake();h.handle_get(f,urlparse(URL+'/extractions/'+JID+'?offset=3&limit=5'))
            self.assertEqual(f.code,200)
            result.assert_called_once_with(PID,FID,JID,offset=3,limit=5)

    def test_errors_and_route_isolation(self):
        f=Fake();f.headers['Content-Length']='9000';h.handle_post(f,URL+'/extract')
        self.assertEqual(f.code,413)
        f=Fake();h.handle_get(f,urlparse(URL+'/extractions?offset=5'))
        self.assertEqual(f.code,400)
        f=Fake();h.handle_get(f,urlparse(URL+'/extractions/'+JID+'?offset=-9'))
        self.assertEqual(f.code,400)
        self.assertFalse(h.handle_get(Fake(),urlparse('/api/projects/'+PID+'/taxonomy-input')))

    def test_manual_retry_only(self):
        with patch.object(h.jobs,'retry',return_value={'state':'queued'}) as retry:
            f=Fake({});h.handle_post(f,URL+'/extractions/'+JID+'/retry')
            self.assertEqual(f.code,202);retry.assert_called_once_with(PID,FID,JID)
        f=Fake({'do':'bad'});h.handle_post(f,URL+'/extractions/'+JID+'/retry')
        self.assertEqual(f.code,400)

if __name__=='__main__':unittest.main()
