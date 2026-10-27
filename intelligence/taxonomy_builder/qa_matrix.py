"""RTB FIX-10: the spec's 36 QA requirements mapped to actual tests.

A mapped test means an AST reference exists, not that CI has passed.
Partial and external-gap statuses MUST remain visible until closed.
Run with python -m intelligence.taxonomy_builder.qa_matrix --check.
"""
from __future__ import annotations
import argparse
import ast
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
TESTS=ROOT/"tests"
CASES=json.loads("[{\"id\":\"T01\",\"title\":\"B9 approved plus genuine A8 frozen input\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_research_input.py\",\"test\":\"test_two_real_b9_facts_and_original_a_observations\"}],\"reason\":\"\"},{\"id\":\"T02\",\"title\":\"Empty A video permitted\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_research_input.py\",\"test\":\"test_zero_video_is_valid_and_stable\"}],\"reason\":\"\"},{\"id\":\"T03\",\"title\":\"Foreign video context fail-closed\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_research_input.py\",\"test\":\"test_rejected_unapproved_and_foreign_context\"}],\"reason\":\"\"},{\"id\":\"T04\",\"title\":\"B9/A hash drift blocks\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_research_input.py\",\"test\":\"test_handoff_digest_mismatch_is_blocking\"}],\"reason\":\"\"},{\"id\":\"T05\",\"title\":\"Wrong source schema version\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_research_input.py\",\"test\":\"test_wrong_b9_handoff_and_a_video_schema_versions_fail_closed\"}],\"reason\":\"\"},{\"id\":\"T06\",\"title\":\"Prompt-injection isolation\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_quote.py\",\"test\":\"test_malicious_source_instructions_remain_untrusted_user_data\"}],\"reason\":\"\"},{\"id\":\"T07\",\"title\":\"Read-only previews never pay\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_research_input.py\",\"test\":\"test_preview_idempotent_across_two_calls\"}],\"reason\":\"\"},{\"id\":\"T08\",\"title\":\"Frozen data survives source updates\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_binding_db.py\",\"test\":\"test_real_b9_storage_mutation_refused_after_binding_pin\"}],\"reason\":\"\"},{\"id\":\"T09\",\"title\":\"Pass1 separates B9 facts and A8 observations\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_quote.py\",\"test\":\"test_actual_video_evidence_passes_unchanged_as_untrusted_data\"}],\"reason\":\"\"},{\"id\":\"T10\",\"title\":\"Unknown fact/observation IDs rejected\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_validation.py\",\"test\":\"test_legacy_graph_rules_reject_bad_parent_and_fake_facts\"}],\"reason\":\"\"},{\"id\":\"T11\",\"title\":\"Competitor claim cannot authorize direct own product\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_validation.py\",\"test\":\"test_direct_claim_must_come_from_approved_own_product_fact\"}],\"reason\":\"\"},{\"id\":\"T12\",\"title\":\"Hypothesis valid with no observations\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_validation.py\",\"test\":\"test_valid_search_hypotheses_without_video_observation_references\"}],\"reason\":\"\"},{\"id\":\"T13\",\"title\":\"A8 observations do not prove performance\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_sre_projection.py\",\"test\":\"test_video_observation_is_only_search_hint_never_fact_or_verified_video\"}],\"reason\":\"\"},{\"id\":\"T14\",\"title\":\"Pass2 retains exact frozen facts and videos\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_followup.py\",\"test\":\"test_no_source_or_h1_mutation\"}],\"reason\":\"\"},{\"id\":\"T15\",\"title\":\"H1 approved nodes immutable in subsequent passes\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_followup.py\",\"test\":\"test_removed_or_changed_approved_node_is_rejected\"}],\"reason\":\"\"},{\"id\":\"T16\",\"title\":\"Two separately consented expansions maximum\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_expansion_db.py\",\"test\":\"test_two_paid_expansions_and_never_a_third\"}],\"reason\":\"\"},{\"id\":\"T17\",\"title\":\"Truncated provider result uncertain without retry\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_provider.py\",\"test\":\"test_malformed_paid_completion_retains_known_charge\"}],\"reason\":\"\"},{\"id\":\"T18\",\"title\":\"Legacy Deep V1 11 and V2 strict 14\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_contracts.py\",\"test\":\"test_valid_tree_and_coverage_matrix\"}],\"reason\":\"\"},{\"id\":\"T19\",\"title\":\"Hypothesis needs signals and focus\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_validation.py\",\"test\":\"test_hypotheses_must_have_unique_concrete_signals\"}],\"reason\":\"\"},{\"id\":\"T20\",\"title\":\"Nonhypothesis extensions empty\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_validation.py\",\"test\":\"test_additional_missing_or_invalid_extensions_rejected\"}],\"reason\":\"\"},{\"id\":\"T21\",\"title\":\"Duplicate or empty signals and observations blocked\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_validation.py\",\"test\":\"test_hypotheses_must_have_unique_concrete_signals\"}],\"reason\":\"\"},{\"id\":\"T22\",\"title\":\"Coverage and semantic duplicates gate\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_readiness.py\",\"test\":\"test_coverage_gap_blocks_a_publishable_tree\"}],\"reason\":\"\"},{\"id\":\"T23\",\"title\":\"Review UI add/edit/merge/reject provenance\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_review.py\",\"test\":\"test_compound_positive_add_edit_merge_reject_preserves_b9_a8\"},{\"file\":\"test_taxonomy_deep_v2_review_db.py\",\"test\":\"test_positive_add_edit_reject_persisted_review1_no_new_payment\"}],\"reason\":\"\"},{\"id\":\"T24\",\"title\":\"Review2 readiness-invalidating edit blocks publication\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_review2_db.py\",\"test\":\"test_bad_search_signal_edit_is_blocked_and_rolls_back\"}],\"reason\":\"\"},{\"id\":\"T25\",\"title\":\"Published V2 retains all 14 fields and SHA\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_publication_db.py\",\"test\":\"test_publish_once_has_exact_14_and_11_field_immutable_views\"}],\"reason\":\"\"},{\"id\":\"T26\",\"title\":\"Exact V2 to V1 11-field core\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_sre_projection.py\",\"test\":\"test_full_tree_and_exact_11_field_core_without_mutation\"}],\"reason\":\"\"},{\"id\":\"T27\",\"title\":\"Legacy Search Planner accepts V2-compatible core\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_publication_export_v2.py\",\"test\":\"test_existing_search_planner_v1_contract_accepts_adapter\"}],\"reason\":\"\"},{\"id\":\"T28\",\"title\":\"SP V2 consumes observation signals and focus\",\"status\":\"external_gap\",\"refs\":[{\"file\":\"test_taxonomy_publication_export_v2.py\",\"test\":\"test_v2_export_keeps_14_keys_and_original_a_references\"}],\"reason\":\"RTB v2 export ready, Search Planner v2 real consumer belongs to separate SP branch\"},{\"id\":\"T29\",\"title\":\"Query generator uses V2 signals with query validation\",\"status\":\"external_gap\",\"refs\":[{\"file\":\"test_search_planner_query_validation.py\",\"test\":\"test_selection_deterministic_complete_and_non_mutating\"}],\"reason\":\"Existing query validation tested; consuming v2 signals in query generation belongs to separate SP branch\"},{\"id\":\"T30\",\"title\":\"Approved SRE taxonomy and rules remain compatible\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_publication_db.py\",\"test\":\"test_sre_fact_ids_match_pinned_b9_mapping_and_video_is_not_fact\"}],\"reason\":\"\"},{\"id\":\"T31\",\"title\":\"Same B9 fact ID reusable across publications\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_publication_db.py\",\"test\":\"test_matching_preexisting_fact_is_reused_not_inserted_twice\"}],\"reason\":\"\"},{\"id\":\"T32\",\"title\":\"Different-content UUID/foreign reuse rejected\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_publication_db.py\",\"test\":\"test_conflicting_existing_fact_uuid_aborts_publication_and_sre_version\"}],\"reason\":\"\"},{\"id\":\"T33\",\"title\":\"Atomic rollback on publication failure\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_publication_db.py\",\"test\":\"test_failure_after_full_sre_write_rolls_back_everything\"}],\"reason\":\"\"},{\"id\":\"T34\",\"title\":\"Legacy Flat and Deep V1 unchanged\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_db.py\",\"test\":\"test_complete_two_human_reviews_saturation_and_atomic_sre_tree\"}],\"reason\":\"\"},{\"id\":\"T35\",\"title\":\"Provider calls in CI mocked with usage and cost\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_provider.py\",\"test\":\"test_exact_pinned_payload_and_known_usage\"}],\"reason\":\"\"},{\"id\":\"T36\",\"title\":\"Every paid stage requires consent and budget\",\"status\":\"mapped\",\"refs\":[{\"file\":\"test_taxonomy_deep_v2_lifecycle.py\",\"test\":\"test_user_interaction_required_for_every_paid_pass\"}],\"reason\":\"\"}]")
VALID={"mapped","partial","external_gap"}

SAFE_OFFLINE=(
    "test_taxonomy_c_corrector_contracts.py",
    "test_taxonomy_research_input.py",
    "test_taxonomy_deep_v2_lifecycle.py",
    "test_taxonomy_deep_v2_quote.py",
    "test_taxonomy_deep_v2_validation.py",
    "test_taxonomy_deep_v2_provider.py",
    "test_taxonomy_deep_v2_review.py",
    "test_taxonomy_deep_v2_followup.py",
    "test_taxonomy_deep_v2_expansion.py",
    "test_taxonomy_deep_v2_readiness.py",
    "test_taxonomy_deep_v2_sre_projection.py",
    "test_taxonomy_publication_export_v2.py",
    "test_taxonomy_deep_v2_http.py",
    "test_taxonomy_deep_v2_sql_static.py",
    "test_taxonomy_deep_v2_migration_audit.py",
    "test_taxonomy_release_boundary.py",
    "test_taxonomy_v2_qa_matrix.py",
    "test_taxonomy_deep_contracts.py",
    "test_taxonomy_builder_run.py",
    "test_search_planner_foundation.py",
    "test_search_planner_engine_contracts.py",
    "test_search_planner_engine_stage_c.py",
    "test_search_planner_query_validation.py",
    "test_search_planner_stage_d.py",
)
SAFE_NODE=(
    "test_taxonomy_deep_v2_ui.cjs",
    "test_taxonomy_deep_ui.cjs",
    "test_taxonomy_run_ui.cjs",
    "test_taxonomy_studio_ui.cjs",
    "test_taxonomy_cost_ui.cjs",
)
SAFE_PG=(
    "test_taxonomy_research_input_db.py",
    "test_taxonomy_deep_v2_binding_db.py",
    "test_taxonomy_deep_v2_ledger_db.py",
    "test_taxonomy_deep_v2_quote_db.py",
    "test_taxonomy_deep_v2_pass1_db.py",
    "test_taxonomy_deep_v2_review_db.py",
    "test_taxonomy_deep_v2_pass2_db.py",
    "test_taxonomy_deep_v2_expansion_db.py",
    "test_taxonomy_deep_v2_review2_db.py",
    "test_taxonomy_deep_v2_publication_db.py",
    "test_taxonomy_publication_export_v2_db.py",
    "test_taxonomy_deep_v2_http_db.py",
    "test_taxonomy_deep_db.py",
    "test_taxonomy_builder_run_db.py",
)

def audit():
    """Verify immutable T01–T36 IDs and real unittest method definitions."""
    errors=[]
    ids=[f"T{i:02d}" for i in range(1,37)]
    if [c["id"] for c in CASES]!=ids:
        errors.append("T01..T36 order/count/uniqueness mismatch")
    cache={}
    for case in CASES:
        if case["status"] not in VALID:
            errors.append(case["id"]+": unknown status")
        if case["status"]!="mapped" and not case["reason"]:
            errors.append(case["id"]+": missing gap rationale")
        if not case["refs"]:
            errors.append(case["id"]+": missing test evidence")
        for ref in case["refs"]:
            file=ref["file"]
            if not re.fullmatch(r"test_[a-z0-9_]+[.]py",file):
                errors.append(case["id"]+": unsafe file name "+str(file))
                continue
            if file not in cache:
                path=TESTS/file
                if not path.is_file():
                    errors.append(case["id"]+": missing file "+file)
                    cache[file]=set()
                else:
                    try:
                        tree=ast.parse(path.read_text(encoding="utf-8"))
                        cache[file]={
                            node.name
                            for cls in tree.body if isinstance(cls,ast.ClassDef)
                            for node in cls.body if isinstance(node,ast.FunctionDef)
                            and node.name.startswith("test_")
                        }
                    except (SyntaxError,UnicodeError,OSError) as e:
                        errors.append(case["id"]+": invalid source "+str(e))
                        cache[file]=set()
            if ref["test"] not in cache[file]:
                errors.append(case["id"]+": missing "+file+"::"+ref["test"])
    return {
        "schema":"rtb-qa-matrix/v1",
        "source":"ContentMachine_C_Corrector_FINAL_RU(2).txt, section 13",
        "case_count":len(CASES),
        "mapped":sum(c["status"]=="mapped" for c in CASES),
        "partial":[c["id"] for c in CASES if c["status"]=="partial"],
        "external_gaps":[c["id"] for c in CASES if c["status"]=="external_gap"],
        "reference_errors":errors,
        "reference_check_passed":not errors,
        "runtime_tests_executed":False,
        "ci_success_verified":False,
    }

def run_curated(*,postgres=False):
    """Never selects live E2E files or calls OpenRouter in this process."""
    results=[]
    suites=[
        (file,[sys.executable,"-m","unittest","discover","-s","tests","-p",file,"-v"])
        for file in SAFE_OFFLINE
    ]
    suites += [(file,["node",str(TESTS/file)]) for file in SAFE_NODE]
    if postgres:
        suites += [
            (file,[sys.executable,"-m","unittest","discover",
                   "-s","tests","-p",file,"-v"])
            for file in SAFE_PG
        ]
    for name,cmd in suites:
        completed=subprocess.run(cmd,cwd=ROOT,check=False)
        results.append({"suite":name,"exit_code":completed.returncode})
    return results

def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument("--check",action="store_true")
    parser.add_argument("--strict",action="store_true",
        help="Release-only mode; fail while ANY partial/external item remains")
    parser.add_argument("--run-offline",action="store_true")
    parser.add_argument("--run-postgres",action="store_true")
    parser.add_argument("--json-out",type=Path)
    args=parser.parse_args(argv)
    report=audit()
    if args.run_offline or args.run_postgres:
        report["results"]=run_curated(postgres=args.run_postgres)
        report["runtime_tests_executed"]=True
    report["postgres_suites_requested"]=bool(args.run_postgres)
    # An AST inspection or even a local offline test is not a release signoff:
    # require full PG run AND actual external CI success evidence. This script
    # deliberately cannot forge CI success from local process exit codes.
    report["release_ready"]=bool(
        report["reference_check_passed"] and
        not report["partial"] and not report["external_gaps"] and
        report["runtime_tests_executed"] and args.run_postgres and
        report["ci_success_verified"] and
        report.get("results") and all(
            x["exit_code"]==0 for x in report.get("results",()))
    )
    if args.json_out:
        args.json_out.parent.mkdir(parents=True,exist_ok=True)
        args.json_out.write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n",
                                 encoding="utf-8")
    print(json.dumps(report,ensure_ascii=False,indent=2))
    if not report["reference_check_passed"]:
        return 1
    if args.strict and not report["release_ready"]:
        return 2
    if any(x["exit_code"] for x in report.get("results",())):
        return 1
    return 0

if __name__=="__main__":
    raise SystemExit(main())
