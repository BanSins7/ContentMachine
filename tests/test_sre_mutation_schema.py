"""M39 immutable replay migration source and forward-only numbering guard."""
import unittest
from pathlib import Path

from intelligence.relevance.migration_guard import assess_applied,build_manifest

ROOT=Path(__file__).resolve().parents[1]
FILE=ROOT/"intelligence"/"migrations"/"071_sre_mutation_request_receipts.sql"


class MutationMigrationTests(unittest.TestCase):
    def test_071_is_required_and_next_prefix_072(self):
        self.assertIn("071_sre_mutation_request_receipts.sql",build_manifest())
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"072")

    def test_pending_claim_fences_concurrent_and_in_doubt_replay(self):
        text=FILE.read_text(encoding="utf-8")
        for fragment in (
            "CREATE TABLE sre_m39_mutation_receipts",
            "PRIMARY KEY (project_id,actor_id,operation,request_key)",
            "request_sha256 TEXT NOT NULL",
            "state TEXT NOT NULL DEFAULT 'pending'",
            "CHECK (state IN ('pending','committed'))",
            "claimed_at TIMESTAMPTZ NOT NULL",
            "CREATE FUNCTION sre_m39_mutation_receipt_guard()",
            "IF OLD.state<>'pending' OR NEW.state<>'committed'",
            "NEW.request_sha256 IS DISTINCT FROM OLD.request_sha256",
            "CREATE TRIGGER sre_m39_mutation_receipt_immutable",
            "REVOKE ALL ON sre_m39_mutation_receipts FROM PUBLIC",
        ):
            self.assertIn(fragment,text)
        self.assertNotIn("CREATE OR REPLACE",text)
        self.assertNotIn("DROP TABLE",text)

    def test_071_stores_only_bounded_minimal_receipts(self):
        source=FILE.read_text(encoding="utf-8")
        self.assertIn("octet_length(result::text)<=4096",source)
        self.assertNotIn("api_key",source)
        self.assertNotIn("provider_response",source)
        self.assertNotIn("raw_transcript",source)

    def test_m39_does_not_rewrite_historical_migrations(self):
        for name in ("016_sre_d1_intake_rights.sql",
                     "062_sre_live_opus_receipts.sql",
                     "070_sre_live_result_outbox.sql"):
            self.assertIn(name,build_manifest())


if __name__=="__main__":
    unittest.main()
