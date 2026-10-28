"""GB-MOD-01: frozen vocabularies, source-first semantics and fail-closed guards.

Pure unit tests never call QDP/A/provider or a database. SQL freeze is a static
regression check against the authoritative pre-existing migration 005.
"""
from __future__ import annotations

import hashlib
import re
import unittest
from pathlib import Path

from intelligence.group_b import ContractError, content_type_for_kind
from intelligence.group_b import contracts as b

ROOT = Path(__file__).resolve().parents[1]
SQL = ROOT / "intelligence/migrations/005_content_features_b.sql"
SQL_GIT_BLOB_SHA = "e203aab384af65ae11e9960860bee4715bb21e4d"


def assert_error(test, code, func, *args, **kwargs):
    with test.assertRaises(ContractError) as caught:
        func(*args, **kwargs)
    test.assertEqual(caught.exception.code, code)


class TestGroupBFrozenContracts(unittest.TestCase):
    def test_exact_vocab_sizes_and_isolation(self):
        self.assertEqual(tuple(map(len, (b.HOOK_MECHANISMS, b.HOOK_DELIVERY,
                        b.VIDEO_FORMATS, b.CAROUSEL_FORMATS, b.STORY_ROLES,
                        b.CTA_TYPES, b.CTA_DESTINATIONS))),
                         (10, 4, 12, 8, 26, 12, 9))
        self.assertEqual(set(b.B_BLOCK_TABLES), {f"B{i}" for i in range(1, 9)})
        tables = [table for group in b.B_BLOCK_TABLES.values() for table in group]
        self.assertEqual(len(tables), len(set(tables)))
        self.assertNotIn("image_post", ("video", "slideshow_video",
                                        "photo_carousel", "mixed_carousel"))
        assert_error(self, "unsupported_content_kind", content_type_for_kind, "image_post")
        assert_error(self, "unsupported_content_kind", content_type_for_kind, "UNKNOWN")
        self.assertEqual(content_type_for_kind("slideshow_video"), "video")
        self.assertEqual(content_type_for_kind("mixed_carousel"), "carousel")

    def test_labels_unknown_duplicates_null_and_boolean_are_rejected(self):
        self.assertEqual(b.validate_unique_labels(["problem", "proof"],
                         b.HOOK_MECHANISMS, field="mechanisms"), ("problem", "proof"))
        for bad in (["problem", "problem"], ["problem", "bogus"],
                    [None], None, "problem", [True]):
            assert_error(self, "invalid_mechanisms", b.validate_unique_labels,
                         bad, b.HOOK_MECHANISMS, field="mechanisms")
        b.validate_b1(mechanisms=["problem", "promise"],
                      delivery=["speech", "visual_action"],
                      classification_status="inferred", is_question=None)
        assert_error(self, "invalid_is_question", b.validate_b1,
                     mechanisms=[], delivery=[], classification_status="unknown",
                     is_question=0)
        assert_error(self, "invalid_delivery", b.validate_b1,
                     mechanisms=[], delivery=["speech", "speech"],
                     classification_status="unknown")

    def test_seconds_and_slides_are_distinct(self):
        b.validate_placement("video", start_sec=0, end_sec="5.25")
        b.validate_placement("video")  # Storage supports NULL; not proof of evidence.
        b.validate_placement("carousel", slide_index=1)
        for kwargs in ({"content_type":"video", "slide_index":1},
                       {"content_type":"carousel", "start_sec":0, "slide_index":1},
                       {"content_type":"carousel", "slide_index":0},
                       {"content_type":"carousel", "slide_index":None},
                       {"content_type":"video", "start_sec":True},
                       {"content_type":"video", "start_sec":float("nan")},
                       {"content_type":"video", "start_sec":float("inf")},
                       {"content_type":"video", "start_sec":-1},
                       {"content_type":"video", "start_sec":3, "end_sec":3},
                       {"content_type":"video", "start_sec":"bad"}):
            with self.subTest(kwargs=kwargs):
                assert_error(self, "invalid_position", b.validate_placement, **kwargs)

    def test_b2_no_cross_type_or_duplicate_primary_secondary(self):
        b.validate_b2("video", video_primary="talking_head",
                      video_secondary="demonstration", classification_status="inferred")
        b.validate_b2("carousel", carousel_format="listicle", classification_status="inferred")
        b.validate_b2("video", classification_status="unknown")
        for content_type, kwargs, code in (
            ("video", {"carousel_format":"listicle"}, "invalid_b2_format"),
            ("carousel", {"video_primary":"skit"}, "invalid_b2_format"),
            ("video", {"video_primary":"skit", "video_secondary":"skit"}, "invalid_b2_format"),
            ("video", {"video_secondary":"skit"}, "invalid_b2_format"),
            ("video", {"classification_status":"inferred"}, "invalid_b2_format"),
            ("carousel", {"classification_status":"inferred"}, "invalid_b2_format"),
            ("video", {"video_primary":"invented"}, "invalid_video_primary"),
            ("carousel", {"carousel_format":"tutorial"}, "invalid_carousel_format"),
        ):
            with self.subTest(content_type=content_type, kwargs=kwargs):
                assert_error(self, code, b.validate_b2, content_type, **kwargs)

    def test_b3_phase_role_and_review(self):
        b.validate_b3_role("before_after", before_after_phase="before")
        b.validate_b3_role("other", needs_review=True)
        assert_error(self, "invalid_story_role", b.validate_b3_role, "transition")
        assert_error(self, "review_required", b.validate_b3_role, "other")
        assert_error(self, "invalid_before_after_phase", b.validate_b3_role,
                     "hook", before_after_phase="before")
        assert_error(self, "invalid_before_after_phase", b.validate_b3_role,
                     "before_after", before_after_phase="guessed")
        assert_error(self, "invalid_review_state", b.validate_b3_role, "hook", needs_review=1)

    def test_b4_nonexistent_visual_and_unverified_ai_provenance(self):
        b.validate_b4(ai_generated=None, person_visible=False,
                      product_visible=False, logo_visible=True, visual_evidence_present=True)
        assert_error(self, "missing_visual_evidence", b.validate_b4,
                     person_visible=False, product_visible=False, logo_visible=False)
        assert_error(self, "unverified_ai_origin", b.validate_b4,
                     ai_generated=False, person_visible=True, product_visible=False,
                     logo_visible=False, visual_evidence_present=True)
        assert_error(self, "invalid_person_visible", b.validate_b4,
                     person_visible=None, product_visible=False,
                     logo_visible=False, visual_evidence_present=True)

    def test_b7_unknown_not_false_and_repeated_cta(self):
        self.assertIsNone(b.cta_presence("not_run", 0))
        self.assertIsNone(b.cta_presence("partial", 2))
        self.assertIsNone(b.cta_presence("failed", 0))
        self.assertFalse(b.cta_presence("complete", 0))
        self.assertTrue(b.cta_presence("complete", 2))
        b.validate_b7(cta_type="follow", delivery=["speech"], destination_type="profile")
        assert_error(self, "invalid_cta_type", b.validate_b7,
                     cta_type="none", delivery=["speech"], destination_type="profile")
        assert_error(self, "invalid_cta_delivery", b.validate_b7,
                     cta_type="dm", delivery=["speech", "speech"], destination_type="dm")
        assert_error(self, "invalid_destination_type", b.validate_b7,
                     cta_type="dm", delivery=["speech"], destination_type="unverified-link")
        assert_error(self, "invalid_cta_count", b.cta_presence, "complete", True)
        assert_error(self, "invalid_destination", b.validate_b7,
                     cta_type="follow", delivery=["speech"], destination_type="profile",
                     destination_other_desc="unapproved")


class TestGroupBSchema005Drift(unittest.TestCase):
    def test_pinned_git_blob_and_all_tables(self):
        raw = SQL.read_bytes()
        actual = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        self.assertEqual(actual, SQL_GIT_BLOB_SHA,
                         "Migration 005 changed: review contract explicitly; do not rewrite it")
        sql = raw.decode("utf-8")
        for table in ("content_b_analyses", *[t for group in b.B_BLOCK_TABLES.values() for t in group]):
            self.assertRegex(sql, rf"CREATE TABLE IF NOT EXISTS\s+{table}\s*\(")
        for view in ("content_b_story_patterns", "content_b_cta_summary"):
            self.assertIn(f"CREATE OR REPLACE VIEW {view}", sql)
        for trigger in ("trg_b_validate_analysis", "trg_b_hooks_guard", "trg_b_formats_guard",
                        "trg_b_ctas_guard", "trg_b_topic_assignment_guard"):
            self.assertIn(trigger, sql)

    def test_vocab_exact_match_to_SQL_not_just_subset(self):
        sql = SQL.read_text(encoding="utf-8")
        def values(expr):
            match = re.search(expr, sql, re.DOTALL)
            self.assertIsNotNone(match, f"SQL expression missing: {expr}")
            return tuple(re.findall(r"'([^']+)'", match.group(1)))
        for name, expression, expected in (
            ("mechanisms", r"CHECK\(b_valid_labels\(mechanisms,ARRAY\[(.*?)\]::TEXT\[\]\)\)", b.HOOK_MECHANISMS),
            ("hook delivery", r"CHECK\(b_valid_labels\(delivery,ARRAY\[(.*?)\]::TEXT\[\]\)\)", b.HOOK_DELIVERY),
            ("video format", r"video_primary TEXT CHECK\(video_primary IS NULL OR video_primary IN \((.*?)\)\)", b.VIDEO_FORMATS),
            ("carousel format", r"carousel_format TEXT CHECK\(carousel_format IS NULL OR carousel_format IN \((.*?)\)\)", b.CAROUSEL_FORMATS),
            ("story roles", r"content_b_story_frames.*?role TEXT NOT NULL CHECK\(role IN \((.*?)\)\)", b.STORY_ROLES),
            ("editing", r"editing_type TEXT NOT NULL CHECK\(editing_type IN \((.*?)\)\)", b.EDITING_TYPES),
            ("subtitles", r"subtitle_type TEXT NOT NULL CHECK\(subtitle_type IN \((.*?)\)\)", b.SUBTITLE_TYPES),
            ("CTA types", r"cta_type TEXT NOT NULL CHECK\(cta_type IN \((.*?)\)\)", b.CTA_TYPES),
            ("CTA destinations", r"destination_type TEXT NOT NULL CHECK\(destination_type IN \((.*?)\)\)", b.CTA_DESTINATIONS),
        ):
            with self.subTest(vocabulary=name):
                self.assertEqual(values(expression), expected)
        self.assertEqual(values(r"analysis_status IN \((.*?)\)\)"), b.CTA_SCAN_STATES)
        self.assertEqual(values(r"destination_service TEXT CHECK\(destination_service IS NULL OR destination_service IN \((.*?)\)\)"), b.CTA_SERVICES)
        self.assertEqual(values(r"before_after_phase TEXT CHECK\(before_after_phase IS NULL OR before_after_phase IN \((.*?)\)\)"), b.BEFORE_AFTER_PHASES)
