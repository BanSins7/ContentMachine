"""Stage 10 real PostgreSQL 17 integration tests: fully offline and isolated."""
import hashlib
import os
import unittest
from unittest.mock import patch
from uuid import uuid4

DSN=os.environ.get('CM_TEST_PG_DSN')

@unittest.skipUnless(DSN,'PostgreSQL17 CI required')
class EvidenceDB(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.types.json import Jsonb
        from intelligence.project_sources import evidence_jobs as jobs
        from intelligence.project_sources.evidence_normalizer import sha
        self.j=jobs;self.pid=uuid4().hex;self.other=uuid4().hex
        self.key='a'*64
        self._patches=[patch.object(jobs,'_pid',side_effect=lambda p: {} if p==self.pid else (_ for _ in ()).throw(jobs.EvidenceProblem('project','not_found',404))),
                       patch.object(jobs,'_conn',side_effect=lambda:psycopg.connect(DSN))]
        for p in self._patches:p.start();self.addCleanup(p.stop)
        with psycopg.connect(DSN) as conn:
            for pid in (self.pid,self.other):
                conn.execute('''INSERT INTO project_registry(id,name,profile_revision,profile_hash,created_at,updated_at)
                    VALUES(%s,'evidence-stage10',1,%s,now(),now())''',(pid,self.key))
            conn.execute('''INSERT INTO project_profile_versions(project_id,revision,schema_version,profile_hash,profile)
                    VALUES(%s,1,1,%s,%s)''',(self.pid,self.key,Jsonb({'product':{'name':'Alpha'}})))
        self.sources=[]

    def tearDown(self):
        import psycopg
        if not DSN:return
        with psycopg.connect(DSN) as conn:
            conn.execute("""UPDATE source_extraction_jobs SET state='cancelled',lease_until=NULL,updated_at=clock_timestamp()
                WHERE project_id=%s AND stage='evidence_normalization' AND state IN ('queued','processing')""",(self.pid,))

    def source(self,kind='research_document'):
        import psycopg
        from psycopg.types.json import Jsonb
        sid=str(uuid4())
        with psycopg.connect(DSN) as conn:
            conn.execute('''INSERT INTO project_sources(id,project_id,source_type,original_input,identity_key)
                VALUES(%s,%s,%s,'research',%s)''',(sid,self.pid,kind+'-'+uuid4().hex))
            vid=conn.execute('''INSERT INTO project_source_versions(project_id,source_id,version_no,source_locator)
                VALUES(%s,%s,1,%s) RETURNING id''',(self.pid,sid,Jsonb({'kind':'test'}))).fetchone()[0]
            conn.execute('UPDATE project_sources SET active_version_id=%s WHERE project_id=%s AND id=%s',(vid,self.pid,sid))
        self.sources.append(sid)
        return sid,vid

    def put(self,sid,vid,output):
        import psycopg
        from psycopg.types.json import Jsonb
        from intelligence.project_sources.evidence_normalizer import sha
        version=output['schema_version']
        types={'document-segments/v1':'document_segments','web-source-extract/v1':'web_segments',
                'transcript/v1':'transcript','vision-prepare/v1':'visual_scenes'}
        with psycopg.connect(DSN) as conn:
            return conn.execute('''INSERT INTO source_processing_outputs
              (project_id,source_id,source_version_id,output_type,schema_version,output,output_sha256)
              VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id''',
              (self.pid,sid,vid,types[version],version,Jsonb(output),sha(output))).fetchone()[0]

    def doc(self,text):
        return {'schema_version':'document-segments/v1','parser_version':'document-parser/1',
                'format':'.txt','status':'completed','segments':[{'text':text,
                'locator':{'start_char':0,'end_char':len(text)},'role':'paragraph'}],
                'tables':[],'coverage':{},'warnings':[]}

    def test_documents_exact_duplicates_independent_ids_and_reviews(self):
        j=self.j
        a,ver_a=self.source();b,ver_b=self.source()
        text='Product supports shared searchable notes for teams.'
        o1=self.put(a,ver_a,self.doc(text));o2=self.put(b,ver_b,self.doc(text))
        job1=j.enqueue(self.pid,a,{'output_id':str(o1)})
        self.assertTrue(job1['job_id']);self.assertTrue(job1['reused'] is False)
        self.assertEqual(j.enqueue(self.pid,a,{'output_id':str(o1)})['job_id'],job1['job_id'])
        self.assertEqual(j.run_one()['state'],'completed')
        assert not j.run_one()
        item1=j.list_evidence(self.pid,sid=a)['items'][0]
        self.assertTrue(j.verify(self.pid,item1['evidence_id'])['valid'])
        self.assertEqual(j.review(self.pid,item1['evidence_id'],{'decision':'approved'})['decision'],'approved')
        with self.assertRaises(j.EvidenceProblem):j.review(self.pid,item1['evidence_id'],{'decision':'rejected'})
        j.enqueue(self.pid,b,{'output_id':str(o2)});self.assertEqual(j.run_one()['state'],'completed')
        item2=j.list_evidence(self.pid,sid=b)['items'][0]
        self.assertNotEqual(item1['evidence_id'],item2['evidence_id'])
        self.assertEqual(item1['exact_group_id'],item2['exact_group_id'])
        self.assertEqual(j.list_evidence(self.pid,sid=a)['items'][0]['exact_duplicate_occurrences'],2)
        self.assertNotEqual(item1['citation'],item2['citation'])

    def test_web_forgery_is_rejected_not_approved(self):
        import psycopg
        sid,vid=self.source('competitor_website')
        page='Our service offers budget tracking and reminders.'
        out={'schema_version':'web-source-extract/v1','page_url':'https://competitor.example/',
             'page_text':page,'page_text_sha256':hashlib.sha256(page.encode()).hexdigest(),
             'trust_level':'model_reported_unverified','claims':[
                 {'claim':'Has a personal assistant','quote':'budget tracking',
                  'quote_start':page.index('budget tracking'),'quote_end':page.index('budget tracking')+len('budget tracking')},
                 {'claim':'Fabricated','quote':'something nonexistent','quote_start':2,'quote_end':23}],
             'sections':[]}
        oid=self.put(sid,vid,out)
        self.j.enqueue(self.pid,sid,{'output_id':str(oid)})
        self.assertEqual(self.j.run_one()['state'],'completed')
        items=self.j.list_evidence(self.pid,sid=sid)['items']
        self.assertEqual(len(items),1)
        self.assertEqual(items[0]['exact_quote'],'budget tracking')
        self.assertEqual(items[0]['knowledge_scope'],'competitor_research')
        self.assertEqual(items[0]['trust_label'],'model_reported_unverified')
        self.assertIn('NOT against live/raw website',self.j.verify(self.pid,items[0]['evidence_id'])['trust_note'])

    def test_profile_revisions_stale_claim_conflicts(self):
        import psycopg
        from psycopg.types.json import Jsonb
        j=self.j
        result=j.import_profile(self.pid,{})
        self.assertEqual(result['jobs_queued'],1)
        self.assertEqual(j.run_one()['state'],'completed')
        old=j.list_evidence(self.pid)['items'][0]
        self.assertEqual(old['evidence_kind'],'user_claim')
        self.assertEqual(old['exact_quote'],'Alpha')
        self.assertTrue(j.verify(self.pid,old['evidence_id'])['valid'])
        with psycopg.connect(DSN) as conn:
            conn.execute('''INSERT INTO project_profile_versions(project_id,revision,schema_version,profile_hash,profile)
                 VALUES(%s,2,1,%s,%s)''',(self.pid,'b'*64,Jsonb({'product':{'name':'Beta'}})))
            conn.execute('''UPDATE project_registry SET profile_revision=2,profile_hash=%s,
                 updated_at=clock_timestamp() WHERE id=%s''',('b'*64,self.pid))
        res=j.import_profile(self.pid,{})
        self.assertEqual(res['new_sources'],0);self.assertEqual(res['jobs_queued'],1)
        self.assertEqual(j.run_one()['state'],'completed')
        all_items=j.list_evidence(self.pid)['items']
        self.assertEqual(len(all_items),2)
        self.assertTrue(any(x['relationship_flags']==1 for x in all_items))
        self.assertFalse(j.verify(self.pid,old['evidence_id'])['is_current_version'])
        with self.assertRaises(j.EvidenceProblem):j.review(self.pid,old['evidence_id'],{'decision':'approved'})
        self.assertEqual(j.import_profile(self.pid,{'dry_run':True})['fields'],1)

    def test_scopes_wrong_project_stale_output_and_malformed_hash(self):
        import psycopg
        from psycopg.types.json import Jsonb
        j=self.j
        sid,vid=self.source()
        oid=self.put(sid,vid,self.doc('Version one is exact.'))
        with self.assertRaises(j.EvidenceProblem):j.enqueue(self.other,sid,{'output_id':str(oid)})
        with psycopg.connect(DSN) as conn:
            ver2=conn.execute('''INSERT INTO project_source_versions(project_id,source_id,version_no,source_locator)
                 VALUES(%s,%s,2,%s) RETURNING id''',(self.pid,sid,Jsonb({'kind':'test2'}))).fetchone()[0]
            conn.execute('UPDATE project_sources SET active_version_id=%s WHERE project_id=%s AND id=%s',(ver2,self.pid,sid))
        with self.assertRaises(j.EvidenceProblem):j.enqueue(self.pid,sid,{'output_id':str(oid)})
        run=j.enqueue(self.pid,sid,{'output_id':str(oid),'allow_historical':True})
        self.assertEqual(j.run_one()['state'],'completed')
        item=j.list_evidence(self.pid,sid=sid)['items'][0]
        self.assertFalse(item['is_current_version'])
        self.assertTrue(j.verify(self.pid,item['evidence_id'])['valid'])
        with self.assertRaises(j.EvidenceProblem):j.verify(self.other,item['evidence_id'])

if __name__=='__main__':unittest.main()
