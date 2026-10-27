#!/usr/bin/env python3
"""Fail-closed QDP FIX-18 release acceptance; no external calls or DB writes.

This is a release *gate*, not a substitute for the 120 test scenarios.
A passing CI verification of NO_GO must never be reported as release READY.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MATRIX = ROOT / "docs/QDP_QA_001_120.json"


def assess(matrix: dict, linux: dict | None, windows: dict | None) -> dict:
    cases = matrix.get("cases")
    if not isinstance(cases, list) or len(cases) != 120:
        raise ValueError("exactly 120 independent QA definitions are required")
    expected = [f"QA-{n:03d}" for n in range(1, 121)]
    actual = [c.get("id") if isinstance(c, dict) else None for c in cases]
    if actual != expected:
        raise ValueError("QA identifiers must be ordered, unique, and QA-001..120")
    if not all(isinstance(c.get("scenario"), str) and c["scenario"].strip()
               and c.get("tier") in ("automated", "partial", "external")
               and isinstance(c.get("tests"), list)
               and all(isinstance(t, str) and t.startswith("test_")
                       for t in c["tests"])
               for c in cases):
        raise ValueError("every QA needs a nonempty specification, tier and valid test names")
    blockers = []
    def verified_report(name: str, report: dict | None, *, require_pg: bool) -> bool:
        if not isinstance(report, dict):
            blockers.append(f"{name}_evidence_missing")
            return False
        ok = (report.get("overall") == ("PASS" if require_pg else "PARTIAL")
              and report.get("unit_status") == "PASS"
              and report.get("python_syntax") == "PASS"
              and report.get("js_syntax") == "PASS"
              and report.get("tests_failed") == 0
              and report.get("tests_errored") == 0
              and report.get("tests_skipped") == 0
              and isinstance(report.get("git_sha"), str)
              and len(report["git_sha"]) == 40
              and all(x in "0123456789abcdef" for x in report["git_sha"]))
        if require_pg:
            ok = ok and report.get("pg17_status") == "PASS"
        else:
            ok = ok and report.get("pg17_status") == "NOT_RUN"
        if not ok:
            blockers.append(f"{name}_not_verified")
        return ok

    linux_ok = verified_report("linux_pg17", linux, require_pg=True)
    windows_ok = verified_report("windows_unit", windows, require_pg=False)
    if linux_ok and windows_ok and linux["git_sha"] != windows["git_sha"]:
        blockers.append("linux_windows_commit_mismatch")
        windows_ok = False

    executed = linux.get("test_results", {}) if linux_ok else {}
    if linux_ok and not isinstance(executed, dict):
        blockers.append("missing_individual_test_results")
        executed = {}
    item_results = []
    for case in cases:
        tier, methods = case["tier"], case["tests"]
        matched = {}
        for method in methods:
            ids = [key for key in executed if key.endswith("." + method)]
            matched[method] = {
                "match_count": len(ids),
                "status": executed[ids[0]] if len(ids) == 1 else "UNVERIFIED",
            }
        all_passed = bool(methods) and all(
            v["match_count"] == 1 and v["status"] == "PASS"
            for v in matched.values()
        )
        if tier == "external":
            status = "BLOCKED_EXTERNAL"
        elif tier == "partial":
            status = "PARTIAL_EVIDENCE" if all_passed else "UNVERIFIED"
        elif linux_ok and all_passed:
            status = "PASS"
        else:
            status = "UNVERIFIED"
        item_results.append({
            "id": case["id"], "scenario": case["scenario"],
            "status": status, "tier": tier, "test_matches": matched,
        })
    nonpass = [item["id"] for item in item_results if item["status"] != "PASS"]
    if nonpass:
        blockers.append(f"qa_scenarios_unclosed:{len(nonpass)}")
    # Integration to actual SRE/Collector/Group A/Group B is owner-scoped.
    # An unrelated synthetic fixture can never bypass this release boundary.
    ready = linux_ok and windows_ok and not blockers
    return {
        "schema_version": "qdp-fix18-release-gate/v1",
        "release_status": "READY" if ready else "NO_GO",
        "linux_pg17_sha": linux.get("git_sha") if linux_ok else None,
        "windows_unit_sha": windows.get("git_sha") if windows_ok else None,
        "qa_pass": 120 - len(nonpass),
        "qa_not_closed": len(nonpass),
        "qa_not_closed_ids": nonpass,
        "blockers": blockers,
        "cases": item_results,
        "note": "PASS on the NO_GO gate is not approval to deploy or merge.",
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--matrix", type=Path, default=DEFAULT_MATRIX)
    p.add_argument("--linux-report", type=Path)
    p.add_argument("--windows-report", type=Path)
    p.add_argument("--report", type=Path)
    group = p.add_mutually_exclusive_group()
    group.add_argument("--expect-no-go", action="store_true")
    group.add_argument("--require-ready", action="store_true")
    args = p.parse_args()

    def read(path: Path | None):
        return json.loads(path.read_text(encoding="utf-8")) if path else None

    malformed = False
    try:
        result = assess(read(args.matrix), read(args.linux_report), read(args.windows_report))
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        malformed = True
        result = {"schema_version": "qdp-fix18-release-gate/v1",
                  "release_status": "NO_GO", "blockers": ["invalid_evidence_or_matrix", str(exc)]}
    output = json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True)
    print("QDP_FIX18_GATE=" + json.dumps({k: v for k, v in result.items() if k != "cases"},
                                       ensure_ascii=False, sort_keys=True))
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(output + "\n", encoding="utf-8")
    # A malformed manifest is a harness FAILURE, not a passing NO_GO check.
    if malformed:
        return 2
    if args.expect_no_go:
        return 0 if result["release_status"] == "NO_GO" else 1
    return 0 if result["release_status"] == "READY" else 1


if __name__ == "__main__":
    raise SystemExit(main())
