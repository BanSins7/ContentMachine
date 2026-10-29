"""QDP-FIX-02: deterministic SRE expected video×hypothesis coverage."""
import unittest
import uuid

from intelligence.qualified_data.contracts import QDPError
from intelligence.qualified_data.coverage import (
    parse_expected_manifest, validate_result_coverage,
)

PID = "a" * 32
RUN = uuid.uuid4()
SHA = "d" * 64
TAX = "f" * 64
KEYS = [str(uuid.uuid5(uuid.NAMESPACE_URL, "qdp-leaf-" + str(i))) for i in range(20)]


def run_plan(videos=1, keys=KEYS):
    return {
        "id": RUN, "project_id": PID, "taxonomy_version_id": 33,
        "dataset_snapshot": {
            "schema_version": "sre-run-plan/v1",
            "project_id": PID, "run_id": str(RUN),
            "taxonomy_version_id": 33, "taxonomy_sha256": TAX,
            "items": [{
                "video_id": video, "content_version_id": 100 + video,
                "content_sha256": SHA, "hypothesis_keys": keys[:],
            } for video in range(1, videos + 1)],
        },
    }


def category_rows(keys=KEYS):
    return [{"category_key": k, "review_status": "approved"} for k in keys]


def result_rows(videos=1, keys=KEYS):
    return [{
        "result_id": 100000 + video * 100 + ix,
        "video_id": video, "category_key": key,
        "content_version_id": 100 + video,
        "result_content_sha256": SHA,
        "model_decision": "no_match", "effective_decision": "no_match",
        "content_readable": True,
        "content_snapshot": {"title": "sample title"},
        "evidence": [], "reason": "Not related",
        "stage_trace": [{"provider": "openrouter", "model_id": "anthropic/claude-opus-5.5"}],
    } for video in range(1, videos + 1) for ix, key in enumerate(keys)]


class PinnedSreCoverageTests(unittest.TestCase):
    def failure(self, run, rows, cats=None, code=None, video=None):
        with self.assertRaises(QDPError) as exc:
            validate_result_coverage(run, rows, cats if cats is not None else category_rows(),
                                     only_video_id=video)
        if code is not None:
            self.assertEqual(exc.exception.code, code)

    def test_20_leaf_100_videos_exact_2000_pairs(self):
        run = run_plan(100)
        rows = result_rows(100)
        x = validate_result_coverage(run, rows, category_rows())
        self.assertEqual(x["expected_pairs"], 2000)
        self.assertEqual(x["verified_videos"], 100)
        self.assertEqual(x["manifest_digest"], parse_expected_manifest(run)["digest"])

    def test_missing_pair_is_never_silent_no_match(self):
        self.failure(run_plan(100), result_rows(100)[:-1],
                     code="sre_coverage_incomplete")

    def test_duplicate_pair_and_unexpected_pair(self):
        rows = result_rows()
        self.failure(run_plan(), rows + [dict(rows[0], result_id=99999)],
                     code="sre_duplicate_result")
        self.failure(run_plan(), rows + [dict(rows[0], result_id=99999, video_id=404)],
                     code="sre_unexpected_result")

    def test_content_revision_and_sha_are_exact(self):
        rows = result_rows()
        self.failure(run_plan(), [dict(rows[0], content_version_id=999)] + rows[1:],
                     code="sre_content_mismatch")
        self.failure(run_plan(), [dict(rows[0], result_content_sha256="e" * 64)] + rows[1:],
                     code="sre_content_mismatch")

    def test_match_quote_must_be_in_pinned_source(self):
        rows = result_rows()
        rows[0] = dict(rows[0], model_decision="match", effective_decision="match",
                       evidence=[{"source": "title", "quote": "sample"}])
        validate_result_coverage(run_plan(), rows, category_rows())
        rows[0] = dict(rows[0], evidence=[{"source": "title", "quote": "invented"}])
        self.failure(run_plan(), rows, code="sre_evidence_unverified")

    def test_transcript_and_scene_evidence_with_exact_segments(self):
        rows = result_rows()
        content = {"data": {"title": "sample", "description": None,
                            "transcript": [{"text": "transcript spoken hook",
                                            "start_ms": 100, "end_ms": 600}],
                            "scenes": [{"text": "bright object",
                                        "start_ms": 800, "end_ms": 1000}]}}
        rows[0] = dict(rows[0], content_snapshot=content,
                       model_decision="match", effective_decision="match",
                       evidence=[{"source":"transcript", "quote":"spoken hook",
                                  "index":0,"start_ms":150,"end_ms":400}])
        validate_result_coverage(run_plan(), rows, category_rows())
        rows[0]["evidence"] = [{"source":"scene","quote":"bright object",
                               "index":0,"start_ms":810,"end_ms":950}]
        validate_result_coverage(run_plan(), rows, category_rows())
        rows[0]["evidence"] = [{"source":"scene","quote":"bright object",
                               "index":0,"start_ms":810,"end_ms":1050}]
        self.failure(run_plan(), rows, code="sre_evidence_unverified")

    def test_hashtag_grounding_and_invalid_source(self):
        rows=result_rows()
        rows[0]=dict(rows[0], content_snapshot={"hashtags":["sports","fitness"]},
                     model_decision="match", effective_decision="match",
                     evidence=[{"source":"hashtag","quote":"fitness"}])
        validate_result_coverage(run_plan(),rows,category_rows())
        rows[0]["evidence"]=[{"source":"other","quote":"fitness"}]
        self.failure(run_plan(),rows,code="sre_evidence_unverified")

    def test_missing_trace_or_synthetic_stage_fails(self):
        rows = result_rows()
        rows[0] = dict(rows[0], stage_trace=[])
        self.failure(run_plan(), rows, code="sre_stage_unverified")
        rows[0] = dict(rows[0], stage_trace=[{"synthetic": True}])
        self.failure(run_plan(), rows, code="sre_synthetic_stage")

    def test_unreadable_content_preserves_exact_negative_and_positive_pairs(self):
        rows = result_rows()
        rows[0] = dict(rows[0], content_readable=False, model_decision="match",
                       effective_decision="match",
                       evidence=[{"source": "title", "quote": "sample"}])
        validate_result_coverage(run_plan(), rows, category_rows())

    def test_manifest_video_id_and_hypotheses_are_unique(self):
        run = run_plan()
        run["dataset_snapshot"]["items"].append(
            dict(run["dataset_snapshot"]["items"][0]))
        self.failure(run, result_rows(), code="sre_manifest_duplicate")
        run = run_plan()
        run["dataset_snapshot"]["items"][0]["hypothesis_keys"].append(KEYS[0])
        self.failure(run, result_rows(), code="sre_manifest_duplicate")

    def test_missing_manifest_and_invalid_model_scope(self):
        run = run_plan()
        run["dataset_snapshot"] = {}
        self.failure(run, result_rows(), code="sre_manifest_missing")
        run = run_plan()
        run["dataset_snapshot"]["project_id"] = "b" * 32
        self.failure(run, result_rows(), code="sre_manifest_scope")

    def test_missing_leaf_and_unapproved_category(self):
        self.failure(run_plan(), result_rows(), category_rows()[:-1],
                     code="sre_taxonomy_coverage")
        cats = category_rows()
        cats[0] = dict(cats[0], review_status="proposed")
        self.failure(run_plan(), result_rows(), cats, code="sre_taxonomy_coverage")

    def test_no_review_error_can_turn_into_no_match(self):
        rows = result_rows()
        rows[0] = dict(rows[0], model_decision="not_evaluated", reason=None)
        self.failure(run_plan(), rows, code="sre_not_evaluated_reason")

    def test_targeted_revalidation_does_not_require_other_video_result_rows(self):
        rows = result_rows(2)
        one_video = [r for r in rows if r["video_id"] == 2]
        output = validate_result_coverage(run_plan(2), one_video, category_rows(),
                                          only_video_id=2)
        self.assertEqual(output["expected_pairs"], 20)
        self.failure(run_plan(2), one_video, code="sre_coverage_incomplete")

    def test_hash_stability_across_manifest_item_order(self):
        a = run_plan(2)
        b = run_plan(2)
        b["dataset_snapshot"]["items"].reverse()
        b["dataset_snapshot"]["items"][0]["hypothesis_keys"].reverse()
        self.assertEqual(parse_expected_manifest(a)["digest"],
                         parse_expected_manifest(b)["digest"])

    def test_offline_fixture_manifest_never_live(self):
        run = run_plan()
        run["dataset_snapshot"]["mode"] = "offline_fixture_s4"
        self.failure(run, result_rows(), code="sre_manifest_invalid")


if __name__ == "__main__":
    unittest.main()
