"""Static SQL guard for RTB-owned migrations, runs without PostgreSQL.

PostgreSQL17 migration/integration remains authoritative; this catches
unbalanced PL/pgSQL dollar delimiters before a runner executes SQL.
"""
from pathlib import Path
import re
import unittest

MIGRATIONS = Path(__file__).resolve().parents[1] / "intelligence" / "migrations"
FILES = [
    "054_tb_deep_v2_research_binding.sql",
    "055_tb_deep_v2_budget_ledger.sql",
    "056_tb_deep_v2_human_reviews.sql",
    "057_tb_deep_v2_expansion_consent.sql",
]


class RTBDeepV2SQLStatic(unittest.TestCase):
    def test_all_trigger_functions_have_balanced_dollar_quoting(self):
        for filename in FILES:
            with self.subTest(filename=filename):
                sql = (MIGRATIONS / filename).read_text(encoding="utf-8")
                opening = len(re.findall(
                    r"LANGUAGE\s+plpgsql\s+AS\s+\$\$", sql, re.I))
                endings = len(re.findall(r"END\s+\$\$\s*;", sql, re.I))
                self.assertGreater(opening, 0)
                self.assertEqual(opening, endings)
                self.assertEqual(sql.count("$$"), 2 * opening)
                self.assertNotRegex(sql, r"LANGUAGE\s+plpgsql\s+AS\s+\$(?!\$)")
                self.assertNotRegex(sql, r"END\s+\$(?!\$)\s*;")

    def test_deep_v2_ledger_contains_one_attempt_per_stage_and_no_legacy_mutations(self):
        sql = (MIGRATIONS / FILES[1]).read_text(encoding="utf-8")
        for required in (
            "UNIQUE(project_id,run_id,stage)",
            "tb_deep_v2_check_run_insert",
            "tb_deep_v2_check_attempt_insert",
            "tb_deep_v2_revocations",
            "tb_deep_v2_run_update_guard",
            "tb_deep_v2_attempt_update_guard",
        ):
            self.assertIn(required, sql)
        self.assertNotRegex(
            sql, r"(?i)(?:alter|drop|truncate)\s+table\s+(?:tb_runs|tb_deep_runs|knowledge_c_generation_runs)\b")

    def test_expansion_requires_two_separate_consents_and_auditable_budget(self):
        sql = (MIGRATIONS / FILES[3]).read_text(encoding="utf-8")
        for clause in (
            "expansion_no BETWEEN 1 AND 2",
            "UNIQUE(project_id,run_id,expansion_no)",
            "tb_deep_v2_expansion_grant",
            "CREATE OR REPLACE FUNCTION tb_deep_v2_run_guard",
            "tb_deep_v2_expansion_revocations",
            "CREATE OR REPLACE FUNCTION tb_deep_v2_check_attempt_insert",
            "eq.request_sha256=NEW.request_sha256",
        ):
            self.assertIn(clause, sql)
        self.assertNotRegex(
            sql, r"(?i)(?:alter|drop|truncate)\\s+table\\s+"
                 r"(?:tb_runs|tb_deep_runs|knowledge_c_generation_runs)\\b")

    def test_human_review_is_append_only_and_state_guarded(self):
        sql = (MIGRATIONS / FILES[2]).read_text(encoding="utf-8")
        for term in (
            "UNIQUE(project_id,run_id,review_no)",
            "CREATE TRIGGER tb_deep_v2_review_immutable",
            "tb_deep_v2_review_guard",
            "review_no IN (1,2)",
            "a.stage='deep_first' AND a.status='completed'",
        ):
            self.assertIn(term, sql)


if __name__ == "__main__":
    unittest.main()
