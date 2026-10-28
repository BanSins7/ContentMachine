# ADR SP-00: Search Planner is a merge-ready standalone ContentMachine submodule
Status: **ACCEPTED FOR DEVELOPMENT**, 2026-10-26. Scope: design freeze, not feature implementation.
Working branch: `feature/search-planner-full-integration`.

## Context

SP1–SP8 implementations already exist on current merged `main`, but the service/HTTP/UI/daemon/production model provisioning and downstream planning/Collector bridge are missing. PR #20 implements draft Collector registry/planner/worker on an older diverged branch and cannot be treated as a compulsory SP import. PR #31 proposes Deep C9 V2 but has not published a V2 taxonomy. Main now contains merged A8/B9 source intelligence and a *separate* flat B9→C execution route. Historically the SP-FIX plan described patching Collector directly; the user explicitly requires ONE independent, complete SP module that can be merged into main whenever release-qualified.

## Decision 1. Module ownership and package isolation

SP owns `intelligence/search_planner/`: source loading, versioned context, OpenRouter generations and cost ledger, immutable candidates, semantic validation/MMR, capability-consumer **port**, provider-neutral plan preview, its own durable inbox and optional Collector delivery port. SP will have a small additive `app.py` router mount and SPA UI scripts. SP *never* imports `intelligence.collector` unconditionally, never copies Collector `registry/planner/worker`, never launches provider search or reports real videos without upstream evidence.

Collector owns its concrete source registry, provider adapters and actual external execution, polling, normalization and Discovery handoff. SP query generation consent does not authorize Collector tasks. In the standalone/SP-only installation, `CollectorPort` is absent → truthful `awaiting_collector` state with no downstream ACK or network side effects.

Alternatives rejected: duplicate Collector registry in SP (two sources of truth), partial code transplant from PR #20 (fragile upgrades), require PR #20 merged before SP import (violates standalone release), treat SP no-Collector inbox as video search success (false).

## Decision 2. Publication and evidence source of truth

SP1 reads only true project-scoped, deeply approved C9 publication and verified two human reviews from PostgreSQL. Legacy `hypothesis-context/v1` and immutable historical `sqg_*/qv_*` remain readable.

Real B9 snapshot uses `knowledge-snapshot/v1` with SHA-named `fact_id`, `context_id`, `knowledge-c-handoff/v1`; merged flat B→C route is not Deep C9. Deep C9 V2 will be enabled only when its version, A/B snapshot references, 14 fields, review/publication and schema signatures exist as immutable facts. C PR #31 has only initial contracts; it is not an implemented V2 publication.

Alternatives rejected: infer v2 from presence of optional attributes, copy whole video analysis into every hypothesis, change historical C9 V1 or rehash source facts as anonymous new facts.

## Decision 3. Versioned producer/consumer interface

Actual QV is `validated_queries/v1`, actual Collector draft `validated_query_set/v1`; cannot directly pass one to the other. SP owns QV verification and **adapter-neutral** canonical projection, with `candidate_id` UUID unchanged as downstream `query_id`. Retain original `qv_run_id`, `qv_outbox_id`, facts, branch, taxonomy and model/source fingerprints; projection is not the authoritative bundle. Deterministic chunks of at most 50 queries unless Collector accepts a negotiated alternative in the future. Zero selected is terminal `no_work`. Source capabilities come through versioned immutable snapshots, fail-closed when absent, unsupported, unhealthy, unpaid pricing unknown or scope mismatch.

No browser JSON containing `validation_state=accepted` becomes executable. Any handler must obtain current authoritative QV + C9 rows and verify selected decisions/project/ancestry/hash.

## Decision 4. Durable handoff semantics

Use a **separate SP-owned inbox/ledger** and a separate Collector delivery ledger/receipt. The upstream `qv_outbox` event is durably ingested into SP inbox and acknowledged in the SAME PostgreSQL transaction (lock original row + unique source event FK + insert verified data/lineage/no_work + ACK). The SP inbox records `awaiting_collector` until an installed adapter returns confirmed durable Collector acceptance. A missing adapter cannot ack Collector delivery. If later delivery times out, allow read-only reconciliation and idempotent replay; never infer success or blind resubmit a paid provider call.

If stronger end-to-end semantics can be achieved by deferring QV ACK until Collector insertion in a given deployment, it must still preserve the separate SP inbox durability and replay protocol; owner-facing state must never equate upstream QV ACK with downstream receipt.

## Decision 5. API/process and authorization

The local application uses Host/Origin and `X-ContentMachine: 1` mutation boundary today, but future external/multiuser deployment needs explicit owner/session auth (local header alone is not user identity). Write operations use project scope, idempotency keys and JSON/body limits. All GET/status/preview paths must be network-cost-free. Worker and paid transports run separate from HTTP/DB locks, with exact quote/model/schema/version/consent and financial reservation before any POST. Unknown paid response stays `uncertain` with reserved funds until reconciliation; no blind repeat. Collector requires its own execution approval.

## Decision 6. Migrations and release

`intelligence/migrate.py` keys by complete basename and checksums applied SHA, sorting lexicographically. Historical SP migrations 015/016 remain untouched. Main already contains 030–053; later C migration may advance higher. SP SQL uses **new additive basenames chosen only after inspecting HEAD**, UUID/project-scoped FK, append-only immutable source/projection and unique idempotency keys. Fresh+upgrade PostgreSQL QA mandatory.

User approval of code merge is distinct from branch feature completion. Final SP PR must build/launch on current main with no Collector package, pass local application and full mocked SP pipeline through inbox, plus optional mock-Collector receiver tests on Windows/Linux. Live paid tests are never automatic.

## Decision 7. Planned file ownership per release slice

| Slice | Paths / owners | Prohibited at that slice |
| --- | --- | --- |
| SP-00 audit | four `docs/SEARCH_PLANNER_{INTEGRATION_MAP,INTERFACE_MATRIX,SP00_BASELINE_QA,MODULE_ADR}.md` | runtime/SQL changes |
| SP-01 foundation | `intelligence/search_planner/{__init__,service,api_contracts,configuration,ports}.py` | copied Collector runtime |
| SP-02 C9 v2 | SP `contracts.py, input_loader.py, context_builder.py` + versioned schema | C V2 without approved C schema |
| SP-03–05 operations/query | SP `generation_engine/`, `ai_query/`, `operations/` | automatic paid retries |
| SP-06–08 semantic/QV | SP `query_validation/` | test fixture in production |
| SP-09–10 capabilities/planning | SP `capabilities/`, `task_planning/` | second concrete source registry |
| SP-11–12 handoff | SP `handoff/`, new additive SQL | Collector query_set inserts via frontend |
| SP-13–15 API/UI/process | SP HTTP, minimal app dispatch, `site/search-planner.*`, operations | paid GET, blocking web server |
| SP-16 release | QA/CI/docs/upgrades and PR | merge without explicit review |
| SP-17 feedback | SP `feedback/` and actual downstream connectors | synthetic search-performance claims |

## Consequences / open risks

- SP's standalone module can **prepare** and store source-neutral plans but cannot execute external searches until Collector provides a compatible authorized adapter. This is a deliberate and observable integration boundary, not a failure of SP delivery.
- C9 Deep V2 is version-dependent, independently developed in PR #31. SP must leave legacy V1 functional while V2 waits for real C9 publication.
- Older Collector PR #20 is behind newer A/B main; its schema/migration must be rebased by its owner, not transplanted by SP. A joint contract test/handshake will be required after both implementations stabilize.
- Production sentence-transformer weights must be provisioned, fingerprinted and tested; mocked lexical fixtures never prove actual retrieval relevance.
- No fresh/upgrade database tests or new SP branch CI have run in SP-00 execution; upstream main CI PASS is evidence only for baseline, not post-change release.
- Versioned source snapshots and real prices/readiness can block a plan legitimately; never replace unknown with zero cost or fabricated verified provider support.

## ADR verification hooks

The next stage must test import without Collector, canonical SHA equality, public DTO contract versioning, project-scoped service port, no direct provider calls from service creation. Final stages test 117→50+50+17, 0→no_work, consistency/replay/PG crash, independent recipient receipts, user consent and billed uncertainty. Review this ADR before any SP-owned SQL/API is frozen.
