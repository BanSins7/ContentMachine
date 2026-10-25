"""B Stage03 real PostgreSQL17 contract/integrity checks.

Uses Stage02's existing synthetic project fixtures, with no external providers.
"""
from __future__ import annotations
import hashlib
import unittest
from uuid import uuid4

from test_ke_stage02_intake_db import ReadOnlyIntakePG, DSN
from intelligence.knowledge_extraction.repository import KnowledgeInputRepository
from intelligence.knowledge_extraction.intake import IntakeError

@unittest.skipUnless(DSN,"Requires PostgreSQL17 CI")
class Stage03BundlePG(ReadOnlyIntakePG):
    def bundle_repo(self):
        return KnowledgeInputRepository(connection_factory=lambda:self.psycopg.connect(DSN))

    def test_bundle_persist_idempotence_hash_and_resolve_all_text(self):
        cid=self.approve()
        repo=self.bundle_repo()
        first=repo.create(self.pid,cid)
        second=repo.create(self.pid,cid)
        self.assertTrue(first["created"])
        self.assertFalse(second["created"])
        self.assertEqual(first["bundle_id"],second["bundle_id"])
        self.assertEqual(first["bundle_sha256"],second["bundle_sha256"])
        self.assertEqual(first["readiness"],"ready")
        stored=repo.get(self.pid,first["bundle_id"])
        bundle=stored["bundle"]
        self.assertEqual(bundle["coverage"]["text_outputs_indexed"],1)
        self.assertEqual(bundle["coverage"]["fragments_indexed"],4)  # document + three A profile fields
        self.assertEqual(bundle["coverage"]["profile_fragments"],3)
        self.assertEqual(bundle["provider_calls"],0)
        self.assertNotIn(self.quote,str(bundle))
        self.assertNotIn("Synthetic",str(bundle))
        self.assertEqual(len(bundle["video_analysis_refs"]),0)
        for f in bundle["fragments"]:
            resolved=repo.resolve_fragment(self.pid,first["bundle_id"],f["fragment_id"])
            self.assertEqual(hashlib.sha256(resolved["text"].encode()).hexdigest(),f["text_sha256"])
            self.assertEqual(resolved["source_role"],f["source_role"])
        document=next(f for f in bundle["fragments"] if f["origin_kind"]=="document_segment")
        self.assertEqual(repo.resolve_fragment(self.pid,first["bundle_id"],document["fragment_id"])["text"],self.quote)

    def test_immutable_sql_rows_and_cross_project_refs(self):
        cid=self.approve();repo=self.bundle_repo()
        bid=repo.create(self.pid,cid)["bundle_id"]
        from psycopg.errors import RaiseException,ForeignKeyViolation
        with self.assertRaises(RaiseException):
            with self.psycopg.connect(DSN) as c:
                c.execute("UPDATE knowledge_input_bundles SET readiness='blocked' WHERE project_id=%s AND id=%s",(self.pid,bid))
        with self.assertRaises(RaiseException):
            with self.psycopg.connect(DSN) as c:
                c.execute("DELETE FROM knowledge_input_fragments WHERE project_id=%s AND bundle_id=%s",(self.pid,bid))
        with self.assertRaises(ForeignKeyViolation):
            with self.psycopg.connect(DSN) as c:
                c.execute("""INSERT INTO knowledge_input_video_refs
                  (project_id,bundle_id,context_id,source_id,source_version_id,
                   output_id,output_sha256,schema_version)
                  VALUES(%s,%s,%s,%s,%s,%s,%s,'video-intelligence/v1')""",
                  (self.other,bid,cid,self.sid,self.vid,self.oid,"a"*64))
        self.assertEqual(repo.get(self.pid,bid)["bundle"]["readiness"],"ready")

    def test_routing_owner_confirmation_and_frozen_config_hash(self):
        cid=self.approve();repo=self.bundle_repo()
        first=repo.create(self.pid,cid)
        routes={self.sid:{"role":"own_product","subject_id":None,"confirmed_by":"local_owner"}}
        second=repo.create(self.pid,cid,source_routes=routes)
        self.assertNotEqual(first["bundle_id"],second["bundle_id"])
        old=repo.get(self.pid,first["bundle_id"])["bundle"]
        new=repo.get(self.pid,second["bundle_id"])["bundle"]
        one=next(f for f in old["fragments"] if f["origin_kind"]=="document_segment")
        two=next(f for f in new["fragments"] if f["origin_kind"]=="document_segment")
        self.assertEqual(one["source_role"],"other")
        self.assertEqual(two["source_role"],"own_product")
        self.assertEqual(two["subject_id"],self.pid)
        self.assertNotEqual(one["fragment_id"],two["fragment_id"])
        self.assertFalse(repo.create(self.pid,cid,source_routes=routes)["created"])

    def test_foreign_project_get_and_fragment_rejected(self):
        cid=self.approve();repo=self.bundle_repo()
        result=repo.create(self.pid,cid)
        bid=result["bundle_id"]
        with self.assertRaises(IntakeError) as error:
            repo.get(self.other,bid)
        self.assertEqual(error.exception.code,"bundle_not_found")
        with self.assertRaises(IntakeError):
            repo.resolve_fragment(self.other,bid,"a"*64)
        with self.assertRaises(IntakeError) as error:
            repo.resolve_fragment(self.pid,bid,"a"*64)
        self.assertEqual(error.exception.code,"fragment_not_found")

    def test_new_profile_revision_blocks_new_bundle_but_existing_is_historical(self):
        cid=self.approve();repo=self.bundle_repo()
        bid=repo.create(self.pid,cid)["bundle_id"]
        with self.psycopg.connect(DSN) as conn:
            conn.execute("""INSERT INTO project_profile_versions
                (project_id,revision,schema_version,profile_hash,profile)
                VALUES(%s,2,1,%s,%s)""",
                (self.pid,"c"*64,self.Jsonb({"goal":"new goal"})))
            conn.execute("""UPDATE project_registry SET profile_revision=2,profile_hash=%s,
                            updated_at=clock_timestamp() WHERE id=%s""",("c"*64,self.pid))
        with self.assertRaises(IntakeError) as exc:repo.create(self.pid,cid)
        self.assertEqual(exc.exception.code,"stale_snapshot")
        self.assertEqual(repo.get(self.pid,bid)["bundle"]["context_id"],cid)
        f=repo.get(self.pid,bid)["bundle"]["fragments"][0]
        self.assertTrue(repo.resolve_fragment(self.pid,bid,f["fragment_id"])["text"])

    def test_real_video_pin_persistence_without_b3_text(self):
        with self.psycopg.connect(DSN) as conn:
            vid_src=str(uuid4())
            self.make_source(conn,vid_src,video=True)
        cid=self.approve();repo=self.bundle_repo()
        bid=repo.create(self.pid,cid)["bundle_id"]
        bundle=repo.get(self.pid,bid)["bundle"]
        self.assertEqual(len(bundle["video_analysis_refs"]),1)
        self.assertEqual(bundle["coverage"]["video_refs"],1)
        self.assertEqual(bundle["approval_decision"],"approved_partial")
        self.assertEqual(bundle["readiness"],"approved_partial")
        self.assertTrue(all(f["origin_kind"]!="video_analysis" for f in bundle["fragments"]))
        self.assertNotIn("must never reach MiMo",str(bundle))

if __name__=="__main__":unittest.main()
