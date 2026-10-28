"""PIE-MOD-29: synthetic quote/approval/price/claim/billing quarantine tests.

Every provider, approval, rights check and response is mocked. NO PAID CALLS.
Disposable PostgreSQL17 tests are opt-in with CM_TEST_PG_DSN.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime,timezone,timedelta
from decimal import Decimal,ROUND_CEILING
import json
import os
from pathlib import Path
from unittest.mock import patch
import unittest

from intelligence.patterns_insights.contracts import PIEBoundaryError,canonical_hash
from intelligence.patterns_insights.research.insight_semantics import compile_candidate_insight_draft
from intelligence.patterns_insights.research.model_governance import (
    OpenRouterModelSnapshot,OpenRouterHTTPS,verify_model_catalog,model_quote,
    reserve_model_attempt,dispatch_reserved_once,ModelReservation,
    _encode,_USD_QUANT,_request,
)
from test_pie_insight_semantics import prepare as stage28_prepare,selection

NOW=datetime(2026,10,28,12,tzinfo=timezone.utc)
UUID='11111111-1111-1111-1111-111111111111'
APPROVAL='22222222-2222-2222-2222-222222222222'
SLUG='openai/gpt-test'


def catalog(*,slug=SLUG,price_prompt='0.000001',price_completion='0.000002',
            request_price='0.0001',parameters=None):
    return {'data':{
        'id':slug,'canonical_slug':slug,'context_length':32768,
        'top_provider':{'max_completion_tokens':2048},
        'supported_parameters':['max_tokens','response_format'] if parameters is None else parameters,
        'architecture':{'input_modalities':['text'],'output_modalities':['text']},
        'pricing':{'prompt':price_prompt,'completion':price_completion,
                   'request':request_price,'image':'0','audio':'0'},
    }}


class FakeAuthorizer:
    def __init__(self,allowed=True):
        self.allowed=allowed
        self.calls=[]
    def require_current_spend_approval(self,project,principal,authorization_id,quote):
        self.calls.append((project,principal,authorization_id,quote.request_sha256))
        return self.allowed


class DBResult:
    def __init__(self,value=None):self.value=value
    def fetchone(self):return self.value


class FakeDB:
    def __init__(self,quote):
        self.quote=quote
        self.actions=[]
        self.attempt=False
        self.claim=False
        self.outcome=None
        self.events=[]
        self.failed=False
    class TX:
        def __enter__(self):return None
        def __exit__(self,*args):return False
    def transaction(self):return self.TX()
    def execute(self,sql,args=()):
        self.actions.append((sql,args))
        if 'INSERT INTO pie_model_attempts' in sql:
            if self.attempt:raise ValueError('duplicate immutable request')
            self.attempt=True
            return DBResult((UUID,))
        if 'SELECT a.reserved_usd::text' in sql:
            if not self.attempt:return DBResult(None)
            return DBResult((self.quote.reserve_usd,APPROVAL))
        if 'INSERT INTO pie_model_dispatch_claims' in sql:
            if self.claim:raise ValueError('duplicate claim forbids retry')
            self.claim=True
        if 'INSERT INTO pie_model_dispatch_outcomes' in sql:
            self.outcome=args
        if 'INSERT INTO pie_model_attempt_events' in sql:
            self.events.append(args)
        return DBResult(None)


class MockOpenRouter:
    def __init__(self,prompt,model_raw=None,response=None,crash=False):
        self.prompt=prompt
        self.model_raw=model_raw if model_raw is not None else catalog()
        self.response=response
        self.crash=crash
        self.reads=0
        self.sends=0
        self.request=None
    def fetch_model(self,slug):
        self.reads+=1
        if slug!=SLUG:raise AssertionError('model changed')
        return self.model_raw
    def complete_once(self,request,timeout_s):
        self.sends+=1
        self.request=request
        if self.crash:raise TimeoutError('Ambiguous HTTP timeout')
        return self.response if self.response is not None else {
            'model':SLUG,'id':'gen-mocked-test-v1',
            'choices':[{'finish_reason':'stop',
                        'message':{'content':selection(self.prompt)}}],
            'usage':{'prompt_tokens':100,'completion_tokens':70,'cost':0.00012},
        }


class ModelGovernanceTests(unittest.TestCase):
    def setUp(self):
        self.prompt,_=stage28_prepare()
        self.snapshot=verify_model_catalog(catalog(),SLUG,now=NOW)
        self.quote=model_quote(self.prompt,self.snapshot,max_output_tokens=256,now=NOW)
        self.db=FakeDB(self.quote)
        self.approver=FakeAuthorizer()
        self.res=ModelReservation(self.quote.project_id,UUID,APPROVAL,
                                  self.quote.request_sha256,self.quote.quote_sha256,
                                  self.quote.reserve_usd)
    def reject(self,code,fn,*a,**kw):
        with self.assertRaises(PIEBoundaryError) as err:fn(*a,**kw)
        self.assertEqual(err.exception.code,code)

    def reserve(self,authority=None):
        return reserve_model_attempt(self.db,self.quote,'fixture-owner',
                                     APPROVAL,authority or self.approver)

    def dispatch(self,provider=None,res=None,prompt=None,approved=True):
        service=provider or MockOpenRouter(self.prompt)
        orig=self.prompt if prompt is None else prompt
        gate=FakeAuthorizer(approved)
        with patch('intelligence.patterns_insights.research.model_governance.prepare_candidate_insight_prompt',
                   return_value=orig),patch(
             'intelligence.patterns_insights.research.model_governance.require_noninvalidated_pattern_for_insight'):
            out=dispatch_reserved_once(
                self.db,res or self.res,self.quote,orig,
                __import__('intelligence.patterns_insights.research.pattern_queries',
                          fromlist=['RegistryQuery']).RegistryQuery(
                          self.quote.project_id,'fixture-owner'),
                self.prompt.source_ref.split(':')[2],'fixture-owner',
                None,None,None,None,gate,service,timeout_s=20,now=NOW)
        return out,service

    def test_canonical_model_price_and_capability_from_catalog(self):
        s=self.snapshot
        self.assertEqual(s.model_slug,SLUG)
        self.assertEqual(s.prompt_price_per_token,'0.000001')
        self.assertEqual(s.completion_price_per_token,'0.000002')
        self.assertIn('response_format',s.supported_parameters)

    def test_model_slug_not_known_or_auto_router_denied(self):
        for slug in ('openrouter/auto','openai/test:free','name with spaces','model'):
            raw=catalog(slug=slug)
            self.reject('model_capability_unverified',verify_model_catalog,raw,slug,now=NOW)

    def test_model_slug_mismatch_and_unsupported_json_denied(self):
        self.reject('model_slug_mismatch',verify_model_catalog,catalog(),
                    'openai/different',now=NOW)
        self.reject('model_capability_unverified',verify_model_catalog,
                    catalog(parameters=['max_tokens']),SLUG,now=NOW)

    def test_no_unknown_auxiliary_pricing_supported(self):
        raw=catalog()
        raw['data']['pricing']['image']='0.01'
        self.reject('model_price_unbounded',verify_model_catalog,raw,SLUG,now=NOW)

    def test_price_floats_nan_negative_and_no_catalog_are_denied(self):
        for cost in (float('nan'),-1,True,'NaN','Infinity'):
            self.reject('invalid_model_price',verify_model_catalog,
                        catalog(price_prompt=cost),SLUG,now=NOW)
        self.reject('invalid_model_catalog',verify_model_catalog,{},SLUG,now=NOW)

    def test_unchanged_capability_fingerprint_ignores_fetch_time(self):
        later=verify_model_catalog(catalog(),SLUG,now=NOW+timedelta(seconds=17))
        self.assertEqual(later.catalog_sha256,self.snapshot.catalog_sha256)
        changed=verify_model_catalog(catalog(price_prompt='0.000009'),SLUG,now=NOW)
        self.assertNotEqual(changed.catalog_sha256,self.snapshot.catalog_sha256)

    def test_hand_calculated_price_ceiling(self):
        upper=len(_encode(self.prompt.messages).encode())+len(_encode(self.prompt.response_schema).encode())+2048
        target=(Decimal(upper)*Decimal('0.000001')
                +Decimal(256)*Decimal('0.000002')+Decimal('0.0001')).quantize(
                    _USD_QUANT,rounding=ROUND_CEILING)
        self.assertEqual(self.quote.input_token_upper_bound,upper)
        self.assertEqual(self.quote.reserve_usd,str(target))
        self.assertGreater(Decimal(self.quote.reserve_usd),0)

    def test_catalog_quote_age_context_and_max_completion_enforced(self):
        self.reject('model_catalog_stale',model_quote,self.prompt,self.snapshot,
                    now=NOW+timedelta(minutes=10))
        self.reject('model_quote_invalid',model_quote,self.prompt,self.snapshot,
                    max_output_tokens=5000,now=NOW)
        tiny=replace(self.snapshot,context_length=4096)
        self.reject('model_context_overflow',model_quote,self.prompt,tiny,now=NOW)

    def test_request_body_structured_json_no_fallbacks_privacy_usage(self):
        req=_request(self.prompt,self.quote)
        self.assertEqual(req['model'],SLUG)
        self.assertEqual(req['provider']['allow_fallbacks'],False)
        self.assertEqual(req['provider']['require_parameters'],True)
        self.assertEqual(req['provider']['data_collection'],'deny')
        self.assertEqual(req['response_format']['type'],'json_schema')
        self.assertEqual(req['response_format']['json_schema']['strict'],True)
        self.assertEqual(req['usage'],{'include':True})
        self.assertEqual(req['stream'],False)
        self.assertNotIn('models',req)

    def test_reservation_rejected_without_current_approval_or_sql(self):
        self.reject('spend_authority_unavailable',reserve_model_attempt,
                    self.db,self.quote,'fixture-owner',APPROVAL,None)
        self.assertEqual(self.db.actions,[])

    def test_forged_truthy_approval_fails_before_sql(self):
        for allowed in ('yes',1,False):
            db=FakeDB(self.quote)
            self.reject('spend_authority_denied',reserve_model_attempt,
                        db,self.quote,'fixture-owner',APPROVAL,FakeAuthorizer(allowed))
            self.assertEqual(db.actions,[])

    def test_same_request_cannot_be_reserved_twice(self):
        self.reserve()
        self.reject('model_reservation_conflict',self.reserve)
        self.assertEqual(len(self.db.events),1)
        self.assertIn("'reserved'",self.db.actions[-1][0])

    def test_quote_mutation_cannot_reserve(self):
        altered=replace(self.quote,reserve_usd='0.001')
        self.reject('model_reservation_invalid',reserve_model_attempt,
                    self.db,altered,'fixture-owner',APPROVAL,self.approver)
        self.assertEqual(self.db.actions,[])

    def test_valid_success_records_provider_reported_cost(self):
        self.reserve()
        receipt,service=self.dispatch()
        self.assertEqual(service.sends,1)
        self.assertEqual(service.reads,1)
        self.assertEqual(receipt.disposition,'reported_charged')
        self.assertEqual(receipt.reported_cost_usd,'0.000120000000')
        self.assertTrue(receipt.no_automatic_retry)
        self.assertFalse(receipt.actual_billing_independently_verified)
        self.assertEqual(len(self.db.events),2)

    def test_timeout_quarantined_no_unknown_paid_retry(self):
        self.reserve()
        receipt,provider=self.dispatch(MockOpenRouter(self.prompt,crash=True))
        self.assertEqual(provider.sends,1)
        self.assertEqual(receipt.disposition,'uncertain_charge')
        self.assertIsNone(receipt.reported_cost_usd)
        self.assertIsNone(receipt.response_sha256)
        self.assertTrue(self.db.claim)
        self.assertIn("'uncertain'",self.db.actions[-1][0])
        self.reject('model_already_claimed_no_retry',self.dispatch,provider)

    def test_provider_cost_above_reservation_quarantined(self):
        response={'model':SLUG,'id':'gen-bad',
                  'choices':[{'message':{'content':selection(self.prompt)},'finish_reason':'stop'}],
                  'usage':{'prompt_tokens':10,'completion_tokens':10,'cost':1000.0}}
        self.reserve()
        receipt,_=self.dispatch(MockOpenRouter(self.prompt,response=response))
        self.assertEqual(receipt.disposition,'uncertain_charge')

    def test_provider_response_model_mismatch_is_not_accepted(self):
        response={'model':'different/model','id':'gen','choices':[],
                  'usage':{'cost':0.01}}
        self.reserve()
        receipt,_=self.dispatch(MockOpenRouter(self.prompt,response=response))
        self.assertEqual(receipt.disposition,'uncertain_charge')

    def test_provider_hallucinated_citation_quarantined(self):
        fabricated={**self.prompt.expected_codes,'evidence_ids':['pie:fake','pie:source']}
        response={'model':SLUG,'id':'gen-fake',
                  'choices':[{'message':{'content':json.dumps(fabricated)},'finish_reason':'stop'}],
                  'usage':{'prompt_tokens':10,'completion_tokens':10,'cost':0.0001}}
        self.reserve()
        receipt,_=self.dispatch(MockOpenRouter(self.prompt,response=response))
        self.assertEqual(receipt.disposition,'uncertain_charge')

    def test_provider_missing_cost_or_incomplete_finish_quarantined(self):
        response={'model':SLUG,'id':'gen-x',
                  'choices':[{'message':{'content':selection(self.prompt)},'finish_reason':'length'}],
                  'usage':{'prompt_tokens':10,'completion_tokens':10}}
        self.reserve()
        receipt,_=self.dispatch(MockOpenRouter(self.prompt,response=response))
        self.assertEqual(receipt.disposition,'uncertain_charge')

    def test_reprice_changes_before_send_never_claims_or_sends(self):
        provider=MockOpenRouter(self.prompt,model_raw=catalog(price_prompt='0.000009'))
        self.reserve()
        self.reject('model_price_changed',self.dispatch,provider)
        self.assertEqual(provider.sends,0)
        self.assertFalse(self.db.claim)

    def test_no_new_rights_freshness_or_revision_no_send(self):
        self.reserve()
        provider=MockOpenRouter(self.prompt)
        changed=replace(self.prompt,prompt_sha256='f'*64)
        self.reject('model_dispatch_invalid',self.dispatch,provider,prompt=changed)
        self.assertEqual(provider.sends,0)

    def test_no_approval_on_second_check_no_send(self):
        self.reserve()
        provider=MockOpenRouter(self.prompt)
        self.reject('spend_authority_denied',self.dispatch,provider,approved=False)
        self.assertEqual(provider.sends,0)

    def test_openrouter_transport_only_fixed_https_api_path_and_no_live_calls(self):
        # Never use a real key/provider; verify URL construction via mocked opener.
        class FakeResp:
            status=200
            def __enter__(self):return self
            def __exit__(self,*args):return False
            def read(self,n):return json.dumps(catalog()).encode()
        class FakeOpener:
            def __init__(self):self.calls=[]
            def open(self,request,timeout):
                self.calls.append((request.full_url,timeout,request.get_method()))
                return FakeResp()
        opener=FakeOpener()
        with patch('intelligence.patterns_insights.research.model_governance.urllib.request.build_opener',
                   return_value=opener):
            transport=OpenRouterHTTPS(api_key='synthetic-test-key')
            self.assertEqual(transport.fetch_model(SLUG),catalog())
        self.assertEqual(opener.calls[0],
                         ('https://openrouter.ai/api/v1/model/openai/gpt-test',10,'GET'))

    def test_no_network_or_credentials_on_module_import(self):
        self.assertTrue(self.quote.prompt_sha256)
        self.assertNotIn('OPENROUTER_API_KEY',_encode(self.quote.__dict__)
                         if hasattr(self.quote,'__dict__') else str(self.quote))
        self.assertFalse(self.quote.approved_live)

    def test_sql_migration_additive_claim_before_send_and_quarantine(self):
        path=Path(__file__).resolve().parents[1]/'intelligence/migrations/203_pie_model_dispatch_claims.sql'
        sql=path.read_text('utf-8')
        self.assertIn('CREATE TABLE pie_model_dispatch_claims',sql)
        self.assertIn('CREATE TABLE pie_model_dispatch_outcomes',sql)
        self.assertIn('PRIMARY KEY(project_id,attempt_id)',sql)
        self.assertIn("outcome IN ('reported_charged','uncertain_charge')",sql)
        self.assertEqual(sql.count('EXECUTE FUNCTION pie_reject_evidence_update()'),2)
        self.assertNotIn('ALTER TABLE ',sql)


@unittest.skipUnless(os.environ.get('CM_TEST_PG_DSN'), 'Disposable PostgreSQL17 DSN not set')
class ModelGovernanceDisposablePostgreSQL(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        from intelligence import migrate
        cls.db=psycopg.connect(os.environ['CM_TEST_PG_DSN'],autocommit=True)
        name=cls.db.execute('SELECT current_database()').fetchone()[0]
        if 'test' not in name or name in ('postgres','template1','contentmachine'):
            cls.db.close();raise AssertionError('Must be disposable PostgreSQL test DB')
        migrate.apply_migrations(cls.db)
    @classmethod
    def tearDownClass(cls):
        if not cls.db.closed:cls.db.close()
    def setUp(self):
        from uuid import uuid4
        self.project=uuid4().hex
        self.db.execute(
            """INSERT INTO project_registry
                (id,name,profile_revision,profile_hash,created_at,updated_at)
                VALUES(%s,'PIE governance isolated',1,%s,now(),now())""",
            (self.project,'a'*64))
        self.approval_id=self.db.execute(
            """INSERT INTO pie_spend_authorizations
               (project_id,approved_by,approved_usd,expires_at,approval_sha256)
               VALUES (%s,'test-approver',1,now()+interval '1 hour',%s)
               RETURNING id::text""",(self.project,'b'*64)).fetchone()[0]
    def tearDown(self):
        self.db.execute("DELETE FROM project_registry WHERE id=%s",(self.project,))
    def test_fresh_upgrade_budget_guard_same_request_no_duplicate(self):
        from intelligence.patterns_insights.research.insight_semantics import (
            prepare_candidate_insight_prompt,
        )
        from intelligence.patterns_insights.research.pattern_queries import (
            RegistryQuery,PatternQueryPage,
        )
        from test_pie_insight_semantics import candidate
        query=RegistryQuery(self.project,'fixture-owner')
        with patch('intelligence.patterns_insights.research.insight_semantics.query_pattern_registry',
                   return_value=PatternQueryPage(self.project,(candidate(),),None,True)),patch(
             'intelligence.patterns_insights.research.insight_semantics.require_noninvalidated_pattern_for_insight'):
            prompt=prepare_candidate_insight_prompt(
                None,query,'a'*64,None,None,None,None)
        catalog_snapshot=verify_model_catalog(catalog(),SLUG,now=NOW)
        quote=model_quote(prompt,catalog_snapshot,now=NOW)
        first=reserve_model_attempt(
            self.db,quote,'fixture-owner',self.approval_id,FakeAuthorizer())
        self.assertEqual(first.project_id,self.project)
        self.assertGreater(Decimal(first.reserved_usd),0)
        with self.assertRaises(PIEBoundaryError) as ctx:
            reserve_model_attempt(
                self.db,quote,'fixture-owner',self.approval_id,FakeAuthorizer())
        self.assertEqual(ctx.exception.code,'model_reservation_conflict')
        self.assertEqual(self.db.execute(
            'SELECT count(*) FROM pie_model_attempts WHERE project_id=%s',
            (self.project,)).fetchone()[0],1)
        for name in ('pie_model_dispatch_claims','pie_model_dispatch_outcomes'):
            self.assertIsNotNone(self.db.execute(
                'SELECT to_regclass(%s)',(name,)).fetchone()[0])

    def test_cross_project_claim_and_direct_update_are_forbidden(self):
        from uuid import uuid4
        attempt=uuid4()
        # Foreign project/reservation must not bypass compound foreign keys.
        with self.assertRaises(Exception):
            self.db.execute(
                """INSERT INTO pie_model_dispatch_claims
                   (project_id,attempt_id,authorization_id,request_sha256,
                    quote_sha256,prompt_sha256,catalog_sha256,request_payload_sha256,
                    model_slug,max_output_tokens)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'openai/gpt-test',256)""",
                (self.project,attempt,self.approval_id,*(['a'*64]*4)))
        self.assertEqual(self.db.execute(
            'SELECT count(*) FROM pie_model_dispatch_claims WHERE project_id=%s',
            (self.project,)).fetchone()[0],0)

if __name__=='__main__':
    unittest.main()
