"""RTB-FIX-01 real PostgreSQL17 integration with B8/B9 approved publication.

Reuses B's existing synthetic Stage13 test fixture; no external provider,
credentials, network/video downloads, or paid calls. Dedicated RTB test module.
"""
from __future__ import annotations

import unittest

from test_ke_stage02_intake_db import DSN
from test_ke_stage13_knowledge_snapshot import Stage13PG
from intelligence.taxonomy_builder.research_input import RTBResearchInputService


@unittest.skipUnless(DSN, "PostgreSQL17 required for read-only RTB B9 research input")
class RTBResearchInputPG(unittest.TestCase):
    def test_real_db_approved_b9_export_to_immutable_research_input(self):
        fixture = Stage13PG(methodName="runTest")
        try:
            fixture.setUp()
            session_id = fixture._published()
            repo = fixture._repo()
            saved = repo.publish(fixture.pid, session_id, fixture.reviewer)
            handoff = repo.prepare_c_handoff(fixture.pid, session_id, fixture.reviewer)
            source = RTBResearchInputService(connection_factory=fixture._factory())
            first = source.preview(fixture.pid, saved["snapshot_id"], fixture.reviewer)
            second = source.preview(fixture.pid, saved["snapshot_id"], fixture.reviewer)
            self.assertEqual(first["b9_snapshot_id"], saved["snapshot_id"])
            self.assertEqual(first["b9_snapshot_sha256"], saved["snapshot_sha256"])
            self.assertEqual(first["b9_handoff_id"], handoff["handoff_id"])
            self.assertEqual(first["research_input_sha256"], second["research_input_sha256"])
            self.assertGreater(first["fact_count"], 0)
            self.assertEqual(first["video_count"], 0)
            self.assertEqual(first["provider_calls"], 0)
            self.assertFalse(first["paid_execution_authorized"])
            self.assertTrue(all(
                fact["may_support_own_product_claim"] is False
                for fact in first["c1_approved_facts"]
            ))
            with self.assertRaises(ValueError):
                source.preview(fixture.other, saved["snapshot_id"], fixture.reviewer)
            with self.assertRaises(ValueError):
                source.preview(fixture.pid, saved["snapshot_id"], "not-authorized")
        finally:
            fixture.doCleanups()


if __name__ == "__main__":
    unittest.main()
