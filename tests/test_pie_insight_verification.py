"""PIE-MOD-31: numerical source oracles, contradiction and release denial QA.

All positive live/source rights are MOCKED. No real source, model or reviewer.
Opt-in PG17 requires an explicitly disposable CM_TEST_PG_DSN.
"""
from __future__ import annotations

from dataclasses import replace
from hashlib import sha256
from pathlib import Path
import json
import os
import unittest
from unittest.mock import patch

from intelligence.patterns_insights.contracts import PIEBoundaryError,canonical_hash
from intelligence.patterns_insights.research.insight_generation import (
    build_candidate_insight_generation,
)
from intelligence.patterns_insights.research.insight_verification import (
    SCHEMA,_audit,_source_truth,_frozen_generation,
    verify_candidate_insight,
)
from intelligence.patterns_insights.research.pattern_queries import RegistryQuery
from test_pie_insight_semantics import prepare as prepare28
from test_pie_insight_generation import PROJECT,SERIES,HYP

PRECISE_N=4


def registry_source(prompt,*,independent_metadata=True):
    facts=prompt.source_facts
    r=dict(
        schema_version='pie-immutable-pattern-registry-entry-candidate/v1',
        project_id=prompt.project_id,series_id=SERIES,hypothesis_sha256=HYP,
        owner_id='fixture-owner',factor_id=facts['factor_id'],
        analysis_revision_status=facts['status'],sample_scope='discovery_only',
        hypothesis_specification={
            'feature_family':facts['feature_family'],
            'feature_name':facts['feature_name'],'metric_kind':facts['metric_kind'],
            'source_unit':facts['raw_source_unit'],
        },
        matched_pair_count=PRECISE_N,
        source_discovery_pairs=[{'bounded_pair_no':i} for i in range(PRECISE_N)],
        statistical_summary={
            'raw_mean_high_minus_low':{'numerator':5,'denominator':2},
            'bin_coverage':[{'supported':True},{'supported':True}],
            'monotonicity':facts['descriptive_direction'],
            'calibrated_p_value':None,'adjusted_q_value':None,
            'confidence_interval':None,
            'stage23_robustness_status':'candidate_descriptive_only' if independent_metadata else None,
            'missing_observations':[],
        },
        source_quality_dimensions={'confounding':'unverified'} if independent_metadata else {},
        approved_live=False,may_publish=False,independent_holdout_confirmed=False,
        rights_and_review_authenticated=False,
    )
    return r


class StubRow:
    def __init__(self,v):self.v=v
    def fetchone(self):return self.v


class FakeDB:
    def __init__(self,prompt):
        self.prompt=prompt
        self.source=registry_source(prompt)
        self.artifact=build_candidate_insight_generation(
            prompt,json.dumps(prompt.expected_codes,sort_keys=True,
                              separators=(',',':')))
        self.calls=[]
        self.audits={}
        self.fail=False
        self.hidden=False
    class Tx:
        def __enter__(self):return None
        def __exit__(self,*exc):return False
    def transaction(self):return self.Tx()
    def execute(self,sql,args=()):
        self.calls.append((sql,args))
        if self.fail:raise RuntimeError('sql disconnected')
        if 'ORDER BY r.revision DESC LIMIT 1' in sql:
            return StubRow((2,HYP,canonical_hash(self.source),self.source))
        if 'FROM pie_candidate_insight_generations' in sql:
            if self.hidden:return StubRow(None)
            d=self.artifact.as_dict()
            return StubRow((d,self.artifact.artifact_sha256,2,HYP,
                            self.prompt.prompt_sha256))
        if 'FROM pie_model_dispatch_outcomes o' in sql:
            return StubRow(None)
        if 'INSERT INTO pie_candidate_insight_verification_events' in sql:
            o=args[-1].obj
            digest=args[1]
            if digest in self.audits:return StubRow(None)
            self.audits[digest]=o
            return StubRow((digest,))
        if 'FROM pie_candidate_insight_verification_events' in sql:
            d=self.audits.get(args[1])
            return StubRow((d,d['verdict'],d['reason_code'],
                            d['source_registry_payload_sha256']) if d is not None else None)
        return StubRow(None)


class VerificationEngineUnitTests(unittest.TestCase):
    def setUp(self):
        self.prompt,_=prepare28()
        self.query=RegistryQuery(PROJECT,'fixture-owner')
        self.db=FakeDB(self.prompt)

    def reject(self,code,fn,*a,**kw):
        with self.assertRaises(PIEBoundaryError) as raised:fn(*a,**kw)
        self.assertEqual(raised.exception.code,code)

    def run_audit(self,db=None,prompt=None):
        db=self.db if db is None else db
        prompt=self.prompt if prompt is None else prompt
        with patch('intelligence.patterns_insights.research.insight_verification.prepare_candidate_insight_prompt',
                   return_value=prompt),patch(
             'intelligence.patterns_insights.research.insight_verification.require_noninvalidated_pattern_for_insight'):
            return verify_candidate_insight(
                db,self.query,SERIES,db.artifact.artifact_sha256,
                None,None,None,None)

    def test_positive_candidate_remains_unapproved_and_nonpublic(self):
        report,receipt=self.run_audit()
        self.assertEqual(report.verdict,'candidate_checked_for_review')
        self.assertEqual(report.reason_code,'exact_source_consistent')
        self.assertEqual(report.as_dict()['schema_version'],SCHEMA)
        self.assertEqual(report.as_dict()['counterevidence_status'],'independent_not_measured')
        self.assertFalse(report.may_public_read)
        self.assertFalse(report.may_publish)
        self.assertFalse(receipt.may_public_read)
        self.assertFalse(receipt.independent_counterevidence_confirmed)

    def test_independent_four_pair_expected_mean_and_original_unit(self):
        # Hand calculated: (2-1)+(6-3)+(8-4)+(9-7) = 10 ; 10/4=5/2 ms.
        self.assertTrue(_source_truth(
            self.db.source,self.prompt,canonical_hash(self.db.source),HYP)[0])
        self.assertEqual(self.prompt.source_facts['exact_raw_mean_high_minus_low'],'5/2')
        self.assertEqual(self.prompt.source_facts['raw_source_unit'],'ms')
        self.assertEqual(self.prompt.source_facts['discovery_matched_pairs'],4)

    def test_same_evidence_replay_is_idempotent(self):
        first,a=self.run_audit()
        second,b=self.run_audit()
        self.assertEqual(first,second)
        self.assertFalse(a.idempotent)
        self.assertTrue(b.idempotent)
        self.assertEqual(len(self.db.audits),1)

    def test_tampered_effect_or_unit_quarantined(self):
        for metric in ({'numerator':6,'denominator':2},
                       {'numerator':5,'denominator':3}):
            self.db.source=registry_source(self.prompt)
            self.db.source['statistical_summary']['raw_mean_high_minus_low']=metric
            audit,_=self.run_audit()
            self.assertEqual(audit.reason_code,'numeric_or_scope_contradiction')
            self.assertEqual(audit.verdict,'quarantined')
            self.assertFalse(audit.may_public_read)
        self.db.source=registry_source(self.prompt)
        self.db.source['hypothesis_specification']['source_unit']='seconds'
        audit,_=self.run_audit()
        self.assertEqual(audit.reason_code,'numeric_or_scope_contradiction')

    def test_forged_sample_count_and_bin_support_quarantined(self):
        for mutate in (
            lambda s:s.update(matched_pair_count=400),
            lambda s:s.update(source_discovery_pairs=[]),
            lambda s:s['statistical_summary'].update(bin_coverage=[{'supported':False}]),
        ):
            self.db.source=registry_source(self.prompt)
            mutate(self.db.source)
            audit,_=self.run_audit()
            self.assertEqual(audit.reason_code,'numeric_or_scope_contradiction')

    def test_false_inferential_p_value_or_confirmation_quarantined(self):
        for field,val in (('calibrated_p_value','0.001'),
                          ('adjusted_q_value','0.002'),
                          ('confidence_interval',{'low':1,'high':3})):
            self.db.source=registry_source(self.prompt)
            self.db.source['statistical_summary'][field]=val
            a,_=self.run_audit()
            self.assertEqual(a.verdict,'quarantined')
        self.db.source=registry_source(self.prompt)
        self.db.source['independent_holdout_confirmed']=True
        self.assertEqual(self.run_audit()[0].verdict,'quarantined')

    def test_missing_counterevidence_metadata_requires_quarantine(self):
        self.db.source=registry_source(self.prompt,independent_metadata=False)
        a,_=self.run_audit()
        self.assertEqual(a.reason_code,'missing_counterevidence_audit')
        self.assertEqual(a.counterevidence_status,'unknown_quarantined')

    def test_evaluated_robustness_does_not_claim_independent_counterfactual(self):
        a,_=self.run_audit()
        self.assertEqual(a.counterevidence_status,'independent_not_measured')
        self.assertFalse(a.as_dict()['independent_counterevidence_confirmed'])
        self.assertFalse(a.as_dict()['human_review_confirmed'])

    def test_rehashed_fabricated_summary_is_quarantined_not_rendered(self):
        original=self.db.artifact.as_dict()
        original['draft']['summary']='This feature causes 98% guaranteed growth.'
        forged_json=json.dumps(original,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        self.db.artifact=replace(self.db.artifact,
                artifact_sha256=sha256(forged_json.encode()).hexdigest(),
                _json=forged_json)
        a,_=self.run_audit()
        self.assertEqual(a.reason_code,'fabricated_or_modified_draft')
        self.assertNotIn('98%',a.to_json())
        self.assertFalse(a.may_public_read)

    def test_wrong_source_hypothesis_or_latest_revision_blocks(self):
        self.db.source['hypothesis_sha256']='c'*64
        a,_=self.run_audit()
        self.assertEqual(a.reason_code,'numeric_or_scope_contradiction')
        self.db.source=registry_source(self.prompt)
        self.db.artifact=replace(self.db.artifact,source_revision=1)
        # Generation DB row uses fixed stage30 revision 2, so input forged
        # dataclass metadata by itself is not a trusted DB source.
        self.assertEqual(self.run_audit()[0].verdict,'candidate_checked_for_review')

    def test_stale_registry_revision_is_refused_before_audit(self):
        db=self.db
        orig=db.execute
        def newer(sql,args=()):
            if 'ORDER BY r.revision DESC LIMIT 1' in sql:
                return StubRow((3,HYP,canonical_hash(db.source),db.source))
            return orig(sql,args)
        with patch.object(db,'execute',side_effect=newer),patch(
             'intelligence.patterns_insights.research.insight_verification.prepare_candidate_insight_prompt',
             return_value=self.prompt):
            self.reject('insight_verification_source_changed',verify_candidate_insight,
                        db,self.query,SERIES,db.artifact.artifact_sha256,
                        None,None,None,None)
        self.assertEqual(len(db.audits),0)

    def test_missing_stored_artifact_is_denied(self):
        self.db.hidden=True
        self.reject('insight_verification_not_found',self.run_audit)
        self.assertFalse(self.db.audits)

    def test_rights_denied_never_reads_generation_payload(self):
        with patch('intelligence.patterns_insights.research.insight_verification.prepare_candidate_insight_prompt',
                   side_effect=PIEBoundaryError('insight_rights_not_verified','current revoked')):
            self.reject('insight_rights_not_verified',verify_candidate_insight,
                        self.db,self.query,SERIES,self.db.artifact.artifact_sha256,
                        None,None,None,None)
        self.assertEqual(len(self.db.audits),0)
        self.assertFalse(any('FROM pie_candidate_insight_generations' in s
                             for s,_ in self.db.calls))

    def test_revoked_source_refused_without_audit(self):
        with patch('intelligence.patterns_insights.research.insight_verification.prepare_candidate_insight_prompt',
                   return_value=self.prompt),patch(
             'intelligence.patterns_insights.research.insight_verification.require_noninvalidated_pattern_for_insight',
             side_effect=PIEBoundaryError('pattern_evidence_invalidated','revoked')):
            self.reject('pattern_evidence_invalidated',verify_candidate_insight,
                        self.db,self.query,SERIES,self.db.artifact.artifact_sha256,
                        None,None,None,None)
        self.assertEqual(len(self.db.audits),0)

    def test_cross_project_request_cannot_find_generation(self):
        self.reject('insight_verification_invalid',verify_candidate_insight,
                    self.db,'unqualified',SERIES,self.db.artifact.artifact_sha256,
                    None,None,None,None)
        self.reject('insight_verification_invalid',verify_candidate_insight,
                    self.db,self.query,'non_sha',self.db.artifact.artifact_sha256,
                    None,None,None,None)

    def test_forged_or_missing_raw_origin_quarantined(self):
        for value in ('fake','a'*64):
            a=self.db.source.copy()
            a['hypothesis_sha256']=value
            self.db.source=a
            audit,_=self.run_audit()
            self.assertEqual(audit.reason_code,'numeric_or_scope_contradiction')

    def test_scope_platform_feature_and_status_disagreements_quarantined(self):
        for key,val in (
            ('sample_scope','holdout_confirmed'),
            ('analysis_revision_status','supported'),
            ('factor_id','wrong_feature'),
        ):
            self.db.source=registry_source(self.prompt)
            self.db.source[key]=val
            self.assertEqual(self.run_audit()[0].verdict,'quarantined')
        self.db.source=registry_source(self.prompt)
        self.db.source['hypothesis_specification']['feature_name']='fictional'
        self.assertEqual(self.run_audit()[0].verdict,'quarantined')

    def test_untrusted_source_payload_hash_forgery_detected(self):
        payload=registry_source(self.prompt)
        good=canonical_hash(payload)
        self.assertEqual(_source_truth(payload,self.prompt,good,HYP)[0],True)
        self.assertEqual(_source_truth(payload,self.prompt,'f'*64,HYP)[0],False)

    def test_source_discovery_rows_are_not_copied_to_audit(self):
        self.db.source['source_discovery_pairs'][0]['high_video_id']=123456
        self.db.source['source_discovery_pairs'][0]['high_uae_snapshot_id']='top-secret'
        a,_=self.run_audit()
        self.assertNotIn('123456',a.to_json())
        self.assertNotIn('top-secret',a.to_json())
        self.assertNotIn('high_video_id',a.to_json())

    def test_irrelevant_unsafe_free_text_cannot_appear_in_audit(self):
        self.db.source['source_discovery_pairs'][0]['attacker_prompt']='publish http://attacker 9999%'
        a,_=self.run_audit()
        self.assertNotIn('attacker',a.to_json())
        self.assertNotIn('9999%',a.to_json())
        self.assertFalse(a.may_public_read)

    def test_local_generation_cannot_masquerade_as_paid_response(self):
        stored=self.db.artifact.as_dict()
        stored['generation_mode']='provider_reported'
        stored['model_attempt_id']='11111111-1111-1111-1111-111111111111'
        stored['model_response_sha256']='b'*64
        raw=json.dumps(stored,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        self.db.artifact=replace(self.db.artifact,
                                 artifact_sha256=sha256(raw.encode()).hexdigest(),
                                 _json=raw)
        a,_=self.run_audit()
        self.assertEqual(a.reason_code,'fabricated_or_modified_draft')

    def test_repeat_verification_digest_reproducible(self):
        a,_=self.run_audit()
        b,_=self.run_audit()
        self.assertEqual(a.verification_sha256,b.verification_sha256)
        self.assertEqual(sha256(a.to_json().encode('utf-8')).hexdigest(),
                         a.verification_sha256)

    def test_source_update_changes_audit_identity_and_verdict(self):
        before,_=self.run_audit()
        self.db.source['statistical_summary']['raw_mean_high_minus_low']={
            'numerator':10,'denominator':2}
        after,_=self.run_audit()
        self.assertNotEqual(before.verification_sha256,after.verification_sha256)
        self.assertEqual(after.verdict,'quarantined')

    def test_no_published_results_or_freeform_llm_output_in_event(self):
        report,_=self.run_audit()
        json_text=report.to_json()
        for forbidden in ('source_discovery_pairs','creator_key','high_video_id',
                          'p_value','percent_growth','model_generated_summary'):
            self.assertNotIn(forbidden,json_text)
        self.assertEqual(report.as_dict()['human_review_confirmed'],False)
        self.assertEqual(report.as_dict()['may_public_read'],False)

    def test_db_outage_is_fail_closed_and_no_qa_pass(self):
        self.db.fail=True
        self.reject('insight_verification_unavailable',self.run_audit)

    def test_static_sql_requires_append_only_nopublish_and_scope(self):
        sql=(Path(__file__).resolve().parents[1]/
             'intelligence/migrations/205_pie_insight_verification_events.sql').read_text('utf-8')
        for term in (
            'CREATE TABLE pie_candidate_insight_verification_events',
            'FOREIGN KEY (project_id,generation_sha256)',
            'source_registry_payload_sha256',
            'pie_candidate_insight_verify_immutable',
            'pie_candidate_insight_verify_guard_insert',
            "NEW.source_prompt_sha256",
            "audit->'may_public_read'",
        ):
            self.assertIn(term,sql)
        self.assertEqual(sql.count('$pie31$'),2)
        self.assertNotIn('ALTER TABLE ',sql)

    def test_direct_status_syntax_does_not_allow_supported(self):
        sql=(Path(__file__).resolve().parents[1]/
             'intelligence/migrations/205_pie_insight_verification_events.sql').read_text('utf-8')
        self.assertIn("CHECK (verdict IN ('candidate_checked_for_review','quarantined'))",sql)
        self.assertIn("counterevidence_status='independent_not_measured'",sql)


@unittest.skipUnless(os.getenv('CM_TEST_PG_DSN'),'Explicit disposable PG17 DSN missing')
class InsightVerificationPostgres17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.db=psycopg.connect(os.environ['CM_TEST_PG_DSN'],autocommit=True)
        name=cls.db.execute('SELECT current_database()').fetchone()[0]
        if 'test' not in name or name in ('postgres','template1','contentmachine'):
            cls.db.close()
            raise AssertionError('Refuse non-test database')
        migrate.apply_migrations(cls.db)
    @classmethod
    def tearDownClass(cls):
        if not cls.db.closed:cls.db.close()

    def test_migration_fresh_upgrade_tables_and_immutable_trigger(self):
        from intelligence import migrate
        self.assertEqual(migrate.apply_migrations(self.db),[])
        self.assertEqual(
            self.db.execute("SELECT to_regclass('pie_candidate_insight_verification_events')").fetchone()[0],
            'pie_candidate_insight_verification_events')
        names={v[0] for v in self.db.execute(
            """SELECT tgname FROM pg_trigger
                WHERE tgrelid='pie_candidate_insight_verification_events'::regclass
                  AND NOT tgisinternal""").fetchall()}
        self.assertIn('pie_candidate_insight_verify_immutable',names)
        self.assertIn('pie_candidate_insight_verify_guard_insert',names)

    def test_foreign_generation_cannot_be_forged_with_direct_sql(self):
        from psycopg.types.json import Jsonb
        from uuid import uuid4
        project=uuid4().hex
        self.db.execute(
            """INSERT INTO project_registry
               (id,name,profile_revision,profile_hash,created_at,updated_at)
               VALUES(%s,'PIE stage31 isolated',1,%s,now(),now())""",
            (project,'a'*64))
        try:
            with self.assertRaises(Exception):
                self.db.execute(
                    """INSERT INTO pie_candidate_insight_verification_events
                       (project_id,verification_sha256,generation_sha256,
                        source_series_id,source_revision,source_hypothesis_sha256,
                        source_registry_payload_sha256,source_prompt_sha256,
                        verdict,reason_code,counterevidence_status,audit)
                       VALUES(%s,%s,%s,%s,1,%s,%s,%s,'candidate_checked_for_review',
                              'exact_source_consistent','independent_not_measured',%s)""",
                    (project,'a'*64,'b'*64,'c'*64,'d'*64,'e'*64,'f'*64,
                     Jsonb({'may_public_read':False})))
        finally:
            self.db.execute('DELETE FROM project_registry WHERE id=%s',(project,))

if __name__=='__main__':
    unittest.main()
