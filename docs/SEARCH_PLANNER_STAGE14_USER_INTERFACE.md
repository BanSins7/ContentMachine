# ContentMachine — SP-14: Search Planner UI

Дата: 2026-10-26
Ветка: feature/search-planner-full-integration
Реализация: CODE COMPLETED [x].
Официальный CI / PostgreSQL17 / браузер E2E: NOT VERIFIED. PR #34 DRAFT. Main и Collector не изменялись.

## Назначение

Из карточки проекта кнопка «Search Planner · Поисковые запросы» открывает полноценный локальный SPA-раздел Content Machine, не отдельное приложение. Модуль безопасен без Collector. Доступен режим чтения без секретов; все POST защищены настоящей серверной локальной owner-авторизацией из SP-13. Установка OpenRouter каталога, QV offline multilingual модели и реальной Collector совместимости не подменяется примерами.

## Шесть этапов

1. C9 — выбор одобренной Deep C9 публикации из БД с двумя Human Reviews, просмотр версии/UUID/числа гипотез, явные языки, рынки, режимы keyword/hashtag и ограничения до 480. Отдельный бесплатный Context Preview.
2. AI Query — только реально verified/fresh OpenRouter models с версией snapshot и Stage D pins, точная local-конфигурация search-generation-config/v1, отдельная регистрация и создание поколения; quote, budget, consent, generation status, candidates, attempts, preflight, cancel, recover, revoke, unsubmitted reserve release. Operator-paid flag по умолчанию выключен, платный запуск только один batch с отдельным человеческим согласием.
3. QV — production local embedding, explicit SQG Outbox UUID, policy JSON, selected/rejected/coverage и provenance, human-readable отбор по гипотезам. Relevance/MMR прямо названы embedding proxy, а не retrieval precision.
4. Tasks — source capability catalog declared/ready/authorized, safe match preview, HELD tasks preview с source/cost/coverage из сервера, отдельное подтверждение immutable HOLD без запуска Collector.
5. Inbox — deterministic 50-chunk preview, atomic QV→SP durable inbox, проверка статуса и отдельных receipts. Без Collector = awaiting_collector. Передача только одного пакета по отдельному согласию; reconcile выполняет receipt readback, никогда не повторный blind accept.
6. История — только реальные scoped PostgreSQL config/generation/QV/inbox IDs, плюс idempotency keys и completed/uncertain/in_progress состояния из sp_http_paid_actions. После обновления страницы можно восстановить тот же ключ запроса, не создавать новый платный batch.

## Файлы и интеграция

- site/index.html: секция spView, 6 панелей, явные financial/HELD/delivery consent, idempotency keys и модельный picker.
- site/search-planner-core.js: testable API transport, project/UUID validation, client-only draft config, строгий policy parser.
- site/search-planner.js: DOM controller, results/coverage/history, кнопки, cancellation, server status, full JSON export.
- site/search-planner.css: адаптивный desktop/tablet/mobile dark UI, focus-visible, aria-live и prefers-reduced-motion.
- site/projects.js: вход/выход из карточки проекта; site/app.js: обнуление полномочий при переходе к Downloader/Discovery/Settings.
- app.py: только whitelist статических ресурсов SP, существующая маршрутная архитектура не сломана.
- intelligence/search_planner/ui_overview.py: zero-spend SQL owner overview и verified model catalog; service.py и http_api.py: readonly overview/models GET.
- intelligence/search_planner/api_contracts.py: read-only capability exports.
- tests/test_search_planner_ui.mjs: 15 контракто-UI сценариев.
- tests/test_search_planner_ui_dom.mjs: полный mocked 6-stage click workflow + запрет unauthenticated POST (2 сценария).
- tests/test_search_planner_stage14_overview.py: Python unittest с PG SQL double.
- .github/workflows/sp-stage01-fast.yml: Node 22 и focused Python/JS suite.

## Безопасность и достоверность

Первое открытие SP выполняет только GET approved C9; никаких paid/source POST автоматически. Секрет владельца хранится только в замыкании JS текущей вкладки. Поле пароля очищается при unlock; on project/back/other app tab/beforeunload вызывается forget. Секрет не попадает в localStorage, sessionStorage, cookies, IndexedDB, URL, JSON body или экспорт.

Owner token предоставляется только в HTTP header X-CM-SP-Owner-Token; сервер независимо проверяет local loopback/Host/Origin/X-ContentMachine/секрет/project scope. Browser unlock не означает успешную серверную аутентификацию. Отдельные кнопки подтверждают quote→consent, один potentially paid batch, immutable HOLD и ровно один Collector intake. Старые idempotency keys не меняются самопроизвольно; создать новый paid key можно только после отдельного предупреждения.

Показ статусов:
- needs_attention / uncertain / blocked_config / incompatible / paused_budget / paused_price / failed → предупреждение, без automatic retry.
- awaiting_collector → SP сохранил событие, Collector receipt отсутствует.
- no_work → подтверждённый пустой отбор, без фиктивного QuerySet.
- Все внешние тексты и provenance выводятся только через textContent, не innerHTML. JSON preview ограничен по размеру, полный snapshot можно скачать локально без owner header/token.

## Проверка и release gates

Фактически выполнены адаптированные проверки исходников JavaScript: 15/15 basic V8 test-contract проверки, 2/2 UI mock DOM click-flow проверки. Это не штатный Node CI / browser E2E. GitHub Actions на exact HEAD остаётся в очереди, PostgreSQL17 fresh/upgrade и настоящий Windows/Linux local browser smoke не запускались; не указывать PASS.

Нужны SP-15 operations/supervision и SP-16 PG17 migrations 054–056, real HTTP owner + CSRF browser smoke, crash recovery, exact-HEAD CI PASS, collector-independent merge gate. C9 Deep V2, настоящий provisioned multilingual runtime и установленный Collector production adapter — отдельные внешние зависимости, их нельзя отметить выполненными. Main и чужие ветки не менять и PR #34 не мержить без разрешения владельца.
