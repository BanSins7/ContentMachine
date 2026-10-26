"""PostgreSQL17 web job integration, completely offline mocked OpenRouter transport."""
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

DSN=os.environ.get('CM_TEST_PG_DSN')
URL='https://product.acme.org/features'

@unittest.skipUnless(DSN,'Needs ephemeral PostgreSQL 17 in CI')
class WebJobsDB(unittest.TestCase):
    def setUp(self):
        import psycopg
        from intelligence.project_sources import web_jobs as jobs, repository as repo
        from intelligence.project_sources.web_extractor import parse_provider_response
        self.jobs=jobs;self.repo=repo;self.pid=uuid4().hex;self.other=uuid4().hex
        with psycopg.connect(DSN) as conn:
            for pid in (self.pid,self.other):
                conn.execute("""INSERT INTO project_registry
                  (id,name,profile_revision,profile_hash,created_at,updated_at)
                  VALUES (%s,'web-test',1,%s,now(),now())""",(pid,'a'*64))
        self.p=patch.object(repo,'_project',side_effect=lambda pid:{'id':pid})
        self.c=patch.object(repo,'_connection',side_effect=lambda:psycopg.connect(DSN))
        self.p.start();self.addCleanup(self.p.stop)
        self.c.start();self.addCleanup(self.c.stop)
        self.sid=repo.create_source(self.pid,{'source_type':'product_website','url':URL})['source']['source_id']

    def _mock_result(self,url,key):
        from intelligence.project_sources.web_extractor import digest
        self.assertEqual(url,URL);self.assertEqual(key,'ci-key')
        page='Our app recognizes meals from photographs.'
        return {'schema_version':'web-source-extract/v1','model':'anthropic/claude-sonnet-4.6',
                'extractor_version':'web-sonnet46/v1','engine':'openrouter',
                'requested_url':url,'page_url':url,'fetch_status':'completed','title':'Product',
                'language':'en','page_text':page,
                'page_text_sha256':__import__('hashlib').sha256(page.encode()).hexdigest(),
                'summary':'Photo meal recognition.',
                'sections':[{'heading':'Features','text':'Meal recognition','quote':'photographs',
                            'quote_start':32,'quote_end':43}],
                'claims':[],'limitation':'','trust_level':'model_reported_unverified',
                'source_content_verified':False,'provenance':{'kind':'model_reported_web_fetch'},
                'usage':{'prompt_tokens':150,'completion_tokens':40},
                'actual_cost_usd':'0.024','provider_request_id':'ci_req_1'}

    def test_quote_consent_idempotency_one_api_call_and_history(self):
        j=self.jobs;quote=j.cost_quote(self.pid,self.sid)
        self.assertEqual(quote['model'],'anthropic/claude-sonnet-4.6')
        with self.assertRaises(j.WebProblem):j.enqueue(self.pid,self.sid,{'quote_id':quote['quote_id'],'approve_estimate_usd':'0.05'})
        body={'quote_id':quote['quote_id'],'approve_estimate_usd':quote['estimated_usd']}
        first=j.enqueue(self.pid,self.sid,body)
        self.assertTrue(first['created']);self.assertEqual(first['state'],'queued')
        again=j.enqueue(self.pid,self.sid,body)
        self.assertFalse(again['created']);self.assertEqual(first['job_id'],again['job_id'])
        with self.assertRaises(j.WebProblem):j.cost_quote(self.pid,self.sid)
        calls=[]
        def transport(url,key):calls.append(url);return self._mock_result(url,key)
        out=j.run_one(send=transport,key_provider=lambda:'ci-key')
        self.assertEqual(out['state'],'completed');self.assertEqual(len(calls),1)
        self.assertIsNone(j.run_one(send=transport,key_provider=lambda:'ci-key'))
        result=j.result(self.pid,self.sid,first['job_id'])
        self.assertEqual(result['result']['trust_level'],'model_reported_unverified')
        self.assertEqual(result['actual_cost_usd'],'0.02400000')
        self.assertNotEqual(result['result_source_version_id'],None)
        with __import__('psycopg').connect(DSN) as conn:
            assert conn.execute('''SELECT count(*) FROM project_source_versions WHERE project_id=%s AND source_id=%s''',
                                (self.pid,self.sid)).fetchone()[0]==2
        # Same paid quote remains idempotent AFTER completion and source version changes.
        after=j.enqueue(self.pid,self.sid,body)
        self.assertFalse(after['created']);self.assertEqual(after['job_id'],first['job_id'])
        quoted=j.cost_quote(self.pid,self.sid)
        self.assertNotEqual(quoted['quote_id'],quote['quote_id'])
        rerun=j.enqueue(self.pid,self.sid,{'quote_id':quoted['quote_id'],
                       'approve_estimate_usd':quoted['estimated_usd']})
        self.assertEqual(j.run_one(send=transport,key_provider=lambda:'ci-key')['state'],'completed')
        self.assertEqual(j.result(self.pid,self.sid,rerun['job_id'])['result_source_version_id'],
                         result['result_source_version_id'])
        with __import__('psycopg').connect(DSN) as conn:
            self.assertEqual(conn.execute('''SELECT count(*) FROM project_source_versions WHERE
               project_id=%s AND source_id=%s''',(self.pid,self.sid)).fetchone()[0],2)

    def test_missing_key_does_not_call_provider(self):
        j=self.jobs;quote=j.cost_quote(self.pid,self.sid)
        payload={'quote_id':quote['quote_id'],'approve_estimate_usd':quote['estimated_usd']}
        task=j.enqueue(self.pid,self.sid,payload)
        out=j.run_one(send=lambda *_:self.fail('Must not call network'),key_provider=lambda:'')
        self.assertEqual(out['state'],'failed')
        self.assertEqual(j.result(self.pid,self.sid,task['job_id'])['charge_state'],'not_applicable')

    def test_provider_error_is_never_auto_retried(self):
        j=self.jobs;from intelligence.project_sources.web_extractor import WebProblem
        quote=j.cost_quote(self.pid,self.sid)
        payload={'quote_id':quote['quote_id'],'approve_estimate_usd':quote['estimated_usd']}
        jid=j.enqueue(self.pid,self.sid,payload)['job_id']
        def fail(*_):raise WebProblem('provider_timeout','Unknown state',502)
        out=j.run_one(send=fail,key_provider=lambda:'ci-key')
        self.assertEqual(out['state'],'failed')
        self.assertEqual(j.result(self.pid,self.sid,jid)['charge_state'],'unknown')
        self.assertIsNone(j.run_one(send=lambda *_:self.fail('retried'),key_provider=lambda:'ci-key'))

    def test_other_project_cannot_read_and_account_cannot_fetch(self):
        j=self.jobs
        with self.assertRaises(j.WebProblem):j.list_runs(self.other,self.sid)
        account=self.repo.create_source(self.pid,{'source_type':'competitor_account',
                        'url':'https://www.instagram.com/examplebrand'})['source']['source_id']
        with self.assertRaises(j.WebProblem):j.cost_quote(self.pid,account)

if __name__=='__main__':unittest.main()
