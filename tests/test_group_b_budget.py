"""GB-MOD-10: deterministic quote and deny-by-default budget tests.

All pure tests have no HTTP/model side effects, no credentials, no dollar spend.
"""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from pathlib import Path
from uuid import uuid4
import unittest
from unittest.mock import Mock

from intelligence.group_b.budget import BudgetDenied, quote_context
from intelligence.group_b.ledger import BudgetLedger
from intelligence.group_b.configuration import GroupBConfig
from intelligence.group_b.context.builder import ContextPacket
from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.providers.protocols import ModelDescriptor,ModelCatalogSnapshot
from intelligence.group_b.errors import ExecutionNotApproved

PID="a"*32
RUN=str(uuid4())
SHA="b"*64


def packet(*,images=0):
    payload={
        "schema_version":"group-b-context/v1","project_id":PID,
        "b_run_id":RUN,"block":"B7",
        "source_evidence_manifest_sha256":SHA,
        "delivery_state":"not_sent","media_delivery_state":"opaque_refs_not_attached",
        "entries":[],"coverage":{"selected_image_refs":images},
    }
    return ContextPacket(payload,canonical_hash(payload),250,500)


def catalog(*,input_modalities=("text",),prompt="0.000001",completion="0.000002",
            request="0.000010",max_output=1000,context=50000,priced=True):
    descriptor=ModelDescriptor(
        slug="fixture/strict",catalog_sha256="e"*64,
        context_tokens=context,max_output_tokens=max_output,
        input_modalities=frozenset(input_modalities),
        output_modalities=frozenset(("text",)),
        supported_parameters=frozenset(("structured_outputs",)),
        prompt_usd_per_token=Decimal(prompt),
        completion_usd_per_token=Decimal(completion),
        image_usd=Decimal("0.50") if priced else Decimal("0"),
        request_usd=Decimal(request),
    )
    return ModelCatalogSnapshot((descriptor,),"e"*64,"fixture/mock",True)


def create_quote(*,pkt=None,cat=None,prompt_tokens=6000,output=500,
                 created=None,ttl=timedelta(minutes=10),config=None):
    return quote_context(
        packet=pkt or packet(),catalog=cat or catalog(),
        model="fixture/strict",request_sha256="c"*64,
        output_tokens=output,prompt_tokens_ceiling=prompt_tokens,
        created_at=created or datetime.now(timezone.utc),
        ttl=ttl,config=config,
    )


class TestPureBudget(unittest.TestCase):
    def test_quote_exact_decimal_ceiling_catalog_snapshot(self):
        q=create_quote()
        expected=(Decimal("6000")*Decimal("0.000001")+
                  Decimal("500")*Decimal("0.000002")+
                  Decimal("0.000010"))
        self.assertEqual(q.quoted_usd,expected)
        self.assertTrue(q.verify())
        self.assertEqual(q.pricing_snapshot["catalog_sha256"],"e"*64)
        self.assertEqual(q.quote_details["provider_execution"],"not_authorized")
        self.assertEqual(q.quote_details["packet_sha256"],packet().packet_sha256)
        self.assertEqual(q.expires_at-q.created_at,timedelta(minutes=10))
        self.assertEqual(q.quote_sha256,create_quote(
            created=q.created_at).quote_sha256)

    def test_tampered_cost_or_model_or_context_blocks(self):
        q=create_quote()
        self.assertFalse(replace(q,quoted_usd=Decimal("0.001")).verify())
        self.assertFalse(replace(q,model_id="fake/cheaper").verify())
        bad=replace(packet(),packet_sha256="0"*64)
        with self.assertRaises(BudgetDenied):
            create_quote(pkt=bad)

    def test_expired_and_future_and_oversize_quote(self):
        now=datetime.now(timezone.utc)
        with self.assertRaises(BudgetDenied):
            create_quote(ttl=timedelta(0))
        with self.assertRaises(BudgetDenied):
            create_quote(ttl=timedelta(days=1))
        with self.assertRaises(BudgetDenied):
            create_quote(created=now+timedelta(days=2))
        with self.assertRaises(BudgetDenied):
            create_quote(prompt_tokens=100)
        with self.assertRaises(BudgetDenied):
            create_quote(prompt_tokens=49500,output=900)
        with self.assertRaises(BudgetDenied):
            create_quote(output=2000)

    def test_no_unverified_image_or_cache_price_inferred(self):
        image_pkt=packet(images=1)
        with self.assertRaises(BudgetDenied) as error:
            create_quote(pkt=image_pkt,cat=catalog(input_modalities=("text","image")))
        self.assertEqual(error.exception.reason,"multimodal_billing_unit_unknown")

    def test_per_attempt_ceiling_and_disabled_mode(self):
        enabled=GroupBConfig(
            enable_paid_execution=True,max_paid_attempt_usd=Decimal("0.001"))
        with self.assertRaises(BudgetDenied):
            create_quote(config=enabled)
        # Read-only quoting itself is allowed without payment activation.
        self.assertTrue(create_quote(config=GroupBConfig()).verify())

    def test_negative_price_zero_unknown_model_and_nan(self):
        for price in ("-0.1","NaN","Infinity"):
            try:
                source=catalog(prompt=price)
            except Exception:
                continue
            with self.subTest(price=price),self.assertRaises(BudgetDenied):
                create_quote(cat=source)
        with self.assertRaises(BudgetDenied):
            create_quote(cat=catalog(prompt="0",completion="0",request="0"))
        with self.assertRaises(BudgetDenied):
            create_quote(cat=ModelCatalogSnapshot((),"e"*64,"mock",True))

    def test_budget_manager_requires_factory_and_never_connects_on_construction(self):
        factory=Mock()
        BudgetLedger(factory)
        factory.assert_not_called()
        with self.assertRaises(TypeError):
            BudgetLedger(None)

    def test_057_migration_additive_limits_and_guard(self):
        root=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"
        sql=(root/"057_group_b_budget_ledger.sql").read_text(encoding="utf-8")
        for table in ("group_b_budget_project_limits","group_b_budget_run_limits",
                      "group_b_budget_events"):
            self.assertIn("CREATE TABLE "+table,sql)
        for required in ("FOR EACH ROW","group_b_budget_attempt_guard",
                         "trg_group_b_budget_events_immutable",
                         "group_b_attempt_one_per_quote",
                         "group_b_attempt_provider_request_unique",
                         "lifetime_ceiling_usd","SETTLE" if False else "settled"):
            self.assertIn(required,sql)
        self.assertNotIn("DROP TABLE",sql)
        self.assertNotIn("ALTER TABLE content_b_",sql)


if __name__=="__main__":
    unittest.main()
