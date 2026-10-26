"""GB-MOD-10: REAL disposable PG17 cap locks, quote approval and ledger QA.

No OpenRouter HTTP requests. Positive 'enable_paid_execution' config is test
fixture only and cannot unlock the stage09 transport/actual billable execution.
"""
from __future__ import annotations

import concurrent.futures
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import os
from threading import Barrier
import unittest
from uuid import uuid4

from intelligence.migrate import apply_migrations
from intelligence.group_b.budget import BudgetDenied,quote_context
from intelligence.group_b.ledger import BudgetLedger
from intelligence.group_b.configuration import GroupBConfig
from intelligence.group_b.context.builder import ContextPacket
from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.providers.protocols import ModelDescriptor,ModelCatalogSnapshot
from intelligence.group_b.errors import ExecutionNotApproved

HASH="a"*64
VALID_KEY="fixture-provider-"+str(uuid4())


def disposable_dsn():
    from psycopg.conninfo import conninfo_to_dict
    dsn=os.environ["CM_TEST_PG_DSN"]
    db=conninfo_to_dict(dsn).get("dbname","").lower()
    if "test" not in db and "_ci" not in db:
        raise RuntimeError("Refusing nondisposable DB")
    return dsn


class ReceiptVerifier:
    def verify(self,receipt,expected_request_id,expected_sha256):
        return (receipt["request_id"]==expected_request_id and
                receipt["request_sha256"]==expected_sha256 and
                receipt.get("state") in ("settled","no_charge"))


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"No disposable PostgreSQL 17 DSN")
class TestBudgetPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg=psycopg
        cls.dsn=disposable_dsn()
        with psycopg.connect(cls.dsn) as conn:
            apply_migrations(conn)

    def setUp(self):
        self.project=uuid4().hex
        self.source="gb-budget-"+uuid4().hex
        self.key=uuid4().hex
        self.cfg=GroupBConfig(
            enable_paid_execution=True,
            max_paid_attempt_usd=Decimal("0.010000000000"),
        )
        self.ledger=BudgetLedger(lambda:self.psycopg.connect(self.dsn))
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                """INSERT INTO project_registry(id,name,profile_revision,profile_hash,
                   created_at,updated_at) VALUES (%s,'Budget PG test',1,%s,now(),now())""",
                (self.project,HASH)
            )
            conn.execute(
                """INSERT INTO sources(id,provider,source_type,status,can_analyze_media)
                   VALUES (%s,'test','owned','enabled',TRUE)""",(self.source,)
            )
            video=conn.execute(
                """INSERT INTO videos(platform,platform_video_id,canonical_url,content_kind)
                   VALUES('tiktok',%s,%s,'video') RETURNING id""",
                (self.key,"https://example.test/budget/"+self.key)
            ).fetchone()[0]
            inbox=conn.execute(
                """INSERT INTO group_b_inbox(project_id,video_id,source_id,
                   event_key,qdp_run_id,producer_schema,event_sha256,
                   preparation_sha256)
                   VALUES(%s,%s,%s,%s,%s,'qdp-handoff/v1',%s,%s)
                   RETURNING id""",
                (self.project,video,self.source,str(uuid4()),str(uuid4()),
                 "e"*64,"b"*64)
            ).fetchone()[0]
            conn.execute(
                "UPDATE group_b_inbox SET status='accepted' WHERE id=%s",(inbox,)
            )
            self.run=str(conn.execute(
                """INSERT INTO group_b_runs(project_id,inbox_id,video_id,source_id,
                   content_type,a_snapshot_sha256,media_manifest_sha256,
                   config_sha256,idempotency_key)
                   VALUES(%s,%s,%s,%s,'video',%s,%s,%s,%s) RETURNING id""",
                (self.project,inbox,video,self.source,"f"*64,"b"*64,
                 "c"*64,uuid4().hex)
            ).fetchone()[0])
            conn.execute(
                """INSERT INTO group_b_budget_project_limits
                   (project_id,enabled,lifetime_ceiling_usd)
                   VALUES(%s,TRUE,0.010000000000)""",(self.project,)
            )
            conn.execute(
                """INSERT INTO group_b_budget_run_limits
                   (project_id,run_id,enabled,lifetime_ceiling_usd)
                   VALUES(%s,%s::uuid,TRUE,0.010000000000)""",
                (self.project,self.run)
            )

    def make_quote(self,request_sha=None,when=None):
        model=ModelDescriptor(
            "test/budget","d"*64,30000,1024,
            frozenset(("text",)),frozenset(("text",)),
            frozenset(("structured_outputs",)),
            Decimal("0.000001"),Decimal("0.000002"),
            Decimal("0"),Decimal("0.000010"),
        )
        catalog=ModelCatalogSnapshot((model,),"d"*64,"fixture/mock",True)
        payload={
            "schema_version":"group-b-context/v1","project_id":self.project,
            "b_run_id":self.run,"block":"B7","delivery_state":"not_sent",
            "media_delivery_state":"opaque_refs_not_attached",
            "source_evidence_manifest_sha256":"b"*64,
            "entries":[],"coverage":{"selected_image_refs":0},
        }
        p=ContextPacket(payload,canonical_hash(payload),100,500)
        return quote_context(
            packet=p,catalog=catalog,model="test/budget",
            request_sha256=request_sha or "e"*64,
            output_tokens=500,prompt_tokens_ceiling=6000,
            created_at=when or datetime.now(timezone.utc)
        )

    def create_approved(self,request_sha=None):
        quote=self.make_quote(request_sha)
        stored=self.ledger.save_quote(quote)
        approved=self.ledger.approve_quote(
            project_id=self.project,run_id=self.run,quote_id=stored.id,
            approved_by="authenticated-ci-operator",
            authorized_usd=quote.quoted_usd,explicit_confirmation=True,
        )
        return stored,approved,quote

    def reserve(self,quote,approval, *,key=None,sha="e"*64,config=None):
        return self.ledger.reserve_attempt(
            project_id=self.project,run_id=self.run,
            quote_id=quote.id,approval_id=approval.id,
            request_key=key or "request-"+uuid4().hex,
            request_sha256=sha,config=config or self.cfg,
        )

    def test_quotes_immutable_and_double_approval_idempotent(self):
        quote=self.make_quote()
        stored=self.ledger.save_quote(quote)
        self.assertEqual(stored.id,self.ledger.save_quote(quote).id)
        with self.assertRaises(ExecutionNotApproved):
            self.ledger.approve_quote(
                project_id=self.project,run_id=self.run,
                quote_id=stored.id,approved_by="authenticated-ci-operator",
                authorized_usd=quote.quoted_usd,explicit_confirmation=False
            )
        confirmation=self.ledger.approve_quote(
            project_id=self.project,run_id=self.run,quote_id=stored.id,
            approved_by="authenticated-ci-operator",
            authorized_usd=quote.quoted_usd,explicit_confirmation=True
        )
        same=self.ledger.approve_quote(
            project_id=self.project,run_id=self.run,quote_id=stored.id,
            approved_by="authenticated-ci-operator",
            authorized_usd=quote.quoted_usd,explicit_confirmation=True
        )
        self.assertEqual(same.id,confirmation.id)
        with self.assertRaises(BudgetDenied):
            self.ledger.approve_quote(
                project_id=self.project,run_id=self.run,quote_id=stored.id,
                approved_by="other",authorized_usd=quote.quoted_usd,
                explicit_confirmation=True
            )
        with self.psycopg.connect(self.dsn) as conn:
            with self.assertRaises(Exception):
                with conn.transaction():
                    conn.execute(
                        "UPDATE group_b_model_quotes SET quoted_usd=2 WHERE id=%s",
                        (stored.id,)
                    )

    def test_expired_quote_disabled_caps_and_run_cap(self):
        old=self.make_quote(when=datetime.now(timezone.utc)-timedelta(hours=1))
        stored=self.ledger.save_quote(old)
        with self.assertRaises(BudgetDenied):
            self.ledger.approve_quote(
                project_id=self.project,run_id=self.run,quote_id=stored.id,
                approved_by="authenticated-ci-operator",
                authorized_usd=old.quoted_usd,explicit_confirmation=True
            )
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                "UPDATE group_b_budget_run_limits SET enabled=FALSE WHERE project_id=%s",
                (self.project,)
            )
        q=self.make_quote("1"*64)
        quote=self.ledger.save_quote(q)
        with self.assertRaises(BudgetDenied):
            self.ledger.approve_quote(
                project_id=self.project,run_id=self.run,quote_id=quote.id,
                approved_by="authenticated-ci-operator",
                authorized_usd=q.quoted_usd,explicit_confirmation=True
            )
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                "UPDATE group_b_budget_run_limits SET enabled=TRUE WHERE project_id=%s",
                (self.project,)
            )
        stored,approved,_=self.create_approved("2"*64)
        with self.assertRaises(ExecutionNotApproved):
            self.reserve(stored,approved,sha="2"*64,config=GroupBConfig())
        with self.psycopg.connect(self.dsn) as conn:
            conn.execute(
                "UPDATE group_b_budget_run_limits SET lifetime_ceiling_usd=0.001 WHERE project_id=%s",
                (self.project,)
            )
        with self.assertRaises(BudgetDenied):
            self.reserve(stored,approved,sha="2"*64)

    def test_reservation_idempotency_no_double_click_spend(self):
        quote,approval,_=self.create_approved()
        key="request-"+uuid4().hex
        first=self.reserve(quote,approval,key=key)
        replay=self.reserve(quote,approval,key=key)
        self.assertEqual(first.id,replay.id)
        with self.assertRaises(BudgetDenied):
            self.reserve(quote,approval,key="request-"+uuid4().hex)
        with self.psycopg.connect(self.dsn) as conn:
            self.assertEqual(conn.execute(
                """SELECT count(*) FROM group_b_budget_events
                   WHERE project_id=%s AND event_type='reserved'""",
                (self.project,)
            ).fetchone()[0],1)
            with self.assertRaises(Exception):
                with conn.transaction():
                    conn.execute(
                        "UPDATE group_b_budget_events SET amount_usd=1 WHERE project_id=%s",
                        (self.project,)
                    )

    def test_ambiguous_submission_blocks_retry_until_trusted_reconciliation(self):
        quote,approval,_=self.create_approved()
        attempt=self.reserve(quote,approval)
        self.ledger.mark_submission(
            project_id=self.project,attempt_id=attempt.id,
            provider_request_id=VALID_KEY
        )
        self.ledger.mark_ambiguous(project_id=self.project,attempt_id=attempt.id)
        newquote,newapproval,_=self.create_approved("2"*64)
        with self.assertRaises(BudgetDenied) as blocked:
            self.reserve(newquote,newapproval,sha="2"*64)
        self.assertEqual(blocked.exception.reason,"unreconciled_charge_blocks_retry")
        receipt={
            "state":"settled","request_id":VALID_KEY,"request_sha256":"e"*64,
            "charged_usd":Decimal("0.006000000000"),
            "prompt_tokens":5000,"completion_tokens":300,
        }
        with self.assertRaises(BudgetDenied):
            self.ledger.reconcile_attempt(
                project_id=self.project,attempt_id=attempt.id,
                authenticated_receipt=receipt,receipt_verifier=None
            )
        status=self.ledger.reconcile_attempt(
            project_id=self.project,attempt_id=attempt.id,
            authenticated_receipt=receipt,receipt_verifier=ReceiptVerifier()
        )
        self.assertEqual(status,"settled")
        with self.psycopg.connect(self.dsn) as conn:
            events=conn.execute(
                """SELECT event_type FROM group_b_budget_events
                   WHERE project_id=%s AND attempt_id=%s::uuid ORDER BY event_no""",
                (self.project,attempt.id)
            ).fetchall()
            self.assertEqual(events,[("reserved",),("submitted",),
                                     ("uncertain",),("settled",)])
            with self.assertRaises(Exception):
                with conn.transaction():
                    conn.execute(
                        """UPDATE group_b_model_attempts SET reserved_usd=0
                           WHERE id=%s::uuid""",(attempt.id,)
                    )
        # Even after settlement, no new reservation can exceed the
        # remaining 0.004 project lifetime budget.
        with self.assertRaises(BudgetDenied):
            self.reserve(newquote,newapproval,sha="2"*64)

    def test_unknown_charge_above_reserved_never_looks_settled(self):
        quote,approval,_=self.create_approved()
        attempt=self.reserve(quote,approval)
        self.ledger.mark_submission(
            project_id=self.project,attempt_id=attempt.id,
            provider_request_id=VALID_KEY
        )
        receipt={
            "state":"settled","request_id":VALID_KEY,"request_sha256":"e"*64,
            "charged_usd":Decimal("999"),"prompt_tokens":100,
            "completion_tokens":100,
        }
        with self.assertRaises(BudgetDenied) as e:
            self.ledger.reconcile_attempt(
                project_id=self.project,attempt_id=attempt.id,
                authenticated_receipt=receipt,receipt_verifier=ReceiptVerifier()
            )
        self.assertEqual(e.exception.reason,"provider_charge_over_reservation")
        self.ledger.mark_ambiguous(project_id=self.project,attempt_id=attempt.id)
        self.assertEqual(self.psycopg.connect(self.dsn).execute(
            "SELECT charge_state FROM group_b_model_attempts WHERE id=%s::uuid",
            (attempt.id,)
        ).fetchone()[0],"uncertain")

    def test_concurrent_project_reservations_never_exceed_ceiling(self):
        one,approval_one,_=self.create_approved("3"*64)
        two,approval_two,_=self.create_approved("4"*64)
        start=Barrier(2)
        def worker(quote,approval,sha):
            start.wait(timeout=15)
            try:
                return ("ok",self.reserve(quote,approval,sha=sha).id)
            except BudgetDenied as error:
                return ("blocked",error.reason)
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            a=pool.submit(worker,one,approval_one,"3"*64)
            b=pool.submit(worker,two,approval_two,"4"*64)
            result=[a.result(timeout=25),b.result(timeout=25)]
        self.assertCountEqual([x[0] for x in result],["ok","blocked"])
        with self.psycopg.connect(self.dsn) as conn:
            amount=conn.execute(
                """SELECT COALESCE(SUM(reserved_usd),0)
                     FROM group_b_model_attempts WHERE project_id=%s""",
                (self.project,)
            ).fetchone()[0]
        self.assertLessEqual(amount,Decimal("0.010000000000"))


if __name__=="__main__":
    unittest.main()
