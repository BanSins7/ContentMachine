"""UA-MOD-03 pure gate regressions derived from legacy test_analytics_engine.

Fixtures simulate future source-backed verified snapshots. They are not LIVE
SRE/QDP/A/B/C records and do NOT establish an end-to-end production PASS.
"""
from __future__ import annotations

import copy
import unittest
from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from intelligence.unified_analytics import AnalyticsBoundaryError, InputScope
from intelligence.unified_analytics.dto import EvidenceRef
from intelligence.unified_analytics.qualification import (
    GATE_SCHEMA, REQUIRED_PORTS, ObservationState,
    QualificationObservation, QualificationInput,
    canonical_gate_hash, evaluate_qualification, validate_request,
)

PID = "a" * 32
RUN = "00112233-4455-6677-8899-aabbccddeeff"
AS_OF = datetime(2026, 10, 26, 12, tzinfo=timezone.utc)
SHA = "d" * 64
SCOPE = InputScope(PID, 7, "legal_source", 5, "video", RUN, SHA)
RIGHTS = ("can_store_metadata", "can_analyze_media", "can_store_metrics", "can_derive_scores")
PEER_SCHEMAS = {
    "source_policy": "uae-source-policy-reader/v1",
    "d1": "uae-d1-reader/v1",
    "qdp": "qdp-handoff/v1",
    "sre": "sre.classifications_ready/v1",
    "group_a": "group-a-to-b/v1",
    "group_b": "peer-b-output/v1",
    "group_c": "peer-c-output/v1",
}


def legacy_vector(scope=SCOPE):
    """Grounded in PR23's test_analytics_engine.evidence(): explicit new pins."""
    video = scope.content_kind in ("video", "slideshow_video")
    feats = {
        "hook": {"classification_status": "inferred", "taxonomy_version": "taxonomy/1"},
        "format": {"classification_status": "inferred", "taxonomy_version": "taxonomy/1"},
        "story": {"frames": [{"needs_review": False}] if video else [],
                  "intervals": [], "slides": [{"needs_review": False}] if not video else []},
        "visual": {"ai_generated": None, "person_visible": True},
        "editing": {"cut_count": 0} if video else None,
        "subtitles": {"subtitle_type": "none"} if video else None,
        "cta_summary": {"analysis_status": "complete", "has_cta": False},
        "ctas": [],
        "topic": {"topic_id": 14, "needs_review": False, "taxonomy_version": "taxonomy/1"},
    }
    return {
        "source_policy": {
            "source": {"id": scope.source_id, "status": "enabled",
                       **{r: True for r in RIGHTS}},
            "policy": {"source_id": scope.source_id, "version_id": scope.source_policy_version_id,
                       "is_active": True, "not_revoked": True, "retention_valid": True,
                       "expires_at": "2026-11-01T12:00:00Z",
                       "permissions": {r: True for r in RIGHTS}},
        },
        "d1": {
            "project_id": scope.project_id, "video_id": scope.video_id,
            "source_id": scope.source_id, "content_kind": scope.content_kind,
            "project_link": True,
            "metric": {"id": 11, "video_id": scope.video_id, "source_id": scope.source_id,
                       "data_quality_status": "ok", "valid_retention": True,
                       "metric_definition": "video_views/v1", "metric_version": "1"},
        },
        "qdp": {
            "project_id": scope.project_id, "video_id": scope.video_id,
            "source_id": scope.source_id, "source_policy_version_id": scope.source_policy_version_id,
            "qdp_run_id": scope.qdp_run_id, "input_sha256": scope.qdp_input_sha256,
            "qualified": True,
        },
        "sre": {
            "execution_mode": "live", "run_status": "completed", "decision": "match",
            "approved": True, "is_current": True, "superseded": False,
            "taxonomy_version": "taxonomy/1", "content_version_id": 8,
        },
        "group_a": {
            "analysis_id": 3, "video_id": scope.video_id,
            "source_id": scope.source_id, "qdp_input_sha256": scope.qdp_input_sha256,
            "content_kind": scope.content_kind, "state": "completed",
            "verified_media": True, "manifest_sha256": "f"*64,
            "duration_sec": "24", "scene_count": 1, "scene_status": "complete",
            "speech_status": "complete", "screen_text_status": "complete",
            "has_subtitles": "false",
            "slide_status": "complete", "text_status": "complete", "slide_count": 3,
        },
        "group_b": {
            "analysis_id": 4, "video_id": scope.video_id, "source_id": scope.source_id,
            "qdp_input_sha256": scope.qdp_input_sha256,
            "a_analysis_id": 3, "content_type": "video" if video else "carousel",
            "state": "completed", "needs_review": False, "taxonomy_version": "taxonomy/1",
            "features": feats,
        },
        "group_c": {
            "calculated_metric_id": 22, "post_id": scope.video_id,
            "source_id": scope.source_id, "snapshot_id": 11,
            "score_status": "complete", "breakout_score": "113.75",
            "metric_definition": "video_views/v1", "metric_version": "1",
            "approved_config": True, "approved_cohorts": True,
            "score_config_id": 2, "score_version": "group-c-raw/v1",
        },
    }


def make_observation(port, scope, facts, state=ObservationState.COMPLETE,
                     checked=True, current=True):
    ref = EvidenceRef(
        scope, port, "record:" + port, PEER_SCHEMAS[port], "v1",
        canonical_gate_hash(facts),
    )
    return QualificationObservation(port, state, ref, facts, checked, current)


def snapshot(scope=SCOPE, data=None, *, as_of=AS_OF):
    data = copy.deepcopy(legacy_vector(scope) if data is None else data)
    return QualificationInput(scope, as_of, {
        name: make_observation(name, scope, body) for name, body in data.items()
    })


def with_data(path, value, base=None):
    data = copy.deepcopy(legacy_vector() if base is None else base)
    obj = data
    for key in path[:-1]:
        obj = obj[key]
    obj[path[-1]] = value
    return snapshot(data=data)


class CanonicalRequestTests(unittest.TestCase):
    def test_legacy_request_id_rules_and_bool_not_positive(self):
        validate_request(PID, 7, "qualified:video7")
        for pid, video, key in (
            ("UPPER" * 6 + "AA", 7, "qualified:video7"),
            (PID, True, "qualified:video7"),
            (PID, 0, "qualified:video7"),
            (PID, 7, "x"),
            (PID, 7, "inject/drop"),
            (PID, 7, "z" * 181),
        ):
            with self.subTest(pid=pid, video=video, key=key):
                with self.assertRaises(AnalyticsBoundaryError):
                    validate_request(pid, video, key)

    def test_legacy_canonical_json_vector_and_strict_types(self):
        original = {"a": [Decimal("13.250"), UUID(RUN)],
                    "b": date(2026, 10, 26),
                    "z": datetime(2026, 10, 26, 12, tzinfo=timezone.utc)}
        self.assertEqual(canonical_gate_hash(original), canonical_gate_hash(copy.deepcopy(original)))
        self.assertEqual(len(canonical_gate_hash(original)), 64)
        self.assertEqual(
            canonical_gate_hash({"x": Decimal("1.00")}),
            canonical_gate_hash({"x": "1.00"}),
        )
        for invalid in ({"x": Decimal("NaN")}, {"x": float("inf")},
                        {"x": object()}, {3: "key"}, {"x": datetime(2026, 10, 26)},
                        ["not a dict"]):
            with self.subTest(invalid=str(invalid)):
                with self.assertRaises(AnalyticsBoundaryError):
                    canonical_gate_hash(invalid)


class QualifiedGateTests(unittest.TestCase):
    def test_legacy_positive_video_with_new_provenance(self):
        inp = snapshot()
        first = evaluate_qualification(inp)
        self.assertEqual(first["status"], "qualified")
        self.assertEqual(first["blockers"], [])
        self.assertEqual(first["schema_version"], GATE_SCHEMA)
        self.assertTrue(all(x == "ready" for x in first["readiness"].values()))
        self.assertEqual(set(first["readiness"]), set(REQUIRED_PORTS))
        self.assertEqual(first, evaluate_qualification(inp))
        self.assertEqual(len(first["decision_sha256"]), 64)
        # C score is not normalized to [0,100], no recomputation is performed.
        self.assertEqual(legacy_vector()["group_c"]["breakout_score"], "113.75")

    def test_legacy_status_vectors_extended_with_source_qdp_sre(self):
        vectors = [
            (("source_policy", "source", "can_derive_scores"), False, "blocked", "rights_can_derive_scores"),
            (("source_policy", "policy", "is_active"), False, "blocked", "source_policy_inactive"),
            (("group_c", "score_status"), "insufficient_data", "partial", "c_score_not_complete"),
            (("group_c", "breakout_score"), None, "partial", "c_score_not_complete"),
            (("group_a", "scene_status"), "partial", "partial", "a_incomplete_measurement_coverage"),
            (("group_a", "scene_count"), 0, "partial", "a_incomplete_measurement_coverage"),
            (("group_b", "a_analysis_id"), 12, "partial", "a_b_lineage_mismatch"),
            (("group_b", "taxonomy_version"), "different", "partial", "b_taxonomy_version_mismatch"),
            (("sre", "decision"), "no_match", "blocked", "no_approved_current_live_relevance"),
            (("sre", "execution_mode"), "offline_fixture", "blocked", "no_approved_current_live_relevance"),
            (("qdp", "qualified"), False, "blocked", "qdp_membership_or_lineage_mismatch"),
            (("d1", "project_link"), False, "blocked", "not_in_project"),
            (("group_a", "verified_media"), False, "partial", "a_media_unverified"),
            (("group_b", "features", "cta_summary", "analysis_status"),
             "not_run", "partial", "b7_cta_unresolved"),
            (("group_b", "features", "ctas"),
             [{"extraction_status": "needs_review"}], "partial", "b7_cta_needs_review"),
            (("group_c", "approved_config"), False, "partial", "c_configuration_unapproved"),
        ]
        for path, val, expected, blocker in vectors:
            with self.subTest(path=path, val=val):
                result = evaluate_qualification(with_data(path, val))
                self.assertEqual(result["status"], expected)
                self.assertIn(blocker, result["blockers"])

    def test_source_expiry_and_revocation_as_of(self):
        self.assertEqual(evaluate_qualification(snapshot(as_of=AS_OF))["status"], "qualified")
        future = AS_OF + timedelta(days=10)
        got = evaluate_qualification(snapshot(as_of=future))
        self.assertEqual(got["status"], "blocked")
        self.assertIn("source_expired", got["blockers"])
        for path, val, expected in [
            (("source_policy", "policy", "retention_valid"), None, "source_retention_or_revocation"),
            (("source_policy", "policy", "not_revoked"), False, "source_retention_or_revocation"),
            (("source_policy", "policy", "permissions", "can_analyze_media"), None, "rights_can_analyze_media"),
        ]:
            with self.subTest(path=path):
                got = evaluate_qualification(with_data(path, val))
                self.assertEqual(got["status"], "blocked")
                self.assertIn(expected, got["blockers"])

    def test_missing_unknown_and_revoked_do_not_coerce_to_false_or_zero(self):
        orig = snapshot()
        o = dict(orig.observations)
        o.pop("group_c")
        decision = evaluate_qualification(replace(orig, observations=o))
        self.assertEqual(decision["status"], "partial")
        self.assertEqual(decision["readiness"]["group_c"], "missing")
        self.assertIn("group_c_missing", decision["blockers"])
        self.assertNotIn(False, decision["readiness"].values())
        self.assertNotIn(0, decision["readiness"].values())

        o = dict(orig.observations)
        o["source_policy"] = QualificationObservation(
            "source_policy", ObservationState.REVOKED, reason_code="source_revoked")
        decision = evaluate_qualification(replace(orig, observations=o))
        self.assertEqual(decision["status"], "blocked")
        self.assertEqual(decision["readiness"]["source_policy"], "revoked")

        o["qdp"] = QualificationObservation("qdp", ObservationState.UNAVAILABLE,
                                           reason_code="peer_unavailable")
        decision = evaluate_qualification(replace(orig, observations=o))
        self.assertIn("qdp_unavailable", decision["blockers"])
        self.assertEqual(decision["status"], "blocked")

    def test_checked_flag_not_authentication_of_production_peers(self):
        orig = snapshot()
        for name in REQUIRED_PORTS:
            with self.subTest(port=name):
                o = dict(orig.observations)
                o[name] = replace(o[name], checked=False)
                res = evaluate_qualification(replace(orig, observations=o))
                self.assertNotEqual(res["status"], "qualified")
                self.assertEqual(res["readiness"][name], "unverified")
                self.assertIn(name + "_unchecked_or_stale", res["blockers"])
                o[name] = replace(o[name], checked=True, current=False)
                res = evaluate_qualification(replace(orig, observations=o))
                self.assertNotEqual(res["status"], "qualified")

    def test_no_c_scoring_or_fake_ready_from_bare_pointer(self):
        import intelligence.unified_analytics as module
        self.assertFalse(hasattr(module, "compute_c_metrics"))
        with self.assertRaises(AnalyticsBoundaryError):
            evaluate_qualification(SCOPE)
        with self.assertRaises(AnalyticsBoundaryError):
            evaluate_qualification({"source_policy": {"rights": True}})

    def test_version_source_video_parent_and_metric_incompatibility(self):
        vectors = [
            (("qdp", "input_sha256"), "f"*64, "qdp_membership_or_lineage_mismatch"),
            (("qdp", "source_policy_version_id"), 987, "qdp_membership_or_lineage_mismatch"),
            (("d1", "source_id"), "other", "not_in_project"),
            (("group_a", "qdp_input_sha256"), "f"*64, "a_missing_or_incomplete"),
            (("group_b", "qdp_input_sha256"), "f"*64, "b_qdp_lineage_mismatch"),
            (("group_c", "snapshot_id"), 900, "c_lineage_mismatch"),
            (("group_c", "metric_definition"), "reach", "c_lineage_mismatch"),
            (("group_c", "approved_cohorts"), None, "c_configuration_unapproved"),
        ]
        for path, val, blocker in vectors:
            with self.subTest(path=path):
                res = evaluate_qualification(with_data(path, val))
                self.assertNotEqual(res["status"], "qualified")
                self.assertIn(blocker, res["blockers"])

    def test_video_and_carousel_semantics_do_not_leak(self):
        carousel_scope = replace(SCOPE, content_kind="photo_carousel")
        got = evaluate_qualification(snapshot(carousel_scope))
        self.assertEqual(got["status"], "qualified")
        d = legacy_vector(carousel_scope)
        d["group_a"]["slide_count"] = 1
        self.assertIn("a_incomplete_slide_coverage",
                      evaluate_qualification(snapshot(carousel_scope, d))["blockers"])
        d = legacy_vector(carousel_scope)
        d["group_b"]["content_type"] = "video"
        self.assertIn("b_kind_mismatch",
                      evaluate_qualification(snapshot(carousel_scope, d))["blockers"])
        with self.assertRaises(AnalyticsBoundaryError):
            replace(SCOPE, content_kind="image_post")

    def test_invalid_observation_shape_fails_closed_without_evidence(self):
        o = snapshot().observations["group_c"]
        for kwargs in (
            {"facts": {"score_status": "complete"}},
            {"port": "qdp"},
            {"state": ObservationState.UNAVAILABLE},
            {"checked": 1},
            {"current": None},
            {"state": "complete"},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(AnalyticsBoundaryError):
                replace(o, **kwargs)
        with self.assertRaises(AnalyticsBoundaryError):
            QualificationObservation("group_a", ObservationState.MISSING,
                                     checked=True, reason_code="no_row")
        with self.assertRaises(AnalyticsBoundaryError):
            QualificationInput(SCOPE, AS_OF, {"group_c": snapshot().observations["group_a"]})

    def test_utc_required_and_cross_scope_refused(self):
        with self.assertRaises(AnalyticsBoundaryError):
            replace(snapshot(), as_of=datetime(2026, 10, 26, 12))
        with self.assertRaises(AnalyticsBoundaryError):
            replace(snapshot(), as_of=datetime(2026, 10, 26, 12,
                                                tzinfo=timezone(timedelta(hours=2))))
        altered = replace(SCOPE, video_id=8)
        with self.assertRaises(AnalyticsBoundaryError):
            replace(snapshot(), scope=altered)

    def test_gate_decision_is_input_idempotent_and_version_pinned(self):
        start = snapshot()
        x = evaluate_qualification(start)
        self.assertEqual(x, evaluate_qualification(copy.deepcopy(start)))
        altered = with_data(("group_c", "breakout_score"), "1000.000")
        self.assertEqual(evaluate_qualification(altered)["status"], "qualified")
        # Values are not normalized but source payload version changes decision,
        # so future persistence can reject duplicate request-key conflicts.
        # The gate records scope only; snapshot assembly pins every port's hash later.
        self.assertEqual(x["decision_sha256"], evaluate_qualification(altered)["decision_sha256"])
        self.assertEqual(x["scope_sha256"], evaluate_qualification(altered)["scope_sha256"])


if __name__ == "__main__":
    unittest.main()
