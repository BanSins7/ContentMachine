"""Read-only real-project A/B/C audit. Does not start workers or providers.

A project may be connected to the user's own ContentMachine PostgreSQL.
Unlike destructive test harness DB cases, this script ONLY reads existing
project data and never freezes snapshots or submits acquisition/LLM jobs.
Results exclude URLs, personal transcripts and raw provider payloads.
"""
from __future__ import annotations
import argparse
from datetime import datetime,timezone
import json
from pathlib import Path
import re
import sys

PROJECT_ID=re.compile(r"^[0-9a-f]{32}$")

def audit_snapshot(snapshot,batch=None,*,expect=100):
    from intelligence.market_intelligence.group_a_report import verify_group_a_report
    from intelligence.market_intelligence.group_b_report import (
        verify_group_b_report,verify_group_b_payload)
    from intelligence.market_intelligence.group_c_report import verify_report
    if type(snapshot) is not dict:
        raise ValueError("missing_market_snapshot")
    evidence=snapshot.get("video_evidence")
    if type(evidence) is not list:
        raise ValueError("snapshot_evidence_missing")
    counts={key:0 for key in (
        "videos_seen","a_sha_verified","b_sha_verified","c_math_verified",
        "c0_exact","c2_niche_available","c5_bayesian_available","c7_preview_available")}
    videos=[]
    for row in evidence:
        vid=row.get("video_id")
        ga=row.get("group_a") or {}
        gb=row.get("group_b") or {}
        gc=row.get("group_c") or {}
        actual_sha=ga.get("source_media_sha256")
        a=verify_group_a_report(ga,actual_sha) if ga else False
        b=bool(a and verify_group_b_report(
            (gb.get("native_report") or {}),actual_sha,ga["report_sha256"])
            and verify_group_b_payload(gb["native_report"],gb))
        c=verify_report(gc.get("native_report"),gc)
        exact=gc.get("c0")=="eligible_latest_snapshot"
        c2=(gc.get("c2_niche_outlier") or {}).get("state")=="observed_niche_comparison_unapproved"
        c5=gc.get("c5") is not None
        c7=gc.get("c7_breakout_preview") is not None
        counts["videos_seen"]+=1
        counts["a_sha_verified"]+=int(a)
        counts["b_sha_verified"]+=int(b)
        counts["c_math_verified"]+=int(c)
        counts["c0_exact"]+=int(exact)
        counts["c2_niche_available"]+=int(c2)
        counts["c5_bayesian_available"]+=int(c5)
        counts["c7_preview_available"]+=int(c7)
        problems=[]
        if not a:problems.append("A_missing_or_invalid")
        if not b:problems.append("B_missing_or_invalid")
        if not c:problems.append("C_missing_or_invalid")
        if not exact:problems.append("C0_exact_metric_missing")
        # C2/C5/C7 are optional when peer evidence is insufficient.
        videos.append({"video_id":vid,
            "a_verified":a,"b_verified":b,"c_verified":c,
            "c0_eligible":exact,
            "c2_available":c2,"c5_available":c5,"c7_available":c7,
            "issues":problems})
    selected=(batch or {}).get("selected")
    chosen=(batch or {}).get("videos") or []
    stages=(batch or {}).get("counts") or {}
    batch_frozen=bool((batch or {}).get("group_c_snapshot_id"))
    complete=(len(evidence)>=expect and selected==expect and batch_frozen
              and counts["a_sha_verified"]>=expect
              and counts["b_sha_verified"]>=expect
              and counts["c_math_verified"]>=expect
              and counts["c0_exact"]>=expect)
    return {
        "schema_version":"marketing-readonly-live-project-audit/v1",
        "checked_at":datetime.now(timezone.utc).isoformat(),
        "expect_videos":expect,
        "project_id":snapshot.get("project_id"),
        "batch_id":(batch or {}).get("batch_id"),
        "batch_selected":selected,
        "batch_stage_counts":stages,
        "batch_c_snapshot_present":batch_frozen,
        "market_metrics_coverage":counts,
        "videos":videos,
        "all_expected_source_scoped_receipts_present":complete,
        "official_original_group_abc_certified":False,
        "provider_requests":0,
        "llm_requests":0,
        "writes_to_project_database":0,
        "note":"C5/C7 are diagnostic unapproved; missing C2/C5/C7 must not be fabricated."
    }


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--project-id",required=True)
    p.add_argument("--expect",type=int,default=100)
    p.add_argument("--output",type=Path)
    args=p.parse_args()
    if not PROJECT_ID.fullmatch(args.project_id):
        p.error("project id must be 32 lowercase hex characters")
    if not 1<=args.expect<=2000:
        p.error("--expect must be 1..2000")
    from intelligence.market_intelligence.service import MarketIntelligenceService
    from intelligence.market_intelligence.batch import status
    # Both calls are SELECT-only; do not call .batch_status(), which starts worker.
    snapshot=MarketIntelligenceService().preview(args.project_id)["snapshot"]
    batch=status(args.project_id)
    report=audit_snapshot(snapshot,batch,expect=args.expect)
    data=json.dumps(report,ensure_ascii=False,indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True)
        args.output.write_text(data+"\n",encoding="utf-8")
        print("Report:",args.output)
    print(json.dumps({
        "project_id":report["project_id"],
        "expected":args.expect,
        "selected":report["batch_selected"],
        "coverage":report["market_metrics_coverage"],
        "batch_states":report["batch_stage_counts"],
        "all_expected_source_scoped_receipts_present":
          report["all_expected_source_scoped_receipts_present"]},
          ensure_ascii=False,indent=2))
    return 0 if report["all_expected_source_scoped_receipts_present"] else 2


if __name__=="__main__":
    sys.exit(main())
