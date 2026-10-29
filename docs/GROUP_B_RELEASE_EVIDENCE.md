# Group B — GB-MOD-28 Release QA Evidence (NOT A PASS CERTIFICATE)

**Branch:** `feature/group-b-full-module`  
**Scope:** full source/tests discovery, Python 3.11 Windows/Linux, Node 22, disposable PostgreSQL 17, frozen synthetic video/carousel cases, mocked OpenRouter, safety/regression checks and measured **synthetic** scheduler performance.

## Authoritative truth as of 2026-10-28

| Gate | Prepared and wired | Actually observed outcome |
|---|---|---|
| Full Group B Python syntax/compile + import/contract suites | Yes, `scripts/group_b_qa.py --mode pure` | **NOT RUN / NO PASS OBSERVED** |
| Linux Python 3.11 tests and Node 22 Studio | Yes, `pure-contracts` Ubuntu matrix | **GitHub Actions QUEUED, not green** |
| Windows Python 3.11 and Node 22 Studio | Yes, `pure-contracts` Windows matrix | **GitHub Actions QUEUED, not green** |
| PostgreSQL 17 full existing B migrations + schema isolation | Yes, service-backed strict `--mode pg17` | **QUEUED, NOT VERIFIED** |
| 005 checksum drift and 055–059 additive upgrade/idempotency | Yes, `tests/test_group_b_release_db.py` | **QUEUED, NOT VERIFIED** |
| 18 frozen video/carousel golden safety scenarios | Yes, `tests/fixtures/group_b_golden_matrix_v1.json` + `test_group_b_release_golden.py` | **QUEUED, SYNTHETIC ONLY** |
| Mock OpenRouter POST denial and 429/5xx uncertain charge | Yes, `tests/test_group_b_release_provider.py` | **QUEUED, no real provider call** |
| Crash/lease/1k batch/project rights | Yes, pre-existing GB27 PG17/Unit suites plus GB28 strict runner | **QUEUED, NOT VERIFIED** |
| Synthetic 1k-job scheduling p50/p95 and memory | Yes, `scripts/group_b_bench.py --rounds 100` | **NOT MEASURED until script executes** |
| Actual video/audio/OCR media & trusted GA/QDP receipt on host | No production host credentials in this branch | **BLOCKED_EXTERNAL** |
| Human-reviewed real gold set and per-class precision/recall/F1 | Requires independent real source/reviewer and model traces | **BLOCKED_EXTERNAL — NO MEASURED REAL QUALITY** |
| Real paid provider model benchmark | No trusted paid transport+approval, POST intentionally denied | **BLOCKED_EXTERNAL, NOT RUN** |
| Unified Analytics full seven-port qualification and Group C | Requires peer contract fixes, trustworthy data | **BLOCKED_EXTERNAL** |

**Do NOT treat “test code committed”, “synthetic candidate”, “GitHub queued” or “unit suite passed” as evidence that an actual paid provider, real original video classification or independently scored model quality gate has passed.** This matrix is a preparation and evidence-capture contract only.

## New GB28 test artifacts

`tests/fixtures/group_b_golden_matrix_v1.json` includes frozen cases for:
- Video without sound or original OCR confirmation: subtitle absence stays **unknown**, no inferred `none`.
- Video without CTA: only a test-stub explicitly marked complete scan permits `has_cta=false` candidate, never a real negative without original media coverage.
- Unreadable hook, missing original B6 and forged B5/B6 completion: **blocked**.
- Group A re-run/FK change, QDP input SHA change, source rights revocation, stale invalidation and human correction: **blocked**.
- Photo or mixed carousel with explicit B5/B6 N/A: `candidate` only; fake video editing evidence or no story-slide coverage: **blocked**.

`tests/test_group_b_release_golden.py` runs each case against the actual source `integration.analytics_contract.validate_snapshot`. It insists no synthetic result is UAE-qualified or a real provider receipt. The frozen manifest never includes media files, real user data, API keys, original transcripts or web downloads.

`tests/test_group_b_release_provider.py` uses an injected Mock opener and verifies that even forged Authorization/consent cannot cause `OpenRouterTransport` to POST or `PaidWorker` to run. It also exercises after-submit 408/429/5xx ambiguity; **automatic paid retries remain denied**.

`tests/test_group_b_release_db.py` requires a disposable PostgreSQL 17 whose DB name contains `test` or `_ci`; it checks applied source file SHA for SQL005 and additive 055–059, refuses checksum tampering, reruns `apply_migrations` idempotently, and verifies 059 append-only metric guard triggers and project-scoped indexes. No other branch or SQL005 is modified by the tests.

`scripts/group_b_bench.py` generates 1000 synthetic UUID jobs with 888 local and 112 paid jobs and times 100 repetitions of the pure planner on the actual runner, recording measured p50/p95/max milliseconds and tracemalloc peak bytes. All paid jobs stay deferred. These are **planner-only baseline measurements**, not FFmpeg, actual original-media bytes, provider TPS or billed-model latency.

## Strict reproducible execution

The cross-platform runner `scripts/group_b_qa.py` exposes:
```bash
python scripts/group_b_qa.py --mode static --report qa-evidence/group-b-static.json
python scripts/group_b_qa.py --mode pure   --report qa-evidence/group-b-pure.json
python scripts/group_b_bench.py --rounds 100 --report qa-evidence/group-b-synthetic-perf.json
```

On a disposable PostgreSQL 17 deployment **after installing psycopg 3**:
```bash
# Linux bash
export CM_TEST_PG_DSN='postgresql://gb_test:YOUR_LOCAL_TEST_PASSWORD@127.0.0.1:5432/cm_group_b_test'
python scripts/group_b_qa.py --mode pg17 --report qa-evidence/group-b-pg17.json
python scripts/group_b_qa.py --mode full --report qa-evidence/group-b-full.json
```

Windows PowerShell uses `$env:CM_TEST_PG_DSN="postgresql://.../cm_group_b_test"` instead of `export`. The runner probes **server major version exactly 17** and a clearly disposable DB name before touching the database. PostgreSQL test suites that skip unexpectedly **FAIL** the release QA: no skipped test may be considered a full PASS.

The report records source `GITHUB_SHA` if provided by GitHub Actions, date, platform, counts, skips, elapsed wall milliseconds, status `pass`/`fail`/`blocked`, and synthetic/real-provider flags. **It never records the DSN, environment, requests, provider errors or prompts.** CI uploads safe report artifacts with `actions/upload-artifact@v4` even when QA fails, and stores synthetic planner measurements separately. These artifacts are only trustworthy with a matching exact commit SHA and a completed SUCCESS run, not queued/in_progress.

## Detected regression fixed during GB28

`intelligence/group_b/review/quality.py` contained an invalid multiline `if` statement in `evaluate_quality`, creating a Python `SyntaxError`. Fixed locally in this branch; the full `compile()` sweep ensures any other Python syntax error fails early. This is an **implemented fix, not verified passing compilation**.

## Release/merge gates intentionally still blocked

Before any final `ready` or PR-to-main approval:
1. Observe **completed successful** Linux/Windows/PG17 runs at the *same exact commit* after a fresh clean checkout and successful upgrade path.
2. Reproduce clean DB/schema checksum, full B1–B8 actual source/rights negative scenarios, all source/tenant and queue safety tests.
3. Obtain independent human review of **actual** video/carousel model labels, real gold samples with per-class precision/recall/F1 and appropriate thresholds, original media/GA/QDP bytes and source-policy proof. Synthetic gold is insufficient.
4. Obtain authenticated paid provider receipt/cost reconciliation and enforce explicit budget/consent if enabling actual provider POST in a separate reviewed change; currently disabled.
5. Validate Unified Analytics and Group C peer interface compatibility, correct A/QDP version pins and invalidation; no fabricated `qualified` state.

**Current stage GB-MOD-28 result:** QA framework, golden regressions, strict workflow and evidence capture **IMPLEMENTED**. Full QA **NOT PASSED/NOT YET EXECUTED**. **GB-MOD-29 final merge-readiness remains BLOCKED** until real evidence exists; do not merge.
