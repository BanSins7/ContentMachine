"""API routes stay strictly scoped; no synchronous yt-dlp work from HTTP."""
import io,json,unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from intelligence.project_sources import video_http

PID='a'*32
SID='a1009e77-6ed0-4263-8774-f443e79c9e28'
JID='918a663c-62ac-4a4d-a136-e2bba7ab4931'
BASE=f'/api/projects/{PID}/sources/{SID}/video'

class Handler:
    def __init__(self,body=None):
        self.data=json.dumps({} if body is None else body).encode('utf-8');self.rfile=io.BytesIO(self.data)
        self.headers={'Content-Length':str(len(self.data))};self.code=None;self.result=None
    def json_response(self,code,result):self.code=code;self.result=result

class HTTP(unittest.TestCase):
    def test_enqueue_is_not_synchronous_download(self):
        with patch.object(video_http.video_jobs,'enqueue',return_value={'job_id':JID,'state':'queued'}) as f:
            h=Handler();self.assertTrue(video_http.handle_post(h,BASE+'/start'));self.assertEqual(h.code,202)
            f.assert_called_once_with(PID,SID,{})
    def test_jobs_results_cancel_retry(self):
        with patch.object(video_http.video_jobs,'list_jobs',return_value={'jobs':[]}) as f:
            h=Handler();self.assertTrue(video_http.handle_get(h,urlsplit(BASE+'/jobs')))
            self.assertEqual(h.code,200);f.assert_called_once_with(PID,SID)
        with patch.object(video_http.video_jobs,'result',return_value={'state':'completed'}):
            h=Handler();video_http.handle_get(h,urlsplit(BASE+'/jobs/'+JID));self.assertEqual(h.code,200)
        for kind in ('retry','cancel'):
            with patch.object(video_http.video_jobs,kind,return_value={'state':kind}) as fn:
                h=Handler();video_http.handle_post(h,BASE+'/jobs/'+JID+'/'+kind)
                self.assertEqual(h.code,200);fn.assert_called_once_with(PID,SID,JID)
    def test_invalid_json_route_and_query(self):
        h=Handler();video_http.handle_get(h,urlsplit(BASE+'/jobs?x=y'));self.assertEqual(h.code,400)
        h=Handler({'bad':True});video_http.handle_post(h,BASE+'/jobs/'+JID+'/cancel');self.assertEqual(h.code,400)
        h=Handler();video_http.handle_post(h,BASE+'/secret');self.assertEqual(h.code,404)
        h=Handler();self.assertFalse(video_http.handle_get(h,urlsplit('/api/projects/'+PID+'/sources')))

if __name__=='__main__':unittest.main()
