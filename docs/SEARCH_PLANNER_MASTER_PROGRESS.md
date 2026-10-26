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
- [x] ~~**SP-10 — Trusted Task Planner (код)**~~. **IMPLEMENTED / CI QUEUED**: доверенное получение immutable production QV/C9/SQG/outbox через PostgreSQL, строгая проверка всех selected UUID и multi-hyp source fact links; детерминированный fair round-robin held-plan, explicit all-source policy, операции/локали/рынок, quotas/cost/candidate caps, Decimal 8 знаков, stable plan/task hashes и полное blocked/coverage; новая аддитивная миграция `054_sp_held_task_plans.sql` со снимками и owner approval audit, идемпотентное `commit_held_source_plan()` без provider dispatch. Unit-тесты и CI внесены. **QA:** точный HEAD не запускался/в очереди; реальный PG17 migration/concurrency и HTTP owner ACL остаются SP-13/16. [Подробный отчёт](SEARCH_PLANNER_STAGE10_TRUSTED_TASK_PLANNER.md).
- [x] ~~**SP-11 — SP8→SP11 trusted handoff projection / chunking (код)**~~. **IMPLEMENTED / EXACT-HEAD CI QUEUED**: отдельный `handoff/{contracts,projection,verification}.py`, production QV/C9/SQG+outbox source loader из SP-10, collector-compatible `validated_query_set/v1` с исходными UUID и 7 обязательными полями, детерминированное разбиение 117→50+50+17 (0→no_work), все QV decision/coverage/alias/source_fact IDs сохранены, per-chunk SHA и полный `projection_sha256`, fail-closed при повреждённых пакетах или fake `accepted`. Добавлены public read-only `preview_verified_handoff()`, JSON Schema, unit tests и CI. **Без** ACK QV и **без** Collector receipt — это SP-12. [Отчёт SP-11](SEARCH_PLANNER_STAGE11_HANDOFF_PROJECTION.md).
- [x] ~~**SP-12 — Durable SP inbox / optional Collector adapter (код)**~~. **IMPLEMENTED / EXACT-HEAD CI QUEUED**: новая аддитивная `055_sp_handoff_inbox.sql` со SP-owned immutable events/chunks/original-query links и отдельными delivery attempts/receipts; QV row-lock→все 50/50/17 chunks+SHA+lineage→QV ACK в **одной** PostgreSQL-транзакции; уникальные ID/replay/crash guards; zero-work; default `EmptyCollectorAdapter` и `awaiting_collector` без внешних вызовов; явный owner+SP10 held approval перед intake; durable uncertain reservation до accept, независимый get_receipt, никаких blind retries, отдельная receipt-only reconciliation; тесты и CI. **Не утверждаем:** реальные PG17/Windows/Linux результаты CI и наличие production Collector adapter — они открыты в SP16. [SP12 report](SEARCH_PLANNER_STAGE12_DURABLE_INBOX.md).
- [x] ~~**SP-13 — Owner HTTP API (код)**~~. **IMPLEMENTED / EXACT-HEAD CI QUEUED**: `http_api.py` с lazy route mount в `app.py`, локальный Host/Origin/loopback/`X-ContentMachine` + server-owned `CM_SP_OWNER_TOKEN` для каждого POST, project lookup + scoped PostgreSQL, service assembly owner guards; безопасные GET publications/C9/runs/QV/capabilities/plan/inbox, bounded strict JSON, no paid GET; mutation routes SP03–SP12, safe Collector default. Paid worker по умолчанию выключен, разрешается только отдельным operator flag, SQL financial consent и **durable** per-project HTTP `idempotency_key`; миграция `056_sp_http_paid_idempotency.sql` и `http_paid.py` блокируют повторную оплату при crash/unknown result. Identical generation config SHA project-locked reuse. Regression suites+CI и [отчёт SP-13](SEARCH_PLANNER_STAGE13_OWNER_HTTP_API.md). **QA:** exact-HEAD runner, реальный PG17 migration/crash/browser, paid live и remote multiuser auth НЕ проверены. Remote access не поддерживается.
- [x] ~~**SP-14 — полноценный Search Planner UI (код)**~~. **IMPLEMENTED / адаптированные JS проверки 15/15 + mock DOM E2E 2/2 / EXACT-HEAD GitHub CI QUEUED:** самостоятельный шестиэкранный проектный UI C9→verified OpenRouter model config+quote/consent/one-paid-batch→QV с группировкой по гипотезам, reasons, relevance/MMR proxy→HELD→SP Inbox/отдельные Collector receipts→read-only SQL history. В карточке проекта + адаптивный CSS и static whitelist app.py, безопасный owner token в памяти только текущей вкладки, очистка при всех переходах, отдельные financial/HOLD/Collector подтверждения; zero POST on open, server project-scoped overview/models, после refresh доступны оригинальные UUID **и paid idempotency keys + uncertain state** без localStorage. Созданы Node 22 JS contract tests `test_search_planner_ui.mjs`, mock-click flow `test_search_planner_ui_dom.mjs`, Python SQL overview fixture; оба UI набора включены в CI. [Основной отчёт SP14](SEARCH_PLANNER_STAGE14_OWNER_UI.md), [дополнения full-path QA](SEARCH_PLANNER_STAGE14_USER_INTERFACE.md). **Отдельно открыто:** официальный CI PASS, real browser + PostgreSQL17/Windows/Linux + Collector adapter release E2E (SP16); результаты в V8 не следует выдавать за штатный Node PASS.
- [ ] **SP-15 — Operations**: **SP-15A/B + SAFE SP-15C SUPERVISOR CODE IMPLEMENTED / EXACT-HEAD CI QUEUED** — создан read-only project-scoped operational snapshot (aggregation SQG/QV/inbox/uncertain и Decimal), GET /operations, opt-in bounded foreground QV→SP inbox pass без OpenRouter/Collector/платного search, тесты+CI, инструкция `SEARCH_PLANNER_STAGE15A_SAFE_SUPERVISION.md`. **Реализовано дополнительно:** SP-15C `safe_daemon.py` (explicit continuous QV-only process, independent thread heartbeat, signal stop, strict lease release, structured redacted events; tests + CI). **Всё ещё НЕ реализованы:** evidence-backed operator invoice reconciliation, реальный Collector capability publisher/adapter readiness, полноценная platform process manager integration и Windows/Linux PG17 crash/full recovery; этап SP-15 НЕ зачёркнут. Реальные PostgreSQL17/Actions PASS отсутствуют.
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
- [x] ~~SP-13: подключена локальная owner-авторизация HTTP через обязательный секрет окружения, повторные Host/Origin/loopback проверки и scoped серверный OwnerActionGuard.~~ Remote/multiuser sessions НЕ реализованы и не заявлены.
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
- [ ] Дореализовать supervision/heartbeat/lease и реальный процессный запуск (SP-15), подтверждаемое invoice reconciliation (без имитации bill=0), проверка local owner HTTP secret+связки browser под реальным сервером (SP-16).
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
**SP-10 — Trusted Task Planner (реализация [x], QA отдельно):**
- [x] ~~Введён `task_planner/repository.py` для загрузки DB-verified QV+SP07 links, проверки QV outbox/SQG ACK и итогового C9 Deep V1 tree с Human Reviews.~~
- [x] ~~Создан `task_planner/service.py`: full held plan, round-robin между гипотезами, stable NFKC per-source physical dedup, all candidate/hypothesis/source_fact_ids, deterministic SHA, no loss при quota.~~
- [x] ~~Источники — только SP09 trusted capability port; explicit single_best/all_compatible routing, blocked capability/locale/market/pricing/quota, source-level request and candidate caps, Decimal USD; никакого Collector import/provider call.~~
- [x] ~~Добавлена новая аддитивная `054_sp_held_task_plans.sql`: immutable SP-owned plan snapshot и owner approval/audit, project QV FK, уникальный idempotency key, immutable triggers; исторические 015/016 не менялись.~~
- [x] ~~`commit_held_source_plan()` повторно генерирует DB-trusted план, требует server-owned owner guard, транзакционно сверяет QV source и сохраняет HELD without dispatch; collisions fail closed.~~
- [x] ~~Добавлены offline tests trust/cost/quota/dedup/0-work/reordering/fairness/multisource, fake DB idempotency/receipt semantics и отдельный focused SP01..SP10 workflow.~~
- [ ] Получить exact SHA GitHub Actions focused/full Linux+Windows PASS после назначения runner; не путать QUEUED и FAILURE.
- [ ] SP16: disposable PG17 fresh/upgrade schema 054, два параллельных owner commit, rollback/replay/conflicting key; реальный capability publisher, no provider calls.
- [ ] SP13: привязать owner guard к реальной authenticated локальной сессии и ACL; default `SearchPlannerService()` deny commit.
- [ ] SP11/12: отдельно создать handoff projection/chunks/inbox; immutable HELD approval — НЕ Collector receipt и НЕ внешняя авторизация.
**SP-11 — Verified handoff projection/chunking (код [x]):**
- [x] ~~Подтверждена реальная несовместимость SP8 `validated_queries/v1` и Collector `validated_query_set/v1` из VC ветки (только чтение), сохранён исходный `candidate_id == query_id`, `query_mode -> mode`.~~
- [x] ~~`VerifiedHandoffProjector` читает только DB-verified production QV через SP10 loader, вместе с C9 approved publication/version/SHA, SQG generation/ACK, настоящим QV outbox и SP07 multi-hyp provenance.~~
- [x] ~~Созданы самостоятельные `handoff/contracts.py`, `projection.py`, `verification.py`: схемы, trusted SHA checks, полная source-facts/hypothesis/decision lineage, отсутствие fake client accepted.~~
- [x] ~~Детерминированные полные чанки <=50 с origin и hashes: 117→50+50+17, 0→no_work без пустого Collector QuerySet, до 480 без потери query UUID.~~
- [x] ~~Публичный `SearchPlannerService.preview_verified_handoff()` read-only: no QV ACK, no Collector receipt, no paid/provider operations; фактический mutable Inbox остаётся SP12.~~
- [x] ~~Созданы `docs/SEARCH_PLANNER_HANDOFF_V1.schema.json`, технический `STAGE11_HANDOFF_PROJECTION.md`, regression unit-тесты и focused CI SP01..SP11.~~
- [ ] Exact-head Python/Windows/Linux focused CI PASS: GitHub-hosted runner остаётся QUEUED, никакой unit PASS не утверждается.
- [ ] SP16: настоящий PG17 C9→QV→SP11 end-to-end с 117 документированными selected rows, DB tampering and crash tests.
- [ ] SP12: durable inbox/source ACK at one DB transaction, separate Collector delivery receipt and awaiting_collector state — не отмечать как сделанное на SP11.

**SP-12 — Durable SP inbox / Optional Collector (реализация [x], QA отдельно):**
- [x] ~~Добавлена только новая additive миграция `055_sp_handoff_inbox.sql`: sp_handoff_events, chunks, query_links, delivery_attempts, receipts, SQL FK/UNIQUE/immutability/status.~~
- [x] ~~В `handoff/repository.py` настоящая транзакция QV row FOR UPDATE, проектная проверка и SHA, вставка полного event/chunks/links, read-after-write check, только затем qv_outbox delivered_at в этой транзакции.~~
- [x] ~~Same-source replays: уникальный inbox + все chunks/provenance должны совпасть, else 409; отсутствие Collector не теряет событие; 0 selected сохраняется как durable no_work.~~
- [x] ~~В `handoff/collector_adapter.py` default no-network EmptyCollectorAdapter и QA MockCollectorAdapter; версия `search-planner-collector-durable-receipt/v1`, проверки project/UUID/chunk/query SHA/contract/accepted_at.~~
- [x] ~~В `handoff/dispatcher.py`: явное server-owned owner guard+SP10 approval, один chunk на запрос, durable `uncertain` до внешнего accept, независимый receipt readback, per-chunk separate ACK, blocked blind retry при timeout, explicit reconcile только get_receipt.~~
- [x] ~~В сервисе: ingest_verified_qv_handoff(), ingest_pending_qv_handoffs(), get_handoff_status(), deliver_next_handoff_chunk(), reconcile_handoff_chunk(). Defaults без Collector и без owner не авторизуют исполнение.~~
- [x] ~~Добавлены две unit test suites: fake transactional DB (117→3 chunks, 10 replays, rollback до/после ACK, no-work, no adapter, owner ACL, unknown outcome/reconcile), fake Collector receipt tampering; CI SP01–SP12.~~
- [ ] Получить exact-head focused/full Linux/Windows CI PASS после назначения GitHub-hosted runner; QUEUED не равно failure.
- [ ] SP16: PostgreSQL17 fresh/upgrade 055 и реальный конкурентный 2-worker/crash/WAL E2E, SQL triggers and locks, mock + version-verified real Collector adapter integration.
- [ ] Collector production branch должен отдельно опубликовать аутентифицированный durable ingest/get_receipt порт и release/cost/rights проверки. Никакой fake receipt от отсутствующего Collector.
- [ ] SP13: owner HTTP ACL и request parsing, не допускать callbacks из browser; SP14 UI, SP15 наблюдаемость/worker.
**SP-13 — Authenticated Owner HTTP API (код [x], тесты отдельно):**
- [x] ~~Создан проектно-ограниченный `intelligence/search_planner/http_api.py`, GET/POST router и lazy 2-route mount `app.py`, без обязательного VC/модельного import для остальных маршрутов.~~
- [x] ~~GET: status, reviewed C9 publication picker, generation/budget/results/QV/links, capabilities, held-plan and source handoff preview, actual inbox status; никаких платных/provider вызовов.~~
- [x] ~~POST: строгий local-only Host/Origin/X-ContentMachine + адрес loopback + серверный CM_SP_OWNER_TOKEN (>=32 chars), constant-time comparison, `OwnerActionGuard` bound to project/actions.~~
- [x] ~~Операции конфигураций/планирования/quote/consent/revoke/one-batch/recovery/cancel/QV semantic validation/tasks HELD/inbox/drain/Collector reconcile, только с проверенными scopes и полным JSON key whitelist.~~
- [x] ~~Новая additive миграция `056_sp_http_paid_idempotency.sql` и `http_paid.py`: per-project idempotency before any paid worker, completed replay 0 вызовов, unknown/crash needs_attention, next batch никогда не запускается по старому ключу; explicit CM_SP_PAID_HTTP_ENABLED=1 and server owner+ledger consent mandatory.~~
- [x] ~~Повтор одинаковой настройки generation теперь project-locked по config SHA и возвращает исходный config ID/revision, исключая дубликат при повторном браузерном POST.~~
- [x] ~~HTTP response version v1, body <=32KB, без NaN/duplicate object keys/chunked, exact query params, redacted unknown failures; unit tests HTTP/C9/paid ledger/config replay включены в focused SP01..SP13 CI.~~
- [ ] Получить exact-HEAD focused/full Linux/Windows PASS после назначения runner. GitHub queued не означает FAILED или PASSED.
- [ ] SP16: PostgreSQL17 apply/upgrade 054/055/056; настоящий `app.py` local request/CSRF/browser smoke; две параллельные paid POST; WAL crash and financial reconciliation — пока не верифицировано.
- [ ] SP14: добавить UI token-entry без сохранения секрета в JS/localStorage и полноценный интерфейс. Remote/multiuser OAuth вне текущей single-local-owner архитектуры.
**SP-14 — Project UI (код [x], release QA отдельно):**
- [x] ~~`site/index.html` + `site/search-planner.css`: шесть адаптивных accessible экранов C9, generation/budget, QV, task planner, SP Inbox, immutable history.~~
- [x] ~~`site/projects.js`: отдельная кнопка в действительной карточке проекта, возврат и закрытие owner secret при уходе. `app.py` whitelist для 3 статических ресурсов.~~
- [x] ~~`site/search-planner-core.js` + `search-planner.js`: typed API calls only, strict explicit C9 language/market/policy, real PG UUID, in-memory owner secret, no persistent browser storage, no untrusted `innerHTML`; full JSON export and bounded preview.~~
- [x] ~~`ui_overview.py` + `GET /overview`: транзакционный read-only project-scoped список реальных SQG configs/runs/QV/inbox, не мнимые статусы. `GET /models`: только actual verified model snapshots и Stage D pinned prompt/schema hashes, без OpenRouter refresh/spend.~~
- [x] ~~Отдельный owner consent, quote, paid one-batch, revoked consent, uncertain review, unsubmitted reserve release; task HELD и отдельный one-chunk Collector intake; no Collector→awaiting_collector, no-work, needs_attention честно видны.~~
- [x] ~~Никаких blind paid retry при ручном повторе: idempotency key сохраняется до закрытия UI, генерация нового требует отдельного явного действия с предупреждением, backend SP13 ledger остаётся источником истины.~~
- [x] ~~Добавлены Node `tests/test_search_planner_ui.mjs` (12 тестов) и Python `test_search_planner_stage14_overview.py`; focused CI использует Node 22. На доступном V8 test shim выполнены 12/12 тестовых тел, JS parse 3/3, DOM 55/55 unique, routes/handlers complete; **не** выдавать за официальный Node/PG17 PASS.~~
- [ ] Официальный GitHub Actions exact-head Node/Python focused PASS после назначения runner; проверить реальные PG17 SQL миграции и project RLS/keys до production.
- [ ] SP16: end-to-end Chromium/Firefox/Windows/Linux UI с настоящим local owner secret, C9, priced quote, paid disabled+enabled, QV policy/model available, HELD/inbox/receipt. Адаптер Collector фактически отсутствует — это не выдавать за receipt.
- [ ] SP15: supervision/heartbeat/metrics/redacted logs/operator recovery + Windows/Linux runbook; не запускать платный background worker без owner consent.
**SP-15A — read-only мониторинг / безопасная обработка SP inbox (код выполнен, release QA отдельно):**
- [x] ~~Добавлены точные project-scoped SQL-агрегаты generation/QV/fixture/inbox/attempts, Decimal сумм и machine warning codes, одна read-only repeatable-read транзакция.~~
- [x] ~~Добавлены SearchPlannerService.operational_snapshot() и GET /api/projects/{project_id}/search-planner/operations с прежним transport/project ACL, без внешних запросов.~~
- [x] ~~Добавлен явный bounded foreground CLI --mode observe|inbox; по умолчанию read-only, режим inbox только QV→durable SP12, ни одного платного/provider/Collector accept, без скрытого запуска на import.~~
- [x] ~~Добавлены 9 offline test cases и Python focused CI, runbook `SEARCH_PLANNER_STAGE15A_SAFE_SUPERVISION.md`.~~
- [ ] Проверить Node/Python/Linux/Windows CI на **точном текущем SHA**, disposable PostgreSQL17+WAL, в том числе реальный read-only GET и 2 одновременно запущенных inbox процесса.
- [x] ~~**SP-15B:** добавлена аддитивная миграция `057_sp_supervisor_leases.sql` и per-project lease для единственного небиллингового qv_inbox worker; atomic UPSERT при expired, monotonic fence_generation и owner-bound heartbeat/release.~~
- [x] ~~Добавлен `run_fenced_inbox_cycle()` и CLI `--mode leased-inbox`: проигравший конкурент не выполняет QV, победитель не может бесконтрольно выполнять paid/Collector вызовы; failure и stale-fence fail-closed.~~
- [x] ~~Добавлены 9 offline tests на lease migration/acquisition/contention/stale holder/explicit CLI и focused CI; написан `SEARCH_PLANNER_STAGE15B_FENCED_LEASES.md`.~~
- [x] ~~**SP-15C (safe QV process slice):** создан `safe_daemon.py` с явным `--enable-safe-inbox` (по умолчанию ничего не запускается), foreground QV→SP цикл, отдельный heartbeat-thread, fenced pre/post проверки, graceful Ctrl+C/SIGTERM, redacted JSON status, nonzero shutdown при неподтверждённом lease release.~~
- [x] ~~Добавлены `test_search_planner_stage15_daemon.py` (14 offline cases, включая реальный независимый heartbeat на slow fixture, owner stop, lease takeover/loss, failed release, forged zero, secret redaction), расширен focused Linux CI **и отдельный Windows Python 3.12 SP15A–C smoke job**, плюс runbook `SEARCH_PLANNER_STAGE15C_SAFE_DAEMON.md`.~~
- [ ] SP-15D: подтверждаемая invoice reconciliation только по подлинному provider billing evidence и operator approval; real Collector capability publisher/readiness+receipt ports без копирования VC; production alerting/log integration. Биллинг не закрывать искусственным 0.
- [ ] SP-15 platform QA: Linux/Windows supervisor start-stop и crash/replay на PostgreSQL17, окончательная проверка telemetry/heartbeat/fairness под нагрузкой.
- [ ] SP-16: PG17 свежая установка и upgrade миграции 057, two-process takeover/expiry, WAL crash, Windows/Linux start-stop/CI на точном HEAD.
- [ ] Окончательно закрывать SP-15 только после завершения всех программных подэтапов; CI PASS отслеживать отдельно от [x].

**SP-15C QA gate:**
- [ ] Exact-HEAD focused + full GitHub Linux/Windows CI PASS (на коммите после добавления теста), а не QUEUED/NOT VERIFIED.
- [ ] Проверить двухпроцессный takeover/PG17 WAL crash, heartbeat thread длительной транзакции, cross-project isolation и корректность release без тайных платных вызовов.
- [ ] Не объявлять завершённым весь SP-15 из-за одной рабочей safe-daemon подсистемы. SP-04 paid supervision и external invoice evidence — отдельные workstreams.

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

- **SP-10 (2026-10-26):** полный код trusted task preview + immutable HELD owner approval + migration 054 + offline tests/CI/docs внесён, этап [x]. CI по конкретному SHA ещё QUEUED, PG17 миграционные/конкурентные проверки и подлинная owner HTTP ACL остаются открытыми QA/integration gates.

- **SP-11 (2026-10-26):** code [x]; DB-verified QV/C9 source → lossless versioned Collector V1 chunks with 117→50+50+17, zero-work and SHA/full provenance; strict read-only service, tests, schema and CI committed. Production PostgreSQL/Runner QA separately OPEN; real SP inbox/Collector ACK belongs to SP12.

- **SP-12 (2026-10-26):** Код [x], atomic QV→SP event/chunks/links/ACK, optional default EmptyCollector, immutable separate external receipt + no-blind-retry, tests/CI/docs committed. GitHub runners remain queued and real PG17/Collector acceptance are OPEN, not claimed PASS.

- **SP-13 (2026-10-26):** реализация [x]: полноценный single-local-owner project HTTP facade SP1–SP12, local server secret auth, typed GET/POST, no paid GET, manual paid flag and durable 056 one-shot idempotency, approved Deep C9 picker, regression tests/CI/docs; runner/PG17/browser E2E remain QA OPEN. No change to main or VC.

- **SP-14 (2026-10-26):** CODE [x]. Адаптированные JavaScript/V8 contract checks 15/15 + mock DOM full click-flow 2/2; штатные Node/Python/PG17/browser GitHub CI продолжают быть QUEUED/NOT VERIFIED. Предыдущий клиентский 12/12 smoke расширен новыми security/QV/paid history/DOM сценариями. Следующий SP15.

- **SP-15A (2026-10-26):** кодовые подзадачи [x], родительский SP-15 [ ]: read-only metrics/GET, bounded explicitly invoked QV inbox supervisor, error redaction, no paid/Collector dispatch; tests and focused CI workflow added. Текущий GitHub Actions runner status НЕ PASS; PostgreSQL17, Windows/Linux и end-to-end ещё не запущены. Следующая работа SP-15B durable supervision/lease + ops reconciliation.
- **SP-15B (2026-10-26):** кодовые задачи [x], SP-15 [ ]: новая 057 PostgreSQL fenced lease по проекту, atomic acquire/heartbeat/release, fixed 120s TTL, loss-of-lease blocking, explicit leased-inbox CLI, 9 offline regressions + focused CI и документ. Lease — ТОЛЬКО safe QV→SP inbox, не billable scheduler. Целевые PG17/Windows/Linux tests ПОКА НЕ PASS. Следующая SP-15C полноценная эксплуатационная supervision/reconciliation.

- **SP-15C (2026-10-26):** безопасная процессная QV-only часть [x], SP-15 parent [ ]. `safe_daemon.py` — explicitly enabled foreground service, independent continuous fenced heartbeat, graceful shutdown, fail-closed lost lease/release, 14 mock/offline unit tests и focused GitHub workflow. Официальные CI/PG17/Win/Linux PASS пока НЕ ПОДТВЕРЖДЕНЫ. SP-15D будет закрывать реальный provider billing reconciliation/readiness, SP-16 — release gate.
