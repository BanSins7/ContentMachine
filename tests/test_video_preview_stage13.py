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
            code,headers,payload=self.get()
            self.assertEqual(code,200)
            self.assertIsNone(headers.get('Content-Range'))
            self.assertEqual(len(payload),2060)
            code,headers,payload=self.get('bytes=-8')
            self.assertEqual(code,206)
            self.assertEqual(headers.get('Content-Range'),'bytes 2052-2059/2060')
            self.assertEqual(payload,b'v'*8)

    def test_missing_asset_and_invalid_range_are_not_served(self):
        with patch('intelligence.project_sources.video_jobs.preview_media',
              side_effect=AcquisitionProblem('Wrong project','not_found',404)):
            code,_,body=self.get()
            self.assertEqual(code,404)
            self.assertNotIn(b'ftyp',body)
        with patch('intelligence.project_sources.video_jobs.preview_media',
              return_value=(__import__('pathlib').Path(self.asset),2060,'a'*64)):
            for header in ('bytes=999999-','bytes=10-5','bytes=0-10,20-30',
                           'bytes=-0','bytes='+('9'*4000)+'-'):
                code,_,_=self.get(header)
                self.assertEqual(code,416,header)


class ProjectVideoPreviewIntegrity(unittest.TestCase):
    def setUp(self):
        import hashlib
        self.pid=uuid4().hex;self.sid=str(uuid4());self.jid=str(uuid4())
        self.version_id=str(uuid4())
        self.dir=tempfile.TemporaryDirectory();self.addCleanup(self.dir.cleanup)
        from pathlib import Path
        self.root=Path(self.dir.name)
        self.path=self.root/'acquired.mp4'
        self.data=bytes((0,0,0,24))+b'ftypisom'+b'video bytes'*20
        self.path.write_bytes(self.data)
        self.hash=hashlib.sha256(self.data).hexdigest()
        self.payload={'schema_version':'video-acquisition/v1','video_sha256':self.hash,
                      'source_id':self.sid,'source_version_id':self.version_id,
                      'byte_size':len(self.data),'video_path':str(self.path)}
    def _preview(self,row,root=None):
        from intelligence.project_sources.video_jobs import preview_media
        class FakeConn:
            def __enter__(self):return self
            def __exit__(self,*args):pass
            def execute(self,*args):return self
            def fetchone(self):return row
        class Adapter:
            def resolve_identity(self,url):return 'immutable-video-id'
            def candidate_paths(self,ident):
                return [(root or self_outer.root,self_outer.path,self_outer.root/'meta.json')]
        self_outer=self
        with patch('intelligence.project_sources.video_jobs._pid',return_value={'id':self.pid}),patch(
              'intelligence.project_sources.video_jobs._conn',return_value=FakeConn()),patch(
              'intelligence.project_sources.video_adapters.adapter_for',return_value=Adapter()):
            return preview_media(self.pid,self.sid,self.jid)
    def test_exact_archive_asset_and_sha_is_verified_on_read(self):
        row=('completed','youtube','https://youtube.com/watch?v=abcd',
             self.payload,str(self.path),len(self.data),self.hash,'video/mp4',self.version_id)
        path,size,digest=self._preview(row)
        self.assertEqual(path,self.path)
        self.assertEqual(size,len(self.data))
        self.assertEqual(digest,self.hash)
        self.path.write_bytes(self.data+b'external mutation')
        with self.assertRaises(AcquisitionProblem) as cm:
            self._preview(row)
        self.assertIn(cm.exception.code,('hash_mismatch','asset_unavailable'))
        # Even a valid unchanged file may not be served if the immutable
        # output points to another source/version in this project.
        self.path.write_bytes(self.data)
        foreign={**self.payload,'source_id':str(uuid4())}
        with self.assertRaises(AcquisitionProblem) as mismatch:
            self._preview(row[:3]+(foreign,)+row[4:])
        self.assertEqual(mismatch.exception.code,'asset_integrity')
    def test_unbound_or_unapproved_asset_never_served(self):
        row=('completed','youtube','https://youtube.com/watch?v=abcd',
             self.payload,str(self.path),len(self.data),self.hash,'video/mp4',self.version_id)
        with self.assertRaises(AcquisitionProblem) as cm:
            self._preview(None)
        self.assertEqual(cm.exception.code,'asset_unavailable')
        with self.assertRaises(AcquisitionProblem):
            self._preview(('processing',)+row[1:])
        with self.assertRaises(AcquisitionProblem):
            self._preview(row[:6]+('bad'*21+'x',)+row[7:])
        from pathlib import Path
        with tempfile.TemporaryDirectory() as untrusted:
            with self.assertRaises(AcquisitionProblem) as cm:
                self._preview(row,root=Path(untrusted))
            self.assertEqual(cm.exception.code,'unsafe_path')

if __name__=='__main__':unittest.main()
