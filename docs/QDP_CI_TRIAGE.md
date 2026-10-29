# QDP CI / TESTS TRIAGE — 2026-10-26

## Actual observed state (not an inferred Python regression)

GitHub PR #24 `feature/qualified-data-pipeline-v1-20261023`.
After baseline SHA `f40949c8` (its tests DID pass), each new QDP workflow registers with GitHub but remains queued with job labels `ubuntu-latest` and `runner_name=""`; e.g. runs [40300819629](https://github.com/BanSins7/ContentMachine/actions/runs/40300819629), [40304063101](https://github.com/BanSins7/ContentMachine/actions/runs/40304063101).

**Queued != failing tests.** No new Python/PostgreSQL tests can be reported as PASS or FAIL until a runner actually starts. This GitHub connector can read run/job status but cannot access repository Actions/billing/runner administration endpoints. No proof of an outage, billing issue, permissions block or particular cause. Current sandbox cannot clone the GitHub repository through DNS; a local full checkout/PG17 instance is not available here.

## Changes made in QDP CI

- Changed explicit runner image from `ubuntu-latest` to `ubuntu-24.04` to avoid ambiguity in image selection. This does **not** force assignment or bypass GitHub-hosted runner restrictions.
- Added `concurrency.group`/`cancel-in-progress=true` to cancel superseded QDP push/PR jobs instead of accumulating queued redundant tests.
- Added a standalone, cross-platform `scripts/qdp_verify.py` QA script using Python unittest + Python compileall + Node `--check`. It works on an independently cloned checkout without GitHub-hosted Actions.
- Use the same script for CI, preserve machine-readable local evidence as `qdp-qa.json` (never include DSN/password in the report).

## How to run outside GitHub Actions

From a real local checkout of the **exact QDP HEAD**:

`
python scripts/qdp_verify.py --report qdp-unit-qa.json
`

To test PostgreSQL 17 **on a disposable DB only**:

`
export CM_TEST_PG_DSN='postgresql://.../contentmachine_test'
export QDP_DISPOSABLE_TEST_DB=YES
python -m intelligence.migrate
python scripts/qdp_verify.py --with-postgres --report qdp-pg17-qa.json
`

On Windows PowerShell use `$env:CM_TEST_PG_DSN = '...'` and `$env:QDP_DISPOSABLE_TEST_DB = 'YES'`. Never point it at production. When reporting SHA, use `git rev-parse HEAD`.

## Owner UI checks to resolve GitHub-hosted queue

1. Open the latest workflow in GitHub → Actions. Inspect “Waiting for a runner to pick up this job” and check if jobs have been assigned/started.
2. Inspect repository and account **Settings → Actions / Runners**, including Actions availability and workflow policy. Review billing/usage for the account or organization if applicable.
3. Check https://www.githubstatus.com/ for any current Actions incident and GitHub support if runners are consistently unassigned.
4. If runners start, read the failing test logs and record actual FAILs with tracebacks in `QDP_RELEASE_EVIDENCE.md`. The test suite is not automatically green merely because runner assignment was restored.
5. Keep full cross-branch LIVE SRE/Collector/A/B/C E2E separately BLOCKED_EXTERNAL until actual integration exists.

Status at authoring: **CODE_IMPLEMENTED FIX-00..06**, **QA NOT RUN/QUEUED for exact HEAD**, original baseline CI passed only earlier SHA.
