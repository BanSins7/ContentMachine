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
