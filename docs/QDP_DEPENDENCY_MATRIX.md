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

## FIX-07 external SRE S3 dependency

Requires peer SRE branch's forward-only migration `016_sre_d1_intake_rights.sql` (adds `sre_content_versions.metadata_observation_id` and `sre_project_discoveries`); QDP does not duplicate it into production schema. Exact source+observation+discovery checks are QDP-owned and complete in `provenance.py`. Without peer integration metadata enrichment returns an explicit blocked reason. Cross-branch LIVE smoke **not validated**. QDP's own isolated PG17 tests simulate the peer schema only in a disposable database.


## FIX-08 current D1 → QDP → Group C boundary

QDP's `metric_history.py` consumes only existing `metric_snapshots` and the exact metadata source/policy from FIX-07. Owns bounded history, true timestamp and version/definition comparability, Group C preparation pointer. The real Group C worker remains independent and must re-read current D1 facts/policies and compute its own results. No schema duplicate and no external paid calls. QDP-owned PG17 QA [40310914445](https://github.com/BanSins7/ContentMachine/actions/runs/40310914445) PASS 135/135; real Group C E2E BLOCKED_EXTERNAL.


## FIX-09 real media owner contract

Current Group A branch `feature/group-a-full-module` accepts `media_kind=video` and `video/slideshow_video`, independently checking actual local video file before technical extraction. Current Group B branch `feature/group-b-full-module` defines video and carousel content types separately. QDP only references existing D1 media after rights, authorized file roots and SHA proof. Media Acquisition and ordered carousel slide manifest are **not QDP-owned**; a missing manifest blocks carousel readiness. QDP PG17 154/154 [PASS](https://github.com/BanSins7/ContentMachine/actions/runs/40312315651); cross-branch Group A/B LIVE E2E BLOCKED_EXTERNAL.


## FIX-10 group preparation read surface

`qdp_current_base_enrichments` is a D1 legal metadata view, separate from `qdp_group_current_preparations`: the latter enforces current A/B media vs C metric/config rights independently. Python internal port `read_current_preparation()` rechecks exact SRE/D1/observed binary/hash or metric history before returning immutable inputs. Peers Group A/B/C must separately revalidate before execution, so this QDP side does not claim those branches have been merged. Historical blocked state is not permission; current-read views can revoke it without update. 042/043 are QDP-only forward migrations.


## FIX-12 consumer adapter integration boundary

QDP provides `outbox_delivery.dispatch_once` as the operator-injected durable delivery service but NEVER imports or schedules peer Group analysis jobs. `receiver.receive(QDPHandoffPointer,payload_sha256)` must commit an actual Group consumer inbox+job **within that Group's transaction** and return its durable receipt, identical across duplicate event deliveries. A configured peer port may return `committed` but not `completed analysis`. Group A adapter implementation belongs to FIX-13; Group B/C adapters FIX-14. Their absent code results in `consumer_unavailable`, not fabricated acceptance. The 197/197 PG17 suite uses a separate disposable consumer table to prove the required transaction/idempotency semantics.


## FIX-13 actual Group A adapter — verified capability boundary

QDP owns `intelligence.qualified_data.integrations.group_a` wire translation/current reader and uses existing Group A `feature/group-a-full-module` **GA-MOD-04** `GroupAInboxReceiver`, `VerifiedMediaCatalog`, `GASettings` only under explicit enable after installation. GA owns `ga_runs/ga_steps/ga_inbox` and migrations 054/055. Both contracts ran together in a disposable PG17 environment pinned to GA `cc1d43497aa70b6f1831b104d835c8674861e9ab` [CI 40321549312](https://github.com/BanSins7/ContentMachine/actions/runs/40321549312) PASS. Peer bootstrapped API/old A1 execution remain disabled; QDP never invokes Group A FFmpeg or paid analysis. No writes to GA/other branches.


## FIX-14 current peer capability matrix

Group C GC-MOD-04 opt-in QDP consumer wired and proven against QDP producer/dispatcher in independent disposable PG17, pinned to `37d78b76` ([CI 40324710488](https://github.com/BanSins7/ContentMachine/actions/runs/40324710488)). Group B GB-MOD-04 pinned `63ee084f` is incompatible with QDP current event/v2 input semantics; QDP owns fail-closed capability gate, B owner must change its own reader. UAE UA-MOD-05 `3c616f9` identity-only: QDP never supplies invented A/B/C completed results or approves analytics. Group A earlier pinned actual receiver CI still passes. Independent package installation, migrations, origin rights and real live external execution remain separate integration/release gates.


## FIX-16 local read/UI portability

QDP Studio is QDP-owned `site/qualified-data.js` and `intelligence.qualified_data.http_api/studio_read`. It requires live SRE read schema for the picker but does not require Group A/B/C/UAE packages to START: absent peers produce unavailable diagnostics or pending deliveries. Existing `app.py` loopback Host/Origin and `X-ContentMachine` mutation guard are retained. No server-side paid calls or media downloads on any GET. Tests exercise the QDP routes and DOM source as well as read models on disposable PG17; direct browser rendering remains to be performed in an actual local deployment.
