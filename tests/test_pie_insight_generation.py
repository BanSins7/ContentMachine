"""PIE-MOD-30: synthetic governed Insight Generation Engine QA.

Test-only stage28 rights/provenance is MOCKED. No LLM, network or paid provider.
Opt-in database cases require separately disposable PostgreSQL17 CM_TEST_PG_DSN.
"""
from __future__ import annotations

from datetime import datetime,timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from dataclasses import replace
from unittest.mock import patch
import json
import os
import unittest

from intelligence.patterns_insights.contracts import PIEBoundaryError,canonical_hash
from intelligence.patterns_insights.research.insight_generation import (
    SCHEMA,CandidateInsightGeneration,InsightGenerationReceipt,
    build_candidate_insight_generation,replay_candidate_insight_generation,
    generate_candidate_insight,_verified_paid_provenance,
)
from intelligence.patterns_insights.research.model_governance import (
    ModelDispatchReceipt,verify_model_catalog,model_quote,
)
from intelligence.patterns_insights.research.pattern_queries import RegistryQuery
from test_pie_insight_semantics import prepare as stage28_prepare,selection
from test_pie_model_governance import catalog as fake_catalog,SLUG

NOW=datetime(2026,10,28,12,tzinfo=timezone.utc)
HYP='b'*64
SERIES='a'*64
UUID='11111111-1111-1111-1111-111111111111'
PROJECT='f'*32


class StubRow:
    def __init__(self,row):self.row=row
    def fetchone(self):return self.row


class StubDB:
    """Parameter-aware in-memory transaction stub: never claims real PG17 QA."""
    def __init__(self,prompt):
        self.prompt=prompt
        self.calls=[]
        self.revision=(prompt.source_revision,HYP)
        self.artifact=None
        self.receipt=None
        self.fail_store=False
        self.preclaim=True
    class TX:
        def __enter__(self):return None
        def __exit__(self,*args):return False
    def transaction(self):return self.TX()
    def execute(self,sql,args=()):
        self.calls.append((sql,args))
        if 'ORDER BY r.revision DESC LIMIT 1' in sql:
            return StubRow(self.revision)
        if 'FROM pie_model_dispatch_outcomes o' in sql:
            return StubRow(self.receipt)
        if 'INSERT INTO pie_candidate_insight_generations' in sql:
            if self.fail_store:raise RuntimeError('db unavailable')
            if self.artifact is not None:return StubRow(None)
            self.artifact=args[-1].obj  # psycopg.Jsonb wrapping an already redacted dict.
            return StubRow((args[1],))
        if 'SELECT artifact,candidate_draft_sha256' in sql:
            if self.artifact is None:return StubRow(None)
            return StubRow((self.artifact,self.artifact['candidate_draft_sha256'],
                            self.artifact['source_revision'],
                            self.artifact['generation_mode']))
        return StubRow(None)


class CandidateInsightGenerationTests(unittest.TestCase):
    def setUp(self):
        self.prompt,_=stage28_prepare()
        self.query=RegistryQuery(PROJECT,'fixture-owner')
        self.db=StubDB(self.prompt)
        self.choice=selection(self.prompt)
        self.snapshot=verify_model_catalog(fake_catalog(),SLUG,now=NOW)
        self.quote=model_quote(self.prompt,self.snapshot,now=NOW)
        self.raw_hash=sha256(self.choice.encode('utf-8')).hexdigest()
        self.receipt=ModelDispatchReceipt(PROJECT,UUID,'reported_charged',
                                          self.raw_hash,'0.000120000000',
                                          'gen-fixture-v1')
        self.db.receipt=('reported_charged',self.raw_hash,'0.000120000000',
                         self.prompt.prompt_sha256,self.quote.quote_sha256,
                         self.quote.request_sha256,self.quote.model_slug,
                         self.quote.reserve_usd)
    def deny(self,code,fn,*args,**kwargs):
        with self.assertRaises(PIEBoundaryError) as err:fn(*args,**kwargs)
        self.assertEqual(err.exception.code,code)

    def create(self,db=None,prompt=None,**extra):
        p=self.prompt if prompt is None else prompt
        with patch('intelligence.patterns_insights.research.insight_generation.prepare_candidate_insight_prompt',
                   return_value=p),patch(
             'intelligence.patterns_insights.research.insight_generation.require_noninvalidated_pattern_for_insight'):
            return generate_candidate_insight(
                db if db is not None else self.db,self.query,SERIES,
                None,None,None,None,now=NOW,**extra)

    def test_local_deterministic_produces_nonpublishable_source_pinned_insight(self):
        artifact,receipt=self.create()
        d=artifact.as_dict()
        self.assertEqual(d['schema_version'],SCHEMA)
        self.assertEqual(d['source_series_id'],SERIES)
        self.assertEqual(d['source_revision'],2)
        self.assertEqual(d['source_hypothesis_sha256'],HYP)
        self.assertEqual(d['evidence_ids'],list(self.prompt.evidence_ids))
        self.assertEqual(d['generation_mode'],'local_deterministic')
        self.assertIsNone(d['model_attempt_id'])
        self.assertIsNone(d['model_response_sha256'])
        self.assertEqual(d['review_state'],'unverified_candidate')
        self.assertFalse(d['approved_live'])
        self.assertFalse(d['can_publish'])
        self.assertFalse(d['causal_claim'])
        self.assertFalse(receipt.publication_allowed)

    def test_report_has_original_numeric_unit_cohort_and_caveats(self):
        d=self.create()[0].as_dict()
        draft=d['draft']
        self.assertIn('5/2 ms',draft['effect'])
        self.assertIn('4 сопоставленных пар',draft['cohort_scope'])
        self.assertIn('статистическая значимость не доказана',
                      draft['statistical_uncertainty'])
        self.assertIn('не причинный вывод',draft['summary'])
        self.assertIsNone(draft['calibrated_p_value'])
        self.assertFalse(draft['guaranteed_uplift'])
        self.assertIn('Предварительно зарегистрировать',draft['actionable_tests'])

    def test_repeat_same_source_is_idempotent_without_paid_call(self):
        first,r1=self.create()
        second,r2=self.create()
        self.assertEqual(first,second)
        self.assertFalse(r1.idempotent)
        self.assertTrue(r2.idempotent)
        self.assertEqual(len([x for x,_ in self.db.calls if 'INSERT INTO pie_candidate_insight_generations' in x]),2)

    def test_exact_hash_and_byte_replay(self):
        obj=self.create()[0]
        recovered=replay_candidate_insight_generation(
            obj.to_json(),obj.artifact_sha256,self.prompt,self.choice)
        self.assertEqual(obj,recovered)
        self.assertEqual(
            replay_candidate_insight_generation(
                obj.to_json().encode(),obj.artifact_sha256,self.prompt,self.choice),obj)

    def test_change_candidate_content_cannot_rehash_forged_replay(self):
        obj=self.create()[0]
        forged=obj.as_dict()
        forged['draft']['summary']='Гарантирован рост 300 процентов'
        body=json.dumps(forged,ensure_ascii=False,sort_keys=True,separators=(',',':'))
        self.deny('insight_generation_replay_mismatch',
                  replay_candidate_insight_generation,body,
                  sha256(body.encode()).hexdigest(),self.prompt,self.choice)

    def test_mismatched_revision_even_valid_prompt_fails_before_write(self):
        self.db.revision=(3,HYP)
        self.deny('insight_generation_stale_source',self.create)
        self.assertIsNone(self.db.artifact)

    def test_mismatched_hypothesis_cannot_be_reinterpreted(self):
        self.db.revision=(2,'c'*64)
        self.deny('insight_generation_stale_source',self.create)
        self.assertIsNone(self.db.artifact)

    def test_missing_pattern_not_stored(self):
        self.db.revision=None
        self.deny('insight_generation_stale_source',self.create)
        self.assertIsNone(self.db.artifact)

    def test_new_rights_rejection_prevents_output_or_store(self):
        with patch('intelligence.patterns_insights.research.insight_generation.prepare_candidate_insight_prompt',
                   side_effect=PIEBoundaryError('insight_evidence_not_visible','revoked')):
            self.deny('insight_evidence_not_visible',generate_candidate_insight,
                      self.db,self.query,SERIES,None,None,None,None,now=NOW)
        self.assertIsNone(self.db.artifact)

    def test_stage27_revocation_prevents_generation(self):
        with patch('intelligence.patterns_insights.research.insight_generation.prepare_candidate_insight_prompt',
                   return_value=self.prompt),patch(
             'intelligence.patterns_insights.research.insight_generation.require_noninvalidated_pattern_for_insight',
             side_effect=PIEBoundaryError('pattern_evidence_invalidated','revoked')):
            self.deny('pattern_evidence_invalidated',generate_candidate_insight,
                      self.db,self.query,SERIES,None,None,None,None,now=NOW)
        self.assertIsNone(self.db.artifact)

    def test_raw_source_media_creator_and_row_ids_not_exported(self):
        raw=self.create()[0].to_json()
        for secret in ('high_video_id','low_video_id','uae_snapshot_id',
                       'source_discovery_pairs','creator_key','source_rows_sha256',
                       'api_key','source_evidence','url'):
            self.assertNotIn(secret,raw)

    def test_payload_has_no_model_charge_or_inference_in_free_path(self):
        d=self.create()[0].as_dict()
        self.assertEqual(d['statistical_confidence'],'uncalibrated_descriptive_candidate')
        self.assertFalse(d['cost_settled_independently'])
        self.assertFalse(d['independent_holdout_confirmed'])
        self.assertFalse(d['rights_for_future_read_guaranteed'])

    def test_provider_mode_requires_all_three_quote_receipt_and_json(self):
        self.deny('insight_generation_request_invalid',self.create,model_quote=self.quote)
        self.deny('insight_generation_request_invalid',self.create,dispatch_receipt=self.receipt)
        self.deny('insight_generation_request_invalid',self.create,
                  model_response_json=self.choice)

    def test_reported_provider_receipt_and_exact_bytes_can_create_candidate(self):
        item,receipt=self.create(model_quote=self.quote,dispatch_receipt=self.receipt,
                                 model_response_json=self.choice)
        self.assertEqual(item.generation_mode,'provider_reported')
        self.assertEqual(item.model_attempt_id,UUID)
        self.assertEqual(item.model_response_sha256,self.raw_hash)
        self.assertEqual(receipt.generation_mode,'provider_reported')
        self.assertFalse(receipt.independently_verified_scientific_claim)
        self.assertEqual(item.as_dict()['draft']['source_prompt_sha256'],
                         self.prompt.prompt_sha256)

    def test_provider_receipt_is_not_independent_settlement(self):
        self.create(model_quote=self.quote,dispatch_receipt=self.receipt,
                    model_response_json=self.choice)
        self.assertFalse(self.db.artifact['cost_settled_independently'])
        self.assertFalse(self.db.artifact['can_publish'])

    def test_provider_receipt_without_db_outcome_refused(self):
        self.db.receipt=None
        self.deny('insight_generation_provider_invalid',self.create,
                  model_quote=self.quote,dispatch_receipt=self.receipt,
                  model_response_json=self.choice)

    def test_uncertain_or_fake_cost_outcome_refused(self):
        row=list(self.db.receipt)
        row[0]='uncertain_charge'
        self.db.receipt=tuple(row)
        self.deny('insight_generation_provider_invalid',self.create,
                  model_quote=self.quote,dispatch_receipt=self.receipt,
                  model_response_json=self.choice)

    def test_wrong_model_response_hash_refused_without_store(self):
        wrong=replace(self.receipt,response_sha256='f'*64)
        self.deny('insight_generation_provider_invalid',self.create,
                  model_quote=self.quote,dispatch_receipt=wrong,
                  model_response_json=self.choice)
        self.assertIsNone(self.db.artifact)

    def test_model_hallucinated_source_link_refused(self):
        altered={**self.prompt.expected_codes,'evidence_ids':['pie:fake','pie:other']}
        raw=json.dumps(altered)
        receipt=replace(self.receipt,response_sha256=sha256(raw.encode()).hexdigest())
        row=list(self.db.receipt);row[1]=receipt.response_sha256
        self.db.receipt=tuple(row)
        self.deny('insight_ungrounded_model_output',self.create,
                  model_quote=self.quote,dispatch_receipt=receipt,
                  model_response_json=raw)

    def test_tampered_model_quote_prompt_fails(self):
        bad=replace(self.quote,prompt_sha256='f'*64)
        self.deny('insight_generation_provider_invalid',self.create,
                  model_quote=bad,dispatch_receipt=self.receipt,
                  model_response_json=self.choice)

    def test_model_attempt_cannot_relabel_cross_project(self):
        altered=replace(self.receipt,project_id='e'*32)
        self.deny('insight_generation_provider_invalid',self.create,
                  model_quote=self.quote,dispatch_receipt=altered,
                  model_response_json=self.choice)

    def test_model_record_cost_above_reservation_rejected(self):
        row=list(self.db.receipt);row[2]='5'
        self.db.receipt=tuple(row)
        self.deny('insight_generation_provider_invalid',self.create,
                  model_quote=self.quote,dispatch_receipt=self.receipt,
                  model_response_json=self.choice)

    def test_database_write_failure_is_typed_no_secret_leak(self):
        self.db.fail_store=True
        self.deny('insight_generation_store_unavailable',self.create)

    def test_invalid_tenant_or_invalid_series_refused(self):
        self.deny('insight_generation_request_invalid',
                  generate_candidate_insight,self.db,self.query,'wrong',
                  None,None,None,None)
        self.deny('insight_generation_request_invalid',
                  generate_candidate_insight,self.db,'not-query',SERIES,
                  None,None,None,None)

    def test_local_cannot_supply_external_citations_even_in_canonical_json(self):
        fake=json.dumps({**self.prompt.expected_codes,
                         'additional_sources':['https://invented.example']})
        self.deny('insight_generation_local_invalid',
                  build_candidate_insight_generation,self.prompt,fake)

    def test_immutable_sql_source_fks_and_triggers(self):
        path=Path(__file__).resolve().parents[1]/'intelligence/migrations/204_pie_candidate_insight_generations.sql'
        sql=path.read_text('utf-8')
        self.assertIn('CREATE TABLE pie_candidate_insight_generations',sql)
        self.assertIn('FOREIGN KEY(project_id,series_id,source_revision)',sql)
        self.assertIn('FOREIGN KEY(project_id,model_attempt_id)',sql)
        self.assertIn('pie_candidate_insight_generation_immutable',sql)
        self.assertIn('pie_candidate_insight_generation_guard_insert',sql)
        self.assertIn('pie_pattern_registry_invalidations i',sql)
        self.assertIn('reported_charged',sql)
        self.assertNotIn('ALTER TABLE ',sql)


@unittest.skipUnless(os.environ.get('CM_TEST_PG_DSN'),'Disposable CM_TEST_PG_DSN absent')
class CandidateGenerationPostgreSQL17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.db=psycopg.connect(os.environ['CM_TEST_PG_DSN'],autocommit=True)
        name=cls.db.execute('SELECT current_database()').fetchone()[0]
        if 'test' not in name or name in ('postgres','template1','contentmachine'):
            cls.db.close()
            raise AssertionError('Explicit disposable PostgreSQL17 test DB required')
        migrate.apply_migrations(cls.db)
    @classmethod
    def tearDownClass(cls):
        if not cls.db.closed:cls.db.close()

    def test_migration_fresh_upgrade_and_append_only_schema(self):
        from intelligence import migrate
        self.assertEqual(migrate.apply_migrations(self.db),[])
        self.assertEqual(self.db.execute(
            "SELECT to_regclass('pie_candidate_insight_generations')").fetchone()[0],
            'pie_candidate_insight_generations')
        trigger_names={v[0] for v in self.db.execute(
            """SELECT tgname FROM pg_trigger
                WHERE tgrelid='pie_candidate_insight_generations'::regclass
                  AND NOT tgisinternal""").fetchall()}
        self.assertIn('pie_candidate_insight_generation_guard_insert',trigger_names)
        self.assertIn('pie_candidate_insight_generation_immutable',trigger_names)

    def test_cross_project_generation_fk_prevents_forged_pattern_insert(self):
        from psycopg.types.json import Jsonb
        from uuid import uuid4
        project=uuid4().hex
        self.db.execute(
            """INSERT INTO project_registry
               (id,name,profile_revision,profile_hash,created_at,updated_at)
               VALUES(%s,'PIE stage30 isolated',1,%s,now(),now())""",
            (project,'e'*64))
        try:
            with self.assertRaises(Exception):
                self.db.execute(
                    """INSERT INTO pie_candidate_insight_generations
                       (project_id,generation_sha256,series_id,source_revision,
                        hypothesis_sha256,source_prompt_sha256,source_facts_sha256,
                        candidate_draft_sha256,evidence_ids,generation_mode,artifact)
                       VALUES (%s,%s,%s,1,%s,%s,%s,%s,%s,'local_deterministic',%s)""",
                    (project,'a'*64,'b'*64,'c'*64,'d'*64,'e'*64,'f'*64,
                     Jsonb(['pie:unknown']),Jsonb({'can_publish':False})))
        finally:
            self.db.execute('DELETE FROM project_registry WHERE id=%s',(project,))

    def test_sql_dollar_quoted_stage29_dependency_present(self):
        f=Path(__file__).resolve().parents[1]/'intelligence/migrations/203_pie_model_dispatch_claims.sql'
        s=f.read_text('utf-8')
        self.assertEqual(s.count('$pie29$'),4)

if __name__=='__main__':
    unittest.main()
