# Search Planner — cross-branch integration preparation (2026-10-27)

**Scope:** READ peer branches; WRITE ONLY `feature/search-planner-full-integration`. No merge, cherry-pick, rebase, deployment or provider POST was authorized. Keep PR #34 draft. This inventory is a source snapshot, not a claim of live data compatibility.

## Pinned GitHub baseline

| Role | Branch | Verified HEAD (2026-10-27) |
| --- | --- | --- |
| integration base | `main` | `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` |
| Search Planner | `feature/search-planner-full-integration` | started at `598eb84e23b6be505bdc7d732ed7390ac914bc52`; new code in this PR |
| RTB Deep V2 | `feature/rtb-module-v2` | originally `178ee3d909d74cf8f1518b5a9ce616de773cca45`; advanced during audit to `04850c8dc2c6e1053336a6e2852bd18571828a01` (+14 commits, local release handoff only) |
| C Corrector initial freeze | `feature/rtb-c-corrector-v2` | `deff6c9883229b37c4ebba52a1bce8061ecdbec6` |
| Collector | `feature/video-collector-dev-20261023` | `a4f658de77fb82de2437474e60c2b917ae2a9811` |
| SRE | `feature/sre-production-v1-20261023` | `cf95d8c4ee9a8e6f3a47c631a9e37cd0cbf6b279` |
| QDP | `feature/qualified-data-pipeline-v1-20261023` | `77b48e35779c19c7917146e96e091a5b62a221f0` |
| Group A | `feature/group-a-full-module` | `a72520a398f9644ae63e061422493c120980d8e6` |
| Group B | `feature/group-b-full-module` | `ba724c498d66b622ea2f31a43b38171a36d9f275` |
| Group C | `feature/group-c-full-module` | `3917f54c01db88ef45ac9996aaee18c861a35c0b` |
| Unified Analytics | `feature/unified-analytics-full-module` | `d64ab6ab7743579a71155f96299b7fb928e03ba2` |
| Patterns & Insights | `feature/patterns-insights-full-module` | `25a1daa88eea7499e59312ccb4c0d0ab327b32ed` |

## The actual dependency graph (no invented direct peer access)

`RTB C9 reviewed publication → SP approved contexts → SQG consent/generation → semantic QV → HELD plan → SP durable inbox → independently authenticated Collector receipt → Collector held tasks → canonical Discovery D1 → SRE live relevance → QDP qualification → Group A/B/C → Unified Analytics → Patterns & Insights.`

The right-hand modules **do not** receive an SP inbox receipt as evidence that video search, transcription, analysis or qualification was completed. SP must not import Group A/B/C/QDP/SRE/UAE/PIE modules to claim end-to-end readiness.

## Verified concrete incompatibilities / missing release evidence

### P0: RTB Deep V2 (14 fields) ≠ SP V1 publication reader

RTB branch `deep_v2_publication.py` inserts into `tb_deep_v2_publications` and stores `v2_tree`, `v1_core`, `fact_mapping`, the second human review and readiness report digest. This **is a real standalone publication code path in that branch**, not part of main. SP's `input_loader.py` currently selects `tb_deep_publications` joined to `tb_taxonomy_publications` and V1 reviews, with **11-field** `deep-rtb/v1`. Treating RTB `v1_core` as an already published V1 row would silently invent identity, reviews and original fact relationships. The numeric taxonomy revision is not the schema version.

**Confirmed RTB export, no immediate edit required there:** the RTB branch already contains `intelligence/taxonomy_builder/publication_export_v2.py` with `RTBDeepV2PublicationExportRepository.export()`, explicit `rtb-publication-export/v1|v2` negotiation, exact reviewer-scoped SQL replay, A8/B9 source and human reviews, as well as `validate_publication_export()`. The RTB FIX-10/11 later commits provide a local source handoff utility; they do not claim PG/browser release success. SP now contains an **optional read-only** `integration/rtb_v2.py` bridge which dynamically uses this real RTB verifier only after the module is installed and the server assembles a reviewer-bound export source. `GET /publications-v2/{publication_id}/preview` is safe/default-deny (503 without the authenticated source), 14 fields remain intact, no generation/legacy FK promotion. **SP-owned remaining work:** fully versioned Deep V2 Context/Generation/QV path and integration PostgreSQL tests; retain V1. RTB-owner modifications should be requested **only if** the real verified export is missing specific upstream provenance required by that new SP reader — not preemptively.

### P0: Collector has a V1 parser/held plan, but no attested production SP publisher/receipt

Collector `query_intake.py::parse_validated_query_set` recognizes `validated_query_set/v1` (1–50 rows). Its `repository.py::_trusted_query_set` requires a row already in `collector_query_sets` with `validation_origin='trusted_query_validation/v1'`, a completed same-project SQG generation and approved RTB source. Collector `016_collector_planning.sql` comments expressly say a future trusted QV publisher must populate that table. No production SP-owned durable `accept(project_id,handoff,idempotency_key)` and independent `get_receipt(project_id,idempotency_key)` handshake was found in the inspected Collector branch. A preview or `commit_run` is NOT a compatible durable SP acceptance receipt.

**Important hash/order mismatch:** SP SP11 `collector_query_set_sha256` hashes the original *scientific rank order*. Collector parses queries and sorts by `query_id` before calculating `ValidatedQuerySet.fingerprint`. Same rows in different orders can produce different SHA256 digests. SP now offers `assess_collector_v1_projection`, reporting both hashes without modifying historical SP inbox, and no dispatch/receipt claim. A real trusted publisher must preserve both the original SP chunk SHA and Collector's canonical query set digest, with independent receipt readback.

**Collector-owner changes required for production integration:** (1) a trusted, project-scoped server-to-server QV/SP intake endpoint or SQL consumer, (2) unique idempotency and source event/chunk hash binding, (3) own durable query set and original candidate/hypothesis/fact lineage publication, (4) authentic receipt and readback carrying the exact SP12 receipt contract, (5) never auto-authorize external searches or paid quota on intake, (6) current source rights/locale/price/credentials/quotas with provider health and project-specific owner consent, (7) canonical video/D1+SRE outbox evidence if claiming downstream completion. SP cannot fake these. Needs independent Collector code and tests before production, **not modified in this work**.

### P0: Collector/QDP stale branch bases; shared HTTP/UI

Compared with `main`, Collector branch is **25 commits ahead, 145 behind**; QDP is **154 ahead, 143 behind** at audit. Both are divergent, not automatically safe to merge. RTB/SRE also edit `app.py` and `site/index.html`; RTB edits `site/projects.js`. Search Planner edits those files too. Their eventual three-way UI/router reconciliation must preserve every module route, owner ACL, static whitelist and navigation; do not replace the whole file from either branch.

**Integration-owner action needed:** reconcile divergent branch histories against current main and review any conflicts as a separate authorized task. No rewriting other branches here.

### P1: SQL migration numbers overlap but basenames differ

`intelligence.migrate.apply_migrations()` sorts complete migration **filenames**, records `schema_migrations.version` as the **entire basename** and verifies SHA256. Hence `054_sp_held_task_plans.sql`, `054_group_a_module_core.sql`, `054_tb_deep_v2_research_binding.sql` and `054_sre_live_core.sql` do **not** collide as filenames, but installation order is lexical across all `054_*` migrations. The same holds for 055–059 across SP, Group A/B/C, RTB, SRE. No historical SQL may be renamed/reused in a deployed database. Must test one aggregated migration tree and upgrade from an actual installed history, with all new FKs pointing to previously created real tables. No aggregate-all-branches migration PASS has been demonstrated. Older Collector/QDP files named `016_collector_planning.sql` / `016_qualified_data_pipeline.sql` are similarly **distinct from** `016_query_validation.sql` in main but their historic base may lack later dependencies.

### P1: SRE/QDP/D1 and Group A/B/C + UAE + PIE are downstream

SRE requires real canonical D1 video and selected-query attribution, not an SP preview. QDP must admit only real current positive/live relevance, not `offline_fixture`. Group A/B/C need current qualified media/rights and independent durable event receipts. UAE requires persisted approved C and exact A/B/SRE/QDP joins, PIE requires authorized row-level UAE evidence. Their separate integration contracts describe this, but **no direct SP→downstream bypass** may be added. No upstream code was edited.

## SP-side preparation actually committed

- `intelligence/search_planner/integration/readiness.py`: project-scoped PostgreSQL **read-only** schema inspection, explicit missing dependencies, fail-closed, no secrets/provider calls. Detecting tables never sets readiness/consent/receipt flags.
- `intelligence/search_planner/integration/collector_v1.py`: validate Collector V1 wire structure and compare original SP SHA with Collector V1 sorted-by-query canonical fingerprint, zero writes. No claims that SHA matching authenticates a publisher.
- `SearchPlannerService.integration_readiness()` and `collector_wire_compatibility()` work from the SP project scope / trusted QV projection.
- `integration/rtb_v2.py`: optional server-owned reviewer-export preview using the real RTB FIX-08 `validate_publication_export()` only when RTB is installed; default 503 when absent, no silent V2→V1, no unapproved generation.
- `GET /api/projects/{project_id}/search-planner/publications-v2/{publication_id}/preview` stays read-only and off by default until the server supplies an authenticated RTB exporter.
- `GET /api/projects/{project_id}/search-planner/integration-readiness` and `GET /api/projects/{project_id}/search-planner/handoff/{validation_run_id}/compatibility`; existing local host/Origin guard, no POST provider calls.
- `tests/test_search_planner_peer_integration.py` and Ubuntu/Windows focused CI include missing/partial/full table diagnostics, forged schema, project isolation, 117→50+50+17, normalizing order mismatch, no-work and invalid modes/languages.
- All business/dispatch gates from SP03–SP16 unchanged. Existing `EmptyCollectorAdapter` remains default; no automatic migrations/model download/provider calls on imports.

## Gate before standalone integration

1. **SP self-contained merge:** latest-main comparison, exact-head full Python/Node/Linux/Windows/PostgreSQL17 CI, actual local API/browser, full V1 approved C9→semantic QV→SP inbox, SIGKILL/client and PostgreSQL server/WAL recovery, local billing safety, no automatic paid provider requests.
2. **RTB V2 extension:** activate only on proven reviewed V2 schema + immutable upstream facts and strict V2 output. Missing V2 remains blocked; V1 remains safe.
3. **Collector handshake:** independently verified publisher + durable receipt readback + idempotent 50-query acceptance in Collector-owned code; real provider source readiness is separate, no misleading source policy booleans.
4. **All-module merge train:** explicit approved order on reconciled main, migration histories and SHA, router/static UI merge, overlapping owner ACL, real D1/SRE/QDP/A/B/C/UAE/PIE E2E after each peer is separately released.

**Owner decision before editing foreign branches:** Collector definitely needs its trusted intake + independently verified durable receipt and D1 lineage. RTB already supplies a reviewed V2 export and does not currently need a code edit for the preview bridge; SP must finish its own V2 consumer. If the eventual V2 Context producer discovers verifiably missing upstream fields, request permission for a narrowly scoped RTB change only then. This document is a precise issue handoff, **not permission to change peer modules or merge PR #34**.
