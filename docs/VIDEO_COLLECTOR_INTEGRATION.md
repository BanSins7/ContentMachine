# Video Collector V1 — independent module integration

**Working branch:** `feature/video-collector-v1`.
**Status:** VC-MOD-00 integration baseline; **NOT** an end-to-end complete production Collector yet.
**Main base:** `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` (including PSI PR #29, Knowledge Extraction PR #30).
**Collector source:** `a4f658de77fb82de2437474e60c2b917ae2a9811` (PR #20 Stage 2–9).

This branch will become the **one complete module** to integrate with main. Each VC-MOD stage modifies this same branch. See the [master progress checklist](VIDEO_COLLECTOR_MASTER_IMPLEMENTATION_PLAN.md); struck stages are implemented in committed code (QA outcomes are recorded separately; a pending/failed check is never PASS). Its code contains the entire original Collector, DTK connector, adapter, worker, inspector, migrations, HTTP handlers, frontend tool, tests, and fixtures. No second integration of PR #13 or #20 is required. Main's Search Planner, Query Validation, source intelligence and knowledge extraction stay intact.

## Responsibilities and exact intended flow

Approved RTB C9 -> Search Planner v1 -> PostgreSQL QV `validated_queries/v1` -> **trusted QV converter and durable publisher (code implemented; CI pending)** -> sealed Collector shards -> offline preview -> held commit -> explicit release -> dedicated DTK Worker -> Response Inspector -> **atomic Discovery ingest (implemented in VC-MOD-09; PG17 QA pending)** -> canonical `videos` and `discoveries` -> **durable SRE sender (implemented in VC-MOD-11; receiver SRE M35 still unavailable)** -> QDP after SRE relevance approval.

- `intelligence/search_planner/*`: production query generation/validation; no second query planner.
- `intelligence/collector/*`: one source capability registry, deterministic technical planner, collector jobs/provider inspector/ingest and operations.
- `intelligence/connectors/dtk.py`: the ONE DTK transport shared with manual diagnostics. Do not copy PR #13 twice.
- `intelligence/discovery/*`: only canonical video and discovery records; no parallel videos table.
- `intelligence/relevance/*`: independently evaluates relevance; Collector never auto-marks a video as match.
- `app.py`, `site/*`: integrated with main's existing owner/same-origin API and UI.

## Migration handling

Old branch: `016_collector_planning.sql`, `017_collector_worker.sql` (incompatible with existing main `016_query_validation.sql`). New, clean-DB paths: `054_collector_planning.sql`, `055_collector_worker.sql`. Applied source migration checksums must not be rewritten. A real database previously bootstrapped directly from the old Collector branch needs explicit migration reconciliation before upgrading. No existing historical main SQL files were modified. VC-MOD-02 owns new schema + PostgreSQL upgrade verification.

See [VC-MOD-00 branch audit and compatibility matrix](VIDEO_COLLECTOR_VC_MOD_00_BASELINE.md).

## Roadmap in this single branch

- VC-MOD-00: current main + original Stage 2–9 integration, migration deconfliction, baseline audit and QA.
- VC-MOD-01: versioned module ports and independent dependency contracts. See [Ports V1](VIDEO_COLLECTOR_PORTS_V1.md).
- VC-MOD-02: additive schema, PostgreSQL fresh/upgrade and project isolation. See [Collector Schema V1](VIDEO_COLLECTOR_SCHEMA_V1.md).
- VC-MOD-03/04: trusted QV reader ([source/contract](VIDEO_COLLECTOR_QV_READER_V1.md)), deterministic sharding, publisher and durable ACK. Reader and publisher code implemented; final PG17 concurrency/integration tests pending. See [QV Publisher V1](VIDEO_COLLECTOR_QV_PUBLISHER_V1.md).
- VC-MOD-05/06: registry + [manifest technical planner](VIDEO_COLLECTOR_MANIFEST_PLANNER_V1.md) are implemented; persisted QuerySet UI and held commit/release implemented in VC-MOD-06; see [trusted held release](VIDEO_COLLECTOR_HELD_RELEASE_V1.md). Latest HEAD QA pending.
- VC-MOD-07/08: DTK Worker resilience and [strict Page[Content] Inspector + safe DTO V1](VIDEO_COLLECTOR_INSPECTOR_V1.md) implemented in code; exact-head QA pending. D1 ingest code present; exact-head PostgreSQL integration QA pending.
- VC-MOD-09/10: [atomic canonical D1 ingest](VIDEO_COLLECTOR_D1_INGEST_V1.md) and [Worker completion after persisted receipt](VIDEO_COLLECTOR_D1_COMPLETION_V1.md) implemented in code. Exact-head PostgreSQL/Windows E2E QA still pending; no live provider claim.
- VC-MOD-11: scoped durable SRE-ready outbox, pending while SRE offline.
- VC-MOD-12: [owner-facing read-only D1 run/results/trace and paging](VIDEO_COLLECTOR_OPERATOR_RESULTS_V1.md) implemented in code, CI pending; no auto-posts or fake SRE relevance.
- VC-MOD-13: [project-scoped cancellation reconciliation, quota fencing and metadata retention](VIDEO_COLLECTOR_RECOVERY_RETENTION_V1.md) implemented in code, exact-head PG17/CI QA pending; dry-run default and explicit opt-in only.
- VC-MOD-14: [independent module CLI, zero-by-default feature flags and safe cross-OS deployment/rollback](VIDEO_COLLECTOR_MODULE_DEPLOYMENT_V1.md) implemented in code; exact-head CI pending.
- VC-MOD-15: comprehensive disposable PostgreSQL17 and cross-platform E2E + opt-in live smoke.
- VC-MOD-16: final one-PR merge readiness, release/rollback runbook.

## Checks

```sh
python -m compileall -q app.py intelligence/collector intelligence/connectors
python -m unittest discover -s tests -p 'test_video_collector_*.py' -v
python -m unittest discover -s tests -p 'test_dtk_connector.py' -v
node --check site/dtk-tool.js
CM_DB_DSN=postgresql://...DISPOSABLE... python -m intelligence.migrate
```

**Do not claim finished V1 until VC-MOD-16 and a separate operator-controlled live DTK smoke.** Mock DTK checks only prove the offline software pipeline. No provider POST is triggered by offline preview or GET.
