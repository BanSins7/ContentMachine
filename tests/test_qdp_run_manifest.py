"""FIX-06 immutable QDP manifest and exact canonical SRE evidence refs."""
import copy
import unittest
import uuid
from intelligence.qualified_data.contracts import QDPError, options, sha256
from intelligence.qualified_data.qualification import resolve_qualified_set
from intelligence.qualified_data.run_manifest import (
    VERSION, build_run_manifest, read_run_manifest, read_pinned_video,
)

PID = "a" * 32
RUN = str(uuid.uuid4())
CATEGORIES = [str(uuid.uuid4()) for _ in range(3)]


def fixture(videos=2, state="match"):
    items = []
    rows = []
    for i in range(1, videos + 1):
        keys = CATEGORIES[:]
        items.append({"video_id": i, "content_version_id": i + 100,
                      "content_sha256": "b" * 64, "hypothesis_keys": keys})
        for j, category in enumerate(keys):
            rows.append({
                "video_id": i, "result_id": i * 10 + j + 1,
                "category_key": category, "content_version_id": 100 + i,
                "result_content_sha256": "b" * 64,
                "model_decision": state if j == 0 else "no_match",
                "effective_decision": state if j == 0 else "no_match",
                "content_readable": True, "evidence": [{
                    "source": "title", "quote": "real DB title"}] if j == 0 else [],
                "human_label_id": None,
                "human_label_decision": None,
                "review_created_at": None,
                "source_id": "collector-source-a",
                "source_policy_version_id": 51,
            })
    source = {
        "id": uuid.UUID(RUN), "project_id": PID,
        "taxonomy_version_id": 88,
        "dataset_snapshot": {
            "schema_version": "sre-run-plan/v1",
            "project_id": PID, "run_id": RUN,
            "taxonomy_version_id": 88, "taxonomy_sha256": "e" * 64,
            "items": items,
        },
    }
    return source, rows


class PinnedManifestTests(unittest.TestCase):
    def make(self, videos=2, state="match"):
        source, rows = fixture(videos, state)
        selection = resolve_qualified_set(rows, strict=True)
        return build_run_manifest(PID, RUN, source, selection, options())

    def test_manifest_pins_exact_qualified_set(self):
        pinned = self.make()
        self.assertEqual(pinned["expected_videos"], 2)
        self.assertEqual(pinned["expected_pairs"], 6)
        self.assertEqual(len(pinned["lineage"]), 6)
        self.assertEqual(pinned["manifest"]["schema_version"], VERSION)
        self.assertEqual(pinned["manifest"]["totals"]["qualified"], 2)
        self.assertEqual(pinned["manifest"]["by_video"]["1"]["source_ids"],
                         ["collector-source-a"])
        self.assertEqual(pinned["manifest"]["by_video"]["1"]
                         ["source_policy_version_ids"], [51])
        self.assertEqual(pinned["manifest"]["config_sha256"], sha256(options()))
        self.assertEqual(pinned["manifest_sha256"], sha256(pinned["manifest"]))
        self.assertTrue(all(x["evidence_sha256"] for x in pinned["lineage"]))

    def test_stable_across_video_and_leaf_order(self):
        source, rows = fixture(3)
        a = build_run_manifest(PID, RUN, source,
                               resolve_qualified_set(rows, strict=True), options())
        copy_rows = list(reversed(rows))
        copy_source = copy.deepcopy(source)
        copy_source["dataset_snapshot"]["items"].reverse()
        b = build_run_manifest(PID, RUN, copy_source,
                               resolve_qualified_set(copy_rows, strict=True), options())
        self.assertEqual(a["manifest_sha256"], b["manifest_sha256"])
        self.assertEqual(a["lineage"], b["lineage"])

    def test_zero_qualified_yet_nonempty_verified_input(self):
        pinned = self.make(videos=2, state="no_match")
        self.assertEqual(pinned["manifest"]["totals"],
                         {"qualified": 0, "needs_review": 0, "excluded": 2})
        self.assertEqual(pinned["expected_videos"], 2)
        self.assertEqual(len(pinned["lineage"]), 6)

    def test_human_revision_changes_snapshot(self):
        source, rows = fixture(1)
        initial = build_run_manifest(PID, RUN, source,
                                     resolve_qualified_set(rows, strict=True), options())
        changed = copy.deepcopy(rows)
        changed[0].update(human_label_id=123,
                          human_label_decision="no_match",
                          effective_decision="no_match",
                          review_created_at="2026-10-26T10:00:00+00:00")
        reviewed = build_run_manifest(PID, RUN, source,
                                      resolve_qualified_set(changed, strict=True), options())
        self.assertNotEqual(initial["manifest_sha256"], reviewed["manifest_sha256"])
        self.assertEqual(reviewed["manifest"]["totals"]["qualified"], 0)
        self.assertEqual(reviewed["lineage"][0]["human_label_id"], 123)

    def test_wrong_content_sha_or_missing_video_fails_closed(self):
        source, rows = fixture(2)
        for edit in ("missing", "wrong_sha"):
            wrong = copy.deepcopy(rows)
            if edit == "missing":
                wrong = wrong[:-3]
            else:
                wrong[0]["result_content_sha256"] = "f" * 64
            with self.subTest(edit=edit), self.assertRaises(QDPError):
                build_run_manifest(PID, RUN, source,
                                   resolve_qualified_set(wrong, strict=True), options())

    def test_rejects_empty_SRE_input_not_empty_qualified(self):
        source, rows = fixture(2)
        source["dataset_snapshot"]["items"] = []
        with self.assertRaises(QDPError):
            build_run_manifest(PID, RUN, source,
                               resolve_qualified_set(rows, strict=True), options())

    def test_manifest_read_rejects_modified_db_hash(self):
        pinned = self.make(videos=1)
        class DB:
            def execute(self, sql, args):
                class Cursor:
                    def fetchone(self_inner):
                        return {"manifest": pinned["manifest"],
                                "manifest_sha256": "0" * 64,
                                "selected_sha256": pinned["selected_sha256"]}
                return Cursor()
        with self.assertRaises(QDPError) as ctx:
            read_run_manifest(DB(), PID, RUN)
        self.assertEqual(ctx.exception.code, "qdp_manifest_integrity")

    def test_pinned_video_missing_fails_closed(self):
        class DB:
            def execute(self, sql, args):
                class Cursor:
                    def fetchone(self_inner):
                        return None
                return Cursor()
        with self.assertRaises(QDPError):
            read_pinned_video(DB(), PID, RUN, 1)

    def test_explicit_10000_video_cap(self):
        # Manifest verification guards full upstream dataset, not just
        # number of qualified videos; no silent truncation.
        source, rows = fixture(2)
        source["dataset_snapshot"]["items"] = (
            source["dataset_snapshot"]["items"] * 5001)
        with self.assertRaises(QDPError):
            build_run_manifest(PID, RUN, source,
                               resolve_qualified_set(rows, strict=True), options())


if __name__ == "__main__":
    unittest.main()
