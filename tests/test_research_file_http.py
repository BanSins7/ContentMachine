"""HTTP adapter contracts; no live DB/network or external AI calls."""
import base64
import io
import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import urlsplit
from intelligence.project_sources import file_http

PID='a'*32
FID='c86f82bf-2f59-428f-b106-098e9a3b05c3'
BASE=f'/api/projects/{PID}/research-files'

class FakeHandler:
    def __init__(self,body={}):
        self.payload=json.dumps(body).encode();self.rfile=io.BytesIO(self.payload)
        self.wfile=io.BytesIO();self.headers={'Content-Length':str(len(self.payload))}
        self.code=None;self.result=None;self.headers_out={}
    def json_response(self,status,result):self.code=status;self.result=result
    def send_response(self,status):self.code=status
    def send_header(self,key,val):self.headers_out[key]=val
    def end_headers(self):pass

class FileHTTP(unittest.TestCase):
    def test_get_scoped_routes_and_reject_extra_params(self):
        with patch.object(file_http.lib,'list_files',return_value={'count':0,'files':[]}) as f:
            h=FakeHandler()
            self.assertTrue(file_http.handle_get(h,urlsplit(BASE)))
            self.assertEqual(h.code,200);f.assert_called_once_with(PID,include_archived=False)
        h=FakeHandler();file_http.handle_get(h,urlsplit(BASE+'?include_archived=garbage'))
        self.assertEqual(h.code,400)
        h=FakeHandler();self.assertFalse(file_http.handle_get(h,urlsplit('/api/projects/'+PID+'/taxonomy-input')))

    def test_post_add_replace_legacy_and_archive_dispatch(self):
        payload={'name':'a.pdf','data_base64':base64.b64encode(b'%PDF-1.4').decode()}
        with patch.object(file_http.lib,'add_file',return_value={'file_id':FID}) as f:
            h=FakeHandler(payload);self.assertTrue(file_http.handle_post(h,BASE));self.assertEqual(h.code,201)
            f.assert_called_once()
        with patch.object(file_http.lib,'replace_file',return_value={'file_id':FID}):
            h=FakeHandler(payload);file_http.handle_post(h,BASE+'/'+FID+'/replace');self.assertEqual(h.code,201)
        with patch.object(file_http.lib,'import_legacy',return_value={'created':0}):
            h=FakeHandler({'dry_run':True});file_http.handle_post(h,BASE+'/import-legacy');self.assertEqual(h.code,200)
        with patch.object(file_http.lib,'archive_file',return_value={'archived':True}):
            h=FakeHandler({'expected_updated_at':'2026-01-01T00:00:00+00:00'});file_http.handle_post(h,BASE+'/'+FID+'/archive');self.assertEqual(h.code,200)

    def test_download_byte_integrity_and_nosniff(self):
        with patch.object(file_http.lib,'file_download',return_value=(b'%PDF-1.4','russian отчет.pdf','application/pdf')):
            h=FakeHandler();file_http.handle_get(h,urlsplit(BASE+'/'+FID+'/download?version=1'))
            self.assertEqual(h.code,200);self.assertEqual(h.wfile.getvalue(),b'%PDF-1.4')
            self.assertEqual(h.headers_out['X-Content-Type-Options'],'nosniff')
            self.assertIn("filename*=UTF-8''",h.headers_out['Content-Disposition'])

    def test_request_limit_errors(self):
        h=FakeHandler();h.headers['Content-Length']=str(file_http.lib.MAX_POST_BYTES+1)
        file_http.handle_post(h,BASE);self.assertEqual(h.code,413)
        h=FakeHandler({'not_allowed':True});file_http.handle_post(h,BASE+'/import-legacy')
        self.assertEqual(h.code,400)

if __name__=='__main__':unittest.main()
