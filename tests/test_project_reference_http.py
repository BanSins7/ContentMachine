"""Localhost API contract tests. Mock DB service; no fetch/provider calls."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from unittest.mock import patch

import app

PID='a'*32
SID='11111111-1111-1111-1111-111111111111'

class TestProjectReferenceHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Handler's localhost host guard compares against app.PORT. Bind that port
        # temporarily and restore the global value immediately after this class.
        cls.server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        cls.old_port=app.PORT
        app.PORT=cls.server.server_address[1]
        cls.thread=threading.Thread(target=cls.server.serve_forever,daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown();cls.thread.join(timeout=5);cls.server.server_close()
        app.PORT=cls.old_port

    def req(self,path,method='GET',data=None,headers=None):
        url='http://127.0.0.1:'+str(app.PORT)+path
        payload=json.dumps(data).encode() if data is not None else None
        req=Request(url,data=payload,method=method,headers={
            'Host':'127.0.0.1:'+str(app.PORT),
            'Content-Type':'application/json',
            **(headers or {})
        })
        try:
            with urlopen(req,timeout=4) as r:
                return r.status,json.loads(r.read().decode())
        except HTTPError as e:
            return e.code,json.loads(e.read().decode())

    def test_get_list_and_manual_create(self):
        from intelligence.project_sources import repository as repo
        with patch.object(repo,'list_sources',return_value={'sources':[], 'count':0,'processing_started':False}), \
             patch.object(repo,'create_source',return_value={'source':{'source_id':SID}}) as create:
            code,data=self.req('/api/projects/'+PID+'/sources')
            self.assertEqual(code,200);self.assertEqual(data['count'],0)
            code,data=self.req('/api/projects/'+PID+'/sources','POST',
                {'url':'https://example.com','source_type':'product_website'},
                {'X-ContentMachine':'1'})
            self.assertEqual(code,201);self.assertEqual(data['source']['source_id'],SID)
            create.assert_called_once()

    def test_patch_delete_import_and_host_protection(self):
        from intelligence.project_sources import repository as repo
        with patch.object(repo,'update_source',return_value={'source':{'source_id':SID}}), \
             patch.object(repo,'archive_source',return_value={'source':{'source_id':SID},'archived':True}), \
             patch.object(repo,'import_legacy',return_value={'created':1,'origins_added':1,'skipped':0,'warnings':[],'total_proposals':1,'dry_run':False}):
            base='/api/projects/'+PID+'/sources'
            code,_=self.req(base+'/'+SID,'PATCH',{'label':'new','expected_updated_at':'2026-10-24T00:00:00+00:00'}, {'X-ContentMachine':'1'})
            self.assertEqual(code,200)
            code,_=self.req(base+'/'+SID,'DELETE',{'expected_updated_at':'2026-10-24T00:00:00+00:00'}, {'X-ContentMachine':'1'})
            self.assertEqual(code,200)
            code,data=self.req(base+'/import-legacy','POST',{}, {'X-ContentMachine':'1'})
            self.assertEqual(code,200);self.assertEqual(data['created'],1)
            code,_=self.req(base,'POST',{'url':'https://example.com','source_type':'product_website'})
            self.assertEqual(code,403)
            code,_=self.req(base,'GET',headers={'Host':'evil.example'})
            self.assertEqual(code,403)

    def test_missing_path_and_payload_bounds(self):
        base='/api/projects/'+PID+'/sources'
        code,_=self.req(base+'/not-a-uuid','PATCH',{'label':'x'},{'X-ContentMachine':'1'})
        self.assertEqual(code,404)
        code,_=self.req(base,'POST',{'url':'x'*60000},{'X-ContentMachine':'1'})
        self.assertEqual(code,400)

if __name__=='__main__':unittest.main()
