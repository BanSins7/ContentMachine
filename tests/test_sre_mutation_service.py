"""M39 durable request idempotency and closed payment/review dispatch tests."""
import unittest
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch

from intelligence.relevance import mutation_service as m
from intelligence.relevance.v1_contracts import SREContractError

PID="a"*32
OTHER="b"*32
RUN="11111111-1111-4111-8111-111111111111"
PUB="22222222-2222-4222-8222-222222222222"
KEY="mut-20261028-0001"
HASH="f"*64


class Cursor:
    def __init__(self,row):self.row=row
    def fetchone(self):return self.row


class MockConnection:
    autocommit=True
    info=SimpleNamespace(transaction_status=SimpleNamespace(name="IDLE"))
    def __init__(self):
        self.facts={}
        self.events=[]
        self.fail_commit=False
    def __call__(self):return self
    def __enter__(self):return self
    def __exit__(self,*a):return False
    @contextmanager
    def transaction(self):
        yield self
    def execute(self,sql,params=None):
        self.events.append((sql,params))
        if sql==m.SQL_CLAIM:
            pid,actor,op,key,sha=params;ident=(pid,actor,op,key)
            if ident in self.facts:return Cursor(None)
            self.facts[ident]=[sha,"pending",None]
            return Cursor(("pending",))
        if sql==m.SQL_EXIST:
            item=self.facts.get(tuple(params))
            return Cursor(tuple(item) if item else None)
        if sql==m.SQL_COMMIT:
            if self.fail_commit:raise RuntimeError("DB lost before ACK")
            data,pid,actor,op,key,sha=params
            row=self.facts.get((pid,actor,op,key))
            if row is None or row[0]!=sha or row[1]!="pending":
                return Cursor(None)
            import json
            row[1]="committed";row[2]=json.loads(data)
            return Cursor(("committed",))
        raise AssertionError("unexpected SQL")


def service(db,**extra):
    return m.TrustedSREMutationService(
        connect=db,
        actor_authorizer=lambda pid,actor,permission:
            pid==PID and actor=="authenticated:human" and
            permission.startswith("api_mutate_sre_"),
        **extra,
    )


class M39ServiceTests(unittest.TestCase):
    def setUp(self):
        self.db=MockConnection()
        self.svc=service(self.db)
        self.cancel={"run_id":RUN}
    def test_cancel_replay_is_exactly_once_and_result_is_not_current_authority(self):
        with patch.object(self.svc,"_execute",return_value={
            "run_id":RUN,"status":"cancelled","unknown_items":0,
        }) as call:
            first=self.svc.perform(PID,"authenticated:human",
                                   "cancel_run",KEY,self.cancel)
            replay=self.svc.perform(PID,"authenticated:human",
                                    "cancel_run",KEY,self.cancel)
        self.assertEqual(call.call_count,1)
        self.assertEqual(first["state"],"committed")
        self.assertEqual(replay["state"],
                         "previously_committed_not_current_authorization")
        self.assertTrue(replay["replayed"])
        self.assertEqual(replay["receipt"],first["receipt"])
        self.assertEqual(first["paid_calls"],0)
        self.assertEqual(len(self.db.facts),1)
    def test_same_key_changed_body_denied_even_after_commit(self):
        with patch.object(self.svc,"_execute",return_value={"run_id":RUN,
                "status":"cancelled","unknown_items":0}):
            self.svc.perform(PID,"authenticated:human","cancel_run",KEY,self.cancel)
            with self.assertRaises(SREContractError) as ctx:
                self.svc.perform(PID,"authenticated:human","cancel_run",KEY,
                                 {"run_id":PUB})
            self.assertEqual(ctx.exception.code,"idempotency_conflict")
        self.assertEqual(len(self.db.facts),1)
    def test_crash_after_domain_effect_does_not_repeat_effect(self):
        self.db.fail_commit=True
        with patch.object(self.svc,"_execute",return_value={"run_id":RUN,
                "status":"cancelled"}) as call:
            with self.assertRaises(RuntimeError):
                self.svc.perform(PID,"authenticated:human","cancel_run",
                                 KEY,self.cancel)
            self.db.fail_commit=False
            with self.assertRaises(SREContractError) as ctx:
                self.svc.perform(PID,"authenticated:human","cancel_run",
                                 KEY,self.cancel)
        self.assertEqual(ctx.exception.code,"mutation_in_doubt")
        self.assertEqual(call.call_count,1)
        self.assertEqual(next(iter(self.db.facts.values()))[1],"pending")
    def test_in_doubt_competing_request_is_fenced_without_work(self):
        self.db.facts[(PID,"authenticated:human","cancel_run",KEY)]=[
            m.fingerprint({"schema_version":m.VERSION,"project_id":PID,
                           "actor_id":"authenticated:human",
                           "operation":"cancel_run","body":self.cancel}),
            "pending",None]
        with patch.object(self.svc,"_execute") as call:
            with self.assertRaises(SREContractError) as ctx:
                self.svc.perform(PID,"authenticated:human",
                                 "cancel_run",KEY,self.cancel)
            self.assertEqual(ctx.exception.code,"mutation_in_doubt")
            call.assert_not_called()
    def test_actor_tenant_missing_permission_or_bad_key_before_claim(self):
        for pid,who,key in [
            (OTHER,"authenticated:human",KEY),
            (PID,"untrusted:actor",KEY),
            (PID,"authenticated:human","too-short"),
        ]:
            with self.assertRaises(SREContractError):
                self.svc.perform(pid,who,"cancel_run",key,self.cancel)
        self.assertEqual(self.db.events,[])
    def test_input_whitelist_denies_api_key_paid_switch_and_uuid_bigint(self):
        cases=[
            ("reconcile_unknown",{"run_id":RUN,"item_id":3,"intent_id":PUB,
                                   "api_key":"SECRET"}),
            ("reconcile_unknown",{"run_id":RUN,"item_id":3,"intent_id":PUB,
                                   "generation_id":"paid"}),
            ("create_run",{"publication_id":PUB,
                           "content_version_ids":["uuid-invalid"],
                           "pipeline_config_id":1,"idempotency_key":KEY}),
            ("attach",{"discovery_ids":[2,1]}),
            ("attach",{"discovery_ids":[1,1]}),
            ("grant_consent",{"run_id":RUN,"item_id":1,"quote_sha256":HASH,
                              "explicit_confirmation":{"action":"fake"},
                              "execute_paid":True}),
            ("human_review",{"run_id":RUN,"item_id":1,"category_key":PUB,
                             "human_decision":"irrelevant","reason_code":"wrong",
                             "explicit_confirmation":{}}),
        ]
        for op,payload in cases:
            with self.subTest(op=op,payload=payload):
                with self.assertRaises(SREContractError):
                    self.svc.perform(PID,"authenticated:human",op,KEY,payload)
        self.assertEqual(self.db.events,[])
    def test_selected_attach_calls_existing_intake_with_real_d1_ids(self):
        with patch.object(m,"attach_discovery",side_effect=[
            {"video_id":21,"content_version_id":101,"replayed":False},
            {"video_id":22,"content_version_id":102,"replayed":True}
        ]) as internal:
            result=self.svc.perform(PID,"authenticated:human","attach",KEY,
                                    {"discovery_ids":[31,32]})
        self.assertEqual(internal.call_count,2)
        self.assertEqual(result["receipt"],{"count":2,"ids":[101,102]})
        self.assertEqual(result["paid_calls"],0)
    def test_catalog_missing_blocks_before_claim(self):
        body={"run_id":RUN,"item_id":1}
        with self.assertRaises(SREContractError) as ctx:
            self.svc.perform(PID,"authenticated:human","issue_quote",KEY,body)
        self.assertEqual(ctx.exception.code,"rights_denied")
        self.assertEqual(self.db.facts,{})
        self.assertNotIn("provider_response",m.BODY_KEYS["issue_quote"])
    def test_human_review_requires_secondary_interactive_proof(self):
        body={"run_id":RUN,"item_id":3,"category_key":PUB,
              "human_decision":"match","reason_code":"verified_model_decision",
              "explicit_confirmation":{"action":"sre-human-review-confirm/v1"}}
        with self.assertRaises(SREContractError):
            self.svc.perform(PID,"authenticated:human","human_review",KEY,body)
        self.assertEqual(self.db.facts,{})
    def test_run_idempotency_keys_match_before_any_claim(self):
        with self.assertRaises(SREContractError):
            self.svc.perform(PID,"authenticated:human","create_run",KEY,{
                "publication_id":PUB,"content_version_ids":[13],
                "pipeline_config_id":2,"idempotency_key":"different-run-id-01"})
        self.assertEqual(self.db.facts,{})

    def test_spending_consent_requires_authenticated_human_before_claim(self):
        def catalog(pid,actor,operation):
            return {"catalog_snapshot":{},"operator_expected_sha256":HASH,
                    "operator_endpoint_tag":"exact-opus",
                    "operator_model_revision":"v1",
                    "operator_maximum_usd":"1.000000000"}
        svc=service(self.db,catalog_resolver=catalog)
        with self.assertRaises(SREContractError) as ctx:
            svc.perform(PID,"authenticated:human","grant_consent",KEY,{
                "run_id":RUN,"item_id":1,"quote_sha256":HASH,
                "explicit_confirmation":{"action":"authorize_sre_spending"}})
        self.assertEqual(ctx.exception.code,"actor_not_authorized")
        self.assertEqual(self.db.facts,{})

    def test_domain_uses_exact_existing_services_without_paid_dispatch(self):
        self.assertNotIn("dispatch",m.ALLOWED_OPERATIONS)
        self.assertNotIn("reserve_budget",m.ALLOWED_OPERATIONS)
        self.assertNotIn("settle_generation",m.ALLOWED_OPERATIONS)
        self.assertIn("reconcile_unknown",m.ALLOWED_OPERATIONS)
        self.assertNotIn("api_key",m.BODY_KEYS["reconcile_unknown"])


if __name__=="__main__":
    unittest.main()
