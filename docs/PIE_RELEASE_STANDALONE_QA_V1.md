# PIE-MOD-35 — Final scientific E2E, PostgreSQL 17 and standalone release audit

Branch: feature/patterns-insights-full-module. Draft PR #39. Do not merge into main without explicit user approval.

STATUS: Stage35 QA/release infrastructure authored and committed; actual UAE, independent scientific holdout, real 10k source benchmarks, PG17 and Windows/Linux tests NOT VERIFIED. Release remains BLOCKED.

## Implementation progress (not release confidence)
- PIE-MOD-00..34: 35 stages implemented, source tracker marked [x].
- PIE-MOD-35: release evaluation and test code implemented; proof-of-done requires independent passing E2E.
- 36 total stages, 35/36 = 97.22% of tracker stages prior to confirming PIE35. Implementation progress is NOT a quality or readiness percentage.

## New files

1. intelligence/patterns_insights/release_readiness.py: 22 explicit release gates; typed provenance (local test vs independent external attestation), fail-closed unavailable/blocked/failed; immutable SHA256 report. All evidence missing => blocked_unverified. Even all successful evidence => candidate_verified_pending_explicit_merge_authorization, never automatic merge, publish or paid request. A digest is not a signature.
2. tests/test_pie_release_quality.py: 33 authored synthetic mathematical/scientific oracle tests. Paired high-low (1,3,4,2) => mean 5/2 ms, median difference 3.5 ms, two-arm histogram total variation 1/4 from bins [1,1,2] and [1,2,1] at cutpoints 3/7. Binary risk, undefined zero baseline, deterministic SHA256 bootstrap, nearest-rank 95% bounds, BH q=0.04/0.04/0.04/0.20 for SYNTHETIC p=0.01/0.02/0.03/0.20, disjoint creator/media/time discovery-holdout and adversarial leakage, drift 1/4, 12,000 deterministic SYNTHETIC rows across 150 pages of 80 and identity/replay.
3. tests/test_pie_release_pg17.py: 12 opt-in actual PG17 cases: migration checksum/schema/replay/immutable outbox, separate fresh+baseline upgrade, true two independent psycopg sessions and Barrier racing the same deterministic job, one claim winner, wrong-worker refusal, 3 outbox events, tenant isolation, immutable event UPDATE, 10-second DB lease expiry/recovery/stale completion.
4. scripts/pie_release_preflight.py: Windows/Linux local QA CLI, binds Git HEAD, checks branch and worktree, runs compileall/full PIE unit suite/Node Studio/synthetic quality/opt-in disposable PG17, records subprocess output SHA/exit code and explicit missing external gates. Each PostgreSQL test must be individually observed as verbose "... ok"; skip never counts as passed. Emits pie-local-preflight-report/v1 with release_approved=false, merge_authorized=false, model_payment_authorized=false.
5. tests/test_pie_release_preflight.py: 13 authored negative tests for skipped/mismatched PG verdict, unsafe database names, process failures, digest checks and no unsourced approval.
6. Root PIE API exports updated. No SQL migrations changed in this stage, no other module branches edited.

Total: 33 + 12 + 13 = 58 authored tests. NOT claimed executed.

## Local QA on real checkout

Windows PowerShell:
    git switch feature/patterns-insights-full-module
    git pull --ff-only
    python -m unittest discover -s tests -p 'test_pie_release_quality.py' -v
    python -m unittest discover -s tests -p 'test_pie_release_preflight.py' -v
    node tests/test_pie_explorer_ui.cjs
    python scripts/pie_release_preflight.py --output pie-release-audit.json

Linux:
    git switch feature/patterns-insights-full-module
    git pull --ff-only
    python3 -m unittest discover -s tests -p 'test_pie_release_quality.py' -v
    python3 -m unittest discover -s tests -p 'test_pie_release_preflight.py' -v
    node tests/test_pie_explorer_ui.cjs
    python3 scripts/pie_release_preflight.py --output pie-release-audit.json

Preflight exits 2 if ANY required gate remains missing/failed or tree dirty. --no-run writes all gates explicitly NOT RUN. No claim of PASS from a GitHub queue or manual assertion.

## Disposable PostgreSQL17 matrix (ONLY test databases)

CM_TEST_PG_DSN: disposable PG17 database for schema/immutability, optionally worker fixtures.
CM_TEST_PG_FRESH_DSN: a different empty PG17 test database without schema_migrations.
CM_TEST_PG_UPGRADE_DSN: a different PG17 test database with a preinstalled migration baseline.
CM_TEST_PIE_PROJECT_ID and CM_TEST_PIE_RUN_ID: isolated eligible 32hex test project and canonical UUID for a draft pie_research_runs row with valid source/policy foreign keys and no unrelated jobs.

Test connection checks current_database contains "test" and server_version_num is PostgreSQL 17, still use a dedicated disposable DB cluster. Real worker test creates/removes only its own job/event/outbox, expiry test sleeps ~10.25s for DB lease. Without valid fixture the tests skip and gates remain blocked. Do not point to production.

Migration203 historical caveat: stage30 corrected dollar quote syntax in 203_pie_model_dispatch_claims.sql. Existing deployment with old SHA cannot be forcibly rewritten; require separately approved forward-migration reconciliation. Main migration runner compares SHA and fails on mismatch.

## Real external release blockers

- UAE draft PR #38's peer wire uae-to-pie/v1 is currently described as a PROPOSED contract. Must verify the actual emitted qualified read, current project permissions, licensing/retention of ALL matched source rows, revocation and version compatibility; DO NOT substitute mock UAE fixtures.
- Cross-module SRE/QDP/Group A/B/C must supply qualified upstream evidence through UAE. PIE never silently re-computes Group C or upgrades unknown to supported.
- Independent preregistered hypothesis family, full multiple-testing correction, distinct media/creator/time holdout, calibrated p-values/CI from a genuine inferential producer, independent reviewer and proper science conditions. Synthetic gold is only a code oracle, never scientific validation.
- Human reviewer current authenticated nonauthor role, appeals and independent adjudication, actual source export permission. Internal positive review never auto-publishes.
- Stage29 unknown paid-send never retried. Actual provider bills/charges must be independently reconciled before any payment release claims.
- Actual 10,000+ qualified UAE-post benchmark measuring wall-time, memory, PostgreSQL load, pagination/worker recovery, Windows/Linux compatibility. 12k synthetic list arithmetic is NOT such a benchmark.
- App.py currently authenticates localhost host/origin, NOT a real human. Stage34 Explorer returns safe HTTP 503 until trusted VerifiedActor and fresh UAE/rights ports connected.
- Actual disposable PG17 fresh + upgrade, cross-project isolation, concurrent 2-worker claim, stale-write prevention and crash recovery, migration checksum reconciled.
- User must approve merge explicitly after satisfactory independent test evidence. No automatic PR merge, publisher permissions, outbox dispatch or paid LLM calls created in this stage.

## QA verdict

Stage35's test/release-gate code is implemented; required real test and scientific evidence is not available in this session. Retain stage35 as [ ] BLOCKED (IMPLEMENTATION DONE, QA NOT RUN); treat standalone release as NOT READY. Never equate green stage-code progress with ready-to-merge or publish.
