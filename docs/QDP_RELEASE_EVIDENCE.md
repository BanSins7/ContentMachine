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


## FIX-05 SRE inbox evidence — 2026-10-26

- `8133e98de8f0aabfb74630d64b719530dc6d973e`: QDP SRE run completion receiver + additive 041 forensic quarantine schema.
- `9260c5a13fdc016ca020c791be9cb3603fba0287`: race-safe duplicate replay, 041 migrations topology/checksum preflight.
- `6aec28fdb568da79b46032879a6e2d4d9f92b5d2`: 15 named unit/PG scenarios; CI workflow includes test_qdp_sre_inbox and 041 SQL.
- Source-validated against SRE `sre-analytics-event/v1` summary schema at SRE PR #22 `695942aa`, with per-item `sre.classifications_ready/v1` deliberately not accepted.
- No real SRE event producer, source DB migration integration, exact-SHA green PG17, or paid provider calls. Tests **authored, not passed** until CI completion.


## FIX-06 submitted / GitHub Actions queue (2026-10-26)

- `144d585ca5b046da1f217945d02d603a900ae887` immutable manifest source and transaction integration; `73907bffe9cf2cd6491438a17da769375cb16802` pure/PG17 tests, including 12 concurrent `create_run` calls; `8ca25a57f82b1f915723e3905474df20c9b4c038` PG fixture correction.
- Expected SQL actions: atomically insert 1 `qdp_run_manifests` per run + 1 `qdp_item_lineage` per SRE video/leaf (includes no_match) + exactly one job per qualified canonical video. No provider work.
- GitHub Actions runner assignment is the observed blocker: new jobs queued (no `runner_name`) on workflow `QDP isolated integration`. E.g. [FIX-06 run](https://github.com/BanSins7/ContentMachine/actions/runs/40304063101). **No new tests PASSED**. Root cause unknown, not a Python test failure.


## Runner triage and QA portability — 2026-10-26

- `cb5969efbbf3ce5ff5daaed28473adc0ec34b513`: added guarded local cross-platform QDP QA runner, fallback when GitHub-hosted runner is unassigned, JSON evidence artifact and triage document.
- `cf85b17e5801f3cae1da47a33544127c7154d8fd`: QA runner guards actual PostgreSQL **17** and prevents skipped PG suites from being reported as PASS.
- GitHub-hosted CI latest visible run: [40305461109](https://github.com/BanSins7/ContentMachine/actions/runs/40305461109), **queued** at inspection; earlier new runs cancelled by concurrency, not test failures. No completed test evidence on FIX-06 SHA.
- QA script is available for the user's own full clone; script was not executed on the actual repo in this sandbox because git clone DNS is unavailable and no PG17 database/driver is present. Never imply local tests passed.

## Confirmed CI test failures and resolution — 2026-10-26

[40305617307](https://github.com/BanSins7/ContentMachine/actions/runs/40305617307) on `d616f8a3` completed **FAILURE** (105 tests; failures=2; errors=3). All five traces reviewed: 3 × KeyError(0) due to test dict_row migrator, 1 × category-order assertion, 1 × lineage expected=1 instead of 2. Regression repair commit `e2844c8ee6efbd7c0e581a40b313915dbfe6624e` ran on disposable PostgreSQL 17: [40307133052](https://github.com/BanSins7/ContentMachine/actions/runs/40307133052) **SUCCESS**, all workflow steps including database migrations/QA and Python/JS checks. Do not retroactively mark the failing run green.

## FIX-07 exact metadata observation — 2026-10-26

- Code commits `7cf8496f3ec736e108d856a172f023a861463c83`, `50266593fd081bfa62cb034e76cc1a9823980af4`, `44c5e88de9f8535955cd039866877a8d754fe3e2`, `8c904a5704cfb3e8d6db41ee8edd8d7a25c8db87`, `1ac40daed30a270e113b9afb2fa6665c85e587d5`.
- New `provenance.py`, intake/selection/manifest pinned D1 observation + SRE project discovery; no implicit newest-metadata lookup, no cross-source fallback, unknown publication date not invented.
- 7 pure mocked tests + 4 additional PG17 exact-observation tests. SRE-owned real peer migration `016_sre_d1_intake_rights.sql` remains EXTERNAL and was only simulated in **disposable** QDP test DB.
- FIX-07 code complete; **current exact SHA PG17 CI not yet PASS** at this note. LIVE SRE cross-branch smoke BLOCKED_EXTERNAL.


## FIX-07 first exact-SHA CI failures and repair — 2026-10-26

- [40307884044](https://github.com/BanSins7/ContentMachine/actions/runs/40307884044) on `1ac40dae`: real PostgreSQL 17 job completed **FAIL**; 116 tests, **2 errors**, 0 assertions failed. Error #1 mocked `metadata={}` triggered `KeyError('title')` in provenance read. Error #2 legacy negative metadata test used hard DELETE of referenced D1 observation; SRE S3 FK correctly rejected deletion (`ForeignKeyViolation`).
- Repair `b677a8dd7f150a3b1dfabc8d897b995c4f13c9c0`: provenance treats empty/unavailable mapping as `pinned_metadata_unavailable_or_revoked`; PG fixture models soft deletion (`is_current=false, deleted_at=now()`), maintaining immutable FK lineage.
- [40308355403](https://github.com/BanSins7/ContentMachine/actions/runs/40308355403) (repair SHA) was **QUEUED** when checked. Do not record QA_PASS on FIX-07 until a successful run actually completes.


## FIX-07 verified green PG17 — 2026-10-26

- Verified [Actions run 40308355403](https://github.com/BanSins7/ContentMachine/actions/runs/40308355403) on exact code SHA `b677a8dd7f150a3b1dfabc8d897b995c4f13c9c0` **SUCCESS**.
- Log: `Ran 116 tests in 3.425s\nOK`. QA JSON `overall=PASS`, `pg17_status=PASS`, `python_syntax=PASS`, `js_syntax=PASS`, `tests_run=116`, `tests_skipped=0`, `tests_failed=0`, `tests_errored=0`. Migration and artifact-upload workflow steps succeeded.
- Previous failure [40307884044](https://github.com/BanSins7/ContentMachine/actions/runs/40307884044) is recorded as FAIL, not overwritten; repair `b677a8dd` fixed two cases.
- Scope: independent QDP migration + disposable simulated SRE S3 peer schema. No real SRE producer, external LIVE, Group A/B/C E2E or paid calls. These remain BLOCKED_EXTERNAL.


## FIX-08 verified green PG17 — 2026-10-26

- Exact code SHA `8135b7620dcd73b4c6e91e18e8c854f7f13ba9c5`. GitHub Actions [40310914445](https://github.com/BanSins7/ContentMachine/actions/runs/40310914445) completed **SUCCESS**.
- Verified raw QA log: `Ran 135 tests in 3.863s\nOK`; `QDP_QA_REPORT`: overall=PASS, pg17_status=PASS, python_syntax=PASS, js_syntax=PASS, tests_run=135, tests_skipped=0, tests_failed=0, tests_errored=0. Migration, test, artifact steps all SUCCESS.
- Owner-only metric history selection and Group C input pointers, **not** breakout score calculation. No peer LIVE integration, provider calls or paid work.


## FIX-09 media tests and verified QA — 2026-10-26

- Initial media implementation `7a48245dcc45176116ee94beb7edbb159e29d2f3`, initial [40312069484](https://github.com/BanSins7/ContentMachine/actions/runs/40312069484) FAIL one obsolete test expected `media_rights_or_asset_missing` while new precise code yielded `media_rights_missing`.
- Test/fix commit `0ed73d76df65cffcc7b16ea965da417f3baed5fc`, exact [40312315651](https://github.com/BanSins7/ContentMachine/actions/runs/40312315651) **SUCCESS**: `Ran 154 tests in 6.392s\nOK`, `overall=PASS`, `pg17_status=PASS`, `unit_status=PASS`, `python_syntax=PASS`, `js_syntax=PASS`, `tests_skipped=0`, `tests_failed=0`, `tests_errored=0`.
- Scope: QDP media metadata/path/hash permission checks and Group A/B preparation, not playable media proof. No external media download, Group A FFmpeg, Group B media manifest or LIVE inter-branch E2E was executed. See [QDP_MEDIA_READINESS_HANDOFF.md](QDP_MEDIA_READINESS_HANDOFF.md).


## FIX-10 new independent group contract — 2026-10-26

- Code 6743f6ee / 18a1166f / 735adc59 / 4931989f includes `preparation.py`, `042` + `043` additive QDP-owned SQL, current group read, 11 unit+4 PG17 test cases. `016`, `040`, `041`, peer SQL untouched.
- [40314043492](https://github.com/BanSins7/ContentMachine/actions/runs/40314043492) first FIX-10 code-only SHA `6743f6ee`: **SUCCESS** (old suite, not FIX-10 new tests).
- [40314193155](https://github.com/BanSins7/ContentMachine/actions/runs/40314193155) and [40314590625](https://github.com/BanSins7/ContentMachine/actions/runs/40314590625) **FAIL** one legacy end-to-end metadata visibility assertion after overstrict item-status gate. Both preserved as failed evidence.
- Repair 4931989f introduces **043 forward migration** to remove only item status filter from authorized metadata view. New exact-SHA runner not yet verified; do NOT infer PASS from prior 154/154 FIX-09.


## FIX-10 verified clean PostgreSQL QA — 2026-10-26

- Exact code SHA `4931989ff51f4e89d12618acfddc5bae8cfd5357`, [workflow 40314853464](https://github.com/BanSins7/ContentMachine/actions/runs/40314853464) **SUCCESS**.
- Job logs: `Ran 169 tests in 6.858s\nOK`; QA JSON `overall=PASS, pg17_status=PASS, unit_status=PASS, js_syntax=PASS, python_syntax=PASS, tests_run=169, tests_failed=0, tests_errored=0, tests_skipped=0`. New migrations 042 and 043 installed on a disposable PostgreSQL 17 instance; repeat checksum guard and Python/JS syntax successful.
- Old runs 40314193155 and 40314590625 genuinely failed one regression before 043; no later overwrite of those logs. No real peer Group A/B/C paid job or multi-branch LIVE integration performed.


## FIX-11 executed PG17 evidence — 2026-10-26

- Implementation `fc12a458`, test `50b7d9ab`, injection fix `91769fa0`, psycopg LIKE parameterization fix `3b623e1b`.
- [40316427951](https://github.com/BanSins7/ContentMachine/actions/runs/40316427951) FAILED with 2 test errors (invalid mock and SQL LIKE); [40316619295](https://github.com/BanSins7/ContentMachine/actions/runs/40316619295) FAILED with 3 psycopg SQL LIKE errors. Previous failures retained.
- [40316720077](https://github.com/BanSins7/ContentMachine/actions/runs/40316720077) on code SHA `3b623e1b690fc5fc9a5b989ffa467b57fd9e2afb` **SUCCESS**: `Ran 180 tests in 6.767s\nOK`; `overall=PASS, pg17_status=PASS, python_syntax=PASS, js_syntax=PASS, tests_run=180, tests_failed=0, tests_errored=0, tests_skipped=0`; migrations and evidence upload passed.
- Cross-branch SRE/Group A/B/C LIVE E2E, actual delivery and consumer ACK remain **BLOCKED_EXTERNAL / FIX-12**, not part of these results.


## FIX-12 verified delivery, retry and ACK — 2026-10-26

- `8535ef5a8238ca688db43cff78f4c0f19dbe11a4`: first source implementation of `outbox_delivery.py`; `b196ca2e`: tests; `695fa3c3`: 50 direct consumer replays/ten dispatchers; `8c6e78ba`: append-only journal 044; `8111425b`: journal tests; `5283884425a0326b40fd182607f74b453f11621e`: forward 045 preserving FK-cascade cleanup.
- [40318140710](https://github.com/BanSins7/ContentMachine/actions/runs/40318140710) 195/195 PASS; [40318478990](https://github.com/BanSins7/ContentMachine/actions/runs/40318478990) 196/196 PASS without journal; [40318748792](https://github.com/BanSins7/ContentMachine/actions/runs/40318748792) FAIL with 7 errors and 1 failure in 196 tests, caused by audit DELETE trigger blocking FK-cascade cleanup during isolated test teardown, contaminating later test expectations.
- **Verified final [GitHub Actions 40319126522](https://github.com/BanSins7/ContentMachine/actions/runs/40319126522) SUCCESS on code SHA `5283884425a0326b40fd182607f74b453f11621e`**: `Ran 197 tests in 12.649s\nOK`; evidence JSON `overall=PASS, pg17_status=PASS, python_syntax=PASS, js_syntax=PASS, tests_run=197, tests_failed=0, tests_errored=0, tests_skipped=0`. PostgreSQL migrations and artifact upload passed.
- Disposable consumer receiver created a test-only inbox/job table; NO actual Group A/B/C integration, paid job, media analysis, FFmpeg, or LIVE SRE end-to-end was executed. ACK represents durable acceptance only.


## FIX-13 combined QDP / real Group A inbox acceptance (2026-10-26)

- Main QDP code SHA `a39459dc031ef63377321dbb6af2099c8265e7c2` and independent Group A SHA `cc1d43497aa70b6f1831b104d835c8674861e9ab`, [GitHub Actions 40321549312](https://github.com/BanSins7/ContentMachine/actions/runs/40321549312) **SUCCESS** both jobs.
- QDP standalone job: `Ran 206 tests in 10.729s\nOK`, QA JSON `overall=PASS; pg17_status=PASS; python_syntax=PASS; js_syntax=PASS; tests_run=206; tests_failed=0; tests_errored=0; tests_skipped=0`.
- Independent GA peer job: applies combined forward-only QDP migrations and GA-owned 054/055 to one disposable PostgreSQL 17; runs `tests.test_qdp_group_a_peer_pg.PeerQDPGroupAHandoff.test_real_peer_inbox_job_ack_and_fifty_replays` => **ok / OK**. Verified Group A receiver committed 1 real `ga_inbox`, 1 `ga_runs`, 1 `ga_steps`; QDP receipt ACK persisted; 50 direct replay calls did not create another GA step.
- No real media FFprobe/FFmpeg decoding, paid Group jobs, OCR/ASR, or SRE LIVE multi-branch producer tests. Input file is a synthetic container header with verified bytes and rights, not a playable production video. `main` and GA branch unchanged.
