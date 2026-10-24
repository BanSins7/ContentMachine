"""Stage 12 PostgreSQL 17 verification against actual Stage 11 fixtures."""
import os
import unittest
from uuid import uuid4

from test_context_db import ContextDB
from intelligence.taxonomy_builder.context_binding import context_material,MODE
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

DSN=os.environ.get("CM_TEST_PG_DSN")

@unittest.skipUnless(DSN,"PostgreSQL 17 CI service is required")
class ContextRTBDatabase(ContextDB):
    def test_approved_grounded_context_selected_and_stale_profile_refused(self):
        import psycopg
        from psycopg.types.json import Jsonb
        with psycopg.connect(DSN) as c:
            c.execute("""INSERT INTO project_evidence_reviews(project_id,evidence_id,decision,reviewed_by)
                 VALUES(%s,%s,'approved','local_owner')""",(self.pid,self.eid))
        draft=self.s.preview(self.pid)
        cid=self.s.create_snapshot(self.pid,{"expected_context_sha256":draft["context_sha256"]})["context_id"]
        approved=self.s.decide(self.pid,cid,{"decision":"approved",
            "expected_context_sha256":draft["context_sha256"],
            "excluded_source_ids":[],"acknowledge_partial":False})
        with psycopg.connect(DSN) as c:
            selected=context_material(c,self.pid,cid,500)
            self.assertEqual(selected["binding"]["mode"],MODE)
            self.assertEqual(selected["binding"]["approval_id"],approved["approval_id"])
            self.assertEqual(selected["binding"]["selected_evidence_ids"],[self.eid])
            self.assertEqual(selected["selection"][0]["allowed_fact_kinds"],["problem","audience"])
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with c.transaction():
                    c.execute("""INSERT INTO tb_context_quote_bindings
                      (project_id,quote_id,context_id,approval_id,context_sha256,selection_sha256,token_budget)
                      VALUES(%s,%s,%s,%s,%s,%s,500)""",
                      (self.pid,str(uuid4()),cid,approved["approval_id"],
                       draft["context_sha256"],selected["binding"]["selection_sha256"]))
        with psycopg.connect(DSN) as c:
            c.execute("""INSERT INTO project_profile_versions
               (project_id,revision,schema_version,profile_hash,profile)
               VALUES(%s,2,1,%s,%s)""",(self.pid,"b"*64,Jsonb({"goal":"changed"})))
            c.execute("""UPDATE project_registry SET profile_revision=2,profile_hash=%s,
                updated_at=clock_timestamp() WHERE id=%s""",("b"*64,self.pid))
        with psycopg.connect(DSN) as c:
            with self.assertRaises(PipelineError):
                context_material(c,self.pid,cid,500)
            historical=context_material(c,self.pid,cid,500,require_current=False)
            self.assertEqual(historical["binding"]["selected_evidence_ids"],[self.eid])

if __name__=="__main__":unittest.main()
