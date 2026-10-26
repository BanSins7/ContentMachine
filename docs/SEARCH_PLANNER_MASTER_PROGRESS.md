# Content Machine — Search Planner: единый план и журнал выполнения
**Назначение:** это канонический файл для любого следующего чата. После каждого этапа **в той же рабочей ветке** обновить этот файл коммитом. **Новое правило учёта владельца (26.10.2026): зачёркивать и отмечать [x] этап, когда его программная реализация реально внесена в ветку, даже если CI ещё queued/blocked.** Отдельно вести тесты как PASS / FAIL / QUEUED / NOT RUN, не называть [x] подтверждённым release gate. Если функциональность (не QA) ещё отсутствует, этап остаётся [ ] с объяснением.

**Репозиторий:** `BanSins7/ContentMachine`  
**Единственная SP-ветка:** `feature/search-planner-full-integration`  
**Черновой PR:** https://github.com/BanSins7/ContentMachine/pull/34 — не сливать до полного release gate и согласования владельца.  
**Не редактировать:** `main`, `feature/video-collector-dev-20261023`, `feature/rtb-c-corrector-v2`.  
**Правила:** один целостный SP внутри ContentMachine, без обязательного Collector-кода; никакой платной LLM без отдельного согласия, никаких правок применённых SQL-миграций. Внешние зависимости отмечать отдельно, не считать их реализованными.

## Этапный чеклист (порядок неизменен)

- [x] ~~**SP-00 — инвентаризация / freeze контрактов**: точные refs main/SP/C/Collector, актуальная SQL-карта, QA-baseline и ADR.~~ Выполнен (документы: `SEARCH_PLANNER_INTEGRATION_MAP.md`, `SEARCH_PLANNER_INTERFACE_MATRIX.md`, `SEARCH_PLANNER_SP00_BASELINE_QA.md`, `SEARCH_PLANNER_MODULE_ADR.md`; завершение на `195aff78`). Это **документальный freeze**, не runtime release.
- [x] ~~**SP-01 — package facade, public DTO, config и dependency ports (код)**~~. **IMPLEMENTED / CI QUEUED**: `service.py`, `api_contracts.py`, `configuration.py`, `ports.py`, `__init__.py`, offline tests. Настоящий Collector не нужен для импорта; результаты CI всё ещё не подтверждены.
- [ ] **SP-02 — доверенный SP1/SP2 C9 reader + HypothesisContext V1/V2**: проверить source row, два Review, published final tree, version/identity, исключить silent 14→11 truncation, tests v1. Включать реальный V2 только после появления upstream C9 Deep V2 schema/publication (PR #31 первоначально только freeze). **V1 код реализован, QA ожидает CI, Deep V2 заблокирован внешним PR #31; этап НЕ завершён.** Детали: `docs/SEARCH_PLANNER_STAGE02_C9_PROVENANCE.md`.
- [x] ~~**SP-03 — Generation Run/Batch/Quote/Budget/Consent (код)**~~. **IMPLEMENTED / CI QUEUED**: `generation_workflow.py`, серверный owner guard, устойчивые SQG-таблицы, контрактные tests. Реальная HTTP-авторизация запланирована в SP-13, PG конкурентный QA — в SP-16.
- [ ] **SP-04 — Paid worker и recovery**: supervised queue/lease/one POST, unknown billing hold, durable restart, полный ledger. **Часть кода реализована:** `operations.py`, публичные методы Service, тесты и CI; используется существующий платный worker с атомарным `dispatch_intent` и удержанием неопределённого резерва. **Открыто:** реальное процессное supervision/heartbeat, подтверждённая invoice reconciliation, PG race/crash integration и точный CI PASS. [Отчёт SP-04](SEARCH_PLANNER_STAGE04_WORKER_RECOVERY.md).
- [x] ~~**SP-05 — AI Query + Output Manager (код)**~~. **IMPLEMENTED / CI QUEUED**: pinned model/schema trace, immutable output provenance, полное coverage/candidate SHA, `get_generation_candidates()` и regressions. Без изменения старого SQG v1.
- [x] ~~**SP-06 — offline pinned multilingual embedding runtime (код)**~~. **IMPLEMENTED / QA NOT VERIFIED**: manifest SHA всех локальных весов, offline provider, version pin, fixture-isolation, unit tests/CI. Настоящие операторские multilingual веса, real smoke и quality calibration ещё не проверялись.
- [x] ~~**SP-07 — Relevance / cross-hypothesis dedup / MMR / lossless query links (код)**~~. **IMPLEMENTED / CI QUEUED**: NFKC normalization, local MMR before physical cross-hyp merge, original UUID/fact/hypothesis links persisted via existing immutable QV decisions, trusted DB read projection, tests. [SP-07 report](SEARCH_PLANNER_STAGE07_SHARED_QUERY_LINKS.md).
- [x] ~~**SP-08 — Validated Registry и QV Outbox (код)**~~. **IMPLEMENTED / CI QUEUED**: сохранён существующий atomic SQG→QV SQL ledger; добавлена полная проверка accounting run/coverage/decisions/query IDs, read-only snapshot, строгий QV event pointer, проверка реального QV outbox при replay, default-deny downstream ACK до доверенного транзакционного подтверждения. SP inbox как настоящий consumer разрабатывается в SP-12, PG race/restart QA отдельно в SP-16. [SP-08 report](SEARCH_PLANNER_STAGE08_QV_REGISTRY.md).
- [x] ~~**SP-09 — Tool Capabilities Port / Matching (код)**~~. **IMPLEMENTED / CI QUEUED**: отдельный `capabilities/`, строгий `search-planner-source-capabilities/v1`, snapshot с project ID, registry version, SHA и TTL; разделение declared/operational/policy+budget/dispatch; отсутствие адаптера даёт 0 доступных источников. Новые методы `source_capabilities()` / `match_query_capabilities()`, offline tests, CI и [отчёт SP-09](SEARCH_PLANNER_STAGE09_CAPABILITIES.md). Реальная readiness ещё не доказана.
- [ ] **SP-10 — Trusted Task Planner**: preview, quotas/costs/block reasons/coverage, dedup без потери связи с гипотезами, immutable plan hash.
- [ ] **SP-11 — SP8→SP11 projection / chunking**: DB-verified QV/C9 SHA, original candidate IDs, 117→50+50+17, 0→no_work.
- [ ] **SP-12 — Durable SP inbox / optional Collector adapter**: атомарный QV→SP ACK, отдельная downstream receipt, replay/race/crash QA, awaiting_collector без Collector.
- [ ] **SP-13 — Owner HTTP API**: project ACL, idempotency, safe GET, bounded bodies, no unauthorised paid calls.
- [ ] **SP-14 — User Interface**: полный Search Planner UI внутри ContentMachine, budgets/approvals/runs/validation/plan/handoff.
- [ ] **SP-15 — Operations**: worker supervision, monitoring, logs redaction, secure recovery, Windows/Linux.
- [ ] **SP-16 — Полный release gate**: fresh/upgrade PostgreSQL, all-up E2E + mock Collector, Windows/Linux CI, 0 implicit spend, self-contained mergeable PR; **без автоматического merge**.
- [ ] **SP-17 — feedback от реальных Collector→Discovery→SRE результатов**: measured feedback contract, настоящий анализ только после появления downstream фактов; внешняя зависимость, не подменять mock данными.

## Подэтапы и отдельные release QA gates (галочка = код написан, не CI PASS)

**SP-01**
- [x] ~~Публичный фасад, immutable DTO, конфигурация, typed ports и backward-compatible exports внесены в SP-ветку.~~
- [x] ~~Добавлены 13 контрактных tests и отдельный GitHub workflow; на промежуточных коммитах focused QA прошёл.~~
- [ ] Подтвердить зелёный **Linux + Windows + focused CI** на согласованном HEAD. [x] означает реализованный код; оставить этот PASS как отдельный внешний QA gate.

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
- [ ] После реализации supervision/lease отметить SP-04 [x], а QA продолжать отслеживать отдельно.

**SP-05 — AI Query + Output Manager (реализация без подтверждённого release QA):**
- [x] ~~Аудированы существующие pinned OpenRouter prompt/schema/strategy, одноразовый transport, методы ThinkQE/MuGI/Jina и OutputManager: дублирующая генерация не создавалась.~~
- [x] ~~В атомарное settlement добавлена проверка SHA сохранённых HypothesisContext и повторный pinned Stage D method-trace audit.~~
- [x] ~~Добавлена server-trusted валидация полного `query_candidates/v1`: исходные UUID, source facts, per-hypothesis coverage, честное zero-work состояние, fail-closed при расхождениях.~~
- [x] ~~Публичный Service получает проверенные кандидаты без расходов через `CandidateBundleReader` и SHA digest; старый SQG→QV контракт не меняется.~~
- [x] ~~Созданы offline регрессионные тесты, расширен focused CI SP-01..SP-05, написан отдельный технический отчёт.~~
- [ ] Получить PASS на точном SHA focused+Linux/Windows CI, затем recheck critical tests.
- [ ] Проверить реальный PostgreSQL17 settlement/full coverage/outbox + mock OpenRouter billable-invalid response через disposable DB на SP-16.
- [ ] Не считать подтверждёнными реальные видеорезультаты, количество просмотров и recall без Collector/SRE.
- [ ] SP-05 реализация теперь [x] по новому правилу; необходимый CI/PG QA отражён отдельными незакрытыми пунктами.

**SP-06 — подготовка настоящей локальной модели (код реализован, этап НЕ закрыт):**
- [x] ~~Создан `query_validation/model_manifest.py`: канонический identity + полный SHA-256 по каждому байту локальных конфигов/весов, ровно один manifest, запрет symlinks, drift и дополнительных файлов.~~
- [x] ~~Создан offline-only operator CLI `provision_model.py` с режимами write-once и verify. Он не скачивает модель и не подтверждает подлинность Git-ревизии upstream самостоятельно.~~
- [x] ~~В `SentenceTransformerEmbeddingProvider` нельзя задать произвольный `model_key`: он должен совпадать с derived SHA; загрузка только `local_files_only=True`, `trust_remote_code=False`; проверяются настоящая размерность и целостность после загрузки.~~
- [x] ~~В Query Validation service production допускается только точный проверенный provider, test fixture — только `HashingTestEmbeddings` с `allow_test_embeddings=True`; проверяется дрейф весов до и после инференса перед PostgreSQL ACK.~~
- [x] ~~Добавлены offline регрессионные тесты с mock загрузчиком без GPU/весов/сети, отдельный optional pinned requirements, CI и эксплуатационный runbook.~~
- [ ] Получить PASS на focused + Linux/PostgreSQL17 + Windows Actions на общем текущем HEAD. Код этапа отмечен [x]; тестовый gate остаётся открытым.
- [ ] Оператору заранее установить настоящие multilingual веса, проверить модель/40-символьный upstream revision, создать manifest и выполнить реальный offline русский+английский encode smoke; никаких весов в GitHub.
- [ ] Quality calibration multilingual relevance/MMR на размеченном датасете + реальные PostgreSQL17 QV race/upgrade/E2E — SP-16. Нельзя приписывать mock тестам качество настоящей модели.

**SP-07 — восстановление множественных связей и исправление quota-loss:**
- [x] ~~Нормализация текстов NFKC/casefold; локальный semantic/exact dedup и MMR выполняются до cross-hypothesis dedup только между реально выбранными кандидатами.~~
- [x] ~~Детерминированный выбранный winner остаётся selected; aliases сохраняют исходные candidate UUID + hypothesis, selected ranks и coverage пересчитываются.~~
- [x] ~~Создан project-scoped read-only `search-planner-query-hypothesis-links/v1` из настоящих immutable QV decisions + SQG source facts, без новой SQL-миграции или смены validated_queries/v1.~~
- [x] ~~Добавлены regression tests, включая quota-suppression, multi-hypothesis provenance, NFKC, locale/mode guard, 0-work, forged DB alias; focused CI обновлён.~~
- [ ] GitHub Actions focused/full Linux+Windows PASS на текущем HEAD (см. очередь PR #34).
- [ ] PostgreSQL17 + реальная multilingual модель / качество и SP-16 E2E release gate.

**Отдельное расследование тестов — 26.10.2026:**
- [x] ~~Проверены GitHub Actions jobs: focused SP run 40300195286 имел статус queued, без runner/шагов и без результата; другие ветки тоже queued. Это не pytest/unittest FAIL.~~
- [x] ~~Добавлена concurrency cancel-in-progress в SP-owned CI, чтобы отменять лишние устаревшие прогоны; действительно наблюдалась отмена старого SP workflow.~~
- [ ] Причину недоступности hosted runners/очереди проверить в GitHub Actions Settings (quota, repository/organization runners, billing/service). Нельзя заявлять, что root cause исправлен.
- [ ] Когда новый job реально запустится, устранить фактические Python failures (если будут), сохранить логи и SHA. В текущем окружении GitHub clone заблокирован DNS.

**SP-08 — immutable Registry/QV Outbox:**
- [x] ~~Повторно использованы существующие `qv_runs/qv_decisions/qv_coverage/qv_outbox` и атомарный SQG→QV ACK; нет новой конфликтующей миграции.~~
- [x] ~~`integrity.py` проверяет каждую QV decision/query/coverage, исходные/выбранные суммы, корректный zero-work, отсутствие двойных UUID и рангов.~~
- [x] ~~`get_validated_bundle` читает проектно-ограниченный generation-joined snapshot в одной REPEATABLE READ READ ONLY транзакции.~~
- [x] ~~`pending_validated_outbox` и replay подтверждают DB-истину: versioned pointer, generation ID, mode, count, реально созданный durable outbox и upstream SQG ACK.~~
- [x] ~~`ack_validated_outbox` больше не подтверждает delivery без server-owned `verified_ingestion` в той же транзакции; отсутствие SP-12 получателя означает pending, не поддельный ACK.~~
- [x] ~~Добавлены offline регрессии SP-08 и запуск в focused CI; `SEARCH_PLANNER_STAGE08_QV_REGISTRY.md` документирует contract.~~
- [ ] Подтвердить GitHub focused+full Linux/Windows PASS на exact SHA и устранить реальные ошибки, если workflow начнёт исполняться.
- [ ] SP-12: реализовать настоящий durable receiver/inbox, который использует server-owned ACK verifier. Не пытаться заменить её пользовательским булевым полем.
- [ ] SP-16: проверить PG17 конкурентные 2-воркера, откат после partial QV row, идемпотентный replay и real test schema upgrade.

**SP-09 — versioned Tool Capabilities Port, реализация [x]:**
- [x] ~~Прочитан текущий Collector PR #20: DTK декларирует keyword/search_posts, остальные три источника без операций, ни один не готов к реальному запуску.~~
- [x] ~~Создан отдельный `capabilities/` и строгий project-bound, expiry-bound, SHA-pinned source snapshot без импорта/дублирования Collector.~~
- [x] ~~Разделены declared, operationally_ready, policy_and_budget_verified и planning_compatible; неизвестная цена, квота, язык и регион блокируют планирование.~~
- [x] ~~Добавлен legacy Collector declaration translator, не превращающий декларацию в verified readiness.~~
- [x] ~~В `SearchPlannerService` добавлены read-only source_capabilities/match_query_capabilities с external_dispatch_authorized=False.~~
- [x] ~~Добавлены offline tests snapshot/matching/expired/quota/price/language/no-Collector, обновлён CI SP-01..SP-09.~~
- [ ] Focused+full Linux+Windows CI PASS на точном HEAD (Actions пока queued, не Python FAIL).
- [ ] SP15/16: реальный server-owned capabilities publisher от Collector и E2E с actual provider health/policy/quota/price; dispatch разрешает только отдельный владелец.
- [ ] SP10: использовать только DB-verified QV, не пользовательский accepted JSON и не неподтверждённые preview.
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

- **SP-05:** исправлены OutputManager и source-provenance границы, добавлены `ai_query/provenance.py`, публичная проекция и unit/CI. GitHub Actions exact SHA пока не подтвердил PASS; PostgreSQL full E2E остаётся открытым. Полный этап не зачёркнут.

- **SP-06 (26.10.2026):** модельный SHA pin, независимый offline provisioning CLI, строго реальный semantic_local provider, QV double-check, тесты/CI и инструкция внесены в SP-ветку. Точный Actions PASS и проверка настоящих операторских весов **не подтверждены**; SP-06 code отмечен [x], production QA ещё не подтверждён.

- **SP-07 (26.10.2026):** code DONE [x], тесты добавлены, но Actions ещё queued; post-MMR physical query merging now preserves all shared hypothesis candidate/fact IDs. SP-owned workflow concurrency cancels stale PR revisions. Release QA separate.

- **SP-08 (2026-10-26):** реализация [x]: строгая QV registry accounting, immutable run/outbox replay, callback-verified atomic downstream ACK, tests+CI. QA exact SHA пока QUEUED; настоящий SP Inbox/Collector receiver остаётся SP-12.

- **SP-09 (2026-10-26):** read-only upstream audit, versioned source snapshot/matcher и регрессионные tests+CI закоммичены. Код [x], настоящий adapter/CI QA всё ещё ожидается.
