# Video Collector V1 — independent module integration

**Working branch:** `feature/video-collector-v1`.
**Status:** VC-FIX-00 baseline integrated; **NOT** an end-to-end complete production Collector yet.
**Main base:** `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` (including PSI PR #29, Knowledge Extraction PR #30).
**Collector source:** `a4f658de77fb82de2437474e60c2b917ae2a9811` (PR #20 Stage 2–9).

This branch will become the **one complete module** to integrate with main. Each VC-FIX stage modifies this same branch. Its code contains the entire original Collector, DTK connector, adapter, worker, inspector, migrations, HTTP handlers, frontend tool, tests, and fixtures. No second integration of PR #13 or #20 is required. Main's Search Planner, Query Validation, source intelligence and knowledge extraction stay intact.

## Responsibilities and exact intended flow

Approved RTB C9 -> Search Planner v1 -> PostgreSQL QV `validated_queries/v1` -> **trusted QV converter and durable publisher (not yet implemented)** -> sealed Collector shards -> offline preview -> held commit -> explicit release -> dedicated DTK Worker -> Response Inspector -> **atomic Discovery ingest (not yet implemented)** -> canonical `videos` and `discoveries` -> **durable SRE handoff (not yet implemented)** -> QDP after SRE relevance approval.

- `intelligence/search_planner/*`: production query generation/validation; no second query planner.
- `intelligence/collector/*`: one source capability registry, deterministic technical planner, collector jobs/provider inspector/ingest and operations.
- `intelligence/connectors/dtk.py`: the ONE DTK transport shared with manual diagnostics. Do not copy PR #13 twice.
- `intelligence/discovery/*`: only canonical video and discovery records; no parallel videos table.
- `intelligence/relevance/*`: independently evaluates relevance; Collector never auto-marks a video as match.
- `app.py`, `site/*`: integrated with main's existing owner/same-origin API and UI.

## Migration handling

Old branch: `016_collector_planning.sql`, `017_collector_worker.sql` (incompatible with existing main `016_query_validation.sql`). New, clean-DB paths: `054_collector_planning.sql`, `055_collector_worker.sql`. Applied source migration checksums must not be rewritten. A real database previously bootstrapped directly from the old Collector branch needs explicit migration reconciliation before upgrading. No existing historical main SQL files were modified. VC-FIX-01 will verify clean PG17 application and implement query manifests/checkpoints/outbox.

## Roadmap in this single branch

- VC-FIX-00: baseline merge with current main, carry full Stage 2–9, migration number deconflict, API/UI integration and cleanup.
- VC-FIX-01: additive schema, PostgreSQL migration QA and project isolation.
- VC-FIX-02: one provider registry / task planner.
- VC-FIX-03–04: trustworthy QV reader, conversion, sharding, atomic publisher and ACK.
- VC-FIX-05–06: production QuerySet API/UI, trusted preview, held commit and explicit release.
- VC-FIX-07–08: DTK Worker hardening and strict Inspector QA.
- VC-FIX-09–10: transactional D1 ingest and completion semantics.
- VC-FIX-11: project-scoped SRE handoff; videos default to not_evaluated until real SRE review.
- VC-FIX-12–13: results/trace UI and failure recovery/pagination bounds.
- VC-FIX-14: full mock-provider PostgreSQL E2E CI and separate operator opt-in live smoke.
- VC-FIX-15: release, runbooks, rollback and production gates.

## Checks

```sh
python -m compileall -q app.py intelligence/collector intelligence/connectors
python -m unittest discover -s tests -p 'test_video_collector_*.py' -v
python -m unittest discover -s tests -p 'test_dtk_connector.py' -v
node --check site/dtk-tool.js
CM_DB_DSN=postgresql://...DISPOSABLE... python -m intelligence.migrate
```

**Do not claim finished V1 until VC-FIX-15 and a separate live DTK smoke.** Mock DTK checks only prove the offline software pipeline. No provider POST is triggered by offline preview or GET.
