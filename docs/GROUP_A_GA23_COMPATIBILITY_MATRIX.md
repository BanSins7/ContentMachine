# GA-MOD-23 — CI, Windows/Linux, PostgreSQL 17, contract and E2E matrix

**Implementation:** 2026-10-28, committed in isolated `feature/group-a-full-module`. **Execution/QA:** explicitly NOT RUN in this stage, by user request. This is a *ready-to-execute* test matrix, **NOT release-qualified QA**. The comparison to actual QDP/SRE/PSI/B/Analytics implementations is still untested. `main` and all other module branches were left untouched.

## Scope and execution truth

- `tests/test_group_a_e2e.py` — real GA-owned PostgreSQL 17 chain using a synthetic, preexisting GA19 PG17 fixture: QDP-shaped **FAKE** accepted GA04 run → GA18 immutable A003 → GA19 completed result with original GA run/QDP/source/media/policy SHA pin → GA20 exact current-result read → Group B **FAKE** exact-`ga_result_id+sha256` admission and verified test ACK persisted by GA20. Includes immutable replay, rejected false digest, revoked QDP state, outbox non-dispatchability and retained historic A003. **This does not exercise real Group B B1–B8, a real QDP service or live authorization**.
- `tests/test_group_a_migration_matrix.py` — isolated, randomized, disposable PostgreSQL 17 schema per test: fresh full migration install, replay without extra migrations, full pre-054 baseline → GA054–058 upgrade, all migrations SHA unchanged, detect deliberately tampered `schema_migrations` checksums. Requires a dedicated disposable DB and two explicit environment variables; refuses production-default execution.
- `.github/workflows/group-a-module.yml` — existing Ubuntu PG17 + offline media/schema workflow preserved; new native Windows Python 3.11/3.13 compile/contract/JS/synthetic benchmark smoke job. New **separate** Ubuntu PostgreSQL 17 fresh/upgrade/e2e job, whose environment is explicitly disposable and never contacts real QDP/B. Windows DB and real browser tests remain **NOT RUN**, not silently marked green. The workflow retains its existing on-push behavior; no CI workflow result or commit status was inspected here.
- `scripts/run_group_a_ga23_matrix.py` — optional local Windows/Linux QA runner. Outputs a fresh SHA-addressable JSON evidence report per actual local OS/Python/git HEAD, with status `PASS`, `FAIL`, `TIMEOUT` or `NOT_RUN` per invoked step. It never persists DSNs, raw logs or media paths. Windows PG17, actual QDP/B/PSI/Analytics integration, human-gold model accuracy are default `NOT_RUN`, even when all pure contract tests succeed. Does not launch or modify any other branch.
- No paid model inference, downloads, provider API calls, production user data, independent currentness callback, real source rights or external B commit are introduced by GA23 test sources. Synthetic tests use `trust_fixture` and must never be used as production verifiers.

## Cross-platform and peer compatibility matrix

| Gate | Linux | Native Windows | Real combined-peer disposable checkout |
|---|---|---|---|
| Python `compileall` Group A | READY TO RUN / **NOT RUN** | READY TO RUN / **NOT RUN** | NOT RUN |
| Pure frozen schema and contracts | READY / **NOT RUN** | READY / **NOT RUN** | NOT RUN |
| JavaScript Studio syntax | READY / **NOT RUN** | READY / **NOT RUN** | Browser E2E **NOT RUN** |
| Local FFmpeg/FFprobe/Tesseract synthetic fixture | READY / **NOT RUN** | Availability-dependent; **NOT RUN** | Real video pipeline **NOT RUN** |
| PostgreSQL 17 GA054–058 fresh migration | READY / **NOT RUN** | No PG service configured; **NOT RUN** | NOT RUN |
| PostgreSQL 17 pre-054 → GA054–058 upgrade and replay | READY / **NOT RUN** | **NOT RUN** | Migration filename/ordering conflict checks **NOT RUN** |
| Real PostgreSQL 17 GA04 → A003 → GA19 result → fake B ACK | READY / **NOT RUN** | **NOT RUN** | Real B read/ACK **NOT RUN** |
| Concurrency, revoked QDP/media, stale worker lease, result immutability | READY / **NOT RUN** | Pure contracts only, **NOT RUN** | Live rights event tests **NOT RUN** |
| QDP true event version, SRE/PSI replay and media catalog | Contract fake only; **NOT RUN** | **NOT RUN** | NOT RUN |
| Group B real `read_exact_a` and B1–B8, CTA ownership | Fake pinned consumer only; **NOT RUN** | **NOT RUN** | NOT RUN |
| Unified Analytics real read of A003/A004, Query Validation/PSI/taxonomy regression | **NOT RUN** | **NOT RUN** | NOT RUN |
| Human-reviewed rights-cleared RU/EN WER/CER, subtitle gold, music/SFX F1 | **NOT RUN** | **NOT RUN** | NOT RUN |
| 10,000 assets/month end-to-end latency, physical GPU/CPU memory, full load | **NOT RUN** | **NOT RUN** | NOT RUN |

"READY TO RUN" means only runnable source was committed. **No PASS without observed execution logs and matching branch HEAD SHA.**

## Local runbook — on a disposable machine/database

Run after checking out `feature/group-a-full-module` on the user's own machine. The runner itself never imports another module's private code and never switches branches.

```bash
# Both platforms: no database, no external pay-per-call; will record exact HEAD:
python scripts/run_group_a_ga23_matrix.py --output ga23-linux.json --media --all-group-a

# PostgreSQL 17 is a throwaway test instance, NEVER a production database:
export CM_TEST_PG_DSN='postgresql://.../ga23_disposable_test'
export CM_GA23_DISPOSABLE_ACK='yes'
python scripts/run_group_a_ga23_matrix.py \
  --output ga23-linux-pg17.json --pg17 --media --all-group-a
```

Windows PowerShell:

```powershell
python scripts/run_group_a_ga23_matrix.py --output ga23-windows.json --media
$env:CM_TEST_PG_DSN = 'postgresql://.../ga23_disposable_test'
$env:CM_GA23_DISPOSABLE_ACK = 'yes'
python scripts/run_group_a_ga23_matrix.py --output ga23-windows-pg17.json --pg17
```

Report `schema_version=ga23-local-compatibility-evidence/v1`, `git.commit_sha`, OS/Python, exit codes and output SHA of executed tests are enough to distinguish **executed** test cases from declared readiness. Observe actual unittest skip counts; `0` exit status with all PG17 tests skipped is NOT a PG17 PASS.

## Combined-peer integration — must be **separate throwaway branch**

**Do not merge peers into this Group A development branch.** The intended GA23 cross-peer check is on an independently created *disposable* integration worktree/branch from the current main, with the exact selected QDP, SRE/PSI, Group B and Analytics commits, coordinated migration version/schema namespaces, and no live production database or provider credentials. Test must explicitly assert:

1. Real QDP A-ready version and replay → only exact GA04 durable run; source/policy/QDP SHA/rights pinned at every use.
2. Actual verified SRE media versions/PSI transcript and cache hash source bridge; no shadow-media artifacts and no altered classifications.
3. GA18 video A003 + GA16/17 photo/mixed A004 use independently verified provenance; missing text/ASR/OCR remains partial and mixed carousel never forges global OCR completion.
4. Real Group B receives exact immutable `ga_results.id + result_sha256`; matches QDP/project/source/asset/policy/version, B owns CTA B7/B8 taxonomy, no circular A003 write.
5. Unified Analytics reads A003/A004 without modifying their meanings, joins, sources or previous historical data. Existing Query Validation, PSI and taxonomy regressions must still PASS.
6. Source revocation, cancelled worker, stale lease, idempotent replay and outbox ACK after real B commit, including same-identifier re-delivery without duplicate B rows.
7. Native Windows + Linux, FFmpeg/Tesseract, PostgreSQL 17 fresh + existing-schema upgrade + repeat with measured checksums and actual output links/HEAD SHAs.

These are **mandatory unverified integration obligations**, not success claims.

## Release blockers

No real QA run was executed for GA23 during this request. There is therefore **no evidence of green CI, no P0/P1 bug-free certification, no actual Windows/Linux/PG17 status and no final release permission**. The above jobs and tests may reveal failures when first run. Known earlier unresolved issues include mixed-carousel full-text continuity and the lack of independently validated real speech/music/subtitle gold; the user's planned actual E2E checks on their computer are still required.

**Next GA-MOD-24:** compare to current main, review only Group A-owned paths, map integration dependencies and migration ordering, prepare opt-in deploy/rollback instructions and PR/review checklist. Do NOT merge into main until required GA23 checks and peer integrations are actually run and passed.
