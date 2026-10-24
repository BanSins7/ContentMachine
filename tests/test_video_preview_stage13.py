"""Stage13 byte-range preview route: browser-safe, scope-bound, zero write requests."""
import os,json,tempfile,threading,unittest
from uuid import uuid4
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import app
from intelligence.project_sources.video_adapters import AcquisitionProblem

class ProjectVideoPreviewHTTP(unittest.TestCase):
    def setUp(self):
        self.pid=uuid4().hex;self.sid=str(uuid4());self.jid=str(uuid4())
        self.dir=tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup)
        self.asset=os.path.join(self.dir.name,'fixture.mp4')
        # Mock verifier means content need only be valid for HTTP range transport.
        with open(self.asset,'wb') as f:f.write(b'\0\0\0\x18ftypisom'+b'v'*2048)
        self.server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join,3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url='http://127.0.0.1:'+str(self.server.server_port)+\
             '/api/projects/'+self.pid+'/sources/'+self.sid+'/video/jobs/'+self.jid+'/asset'
    def get(self,range_value=None):
        headers={'Host':'localhost:8765'}
        if range_value is not None:headers['Range']=range_value
        req=Request(self.url,headers=headers)
        try:
            with urlopen(req,timeout=5) as f:
                return f.status,f.headers, f.read()
        except HTTPError as error:return error.code,error.headers,error.read()
    def test_content_range_is_small_and_source_is_server_verified(self):
        with patch('intelligence.project_sources.video_jobs.preview_media',
              return_value=(__import__('pathlib').Path(self.asset),2060,'a'*64)) as verifier:
            code,headers,payload=self.get('bytes=0-11')
            self.assertEqual(code,206)
            self.assertEqual(payload,b'\0\0\0\x18ftypisom')
            self.assertEqual(headers.get('Content-Type'),'video/mp4')
            self.assertEqual(headers.get('Content-Range'),'bytes 0-11/2060')
            self.assertEqual(headers.get('Cache-Control'),'private, no-store')
            verifier.assert_called_once_with(self.pid,self.sid,self.jid)
            code,_,data=self.get('bytes=2000-')
            self.assertEqual(code,206);self.assertEqual(len(data),60)

    def test_missing_asset_and_invalid_range_are_not_served(self):
        with patch('intelligence.project_sources.video_jobs.preview_media',
              side_effect=AcquisitionProblem('Wrong project','not_found',404)):
            code,_,body=self.get()
            self.assertEqual(code,404)
            self.assertNotIn(b'ftyp',body)
        with patch('intelligence.project_sources.video_jobs.preview_media',
              return_value=(__import__('pathlib').Path(self.asset),2060,'a'*64)):
            for header in ('bytes=999999-','bytes=10-5','bytes=0-10,20-30'):
                code,_,_=self.get(header)
                self.assertEqual(code,416,header)

if __name__=='__main__':unittest.main()
