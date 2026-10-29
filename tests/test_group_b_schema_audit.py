"""GB-MOD-01 secondary SQL-structure audit; no external deps or paid calls.

This independent test pins the MAIN-owned migration and checks actual table,
view, trigger and index count/identities. A vocabulary-only test cannot catch
accidentally removed triggers or renamed source-first FKs.
"""
from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

from intelligence.group_b import contracts as b

SQL_PATH = Path(__file__).resolve().parents[1] / "intelligence/migrations/005_content_features_b.sql"
ORIGINAL_GIT_BLOB = "e203aab384af65ae11e9960860bee4715bb21e4d"
EXPECTED_VIEWS = frozenset(("content_b_story_patterns", "content_b_cta_summary"))
EXPECTED_INDEXES = frozenset((
    "idx_b_video_versions", "idx_b_source_versions",
    "idx_b_ctas_type", "idx_b_ctas_destination", "idx_b_ctas_slide",
    "idx_b_ctas_time", "idx_b_topic_category", "idx_b_topic_topic",
    "idx_b_topic_subtopic",
))
EXPECTED_TRIGGERS = frozenset((
    "trg_b_validate_analysis", "trg_b_hooks_guard",
    "trg_b_hook_parts_guard", "trg_b_formats_guard", "trg_b_sampling_guard",
    "trg_b_frames_guard", "trg_b_intervals_guard", "trg_b_slides_guard",
    "trg_b_visual_guard", "trg_b_edit_guard", "trg_b_subs_guard",
    "trg_b_cta_analyses_guard", "trg_b_ctas_guard",
    "trg_b_topic_assignment_guard",
))


class GroupBSchemaAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = SQL_PATH.read_bytes()
        cls.sql = cls.raw.decode("utf-8")

    def test_sql_blob_is_original_main_owned_immutable_migration(self):
        hash_value = hashlib.sha1(
            b"blob " + str(len(self.raw)).encode("ascii") + b"\x00" + self.raw
        ).hexdigest()
        self.assertEqual(hash_value, ORIGINAL_GIT_BLOB)

    def test_all_tables_views_indexes_triggers_exact(self):
        tables = frozenset(re.findall(
            r"CREATE TABLE IF NOT EXISTS\s+(content_b_[a-z_]+)\s*\(", self.sql
        ))
        views = frozenset(re.findall(
            r"CREATE OR REPLACE VIEW\s+(content_b_[a-z_]+)", self.sql
        ))
        indexes = frozenset(re.findall(
            r"CREATE (?:UNIQUE )?INDEX IF NOT EXISTS\s+([a-z_]+)", self.sql
        ))
        triggers = frozenset(re.findall(
            r"CREATE OR REPLACE TRIGGER\s+(trg_b_[a-z_]+)", self.sql
        ))
        b_tables = {t for values in b.B_BLOCK_TABLES.values() for t in values}
        self.assertEqual(len(tables),17)
        self.assertEqual(tables, b_tables | {"content_b_analyses"})
        self.assertEqual(views,EXPECTED_VIEWS)
        self.assertEqual(indexes,EXPECTED_INDEXES)
        self.assertEqual(triggers,EXPECTED_TRIGGERS)

    def test_analysis_lineage_and_immutable_completion(self):
        self.assertIn("video_id BIGINT NOT NULL REFERENCES videos(id)", self.sql)
        self.assertIn("source_id TEXT NOT NULL REFERENCES sources(id)", self.sql)
        self.assertIn(
            "video_a_analysis_id BIGINT REFERENCES video_feature_a_analyses(id)",
            self.sql,
        )
        self.assertIn(
            "carousel_a_analysis_id BIGINT REFERENCES carousel_feature_a_analyses(id)",
            self.sql,
        )
        self.assertIn("source_policy_version_id BIGINT REFERENCES source_policy_versions(id)", self.sql)
        self.assertIn("OLD.state='completed'", self.sql)
        self.assertIn("Completed B analysis components are immutable", self.sql)
        self.assertIn("B media rights have been revoked", self.sql)
        self.assertIn("NEW.slide_index IS NOT NULL", self.sql)
        self.assertIn("NEW.start_sec IS NOT NULL", self.sql)

    def test_null_means_unknown_not_measured_negative(self):
        # SQL CHECK may allow NULL. Explicit review/coverage remains the
        # responsibility of forthcoming B runtime and validation gate.
        self.assertIn("ai_generated BOOLEAN",self.sql)
        self.assertIn("is_question BOOLEAN",self.sql)
        self.assertIn("before_after_phase TEXT CHECK",self.sql)
        self.assertIn("THEN EXISTS(",self.sql)
        self.assertIn("ELSE NULL END AS has_cta",self.sql)
        self.assertIn("analysis_status TEXT NOT NULL DEFAULT 'not_run'",self.sql)
        self.assertIn("needs_review BOOLEAN NOT NULL DEFAULT FALSE",self.sql)

    def test_no_destructive_catalog_migration_or_peer_import(self):
        lowered = self.sql.lower()
        for forbidden in ("drop table", "truncate ", "delete from videos",
                          "update videos set", "alter table videos",
                          "alter table topics"):
            self.assertNotIn(forbidden,lowered)
        from intelligence.group_b import contracts
        self.assertNotIn("intelligence.group_a", contracts.__dict__)
        self.assertNotIn("intelligence.qualified_data", contracts.__dict__)


if __name__=="__main__":
    unittest.main()
