# Content Machine — Search Planner: standalone, merge-ready module

**Decision:** 2026-10-26. **Owner:** Search Planner development branch `feature/search-planner-full-integration`.
**Status:** target architecture / release contract, NOT a claim that implementation or tests have passed.

## 1. Goal and branch ownership

The branch must deliver a **complete, integrated Search Planner module**, not isolated SP-FIX patches. Everything implemented in this branch must be independently mergeable into the then-current `main`, without merging the separately developed Video Collector branch first.

- SP branch owns SP1–SP8 generation and validation, SP-owned orchestration, API, UI, operations, database changes, module-owned contracts, validation and release QA.
- Video Collector branch owns provider-specific capabilities/adapters, external search dispatch, pagination, inspection, normalization and delivery to Discovery.
- SP must not rewrite Collector-owned provider code, or copy `intelligence/collector/{registry,planner,worker}.py` from an unmerged branch.
- Prior SP1–SP8 implementation already inherited from `main` is retained; changes are additive and backward-compatible unless an explicit versioned migration is justified.
- `main` and `feature/video-collector-dev-20261023` remain unmodified by SP development. All SP commits go to this branch.

## 2. Definition of a self-contained SP deliverable

```text
ContentMachine app/UI
    -> intelligence.search_planner HTTP service + owner approval
    -> SP1 approved C9 publication loader
    -> SP2 HypothesisContext v1/v2
    -> SP3 generation config, batching, quote, budget consent, durable workers
    -> SP4 AI Query (pinned one completion per batch, OpenRouter)
    -> SP5 immutable candidate output + sqg_outbox
    -> SP6 true local semantic validation (provisioned pinned model)
    -> SP7 exact/semantic dedup + MMR
    -> SP8 validated_queries/v1, qv_runs/decisions/coverage/outbox
    -> SP9 external capability catalog port (versioned declared/readiness/policy)
    -> SP10 provider-neutral deterministic planning/preview/coverage/limits
    -> SP11 durable event inbox / integration outbox with distinct execute approval
         -> optional Collector adapter -> Collector trusted ingest / held tasks
    -> SP12 future measured feedback input port; real metrics only after Collector+SRE.
```

Internal SP can complete through a durable, non-executable handoff envelope **without Collector present**. When the Collector adapter is unavailable, leave event in `awaiting_collector` or equivalent durable status, make the reason visible, do not acknowledge a downstream receipt that never occurred, and never send external provider requests. Once Collector is installed, the adapter must validate schema/version and complete the durable handoff idempotently.

Standalone means independently installable and mergeable **within ContentMachine**, not a second application divorced from shared approved C9, PostgreSQL and project identity.

## 3. Code organization / ownership (target paths; create only when implementing)

Existing core:
- `intelligence/search_planner/input_loader.py`, `context_builder.py`, `contracts.py`.
- `intelligence/search_planner/generation_engine/` and `ai_query/`.
- `intelligence/search_planner/query_validation/`.
- `intelligence/migrations/015_search_query_generation.sql` and `016_query_validation.sql`: **immutable historical migrations; do not edit**.

SP-owned new modules:
- `intelligence/search_planner/service.py`: app-facing workflow facade, full run state and owner actions.
- `intelligence/search_planner/http_api.py`: local project-scoped API; bounded input, structured errors and idempotent mutating operations.
- `intelligence/search_planner/operations.py` (or dedicated package): supervised durable workers, queue/outbox consumers, pause/recovery/status, explicit limits.
- `intelligence/search_planner/tool_capabilities.py`: source catalog **interface** + matching and rejection semantics, not an independent copy of Collector registry data.
- `intelligence/search_planner/task_planning.py`: provider-neutral preview/coverage/dedupe/limits and immutable plan identity; no provider HTTP.
- `intelligence/search_planner/handoff/`: trusted QV source verification, canonical projection, chunking, lineage, durable dispatch state, optional Collector adapter.
- `intelligence/search_planner/feedback/`: versioned outcome ingestion contract, no invented video matches or autonomous regeneration.
- `intelligence/migrations/<new_version>_search_planner_*.sql`: additive SP-owned tables only, after inspecting actual migration order and dependencies.
- `site/search-planner.*` (actual paths to be chosen after inspecting current UI conventions): SP view/components wired into existing shell.
- `tests/test_search_planner_*.py` plus disposable PostgreSQL, mocked provider, UI/HTTP and Windows/Linux QA.
- `docs/SEARCH_PLANNER_*.md`, JSON schemas, deployment/runbook and release gate.

The existing `app.py` must receive only a **small, additive route/UI mount** for SP; other business logic stays in `intelligence/search_planner/`. No unconditional import of `intelligence.collector` or collector-only SQL objects.

## 4. Independently versioned boundaries

### Upstream C / B / A
- Consume a database-verified immutable C9 approved publication, scoped by project and both human approvals.
- Keep legacy taxonomy/context v1 working.
- Add C v2 only after actual upstream C schema is pinned: `source_observation_ids`, `search_signals`, `validation_focus` and A/B snapshot provenance. No guessing fields and no mutation of historical publications.
- The B9/A provenance references and any present C Corrector adapter must be checked against installed schema; new upstream versions may be marked incompatible rather than accepted unsafely.

### SP → Collector
- Authoritative source: PostgreSQL `qv_runs`, `qv_decisions`, `sqg_candidates`, C9 publication, not frontend `validation_state=accepted` JSON.
- Current SP output `validated_queries/v1` differs from current Collector draft `validated_query_set/v1`; adapter must map `candidate_id` to **the same** `query_id`, `query_mode` to `mode` and resolve taxonomy pins from trusted DB rows.
- No silent truncation: 117 approved queries must produce 50+50+17 chunks if 50 is retained; each original query and every hypothesis/source link must remain traceable.
- `qv_outbox` ACK is allowed only after verifiable durable downstream acceptance (or a deliberately specified SP-owned durable inbox with separately tracked Collector delivery), never after merely rendering a preview.
- Distinguish **SP-approved generation spend** from **Collector external search execution authorization**.
- Collector integration has explicit `unavailable / incompatible / connected` statuses, versioned ports, deterministic replay/idempotency, no uncontrolled imports, no implicit paid provider fallback.

### Capabilities and task plans
- One authoritative provider/source registry in the assembled product. SP defines a consumer port, not a competing provider catalog.
- A provider-neutral plan can be previewed from an explicit pinned catalog snapshot. Without real readiness or supported operation, no executable task is released. Unknown costs/readiness remain blocked, not inferred.
- Preserve shared query→multiple hypothesis links when physically deduplicating tasks.

## 5. Runtime and release behavior

The installed SP module must support:
1. Choose approved publication and explicit Search Policy.
2. Create reproducible generation run and pinned model/strategy configuration.
3. Display real quote/reservation, obtain operation-specific explicit consent.
4. Execute queued OpenRouter batches via separate worker with financial ledger, no blind paid retry after uncertain POST.
5. Surface candidates, coverage, rejected reasons, relevance and MMR proxy scores honestly.
6. Run production local embeddings with verified pinned revision/digest, no test fixture leakage.
7. Preview source capabilities, blocked modes, task cost/quota upper bounds and lineage.
8. Produce a durable, auditable, replay-safe, non-executable handoff; connect to Collector only through the versioned adapter.
9. Show run/job/outbox/adapter status, errors, retry-safe recovery and pending owner approval.
10. Preserve all data across process restarts, with redacted logs and no secrets in DB responses.

SP12 measured feedback remains a **separate dependency-gated extension** until real Collector→Discovery→SRE labels/results exist. Do not claim full measured feedback merely because SP code is merge-ready.

## 6. Staged implementation — not just SP-FIX patches

- **SP-00, foundation and contracts:** baseline audit on actual branch, module boundaries, compatibility matrix, CI/test baseline and frozen port contract.
- **SP-01, harden SP1–SP2:** approved C9 v1 and versioned v2 when actual C schema available; exact context/provenance.
- **SP-02, make SP3–SP5 operable:** generation config/quote/consent API, background worker and usage/status/recovery.
- **SP-03, make SP6–SP8 production-capable:** model packaging/pinning, semantic validation job, QV outbox/QA.
- **SP-04, complete SP9–SP10:** external source-capability port and deterministic task planner with costs, coverage, validation and preview.
- **SP-05, implement SP11 as isolated integration boundary:** QV projection, chunking, DB lineage, durable state, optional Collector adapter; reconciliation and concurrent replay tests.
- **SP-06, user-facing SP application:** routes, UI, operations controls, error visibility, Windows compatibility.
- **SP-07, full standalone QA:** unit, schemas, migration fresh/upgrade, PostgreSQL, mocked OpenRouter, no-Collector operation, mocked Collector, Linux/Windows CI.
- **SP-08, release branch gate:** re-check latest `main`, resolve only SP-owned conflicts, green test matrix, one self-contained PR into `main`. No SP changes to the Collector feature branch.
- **SP-09, deferred measured feedback:** only once genuine downstream results and versioned SRE labels exist.

These are release workstreams; the original document's SP-FIX-00…10 remains the detailed engineering checklist inside them. They do not authorize pretending incomplete pieces are finished.

## 7. Non-negotiable acceptance tests

- Merge SP into current `main` with no dependency on unmerged Collector code; importing SP and starting the application must work with the Collector package absent.
- SP's complete user workflow through semantic QV works with mocked OpenRouter and disposable PostgreSQL.
- Connector absent: durable waiting/blocked status, zero fake receipt, zero external search calls.
- Connector present (mock adapter): replay delivers identical payload exactly once logically, preserves original UUIDs, all hypotheses and query links; zero-query terminal no-work.
- 117 queries mapped as 50+50+17 where applicable; no loss.
- Schema mismatches, cross-project refs, forged client queries and test embeddings fail closed.
- Paid dispatch is separated from reads; independent generation consent does not release Collector execution.
- Existing migration files untouched; clean installation and upgrade both pass; backward-compatible historical C/QV runs remain readable.
- Full Linux/Windows CI on branch HEAD plus a documented release matrix. Any live paid test requires separate authorization.
- Actual merge into `main` is a distinct owner-approved step; independently mergeable does not mean silent auto-merge or guaranteed zero conflicts after `main` changes.

## 8. Immediate first action

Complete SP-00 by inspecting the **current** main-derived branch and the current Collector PR as a read-only dependency reference, then freeze the actual API/SQL map and record test baseline before feature implementation. No existing runtime behavior or legacy DB migration changes in this document commit.
