# Search Planner SP-00 — Baseline QA / Migration Freeze
Date: 2026-10-26. Audited: `main=218f5f3163cc`, SP pre-SP-00 `61278c9af75e`. **This is observed upstream CI evidence plus static repository review, not tests newly executed on the SP branch.**

## 1. Test environment availability and truthfulness

| Check | Evidence | Status for SP-00 |
| --- | --- | --- |
| Main branch Linux GitHub Actions | https://github.com/BanSins7/ContentMachine/actions/runs/40284202868 ; event `push`, exact head `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` | **PASS on base main** |
| PostgreSQL migration apply | same workflow log, PostgreSQL 17 | **PASS on base main**, 49 SQL migration basenames applied |
| Python unit + PG integration | same log: `Ran 1677 tests in 978.940s` / `OK` | **PASS on base main**, not yet re-run on SP branch |
| PSI Stage14 release gate | same log: `technical_gate=pass`, 49 applied, PostgreSQL17 | **PASS on base main** |
| Existing UI and JS / Python compile | same job step conclusions, all success | **PASS on base main** |
| Prior Windows CI on B PR #30 exact head `f0c9e76a8eb270a66c3654cde28a1dc0b5df21a7` | workflow `40216078220`, Windows D1 smoke, success | **Historical PASS on PR head**, not on merge `main=218f5f3` or SP branch |
| Collector PR #20 Linux / Windows | `40070999834` / `40070999885`, both completed success, exact Collector head `a4f658de` | **PASS on old Collector head**, not integrated with current main |
| SP branch `61278c9` or SP-00 doc commits | GitHub Actions lookup by SHA yielded no workflow runs for `61278c9` | **NOT RUN / NOT PROVEN** |
| C Corrector PR #31 | draft `deff6c9`; no full Deep V2 publication | **DEPENDENCY NOT READY** |
| Local Python tests / fresh disposable PostgreSQL | no checked-out GitHub repository, no `psycopg` or `psql` in current execution sandbox; git remote cannot resolve | **NOT RUN** |
| Production OpenRouter and embedding weights | no live secrets/calls/downloads attempted | **NOT TESTED / NO SPEND** |

Never treat upstream `main` green as SP-00 branch-green. CI must be executed from a PR or explicit job at a precise HEAD before claiming release.

## 2. Reproducible QA commands (when repository runner is provisioned)

```bash
# On an isolated disposable PostgreSQL 17, NOT operator's production DB:
export CM_TEST_PG_DSN='postgresql://.../contentmachine_disposable'
CM_DB_DSN="$CM_TEST_PG_DSN" python -m intelligence.migrate

python -m unittest discover -s tests -p 'test_search_planner_foundation.py' -v
python -m unittest discover -s tests -p 'test_search_planner_engine_contracts.py' -v
python -m unittest discover -s tests -p 'test_search_planner_engine_cost.py' -v
python -m unittest discover -s tests -p 'test_search_planner_engine_stage_c.py' -v
python -m unittest discover -s tests -p 'test_search_planner_stage_d.py' -v
python -m unittest discover -s tests -p 'test_search_planner_query_validation.py' -v
CM_TEST_PG_DSN="$CM_TEST_PG_DSN" python -m unittest discover -s tests -p 'test_taxonomy_deep_db.py' -v
CM_TEST_PG_DSN="$CM_TEST_PG_DSN" python -m unittest discover -s tests -v
python -m compileall -q intelligence/search_planner
node --check site/app.js
node tests/test_taxonomy_deep_ui.cjs
```

For Windows, run the existing `windows-d1-smoke.yml` on the final SP PR head. For Linux `.github/workflows/tests.yml`: PostgreSQL 17 Alpine, Python 3.12, Node22, `python -m intelligence.migrate`, Python unittest discovery, PSI release gate, JS syntax, UI behavior and Python syntax. No live paid provider calls in CI.

Planned SP-specific mandatory future QA:
- C9 v1 replay and v2 (when authoritative schema exists).
- Embedding model pinned weights and no test-fixture handoff.
- Zero-work / 117→50+50+17 / wrong project / duplicate event / concurrent ACK.
- Standalone import and app boot with Collector module absent.
- Durable SP inbox atomically ACKs QV upstream; Collector acceptance stored separately.
- Mock model + QV + plan + mock Collector end-to-end; charged uncertainty remains reserved, no retry.
- Fresh PostgreSQL 17 and existing-schema upgrade on **SP final HEAD**.
- Windows and Linux CI green on final HEAD.

## 3. Migration audit

Actual `intelligence/migrate.py` behavior:
- Migration identity is the **complete filename basename**, not a numeric prefix.
- Enumerates `sorted(MIGRATIONS.glob("[0-9]*.sql"))` **lexicographically**.
- Transaction / advisory lock `82931674013`, `schema_migrations(version TEXT PRIMARY KEY, sha256 TEXT NOT NULL)`, SHA256 check forbids editing previously applied files.
- Base `main` includes **49** migration files, highest current `053_ke_c_qualified_execution.sql` (B Stage14 flat C).
- Historical SP `015_search_query_generation.sql`, `016_query_validation.sql` already applied/immutable.
- Collector PR #20 contains `016_collector_planning.sql` and `017_collector_worker.sql` in its older separate branch. Two prefix-`016` filenames are distinct versions; ordering is `016_collector_planning.sql` BEFORE `016_query_validation.sql` when both exist; test migrations/FKs on actual integrated schema later.
- C Corrector PR #31 currently has no migration; later V2 migrations may occupy `054+`.

**Reserved naming policy:** SP's first new migration candidate `054_sp_operations.sql` is **provisional only**; before writing it, re-fetch latest `main` / C branch SQL. If any `054*` is published or dependencies require otherwise, use the next safe basename. Do not rename/edit already deployed 015/016 or merged 030–053; no silent reordering. All new SP SQL additive and project scoped. Never claim fresh-and-upgrade passed merely because upstream base fresh-install passed.

Current schema owner map:
- C9 V1: `tb_deep_publications`, `tb_taxonomy_publications`, reviews, `tb_runs`.
- B9/A8: `project_context_snapshots`, `knowledge_approved_snapshots`, `knowledge_c_handoffs`, `knowledge_c_generation_runs`.
- SP generation: 11 `sqg_*` tables in 015; validation: 4 `qv_*` tables in 016.
- Collector draft: `collector_query_sets/plans/runs/provider_tasks/task_queries`, source DB/job references in PR #20 only; DO NOT assume they exist on main.

## 4. SP-00 review checklist

- [x] Actual GitHub refs and open PR statuses recorded.
- [x] SP1–SP8 functions, exact QV schema, old Collector DTO and B9/C draft checked.
- [x] Authoritative C9 V1 vs B9 flat C vs future Deep V2 distinguished.
- [x] Caller-controlled `accepted` query cannot enter executor merely through JSON.
- [x] Real migration identity/lexicographic ordering recorded; no applied files edited.
- [x] Main exact-SHA GitHub Linux + upstream Windows/Collector baseline recorded.
- [ ] New SP branch Python/PG/UI/Windows/Linux CI **not executed** — required before release and after code stages.
- [ ] Real Deep C9 V2 schema/publication unmerged — verify in SP-02 before enabling.
- [ ] Final SP→Collector handshake and projection not implemented — SP-11/12.

### SP-00 acceptance summary
**Contract/design freeze complete; runtime verification remains an explicit deferred gate.** The stage has no business logic/SQL/API changes and sends zero paid requests. Stage SP-01 may start with this inventory. Do not call the whole Search Planner implemented.
