"""B Stage06: true PostgreSQL17 immutable unsent facts-v3 request batches."""
from __future__ import annotations
from uuid import uuid4
import json
import unittest
from decimal import Decimal

from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb

from test_ke_stage02_intake_db import ReadOnlyIntakePG, DSN
from test_ke_stage04_full_pack import CFG
from test_ke_stage05_multi_pack import MULTI
from intelligence.knowledge_extraction.repository import KnowledgeInputRepository
from intelligence.knowledge_extraction.full_pack_repository import FullContextPackRepository
from intelligence.knowledge_extraction.multi_pack_repository import MultiPackRepository
from intelligence.knowledge_extraction.fact_request_repository import FactRequestRepository
from intelligence.knowledge_extraction.intake import IntakeError


@unittest.skipUnless(DSN,"PostgreSQL17 required for Stage06 facts-v3 request integration")
class Stage06RequestPG(ReadOnlyIntakePG):
    def setUp(self):
        super().setUp()
        self._catalog(window=100000)

    def _catalog(self, *, window):
        model="xiaomi/mimo-v2.6-flash"
        with self.psycopg.connect(DSN) as c:
            sid=c.execute(
                """INSERT INTO tb_model_snapshots(model_id,catalog_sha256,capabilities,pricing)
                   VALUES(%s,%s,%s,%s) RETURNING id""",
                (model,uuid4().hex+uuid4().hex,
                 Jsonb({"model_id":model,"context_length":window,
                        "max_output_tokens":4096,"text_output":True,
                        "supports_structured_outputs":True,
                        "supported_parameters":["structured_outputs"]}),
                 Jsonb({"prompt":"0.0000002","completion":"0.000001",
                        "internal_reasoning":"0.0000005","request":"0.0001"})),
            ).fetchone()[0]
            rid=c.execute(
                "INSERT INTO tb_catalog_refreshes(models_present) VALUES(%s) RETURNING id",
                (Jsonb([model]),),
            ).fetchone()[0]
            c.execute(
                "INSERT INTO tb_catalog_refresh_models(refresh_id,model_id,snapshot_id) VALUES(%s,%s,%s)",
                (rid,model,sid),
            )

    def _factory(self):
        return lambda:self.psycopg.connect(DSN)

    def _bundle(self, *, large=False):
        if large:
            value=("Исследование продукта 🌍 "*600)[:10000]
            with self.psycopg.connect(DSN) as c:
                c.execute(
                    """INSERT INTO project_profile_versions
                       (project_id,revision,schema_version,profile_hash,profile)
                       VALUES(%s,2,1,%s,%s)""",
                    (self.pid,"2"*64,Jsonb({
                        "niche":"Technology","goal":"Learn",
                        "product_description":value})),
                )
                c.execute(
                    """UPDATE project_registry SET profile_revision=2,profile_hash=%s,
                       updated_at=clock_timestamp() WHERE id=%s""",
                    ("2"*64,self.pid),
                )
        cid=self.approve()
        result=KnowledgeInputRepository(
            connection_factory=self._factory()).create(self.pid,cid)
        return result["bundle_id"]

    def _repo(self):
        return FactRequestRepository(connection_factory=self._factory())

    def test_full_pack_batch_persist_idempotent_request_and_offline_draft(self):
        bundle=self._bundle()
        single=FullContextPackRepository(connection_factory=self._factory()).prepare(
            self.pid,bundle,CFG)
        self.assertEqual(single["status"],"fits")
        repo=self._repo()
        first=repo.prepare(self.pid,full_pack_id=single["pack_id"])
        second=repo.prepare(self.pid,full_pack_id=single["pack_id"])
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["batch_id"],second["batch_id"])
        self.assertEqual(first["batch_sha256"],second["batch_sha256"])
        self.assertEqual(first["request_count"],1)
        self.assertGreater(Decimal(first["indicative_reservation_usd"]),0)
        self.assertEqual(first["provider_calls"],0)
        self.assertEqual(first["execution_status"],"not_dispatched")
        replay=repo.materialize(self.pid,first["batch_id"])
        self.assertEqual(replay["batch_sha256"],first["batch_sha256"])
        self.assertEqual(replay["blueprint"]["request_count"],1)
        self.assertEqual(replay["parts"][0]["request"]["model"],"xiaomi/mimo-v2.6-flash")
        self.assertTrue(replay["parts"][0]["requires_new_cost_consent"])
        rows=[json.loads(s) for s in replay["source_packs"][0][1].decode().splitlines()]
        src=rows[1]
        excerpt=src["text"][:18]
        response={"facts":[{"kind":"external_claim","text":"Extracted claim",
                            "source_chunk_id":src["fragment_id"],
                            "source_quote":excerpt,
                            "quote_start":0,"quote_end":len(excerpt)}]}
        draft=repo.inspect_response(self.pid,first["batch_id"],0,response)
        self.assertEqual(draft["candidate_count"],1)
        self.assertEqual(draft["candidates"][0]["source_id"],src["source_id"])
        self.assertEqual(draft["candidates"][0]["grounding_status"],"pending_b5")

    def test_multi_pack_requests_materialize_all_and_keep_video_out(self):
        bundle=self._bundle(large=True)
        self._catalog(window=16000)
        multi=MultiPackRepository(connection_factory=self._factory()).prepare(
            self.pid,bundle,MULTI)
        self.assertEqual(multi["status"],"fits_multi")
        repo=self._repo()
        created=repo.prepare(self.pid,multi_plan_id=multi["plan_id"])
        self.assertTrue(created["created"])
        self.assertGreater(created["request_count"],1)
        replay=repo.materialize(self.pid,created["batch_id"])
        self.assertEqual(len(replay["parts"]),multi["pack_count"])
        self.assertEqual(len(replay["blueprint"]["parts"]),multi["pack_count"])
        for req in replay["parts"]:
            self.assertEqual(req["request"]["response_format"]["type"],"json_schema")
            self.assertNotIn("video-intelligence/v1",str(req["request"]))
            self.assertEqual(req["provider_calls"],0)
        self.assertFalse(repo.prepare(self.pid,multi_plan_id=multi["plan_id"])["created"])

    def test_foreign_project_and_invalid_pack_selection(self):
        bundle=self._bundle()
        single=FullContextPackRepository(connection_factory=self._factory()).prepare(
            self.pid,bundle,CFG)
        repo=self._repo()
        first=repo.prepare(self.pid,full_pack_id=single["pack_id"])
        with self.assertRaises(IntakeError) as caught:
            repo.materialize(self.other,first["batch_id"])
        self.assertEqual(caught.exception.code,"facts_request_batch_not_found")
        for combo in ({}, {"full_pack_id":single["pack_id"],
                          "multi_plan_id":str(uuid4())}):
            with self.assertRaises(IntakeError):
                repo.prepare(self.pid,**combo)
        with self.assertRaises(IntakeError):
            repo.prepare(self.other,full_pack_id=single["pack_id"])

    def test_immutable_sql_blueprint_and_request_parts(self):
        bundle=self._bundle()
        single=FullContextPackRepository(connection_factory=self._factory()).prepare(
            self.pid,bundle,CFG)
        repo=self._repo()
        ident=repo.prepare(self.pid,full_pack_id=single["pack_id"])["batch_id"]
        with self.assertRaises(RaiseException):
            with self.psycopg.connect(DSN) as c:
                c.execute(
                    """UPDATE knowledge_fact_request_batches SET batch_sha256=%s
                       WHERE project_id=%s AND id=%s""",
                    ("0"*64,self.pid,ident),
                )
        with self.assertRaises(RaiseException):
            with self.psycopg.connect(DSN) as c:
                c.execute(
                    """DELETE FROM knowledge_fact_request_parts
                       WHERE project_id=%s AND batch_id=%s""",
                    (self.pid,ident),
                )
        with self.psycopg.connect(DSN) as c:
            record=c.execute(
                """SELECT batch::text FROM knowledge_fact_request_batches
                   WHERE project_id=%s AND id=%s""",
                (self.pid,ident),
            ).fetchone()[0]
        self.assertNotIn(self.quote,record)
        self.assertNotIn("OPENROUTER_API_KEY",record)
        self.assertIn("not_dispatched",record)

    def test_stale_profile_blocks_new_request_batch_but_replay_survives(self):
        bundle=self._bundle()
        single=FullContextPackRepository(connection_factory=self._factory()).prepare(
            self.pid,bundle,CFG)
        repo=self._repo()
        created=repo.prepare(self.pid,full_pack_id=single["pack_id"])
        with self.psycopg.connect(DSN) as c:
            c.execute(
                """INSERT INTO project_profile_versions(
                     project_id,revision,schema_version,profile_hash,profile)
                   VALUES(%s,2,1,%s,%s)""",
                (self.pid,"a"*64,Jsonb({"goal":"new"})),
            )
            c.execute(
                """UPDATE project_registry SET profile_revision=2,profile_hash=%s,
                       updated_at=clock_timestamp() WHERE id=%s""",
                ("a"*64,self.pid),
            )
        with self.assertRaises(IntakeError) as caught:
            repo.prepare(self.pid,full_pack_id=single["pack_id"])
        self.assertEqual(caught.exception.code,"stale_snapshot")
        old=repo.materialize(self.pid,created["batch_id"])
        self.assertEqual(old["batch_sha256"],created["batch_sha256"])

    def test_prepared_batch_is_never_paid_execution_and_rejects_wrong_part(self):
        bundle=self._bundle()
        single=FullContextPackRepository(connection_factory=self._factory()).prepare(
            self.pid,bundle,CFG)
        repo=self._repo()
        result=repo.prepare(self.pid,full_pack_id=single["pack_id"])
        with self.assertRaises(IntakeError) as caught:
            repo.inspect_response(self.pid,result["batch_id"],1,{"facts":[]})
        self.assertEqual(caught.exception.code,"facts_part_index_invalid")
        with self.psycopg.connect(DSN) as c:
            self.assertEqual(c.execute(
                """SELECT COUNT(*) FROM knowledge_fact_request_batches
                   WHERE project_id=%s""",(self.pid,),
            ).fetchone()[0],1)


if __name__=="__main__":
    unittest.main()
