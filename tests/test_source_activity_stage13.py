"""Stage13 unified project activity: scope, persistence, state and API contracts."""
import json,os,threading,unittest
from uuid import uuid4
from http.server import ThreadingHTTPServer
from urllib.request import Request,urlopen
from urllib.error import HTTPError
from unittest.mock import patch
import app

from intelligence.project_sources.activity import activity,_status,ActivityProblem
from test_context_db import ContextDB

class ActivityStates(unittest.TestCase):
    def test_never_promote_registered_queued_or_partial_to_ready(self):
        self.assertEqual(_status(None,'registered',[],0),'registered')
        self.assertEqual(_status(str(uuid4()),'completed',['queued'],3),'queued')
        self.assertEqual(_status(str(uuid4()),'completed',['processing'],3),'processing')
        self.assertEqual(_status(str(uuid4()),'completed',['partial'],3),'partial')
        self.assertEqual(_status(str(uuid4()),'completed',['failed'],3),'failed')
        self.assertEqual(_status(str(uuid4()),'completed',['completed'],3),'ready')
        self.assertEqual(_status(str(uuid4()),'completed',['completed'],0),
                         'processed_without_evidence')
        self.assertEqual(_status(None,'registered',['completed'],0),'partial')

class ActivityHTTP(unittest.TestCase):
    def setUp(self):
        self.pid=uuid4().hex
        self.server=ThreadingHTTPServer(('127.0.0.1',0),app.Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True)
        self.thread.start()
        self.addCleanup(self.thread.join,3)
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
    def req(self,path):
        req=Request('http://127.0.0.1:'+str(self.server.server_port)+path,
                    headers={'Host':'localhost:8765'})
        try:
            with urlopen(req,timeout=5) as response:
                return response.status,json.loads(response.read())
        except HTTPError as ex:
            return ex.code,json.loads(ex.read())
    def test_readonly_activity_and_strict_query(self):
        path='/api/projects/'+self.pid+'/source-activity'
        with patch('intelligence.project_sources.activity_http.activity',
            return_value={'items':[],'count':0,'read_only':True}) as read:
            status,value=self.req(path+'?offset=2&limit=30')
            self.assertEqual(status,200);self.assertTrue(value['read_only'])
            read.assert_called_once_with(self.pid,offset=2,limit=30)
            for query in ('?foo=bar','?limit=-5','?limit=20&limit=30',
                          '?offset=x','?limit=2&secret=1'):
                status,_=self.req(path+query)
                self.assertEqual(status,400,query)
            self.assertEqual(read.call_count,1)

@unittest.skipUnless(os.environ.get('CM_TEST_PG_DSN'),'PostgreSQL17 required')
class ActivityDatabase(ContextDB):
    def test_project_scoped_activity_and_queued_not_ready(self):
        import psycopg
        pid=self.pid
        with psycopg.connect(os.environ['CM_TEST_PG_DSN']) as conn:
            ver=conn.execute('SELECT active_version_id FROM project_sources WHERE project_id=%s AND id=%s',
                             (pid,self.sid)).fetchone()[0]
            jid=conn.execute("""INSERT INTO source_extraction_jobs
              (project_id,source_id,source_version_id,stage,state,idempotency_key,cost_estimate_usd)
              VALUES(%s,%s,%s,'vision','queued',%s,0) RETURNING id""",
              (pid,self.sid,ver,'stage13-test:'+self.sid)).fetchone()[0]
        source_connect=patch('intelligence.project_sources.activity._conn',
                             side_effect=lambda:psycopg.connect(os.environ['CM_TEST_PG_DSN']))
        source_connect.start();self.addCleanup(source_connect.stop)
        self.assertEqual(activity(pid)['items'][0]['display_state'],'queued')
        self.assertEqual(activity(self.other)['items'],[])
        with psycopg.connect(os.environ['CM_TEST_PG_DSN']) as conn:
            conn.execute("""UPDATE source_extraction_jobs SET state='partial',updated_at=clock_timestamp()
                 WHERE project_id=%s AND id=%s""",(pid,jid))
        self.assertEqual(activity(pid)['items'][0]['display_state'],'partial')
        with psycopg.connect(os.environ['CM_TEST_PG_DSN']) as conn:
            conn.execute("""UPDATE source_extraction_jobs SET state='completed',updated_at=clock_timestamp()
                 WHERE project_id=%s AND id=%s""",(pid,jid))
        self.assertEqual(activity(pid)['items'][0]['display_state'],'ready')
        with self.assertRaises(ActivityProblem):
            activity(pid,limit=101)
        with self.assertRaises(ActivityProblem):
            activity(uuid4().hex)

if __name__=='__main__':unittest.main()
