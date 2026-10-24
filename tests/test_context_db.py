"""Stage11 true PostgreSQL 17 integration. No external APIs or video processing."""
import hashlib,os,unittest
from uuid import uuid4
from unittest.mock import patch
DSN=os.environ.get('CM_TEST_PG_DSN')

@unittest.skipUnless(DSN,'Requires PostgreSQL17 CI service')
class ContextDB(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.types.json import Jsonb
        from intelligence.project_sources import context_service as svc
        from intelligence.project_sources.context_builder import sha
        self.s=svc;self.sha=sha;self.pid=uuid4().hex;self.other=uuid4().hex
        self.sid=str(uuid4());self.key='a'*64;self.eid=str(uuid4());self.quote='A measurable product capability.'
        self.loc={'kind':'text','start_char':0,'end_char':len(self.quote)}
        self.data={'schema_version':'document-segments/v1','parser_version':'document-parser/1','status':'completed',
                   'segments':[{'text':self.quote,'locator':self.loc,'role':'text'}],'warnings':[]}
        self._patches=[patch.object(svc,'_pid',side_effect=lambda pid: {} if pid==self.pid else (_ for _ in ()).throw(svc.ContextProblem('missing','project_missing',404))),
                       patch.object(svc,'_conn',side_effect=lambda:psycopg.connect(DSN))]
        for p in self._patches:p.start();self.addCleanup(p.stop)
        with psycopg.connect(DSN) as c:
            for pid in (self.pid,self.other):
                c.execute("INSERT INTO project_registry(id,name,profile_revision,profile_hash,created_at,updated_at) VALUES(%s,'context stage11',1,%s,now(),now())",(pid,self.key))
            c.execute('''INSERT INTO project_profile_versions(project_id,revision,schema_version,profile_hash,profile)
                VALUES(%s,1,1,%s,%s)''',(self.pid,self.key,Jsonb({'niche':'wellness','goal':'followers',
                                                      'api_key':'SECRET_NEVER_EXPORT'})))
            self._put_source(c,self.sid,with_evidence=True)

    def _put_source(self,c,sid,with_evidence=False):
        from psycopg.types.json import Jsonb
        c.execute('''INSERT INTO project_sources(id,project_id,source_type,original_input,identity_key,label)
            VALUES(%s,%s,'research_document','doc','stage11:'+%s,'A research document')''',(sid,self.pid,sid))
        vid=c.execute('''INSERT INTO project_source_versions(project_id,source_id,version_no,content_sha256,byte_size,
            media_type,source_locator,initial_extraction_status)
            VALUES(%s,%s,1,%s,%s,'text/plain',%s,'completed') RETURNING id''',
            (self.pid,sid,'e'*64,17,Jsonb({'kind':'text'}))).fetchone()[0]
        c.execute('UPDATE project_sources SET active_version_id=%s WHERE project_id=%s AND id=%s',(vid,self.pid,sid))
        if not with_evidence:return vid
        digest=self.sha(self.data)
        oid=c.execute('''INSERT INTO source_processing_outputs(project_id,source_id,source_version_id,output_type,
             schema_version,output,output_sha256) VALUES(%s,%s,%s,'document_segments','document-segments/v1',%s,%s)
             RETURNING id''',(self.pid,sid,vid,Jsonb(self.data),digest)).fetchone()[0]
        grp=c.execute('''INSERT INTO project_evidence_exact_groups(project_id,evidence_kind,knowledge_scope,normalized_quote_sha256)
            VALUES(%s,'external_claim','general_research',%s) RETURNING id''',(self.pid,hashlib.sha256(self.quote.lower().encode()).hexdigest())).fetchone()[0]
        c.execute('''INSERT INTO project_evidence(id,project_id,source_id,source_version_id,processing_output_id,evidence_kind,
            exact_text_or_observation,locator,extraction_engine,extraction_version,quality_status,source_quote)
            VALUES(%s,%s,%s,%s,%s,'external_claim',%s,%s,'local-document','1','complete',%s)''',
            (self.eid,self.pid,sid,vid,oid,self.quote,Jsonb(self.loc),self.quote))
        c.execute('''INSERT INTO project_evidence_grounding(project_id,evidence_id,source_id,source_version_id,input_output_id,
            quote_sha256,normalized_quote_sha256,locator_sha256,input_sha256,exact_group_id,knowledge_scope,
            trust_label,citation)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'general_research','extracted_document_unverified',%s)''',
            (self.pid,self.eid,sid,vid,oid,hashlib.sha256(self.quote.encode()).hexdigest(),
             hashlib.sha256(self.quote.lower().encode()).hexdigest(),self.sha(self.loc),digest,grp,
             'doc:'+sid+':page:1:char0-30:verified-stored-output'))
        return vid

    def test_freeze_replay_idempotence_approval_and_selection(self):
        s=self.s
        draft=s.preview(self.pid)
        self.assertEqual(draft['context']['coverage']['sources_incomplete'],0)
        self.assertNotIn('SECRET_NEVER_EXPORT',str(draft))
        created=s.create_snapshot(self.pid,{'expected_context_sha256':draft['context_sha256']})
        ctx=created['context_id'];self.assertTrue(created['created']);self.assertEqual(created['context_version'],1)
        duplicate=s.create_snapshot(self.pid,{'expected_context_sha256':draft['context_sha256']})
        self.assertFalse(duplicate['created']);self.assertEqual(ctx,duplicate['context_id'])
        snapshot=s.get_snapshot(self.pid,ctx)
        self.assertEqual(snapshot['context_sha256'],draft['context_sha256'])
        self.assertEqual(snapshot['context']['analysis_refs'][0]['output_sha256'],self.sha(self.data))
        self.assertIn(self.eid,snapshot['context']['evidence_ids'])
        select=s.selection(self.pid,ctx,token_budget=300)
        self.assertEqual(select['selected_count'],1);self.assertIn('measurable',select['selected'][0]['text'])
        result=s.decide(self.pid,ctx,{'decision':'approved','expected_context_sha256':snapshot['context_sha256'],
                                     'excluded_source_ids':[],'acknowledge_partial':False})
        self.assertEqual(result['decision'],'approved')
        self.assertEqual(s.approved(self.pid)['context_id'],ctx)
        with self.assertRaises(s.ContextProblem):s.decide(self.pid,ctx,{'decision':'rejected',
            'expected_context_sha256':snapshot['context_sha256'],'excluded_source_ids':[],
            'acknowledge_partial':False})

    def test_partial_requires_full_explicit_source_ids(self):
        import psycopg
        with psycopg.connect(DSN) as conn:self._put_source(conn,str(uuid4()),False)
        draft=self.s.preview(self.pid)
        missing=draft['context']['unavailable_or_excluded_sources']
        self.assertEqual(len(missing),1)
        created=self.s.create_snapshot(self.pid,{'expected_context_sha256':draft['context_sha256']});ctx=created['context_id']
        request={'decision':'approved','expected_context_sha256':created['context_sha256'],
                  'acknowledge_partial':False,'excluded_source_ids':[missing[0]['source_id']]}
        with self.assertRaises(self.s.ContextProblem):self.s.decide(self.pid,ctx,request)
        request.update(decision='approved_partial',acknowledge_partial=True,excluded_source_ids=[])
        with self.assertRaises(self.s.ContextProblem):self.s.decide(self.pid,ctx,request)
        request['excluded_source_ids']=[missing[0]['source_id']]
        self.assertEqual(self.s.decide(self.pid,ctx,request)['decision'],'approved_partial')
        self.assertEqual(self.s.get_snapshot(self.pid,ctx)['approval']['decision'],'approved_partial')

    def test_old_snapshot_after_profile_update_and_stale_candidate(self):
        import psycopg
        from psycopg.types.json import Jsonb
        s=self.s;first=s.preview(self.pid);ctx=s.create_snapshot(self.pid,{'expected_context_sha256':first['context_sha256']})['context_id']
        with psycopg.connect(DSN) as c:
            c.execute('''INSERT INTO project_profile_versions(project_id,revision,schema_version,profile_hash,profile)
               VALUES(%s,2,1,%s,%s)''',(self.pid,'b'*64,Jsonb({'goal':'conversions'})))
            c.execute('''UPDATE project_registry SET profile_revision=2,profile_hash=%s,updated_at=clock_timestamp()
               WHERE id=%s''',('b'*64,self.pid))
        self.assertEqual(s.get_snapshot(self.pid,ctx)['context_sha256'],first['context_sha256'])
        with self.assertRaises(s.ContextProblem) as e:s.create_snapshot(self.pid,{'expected_context_sha256':first['context_sha256']})
        self.assertEqual(e.exception.code,'stale_preview')
        with self.assertRaises(s.ContextProblem) as e:s.decide(self.pid,ctx,{'decision':'approved',
              'expected_context_sha256':first['context_sha256'],'acknowledge_partial':False,'excluded_source_ids':[]})
        self.assertEqual(e.exception.code,'stale_snapshot')
        current=s.preview(self.pid);other=s.create_snapshot(self.pid,{'expected_context_sha256':current['context_sha256']})
        self.assertEqual(other['context_version'],2)
        self.assertTrue(s.compare(self.pid,ctx,other['context_id'])['profile_changed'])

    def test_cross_project_and_invalid_input_hash(self):
        with self.assertRaises(self.s.ContextProblem):self.s.preview(self.other)
        with self.assertRaises(self.s.ContextProblem):self.s.get_snapshot(self.pid,str(uuid4()))
        with self.assertRaises(self.s.ContextProblem):self.s.create_snapshot(self.pid,{'expected_context_sha256':'0'*64})
        with self.assertRaises(self.s.ContextProblem):self.s.decide(self.pid,str(uuid4()),{'decision':'approved',
            'expected_context_sha256':'a'*64,'acknowledge_partial':False,'excluded_source_ids':[]})

if __name__=='__main__':unittest.main()
