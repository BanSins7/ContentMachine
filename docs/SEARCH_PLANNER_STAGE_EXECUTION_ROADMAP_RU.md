# Content Machine — Search Planner · этапный план полного самостоятельного модуля
Дата: 26.10.2026. Ветка: `feature/search-planner-full-integration`.
Статус: исходный план + актуальная карта выполнения. **Начиная с 26.10.2026 отметка CODE IMPLEMENTED не равна CI PASS**; тестовый gate ведётся отдельно в MASTER_PROGRESS.md.

**ВАЖНО ДЛЯ СЛЕДУЮЩИХ ЧАТОВ:** актуальная версия всех 18 этапов с зачёркнутыми выполненными подзадачами находится в [SEARCH_PLANNER_MASTER_PROGRESS.md](SEARCH_PLANNER_MASTER_PROGRESS.md). Сначала читайте **её**, а затем этот документ для технических подробностей.

## Статус выполнения этапов (фиксируется только по факту)

| Этап | Статус | Факт выполнения |
| --- | --- | --- |
| SP-00 | **DOCUMENTATION / CONTRACT FREEZE COMPLETE** | 4 документа SP-00 закоммичены; baseline `main=218f5f3` Linux/PG17/1677 Python tests подтверждён по GitHub CI. Новый CI SP-ветки **не выполнялся**; функциональный release gate не пройден. |
| SP-01 | **IMPLEMENTED / QA PENDING** | Добавлены service.py, api_contracts.py, configuration.py, ports.py, новые экспорты __init__.py, контрактные тесты и docs/SEARCH_PLANNER_STAGE01_FOUNDATION.md. Нужен CI на текущем HEAD. |
| SP-02 | **V1 HARDENING IMPLEMENTED / QA + DEEP V2 PENDING** | Реализована строгая V1 форма, Review #2 tree equality, полная сверка контекстов, тесты/CI. Deep C9 V2 из внешнего PR #31 ещё отсутствует. Подэтапы и блокеры — в MASTER_PROGRESS.md. |
| SP-03 | **IMPLEMENTED V1 WORKFLOW / QA PENDING** | `generation_workflow.py` + методы Service, 16 unit tests, owner action guard, существующий SQG ledger; нет нового SQL/HTTP. Детали и блокеры в MASTER_PROGRESS.md и STAGE03_GENERATION_WORKFLOW.md. |
| SP-04 | **SAFE SINGLE-STEP CONTROLLER IMPLEMENTED / QA + SUPERVISION PENDING** | operations.py, Service API, 20 offline tests, focused CI + status/cancel/recovery; existing SQL reserve/dispatch/settlement reused. Внешнее invoice reconciliation, process supervision, реальные PG race/crash и CI точного HEAD ещё не завершены. |
| SP-05 | **AI QUERY + OUTPUT PROVENANCE CODE IMPLEMENTED / QA PENDING** | Без изменения старого SQG: pinned trace под SQL settlement, checksum contexts, verify_candidates_bundle, полный coverage/UUID, read-only Service, unit tests и CI; см. MASTER_PROGRESS.md и STAGE05_AI_QUERY_OUTPUT.md. |
| SP-06 | **PINNED OFFLINE SEMANTIC PROVIDER IMPLEMENTED / CI + REAL WEIGHTS QA PENDING** | sha256 manifest для всех файлов модели, строгий upstream revision pin, optional pinned dependency, no-network CLI/loader, QV exact provider check + double integrity before SQL ACK, offline unit tests/CI. Настоящие веса и multilingual quality smoke не проверены; см. MASTER_PROGRESS.md и STAGE06_SEMANTIC_EMBEDDINGS.md. |
| SP-07 | **CODE IMPLEMENTED / CI QUEUED** | NFKC; local MMR before global dedup; original candidate→multiple hypothesis/source facts links retained in immutable QV; read-only link projection, tests, CI and runbook. See MASTER_PROGRESS.md and STAGE07_SHARED_QUERY_LINKS.md. |
| SP-08 | **CODE IMPLEMENTED / CI QUEUED** | Existing QV SQL reused; exact run/decision/coverage accounting, generation-scoped read, trusted outbox event and replay, default-deny ACK until server-verified durable inbox; SP08 tests/CI. Actual SP Inbox is SP-12, concurrency PG validation SP-16. |
| SP-09 | **CODE IMPLEMENTED / CI QUEUED** | Strict project/version/TTL/SHA-pinned source capability snapshot, source readiness/policy+price+quota/locale/market checks, blocked legacy Collector declarations, no adapter => unavailable; offline QA + focused CI. Actual Collector health+quota publisher remains future SP15/16 integration. |
| SP-10 | **CODE IMPLEMENTED / EXACT-HEAD CI QUEUED / REAL PG17 PENDING** | Server-trusted QV+Deep C9+outbox evidence; round-robin fair held planning, source/cost/quota/candidate caps, multi-hyp hypothesis/fact provenance, deterministic plan hash; additive migration 054 immutable snapshot and owner-guarded idempotent approval; standalone tests + SP01..SP10 focused CI. Owner HTTP ACL = SP13, full migration+concurrency QA = SP16. |
| SP-11 | **CODE IMPLEMENTED / CI QUEUED / REAL PG17 PENDING** | DB-verified SP8 production QV/C9/SQG/outbox source → fully reversible Collector V1 chunks, original candidate_id=query_id, 117→50+50+17, 0→no_work, full SHA and per-candidate alias/hypothesis/fact/decision lineage; JSON Schema, facade, unit/CI. QV atomic inbox ACK and real Collector receipt belong to SP12. |
| SP-12 | **CODE IMPLEMENTED / CI QUEUED / REAL PG17 & LIVE COLLECTOR PENDING** | Additive migration 055: durable SP events/chunks/full source fact/UUID links, atomic QV row lock + QV ACK after all inserts, 10x replay/rollback/zero-work fake transaction suite; separately verified Collector intake attempts/receipts, no auto-retry after uncertain submit, EmptyCollector default. Real Collector publisher/auth adapter and PostgreSQL race checks remain downstream/QA gates. |
| SP-13 | **CODE IMPLEMENTED / CI QUEUED / PG17 & BROWSER QA PENDING** | SearchPlanner owner local HTTP lazy-mount GET/POST routes, project/Host/Origin/loopback/constant-time owner token checks, strict JSON, reviewed C9 publication picker and full SP01..SP12 service facade, default-deny paid HTTP with single-worker semaphore and additive migration 056 durable idempotency/uncertainty ledger; focused tests/CI. Real local HTTP/browser and PG17 upgrade/concurrent crash QA remain release gates, remote multiuser auth out of single-owner scope. |
| SP-14 | **CODE IMPLEMENTED / adapted V8 tests 15/15 + mocked DOM flow 2/2 / Node CI & PG17 browser QA PENDING** | Six-stage Search Planner integrated into per-project UI, typed policy and SHA-pinned verified OpenRouter config, budget+consent+one-shot paid idempotency, QV decisions grouped by hypothesis and MMR proxy disclaimer, capability+HELD, durable SP inbox/Collector-independent pending state. SQL-scoped model/overview/paid keys survive reload; token memory-only and erased on project/settings/discovery navigation. Added Node 22 full click DOM workflow and focused Python SQL tests. Real Node/browser/PG17/Windows/Linux CI pending, not PASS. [Implementation](SEARCH_PLANNER_STAGE14_OWNER_UI.md), [extended QA](SEARCH_PLANNER_STAGE14_USER_INTERFACE.md). |
| SP-15 | **PARTIAL: SP-15A/B CODE IMPLEMENTED / QA QUEUED** | Read-only project operations GET+SQL snapshot, bounded explicit zero-paid QV→SP inbox supervisor, 9 offline tests; additive migration 057 with fenced PostgreSQL lease/owner heartbeat/release, 9 lease tests, CI+operator runbooks. Continuous heartbeat/long-running process supervision, financial reconciliation, provider readiness, PG17/Win/Linux still OPEN. |
| SP-16–SP-17 | **NOT STARTED** | All-up release gates and real feedback await actual downstream data. |

SP-00: `SEARCH_PLANNER_INTEGRATION_MAP.md`, `SEARCH_PLANNER_INTERFACE_MATRIX.md`, `SEARCH_PLANNER_SP00_BASELINE_QA.md`, `SEARCH_PLANNER_MODULE_ADR.md`. Все записи только в этой SP-ветке; `main` и Collector не менялись.

## Цель
Создать целостный SP внутри ContentMachine: approved C9 → контексты → генерация OpenRouter с quote/consent/ledger → кандидаты → настоящая локальная semantic validation + MMR → immutable QV → capability port → независимый task planner → долговечный SP handoff inbox → optional Collector adapter. Реализовать полный SP API, UI, workers, Windows/Linux и PostgreSQL QA.

Ветка должна merge-иться в актуальный `main` **без предварительного merge ветки Video Collector**. SP не копирует `intelligence/collector/{registry,planner,worker}.py` и не выполняет поисковые provider tasks. Если Collector отсутствует, SP завершается долговечной записью `awaiting_collector`; ACK от Collector не фабрикуется. SP12 с измеренными результатами подключается, когда существуют подлинные Collector→Discovery→SRE данные.

Основание: `docs/SEARCH_PLANNER_STANDALONE_MODULE_RELEASE_PLAN.md`, пользовательское ТЗ `ContentMachine_Search_Planner_Remaining_Work_FULL_SPEC_RU(1).txt` (аудит старого main SHA `8d9bee1`). Ветка создана от main `218f5f3`, где уже имеется SP1–SP8; этот план не переписывает их.

## Этапы, строго по одному логическому блоку за запрос

| Этап | Работа | Куда вносятся изменения | Что должно быть готово |
|---|---|---|---|
| SP-00 | Аудит, baseline QA, freeze совместимости C/QV/Collector | `docs/SEARCH_PLANNER_INTEGRATION_MAP.md`, `docs/SEARCH_PLANNER_INTERFACE_MATRIX.md`, QA/ADR | Точные SHAs, contracts, migrations, тестовые baseline; runtime неизменён |
| SP-01 | Единый package facade, public DTO, ports, config | `intelligence/search_planner/{__init__,service,api_contracts,configuration,ports}.py` | Импорт/запуск без Collector, совместимые V1 API |
| SP-02 | SP1–SP2: approved C9 loader/context v1/v2, lineage | `{contracts,input_loader,context_builder}.py`, C adapter, schemas/tests | V1 без изменений; V2 только по реальному C9 schema; forged refs blocked |
| SP-03 | SP3: управляемые generations, batch, quote, budget, consent | `generation_engine/`, `service.py`, тесты | Idempotent run/authorization, immutable exact config, no paid call without consent |
| SP-04 | SP3 paid worker: queue, leases, one POST, recovery | `operations/`, `ai_query/live_worker.py`, additive migration | No double send/charge, unknown billing held, restart safe |
| SP-05 | SP4–SP5: pinned AI Query v1/v2, candidate output | `ai_query/`, `generation_engine/output_manager.py`, schemas | One LLM completion per batch, full coverage, immutable outbox |
| SP-06 | SP6: production multilingual embeddings provision/runtime | `query_validation/embedding.py`, `configuration.py`, runbook | Offline pinned real model, no fixture leakage / silent download |
| SP-07 | SP7: exact/semantic dedup/MMR/shared hypothesis lineage | `query_validation/{selection,contracts,service}.py`, tests | Deterministic scores/reasons, preserved scientific links |
| SP-08 | SP8: QV registry/outbox/consumer hardening | `query_validation/service.py`, `operations/`, tests | Complete QV decisions and coverage, concurrent idempotence |
| SP-09 | SP9: typed source capability **port** and matcher | `capabilities/{contracts,matcher,ports}.py`, tests | Empty/unsupported/not-ready explainable, no second Registry |
| SP-10 | SP10: server-trusted provider-neutral task planning | `task_planning/{contracts,planner,costs,coverage,repository}.py`, new SQL | Immutable plan hash, costs/quotas, no lost query↔hypothesis links |
| SP-11 | SP11-A: verifiable QV→execution projection & chunking | `handoff/{contracts,verification,projection}.py`, schema/tests | Candidate UUID unchanged; 117 queries → 50+50+17 |
| SP-12 | SP11-B: durable SP inbox + optional Collector adapter | `handoff/{repository,dispatcher,collector_adapter}.py`, new SQL | Atomic QV→SP inbox ACK, independent real Collector receipt, replay-safe |
| SP-13 | Owner HTTP API + auth/idempotency/status | `http_api.py`, `service.py`, minimal `app.py` mount | Usable project-scoped API, GET performs zero paid calls |
| SP-14 | Entire user-facing Search Planner UI | `site/search-planner.js`, CSS, navigation + JS tests | Publication → Consent → QV → Plan → Handoff from local UI |
| SP-15 | Worker supervision, monitoring, security & operations | `operations/`, config, runbook, tests | Safe restart, reconcile, redacted secrets, Windows/Linux support |
| SP-16 | All-up PG/CI/release gate and standalone PR | SP/DB/JS tests, CI, `docs/SEARCH_PLANNER_RELEASE_GATE.md` | Green fresh+upgrade SQL, full offline E2E, mergeable without Collector |
| SP-17 | SP12 feedback contract; measured feedback after real SRE | `feedback/{contracts,reader}.py`, versioned SQL when justified | No fake metrics; true measured data only after downstream exists |

Этапы 00–16 = самостоятельный **merge-ready SP**. Этап 17 зависит от реальных downstream video results и не является блокером базового выпуска; его контракт/моковая QA могут готовиться заранее. Версия C9 V2 может включаться только после его реального schema freeze, но совместимый V1 обязан работать.

## Правила по файлам и БД
- Не менять `main` и `feature/video-collector-dev-20261023`; все commits в SP branch.
- Не редактировать уже применённые SQL `015_search_query_generation.sql` и `016_query_validation.sql`; новые migrations только additive, номер после проверки всех текущих SQL.
- Не создавать второй provider registry: SP получает immutable capability snapshot через interface; позднее его поставляет Collector.
- Не принимать «validated» JSON из браузера в качестве source of truth: нужен реальный QV run, selected decisions, candidate/project/C9 SHA lineage в PostgreSQL.
- Когда SP consumes `qv_outbox`, atomic durable insert в собственный SP inbox предшествует ACK upstream event; реальное Collector acceptance отображается отдельным receipt, не подменяется SP inbox ACK.
- Ни LLM consent, ни plan commit сами по себе не авторизуют платное исполнение поисковой задачи Collector.
- Никаких auto paid retries после ambiguous POST, никаких автоматических downloads моделей, никаких fake видео.

## Финальный release gate
1. SP монтируется в ContentMachine и работает с PostgreSQL и UI без импортов `intelligence.collector`.
2. Approved C9→context→OpenRouter mocked batches→candidates→semantic QV→plan→SP durable inbox проходит полноформатный E2E.
3. 117 queries сохраняются как 50+50+17, 0 selected → durable `no_work`; исходные UUID/provenance не теряются.
4. Mock Collector connector получает один логически идемпотентный проверенный handoff; отсутствие Collector = `awaiting_collector` без вымышленного ACK.
5. Раздельные платные consent, costs, unknown billing, cross-project, forged data и race/crash tests проходят.
6. PostgreSQL17 fresh install+upgrade, Python/JS/UI, Linux/Windows CI green на финальном HEAD.
7. Обновлены documentation/runbook, проверен актуальный `main`, подготовлен один self-contained PR.

## Формат отчётности
Для каждого SP-N сообщать: ветка, SHA before/after, полный список изменённых файлов, изменения schema, actual tests с PASS/FAIL/SKIP и ссылкой CI, какие инварианты закрыты, какие внешние зависимости остаются, что дальше. Не объявлять выполненным то, что не проверялось; платных live тестов не делать без отдельного разрешения пользователя.
