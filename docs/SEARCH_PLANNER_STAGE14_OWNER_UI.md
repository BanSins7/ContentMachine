# Search Planner · SP-14 — полный интерфейс владельца

**Дата:** 2026-10-26. **Ветка:** `feature/search-planner-full-integration`. **Статус:** код IMPLEMENTED [x]; CI/real browser/PG17 QA отдельно OPEN. Не сливать в `main` без запроса владельца. Никаких изменений в ветке Video Collector.

## 1. Архитектура и навигация

- `site/index.html`: полноценный проектный `spView` с шестью экранами: (1) C9, (2) AI Query, (3) Query Validation, (4) Held Task Planner, (5) SP Inbox/Collector, (6) История. Семантические формы, `aria-live`, read-only status/alerts, отдельные предупреждения о деньгах и внешнем intake.
- `site/projects.js`: кнопка «Search Planner · Поисковые запросы» в карточке существующего проекта; никакой новой сущности проекта или fork UI. Событие `cm-search-planner-return` возвращает на ту же карточку, `cm-sp-leave` очищает session-only секрет.
- `site/search-planner.css`: адаптивный dark UI со стилем Content Machine, mobile раскладка, focus-visible, reduced-motion, семантика форм.
- `site/search-planner-core.js`: отдельно тестируемые строгие JS-контракты C9 policy, UUID, USD, JSON, idempotency keys, Stage-D generation config, owner-bound HTTP client. Токен **только в замыкании оперативной памяти текущей вкладки**, не в `localStorage`, `sessionStorage`, cookie, JSON body, URL, логах, bundle и экспортируемом JSON. На выходе из UI/переходе в другой раздел/перезагрузке страницы вызывается `forget()`.
- `site/search-planner.js`: запросы только к существующему проектному SP-13 HTTP API; защищённая обработка `textContent`, `createElement`, `replaceChildren`, без `innerHTML` с ответами серверов. Результаты до 100KB показаны на экране, полная JSON версия доступна отдельным ручным экспортом. Отмена текущих GET при смене проекта/страницы через AbortController; повторной платной операции этот механизм не создаёт.
- `app.py`: добавлены только **три** статических пути в разрешённый whitelist (`/search-planner.css`, `/search-planner-core.js`, `/search-planner.js`). Существующий SP-13 lazy HTTP mount и прочие разделы остаются самостоятельными.

## 2. Настоящий источник состояния и корректные связи

Добавлен **только read-only** `intelligence/search_planner/ui_overview.py`, открытый через `SearchPlannerService.owner_overview()` и `GET /api/projects/{pid}/search-planner/overview?limit=30`. Он читает только существующие `sqg_config_versions`, `sqg_generations`+`sqg_outbox`, `qv_runs`+`qv_outbox`, `sp_handoff_events`, в одной проектно-ограниченной REPEATABLE READ READ ONLY транзакции, limit 1–50. Нет отдельного реестра, синтетических «успешных» статусов, источников по умолчанию, paid/provider операций.

`GET /models` через `read_sp_pinned_model_catalog()` также только читает существующий кеш `tb_model_snapshots` / `tb_catalog_refreshes` посредством `list_models` и настоящие fingerprint-пины из `ai_query/prompts.py`. Не выполняется OpenRouter refresh, модель не скачивается, оплата не авторизуется. Кнопка «Собрать конфигурацию» доступна только для `selectable=True`, `status='usable'` (актуальный каталог, verified structured outputs), и создаёт строго полный `search-generation-config/v1`:

- `search_policy` только из явно выбранных языков/рынков/modes/лимитов, без вывода из местоположения;
- model `openrouter`, exact `catalog_snapshot_id`, `model_catalog_sha256`, текущие `prompt_version`, `prompt_sha256`, `raw_schema_version`, `raw_schema_sha256`, `strategy_version`, `max_output_tokens`;
- execution с `max_concurrent_provider_calls=1`, `automatic_paid_retries=0`, `samples_per_batch=1` и ограниченными пакетами.

Операция «Собрать» — **локальный черновик**, только отдельный POST `/generation/configs` утверждает версию. Если в PostgreSQL нет подтверждённых моделей, интерфейс показывает пустой список, **не предлагает вымышленный конфиг**.

Поля `spConfigId`, `spGenerationId`, `spQuoteId`, `spSourceOutbox`, `spValidationId`, `spInboxId` заполняются только настоящими ID ответа API или выбираются пользователем из уже существующей истории. Изменение C9/policy/generation сбрасывает устаревшие финансовые и downstream ID/checkbox. Не выбираем самовольно «новейшую» публикацию.

## 3. Действия по шести экранам

1. **C9**: только утверждённые Deep C9 (проверки Human Review 1+2), полная проектная search policy, `/contexts/preview`, без LLM/денег.
2. **AI Query**: выбор реальной модели/снимка, проверяемая полная конфигурация (доступен экспертный JSON), идемпотентный plan, GET generation/candidates/execution/budget/preflight, POST quote/consent/revoke/recover/cancel; просмотр неотправленных резервов и строго серверный release `unsubmitted`. Отдельное одобрение лимита USD, затем **ещё одно независимое подтверждение** одного потенциально платного batch. `CM_SP_PAID_HTTP_ENABLED=1` и PostgreSQL финансовый ledger по-прежнему обязательны; UI их не обходит.
3. **QV**: только локальная server-pinned multilingual embedding-модель, явная QV V1 policy, validated bundle/coverage и все связи с гипотезами/исходными QV решениями. Нет test_fixture fallback.
4. **Task Planner**: read-only capability registry, preview matching одной поисковой строки, изменяемые лимиты, просмотр полного held-only плана и отдельное явное подтверждение immutable HELD. HOLD **не** является запуском Collector или provider.
5. **SP Inbox**: неизменённые SP11 50-packs, SP12 атомарный QV→SP ingest/ACK, explicit bounded drain до 10 QV, project-scoped status; отдельное согласие на **один** Collector intake. Нет Collector? Показывает `awaiting_collector`. Потерянный receipt? `needs_attention` и ручной read-only receipt reconciliation, **без повторного `accept`**.
6. **История**: project-scoped read-only конфигурации, SQG, QV и inbox с реальными датами, UUID и статусами, выбор существующих записей для продолжения. Не копируется содержимое QV в локальное хранилище.

Отдельные ключи `spPlanKey`/`spPaidKey`/`spHeldKey` остаются в полях до закрытия страницы, чтобы обычный повтор не сгенерировал новый ключ автоматически. Создание **нового** ключа платной операции требует специальной кнопки с предупреждением об ещё неопределённом предыдущем счёте; после перезагрузки оператор должен сверить SQL ledger, а не предполагать нулевые расходы. Статусы `needs_attention`, `uncertain`, `incompatible`, `awaiting_collector`, `no_work`, `paused_budget` отображаются честно: больше нет ложного «операция завершена» как эквивалента принятия Collector или оплаты.

## 4. Test matrix

- `tests/test_search_planner_ui.mjs` — 12 встроенных Node `node:test` тестов: разметка шести экранов, статические пути в локальном HTTP whitelist, DOM ID, отсутствие браузерного постоянного хранилища owner secret, policy, USD/UUID validation, реальный GET без owner header, owner POST с секретом только в header, запрет forged route/HTTP contract, отдельные idempotency keys, модельный Stage D config по SHA.
- `tests/test_search_planner_stage14_overview.py` — Python unit с SQL doubles: проектные read-only запросы к четырём существующим SQL слоям, REPEATABLE READ и лимит, недействительный проект/limit, verifiable model catalog/prompt pin, zero provider requests.
- `.github/workflows/sp-stage01-fast.yml`: setup-node 22, Python compileall, focused SP01–SP14 suites, `node --test tests/test_search_planner_ui.mjs`.
- В доступном исполнении JavaScript исходники `site/projects.js`, `site/search-planner.js`, `site/search-planner-core.js` успешно разобраны JS-движком; **12/12 UI тестовых тел успешно исполнены в V8 test shim** с точным содержимым файлов GitHub (не отдельный Node runner). Проверены 55 уникальных DOM ID, полное соответствие всех UI actions handlers, корректность безсекретного GET и запрет неавторизованного POST.

**Незакрытые QA:** официальный GitHub Actions Node 22/Python тест-прогон на точном HEAD (runner QUEUED), PostgreSQL17 свежая БД и миграции 054–056, настоящий браузер Chromium/Firefox/Windows/Linux и responsive/a11y clickthrough, обработка потерянного HTTP ответа после реального paid request и внешнее фактическое Collector durable receipt. Они относятся к SP15/SP16 production release gates. **Code complete ≠ CI PASS.**

## 5. Следующий этап

**SP-15 — Operations, supervision, health/heartbeat, financial uncertain-attempt and Collector receipt reconciliation, metrics, Windows/Linux runbooks and security.** Не трогать `main` и независимую ветку Video Collector. Не считать имитацию Collector ACK фактическим источником данных. Не создавать background worker без явного процесса/конфигурации и защищённого owner action.
