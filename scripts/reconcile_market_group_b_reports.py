"""Offline, no-cost join of one failed visual B window and its verified retry.

Never mutates or re-signs the original Group B evidence. A new QA receipt
records coverage across the two local runs, but cannot claim whole-video
semantic certification, approved B8 topic or a newly validated B1-B8 report.
"""
from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
import json
from pathlib import Path
import sys

from intelligence.market_intelligence.contracts import digest
from intelligence.market_intelligence.group_b_report import (
    verify_group_b_report, verify_group_b_payload,
)
from intelligence.market_intelligence.native_evidence import REF

SCHEMA="marketing-b-two-run-reconciliation/v1"
BASELINE_SCHEMA="marketing-real-mp4-live-b-check/v1"
RETRY_SCHEMA="marketing-single-b-window-live-check/v1"
MAX_INPUT_BYTES=8_000_000


def _safe_file(path: Path) -> dict:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_INPUT_BYTES:
        raise ValueError("report_missing_or_size_invalid")
    try:
        data=json.loads(path.read_text(encoding="utf-8"))
    except (ValueError,UnicodeError):
        raise ValueError("report_json_invalid") from None
    if type(data) is not dict:
        raise ValueError("report_object_required")
    return data


def _clock(value):
    try:
        v=Decimal(str(value))
    except (ValueError,TypeError,InvalidOperation):
        raise ValueError("invalid_window_clock") from None
    if not v.is_finite():
        raise ValueError("invalid_window_clock")
    return v


def reconcile(baseline: dict, retry: dict) -> dict:
    if (type(baseline) is not dict or baseline.get("schema_version") != BASELINE_SCHEMA
            or type(retry) is not dict or retry.get("schema_version") != RETRY_SCHEMA):
        raise ValueError("incompatible_report_schema")
    sha=baseline.get("video_sha256")
    a_sha=baseline.get("group_a_sha256")
    b_report=baseline.get("group_b_report")
    blocks=baseline.get("group_b_evidence")
    if (not isinstance(b_report,dict) or not isinstance(blocks,dict)
            or not verify_group_b_report(b_report,sha,a_sha)
            or not verify_group_b_payload(b_report,{"blocks":blocks})
            or baseline.get("group_b_sha256")!=b_report["report_sha256"]):
        raise ValueError("baseline_b_integrity_invalid")
    # GA OCR uses an independent UUID run_id; the resulting GA report SHA
    # is run-specific even for the same MP4 and deterministic video frames.
    # This is a QA reconciliation of sampled VISUAL evidence, not a merged
    # Group A report or an update to the original B receipt. Match the actual
    # source bytes, exact signed sampled-frame manifest and model separately.
    if retry.get("video_sha256")!=sha:
        raise ValueError("retry_video_sha_mismatch")
    baseline_manifest=b_report.get("frame_manifest_sha256")
    if (type(baseline_manifest) is not str or len(baseline_manifest)!=64
            or retry.get("frame_manifest_sha256")!=baseline_manifest):
        raise ValueError("retry_frame_manifest_sha_mismatch")
    if retry.get("model")!=baseline.get("model"):
        raise ValueError("retry_model_mismatch")
    retry_a_sha=retry.get("group_a_report_sha256")
    if (type(retry_a_sha) is not str or len(retry_a_sha)!=64
            or any(x not in "0123456789abcdef" for x in retry_a_sha)):
        raise ValueError("retry_group_a_sha_invalid")
    a_reports_identical=(retry_a_sha==a_sha)
    if (retry.get("state")!="model_observed_partial"
            or retry.get("attempted_remote_requests")!=1
            or retry.get("completed_remote_requests")!=1):
        raise ValueError("retry_not_a_successful_single_request")
    index=retry.get("window")
    windows=baseline.get("vision_windows")
    if (type(index) is not int or type(windows) is not list
            or not 1<=index<=len(windows) or len(windows)>6):
        raise ValueError("retry_window_number_invalid")
    failed=windows[index-1]
    if type(failed) is not dict or failed.get("state")!="unavailable":
        raise ValueError("baseline_window_not_failed")
    for k in ("start_sec","end_sec"):
        if _clock(retry.get(k))!=_clock(failed.get(k)):
            raise ValueError("retry_window_clock_mismatch")
    count=failed.get("sampled_frame_count")
    refs=retry.get("frame_refs")
    vision=retry.get("vision_evidence")
    if (type(count) is not int or not 1<=count<=8
            or retry.get("sampled_frame_count")!=count
            or type(refs) is not list or len(refs)!=count
            or len(set(refs))!=count
            or any(type(ref) is not str or REF.fullmatch(ref) is None for ref in refs)
            or type(vision) is not dict
            or vision.get("state")!="model_observed_partial"
            or vision.get("model")!=baseline.get("model")):
        raise ValueError("retry_frame_evidence_invalid")
    evidence=vision.get("evidence_frame_refs")
    if (type(evidence) is not list or len(evidence)!=len(set(evidence))
            or any(ref not in refs for ref in evidence)):
        raise ValueError("retry_cites_outside_selected_frames")
    for i,window in enumerate(windows):
        if (type(window) is not dict
                or window.get("state") not in ("unavailable","model_observed_partial")
                or (i!=index-1 and window["state"]!="model_observed_partial")):
            raise ValueError("other_visual_windows_not_observed")
    b8=blocks.get("B8")
    if not isinstance(b8,dict):
        raise ValueError("b8_payload_missing")
    match=b8.get("taxonomy_match") or {}
    if type(match) is not dict:
        raise ValueError("b8_mapping_invalid")
    topics=b8.get("topic_candidates") or []
    if type(topics) is not list or any(type(t) is not str for t in topics):
        raise ValueError("b8_topic_candidates_invalid")
    if match.get("state")=="catalog_matched":
        b8_status="existing_catalog_mapping_not_rechecked"
    else:
        b8_status="unapproved_conflicting_topic_proposals" if (
            match.get("reason")=="conflicting_topic_proposals"
        ) else "unapproved_no_catalog_match"
    base_digest=digest(baseline)
    retry_digest=digest(retry)
    record={
        "schema_version":SCHEMA,
        "video_sha256":sha,
        "group_a_report_sha256":a_sha,
        "retry_group_a_report_sha256":retry_a_sha,
        "group_a_reports_identical":a_reports_identical,
        "video_and_frame_manifest_identical":True,
        "frame_manifest_sha256":baseline_manifest,
        "baseline_group_b_report_sha256":b_report["report_sha256"],
        "baseline_report_file_sha256":base_digest,
        "recovery_report_file_sha256":retry_digest,
        "recovered_window":index,
        "recovered_window_times_sec":{
            "start":str(_clock(retry["start_sec"])),
            "end":str(_clock(retry["end_sec"])),
        },
        "recovered_window_cited_frame_count":len(evidence),
        "baseline_visual_windows_observed":len(windows)-1,
        "joined_visual_windows_observed":len(windows),
        "supplement_visual_windows_observed":baseline.get("supplement_successful_windows"),
        "original_group_b_payload_and_sha_verified":True,
        # This is NOT a claim that both run-specific A reports are the same.
        "recovery_matches_original_media_and_A":a_reports_identical,
        "recovery_matches_original_media_and_frames":True,
        "local_reconciliation_only":True,
        "group_b_report_reissued":False,
        "full_video_semantic_certified":False,
        "original_full_group_b_certified":False,
        "b8_review":{
            "status":b8_status,
            "reason":match.get("reason"),
            "original_topic_candidates":topics[:48],
            "original_topic_id":match.get("topic_id"),
            "new_approved_topic_id":None,
            "new_topic_mapping_performed":False,
            "recovery_does_not_resolve_b8":True,
        },
        "limitations":[
            "successful_sparse_sampled_windows_across_two_runs_only",
            "baseline_group_b_report_not_updated_or_recertified",
            "group_a_run_specific_receipts_may_differ_due_to_ocr_run_id",
            "no_original_provider_response_revalidation_for_other_windows",
            "B8_requires_grounded_reconciliation_with_approved_catalog",
        ],
    }
    return {**record,"reconciliation_sha256":digest(record)}


def main(argv=None):
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument("--baseline",type=Path,required=True)
    cli.add_argument("--recheck",type=Path,required=True)
    cli.add_argument("--output",type=Path,required=True)
    opts=cli.parse_args(argv)
    try:
        result=reconcile(_safe_file(opts.baseline),_safe_file(opts.recheck))
    except (ValueError,TypeError,OverflowError) as exc:
        cli.exit(2,"Reconciliation refused: "+str(exc)+"\n")
    dest=opts.output.resolve()
    dest.parent.mkdir(parents=True,exist_ok=True)
    dest.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print("Offline reconciliation: PASS; no network calls.")
    print("Main vision windows observed across two runs:",
          result["joined_visual_windows_observed"])
    print("Original B1-B8 integrity verified:",
          result["original_group_b_payload_and_sha_verified"])
    print("Same verified video and visual frame manifest:",
          result["video_and_frame_manifest_identical"])
    print("Run-specific Group A report SHA equal:",
          result["group_a_reports_identical"])
    print("B8:",result["b8_review"]["status"])
    print("Full video semantic certification: NO")
    print("Saved:",dest)
    return 0


if __name__=="__main__":
    sys.exit(main())
