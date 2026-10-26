"""VC-MOD-01: immutable, versioned Collector boundary contracts, no I/O."""
from __future__ import annotations
from dataclasses import FrozenInstanceError
import socket
import unittest
from unittest.mock import patch
from uuid import UUID

from intelligence.collector.contracts import CollectorError
from intelligence.collector.ports import (
    BOUNDARY_ERRORS, QV_READY_EVENT, QV_BUNDLE_SCHEMA,
    DISCOVERY_COMMITTED_EVENT, SRE_READY_EVENT,
    QvReadyV1, QvBundleShapeV1, D1VideoRefV1,
    DiscoveryCommittedV1, SREReadyV1, runtime_flags,
)

P = "a" * 32
H = "b" * 64
C = "c" * 64
G = "e7c041e9-1234-4567-8abc-bccde0000042"
O = "e7c041e9-1234-4567-8abc-bccde0000043"
R = "e7c041e9-1234-4567-8abc-bccde0000044"
T = "e7c041e9-1234-4567-8abc-bccde0000045"
Q1 = "e7c041e9-1234-4567-8abc-bccde0000046"
Q2 = "e7c041e9-1234-4567-8abc-bccde0000047"


def qv_event(count=1):
    return {"outbox_id": O, "validation_run_id": R,
            "event_type": QV_READY_EVENT,
            "payload_pointer": {
                "validation_run_id": R, "generation_id": G,
                "schema_version": QV_BUNDLE_SCHEMA,
                "embedding_mode": "semantic_local", "selected_count": count,
            }}


def qv_bundle(query_ids=(Q1,)):
    return {
        "schema_version": QV_BUNDLE_SCHEMA, "validation_run_id": R,
        "generation_id": G, "project_id": P, "status": "completed",
        "embedding_model_key": "test-embedding-model",
        "embedding_mode": "semantic_local",
        "config_sha256": H, "source_bundle_sha256": C,
        "queries": [
            {"candidate_id": q, "hypothesis_id": "Hypothesis_A",
             "direction_id": "Direction_A", "text": "home workout",
             "query_mode": "keyword", "language": "en",
             "branch_id": None, "source_fact_ids": [], "angle": "",
             "research_stance": "", "rationale": "",
             "relevance_score": 0.9, "negative_score": 0.0,
             "mmr_score": 0.7, "rank_in_hypothesis": i + 1}
            for i, q in enumerate(query_ids)
        ],
        "decisions": [{"candidate_id": q, "decision": "selected",
                       "reason_code": "selected", "duplicate_of": None,
                       "positive_similarity": 0.9, "negative_similarity": 0.0,
                       "score_margin": 0.9, "mmr_score": 0.7}
                      for q in query_ids],
        "coverage": [{"hypothesis_id": "Hypothesis_A", "source_count": len(query_ids),
                      "selected_count": len(query_ids),
                      "outcome": "selected" if query_ids else "no_source_candidates",
                      "reason": None if query_ids else "no candidates"}],
    }


def discovery():
    return DiscoveryCommittedV1(
        project_id=P, collector_run_id=4, source_request_id=5,
        checkpoint_id=8, provider_task_id="dtk1234",
        publication_id=T, taxonomy_version=3,
        taxonomy_sha256=H, query_ids=(Q1,),
        page_sha256=C, videos=(D1VideoRefV1(12, 13, 14),),
    )


class PortsV1Tests(unittest.TestCase):
    def rejects(self, code, action):
        with self.assertRaises(CollectorError) as caught:
            action()
        self.assertEqual(caught.exception.code, code)

    def test_qv_production_event_exact_source_shape(self):
        event = QvReadyV1.parse(P, qv_event())
        self.assertEqual(event.to_dict()["selected_count"], 1)
        self.assertEqual(event.validation_run_id, R)
        self.assertEqual(event.to_dict()["schema_version"], QV_READY_EVENT)
        with self.assertRaises(FrozenInstanceError):
            event.project_id = "bad"

    def test_qv_event_rejects_future_version_and_test_embedding(self):
        obj = qv_event()
        obj["payload_pointer"]["schema_version"] = "validated_queries/v2"
        self.rejects("invalid_schema_version", lambda: QvReadyV1.parse(P, obj))
        obj = qv_event()
        obj["payload_pointer"]["embedding_mode"] = "test_fixture"
        self.rejects("source_not_verified", lambda: QvReadyV1.parse(P, obj))
        obj = qv_event()
        obj["event_type"] = "validated_queries_ready/v2"
        self.rejects("invalid_schema_version", lambda: QvReadyV1.parse(P, obj))
        obj = qv_event()
        obj["extra"] = "untrusted"
        self.rejects("invalid_schema", lambda: QvReadyV1.parse(P, obj))

    def test_qv_event_prevents_wrong_ids_and_counts(self):
        obj = qv_event()
        obj["payload_pointer"]["validation_run_id"] = G
        self.rejects("event_integrity_mismatch", lambda: QvReadyV1.parse(P, obj))
        obj = qv_event()
        obj["outbox_id"] = 123
        self.rejects("invalid_uuid", lambda: QvReadyV1.parse(P, obj))
        obj = qv_event()
        obj["payload_pointer"]["selected_count"] = 481
        self.rejects("invalid_limit", lambda: QvReadyV1.parse(P, obj))
        obj = qv_event()
        obj["payload_pointer"]["selected_count"] = True
        self.rejects("invalid_limit", lambda: QvReadyV1.parse(P, obj))
        self.rejects("invalid_identifier", lambda: QvReadyV1.parse("wrong-project", qv_event()))

    def test_qv_bundle_matches_notice_and_selected_decisions(self):
        event = QvReadyV1.parse(P, qv_event(2))
        result = QvBundleShapeV1.parse(event, qv_bundle((Q1, Q2)))
        self.assertEqual(result.query_ids, (Q1, Q2))
        self.assertEqual(result.coverage_count, 1)
        self.assertEqual(result.project_id, P)

    def test_qv_zero_selected_is_valid_event_not_a_query_set(self):
        event = QvReadyV1.parse(P, qv_event(0))
        data = qv_bundle(())
        self.assertEqual(QvBundleShapeV1.parse(event, data).query_ids, ())
        self.assertEqual(event.selected_count, 0)

    def test_qv_bundle_rejects_scope_and_modified_selection(self):
        event = QvReadyV1.parse(P, qv_event())
        b = qv_bundle()
        b["project_id"] = "f" * 32
        self.rejects("project_scope_mismatch", lambda: QvBundleShapeV1.parse(event, b))
        b = qv_bundle()
        b["queries"][0]["candidate_id"] = "12345"
        self.rejects("invalid_uuid", lambda: QvBundleShapeV1.parse(event, b))
        b = qv_bundle()
        b["decisions"][0]["decision"] = "rejected"
        self.rejects("event_integrity_mismatch", lambda: QvBundleShapeV1.parse(event, b))
        b = qv_bundle()
        b["queries"][0]["candidate_id"] = Q2
        self.rejects("event_integrity_mismatch", lambda: QvBundleShapeV1.parse(event, b))
        b = qv_bundle()
        b["embedding_mode"] = "test_fixture"
        self.rejects("source_not_verified", lambda: QvBundleShapeV1.parse(event, b))
        b = qv_bundle()
        b["schema_version"] = "validated_queries/v2"
        self.rejects("invalid_schema_version", lambda: QvBundleShapeV1.parse(event, b))
        b = qv_bundle()
        b["queries"][0]["query_mode"] = "unsupported"
        self.rejects("invalid_port_payload", lambda: QvBundleShapeV1.parse(event, b))
        b = qv_bundle()
        b["queries"][0]["query_mode"] = []
        self.rejects("invalid_port_payload", lambda: QvBundleShapeV1.parse(event, b))
        b = qv_bundle()
        b["coverage"][0]["selected_count"] = 0
        self.rejects("event_integrity_mismatch", lambda: QvBundleShapeV1.parse(event, b))

    def test_d1_identity_cannot_be_uuid_or_bool(self):
        for wrong in (Q1, True, 0, -2, 2**63):
            with self.subTest(wrong=wrong):
                self.rejects("identity_type_mismatch",
                             lambda: D1VideoRefV1(wrong, 3, 4))
        self.assertEqual(D1VideoRefV1(1, 2, 3).to_dict(),
                         {"video_id": 1, "discovery_id": 2, "source_metadata_id": 3})

    def test_discovery_event_is_idempotent_by_content(self):
        event = discovery()
        self.assertEqual(event.to_dict()["schema_version"], DISCOVERY_COMMITTED_EVENT)
        self.assertEqual(event.fingerprint, discovery().fingerprint)
        self.assertEqual(event.videos[0].video_id, 12)
        self.assertEqual(DiscoveryCommittedV1.parse(event.to_dict()), event)
        duplicated = event.to_dict()
        duplicated["videos"].append(dict(duplicated["videos"][0]))
        self.rejects("event_integrity_mismatch",
                     lambda: DiscoveryCommittedV1.parse(duplicated))
        oversized = event.to_dict()
        oversized["videos"] = oversized["videos"] * 101
        self.rejects("invalid_port_payload",
                     lambda: DiscoveryCommittedV1.parse(oversized))
        damaged = event.to_dict()
        damaged["schema_version"] = "collector.discovery_persisted/v2"
        self.rejects("invalid_schema_version", lambda: DiscoveryCommittedV1.parse(damaged))
        self.rejects("invalid_uuid", lambda: DiscoveryCommittedV1(
            P, 4, 5, 8, "dtk1234", T, 3, H, ("not-a-qv-uuid",), C, (),
        ))
        self.rejects("identity_type_mismatch", lambda: DiscoveryCommittedV1(
            P, 4, Q1, 8, "dtk1234", T, 3, H, (Q1,), C, (),
        ))

    def test_sre_handoff_not_evaluated_and_stable_delivery_key(self):
        event = SREReadyV1(P, 4, 5, D1VideoRefV1(12, 13, 14),
                           T, 3, H, (Q1,))
        payload = event.to_dict()
        self.assertEqual(payload["schema_version"], SRE_READY_EVENT)
        self.assertEqual(payload["relevance_state"], "not_evaluated")
        self.assertEqual(payload["evidence_scope"], "metadata_only")
        self.assertEqual(payload["video"]["video_id"], 12)
        self.assertRegex(payload["delivery_key"], r"^[0-9a-f]{64}$")
        self.assertEqual(payload["delivery_key"], event.to_dict()["delivery_key"])
        self.assertEqual(SREReadyV1.parse(payload), event)
        altered = event.to_dict()
        altered["relevance_state"] = "relevant"
        self.rejects("invalid_port_payload", lambda: SREReadyV1.parse(altered))
        altered = event.to_dict()
        altered["query_ids"] = [Q2]
        self.rejects("event_integrity_mismatch", lambda: SREReadyV1.parse(altered))
        altered = event.to_dict()
        altered["video"]["video_id"] = Q1
        self.rejects("identity_type_mismatch", lambda: SREReadyV1.parse(altered))
        self.rejects("identity_type_mismatch", lambda: SREReadyV1(
            P, 4, 5, D1VideoRefV1(Q1, 13, 14), T, 3, H, (Q1,)
        ))

    def test_no_side_effect_flags_are_separate_and_off_by_default(self):
        with patch("intelligence.config.setting", side_effect=lambda k, default="": default):
            flags = runtime_flags()
            self.assertFalse(flags.qv_consumer_enabled)
            self.assertFalse(flags.provider_dispatch_enabled)
            self.assertFalse(flags.sre_delivery_enabled)
        overrides = {"CM_COLLECTOR_QV_CONSUMER_ENABLED": "1",
                     "CM_COLLECTOR_LIVE_ENABLED": "0",
                     "CM_COLLECTOR_SRE_DELIVERY_ENABLED": "1"}
        with patch("intelligence.config.setting",
                   side_effect=lambda k, default="": overrides.get(k, default)):
            flags = runtime_flags()
            self.assertTrue(flags.qv_consumer_enabled)
            self.assertFalse(flags.provider_dispatch_enabled)
            self.assertTrue(flags.sre_delivery_enabled)

    def test_import_ports_never_opens_socket(self):
        # The separate subprocess avoids import caches hiding eager work.
        import subprocess
        import sys
        script = ("import socket\n"
                  "def deny(*a, **kw): raise AssertionError('network during import')\n"
                  "socket.create_connection = deny\n"
                  "socket.socket.connect = deny\n"
                  "from intelligence.collector import ports\n"
                  "assert ports.QV_BUNDLE_SCHEMA == 'validated_queries/v1'\n")
        proc = subprocess.run([sys.executable, "-c", script],
                              capture_output=True, text=True, timeout=20)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)

    def test_error_code_inventory(self):
        self.assertIn("event_integrity_mismatch", BOUNDARY_ERRORS)
        self.assertIn("identity_type_mismatch", BOUNDARY_ERRORS)


if __name__ == "__main__":
    unittest.main()
