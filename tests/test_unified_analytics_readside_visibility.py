"""UA-MOD-19 read visibility: no denied snapshot or aggregate side channel.

Synthetic future-positive peers in mocks are NOT actual approved data;
production UAE85/84 still forbids qualified snapshots. Real PostgreSQL
tests are separately conditional on an isolated CM_TEST_PG_DSN.
"""
from __future__ import annotations

from contextlib import contextmanager
import copy
from datetime import datetime,timezone,timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError,InputScope,canonical_hash
from intelligence.unified_analytics import readside_visibility as mod

PROJECT="a"*32
OTHER="c"*32
RUN="00112233-4455-4677-8899-aabbccddeeff"
NOW=datetime(2026,10,27,19,0,tzinfo=timezone.utc)
SHA="b"*64
PORTS=("source_policy","d1","qdp","sre","group_a","group_b","group_c")


class Cursor:
    def __init__(self,items): self.items=items
    def fetchone(self): return self.items[0] if self.items else None
    def fetchall(self): return self.items


class FakeCandidate:
    def __init__(self,scope):
        self.scope=scope
        self.fingerprint="c"*64
        self.rights=object()
        self.lineage=SimpleNamespace(
            sre_decision_sha256=SHA,sre_taxonomy_version_id=3)
        self.group_a=SimpleNamespace(analysis_id=201,result_sha256="d"*64)
        self.group_b=SimpleNamespace(b_analysis_id=301)
        self.group_c=SimpleNamespace(calculated_metric_id=401,
                                      current_snapshot_id=501,config_id=601)


class FakeSourceReader:
    deny=False
    digest="e"*64
    def __init__(self,db,rights,*,as_of):
        self.db=db
    def check_current_rights(self,scope):
        if self.db.rights_revoked:
            raise AnalyticsBoundaryError("source_policy_revoked_or_superseded",
                                         "source revoked")
        return SimpleNamespace(evidence_sha256=self.db.rights_sha)


class FakeAssembler:
    def __init__(self,db,scope,candidates,*,as_of):
        self.db,self.scope,self.candidates=db,scope,candidates
    def select(self):
        if self.db.peer_missing:
            return SimpleNamespace(selected=None)
        c=self.candidates[0]
        return SimpleNamespace(selected=SimpleNamespace(
            fingerprint=c.fingerprint,scope=self.scope,
            source_rights_sha256="e"*64,
            lineage_sha256="f"*64,binding_sha256="1"*64,
            evidence=tuple(SimpleNamespace(
                port=port,record_key=port+".record",
                producer_schema="producer/v1",producer_version="1",
                payload_sha256=SHA,lineage_sha256="2"*64
            ) for port in PORTS),
        ))


class FakeOrchestrator:
    def __init__(self,assembler): self.db=assembler.db
    def _from_selection(self,selected):
        return SimpleNamespace(qualified=self.db.peer_approved)


class FakeDB:
    def __init__(self,scope,candidate):
        self.info=SimpleNamespace(transaction_status=0)
        self.scope=scope
        self.candidate=candidate
        self.candidate_visible=True
        self.peer_approved=True
        self.peer_missing=False
        self.rights_revoked=False
        self.rights_sha="e"*64
        self.invalidated=False
        self.queries=[]
        self._configure_payload()

    def _configure_payload(self):
        s,c=self.scope,self.candidate
        self.evidence=[dict(
            port=port,state="complete",checked=True,is_current=True,
            record_key=port+".record",producer_schema="producer/v1",
            producer_version="1",evidence_sha256=SHA,
            lineage_sha256="2"*64,reason_code=None
        ) for port in PORTS]
        evidence_digest=canonical_hash({
            "schema":"uae-snapshot-assembly/v1","items":self.evidence,
        })
        self.payload=dict(
            schema_version="uae-snapshot-assembly/v1",
            status="qualified",project_id=s.project_id,video_id=s.video_id,
            source_id=s.source_id,
            source_policy_version_id=s.source_policy_version_id,
            content_kind=s.content_kind,scope_sha256=s.scope_sha256,
            input_sha256="3"*64,decision_sha256="4"*64,
            evidence_set_sha256=evidence_digest,
            current_source_rights_sha256="e"*64,
            lineage_sha256="f"*64,joined_context_sha256="1"*64,
            evidence=self.evidence,
            versions=dict(
                sre_taxonomy_version_id=c.lineage.sre_taxonomy_version_id,
                group_a_analysis_id=c.group_a.analysis_id,
                group_a_result_sha256=c.group_a.result_sha256,
                group_b_analysis_id=c.group_b.b_analysis_id,
                calculated_metric_id=c.group_c.calculated_metric_id,
                calculation_config_id=c.group_c.config_id,
                qdp_run_id=s.qdp_run_id,qdp_input_sha256=s.qdp_input_sha256,
            )
        )
        self.history=dict(
            id=19,project_id=s.project_id,request_id=RUN,
            video_id=s.video_id,source_id=s.source_id,
            source_policy_version_id=s.source_policy_version_id,
            content_kind=s.content_kind,qdp_run_id=s.qdp_run_id,
            qdp_input_sha256=s.qdp_input_sha256,status="qualified",
            scope_sha256=s.scope_sha256,inputs_sha256="3"*64,
            decision_sha256="4"*64,
            sre_decision_sha256=c.lineage.sre_decision_sha256,
            group_a_analysis_id=c.group_a.analysis_id,
            group_b_analysis_id=c.group_b.b_analysis_id,
            calculated_metric_id=c.group_c.calculated_metric_id,
            metric_snapshot_id=c.group_c.current_snapshot_id,
            verified_as_of=NOW-timedelta(minutes=2),
            assembly_schema="uae-snapshot-assembly/v1",
            payload=self.payload,payload_sha256=canonical_hash(self.payload),
            evidence_set_sha256=evidence_digest,
        )

    def __enter__(self):return self
    def __exit__(self,*args):return False
    @contextmanager
    def transaction(self):
        yield
    def execute(self,sql,args=()):
        self.queries.append((sql,args))
        if sql=="SET TRANSACTION ISOLATION LEVEL REPEATABLE READ":
            return Cursor([])
        if sql==mod.CLOCK_SQL:
            return Cursor([dict(db_now=NOW)])
        if sql==mod.VISIBILITY_CANDIDATE_SQL:
            return Cursor([dict(
                project_id=self.scope.project_id,
                snapshot_id=19,video_id=self.scope.video_id
            )] if self.candidate_visible else [])
        if sql==mod.SNAPSHOT_PROOF_SQL:
            return Cursor([self.history])
        if sql==mod.EVIDENCE_PROOF_SQL:
            return Cursor(self.evidence)
        if sql==mod.CURRENT_INVALIDATION_SQL:
            return Cursor([dict(invalidated=self.invalidated)])
        raise AssertionError("Unknown readside SQL")


class VisibilityReaderTests(unittest.TestCase):
    def setUp(self):
        self.scope=InputScope(PROJECT,17,"test.src",5,"video",RUN,SHA)
        self.candidate=FakeCandidate(self.scope)
        self.db=FakeDB(self.scope,self.candidate)
        self.reader=mod.VisibleSnapshotReader(self.db)

    @contextmanager
    def future_mock(self):
        with patch.object(mod,"JoinedCandidatePins",FakeCandidate),\
             patch.object(mod,"SourcePolicyCurrentReader",FakeSourceReader),\
             patch.object(mod,"JoinedContextAssembler",FakeAssembler),\
             patch.object(mod,"QualificationOrchestrator",FakeOrchestrator):
            yield

    def pins(self):
        return mod.VisibilityPins(19,self.candidate)

    def test_future_mock_complete_verification_only_returns_minimal_pointer(self):
        with self.future_mock():
            result=self.reader.visible_one(self.pins())
        self.assertIsInstance(result,mod.VisibleSnapshot)
        self.assertEqual((result.project_id,result.snapshot_id,result.video_id),
                         (PROJECT,19,17))
        self.assertFalse(hasattr(result,"payload"))
        self.assertFalse(hasattr(result,"score"))

    def test_unapproved_peer_blocks_historical_qualified_claim(self):
        self.db.peer_approved=False
        with self.future_mock():
            self.assertIsNone(self.reader.visible_one(self.pins()))

    def test_missing_peer_blocks_without_inferring_latest(self):
        self.db.peer_missing=True
        with self.future_mock():
            self.assertIsNone(self.reader.visible_one(self.pins()))

    def test_expired_or_revoked_rights_block_visibility(self):
        self.db.rights_revoked=True
        with self.future_mock():
            self.assertIsNone(self.reader.visible_one(self.pins()))

    def test_source_digest_changed_rejected(self):
        self.db.rights_sha="a"*64
        with self.future_mock():
            self.assertIsNone(self.reader.visible_one(self.pins()))

    def test_denied_view_returns_no_data_or_raw_snapshot_query(self):
        self.db.candidate_visible=False
        with self.future_mock():
            self.assertIsNone(self.reader.visible_one(self.pins()))
        statements=[x for x,_ in self.db.queries]
        self.assertNotIn(mod.SNAPSHOT_PROOF_SQL,statements)
        self.assertNotIn(mod.EVIDENCE_PROOF_SQL,statements)

    def test_later_invalidation_blocks_even_after_all_checks(self):
        self.db.invalidated=True
        with self.future_mock():
            self.assertIsNone(self.reader.visible_one(self.pins()))

    def test_payload_checksum_corruption_blocks(self):
        self.db.payload["versions"]["group_a_analysis_id"]=777
        with self.future_mock():
            self.assertIsNone(self.reader.visible_one(self.pins()))

    def test_c_config_changed_blocks_even_valid_recomputed_digest(self):
        self.db.payload["versions"]["calculation_config_id"]=999
        self.db.history["payload_sha256"]=canonical_hash(self.db.payload)
        with self.future_mock():
            self.assertIsNone(self.reader.visible_one(self.pins()))

    def test_sre_taxonomy_mismatch_rejected(self):
        self.db.payload["versions"]["sre_taxonomy_version_id"]=77
        self.db.history["payload_sha256"]=canonical_hash(self.db.payload)
        with self.future_mock():
            self.assertIsNone(self.reader.visible_one(self.pins()))

    def test_b_peer_analysis_mismatch_rejected(self):
        self.db.history["group_b_analysis_id"]=123
        with self.future_mock():
            self.assertIsNone(self.reader.visible_one(self.pins()))

    def test_forged_evidence_set_digest_denied_with_matching_payload_sha(self):
        self.db.history["evidence_set_sha256"]="9"*64
        self.db.payload["evidence_set_sha256"]="9"*64
        self.db.history["payload_sha256"]=canonical_hash(self.db.payload)
        with self.future_mock():
            self.assertIsNone(self.reader.visible_one(self.pins()))

    def test_incomplete_evidence_matrix_rejected(self):
        self.db.evidence[2]["checked"]=False
        self.db.history["payload_sha256"]=canonical_hash(self.db.payload)
        with self.future_mock():
            self.assertIsNone(self.reader.visible_one(self.pins()))

    def test_expired_historical_verification_clock_rejected(self):
        self.db.history["verified_as_of"]=NOW+timedelta(seconds=3)
        with self.future_mock():
            self.assertIsNone(self.reader.visible_one(self.pins()))

    def test_source_scope_project_confusion_returns_no_results(self):
        self.db.history["project_id"]=OTHER
        with self.future_mock():
            self.assertIsNone(self.reader.visible_one(self.pins()))

    def test_duplicate_or_cross_project_batch_rejected_before_database_read(self):
        with self.future_mock():
            first=self.pins()
            with self.assertRaises(AnalyticsBoundaryError):
                self.reader.visible((first,first))
            other_scope=InputScope(OTHER,17,"test.src",5,"video",RUN,SHA)
            other=mod.VisibilityPins(20,FakeCandidate(other_scope))
            with self.assertRaises(AnalyticsBoundaryError):
                self.reader.visible((first,other))
        self.assertFalse(self.db.queries)

    def test_stale_nonidle_transaction_rejected(self):
        self.db.info.transaction_status=2
        with self.future_mock():
            with self.assertRaises(AnalyticsBoundaryError) as ctx:
                self.reader.visible_one(self.pins())
        self.assertEqual(ctx.exception.code,"visibility_fresh_transaction_required")

    def test_bad_pins_and_empty_batch_rejected(self):
        with self.future_mock():
            with self.assertRaises(AnalyticsBoundaryError):
                mod.VisibilityPins(0,self.candidate)
            with self.assertRaises(AnalyticsBoundaryError):
                self.reader.visible(())


class VisibilitySQLStaticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root=Path(__file__).resolve().parents[1]
        cls.sql=(root/"intelligence/migrations/"
                 "089_unified_analytics_readside_visibility.sql").read_text(
                     encoding="utf-8").lower()

    def test_view_security_and_no_raw_data_or_public_grants(self):
        for marker in (
            "create view uae_readside_visibility_candidates",
            "security_barrier=true","security_invoker=true",
            "sn.status='qualified'","req.state='resolved'",
            "count(distinct h.analysis_group)","uae_snapshot_invalidations",
            "uae_revision_inbox","revision.observed_at>=sn.verified_as_of",
            "ev.checked is true",
            "payload.payload->>'status'='qualified'",
            "sre_workspaces", "workspace.active_taxonomy_id",
        ):
            self.assertIn(marker,self.sql)
        self.assertNotIn("grant select",self.sql)
        self.assertNotIn("create table calculated_metrics",self.sql)
        self.assertNotIn("compute_c_metrics(",self.sql)
        self.assertNotIn("drop table",self.sql)
        self.assertNotIn("materialized view",self.sql)

    def test_current_rights_expiry_and_scope_checked_every_read(self):
        for marker in (
            "src.status='enabled'",
            "pol.is_active is true",
            "pol.reviewed_at is not null",
            "src.retention_days>0",
            "metric.expires_at>statement_timestamp()",
            "metric.data_quality_status='ok'",
            "membership.project_id=sn.project_id",
            "payload.payload->'versions'->>'calculated_metric_id'",
            "revision.event_kind='source_policy_revoked'",
        ):
            self.assertIn(marker,self.sql)


if __name__=="__main__":
    unittest.main()
