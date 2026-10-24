# SRE Production V1 — implementation tracker

**Branch:** feature/sre-production-v1-20261023  
**Parent branch:** main  
**Base commit:** 4e51de5fc59f52d83e4fa71492d2d767689cd019 (2026-10-23)  
**Master specification:** docs/SRE_PRODUCTION_IMPLEMENTATION_PLAN_V1.md  
**Overall status:** S1 + S2 + S3 IMPLEMENTED — S4 offline runtime next; paid execution remains unavailable  
**Production paid calls performed under this branch:** 0  
**Merge into main:** NOT REQUESTED / NOT PERFORMED

## Status legend

TODO = work not started; IN_PROGRESS = active; BLOCKED = dependency; DONE = implementation, tests, and review notes complete. Planned feature steps are not treated as already working.

## Execution table

| Milestone | Name | Status | Dependencies | Acceptance evidence |
|---|---|---|---|---|
| S0 | Branch and durable plan | DONE | main SHA inspected | Isolated branch, spec and tracker files |
| S1 | Provider-agnostic contracts | DONE | S0 | 22/22 Linux and 22/22 Windows (targeted offline CI on fb5c6399), strict negative cases, documented API |
| S2 | RTB deep taxonomy adapter | DONE | S1 | Read-only approved RTB/SRE mirror, all 11 fields, H1/H2 reviewer provenance, category/fact/examples integrity; 23+7 tests Linux and Windows and real PostgreSQL E2E passed |
| S3 | Discovery D1 intake and policy | DONE | S2 | Explicit project-discovery intake, rights/review gates, versioned metadata, immutability and revocation; targeted Linux/Windows + PG17 CI passed |
| S4 | Offline run engine & fake E2E | TODO | S3 | PG queue, fake classifier, evidence, replay, crash tests |
| S5 | Financial quote/consent ledger | TODO | S4 | Decimal reserve/dispatch/settle and chaos tests |
| S6 | OpenRouter LLM adapter | TODO | S5 | Strict model pinned transport, controlled opt-in smoke |
| S7 | Embeddings and multi-model cascades | TODO | S6 | Reranking, cache, verified calibration, accuracy comparison |
| S8 | Human review and experiments | TODO | S4/S7 | Durable corrections, gold dataset and benchmark metrics |
| S9 | HTTP API and SRE Studio | TODO | S5/S8 | Authenticated localhost mutation and complete UI behavior tests |
| S10 | Collector + Analytics handoff | TODO | S4/S9; Collector contract | D1-backed intake, outbox, consumer idempotency |
| S11 | CI, rights, resilience hardening | TODO | S1–S10 | Fresh/upgraded PG17, Python 3.12, Node22, Windows, crash tests |
| S12 | Release and guarded merge | TODO | S11 | Reviewed draft PR, passing checks, approved merge SHA |

## Required evidence for DONE

1. Exact branch head SHA and date.
2. List of modified files and contract/migration versions.
3. Actual test commands and observed pass/fail counts.
4. Tests of project isolation, rights, idempotency, budget, and recovery whenever relevant.
5. Known limitations and links to follow-up work.
6. No claim of paid provider E2E unless user separately authorized the provider cost and execution.

## Conflict watch

- Open draft PR #20 — Collector offline query intake and registry; dependent integration is S10.
- Open draft PR #10 — separate legacy Collector preview; do not merge blindly.
- Open PR #13 — DTK manual TikTok Tool; SRE must not depend on its live runtime.
- Open draft PR #6 — old SRE foundation; much of its schema is already in main.
- Shared files: app.py, site/index.html, .github/workflows/tests.yml, intelligence/migrations/.
- Main may advance rapidly; before every step re-read current SHA, open PRs, migrations and overlapping files.

## Next coding checkpoint

Start with S4 (offline PostgreSQL-backed run engine, fake classifier and recovery). S1, S2 and S3 are implemented; do not assume Collector automation, media processing or paid calls are operational. Maintain existing behaviors in:
- intelligence/relevance/contracts.py
- intelligence/relevance/pipeline.py
- intelligence/relevance/presets.py
- intelligence/relevance/storage.py
- intelligence/taxonomy_builder/sre_bridge.py

Use fake providers and fixture D1 rows before any live model work. Do not begin paid execution or scrape external platforms by implication.

## Progress log

| Date | Status | Details |
|---|---|---|
| 2026-10-23 | S0 completed | Branch created from 4e51de5; implementation plan committed; tracker established. No engine code modifications. |
| 2026-10-23 | S1 implemented | V1 contracts in relevance/v1_contracts.py, v1_taxonomy.py, v1_runtime.py; docs/SRE_DATA_CONTRACTS_V1.md; original 22 offline unit tests; Linux + Windows S1 passed. Extended to 23 tests under S2 for real RTB reviewed facts. Draft PR #22. No paid calls or main changes. |
| 2026-10-23 | S2 completed | Read-only deep RTB loader and exact SRE projection/fact/example verification, canonical per-hypothesis contexts, 7 unit adversarial tests, real PostgreSQL human-reviewed deep-publication E2E. Targeted CI passed Linux/Windows/PG17 on 72b79f6. Full Linux CI 392 tests passed on earlier S2 SHA ff655971. Draft PR #22 remains open; main unaffected. |
| 2026-10-24 | S3 completed | Explicit D1 discovery->project association, reviewed semantic rights, metadata-only revisions, read-time revocation, append-only SQL guard, source/policy/observation/association lineage, QA with fake and disposable PG17. Tested S3 code commit 95c55cb. Full repository CI on this commit pending at tracker update; earlier full Linux CI on ea329b succeeded after updating legacy fixtures. No paid API calls or main edits. |

## S3 evidence and boundaries

- Verified S3 code SHA: 95c55cb2a515fd9b7b0c5a5047bab7b540f2591f; documentation and tracker HEAD may differ.
- Targeted CI: https://github.com/BanSins7/ContentMachine/actions/runs/40067535297 — **SUCCESS**; Linux: S1 23/23 + S2 7/7 + S3 pure 6/6; Windows: same; PostgreSQL 17: S2 integration + S3 rights, revision and immutable snapshot suites succeeded.
- Full Linux CI at tested code SHA: https://github.com/BanSins7/ContentMachine/actions/runs/40067535269 — SUCCESS, 410 tests (one skipped). Full Windows D1 smoke: https://github.com/BanSins7/ContentMachine/actions/runs/40067535267 — still running at this entry; inspect final GitHub conclusion.
- Full Linux CI at earlier S3 SHA ea329b481c1250611128c3fc1849869289778f0e — success after updating existing SRE storage fake policy fixtures, no production permission downgrade.
- Main code: intelligence/relevance/intake.py, source_policy.py, content_builder.py.
- Additive migration: intelligence/migrations/016_sre_d1_intake_rights.sql. Old checksummed migrations 001–015 not modified.
- Unit/PG tests: tests/test_sre_intake_unit.py, tests/test_sre_intake_pg.py; legacy test fixture changed in tests/test_relevance_storage.py only.
- Documentation: docs/SRE_D1_INTAKE_RIGHTS_V1.md.
- Rights: no implicit semantics for historical D1 manual sources, no external LLM grant by metadata consent, reviewed active policy required; SQL trigger/view reject stale source, old observation, non-boolean grants, deleted project association; S3 revisions append-only.
- S3 stores metadata-only, no media download, no fake hashtags/transcript/scenes; no classification, no spending. S4 runtime and complete cross-system physical retention purge remain deferred.
- Draft PR #22 remains unmerged; S3 branch is independent of Collector #20, Analytics #23 and Qualified #24.

## S2 evidence and boundaries

- Verified code SHA: 72b79f6edf9fca4945222c2e68c59b5f0cfeee57 (2026-10-23); follow-up documentation-only HEAD may differ.
- Targeted workflow: https://github.com/BanSins7/ContentMachine/actions/runs/39985744975 — SUCCESS.
- Observed per OS: S1 contracts 23/23 and S2 adversarial unit tests 7/7 on Linux AND Windows. Separate PostgreSQL17 real approved RTB->SRE test 1/1 on Linux (no network or paid calls).
- Full repository Linux CI (code SHA ff6559715da4cd298420dc7b0ce2b6a0e583dc4c): https://github.com/BanSins7/ContentMachine/actions/runs/39985219647 — SUCCESS, 392 tests, one skipped. A newer run including one additional nullable-provenance S1 test was pending when this entry was written.
- Windows full D1 smoke for verified code SHA: https://github.com/BanSins7/ContentMachine/actions/runs/39985744952 — check live conclusion; still in progress at this entry.
- New code: intelligence/relevance/taxonomy_adapter.py; taxonomy_mapping.py; hypothesis_context.py.
- Adjusted real RTB fact schema: intelligence/relevance/v1_taxonomy.py; tests/test_sre_contracts_v1.py.
- Tests: tests/test_sre_taxonomy_adapter.py and tests/test_sre_taxonomy_adapter_pg.py; dedicated Linux/Windows and PG17 workflow .github/workflows/sre-contracts.yml.
- Detailed architecture: docs/SRE_TAXONOMY_ADAPTER_V1.md.
- All S2 reads are transactional REPEATABLE READ READ ONLY, bound to project/run/publication; root RTB + SRE flat projection + mirrored categories, facts, fact edges and examples must match; no flat fallback, no new DB migrations, no paid API calls.
- S2 validates approved Taxonomy only. S3 D1 rights and metadata are implemented now; S4 worker and S5 financial enforcement are still NOT implemented.
- Draft PR: https://github.com/BanSins7/ContentMachine/pull/22. No merge requested; main stays separate.

## S1 evidence and boundaries

- Verified code SHA: `fb5c6399a21ae9855f8bce3410d187c2eeb291bf`
- Targeted workflow: https://github.com/BanSins7/ContentMachine/actions/runs/39977578466
- Observed: 22/22 S1 tests Linux; 22/22 S1 tests Windows; compileall pass in the same jobs.
- PR: https://github.com/BanSins7/ContentMachine/pull/22 (DRAFT, not merged).
- Full repository tests: https://github.com/BanSins7/ContentMachine/actions/runs/39977578108 (pending at this entry).
- Windows full D1 smoke: https://github.com/BanSins7/ContentMachine/actions/runs/39977578223 (pending at this entry).
- Existing relevance.contracts/pipeline/storage remain unchanged. No DB migrations, automatic classification, HTTP API or paid requests delivered in S1.
- Critical distinction: shape validation never authorizes a RTB publication, D1 rights or paid budget; S2/S3/S5 must implement DB-verified enforcement.

## Future update format

### S[n] — [name]
- Status:
- Implementation commit:
- Contracts/migrations:
- Changes:
- Tests run:
- Test results:
- Risks/blocks:
- Ready for next milestone:
