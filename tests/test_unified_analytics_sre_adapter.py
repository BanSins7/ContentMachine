"""UA-MOD-06: real DB-query-shape mock tests, not LIVE provider or peer results.

Positive mocks deliberately imitate future M13+ settlement after SRE implements
a verified LIVE results pipeline. Current SRE 055 DB permits ONLY
not_evaluated/blocked pair states, so positive mock PASS != production-ready.
"""
from __future__ import annotations

import copy
import os
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID

from intelligence.unified_analytics import (
    AnalyticsBoundaryError, InputScope, UAEPorts, ReadState,
    CapabilityState, CanonicalLineagePins,
)
from intelligence.unified_analytics.contracts import canonical_hash
from intelligence.unified_analytics.sre_adapter import (
    COLUMN_PROBE_SQL, REQUIRED_RELATIONS, SCHEMA_PROBE_SQL,
    SRE_RESULTS_SQL, SRE_RUN_ITEM_SQL, SRE_STALENESS_SQL,
    SREApprovedRelevancePins, SREApprovedRelevanceReader, SRE_PEER_WIRE,
)

NOW = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
PROJECT = "a"*32
RUN = "00112233-4455-4677-8899-aabbccddeeff"
QDP = "11112222-3333-4444-8555-666677778888"
K1 = "10000000-0000-4000-8000-000000000001"
K2 = "10000000-0000-4000-8000-000000000002"
SHA = "c"*64
SCOPE = InputScope(PROJECT, 17, "test_source", 5, "video", QDP, SHA)


def decisions_digest(pairs):
    return canonical_hash({"effective_decisions": [
        [x["category_key"], x["human_label"] or x["model_decision"]] for x in pairs
    ]})


def fixture():
    results = [
        dict(category_key=K1, pair_state="match",
             result_id=42, model_decision="match",
             result_content_version_id=13, result_taxonomy_id=21,
             result_evidence=[{"quote":"verified-from-source"}],
             result_trace=[{"stage":"match"}],
             category_review_status="approved",
             human_label=None, human_label_id=None, human_created_at=None),
        dict(category_key=K2, pair_state="no_match",
             result_id=43, model_decision="no_match",
             result_content_version_id=13, result_taxonomy_id=21,
             result_evidence=[], result_trace=[{"stage":"exclude"}],
             category_review_status="approved",
             human_label=None, human_label_id=None, human_created_at=None),
    ]
    pins = CanonicalLineagePins(
        scope=SCOPE, metadata_id=71, metric_snapshot_id=72,
        sre_run_id=RUN, sre_taxonomy_version_id=21,
        sre_content_version_id=13, group_a_analysis_id=14,
        group_b_analysis_id=15, calculated_metric_id=16,
        b_taxonomy_version="taxonomy/1",
        sre_decision_sha256=decisions_digest(results),
    )
    adapter_pins = SREApprovedRelevancePins(
        lineage=pins, taxonomy_sha256="d"*64,
        content_sha256="e"*64, contexts_sha256="f"*64,
        target_category_keys=(K1,),
    )
    record = dict(
        run_id=UUID(RUN), project_id=PROJECT, video_id=17,
        item_id=25, live_execution_mode="live", classifier_execution_mode="live",
        live_state="completed", classifier_state="completed",
        item_state="completed", cancel_requested=False,
        expected_items=1, finished_items=1, blocked_items=0,
        taxonomy_version_id=21, classifier_taxonomy_id=21,
        active_taxonomy_id=21, taxonomy_state="approved",
        taxonomy_sha256="d"*64, snapshot_checksum="d"*64,
        content_version_id=13, content_sha256="e"*64,
        contexts_sha256="f"*64, source_id="test_source",
        source_policy_version_id=5, metadata_observation_id=71,
        metadata_id=71, manifest_sha256="1"*64,
        item_manifest_sha256="1"*64, content_revision=2,
        live_created_at=NOW-timedelta(hours=4),
        content_created_at=NOW-timedelta(days=3),
        content_expires_at=NOW+timedelta(days=4),
        source_state="enabled", can_store_metadata=True,
        can_analyze_media=True, source_retention_days=30,
        policy_active=True, policy_reviewed_at=NOW-timedelta(days=4),
        policy_permissions={"can_store_metadata":True,
                            "can_semantic_classify":True,
                            "can_analyze_media":True},
        retention_policy={"retention_days":30},
        metadata_current=True, metadata_deleted_at=None,
        metadata_expires_at=NOW+timedelta(days=2),
        metadata_fetched_at=NOW-timedelta(days=3),
        expected_hypothesis_keys=[K1,K2],
    )
    stale=dict(newer_content=False,newer_live_run=False)
    return adapter_pins, record, results, stale


class Cursor:
    def __init__(self, rows):
        self.rows = rows

    def fetchone(self):
        return self.rows[0] if self.rows else None

    def fetchall(self):
        return self.rows


class FakeConnection:
    def __init__(self, data=None):
        self.data = data or fixture()
        self.calls = []
        self.available = True
        self.fail_on = None
        self.incompatible = False

    def execute(self, sql, params=()):
        self.calls.append((sql, params))
        if self.fail_on == sql:
            raise RuntimeError("database password=should_be_hidden")
        if sql == SCHEMA_PROBE_SQL:
            values = {name: name if self.available else None for name in REQUIRED_RELATIONS}
            return Cursor([values])
        if sql == COLUMN_PROBE_SQL:
            return Cursor([{"n": 4 if self.incompatible else 5}])
        if sql == SRE_RUN_ITEM_SQL:
            return Cursor([self.data[1]] if self.data[1] is not None else [])
        if sql == SRE_STALENESS_SQL:
            return Cursor([self.data[3]] if self.data[3] is not None else [])
        if sql == SRE_RESULTS_SQL:
            return Cursor(self.data[2])
        raise AssertionError("Unexpected SQL from SRE adapter")


class AdapterTests(unittest.TestCase):
    def make(self, data=None):
        d = copy.deepcopy(data or fixture())
        fake = FakeConnection(d)
        return SREApprovedRelevanceReader(fake, d[0], as_of=NOW), fake

    def assert_missing(self, data, code):
        reader, _ = self.make(data)
        r = reader.read_current_relevance(SCOPE)
        self.assertIs(r.state, ReadState.MISSING)
        self.assertEqual(r.reason_code, code)
        self.assertIsNone(r.reference)

    def test_mock_future_live_complete_positive_not_real_production_proof(self):
        reader, conn = self.make()
        self.assertIs(reader.capability().state,CapabilityState.DECLARED)
        got=reader.read_current_relevance(SCOPE)
        self.assertIs(got.state,ReadState.LOCATED)
        self.assertEqual(got.reference.producer_schema,SRE_PEER_WIRE)
        self.assertEqual(got.reference.scope,SCOPE)
        self.assertEqual(len(got.reference.lineage_sha256),64)
        self.assertEqual(got,reader.read_current_relevance(SCOPE))
        self.assertIn(SRE_RESULTS_SQL,[x[0] for x in conn.calls])
        self.assertTrue(all(isinstance(params,tuple) for _,params in conn.calls))
        self.assertIs(UAEPorts(sre=reader).status("sre").state,CapabilityState.DECLARED)
        self.assertIs(UAEPorts(sre=reader).read("sre",SCOPE).state,ReadState.LOCATED)
        self.assertFalse(UAEPorts(sre=reader).status("sre").qualified)

    def test_missing_live_schema_unavailable_not_synthetic_fallback(self):
        reader,conn=self.make()
        conn.available=False
        cap=reader.capability()
        self.assertIs(cap.state,CapabilityState.UNAVAILABLE)
        self.assertEqual(cap.reason_code,"live_schema_missing")
        r=reader.read_current_relevance(SCOPE)
        self.assertIs(r.state,ReadState.UNAVAILABLE)
        self.assertNotIn(SRE_RUN_ITEM_SQL,[x[0] for x in conn.calls])
        conn.available=True
        conn.incompatible=True
        self.assertIs(reader.capability().state,CapabilityState.INCOMPATIBLE)
        conn.incompatible=False
        conn.fail_on=SCHEMA_PROBE_SQL
        self.assertEqual(reader.capability().reason_code,"probe_failed")
        self.assertNotIn("password",str(reader.read_current_relevance(SCOPE)))

    def test_live_and_classification_modes_completion_and_cancel_are_required(self):
        variations=[
            ("live_execution_mode","offline_fixture"),
            ("classifier_execution_mode","offline_fixture"),
            ("classifier_execution_mode","legacy"),
            ("live_state","partial"),
            ("live_state","unknown"),
            ("classifier_state","partial"),
            ("item_state","not_evaluated"),
            ("item_state","partial"),
            ("cancel_requested",True),
            ("finished_items",0),
            ("blocked_items",1),
        ]
        for field,bad in variations:
            with self.subTest(field=field,bad=bad):
                d=list(fixture())
                d[1][field]=bad
                self.assert_missing(tuple(d),"no_completed_live_run")

    def test_current_taxonomy_content_policy_and_context_pins(self):
        cases=[
            ("taxonomy_version_id",999,"stale_or_unapproved_taxonomy"),
            ("active_taxonomy_id",999,"stale_or_unapproved_taxonomy"),
            ("taxonomy_state","draft","stale_or_unapproved_taxonomy"),
            ("taxonomy_sha256","a"*64,"stale_or_unapproved_taxonomy"),
            ("classifier_taxonomy_id",999,"stale_or_unapproved_taxonomy"),
            ("content_sha256","b"*64,"content_or_context_mismatch"),
            ("contexts_sha256","a"*64,"content_or_context_mismatch"),
            ("content_revision",0,"content_or_context_mismatch"),
            ("item_manifest_sha256","a"*64,"content_or_context_mismatch"),
            ("source_id","other","live_lineage_mismatch"),
            ("source_policy_version_id",7,"live_lineage_mismatch"),
            ("metadata_id",999,"live_lineage_mismatch"),
        ]
        for field,bad,code in cases:
            with self.subTest(field=field):
                d=list(fixture())
                d[1][field]=bad
                self.assert_missing(tuple(d),code)

    def test_denied_revoked_or_expired_source_rights(self):
        cases=[
            ("source_state","revoked"),
            ("policy_active",False),
            ("can_store_metadata",False),
            ("can_analyze_media",False),
            ("policy_reviewed_at",None),
            ("policy_permissions",{}),
            ("retention_policy",{"retention_days":"NaN"}),
            ("source_retention_days",0),
            ("metadata_current",False),
            ("metadata_deleted_at",NOW),
            ("metadata_expires_at",NOW),
            ("content_expires_at",NOW),
            ("content_created_at",NOW-timedelta(days=100)),
        ]
        for field,val in cases:
            with self.subTest(field=field):
                d=list(fixture())
                d[1][field]=val
                self.assert_missing(tuple(d),"sre_rights_expired_or_revoked")

    def test_no_match_selected_category_blocks(self):
        d=list(fixture())
        d[2][0]["model_decision"]="no_match"
        d[2][0]["pair_state"]="no_match"
        self.assert_missing(tuple(d),"not_approved_relevant")

    def test_uncertain_and_not_evaluated_never_map_to_negative_or_match(self):
        for code,reason in (
            ("uncertain","uncertain_result"),
            ("not_evaluated","unsettled_live_evidence"),
            ("blocked_rights","unsettled_live_evidence"),
            ("blocked_budget","unsettled_live_evidence"),
        ):
            with self.subTest(code=code):
                d=list(fixture())
                d[2][0]["pair_state"]=code
                d[2][0]["model_decision"]=code
                self.assert_missing(tuple(d),reason)

    def test_current_human_revocation_vetoes_model_match(self):
        d=list(fixture())
        d[2][0].update(human_label="no_match",human_label_id=99,
                       human_created_at=NOW-timedelta(minutes=2))
        self.assert_missing(tuple(d),"not_approved_relevant")
        d=list(fixture())
        d[2][0].update(human_label="uncertain",human_label_id=99,
                       human_created_at=NOW-timedelta(minutes=2))
        self.assert_missing(tuple(d),"uncertain_result")
        d=list(fixture())
        d[2][0].update(human_label="match",human_label_id=99,
                       human_created_at=NOW+timedelta(minutes=3))
        self.assert_missing(tuple(d),"invalid_human_review")

    def test_incomplete_leaf_set_and_wrong_category_or_run_stale(self):
        d=list(fixture())
        d[2].pop()
        self.assert_missing(tuple(d),"incomplete_coverage")
        d=list(fixture())
        d[1]["expected_hypothesis_keys"]=[K1,K1]
        self.assert_missing(tuple(d),"hypothesis_coverage_missing")
        d=list(fixture())
        d[2][0]["category_review_status"]="proposed"
        self.assert_missing(tuple(d),"unapproved_or_unpinned_result")
        for field in ("newer_content","newer_live_run"):
            d=list(fixture())
            d[3][field]=True
            self.assert_missing(tuple(d),"superseded_live_relevance")

    def test_event_and_legacy_result_cannot_override_nonterminal_live_pair(self):
        d=list(fixture())
        d[2][0]["pair_state"]="not_evaluated"
        d[2][0]["human_label"]="match"
        d[2][0]["human_label_id"]=888
        d[2][0]["human_created_at"]=NOW-timedelta(days=1)
        self.assert_missing(tuple(d),"unsettled_live_evidence")

    def test_changed_effective_decisions_digest_does_not_reuse_old_pin(self):
        d=list(fixture())
        d[2][1]["human_label"]="match"
        d[2][1]["human_label_id"]=55
        d[2][1]["human_created_at"]=NOW-timedelta(days=1)
        d[2][1]["result_evidence"]=[{"quote":"authorized text"}]
        self.assert_missing(tuple(d),"sre_decision_digest_mismatch")

    def test_pins_and_wrong_project_are_denied(self):
        p=fixture()[0]
        for kw in (
            {"taxonomy_sha256":"A"*64}, {"content_sha256":"invalid"},
            {"target_category_keys":()},
            {"target_category_keys":(K2,K1)},
            {"target_category_keys":(K1,K1)},
        ):
            with self.subTest(kw=kw),self.assertRaises(AnalyticsBoundaryError):
                replace(p,**kw)
        reader,_=self.make()
        with self.assertRaises(AnalyticsBoundaryError):
            reader.read_current_relevance(replace(SCOPE,project_id="f"*32))
        with self.assertRaises(AnalyticsBoundaryError):
            SREApprovedRelevanceReader(None,p,as_of=datetime(2026,10,26))
        with self.assertRaises(AnalyticsBoundaryError):
            SREApprovedRelevanceReader(None,p,as_of=NOW.astimezone(
                timezone(timedelta(hours=2))))

    def test_unavailable_record_never_hides_sensitive_exception(self):
        r,conn=self.make()
        conn.fail_on=SRE_RUN_ITEM_SQL
        with self.assertRaises(AnalyticsBoundaryError) as err:
            r.read_current_relevance(SCOPE)
        self.assertEqual(err.exception.code,"sre_dependency_read_failed")
        self.assertNotIn("password",str(err.exception))


@unittest.skipUnless(os.environ.get("CM_TEST_PG_DSN"),
                     "Disposable PG17 baseline tests are optional")
class SREBaselinePG17Tests(unittest.TestCase):
    def test_current_standalone_branch_does_not_fabricate_missing_sre_live(self):
        import psycopg
        from psycopg.rows import dict_row
        from intelligence import migrate
        if os.environ.get("CM_TEST_PG_DSN") == os.environ.get("CM_DB_DSN"):
            self.fail("Refuse production-like DB DSN")
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"],
                             autocommit=True) as conn:
            ver=int(conn.execute("SHOW server_version_num").fetchone()[0])
            self.assertTrue(170000<=ver<180000)
            migrate.apply_migrations(conn)
            conn.row_factory=dict_row
            p=fixture()[0]
            reader=SREApprovedRelevanceReader(conn,p,as_of=NOW)
            status=reader.capability()
            # The standalone branch has NOT deployed peer 017/054/055 migrations.
            self.assertIs(status.state,CapabilityState.UNAVAILABLE)
            self.assertEqual(status.reason_code,"live_schema_missing")
            self.assertIs(reader.read_current_relevance(SCOPE).state,ReadState.UNAVAILABLE)


if __name__ == "__main__":
    unittest.main()
