"""Stage 08 HTTP routes; no inline FFmpeg, db, or paid network."""
import io,json,unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from intelligence.project_sources import vision_http as http
PID='a'*32;SID='c86f82bf-2f59-428f-b106-098e9a3b05c3';JID='11111111-1111-4111-8111-111111111111'
BASE=f'/api/projects/{PID}/sources/{SID}/vision'
class Handler:
    def __init__(self,body=None):
        b=json.dumps({} if body is None else body).encode();self.rfile=io.BytesIO(b);self.headers={'Content-Length':str(len(b))}
        self.code=None;self.body=None
    def json_response(self,status,body):self.code=status;self.body=body
class VisionHttp(unittest.TestCase):
    def test_routes_no_inline(self):
        for endpoint,function,payload,code in [('prepare','enqueue_prepare',{},202),
                  ('quote','quote',{'prepare_job_id':JID},201),
                  ('analyze','enqueue_analysis',{'quote_id':JID,'approved_estimate_usd':'0.15'},202)]:
            with patch.object(http.jobs,function,return_value={'job_id':JID}) as mock:
                h=Handler(payload);self.assertTrue(http.handle_post(h,BASE+'/'+endpoint));self.assertEqual(h.code,code)
                mock.assert_called_once_with(PID,SID,payload)
        with patch.object(http.jobs,'retry_prepare',return_value={'state':'queued'}):
            h=Handler();http.handle_post(h,BASE+'/jobs/'+JID+'/retry');self.assertEqual(h.code,202)
        h=Handler({'unsafe':'x'});http.handle_post(h,BASE+'/jobs/'+JID+'/retry');self.assertEqual(h.code,400)

    def test_get_paged(self):
        with patch.object(http.jobs,'list_jobs',return_value={'jobs':[]}):
            h=Handler();http.handle_get(h,urlsplit(BASE+'/jobs'));self.assertEqual(h.code,200)
        with patch.object(http.jobs,'result',return_value={'state':'completed'}) as mock:
            h=Handler();http.handle_get(h,urlsplit(BASE+'/jobs/'+JID+'?offset=3&limit=5'))
            self.assertEqual(h.code,200);mock.assert_called_once_with(PID,SID,JID,offset=3,limit=5)
        for path in ('/jobs?extra=1','/jobs/'+JID+'?limit=xx','/jobs/'+JID+'?limit=1&limit=2'):
            h=Handler();http.handle_get(h,urlsplit(BASE+path));self.assertEqual(h.code,400)
        self.assertFalse(http.handle_post(Handler(),'/api/projects/'+PID+'/other'))

if __name__=='__main__':unittest.main()
