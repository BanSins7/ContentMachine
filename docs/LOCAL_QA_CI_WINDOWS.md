# Local Windows QA + CI (PostgreSQL17, no paid providers)

Run from `C:\ContentMachine` on the currently checked-out **main** after `git pull --ff-only origin main`.

## One-click full technical acceptance

1. **Stop** the normal ContentMachine server (`Ctrl+C`); the localhost smoke test needs port 8765.
2. Start Docker Desktop. Keep the original `contentmachine-postgres` container and its `cm_postgres_data` volume. Run `SETUP_POSTGRES_WINDOWS.bat` first and verify that `python -m intelligence.health` prints `database: connected`.
3. Install Node.js (ideally Node 22, matching `tests.yml`) and verify `node --version`. Python 3.11+ must be in PATH; the Linux CI uses Python 3.12 and Windows jobs also use other versions.
4. In PowerShell: `cd C:\ContentMachine`, `git status --short`, `git pull --ff-only origin main`. Preserve local edits instead of resetting if git refuses.
5. Run `./RUN_LOCAL_QA_CI.bat` or `python -u -m scripts.local_qa_ci`.
6. Locate the `QA_REPORT` path printed at the end; read `report.json` and the per-step `.log` files. Logs are in `%LOCALAPPDATA%\ContentMachineQA\<timestamp>_<run>/`, not in Git.

## What is executed

- Python 3.11+, Node availability, exact checkout SHA, and port 8765 preflight.
- Python `compileall` over app, projects, worker, intelligence, scripts and tests.
- Syntax checks for every JavaScript source under site/ and the Group A Studio JS, plus every `.cjs` and `.mjs` Node UI regression under tests/.
- **An isolated PostgreSQL 17 database** named `cmqa_<random>` is created on the existing local Docker PostgreSQL service. It is not the user's `contentmachine` database. Both `CM_DB_DSN` and `CM_TEST_PG_DSN` of every test child process point to that new temporary database; project file writes are redirected to a separate temporary fixture folder.
- The *real* `python -m intelligence.migrate` is run on the disposable DB, then run again to test no-op/idempotent replay. Database health is checked.
- PSI Stage14 release gate `--strict-ci`, Collector release gate `--strict-ci`, and QDP verify `--with-postgres`, with the QDP evidence report stored beside the main JSON.
- Complete `python -m unittest discover -s tests -v` including module contracts, policy/tenant, migration, replays, concurrent leases, failure recovery, real PostgreSQL integration tests guarded by their test fixtures.
- A second *new* disposable database strictly named `cm_sp16_test` is created for opt-in SP16 A/B tests, because the upstream regression rejects every other DB name. Its migration upgrade test itself creates `cm_sp16_upgrade`; both are cleaned up afterward by this launcher. Pre-existing databases with either name cause a **hard stop**, never overwrite.
- The real `tests/windows_d1_smoke.py` is executed with a real local HTTP server on port 8765, D1 imports, deduplication, cancellation and simulated server restart **inside the test DB only**.

## Safety guarantees and limits

- The runner requires a local PostgreSQL **17** connection to `contentmachine` on localhost port 54329 solely to create/destroy test databases with new dedicated names. It does **not** apply test migrations or insert fixtures in `contentmachine`.
- It never deletes, resets, truncates or rewrites production `contentmachine` tables or the Docker named volume. The randomly named QA DB and two exact-name dedicated SP16 DBs are dropped after the run. If a cleanup operation fails, the final report explicitly records `FAIL` and a leftover test DB may require manual DBA action.
- The runner removes process environment provider API keys and turns off known live collector/dispatch switches. **It is not a sandbox for arbitrarily malicious tests**, and some application code can read locally stored credentials; do not add live provider scripts or execute actual paid commands as part of this technical QA.
- It does **not** run one-off GitHub Actions workflows intentionally marked `live`, nor issue paid OpenRouter, DTK or platform API calls. It is not an execution of GitHub Actions itself; parity with Linux Python 3.12/Node 22 and all opt-in, isolated or human-reviewed jobs remains a separate acceptance condition.
- Native `unittest` can report skipped tests (OS, external credentials, standalone branches or special fixtures). A green `PASS_TECHNICAL` only says all *executed* stages exited zero; read each stage log for skips and report exact case coverage. This is **not** full business pipeline approval.
- The code in `main` must actually contain SRE/QDP/Group A and later Group B/C/UAE/PIE for those peer runtime checks to be runnable. Test discovery cannot certify modules or branches that are not in the checked-out source tree.

## Exit codes and how to submit results

- Exit `0`, `QA_STATUS=PASS_TECHNICAL`: all executed local technical stages passed; still verify logged skips and real pipeline separately.
- Exit `2`, `QA_STATUS=FAIL`: at least one test, prerequisite, migration, database cleanup or localhost smoke failed.
- Exit `2`, `QA_STATUS=INCOMPLETE_OFFLINE`: only offline code/UI tests were run, real PostgreSQL and localhost acceptance not established.

To run the reduced offline suite without touching PostgreSQL:
`./RUN_LOCAL_QA_CI.bat --offline`.

If the run fails, send `report.json`, the failing step `.log` and the initial error lines (after checking that test logs contain no secrets). Never send `.env` or passwords. An offline PASS does not release the platform.

## Manual pipeline acceptance after technical QA

On the real, separately configured service: validate project and rights → published taxonomy/RTB → Search Planner/QV → Collector/D1 → SRE qualified evidence → QDP currentness/rights → real Group A media (ASR/OCR) → verified Group B and C outputs → UAE → PIE. Inspect run IDs, result snapshots, provenance, money/consent, idempotent retries, revoked source compensation and actual UI display. Some of these downstream modules remain in separate integration branches and are not currently in main, so their full E2E must be marked **NOT VERIFIED** until they are integrated and executed.
