# SRE Production V1 — implementation tracker

**Branch:** feature/sre-production-v1-20261023  
**Parent branch:** main  
**Base commit:** 4e51de5fc59f52d83e4fa71492d2d767689cd019 (2026-10-23)  
**Master specification:** docs/SRE_PRODUCTION_IMPLEMENTATION_PLAN_V1.md  
**Overall status:** S1 CONTRACTS COMPLETE — S2 READY; runtime execution not yet implemented  
**Production paid calls performed under this branch:** 0  
**Merge into main:** NOT REQUESTED / NOT PERFORMED

## Status legend

TODO = work not started; IN_PROGRESS = active; BLOCKED = dependency; DONE = implementation, tests, and review notes complete. Planned feature steps are not treated as already working.

## Execution table

| Milestone | Name | Status | Dependencies | Acceptance evidence |
|---|---|---|---|---|
| S0 | Branch and durable plan | DONE | main SHA inspected | Isolated branch, spec and tracker files |
| S1 | Provider-agnostic contracts | DONE | S0 | 22/22 Linux and 22/22 Windows (targeted offline CI on fb5c6399), strict negative cases, documented API |
| S2 | RTB deep taxonomy adapter | TODO | S1 | Full immutable 11-field hypothesis lineage and tests |
| S3 | Discovery D1 intake and policy | TODO | S2 | Approved metadata snapshots, source rights, revocation tests |
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

Start with S2 (RTB deep taxonomy adapter); S1 is finished. Maintain existing behaviors in:
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
| 2026-10-23 | S1 implemented | V1 contracts in relevance/v1_contracts.py, v1_taxonomy.py, v1_runtime.py; docs/SRE_DATA_CONTRACTS_V1.md; 22 offline unit tests; GitHub Actions Linux + Windows S1 CI succeeded at fb5c6399a21ae9855f8bce3410d187c2eeb291bf. Draft PR #22. No network/paid calls, no main changes. Full regression and Windows D1 suite reruns pending at tracker update. |

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
