# Content Machine — Search Planner · этапный план полного самостоятельного модуля
Дата: 26.10.2026. Ветка: `feature/search-planner-full-integration`.
Статус: согласованный **план реализации**, не отчёт о выполнении этапов.

**ВАЖНО ДЛЯ СЛЕДУЮЩИХ ЧАТОВ:** актуальная версия всех 18 этапов с зачёркнутыми выполненными подзадачами находится в [SEARCH_PLANNER_MASTER_PROGRESS.md](SEARCH_PLANNER_MASTER_PROGRESS.md). Сначала читайте **её**, а затем этот документ для технических подробностей.

## Статус выполнения этапов (фиксируется только по факту)

| Этап | Статус | Факт выполнения |
| --- | --- | --- |
| SP-00 | **DOCUMENTATION / CONTRACT FREEZE COMPLETE** | 4 документа SP-00 закоммичены; baseline `main=218f5f3` Linux/PG17/1677 Python tests подтверждён по GitHub CI. Новый CI SP-ветки **не выполнялся**; функциональный release gate не пройден. |
| SP-01 | **IMPLEMENTED / QA PENDING** | Добавлены service.py, api_contracts.py, configuration.py, ports.py, новые экспорты __init__.py, контрактные тесты и docs/SEARCH_PLANNER_STAGE01_FOUNDATION.md. Нужен CI на текущем HEAD. |
| SP-02 | **V1 HARDENING IMPLEMENTED / QA + DEEP V2 PENDING** | Реализована строгая V1 форма, Review #2 tree equality, полная сверка контекстов, тесты/CI. Deep C9 V2 из внешнего PR #31 ещё отсутствует. Подэтапы и блокеры — в MASTER_PROGRESS.md. |
| SP-03 | **IMPLEMENTED V1 WORKFLOW / QA PENDING** | `generation_workflow.py` + методы Service, 16 unit tests, owner action guard, существующий SQG ledger; нет нового SQL/HTTP. Детали и блокеры в MASTER_PROGRESS.md и STAGE03_GENERATION_WORKFLOW.md. |
| SP-04–SP-17 | **NOT STARTED** | Следующие этапы пока не менялись. |

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
