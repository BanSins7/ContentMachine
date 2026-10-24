import hashlib
import unittest
from uuid import uuid4
from intelligence.project_sources import context_builder as b

class ContextBuilder(unittest.TestCase):
    def setUp(self):
        self.pid=uuid4().hex;self.sid=str(uuid4());self.vid=str(uuid4());self.oid=str(uuid4());self.eid=str(uuid4())
        self.doc={'schema_version':'document-segments/v1','parser_version':'local/1','status':'completed','segments':[]}
        self.sources=[{'id':self.sid,'version_id':self.vid,'source_type':'research_document',
            'label':'research','canonical_url':None,'status':'completed','initial_status':'completed',
            'content_sha256':'1'*64,'byte_size':123,'version_no':1,'media_type':'application/pdf',
            'locator':{'kind':'pdf'},'recorded_at':None,'tombstoned':False}]
        self.outputs=[{'id':self.oid,'source_id':self.sid,'version_id':self.vid,'output_type':'document_segments',
             'schema_version':self.doc['schema_version'],'output_sha256':b.sha(self.doc),
             'output':self.doc,'created_at':None}]
        quote='A measured independent sentence.'
        self.evidence=[{'id':self.eid,'source_id':self.sid,'version_id':self.vid,
          'output_id':self.oid,'quote':quote,'kind':'external_claim','quality':'complete',
          'review_status':'pending','trust':'document_extracted_unverified','scope':'general_research',
          'locator':{'kind':'pdf','page':2,'start_char':0,'end_char':len(quote)},
          'citation':'source:'+self.sid+':page:2','review_decision':None,'created_at':None}]

    def data(self):
        return b.build(project_id=self.pid,profile_revision=1,profile_hash='a'*64,
            profile_data={'niche':'Technology','goal':'Reach','api_key':'SECRET','product_url':'https://example.org/?key=private&q=public'},
            source_rows=self.sources,output_rows=self.outputs,evidence_rows=self.evidence)

    def test_deterministic_full_provenance_and_budget(self):
        one=self.data();two=self.data()
        self.assertEqual(b.sha(one),b.sha(two))
        self.assertEqual(one['coverage']['evidence_total'],1)
        self.assertEqual(one['analysis_refs'][0]['output_sha256'],b.sha(self.doc))
        self.assertEqual(one['source_manifest'][0]['sha256_kind'],'source_byte_sha256')
        self.assertEqual(one['profile']['fields']['product_url'],'https://example.org/?q=public')
        self.assertNotIn('api_key',str(one));self.assertNotIn('SECRET',str(one))
        selection=b.choose_evidence(one,{self.eid:self.evidence[0]['quote']},token_budget=200)
        self.assertEqual(selection['selected_count'],1)
        self.assertEqual(selection['context_hash'],b.sha(one))
        self.assertEqual(one['evidence_ids'],[self.eid])

    def test_missing_source_and_partial_explicit(self):
        self.evidence=[]
        out=self.data()
        self.assertEqual(out['coverage']['sources_incomplete'],1)
        self.assertEqual(out['unavailable_or_excluded_sources'][0]['reason'],'no_grounded_evidence')
        self.assertIn('source_coverage_incomplete',out['warnings'])

    def test_stale_evidence_cross_source_and_output_hash(self):
        self.outputs[0]['output_sha256']='0'*64
        with self.assertRaises(b.ContextProblem) as ex:self.data()
        self.assertEqual(ex.exception.code,'output_integrity')
        self.outputs[0]['output_sha256']=b.sha(self.doc)
        self.evidence[0]['version_id']=str(uuid4())
        with self.assertRaises(b.ContextProblem) as ex:self.data()
        self.assertEqual(ex.exception.code,'evidence_pin_mismatch')

    def test_old_model_output_provenance_and_review_not_fact(self):
        self.sources[0]['locator']={'kind':'web','retrieval':'model_reported_web_fetch','raw_response_captured':False}
        self.evidence[0]['trust']='model_reported_unverified';self.evidence[0]['review_decision']='approved'
        data=self.data()
        self.assertEqual(data['source_manifest'][0]['sha256_kind'],'model_reported_text_sha256')
        self.assertEqual(data['approved_evidence_ids'],[self.eid])
        self.assertIn('model_reported_unverified',data['warnings'])

    def test_stale_or_sensitive_paths_not_exported(self):
        self.sources[0]['label']='token=SuperSecret /home/alice/data.pdf'
        data=self.data()
        self.assertNotIn('SuperSecret',str(data));self.assertNotIn('/home/alice',str(data))

    def test_profile_revisions_and_new_evidence_change_hash(self):
        old=self.data();self.evidence[0]['review_decision']='approved';new=self.data()
        self.assertNotEqual(b.sha(old),b.sha(new))
        self.assertEqual(b.sha(old),b.sha(old))

    def test_no_implicit_drops_when_limit_exceeded(self):
        with self.assertRaises(b.ContextProblem):b.choose_evidence(self.data(),{},token_budget=99999)
        with self.assertRaises(b.ContextProblem):b.build(project_id=self.pid,profile_revision=1,profile_hash='a'*64,
           profile_data={},source_rows=self.sources*181,output_rows=[],evidence_rows=[])

if __name__=='__main__':unittest.main()
