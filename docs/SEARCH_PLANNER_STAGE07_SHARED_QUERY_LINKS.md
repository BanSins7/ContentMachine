# Search Planner SP-07 — deterministic selection and multi-hypothesis lineage

Date 2026-10-26. Branch feature/search-planner-full-integration.
Code implemented; GitHub CI exact-head and real PostgreSQL/semantic-model quality QA remain open.

## Changed

- intelligence/search_planner/query_validation/selection.py — NFKC casefold normalization; within-hypothesis exact/semantic dedup then local MMR selection, and only afterwards cross-hypothesis dedup of genuinely selected queries. Selected winner is always a real selected query. Alias decisions retain original UUID/hypothesis and duplicate_of. Selected ranks and coverage recalculated. Informational shared_query_links sidecar.
- intelligence/search_planner/query_validation/linkage.py — read-only query-link reconstruction from immutable qv_decisions joined to sqg_candidates; trusted project/QV/mode check, preserved candidate/hypothesis/source-fact provenance, SHA-pinned response.
- intelligence/search_planner/service.py — get_validated_query_links(project_id, validation_run_id) through authoritative DB; no caller-supplied accepted JSON, Collector or provider network execution.
- intelligence/search_planner/query_validation/__init__.py — exports link reader.
- tests/test_search_planner_stage07_links.py — deterministic cross-hypothesis dedup, NFKC, 0-work, quota-suppression regression, mode/locale separation and forged DB decisions.
- .github/workflows/sp-stage01-fast.yml — focused SP-07 QA plus PR-scoped concurrency with cancel-in-progress to avoid piling up outdated SP-focused runs.

## Correct algorithm and original bug

Old Query Validation globally de-duplicated eligible candidates before per-hypothesis MMR. A query from hypothesis B could be removed because it duplicated A's candidate, even if A's candidate was later discarded by its local quota. No selected query remained to represent B.

Now selection applies:
1. Existing exact policy/project/hypothesis/fact constraints and relevance/negative scope gates.
2. Local dedup within each hypothesis and local MMR selection/quota.
3. Optional cross-hypothesis dedup ONLY among selected query rows, deterministically by relevance desc and UUID. The winning target remains selected.
4. Each losing duplicate still exists with its original candidate_id, hypothesis_id, source_fact_ids and immutable duplicate_of; selected rank reset and final counts recalculated.
5. DB-derived search-planner-query-hypothesis-links/v1 provides each physically selected query with all selected/aliased candidate UUIDs, hypotheses and source facts. Original validated_queries/v1 wire format, SQL migration and qv_outbox remain unchanged.

If all three hypotheses proposed the same selected keyword, the downstream plan gets one physical query with three grounded hypothesis links, not just one surviving origin. When a candidate duplicates a quota-rejected candidate, that rejected candidate is NOT wrongly treated as executable.

## Trust boundary

This is NOT an external Collector task, real video result, validated retrieval precision, or provider receipt. Links use immutable production QV SQL source truth (qv_runs, qv_decisions, sqg_candidates) and refuse fixture modes. There is no automatic paid API call, no new SQL migration and no change to existing V1 publication. Semantic similarity thresholds are still vector proxies, pending real multilingual calibration.

## Tests and CI investigations

Offline:
- python -m unittest discover -s tests -p 'test_search_planner_stage07_links.py' -v
- python -m unittest discover -s tests -p 'test_search_planner_query_validation.py' -v
- python -m unittest discover -s tests -v (requires proper DB setup)

On 2026-10-26, SP focused Actions run 40300195286 (SHA 1cfacb187...) had status queued, conclusion null and its sole job queued. No runner was assigned and no test steps had started. Other feature PR runs were queued too. This is NOT a failed Python test. Root cause is NOT established: examine hosted runner capacity, Actions quotas/billing, repository runner permissions and service state from GitHub UI. Main branch run 40284202868 had succeeded earlier.

The SP-owned focused workflow now cancels previous revisions within the same PR concurrency group to reduce queue pressure. It does not cancel other modules or alter main, and does not guarantee Github runners become available. The sandbox cannot clone github.com (DNS failure), so do not report a local PASS.

Pending gate: exact SHA CI green, actual disposable PostgreSQL17 source/lineage projection E2E, real pinned multilingual embedding smoke, SP-16 release checks. Mark implementation done separately from unavailable tests.
