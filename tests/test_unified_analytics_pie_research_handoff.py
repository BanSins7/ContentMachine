"""UA-MOD-25 synthetic cross-contract golden and fail-closed authorization.

No PIE code is imported into UAE. Tests pin PIE's read-only v1 wire keys
and receipt HMAC independently; production source/peer approval is absent.
"""
from __future__ import annotations

from decimal import Decimal
from datetime import timedelta
import hashlib
import hmac
import json
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError, canonical_hash
from intelligence.unified_analytics import pie_research_handoff as pie
from intelligence.unified_analytics import project_ranked_views as rank
from intelligence.unified_analytics import group_a_adapter as ga
from intelligence.unified_analytics import cohort_comparison as cmp
from intelligence.unified_analytics import typed_segmentation as seg
from test_unified_analytics_project_ranked_views import RankDB
from test_unified_analytics_descriptive_aggregation import (
    Pins, MockVisible, Cursor, NOW, SHA,
)
from test_unified_analytics_cohort_comparison import auth_row
from test_unified_analytics_typed_segmentation import make_fact


CHALLENGE="9"*64
SECRET=b"trusted test-only key available to separately configured recipient"+b"!test"
RUNKEY="pie.unit.dataset.v1"


def pinned(n=5):
    allpins=tuple(Pins(i) for i in range(1,n+1))
    for p in allpins:
        scope=p.candidate.scope
        c=p.candidate
        scope.source_policy_version_id=7
        scope.scope_sha256="a"*64
        scope.qdp_run_id="00112233-4455-4677-8899-aabbccddeeff"
        scope.qdp_input_sha256="b"*64
        c.fingerprint="f"*64
        c.group_a.analysis_id=4000+scope.video_id
        c.group_a.result_sha256="1"*64
        c.group_b.b_run_id="00112233-4455-4677-8899-aabbccddeeff"
        c.group_b.b_analysis_id=5000+scope.video_id
        c.group_b.block_evidence_sha256s=("2"*64,)*8
        c.group_c.metadata_id=6000+scope.video_id
        c.lineage.sre_decision_sha256="3"*64
    return allpins


class PIEFakeDB(RankDB):
    def __init__(self,pins):
        super().__init__(pins)
        self.extra_reads=0
        self.extra={
            p.snapshot_id:auth_row(p,333)
            for p in pins
        }
        for p in pins:
            m=self.extra[p.snapshot_id]
            # All publication observations are in the past relative to DB NOW.
            m["observed_at"]=m["published_at"]+timedelta(days=4)
        self.a_rows={
            p.candidate.group_a.analysis_id:{
                "id":p.candidate.group_a.analysis_id,
                "video_id":p.candidate.scope.video_id,
                "source_id":p.candidate.scope.source_id,
                "source_policy_version_id":
                    p.candidate.scope.source_policy_version_id,
                "analysis_version":p.candidate.group_a.analysis_version,
                "state":"completed","scene_count":4,
                "has_subtitles":"unknown",
            } for p in pins
        }
        self.b_rows={
            p.candidate.group_b.b_analysis_id:make_fact(p) for p in pins
        }

    def execute(self,sql,args=()):
        if sql==cmp.AUTHOR_TIME_SQL:
            self.extra_reads+=1
            self.sql.append(sql)
            a=self.extra.get(args[1])
            return Cursor([a] if a else [])
        if sql==ga.A_VIDEO_SQL:
            self.extra_reads+=1
            self.sql.append(sql)
            a=self.a_rows.get(args[0])
            return Cursor([a] if a else [])
        if sql==seg.GROUP_B_DIMENSIONS_SQL:
            self.extra_reads+=1
            self.sql.append(sql)
            a=self.b_rows.get(args[-1])
            return Cursor([a] if a else [])
        if sql.lstrip().startswith("SELECT pl.payload_sha256"):
            self.extra_reads+=1
            self.sql.append(sql)
            return Cursor([{
                "payload_sha256":"a"*64,"project_id":args[0],
                "id":args[1],"video_id":args[2],"status":"qualified",
            }])
        return super().execute(sql,args)


class TemporaryApproval(unittest.TestCase):
    def setUp(self):
        self.pins=pinned()
        self.db=PIEFakeDB(self.pins)
        self.signer=pie.ReceiptSigner(
            SECRET,key_id="unit-test.key.v1",issuer="uae.project.research"
        )

    def create(self):
        with patch.object(rank,"VisibilityPins",Pins),\
             patch.object(rank,"VisibleSnapshotReader",MockVisible):
            return rank.ProjectRankedRunStore(self.db).create(
                self.pins,RUNKEY,sort_mode="breakout_desc",
            )

    def export(self,run=None,challenge=CHALLENGE,pins=None):
        if run is None:
            run=self.create()
        with patch.object(pie,"VisibilityPins",Pins),\
             patch.object(pie,"VisibleSnapshotReader",MockVisible),\
             patch.object(rank,"VisibleSnapshotReader",MockVisible):
            return pie.PIEResearchExportReader(self.db,self.signer).export(
                run.run_id,pins if pins is not None else self.pins,challenge,
            )

    def test_pie_v1_producer_manifest_exact_top_keys(self):
        r=self.export()
        self.assertIsInstance(r,pie.SignedResearchExport)
        doc=json.loads(r.raw_manifest)
        self.assertEqual(set(doc),{
            "schema_version","producer","scope","versions",
            "selection","observations",
        })
        self.assertEqual(doc["schema_version"],"uae-to-pie/v1")
        self.assertEqual(doc["producer"]["engine_version"],
                         pie.PIE_ENGINE_VERSION)
        self.assertEqual(doc["selection"]["scanned_count"],5)
        self.assertEqual(doc["selection"]["included_count"],5)
        self.assertEqual(doc["selection"]["excluded_count"],0)
        self.assertEqual(doc["selection"]["exclusions"],[])
        self.assertFalse(r.verified_live)
        self.assertFalse(r.ready_for_analysis)

    def test_exact_pie_v1_row_level_provenance(self):
        out=self.export()
        rows=json.loads(out.raw_manifest)["observations"]
        self.assertEqual([x["video_id"] for x in rows],[1,2,3,4,5])
        self.assertEqual([x["uae_snapshot_id"] for x in rows],
                         [1001,1002,1003,1004,1005])
        for x in rows:
            self.assertEqual(x["status"],"qualified")
            self.assertEqual(x["score_status"],"complete")
            self.assertEqual(x["creator_key"],"creator:333")
            self.assertEqual(x["platform"],"youtube")
            self.assertEqual(x["source_policy_version_id"],7)
            for name in ("uae_snapshot_sha256","group_a_sha256",
                         "group_b_sha256","metric_snapshot_sha256",
                         "calculated_metric_sha256","features_sha256"):
                self.assertEqual(len(x[name]),64)
            self.assertEqual(x["features_sha256"],canonical_hash({
                "a":x["features_a"],"b":x["features_b"]
            }))
            self.assertEqual(x["features_a"]["scene_count"],4)
            self.assertEqual(x["features_b"]["hook_mechanisms"],
                             ["curiosity","promise"])

    def test_independent_byte_hash_and_receipt_hmac_golden(self):
        a=self.export()
        raw=a.raw_manifest
        self.assertEqual(raw,json.dumps(
            json.loads(raw),sort_keys=True,ensure_ascii=False,
            separators=(",",":"),allow_nan=False
        ).encode("utf-8"))
        self.assertEqual(a.manifest_sha256,
                         canonical_hash(json.loads(raw)))
        self.assertEqual(a.dataset_sha256,canonical_hash({
            "schema_version":"pie-row-dataset/v1",
            "observations":json.loads(raw)["observations"],
        }))
        signed=json.dumps(a.receipt.signed_fields(),sort_keys=True,
                          ensure_ascii=False,separators=(",",":"),
                          allow_nan=False).encode("utf-8")
        self.assertTrue(hmac.compare_digest(
            hmac.new(SECRET,signed,hashlib.sha256).hexdigest(),
            a.receipt.signature,
        ))
        self.assertEqual(a.receipt.challenge,CHALLENGE)

    def test_replay_same_manifest_bytes_and_hash_with_same_db_clock(self):
        run=self.create()
        a=self.export(run)
        b=self.export(run)
        self.assertEqual(a,b)
        self.assertEqual(a.receipt.signature,b.receipt.signature)

    def test_signed_challenge_changes_receipt_not_manifest_data(self):
        run=self.create()
        a=self.export(run,challenge="a"*64)
        b=self.export(run,challenge="b"*64)
        self.assertEqual(a.raw_manifest,b.raw_manifest)
        self.assertNotEqual(a.receipt.signature,b.receipt.signature)

    def test_signed_expiry_bounded_300_seconds(self):
        r=self.export()
        from datetime import datetime
        start=datetime.fromisoformat(r.receipt.issued_at)
        end=datetime.fromisoformat(r.receipt.expires_at)
        self.assertEqual((end-start).total_seconds(),300)

    def test_hidden_last_peer_denies_all_reads(self):
        run=self.create()
        self.db.denied.add(self.pins[-1].snapshot_id)
        before=self.db.query_count
        self.assertIsNone(self.export(run))
        self.assertEqual(self.db.query_count,before)
        self.assertEqual(self.db.extra_reads,0)

    def test_partial_not_promoted_missing_group_a(self):
        run=self.create()
        self.db.a_rows.pop(self.pins[2].candidate.group_a.analysis_id)
        self.assertIsNone(self.export(run))

    def test_group_b_wrong_taxonomy_denies_entire_manifest(self):
        run=self.create()
        key=self.pins[0].candidate.group_b.b_analysis_id
        self.db.b_rows[key]["taxonomy_version"]="wrong"
        self.assertIsNone(self.export(run))

    def test_source_metric_score_change_rejects_old_manifest(self):
        run=self.create()
        self.db.rows[self.pins[4].snapshot_id]["breakout_score"]=Decimal(99)
        self.assertIsNone(self.export(run))

    def test_missing_publication_or_creator_denies(self):
        run=self.create()
        self.db.extra[self.pins[0].snapshot_id]["creator_id"]=None
        self.assertIsNone(self.export(run))

    def test_sample_under_floor_or_duplicate_denied_pre_io(self):
        reader=pie.PIEResearchExportReader(self.db,self.signer)
        with patch.object(pie,"VisibilityPins",Pins):
            for invalid in (self.pins[:4],self.pins+(self.pins[0],)):
                with self.assertRaises(AnalyticsBoundaryError):
                    reader.export(str(UUID(int=123)),invalid,CHALLENGE)
        self.assertEqual(self.db.sql,[])

    def test_bad_challenge_no_signature_no_io(self):
        reader=pie.PIEResearchExportReader(self.db,self.signer)
        with patch.object(pie,"VisibilityPins",Pins):
            for bad in ("abc","X"*64,None,""):
                with self.assertRaises(AnalyticsBoundaryError):
                    reader.export(str(UUID(int=123)),self.pins,bad)
        self.assertEqual(self.db.sql,[])

    def test_no_default_secret_signer_capability(self):
        for args in (b"too-short",b"",None):
            with self.assertRaises(AnalyticsBoundaryError):
                pie.ReceiptSigner(args,key_id="test.key")
        with self.assertRaises(AnalyticsBoundaryError):
            pie.PIEResearchExportReader(self.db,None)

    def test_cannot_sign_arbitrary_receipt_fields(self):
        with self.assertRaises(AnalyticsBoundaryError):
            self.signer.sign({"project_id":"a"*32})

    def test_incompatible_source_cohort_rejected(self):
        run=self.create()
        self.pins[-1].candidate.group_c.config_version="different"
        self.assertIsNone(self.export(run))

    def test_only_private_nonpublisher_outputs(self):
        r=self.export()
        self.assertFalse(r.verified_live)
        self.assertFalse(r.ready_for_analysis)
        self.assertFalse(hasattr(r,"publication_url"))
        self.assertFalse(hasattr(r,"insight"))
        self.assertFalse(hasattr(r,"significance"))

    def test_no_full_project_missingness_claim(self):
        r=self.export()
        selection=json.loads(r.raw_manifest)["selection"]
        self.assertEqual(selection["excluded_count"],0)
        self.assertIn("explicit-current-qualified-pins",
                      selection["rule_version"])
        self.assertNotIn("project_population_total",selection)

    def test_no_media_paths_or_unapproved_features(self):
        r=self.export()
        s=r.raw_manifest.decode("utf-8")
        for x in ("screen_text","spoken_text","media_path",
                  "transcription","private_filename","llm_output"):
            self.assertNotIn(x,s)

    def test_static_pie_signature_wire_receipt_order(self):
        self.assertEqual(pie._RECEIPT_FIELDS,(
            "project_id","source_id","source_policy_version_id",
            "producer_version","producer_run_id","manifest_sha256",
            "dataset_sha256","challenge","issued_at","expires_at",
            "issuer","key_id",
        ))


if __name__=="__main__":
    unittest.main()
