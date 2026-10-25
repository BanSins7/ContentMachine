"""B Stage02 true PostgreSQL17 read-only A source resolver integration.

Synthetic assets only. No network, provider, OCR, ASR, video downloads or paid calls.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from uuid import uuid4

from intelligence.project_sources import context_service as a
from intelligence.project_sources.context_builder import sha
from intelligence.knowledge_extraction.contracts import pinned_video_analyses
from intelligence.knowledge_extraction.intake import PostgresAResolver, IntakeError

DSN = os.environ.get("CM_TEST_PG_DSN")


@unittest.skipUnless(DSN, "PostgreSQL17 is required for Stage02 integration")
class ReadOnlyIntakePG(unittest.TestCase):
    def setUp(self):
        import psycopg
        from psycopg.types.json import Jsonb

        self.psycopg = psycopg
        self.Jsonb = Jsonb
        self.pid = uuid4().hex
        self.other = uuid4().hex
        self.sid = str(uuid4())
        self.quote = "A measurable product capability."
        self.text_bytes = self.quote.encode("utf-8")
        self.content_sha = hashlib.sha256(self.text_bytes).hexdigest()
        self.locator = {"kind": "document", "start_char": 0, "end_char": len(self.quote)}
        self.doc = {
            "schema_version": "document-segments/v1",
            "parser_version": "local/1", "status": "completed",
            "segments": [{"text": self.quote, "locator": self.locator, "role": "text"}],
            "warnings": [],
        }
        self.profile_hash = "a" * 64
        self.patches = [
            patch.object(a, "_pid", side_effect=lambda pid: pid),
            patch.object(a, "_conn", side_effect=lambda: self.psycopg.connect(DSN)),
        ]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)
        self.resolver = PostgresAResolver(connection_factory=lambda: self.psycopg.connect(DSN))
        with self.psycopg.connect(DSN) as conn:
            for pid in (self.pid, self.other):
                conn.execute(
                    """INSERT INTO project_registry
                       (id,name,profile_revision,profile_hash,created_at,updated_at)
                       VALUES(%s,'B Stage02',1,%s,now(),now())""",
                    (pid, self.profile_hash),
                )
            conn.execute(
                """INSERT INTO project_profile_versions
                   (project_id,revision,schema_version,profile_hash,profile)
                   VALUES(%s,1,1,%s,%s)""",
                (self.pid, self.profile_hash,
                 self.Jsonb({"niche": "Technology", "goal": "Learn", "product_description": "Synthetic"})),
            )
            self.vid, self.oid, self.eid = self.make_source(conn, self.sid, evidence=True)

    def make_source(self, conn, sid, *, evidence=False, video=False, registered_file=False):
        source_type = "user_video_reference" if video else "research_document"
        conn.execute(
            """INSERT INTO project_sources
               (project_id,id,source_type,original_input,identity_key,label)
               VALUES(%s,%s,%s,'synthetic',%s,'B1 fixture')""",
            (self.pid, sid, source_type, "stage02:" + sid),
        )
        digest = self.content_sha if not video else "b" * 64
        ref = None
        if registered_file:
            ref = f"research_library/{sid[:2]}/{sid}/v00001_{digest}.bin"
        version = conn.execute(
            """INSERT INTO project_source_versions
               (project_id,source_id,version_no,content_sha256,byte_size,media_type,
                source_locator,initial_extraction_status,immutable_asset_ref)
               VALUES(%s,%s,1,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (self.pid, sid, digest, len(self.text_bytes) if not video else 1000,
             "text/plain" if not video else "video/mp4",
             self.Jsonb({"kind": "local_file"} if registered_file else {"kind": "test"}),
             "completed", ref),
        ).fetchone()[0]
        conn.execute(
            "UPDATE project_sources SET active_version_id=%s WHERE project_id=%s AND id=%s",
            (version, self.pid, sid),
        )
        if registered_file:
            conn.execute(
                """INSERT INTO project_research_files
                   (id,project_id,source_id,display_name,uploaded_by,origin)
                   VALUES(%s,%s,%s,'fixture.txt','tester','upload')""",
                (sid, self.pid, sid),
            )
            asset = conn.execute(
                """INSERT INTO project_media_assets
                   (project_id,source_id,source_version_id,asset_kind,
                    content_sha256,byte_size,media_type,storage_ref)
                   VALUES(%s,%s,%s,'document',%s,%s,'text/plain',%s)
                   RETURNING id""",
                (self.pid, sid, version, digest, len(self.text_bytes), ref),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO project_research_file_versions
                   (project_id,file_id,source_version_id,version_no,content_sha256,
                    byte_size,mime_type,original_name,uploaded_by,
                    parser_eligibility,storage_ref,media_asset_id,origin)
                   VALUES(%s,%s,%s,1,%s,%s,'text/plain','fixture.txt','tester',
                          'unsupported',%s,%s,'upload')""",
                (self.pid, sid, version, digest, len(self.text_bytes), ref, asset),
            )
        data = (
            {"schema_version": "video-intelligence/v1", "processing_status": "completed",
             "project_id": self.pid, "source_id": sid,
             "video_source_version_id": str(version), "video_sha256": digest,
             "transcript": {"segments": [{"text": "must never reach MiMo"}]}}
            if video else self.doc
        )
        output = conn.execute(
            """INSERT INTO source_processing_outputs
              (project_id,source_id,source_version_id,output_type,
               schema_version,output,output_sha256)
              VALUES(%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (self.pid, sid, version, "video_analysis" if video else "document_segments",
             data["schema_version"], self.Jsonb(data), sha(data)),
        ).fetchone()[0]
        eid = None
        if evidence:
            eid = uuid4()
            qsha = hashlib.sha256(self.quote.encode("utf-8")).hexdigest()
            grp = conn.execute(
                """INSERT INTO project_evidence_exact_groups
                   (project_id,evidence_kind,knowledge_scope,normalized_quote_sha256)
                   VALUES(%s,'external_claim','general_research',%s) RETURNING id""",
                (self.pid, qsha),
            ).fetchone()[0]
            conn.execute(
                """INSERT INTO project_evidence
                   (id,project_id,source_id,source_version_id,processing_output_id,
                    evidence_kind,exact_text_or_observation,locator,extraction_engine,
                    extraction_version,quality_status,source_quote)
                   VALUES(%s,%s,%s,%s,%s,'external_claim',%s,%s,
                          'document-parser','1','complete',%s)""",
                (eid, self.pid, sid, version, output, self.quote,
                 self.Jsonb(self.locator), self.quote),
            )
            conn.execute(
                """INSERT INTO project_evidence_grounding
                   (project_id,evidence_id,source_id,source_version_id,input_output_id,
                    quote_sha256,normalized_quote_sha256,locator_sha256,input_sha256,
                    exact_group_id,knowledge_scope,trust_label,citation)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                          'general_research','local_document_extraction',%s)""",
                (self.pid, eid, sid, version, output, qsha, qsha,
                 sha(self.locator), sha(data), grp,
                 "doc:" + sid + ":page:1:stored-output"),
            )
        return str(version), str(output), str(eid) if eid else None

    def approve(self):
        preview = a.preview(self.pid)
        result = a.create_snapshot(
            self.pid, {"expected_context_sha256": preview["context_sha256"]}
        )
        missing = preview["context"]["unavailable_or_excluded_sources"]
        a.decide(self.pid, result["context_id"], {
            "decision": "approved_partial" if missing else "approved",
            "expected_context_sha256": preview["context_sha256"],
            "acknowledge_partial": bool(missing),
            "excluded_source_ids": [item["source_id"] for item in missing],
        })
        return result["context_id"]

    def code(self, callback, code):
        with self.assertRaises(IntakeError) as caught:
            callback()
        self.assertEqual(caught.exception.code, code)

    def test_happy_path_resolve_context_output_evidence_and_inspect(self):
        cid = self.approve()
        envelope = self.resolver.resolve_context(self.pid, cid)
        self.assertEqual(envelope["context_id"], cid)
        self.assertEqual(envelope["approval"]["decision"], "approved")
        self.assertEqual(envelope["context"]["profile"]["fields"]["goal"], "Learn")
        output = self.resolver.resolve_output(
            self.pid, cid, self.sid, self.vid, self.oid, sha(self.doc)
        )
        self.assertEqual(output, self.doc)
        ref = envelope["context"]["evidence_manifest"][0]
        evidence = self.resolver.resolve_evidence(
            self.pid, cid, self.eid, ref["exact_text_sha256"], ref["locator_sha256"]
        )
        self.assertEqual(evidence["exact_text"], self.quote)
        self.assertEqual(evidence["source_version_id"], self.vid)
        report = self.resolver.inspect(self.pid, cid)
        self.assertEqual(report["readiness"], "ready")
        self.assertEqual(report["text_output_refs"], 1)
        self.assertEqual(report["video_analysis_refs"], [])
        self.assertEqual(report["provider_calls"], 0)
        self.assertEqual(report["database_writes"], 0)
        self.assertFalse(report["verified"]["video_media_bytes"])
        self.assertNotIn(self.quote, str(report))

    def test_unapproved_context_is_not_intake_eligible(self):
        preview = a.preview(self.pid)
        cid = a.create_snapshot(self.pid, {"expected_context_sha256": preview["context_sha256"]})["context_id"]
        with self.assertRaises(Exception) as exc:
            self.resolver.resolve_context(self.pid, cid)
        self.assertEqual(getattr(exc.exception, "code", None), "context_not_approved")

    def test_foreign_project_snapshot_and_fake_pins_rejected(self):
        cid = self.approve()
        self.code(lambda: self.resolver.resolve_context(self.other, cid), "context_not_found")
        self.code(lambda: self.resolver.resolve_output(
            self.pid, cid, self.sid, self.vid, str(uuid4()), sha(self.doc)),
            "output_not_pinned")
        self.code(lambda: self.resolver.resolve_output(
            self.pid, cid, self.sid, self.vid, self.oid, "a" * 64),
            "output_not_pinned")
        self.code(lambda: self.resolver.resolve_evidence(
            self.pid, cid, str(uuid4()), "a" * 64, "a" * 64),
            "evidence_not_pinned")

    def test_bad_uuid_and_sha_rejected_before_query(self):
        cid = self.approve()
        self.code(lambda: self.resolver.resolve_context("../tmp", cid), "invalid_project_id")
        self.code(lambda: self.resolver.resolve_context(self.pid, "not-uuid"), "invalid_context_id")
        self.code(lambda: self.resolver.resolve_output(
            self.pid, cid, self.sid, self.vid, self.oid, "not-a-hash"),
            "invalid_sha256")

    def test_stale_profile_blocks_new_intake_but_history_is_readable(self):
        cid = self.approve()
        with self.psycopg.connect(DSN) as conn:
            conn.execute(
                """INSERT INTO project_profile_versions
                   (project_id,revision,schema_version,profile_hash,profile)
                   VALUES(%s,2,1,%s,%s)""",
                (self.pid, "b" * 64, self.Jsonb({"goal": "Updated"})),
            )
            conn.execute(
                """UPDATE project_registry SET profile_revision=2,profile_hash=%s,
                   updated_at=clock_timestamp() WHERE id=%s""",
                ("b" * 64, self.pid),
            )
        self.code(lambda: self.resolver.resolve_context(self.pid, cid), "stale_snapshot")
        historical = PostgresAResolver(
            connection_factory=lambda: self.psycopg.connect(DSN), require_current=False
        )
        self.assertEqual(historical.resolve_context(self.pid, cid)["context_id"], cid)

    def test_new_source_version_blocks_new_intake_but_old_pin_is_immutable(self):
        cid = self.approve()
        with self.psycopg.connect(DSN) as conn:
            ver = conn.execute(
                """INSERT INTO project_source_versions
                   (project_id,source_id,version_no,content_sha256,byte_size,media_type,
                    source_locator,initial_extraction_status)
                   VALUES(%s,%s,2,%s,40,'text/plain',%s,'completed') RETURNING id""",
                (self.pid, self.sid, "f" * 64, self.Jsonb({"kind": "revision"})),
            ).fetchone()[0]
            conn.execute(
                "UPDATE project_sources SET active_version_id=%s WHERE project_id=%s AND id=%s",
                (ver, self.pid, self.sid),
            )
        self.code(lambda: self.resolver.inspect(self.pid, cid), "stale_snapshot")
        historic = PostgresAResolver(
            connection_factory=lambda: self.psycopg.connect(DSN), require_current=False
        )
        self.assertEqual(historic.resolve_context(self.pid, cid)["context_id"], cid)

    def test_partial_sources_require_explicit_a_approval_and_remain_visible(self):
        with self.psycopg.connect(DSN) as conn:
            extra = str(uuid4())
            self.make_source(conn, extra, evidence=False)
        cid = self.approve()
        report = self.resolver.inspect(self.pid, cid)
        self.assertEqual(report["readiness"], "approved_partial")
        self.assertEqual(report["approval_decision"], "approved_partial")
        self.assertEqual(len(report["excluded_sources"]), 1)
        self.assertEqual(report["excluded_sources"][0]["source_id"], extra)

    def test_video_pins_resolved_without_fact_or_mimo_generation(self):
        with self.psycopg.connect(DSN) as conn:
            self.video_sid = str(uuid4())
            self.make_source(conn, self.video_sid, video=True)
        cid = self.approve()
        envelope = self.resolver.resolve_context(self.pid, cid)
        pin = pinned_video_analyses(envelope)[0]
        data = self.resolver.resolve_video(self.pid, cid, pin)
        self.assertEqual(data["schema_version"], "video-intelligence/v1")
        self.assertEqual(data["source_id"], self.video_sid)
        self.assertEqual(self.resolver.inspect(self.pid, cid)["video_analysis_refs"], [pin.output_id])
        self.assertFalse(self.resolver.inspect(self.pid, cid)["verified"]["video_media_bytes"])
        self.code(lambda: self.resolver.resolve_video(
            self.pid, cid, type(pin)(pin.source_id, pin.source_version_id,
                                     str(uuid4()), pin.schema_version, pin.output_sha256)),
            "video_not_pinned")

    def test_document_blob_sha_verified_and_modified_or_missing_file_blocked(self):
        with self.psycopg.connect(DSN) as conn:
            file_sid = str(uuid4())
            vid, oid, _ = self.make_source(conn, file_sid, registered_file=True)
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            ref = f"research_library/{file_sid[:2]}/{file_sid}/v00001_{self.content_sha}.bin"
            path = root / ref
            path.parent.mkdir(parents=True)
            path.write_bytes(self.text_bytes)
            from intelligence.project_sources import file_library
            with patch.object(file_library, "_project_folder", return_value=root):
                cid = self.approve()
                self.assertEqual(self.resolver.inspect(self.pid, cid)["readiness"], "approved_partial")
                path.write_bytes(b"X" * len(self.text_bytes))
                self.code(lambda: self.resolver.resolve_context(self.pid, cid), "research_file_integrity")
                path.unlink()
                self.code(lambda: self.resolver.resolve_context(self.pid, cid), "research_file_unavailable")

    def test_no_persistent_write_and_read_only_transaction_enforced(self):
        cid = self.approve()
        executed = []
        class Proxy:
            def __init__(self, raw):
                self.raw = raw
            def __enter__(self):
                self.raw.__enter__()
                return self
            def __exit__(self, *args):
                return self.raw.__exit__(*args)
            def execute(self, query, params=None):
                executed.append(query)
                return self.raw.execute(query, params)
            def cursor(self, *args, **kwargs):
                # A Stage11 _assemble uses psycopg cursor(row_factory=...).
                # PostgreSQL READ ONLY is still the source of write prevention.
                return self.raw.cursor(*args, **kwargs)

        readonly = PostgresAResolver(
            connection_factory=lambda: Proxy(self.psycopg.connect(DSN))
        )
        self.assertEqual(readonly.inspect(self.pid, cid)["readiness"], "ready")
        self.assertEqual(executed[0], "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        self.assertFalse(any(q.strip().upper().startswith(("INSERT", "UPDATE", "DELETE", "LOCK"))
                             for q in executed))


if __name__ == "__main__":
    unittest.main()
