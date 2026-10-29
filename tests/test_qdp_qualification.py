"""FIX-04 deterministic QDP resolver: no DB, provider or synthetic labels."""
import copy
import unittest
from datetime import datetime, timezone
from uuid import NAMESPACE_URL, uuid5

from intelligence.qualified_data.contracts import QDPError, qualify_sre_rows
from intelligence.qualified_data.qualification import resolve_qualified_set


A = str(uuid5(NAMESPACE_URL, "qdp-fixture/a"))
B = str(uuid5(NAMESPACE_URL, "qdp-fixture/b"))
C = str(uuid5(NAMESPACE_URL, "qdp-fixture/c"))
STAMP = datetime(2026, 10, 26, 7, 0, tzinfo=timezone.utc)


def row(video, key, decision="no_match", result_id=1, **changes):
    result = {
        "video_id": video, "category_key": key, "result_id": result_id,
        "model_decision": decision, "effective_decision": decision,
        "content_version_id": video + 100,
        "result_content_sha256": "a" * 64,
        "content_readable": True,
        "human_label_id": None, "human_label_decision": None,
        "review_created_at": None,
    }
    result.update(changes)
    return result


class QualifiedSetResolverTests(unittest.TestCase):
    def test_one_positive_of_twenty_is_one_video(self):
        keys = [str(uuid5(NAMESPACE_URL, "qualified/leaf/" + str(i)))
                for i in range(20)]
        rows = [row(3, k, "match" if i == 7 else "no_match",
                    result_id=i + 1) for i, k in enumerate(keys)]
        obj = resolve_qualified_set(rows, strict=True)
        self.assertEqual(obj["totals"],
                         {"qualified": 1, "needs_review": 0, "excluded": 0})
        self.assertEqual(obj["qualified"][0]["category_keys"], [keys[7]])
        self.assertEqual(obj["qualified"][0]["result_ids"], [8])
        self.assertEqual(len(obj["details"][0]["pairs"]), 20)

    def test_match_plus_uncertain_still_qualifies(self):
        result = resolve_qualified_set([row(1, A, "match"),
                                       row(1, B, "uncertain", result_id=2)],
                                       strict=True)
        self.assertEqual(result["qualified"][0]["category_keys"], [A])

    def test_all_no_match_excluded_all_uncertain_review(self):
        items = [row(1, A), row(1, B, result_id=2),
                 row(2, A, "uncertain", result_id=3),
                 row(2, B, "not_evaluated", result_id=4)]
        obj = resolve_qualified_set(items, strict=True)
        self.assertEqual(obj["excluded"], [1])
        self.assertEqual(obj["needs_review"], [2])

    def test_unreadable_match_goes_to_review_not_excluded(self):
        obj = resolve_qualified_set([row(2, A, "match",
                                          content_readable=False),
                                     row(2, B, "no_match", result_id=2)],
                                    strict=True)
        self.assertEqual(obj["needs_review"], [2])
        self.assertEqual(obj["excluded"], [])

    def test_human_no_match_veto_only_one_leaf(self):
        orig = [row(4, A, "match", result_id=8),
                row(4, B, "match", result_id=9)]
        reviewed = copy.deepcopy(orig)
        reviewed[0].update(human_label_id=300, human_label_decision="no_match",
                           effective_decision="no_match",
                           review_created_at=STAMP)
        before = resolve_qualified_set(orig, strict=True)
        after = resolve_qualified_set(reviewed, strict=True)
        self.assertEqual(after["qualified"][0]["category_keys"], [B])
        self.assertEqual(after["qualified"][0]["result_ids"], [9])
        self.assertNotEqual(before["review_watermark"], after["review_watermark"])
        self.assertNotEqual(before["selection_sha256"], after["selection_sha256"])
        self.assertEqual(reviewed[0]["model_decision"], "match")

    def test_last_human_veto_excludes_and_review_uncertain(self):
        arr = [row(4, A, "match",
                   human_label_id=200, human_label_decision="no_match",
                   effective_decision="no_match", review_created_at=STAMP),
               row(4, B, "no_match", result_id=2)]
        self.assertEqual(resolve_qualified_set(arr, strict=True)["excluded"], [4])
        arr[1]["effective_decision"] = "uncertain"
        arr[1]["human_label_id"] = 201
        arr[1]["human_label_decision"] = "uncertain"
        arr[1]["review_created_at"] = STAMP
        self.assertEqual(resolve_qualified_set(arr, strict=True)["needs_review"], [4])

    def test_human_promotes_negative_only_with_readable_pinned_content(self):
        reviewed = row(9, A, "no_match",
                       human_label_id=33, human_label_decision="match",
                       effective_decision="match", review_created_at=STAMP)
        self.assertEqual(resolve_qualified_set([reviewed], strict=True)
                         ["qualified"][0]["video_id"], 9)
        reviewed["content_readable"] = False
        self.assertEqual(resolve_qualified_set([reviewed], strict=True)
                         ["needs_review"], [9])

    def test_shuffled_rows_reproducible_and_human_revision_changes_hash(self):
        input_rows = [row(8, B, "match", result_id=12),
                      row(1, A, "no_match", result_id=11),
                      row(8, A, "no_match", result_id=13)]
        a = resolve_qualified_set(input_rows, strict=True)
        b = resolve_qualified_set(list(reversed(input_rows)), strict=True)
        self.assertEqual(a["selection_sha256"], b["selection_sha256"])
        self.assertEqual(a["review_watermark"], b["review_watermark"])
        v = copy.deepcopy(input_rows)
        v[2].update(human_label_id=54, human_label_decision="no_match",
                    review_created_at=STAMP)
        c = resolve_qualified_set(v, strict=True)
        self.assertNotEqual(c["selection_sha256"], a["selection_sha256"])

    def test_duplicate_pair_fails_and_unknown_is_not_negative(self):
        data = row(2, A)
        with self.assertRaises(QDPError) as duplicate:
            resolve_qualified_set([data, dict(data, result_id=2)], strict=True)
        self.assertEqual(duplicate.exception.code, "sre_duplicate_result")
        with self.assertRaises(QDPError):
            resolve_qualified_set([dict(data, effective_decision="provider_error")],
                                  strict=True)

    def test_malformed_scope_decision_and_human_provenance_fail(self):
        base = row(10, A)
        for patch in (
            {"video_id": 0}, {"video_id": True}, {"result_id": 0},
            {"category_key": "not-uuid"}, {"content_readable": None},
            {"content_version_id": None}, {"result_content_sha256": "bad"},
            {"human_label_id": 5, "human_label_decision": "match",
             "effective_decision": "match", "review_created_at": None},
            {"effective_decision": "match"},
            {"human_label_decision": "match"},
        ):
            with self.subTest(patch=patch), self.assertRaises(QDPError):
                resolve_qualified_set([dict(base, **patch)], strict=True)

    def test_wrapper_retains_compact_legacy_api(self):
        got = qualify_sre_rows([row(1, "a", "match"),
                                row(1, "b", "no_match", result_id=2)])
        self.assertEqual(set(got), {"qualified", "needs_review",
                                     "excluded", "totals"})
        self.assertEqual(got["qualified"][0]["category_keys"], ["a"])

    def test_zero_unverified_results_not_excluded(self):
        with self.assertRaises(QDPError):
            resolve_qualified_set([], strict=True)


if __name__ == "__main__":
    unittest.main()
