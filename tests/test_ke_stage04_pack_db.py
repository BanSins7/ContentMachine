"""Stage04 Full Context Pack with genuine PostgreSQL17 + immutable catalog.

All artifacts and model catalog rows synthetic; NO external API or paid call.
"""
from __future__ import annotations
import json
import unittest
from uuid import uuid4

from psycopg.errors import RaiseException
from psycopg.types.json import Jsonb
from unittest.mock import patch

from test_ke_stage02_intake_db import ReadOnlyIntakePG, DSN
from intelligence.knowledge_extraction.full_pack_repository import FullContextPackRepository
from intelligence.knowledge_extraction.intake import IntakeError
from intelligence.knowledge_extraction.full_pack import verify_pack_payload
from test_ke_stage04_full_pack import CFG

@unittest.skipUnless(DSN, "PostgreSQL17 required for Stage04")
class Stage04FullContextPG(ReadOnlyIntakePG):
    def setUp(self):
        super().setUp()
        self._setup_snapshot()

    def _setup_snapshot(self):
        model="xiaomi/mimo-v2.6-flash"
        digest=(uuid4().hex+uuid4().hex)
        cap={"model_id":model,"context_length":100000,
             "max_output_tokens":4096,"supports_structured_outputs":True,
             "text_output":True}
        with self.psycopg.connect(DSN) as conn:
            snap=conn.execute("""INSERT INTO tb_model_snapshots
                (model_id,catalog_sha256,capabilities,pricing)
                VALUES(%s,%s,%s,%s) RETURNING id""",
                (model,digest,Jsonb(cap),
                 Jsonb({"prompt":"0.0000001","completion":"0.0000005"}))).fetchone()[0]
            refresh=conn.execute("""INSERT INTO tb_catalog_refreshes(models_present)
                 VALUES(%s) RETURNING id""",(Jsonb([model]),)).fetchone()[0]
            conn.execute("""INSERT INTO tb_catalog_refresh_models
                (refresh_id,model_id,snapshot_id) VALUES(%s,%s,%s)""",
                (refresh,model,snap))
        self.snapshot_id=snap

    def packs(self):
        return FullContextPackRepository(connection_factory=lambda:self.psycopg.connect(DSN))

    def prepare(self):
        context_id=self.approve()
        from intelligence.knowledge_extraction.repository import KnowledgeInputRepository
        bundle=KnowledgeInputRepository(connection_factory=lambda:self.psycopg.connect(DSN)).create(self.pid,context_id)
        return context_id,bundle["bundle_id"]

    def test_real_single_pack_idempotence_replay_full_payload_and_evidence_ids(self):
        cid,bid=self.prepare()
        repo=self.packs()
        first=repo.prepare(self.pid,bid,CFG)
        second=repo.prepare(self.pid,bid,CFG)
        self.assertEqual(first["status"],"fits")
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["pack_id"],second["pack_id"])
        self.assertEqual(first["payload_sha256"],second["payload_sha256"])
        self.assertEqual(first["fragment_count"],4)
        data=repo.materialize(self.pid,first["pack_id"])
        self.assertTrue(verify_pack_payload(data["manifest"],data["payload"]))
        chunks=[json.loads(x) for x in data["payload"].decode("utf-8").splitlines()]
        self.assertEqual(chunks[0]["schema_version"],"evidence-pack/v1")
        doc=next(x for x in chunks if x.get("origin_kind")=="document_segment")
        self.assertEqual(doc["text"],self.quote)
        self.assertEqual(doc["evidence_ids"],[self.eid])
        self.assertEqual(doc["source_role"],"other")
        self.assertEqual(doc["source_version_id"],self.vid)
        self.assertEqual(first["provider_calls"],0)
        self.assertEqual(first["tokenizer_check"],"pending_exact_provider_preflight")

    def test_immutable_pack_and_cross_project_access(self):
        cid,bid=self.prepare()
        repo=self.packs()
        saved=repo.prepare(self.pid,bid,CFG)
        with self.assertRaises(RaiseException):
            with self.psycopg.connect(DSN) as conn:
                conn.execute("""UPDATE knowledge_full_context_packs SET payload_sha256=%s
                  WHERE project_id=%s AND id=%s""",("a"*64,self.pid,saved["pack_id"]))
        with self.assertRaises(RaiseException):
            with self.psycopg.connect(DSN) as conn:
                conn.execute("""DELETE FROM knowledge_full_context_packs
                  WHERE project_id=%s AND id=%s""",(self.pid,saved["pack_id"]))
        with self.assertRaises(IntakeError) as exc:
            repo.materialize(self.other,saved["pack_id"])
        self.assertEqual(exc.exception.code,"full_pack_not_found")

    def test_stage04_rejects_blocked_or_unacknowledged_partial_and_preserves_video_refs(self):
        with self.psycopg.connect(DSN) as conn:
            self.make_source(conn,str(uuid4()),video=True)
        cid,bid=self.prepare()
        repo=self.packs()
        blocked=repo.prepare(self.pid,bid,CFG)
        self.assertEqual(blocked["status"],"blocked")
        self.assertEqual(blocked["reason"],"partial_requires_explicit_acknowledgment")
        allowed=repo.prepare(self.pid,bid,CFG,allow_partial=True)
        self.assertEqual(allowed["status"],"fits")
        pack=repo.materialize(self.pid,allowed["pack_id"])
        self.assertEqual(pack["manifest"]["excluded_source_count"],1)
        self.assertEqual(pack["manifest"]["video_refs_excluded_count"],1)
        self.assertNotIn(b"video-intelligence/v1",pack["payload"])
        self.assertNotIn(b"must never reach MiMo",pack["payload"])

    def test_reject_model_catalog_absence_or_unverified(self):
        _,bid=self.prepare()
        repo=self.packs()
        from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
        with patch("intelligence.knowledge_extraction.full_pack_repository.latest_verified",
                   side_effect=PipelineError("not verified")):
            with self.assertRaises(IntakeError) as caught:
                repo.prepare(self.pid,bid,CFG)
        self.assertEqual(caught.exception.code,"model_catalog_not_verified")

    def test_model_window_overflow_returns_multi_without_persist(self):
        _,bid=self.prepare()
        repo=self.packs()
        too_small={**CFG, "instruction_reserve_tokens":100000}
        result=repo.prepare(self.pid,bid,too_small)
        self.assertEqual(result["status"],"requires_multi_pack")
        with self.psycopg.connect(DSN) as conn:
            count=conn.execute("""SELECT count(*) FROM knowledge_full_context_packs
                 WHERE project_id=%s AND bundle_id=%s""",(self.pid,bid)).fetchone()[0]
        self.assertEqual(count,0)

    def test_profile_or_source_revision_blocks_new_pack_but_prior_pack_replays(self):
        cid,bid=self.prepare()
        repo=self.packs()
        saved=repo.prepare(self.pid,bid,CFG)
        with self.psycopg.connect(DSN) as conn:
            conn.execute("""INSERT INTO project_profile_versions
                (project_id,revision,schema_version,profile_hash,profile)
                VALUES(%s,2,1,%s,%s)""",
                (self.pid,"f"*64,Jsonb({"goal":"new"})))
            conn.execute("""UPDATE project_registry
                 SET profile_revision=2,profile_hash=%s,updated_at=clock_timestamp()
                 WHERE id=%s""",("f"*64,self.pid))
        with self.assertRaises(IntakeError) as caught:
            repo.prepare(self.pid,bid,CFG)
        self.assertEqual(caught.exception.code,"stale_snapshot")
        replay=repo.materialize(self.pid,saved["pack_id"])
        self.assertIn(self.quote.encode("utf-8"),replay["payload"])

    def test_invalid_payload_override_not_possible_through_db(self):
        _,bid=self.prepare()
        repo=self.packs()
        saved=repo.prepare(self.pid,bid,CFG)
        with self.psycopg.connect(DSN) as conn:
            row=conn.execute("""SELECT manifest_sha256,payload_sha256,fragment_count,
                       payload_bytes FROM knowledge_full_context_packs
                       WHERE project_id=%s AND id=%s""",(self.pid,saved["pack_id"])).fetchone()
        self.assertEqual(row[0],saved["manifest_sha256"])
        self.assertEqual(row[1],saved["payload_sha256"])
        self.assertEqual(row[2],saved["fragment_count"])
        self.assertEqual(row[3],saved["payload_bytes"])

if __name__ == "__main__":
    unittest.main()
