# Content Machine — Search Planner: единый план и журнал выполнения
**Назначение:** это канонический файл для любого следующего чата. После каждого этапа **в той же рабочей ветке** обновить этот файл коммитом. Отмечать выполненное зачёркиванием `~~...~~` **только после фактической реализации и требуемого QA**, а не после написания документации.

**Репозиторий:** `BanSins7/ContentMachine`  
**Единственная SP-ветка:** `feature/search-planner-full-integration`  
**Черновой PR:** https://github.com/BanSins7/ContentMachine/pull/34 — не сливать до полного release gate и согласования владельца.  
**Не редактировать:** `main`, `feature/video-collector-dev-20261023`, `feature/rtb-c-corrector-v2`.  
**Правила:** один целостный SP внутри ContentMachine, без обязательного Collector-кода; никакой платной LLM без отдельного согласия, никаких правок применённых SQL-миграций. Внешние зависимости отмечать отдельно, не считать их реализованными.

## Этапный чеклист (порядок неизменен)

- [x] ~~**SP-00 — инвентаризация / freeze контрактов**: точные refs main/SP/C/Collector, актуальная SQL-карта, QA-baseline и ADR.~~ Выполнен (документы: `SEARCH_PLANNER_INTEGRATION_MAP.md`, `SEARCH_PLANNER_INTERFACE_MATRIX.md`, `SEARCH_PLANNER_SP00_BASELINE_QA.md`, `SEARCH_PLANNER_MODULE_ADR.md`; завершение на `195aff78`). Это **документальный freeze**, не runtime release.
- [ ] **SP-01 — полноценный модуль-фасад и dependency ports**: `service.py`, `api_contracts.py`, `configuration.py`, `ports.py`, `__init__.py`; контрактные тесты, старые V1 exports, Collector-free imports, зелёный CI на проверяемом HEAD. **Код реализован** (последний SP-01 коммит `3fc56b66`). Быстрый CI `Search Planner SP-01 focused QA` проходил на ранних SHA, но итоговый полный CI на последнем SP-01 HEAD не подтверждён; финальную галочку пока не ставить. Файлы: `docs/SEARCH_PLANNER_STAGE01_FOUNDATION.md`.
- [ ] **SP-02 — доверенный SP1/SP2 C9 reader + HypothesisContext V1/V2**: проверить source row, два Review, published final tree, version/identity, исключить silent 14→11 truncation, tests v1. Включать реальный V2 только после появления upstream C9 Deep V2 schema/publication (PR #31 первоначально только freeze). **V1 код реализован, QA ожидает CI, Deep V2 заблокирован внешним PR #31; этап НЕ завершён.** Детали: `docs/SEARCH_PLANNER_STAGE02_C9_PROVENANCE.md`.
- [ ] **SP-03 — Generation Run/Batch/Quote/Budget/Consent**: server idempotency + pinned config, отдельный проверяемый user consent. **Код написан, QA текущего HEAD ожидается.** Добавлены `generation_workflow.py`, методы `SearchPlannerService`, 16 offline тестов, CI и [отчёт SP-03](SEARCH_PLANNER_STAGE03_GENERATION_WORKFLOW.md). Default owner gate блокирует approve/revoke; настоящий HTTP owner verification — SP-13.
- [ ] **SP-04 — Paid worker и recovery**: supervised queue/lease/one POST, unknown billing hold, durable restart, полный ledger. **Часть кода реализована:** `operations.py`, публичные методы Service, тесты и CI; используется существующий платный worker с атомарным `dispatch_intent` и удержанием неопределённого резерва. **Открыто:** реальное процессное supervision/heartbeat, подтверждённая invoice reconciliation, PG race/crash integration и точный CI PASS. [Отчёт SP-04](SEARCH_PLANNER_STAGE04_WORKER_RECOVERY.md).
- [ ] **SP-05 — AI Query + Output Manager**: pinned schema/model, research query traces, один completion на batch, full coverage/sqg_outbox.
- [ ] **SP-06 — Production semantic embeddings**: локальные multilingual weights, версии/digest, no silent download, реальная semantic provider; fixture запрещён в production.
- [ ] **SP-07 — Relevance/Dedup/MMR**: детерминированный selection, reject reasons, множественные связи query→hypothesis.
- [ ] **SP-08 — Validated Registry и QV Outbox**: project-scoped DB, per-candidate decisions, idempotent consumer, fail-closed concurrency.
- [ ] **SP-09 — Tool Capabilities Port/Matching**: внешний versioned capability snapshot, без копии Registry и без реального provider dispatch.
- [ ] **SP-10 — Trusted Task Planner**: preview, quotas/costs/block reasons/coverage, dedup без потери связи с гипотезами, immutable plan hash.
- [ ] **SP-11 — SP8→SP11 projection / chunking**: DB-verified QV/C9 SHA, original candidate IDs, 117→50+50+17, 0→no_work.
- [ ] **SP-12 — Durable SP inbox / optional Collector adapter**: атомарный QV→SP ACK, отдельная downstream receipt, replay/race/crash QA, awaiting_collector без Collector.
- [ ] **SP-13 — Owner HTTP API**: project ACL, idempotency, safe GET, bounded bodies, no unauthorised paid calls.
- [ ] **SP-14 — User Interface**: полный Search Planner UI внутри ContentMachine, budgets/approvals/runs/validation/plan/handoff.
- [ ] **SP-15 — Operations**: worker supervision, monitoring, logs redaction, secure recovery, Windows/Linux.
- [ ] **SP-16 — Полный release gate**: fresh/upgrade PostgreSQL, all-up E2E + mock Collector, Windows/Linux CI, 0 implicit spend, self-contained mergeable PR; **без автоматического merge**.
- [ ] **SP-17 — feedback от реальных Collector→Discovery→SRE результатов**: measured feedback contract, настоящий анализ только после появления downstream фактов; внешняя зависимость, не подменять mock данными.

## Подэтапы текущей работы (зачёркивание фактически написанного кода не означает завершение полного SP-02)

**SP-01**
- [x] ~~Публичный фасад, immutable DTO, конфигурация, typed ports и backward-compatible exports внесены в SP-ветку.~~
- [x] ~~Добавлены 13 контрактных tests и отдельный GitHub workflow; на промежуточных коммитах focused QA прошёл.~~
- [ ] Подтвердить зелёный **Linux + Windows + focused CI** на согласованном HEAD. Только после этого зачеркнуть полный SP-01.

**SP-02**
- [x] ~~Добавлена строгая проверка 11 полей V1 до legacy normalization; все три будущие extension поля Deep V2 не могут быть молча отброшены.~~
- [x] ~~В PostgreSQL SELECT C9 потребованы оба Human Reviews и точное совпадение `approved_nodes` финального Review #2 с immutable опубликованным деревом.~~
- [x] ~~Проверена полнота HypothesisContext против заново построенного дерева/фактов/соседних исключений/policy; новый public facade применяет проверку.~~
- [x] ~~Добавлены offline тесты `test_search_planner_stage02_compatibility.py` и расширен focused CI workflow.~~
- [ ] Получить PASS тестов на точном HEAD (GitHub Actions по PR #34).
- [ ] Получить и интегрировать утверждённый **Deep C9 V2** из отдельного C Corrector PR: 14 полей, snapshot/evidence provenance, versioned approval/publication, корректный v2 context schema. **Внешняя зависимость; не имитировать.**
- [ ] Только тогда зачеркнуть весь SP-02 и начать SP-03; пока SP-01/02 остаются открытыми по QA/внешним условиям.

**SP-03 (разрабатывается независимо от ещё не выпущенного C9 V2):**
- [x] ~~Создан `GenerationWorkflow` над существующими SQG/PostgreSQL операциями регистрации конфигурации, идемпотентного run, quote, budget, consent и revocation; ни одной дублирующей SQL-таблицы.~~
- [x] ~~Публичный `SearchPlannerService` имеет методы для всех SP-03 действий; стандартная сборка запрещает authorize/revoke без отдельного server-owned action guard.~~
- [x] ~~Созданы 16 offline regression tests, обновлён GitHub focused workflow, написан `SEARCH_PLANNER_STAGE03_GENERATION_WORKFLOW.md`.~~
- [ ] Получить PASS focused+полного Linux/Windows QA на **едином текущем HEAD** PR #34.
- [ ] SP-13: фактически подключить доверенный owner/session verifier; инжектируемый guard сам по себе не является аутентификацией HTTP пользователя.
- [ ] SP-16: PostgreSQL17 fresh+upgrade и реальные concurrency/idempotency tests с disposable DB, после чего можно зачеркнуть полный SP-03.

**Почему начал SP-03 до завершения SP-02:** PR #31 пока не публикует Deep V2. У SP3 существует независимый рабочий V1 контракт и legacy SQG PostgreSQL, поэтому можно реализовать **совместимый с V1** сервис без принятия V2 и без merge чужой ветки. Порядок и критерии не отменены: SP-02 остаётся открытым и вернётся в работу после появления настоящей C9 V2 публикации.

**SP-04 (частично, готовность релиза НЕ подтверждена):**
- [x] ~~Создан собственный SP GenerationExecutionController с отдельным owner gate, ровно одной платной попыткой на явный вызов и default-deny поведением.~~
- [x] ~~Публичный SearchPlannerService получил status/preflight/execute_one/cancel/stale inspection/manual release/recover.~~
- [x] ~~Сохранены существующие PostgreSQL SQG блокировки/ledger/one-shot OpenRouter; никаких изменений применённых миграций.~~
- [x] ~~Добавлены offline regressions для разрешения расходов, zero-provider reads, no double dispatch, stale intent→uncertain, ручного reserved-only release и PostgreSQL project-scoped status.~~
- [x] ~~CI workflow дополнен регрессиями SP-04 + legacy live transport; создан отчёт SEARCH_PLANNER_STAGE04_WORKER_RECOVERY.md.~~
- [ ] Подтвердить PASS focused/full Linux + Windows Actions по одному **точному SHA**. GitHub Actions на предшествующем SHA 7a3c053f находился в очереди.
- [ ] Проверить реальный PostgreSQL 17 concurrent reserve и restart до/после intent в disposable DB.
- [ ] Дореализовать supervision/heartbeat/lease и реальный процессный запуск (SP-15), подтверждаемое invoice reconciliation (без имитации bill=0), owner HTTP session (SP-13).
- [ ] Только после QA/интеграции закрыть SP-04, не путать кодовую реализацию и production release.

## Протокол следующего чата

1. Сначала открыть **этот файл** из **SP-ветки** и проверить branch HEAD, PR #34, CI, состояние PR #31.
2. Открыть подробности задачи в `docs/SEARCH_PLANNER_STAGE_EXECUTION_ROADMAP_RU.md` и `docs/SEARCH_PLANNER_STANDALONE_MODULE_RELEASE_PLAN.md`.
3. Делать код, DB migration, тесты только в SP-ветке. Не переносить чужие Collector files и не менять main.
4. Запустить доступный offline/PG QA; проверить GitHub Actions на **точном SHA**. Не сообщать PASS, если queued/in_progress/not run.
5. По окончании этапа обновить именно этот чеклист: `- [x] ~~**SP-XX ...**~~` и добавить ссылку на commit/CI. При частичной работе оставить `- [ ]`, подписать реальный статус и блокер.
6. Зафиксировать SHA `main` и SP-ветки в финальном отчёте. Ни одно выполненное условие не должно зависеть от неслитой ветки Collector.

## Журнал проверок

- **SP-00:** завершён как аудиторская/документальная работа; source of truth — документы СП-00.
- **SP-01:** код foundation + unit tests/CI workflow уже в SP-ветке, но до проверки точного HEAD CI статус оставлен незавершённым. Не объявлять весь SP-01 релизом.
- **SP-02:** V1 hardening и тесты записаны в SP-ветку (последняя реализация `7d24f632`, workflow `bcde8792`, документация `535a822c`); реальный C9 Deep V2 отсутствует, CI текущего HEAD пока queued. Этап НЕ зачёркнут.
- **SP-03:** реализован V1-compatible GenerationWorkflow с explicit owner consent guard и 16 тестами (кодовые коммиты `03dfe44e`, `8034e043`, `d78b189c`, `d9aa8925`, CI `cf11c8ee`), QA/Pg concurrency pending. Не объявлять завершённым до зеленого CI/дополнительных release gates.

- **SP-04:** реализация GenerationExecutionController + offline tests + CI расширение внесены в SP-ветку; внешняя учётная проверка invoices, автономный process runner/heartbeat и CI точного HEAD пока не подтверждены. Чекбокс остаётся пустым.
