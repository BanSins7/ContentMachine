# QDP release / QA evidence ledger

## FIX-00 baseline, 2026-10-26

- Repository `BanSins7/ContentMachine`, working branch `feature/qualified-data-pipeline-v1-20261023`.
- Baseline SHA `f40949c833f735bc57e939d7db98c2b265f366cd`.
- On that exact baseline SHA: GitHub Actions QDP integration `39979057642` successful (PostgreSQL 17 + QDP offline/PG + syntax); Linux checks `39979057605` successful; Windows D1 smoke `39979057726` successful.
- This evidence predates newly added FIX-00 files. It does not validate a newer commit.
- No local repository clone could be fetched from this execution container (no network DNS to github.com). No `CM_TEST_PG_DSN`/local PostgreSQL 17 available; do not claim tests ran there.
- FIX-00 QDP-owned contract tests added to the isolated CI command. Verify new SHA and workflow run status after push.
- No OpenRouter, TikTok, provider, media download or paid external operations performed.
- No main or peer branch commits, SQL migrations or DB data modified.
- Live E2E: NOT RUN. Missing peer LIVE SRE and actual Collector→D1 ingest.


## FIX-01 code submission

New SRE intake and tests added on QDP branch (no SRE/Collector/main changes); no local live SRE or disposable PostgreSQL available. CI for preceding FIX-00 commit `c3132d85` was still absent when queried on 2026-10-26; previously successful workflows are from `f40949c8`, not new changes. This must be rechecked for the FIX-01 exact SHA before declaring PG17/Windows regression verified.

- FIX-01 follow-up: old idempotency-run replay rechecks SRE LIVE state; LIVE run picker now scans past rejected synthetic entries before returning a cursor. Regression tests added.

## FIX-02 evidence (2026-10-26)

- Coverage implementation commit: `280c3fa9d6f2b565aa6bdaf56c1ed28005767d15`.
- Unit/PG negative tests commit: `17e1a55386ff3ed96ed3d47edfa4dc75cf1a58d6`.
- Additional CI workflow now calls `tests.test_qdp_coverage`.
- Real PostgreSQL 17, exact-SHA GitHub Actions, actual SRE provider/FFmpeg E2E: **NOT EXECUTED/NOT VERIFIED**. Do not mark QA green based on old baseline.
- No schema_migrations deployed/rewritten; only test-disposable additions of SRE peer columns.


## FIX-03 migration evidence — 2026-10-26

- QDP DDL first committed at `19cffe41dafd81c48075fbc14e289c5600049a45`.
- Lineage normalization and schema tests at `a2414dac495b2bb40e082edb3f30653a2c9db878`.
- Server-side cross-run SRE lineage trigger at `bb2a6144bba56c35f56264396cdb2415cf8a4a66`.
- Negative no_match lineage PG test at `64491da157c45c9ed245f2aaf5cddd584b1a465b`.
- Legacy `016_qualified_data_pipeline.sql` Git blob SHA-1 recorded `e7a98e6a4cb2acf920ecb8a7160d3fb320eaf0ea` and protected by regression assertion.
- GitHub workflow extended to test new schema, migration checksum, and disposable PG17 semantics.
- Only SQL code and test definitions exist at this stage. LIVE cross-branch merge and new exact-SHA CI still **not observed**.
- No production SQL run, no paid calls, no new QDP branch.

- QDP forward migration/QA workflow commit: `0870bb2e28af081aae383d1a1a9add6daa4277cb`. A GitHub Actions run was queued at [40295657505](https://github.com/BanSins7/ContentMachine/actions/runs/40295657505) for this SHA; queued != passed. The new checklist therefore still has no completed strikethrough stages.


## FIX-04 resolver evidence — 2026-10-26

- Commits `31f5e199be9e5e91e72da2a943d916ec3fa56f7b` (resolver/replay/worker) and `4054a3ecc739b467c8ffcc10fc99dfa5e1868101` (unit + PostgreSQL regression).
- QDP-specific tests are wired into `.github/workflows/qdp-tests.yml`, but no completed result on these SHAs at stage review.
- GitHub Actions: [old queued PG17 40295657505](https://github.com/BanSins7/ContentMachine/actions/runs/40295657505), [FIX-04 code queued 40297122535](https://github.com/BanSins7/ContentMachine/actions/runs/40297122535), [FIX-04 tests queued 40297466955](https://github.com/BanSins7/ContentMachine/actions/runs/40297466955).
- All added test cases are **written, not yet passed**; old baseline success at `f40949c8` cannot validate these changes. No live SRE/Opus, media or paid external calls were performed.
