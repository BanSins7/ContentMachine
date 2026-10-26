# QDP dependency and ownership matrix — FIX-00

Snapshot: 2026-10-26. Exact HEADs: `QDP_IMPLEMENTATION_TRACKER.md`. All peers are independently owned; QDP never writes other branches. An integration port being described is NOT proof that producer/consumer is complete.

| Peer | Source of truth / observed boundary | QDP contract owned here | Live status and next integration gate |
|---|---|---|---|
| C9 taxonomy / RTB | Approved project taxonomy, category_key UUID, publication/hash; approved SRE taxonomy mirror | Verify immutable taxonomy+project pin on SRE run, no guessed hypothesis keys | Owner must supply approved publication/category mapping |
| Search Planner / Query Validation | `validated_queries/v1`, query candidate UUIDs | **No direct QDP input**; provenance traced via D1→SRE | Do not infer match from search query |
| Video Collector PR #20 | Planned query sets, provider/inspection, D1 writes owned by Collector | D1 adapter reads existing committed canonical rows, NEVER calls provider | Actual ingestion and source/provenance fields must be verified after Collector completion |
| D1 | `videos`, `discoveries`, `source_video_metadata`, `metric_snapshots`, `media_assets` | `D1VideoReader`: exact video/source/policy/metadata and media/metric refs | Schema columns must be checked when implementing FIX-07..09 |
| SRE PR #22 | `sre_classification_runs` and results, project scope, taxonomy, human labels | `SreRunReader`: completed LIVE only, manifest coverage, effective corrections; SRE event inbox | Current QDP branch lacks `execution_mode`, SRE's `017_sre_offline_run_engine.sql` adds it; SRE outbox/live run still peer dependency |
| QDP PR #24 | Runs/items/enrichments/preparations + `qdp.prepare_video` | **QDP alone** owns qualification, snapshot, Worker, inbox/outbox, current validity and QDP UI | Existing preview/POST/list/detail/cancel APIs remain stable |
| Group A PR #26 | `group_a.repository.create_task`; `group_a.analyze_video`, local FFprobe/FFmpeg | `PreparationConsumer`; exactly-once receipt + current A readiness | A1 exists on branch based on QDP; full A multimodal pipeline is not implied |
| Group B | Actual creative semantic analysis, A prerequisites, provider/budget belongs to B | Typed consumer pointer, waiting/blocked state, durable delivery only | No fake B results; implementation depends on peer contract |
| Group C | Metric config, cohorts, actual growth/breakout scoring belongs to C | Typed metric/version pointers and independent C readiness | QDP never computes breakout_score or invents priors |
| Unified Analytics PR #23 | Completed verified A/B/C outputs and downstream analytics snapshots | `AnalyticsResultReader` verified completion bridge | QDP-ready is NOT analytics-qualified; schema reconciliation required |
| Project Source Intelligence A / B | Project evidence and project research context | No direct QDP video/metric ownership | PR #29 and #30 merged into main; different domain |

## Capability state model

`available` = independently probed exact implemented schema/version, `unavailable` = missing required peer/schema, `incompatible` = present but wrong shape/version. Default to unavailable. Runtime gate rejects unavailable or incompatible; no implicit fallback, no import of missing external modules.

Interface code in `intelligence/qualified_data/integrations/contracts.py` is QDP-owned; method names are PROPOSED QDP ports, not promises that the peer exposes identically named methods. Future adapters may translate real DB schema into them only after verifying it. Stubs can be used solely in isolated tests, never to make production fake-ready.

## Shared-file ownership

- This QDP branch owns `intelligence/qualified_data/**`, QDP docs/tests, QDP-specific new SQL and `.github/workflows/qdp-tests.yml`.
- Shared `app.py`, `site/index.html`, `intelligence/migrate.py`, legacy SQL files require documented integration owner and minimal changes only when necessary.
- Peer branches SRE/Collector/Group A/B/C/Analytics are READ-ONLY from QDP work.
- Never silently merge or cherry-pick peer commits as a prerequisite for implementing QDP-owned logic.


## FIX-05 handoff status (2026-10-26)

SRE run-completed notification consumer: QDP code/SQL contract implemented in `sre_inbox.py` + `041_qdp_sre_inbox_quarantine.sql`, pointer: [QDP_SRE_INBOX_HANDOFF.md](QDP_SRE_INBOX_HANDOFF.md). SRE owner must provide authenticated S10/M36 producer, transactional outbox and delivery ACK, verified against the exact run-completed event schema (not per-video `sre.classifications_ready/v1`). The QDP consumer only returns `ack=true` after durable commit and never starts paid work. Real producer compatibility is **BLOCKED_EXTERNAL** until the peer is ready; exact-SHA CI verification is still pending.
