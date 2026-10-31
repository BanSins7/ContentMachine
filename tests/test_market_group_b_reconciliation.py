"""No-network B1-B8 two-run result reconciliation and integrity tests."""
import copy
import unittest

from intelligence.market_intelligence.contracts import digest
from intelligence.market_intelligence.group_b_report import VERSION, BLOCKS
from scripts.reconcile_market_group_b_reports import reconcile


MEDIA="a"*64
GA="b"*64
MANIFEST="c"*64
REF="ga-frame:f025-"+("d"*64)


def baseline_and_retry():
    blocks={name:{
        "state":"model_observed_partial" if name!="B8" else "observed_topic_hint_unapproved",
        "evidence_frame_refs":[],
    } for name in BLOCKS}
    blocks["B8"].update({
        "topic_candidates":["wings","FaceKit App","FaceKit facial analysis"],
        "taxonomy_match":{
            "state":"observed_unmapped",
            "reason":"conflicting_topic_proposals",
            "topic_id":None,
        },
    })
    report={
        "schema_version":VERSION,
        "source_media_sha256":MEDIA,
        "group_a_report_sha256":GA,
        "frame_manifest_sha256":MANIFEST,
        "all_eight_block_payload_sha256":digest(blocks),
        "original_full_group_b_certified":False,
        "no_qdp_dependencies":True,
    }
    report["report_sha256"]=digest(report)
    win=[
        {"state":"model_observed_partial","start_sec":str(i*5),
         "end_sec":str(i*5+5),"sampled_frame_count":8}
        for i in range(5)
    ]
    win[3].update({"state":"unavailable","start_sec":14.633,
                   "end_sec":19.633,"sampled_frame_count":8})
    baseline={
        "schema_version":"marketing-real-mp4-live-b-check/v1",
        "model":"openai/gpt-4.1-mini",
        "video_sha256":MEDIA,
        "group_a_sha256":GA,
        "group_b_sha256":report["report_sha256"],
        "group_b_report":report,
        "group_b_evidence":blocks,
        "vision_windows":win,
        "supplement_successful_windows":5,
    }
    refs=[REF]+[
        "ga-frame:f%03d-"%(i+26)+"e"*64 for i in range(7)
    ]
    retry={
        "schema_version":"marketing-single-b-window-live-check/v1",
        "model":"openai/gpt-4.1-mini",
        "video_sha256":MEDIA,
        "group_a_report_sha256":GA,
        "frame_manifest_sha256":MANIFEST,
        "window":4,
        "start_sec":14.633,
        "end_sec":19.633,
        "sampled_frame_count":8,
        "frame_refs":refs,
        "state":"model_observed_partial",
        "attempted_remote_requests":1,
        "completed_remote_requests":1,
        "vision_evidence":{
            "state":"model_observed_partial",
            "model":"openai/gpt-4.1-mini",
            "evidence_frame_refs":[REF],
        }
    }
    return baseline,retry


class ReconciliationTests(unittest.TestCase):
    def test_valid_join_does_not_claim_new_b_report_or_b8_topic(self):
        base,retry=baseline_and_retry()
        result=reconcile(base,retry)
        self.assertEqual(result["joined_visual_windows_observed"],5)
        self.assertEqual(result["recovered_window"],4)
        self.assertTrue(result["original_group_b_payload_and_sha_verified"])
        self.assertFalse(result["group_b_report_reissued"])
        self.assertFalse(result["full_video_semantic_certified"])
        self.assertIsNone(result["b8_review"]["new_approved_topic_id"])
        self.assertEqual(
            result["b8_review"]["status"],"unapproved_conflicting_topic_proposals")
        self.assertEqual(result["b8_review"]["original_topic_candidates"][0],"wings")
        self.assertEqual(
            result["reconciliation_sha256"],
            digest({k:v for k,v in result.items() if k!="reconciliation_sha256"}))

    def test_forged_recovered_reference_is_rejected(self):
        base,retry=baseline_and_retry()
        retry["vision_evidence"]["evidence_frame_refs"]=["invented"]
        with self.assertRaisesRegex(ValueError,"retry_cites_outside_selected_frames"):
            reconcile(base,retry)

    def test_baseline_b8_tamper_breaks_immutable_b_sha(self):
        base,retry=baseline_and_retry()
        base["group_b_evidence"]["B8"]["topic_candidates"]=["fabricated new claim"]
        with self.assertRaisesRegex(ValueError,"baseline_b_integrity_invalid"):
            reconcile(base,retry)

    def test_previously_successful_window_cannot_be_overwritten(self):
        base,retry=baseline_and_retry()
        base["vision_windows"][3]["state"]="model_observed_partial"
        with self.assertRaisesRegex(ValueError,"baseline_window_not_failed"):
            reconcile(base,retry)

    def test_different_video_model_and_clock_rejected(self):
        for field,value,expected in (
            ("video_sha256","0"*64,"retry_media_or_model_mismatch"),
            ("model","different/vision","retry_media_or_model_mismatch"),
            ("start_sec",15.0,"retry_window_clock_mismatch"),
        ):
            with self.subTest(field=field):
                base,retry=baseline_and_retry()
                retry[field]=value
                with self.assertRaisesRegex(ValueError,expected):
                    reconcile(base,retry)

    def test_unavailable_retry_cannot_mark_full_window_coverage(self):
        base,retry=baseline_and_retry()
        retry["state"]="unavailable"
        with self.assertRaisesRegex(ValueError,"retry_not_a_successful_single_request"):
            reconcile(base,retry)


if __name__=="__main__":
    unittest.main()
