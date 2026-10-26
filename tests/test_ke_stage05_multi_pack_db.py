"""Stage05 true PostgreSQL17 Multi-Pack proof using synthetic A/B1 and model prices."""
from __future__ import annotations
from decimal import Decimal
from uuid import uuid4
import unittest

from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb

from test_ke_stage02_intake_db import DSN, ReadOnlyIntakePG
from test_ke_stage05_multi_pack import MULTI
from intelligence.knowledge_extraction.repository import KnowledgeInputRepository
from intelligence.knowledge_extraction.multi_pack_repository import MultiPackRepository
from intelligence.knowledge_extraction.multi_pack import verify_multi_pack_plan
from intelligence.knowledge_extraction.intake import IntakeError


@unittest.skipUnless(DSN,"PostgreSQL17 required for B Stage05")
class Stage05MultiPackPG(ReadOnlyIntakePG):
    def setUp(self):
        super().setUp()
        self._catalog()

    def _catalog(self, *, context_length=16000):
        self.model_id="xiaomi/mimo-v2.6-flash"
        with self.psycopg.connect(DSN) as conn:
            ident=conn.execute(
                """INSERT INTO tb_model_snapshots(model_id,catalog_sha256,capabilities,pricing)
                   VALUES(%s,%s,%s,%s) RETURNING id""",
                (self.model_id,uuid4().hex+uuid4().hex,
                 Jsonb({"model_id":self.model_id,"context_length":context_length,
                        "max_output_tokens":4096,"text_output":True,
                        "supports_structured_outputs":True}),
                 Jsonb({"prompt":"0.0000002","completion":"0.000001",
                        "internal_reasoning":"0.0000005","request":"0.0001"})),
            ).fetchone()[0]
            fresh=conn.execute(
                """INSERT INTO tb_catalog_refreshes(models_present)
                   VALUES(%s) RETURNING id""",
                (Jsonb([self.model_id]),),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO tb_catalog_refresh_models(refresh_id,model_id,snapshot_id)
                   VALUES(%s,%s,%s)""",
                (fresh,self.model_id,ident),
            )

    def _profile(self, *, length=10000, revision=2):
        value=("Исследование продукта 🌍 " * 600)[:length]
        # Context A's sanitized profile keeps values <=12000 characters.
        with self.psycopg.connect(DSN) as conn:
            conn.execute(
                """INSERT INTO project_profile_versions
                     (project_id,revision,schema_version,profile_hash,profile)
                   VALUES(%s,%s,1,%s,%s)""",
                (self.pid,revision,str(revision)*64,
                 Jsonb({"niche":"Technology","goal":"Learn",
                        "product_description":value})),
            )
            conn.execute(
                """UPDATE project_registry SET profile_revision=%s,profile_hash=%s,
                      updated_at=clock_timestamp() WHERE id=%s""",
                (revision,str(revision)*64,self.pid),
            )
        return value

    def _setup_bundle(self, *, large=True, video=False):
        if large:
            self._profile()
        if video:
            with self.psycopg.connect(DSN) as conn:
                self.make_source(conn,str(uuid4()),video=True)
        cid=self.approve()
        repo=KnowledgeInputRepository(connection_factory=lambda:self.psycopg.connect(DSN))
        bid=repo.create(self.pid,cid)["bundle_id"]
        return cid,bid

    def _repo(self):
        return MultiPackRepository(connection_factory=lambda:self.psycopg.connect(DSN))

    def test_multi_pack_persistence_exact_replay_idempotence_and_quote(self):
        cid,bid=self._setup_bundle()
        repo=self._repo()
        first=repo.prepare(self.pid,bid,MULTI)
        second=repo.prepare(self.pid,bid,MULTI)
        self.assertEqual(first["status"],"fits_multi")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["plan_id"],second["plan_id"])
        self.assertEqual(first["plan_sha256"],second["plan_sha256"])
        self.assertGreater(first["pack_count"],1)
        self.assertEqual(first["provider_calls"],0)
        result=repo.materialize(self.pid,first["plan_id"])
        self.assertTrue(verify_multi_pack_plan(
            KnowledgeInputRepository(connection_factory=lambda:self.psycopg.connect(DSN)).get(
                self.pid,bid)["bundle"],result["plan"],result["parts"]))
        self.assertEqual(result["plan_sha256"],first["plan_sha256"])
        self.assertEqual(len(result["parts"]),first["pack_count"])
        self.assertTrue(result["plan"]["coverage"]["every_fragment_exactly_once"])
        self.assertEqual(result["plan"]["coverage"]["uncovered_required"],0)
        self.assertGreater(Decimal(result["plan"]["quote"]["reserved_usd"]),0)
        self.assertTrue(result["plan"]["quote"]["requires_new_cost_consent"])
        self.assertNotIn("Исследование продукта 🌍",str(result["plan"]))
        self.assertEqual(result["tokenizer_check"],"pending_exact_provider_preflight")

    def test_sql_immutable_rows_and_foreign_project(self):
        _cid,bid=self._setup_bundle()
        repo=self._repo()
        item=repo.prepare(self.pid,bid,MULTI)
        ident=item["plan_id"]
        for table in ("knowledge_multi_pack_plans","knowledge_multi_pack_parts",
                      "knowledge_multi_pack_spans"):
            with self.assertRaises(RaiseException):
                with self.psycopg.connect(DSN) as conn:
                    where="id=%s" if table.endswith("_plans") else "plan_id=%s"
                    conn.execute(
                        f"DELETE FROM {table} WHERE project_id=%s AND {where}",
                        (self.pid,ident),
                    )
        with self.assertRaises(IntakeError) as caught:
            repo.materialize(self.other,ident)
        self.assertEqual(caught.exception.code,"multi_pack_not_found")
        with self.psycopg.connect(DSN) as conn:
            count=conn.execute(
                """SELECT COUNT(*) FROM knowledge_multi_pack_spans
                   WHERE project_id=%s AND plan_id=%s""",
                (self.pid,ident),
            ).fetchone()[0]
        self.assertEqual(count,item["span_count"])

    def test_over_budget_never_publishes_a_plan(self):
        _cid,bid=self._setup_bundle()
        repo=self._repo()
        refused=repo.prepare(self.pid,bid,{**MULTI,"budget_cap_usd":"0.00000001"})
        self.assertEqual(refused["status"],"over_budget")
        self.assertEqual(refused["provider_calls"],0)
        with self.psycopg.connect(DSN) as conn:
            count=conn.execute(
                "SELECT COUNT(*) FROM knowledge_multi_pack_plans WHERE project_id=%s",
                (self.pid,),
            ).fetchone()[0]
        self.assertEqual(count,0)

    def test_small_context_uses_stage04_not_a_duplicate_multi_plan(self):
        _cid,bid=self._setup_bundle(large=False)
        # The 16k context actually requires two packs because JSONL and
        # citation metadata count against the budget, even for short text.
        # Observe a fresh real catalog snapshot with a larger context.
        self._catalog(context_length=100000)
        repo=self._repo()
        decision=repo.prepare(self.pid,bid,MULTI)
        self.assertEqual(decision["status"],"use_full_pack")
        with self.psycopg.connect(DSN) as conn:
            count=conn.execute(
                """SELECT COUNT(*) FROM knowledge_multi_pack_plans
                   WHERE project_id=%s""",(self.pid,),
            ).fetchone()[0]
        self.assertEqual(count,0)

    def test_historic_replay_after_profile_revision(self):
        _cid,bid=self._setup_bundle()
        repo=self._repo()
        result=repo.prepare(self.pid,bid,MULTI)
        self.assertEqual(result["status"],"fits_multi")
        self._profile(revision=3)
        with self.assertRaises(IntakeError) as ctx:
            repo.prepare(self.pid,bid,MULTI)
        self.assertEqual(ctx.exception.code,"stale_snapshot")
        historical=repo.materialize(self.pid,result["plan_id"])
        self.assertEqual(historical["plan_sha256"],result["plan_sha256"])

    def test_video_analysis_stays_out_of_mimo_packs(self):
        _cid,bid=self._setup_bundle(video=True)
        repo=self._repo()
        decision=repo.prepare(self.pid,bid,MULTI)
        self.assertEqual(decision["status"],"blocked")
        self.assertEqual(decision["reason"],"partial_requires_explicit_acknowledgment")
        prepared=repo.prepare(self.pid,bid,MULTI,allow_partial=True)
        self.assertEqual(prepared["status"],"fits_multi")
        result=repo.materialize(self.pid,prepared["plan_id"])
        self.assertEqual(result["plan"]["video_refs_excluded_count"],1)
        for _manifest,payload in result["parts"]:
            self.assertNotIn(b"video-intelligence/v1",payload)
            self.assertNotIn(b"must never reach MiMo",payload)


if __name__=="__main__":
    unittest.main()
