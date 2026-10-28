"""M24 dedicated SRE consent PG migration schema/ownership protections."""
from __future__ import annotations
from pathlib import Path
import unittest

from intelligence.relevance.migration_guard import build_manifest,assess_applied

SQL=Path(__file__).resolve().parents[1]/"intelligence"/"migrations"/"059_sre_live_opus_consents.sql"


class M24ConsentSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sql=SQL.read_text(encoding="utf-8")

    def test_only_sre_exact_quote_fk_and_immutable_grant_reversible_only_to_revoked(self):
        s=self.sql
        self.assertIn("CREATE TABLE sre_live_opus_consents",s)
        self.assertIn("sre_m24_quote_composite_owner",s)
        self.assertIn("FOREIGN KEY(project_id,run_id,item_id,quote_sha256)",s)
        self.assertIn("REFERENCES sre_live_opus_quotes(project_id,run_id,item_id,quote_sha256)",s)
        self.assertIn("ON DELETE CASCADE",s)
        self.assertIn("UNIQUE(project_id,quote_sha256)",s)
        self.assertIn("sre_live_opus_paid_execution",s)
        self.assertIn("max_usd NUMERIC(24,9)",s)
        self.assertIn("NEW.max_usd <> pinned_max",s)
        self.assertIn("NEW.expires_at > quote_expires",s)
        self.assertIn("NEW.expires_at <= clock_timestamp()",s)
        self.assertIn("sre_m24_revocation_only",s)
        self.assertIn("to_jsonb(NEW)-'revoked_at'-'revoked_by'-'revoke_reason'",s)
        self.assertIn("OLD.revoked_at IS NOT NULL",s)
        self.assertIn("REVOKE ALL ON sre_live_opus_consents FROM PUBLIC",s)
        self.assertNotIn("INSERT INTO sre_live_attempt_fences",s)
        self.assertNotIn("UPDATE sre_live_runs",s)
        self.assertNotIn("consents_query",s)
        self.assertNotIn("sre_m24_no_delete",s)

    def test_next_schema_prefix_060_and_manifest_required(self):
        manifest=build_manifest()
        self.assertIn("059_sre_live_opus_consents.sql",manifest)
        self.assertIn("058_sre_live_opus_quotes.sql",manifest)
        self.assertEqual(assess_applied(None)["next_sre_migration_prefix"],"065")


if __name__=="__main__":
    unittest.main()
