"""FIX-05 contract-level tests: SRE event is a pointer, never model results."""
from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from intelligence.qualified_data.contracts import QDPError, sha256
from intelligence.qualified_data.sre_inbox import (
    COMPLETED, VERSION, validate_completion_event, _event_id_only,
)


def sample_event(**edits):
    payload = {
        "schema_version": VERSION,
        "event_id": str(uuid.uuid4()),
        "event_type": COMPLETED,
        "project_id": "c" * 32,
        "run_id": str(uuid.uuid4()),
        "publication_id": str(uuid.uuid4()),
        "taxonomy_version_id": 42,
        "occurred_at": "2026-10-26T08:30:00+00:00",
    }
    payload.update(edits)
    return payload


class SreCompletionContractTests(unittest.TestCase):
    def test_matches_sre_run_summary_v1(self):
        event = sample_event()
        checked = validate_completion_event(event)
        self.assertEqual(checked, event)
        self.assertEqual(sha256(event), sha256(dict(reversed(list(event.items())))))
        self.assertIsNot(checked, event)

    def test_never_accepts_item_decisions_or_client_credentials(self):
        for edits in ({"decisions": {"match": []}}, {"video_id": 3},
                      {"authorized_usd": "100.00"}, {"labels": ["match"]},
                      {"run_status": "completed"}, {"execution_mode": "live"}):
            with self.subTest(edits=edits), self.assertRaises(QDPError):
                validate_completion_event(sample_event(**edits))

    def test_rejects_wrong_event_kind_and_schema(self):
        for edits in ({"event_type": "sre.classification_item_completed"},
                      {"event_type": "sre.classifications_ready"},
                      {"event_type": "sre.classification_invalidated"},
                      {"schema_version": "sre-analytics-event/v2"}):
            with self.subTest(edits=edits), self.assertRaises(QDPError):
                validate_completion_event(sample_event(**edits))

    def test_rejects_foreign_uuid_shapes_and_noncanonical(self):
        for edits in ({"event_id": "nope"},
                      {"run_id": str(uuid.uuid4()).upper()},
                      {"publication_id": str(uuid.uuid4()).replace("-", "")},
                      {"project_id": "not-project"},
                      {"taxonomy_version_id": True},
                      {"taxonomy_version_id": 0}):
            with self.subTest(edits=edits), self.assertRaises(QDPError):
                validate_completion_event(sample_event(**edits))

    def test_rejects_invalid_timestamps(self):
        for raw in ("2026-10-26", "yesterday", "2026-10-26T08:00:00",
                    "2026-10-26T08:00:00X"):
            with self.subTest(raw=raw), self.assertRaises(QDPError):
                validate_completion_event(sample_event(occurred_at=raw))

    def test_missing_fields_are_not_auto_repaired(self):
        for key in sample_event():
            payload = sample_event()
            payload.pop(key)
            with self.subTest(key=key), self.assertRaises(QDPError):
                validate_completion_event(payload)

    def test_only_event_uuid_may_be_used_for_conflict_audit(self):
        eid = str(uuid.uuid4())
        self.assertEqual(_event_id_only({"event_id": eid}), eid)
        with self.assertRaises(QDPError):
            _event_id_only({"event_id": "not-a-uuid"})

    def test_no_external_sre_code_import_needed(self):
        import sys
        self.assertNotIn("intelligence.relevance.ports.outbound",
                         sys.modules)


if __name__ == "__main__":
    unittest.main()
