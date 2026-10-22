> **Актуализация на 22.10.2026:** документ описывает проектные решения и/или состояние на дату создания, а **не полный перечень готовых функций `main`**. В `main` реализован только offline/manual Discovery D1; автоматический поиск внешних платформ D2+, KPI 10 000 новых видео в месяц и live Video Collector не подтверждены и не запущены. Исследовательский PR #11 уже merged, draft PR #10 ещё не merged. См. [CURRENT_STATE_2026-10-22.md](CURRENT_STATE_2026-10-22.md).

---

# ContentMachine — D0 MASTER SPEC / Discovery Engine
**Версия документа:** 1.0.1  
**Статус:** проектная спецификация (D0); реализация D1–D7 не утверждается автоматически  
**Дата сверки:** 18.10.2026  
**Базовый код при аудите:** ветка `main`, проверены `app.py`, `site/index.html`, `auto_update.py`, `intelligence/{contracts,jobs,health,migrate}.py`, `intelligence/migrations/001_foundation.sql` и существующие тесты.  
**Цель документа:** дать любому следующему исполнителю (в том числе ChatGPT в другом разговоре) однозначный, проверяемый порядок разработки. **Этот файл — источник истины для D0; не путать с уже работающим ПО.**

> **Правило перед выполнением:** сначала прочитать этот документ и текущий `main`, сравнить фактические схемы и код с проектом. Если они расходятся — создать предложение изменения спецификации; не молча импровизировать. Документ не гарантирует отсутствие будущих ошибок: риск закрывается тестами и согласованием неизвестных требований.

---

## 0. Глоссарий, статусы и границы

- **Candidate** — внешний или импортированный *предполагаемый* ролик; это ещё не валидная запись.
- **Video** — сущность с ключом (`platform`, `platform_video_id`). Не означает, что это Short или что у нас есть медиафайл.
- **Occurrence / discovery** — событие, где и каким запросом кандидат найден; одно видео может иметь много событий.
- **Snapshot** — измерение статистики у конкретного источника на конкретное время с собственными разрешениями.
- **Qualified new video** — уникальный ролик, прошедший критерии новизны и тематической релевантности; формула в `2`.
- **Source** — официальный API, коммерческий поставщик, авторизованные собственные данные или ручной импорт.
- **Capability** — операция, реально доступная данному источнику (поиск, поиск автора, метрики и т. п.).
- **Rights** — разрешения на чтение/хранение/производные метрики/анализ видео и сроки хранения; отсутствие явного разрешения = запрет.
- **Run** — пользовательский или плановый запуск поиска со своим ID, конфигурацией и отчётом.
- **Request attempt** — один фактический сетевой запрос/платная операция; сеть работает с семантикой **at-least-once или неопределённый результат**, не exactly-once.
- **D0** — проектирование, ревью, контракты, QA-план, выбор безопасных допущений. **Без новых исполняемых сборщиков и платных вызовов.**
- **D1** — работающая БД-модель + pipeline из фикстур/CSV/ручных URL; автоматический внешний поиск появится **в D2**.
- **D2** — первый реальный адаптер с API; **D3** — коммерческий адаптер по разрешению; **D4–D7** — диспетчеризация, мониторинг, UI и масштаб.

### Определение готовности D0
Документ описывает: (1) поведение и ограничения; (2) точные входы/выходы; (3) API; (4) БД; (5) ошибки/восстановление; (6) безопасность/права/стоимость; (7) карту реализации; (8) тесты и стоп-факторы. Он прочитан и одобрен пользователем. **D0 не подтверждает достижимость 10 000/мес реальными источниками.**

## 1. Проверенный исходный код и запрещённые побочные эффекты

| Уже есть в репозитории | На что опираемся | Чего там пока нет |
|---|---|---|
| `app.py` — `ThreadingHTTPServer`, локальный `127.0.0.1:8765` | существующие Host/Origin и `X-ContentMachine: 1` для POST | нет полноценного `/api/intelligence/search-runs` и read API |
| `app.py` — `/api/find`, `/api/discover` и in-memory `discover_jobs` | эти эндпоинты обслуживают **загрузчики**, не новый Discovery Engine | нет персистентных поисковых запусков |
| `site/index.html`, `site/app.js` | текущие Instagram/TikTok/YouTube Downloaders | отдельной навигации Discovery нет |
| `intelligence/migrations/001_foundation.sql` | `sources,topics,creators,videos,search_queries,discoveries,metric_snapshots,jobs`, дальнейшие таблицы | нет run/request/quotas/cursor/topic-relations и source-field provenance |
| `intelligence/contracts.py` | `SourceRights`, `VideoCandidate`, `MetricObservation`, `SourceAdapter` | это минимальные наброски, ещё не полный контракт |
| `intelligence/jobs.py` | enqueue, lease через `FOR UPDATE SKIP LOCKED`, complete/fail/retry | нет lease-generation fencing, планировщика и жизненного цикла run |
| `intelligence/migrate.py` | транзакционные версионированные миграции | нельзя править применённую 001 |
| `auto_update.py` + `RUN_AUTO.bat` | GitHub-проверка каждую минуту, контролируемый перезапуск веб-сервера | нет supervision отдельного Discovery worker и блокировки обновления по его заданиям |
| `tests/test_intelligence_infrastructure.py` | CI PostgreSQL integration | нет нагрузочного, идемпотентного и source-contract QA |

**Не трогать в D0:** код, миграции, конфиги, Docker и загрузчики.  
**Не ломать в D1:** существующий роутинг `/api/find`, `/api/discover`, `/api/download`, in-memory downloader jobs, архив скачанных файлов, OpenRouter-настройки, порт 8765, пути личных файлов.  
**Не путать:** источник данных для Discovery ≠ существующий `yt-dlp`-загрузчик видео. Повторное использование парсеров URL возможно только после выделения чистых функций и тестов.

## 2. KPI, который нельзя подменять количеством полученных строк

### 2.1 Целевой показатель
**10 000 уникальных *подходящих новых* видео за календарный месяц**, с обязательной разбивкой по платформе, теме, источнику и разрешённой пригодности данных. Результаты реальных запросов не гарантируются архитектурой.

Чтобы не смешивать две разные трактовки «новое», в UI должны быть **оба** числа:

1. **Newly discovered this month** — `videos.first_seen_at` попадает в месяц UTC; ID появился у нас впервые, без требования к дате публикации.
2. **Fresh published AND discovered this month (основная цель)** — впервые попал в базу в отчётный календарный месяц **и** `published_at` попадает в этот месяц; статус `qualified`, прошёл правила ниши и источника, dedup ключ уникален глобально для проекта.

Основной целевой счётчик = второе определение. Другие числа (старые найденные, неизвестная дата, непроверенная релевантность, невалидные Shorts) показываются **отдельно**.

**Формальное условие:** для каждой уникальной пары (`platform`,`platform_video_id`) берём первую дату добавления и **актуальную разрешённую дату публикации из конкретной source-specific записи (`source_video_metadata.published_at`), не истёкшую и не удалённую**. Если обе принадлежат отчётному месяцу UTC, есть действующий provenance + rights, видео релевантно хотя бы одной выбранной нише и имеет подтверждённый тип согласно настройке, оно учитывается **ровно один раз** за этот месяц. Если Shorts статус не подтверждён, использовать отдельный счётчик `shorts_candidate`, не включать его в KPI «подтверждённые Shorts», если пользователь требует только Shorts.

**В D1 сначала строится тестируемый расчёт этих KPI; выбор целевых ниш и строгость Shorts — открытое решение `21`.**

### 2.2 Нагрузка и прогноз
- Для 30-дневного месяца 10 000/30 ≈ **333,34**, операционная цель **334/сутки**; для 31-дневного месяца ≈ **323/сутки**; 28 дней ≈ **358/сутки**. Планировщик считает динамический `remaining_target / remaining_days`, а не захардкоженное 333.
- Если уникально-релевантный yield = 40%, потребуется **25 000 кандидатов/мес**; 60% — **16 667**. Это предположения, не результаты теста.
- Модель хранения: 10 000 видео/мес ≈ 120 000 видео/год **до политики удаления**. При 3/5/10 снимках на видео в месяц = 30k/50k/100k потенциальных измерений, но реальные сроки хранения зависят от разрешений.
- **Оценки RAM, размера БД, стоимости провайдера, скорости API и покрытия ниши** не придумывать. Получить из бенчмарков D1 и D2/D3.
- Планировщик никогда не делает вывод «теперь хватит API X» по одному theoretical maximum (до 50 результатов на search page): учитываются повторы, релевантность, доступность источников и квоты.

### 2.3 KPI и SLO
`raw_count`, `valid_count`, `unique_count`, `qualified_count`, `qualified_fresh_count`, `duplicate_rate`, `missing_published_rate`, `relevant_precision_on_labeled_sample`, `quota_spent`, `paid_spend_usd`, `cost_per_qualified`, `error_rate`, `recovery_time`, `data_age_hours`.  
**Важное:** агрегировать статистику API между платформами либо вычислять рейтинги только если это допускают условия источника; простые эксплуатационные счётчики поступлений тоже проходят отдельный policy review для YouTube.

## 3. Архитектура процессов и физическое размещение

```text
Пользователь/локальный Dashboard
  ├── ниша, языки, платформа, seed-авторы, ключи, лимиты, бюджет
  ├── Preview/Dry-run → Start → Observe → Stop/Resume
  └── Views: sources / search_runs / videos / trace / diagnostics
                        │ HTTP (localhost only)
                        ▼
           API controller (тонкий app.py-роутер)
                        │ schema validation + ACL
                        ▼
             Discovery service layer
      SearchConfig → Search Planner → Source Gate
                        │ план / источники / квоты
                        ▼
                   PostgreSQL
      ├── runs, jobs, requests, quotas, policy, audit
      ├── videos, creators, topic relations, occurrences
      └── source metadata, metrics, quality, monitor
                        ▲
                 Background worker
   [Adapter] → [Validate] → [Normalize] → [Deduplicate]
                        │
                  [Persistence]
                        │
                 [Read-model API]
                        ▼
       Monitoring / downstream Analytics (ПОЗЖЕ)
```

**Принцип:** контроллер не содержит SQL или кода платформ; adapter не пишет в БД; сервис не скачивает MP4; нормализатор не вычисляет Viral Score; UI не знает паролей и внутренних DSN. Все операции со сторонними сервисами выполняются фоновыми заданиями.

### Способ запуска
- **D1:** безопасный offline importer и тестовый worker, **выключен по умолчанию**; нагрузка от fixture не требует сети.
- **D2+:** выделенный `python -m intelligence.discovery.worker` под supervision. Подготовить изменения в `auto_update.py` отдельно: запуск, shutdown, health, drain при обновлениях, запрет мгновенной остановки незавершённого paid call. Отказ worker не должен убивать downloader UI.
- PostgreSQL уже работает в Docker; новые Redis, Kafka, Kubernetes и облачный сервер на масштабе 10k/мес **не нужны**.
- 1 процесс-воркер и исходно 2 сетевых обращения параллельно; увеличить до 4 только после измерений rate limits и памяти.
- RTX 3090 не используется в ядре Discovery. Локальная LLM и embeddings — отдельный, выключенный до D5, необязательный источник query-expansion/label suggestions.

## 4. Карта жизненного цикла одного search run

1. **Draft:** UI собирает конфигурацию; сервер валидирует, запрещает неизвестные поля и платные источники без разрешения.
2. **Preview:** без сети возвращается детерминированный план: какие source capabilities доступны, число задач, worst-case quota/cost, причины блокировок. Preview **не резервирует** деньги/квоту.
3. **Commit:** POST с idempotency key; создаётся запись `search_runs` с snapshot конфигурации/версии алгоритма и нужные queued jobs в одной транзакции; повтор того же ключа возвращает тот же run_id.
4. **Acquire:** воркер берёт работу по lease-generation (см. `11`). Проверяет актуальные rights, доступность credentials, deadline, budget и cancel flag.
5. **Fetch:** вызывается конкретный adapter с ограничением времени, количеством страниц и разрешёнными параметрами; до платного вызова фиксируется request intent. Секреты не логируются.
6. **Decode/Validate:** каждый элемент получает `accepted / partial / rejected` с machine-readable reason; запрещённые по хранению поля отбрасываются ДО записи в БД.
7. **Normalize:** UTC, точность времени, стабильные ID, канонические URL, значения метрик; неполученные поля — NULL, не ноль.
8. **Persist:** идемпотентная транзакция с identity, allowed source metadata, occurrence, разрешёнными snapshots и audit. **Никакой частичной публикации как полноценного успеха.**
9. **Checkpoint:** страницу отмечаем завершённой только после commit всех результатов и сохранения следующего cursor; при retry обработка повторяется безопасно.
10. **Finalize:** считает counters, выделяет partial failures; run получает `completed` лишь при закрытии всех ожидаемых работ без блокировок. Иначе `partial/failed/blocked/cancelled` с кодом.
11. **Trace:** для каждого сохранённого ролика UI позволяет увидеть источник, запрос, run, страницу/ID request, дату и quality/rights.
12. **Scheduler:** отдельно решает, можно ли повторно измерять разрешённые метрики. Повторная проверка по известному ID не равна новому поиску.

**Инвариант:** внешнее API может вернуть успех, а DB commit упасть; нельзя говорить пользователю «успешно сохранено». Лог события и диагностический код обязательны.

## 5. Источники: интерфейс без зависимости от поставщика

### 5.1 Матрица возможностей (на уровне *конкретной регистрации источника*)
`keyword_search`, `hashtag_search`, `creator_listing`, `fetch_by_ids`, `metrics_snapshot`, `page_cursor`, `published_after`, `language_filter`, `region_filter`, `sort_by_date`, `sort_by_views`, `media_download` (в Discovery **false**) и `raw_response_storage` (по умолчанию false).

Источник хранит `provider`, `source_type`, версию adapter, способ авторизации (без секрета), способ расчёта квоты, текущий статус, список capabilities и **отдельные разрешения**. Capability не равно right. Например «технически можно прочесть views» не значит «можно хранить историю views».

### 5.2 Актуальные по документации ограничения
- **YouTube Data API**: по состоянию на 18.10.2026 в официальных документах указана **отдельная стандартная квота 100 вызовов `search.list` в день**, расход 1 search-unit за вызов; пагинация также расходует вызовы; другие методы — стандартный совокупный пул 10 000 units/день. Сбрасывается в полночь по Pacific Time. Лимит может быть повышен по одобренной процедуре. `search.list` отдаёт до 50 элементов на страницу. Это **не лимит 100 видео**, но и не обещание найти 5000 полезных видео в день.
- Для известного YouTube-канала предпочтительнее рассматривать playlist uploads и `playlistItems.list`, а подробности — через `videos.list`; точные лимиты, доступность и соответствие Shorts проверяются при D2.
- `videoDuration=short` означает **< 4 минут**, не подтверждает факт размещения в Shorts. Маркер `shorts_candidate`, а не выдуманное `confirmed`.
- **TikTok Display API** обслуживает публикации пользователей, явно предоставивших доступ; он не общий поиск по всем чужим аккаунтам. TikTok Research API — для одобренных исследовательских случаев; не брать как коммерческую основу без надлежащих прав.
- **Instagram Graph API**: возможности меняются по приложениям, аккаунтам, токенам и разрешениям; глобальный произвольный поиск всех Reels не предполагать.
- **Apify, Bright Data или иной коммерческий источник**: только кандидаты на пилот D3; не приобретать подписку, не активировать платный сбор и не обещать охват на D0. Внешние условия и право хранения проверять до разрешения writes.

Официальные источники, проверенные при составлении спецификации:
- https://developers.google.com/youtube/data/determine_quota_cost
- https://developers.google.com/youtube/v3/docs/search/list
- https://developers.google.com/youtube/v3/docs/videos/list
- https://developers.google.com/youtube/v3/docs/playlistItems/list
- https://developers.google.com/youtube/terms/developer-policies
- https://developers.google.com/youtube/terms/developer-policies-guide
- https://developers.google.com/youtube/terms/derived-metrics-policy
- https://developers.tiktok.com/docs/en/display-api-overview
- https://developers.tiktok.com/docs/en/about-research-api

### 5.3 Политика YouTube — отдельный стоп-фактор
YouTube ограничивает хранение неавторизованных API-данных (обычно не дольше 30 дней без обновления/удаления) и создание производных метрик. Специальные расширения для аналитики и срока хранения **не подразумеваются полученными**. До юридической и продуктовой проверки соответствующего сценария: **can_derive_scores=false**, retention ограничен разрешёнными условиями, соблюдается источник и правила отображения/удаления. Не строить кроссплатформенный Viral Score по данным YouTube API без подтверждения права. Сервис не скачивает чужие MP4 автоматически.

**Нельзя перепутать:** установка yt-dlp на локальный компьютер не создаёт для приложения прав от YouTube API.

### 5.4 Новый договор с адаптером
```python
class SourceAdapter(Protocol):
    provider_name: str
    adapter_version: str
    def capabilities(self) -> frozenset[str]: ...
    def validate_config(self, spec: "SearchTask") -> list["Issue"]: ...
    def fetch_page(self, task: "SearchTask", cursor: str | None) -> "FetchPage": ...
    def fetch_by_ids(self, platform_ids: list[str]) -> "FetchPage": ...
    # неподдерживаемое = explicit UnsupportedCapability; не пустой «успешный» список.
```

Контракт ответа `FetchPage`: `items`, `next_cursor`, `has_more`, `provider_request_id` (если есть), `fetched_at` UTC, `quota_units`, `estimated_cost_usd`, `actual_cost_usd` если сообщается провайдером, `provider_error_code`, `rate_limit_reset_at`, `partial`. Бизнес-логика находится вне adapter.

## 6. Search Planner: детерминированный алгоритм

### 6.1 Вход
```json
{
  "schema_version": 1,
  "topic_id": 1,
  "platforms": ["youtube"],
  "methods": ["keyword", "creator"],
  "queries": ["AI workflow", "automation tools"],
  "hashtags": [],
  "creator_seeds": [],
  "languages": ["en", "ru"],
  "region": null,
  "published_after": "2026-10-01T00:00:00Z",
  "published_before": "2026-11-01T00:00:00Z",
  "shorts_required": false,
  "max_candidates": 500,
  "max_requests": 20,
  "paid_budget_usd": "0.00",
  "allow_monitoring": false,
  "dry_run": true
}
```

Правила:
- Поля из allowlist; неизвестные ключи → 400. Длины queries <= 160, максимум 50 queries, максимум 1000 seed creators в одном run (точные границы могут быть снижены настройками). Unicode NFKC только для сравнения, **оригинальная строка хранится отдельно**; локаль/язык не угадывать.
- `published_after < published_before`, дата в ISO-8601 с TZ; окно не позднее разумного server now + допуск к дрейфу часов. У источника может не быть фильтра даты — показывать post-filter с предупреждением и более высокой ценой.
- `max_candidates` по умолчанию 500, hard cap 5000/run; `max_requests` <= source quota, локального rate limit и глобального budget.
- `paid_budget_usd` в десятичной строке; **по умолчанию ноль**. Никакой автопокупки/обхода биллинга.
- Источник с недоступным capability → `skipped_unsupported`; ненастроенный credential → `blocked_auth`; права хранения выключены → `blocked_rights`; quota исчерпана → `blocked_quota`.
- Фиксируется сортировка и исходный запрос; никакой тихой смены date на relevance.
- Если нет хотя бы одного действительного источника/метода, preview показывает `blocked`, а commit не создаёт фальшивый successful run.

### 6.2 Формирование заданий
1. Нормализовать и дедуплицировать **поисковые строки** (но сохранять оригинальные).
2. Создать подходящие пары `(source_id, method, query_or_creator, language, date_window, sort)` только из разрешённых capabilities.
3. Сформировать устойчивый `task_fingerprint = sha256(canonical_json(params+planner_version+source_id))`.
4. Установить верхние пределы: по вызовам, страницам, кандидату, источнику, run, дню.
5. Резервировать часть квоты на мониторинг лишь когда он реально включён; иначе 0.
6. Очередность: first manual seeds (если есть), watchlist, проверенные keyword, exploration (квоты выделяются отдельно); веса — **конфигурация**, не «AI решил».
7. Dry-run показывает estimated calls/points/$ при наличии тарифных данных, unknown иначе. После подтверждения создать jobs в транзакции.
8. Новые запросы из query expansion требуют указания `parent_query_id`, версии генератора, объяснения и ограничения глубины <= 1 в D1. LLM-генерацию пока не запускать.
9. Планируемое поведение при ошибке: отдельно fail/skip per task, run частично завершён только по правилу `10`.

### 6.3 Защита от ухода из ниши
Запросы наследуют topic_id и политику исключений; расширение не добавляет себя в постоянно работающий watchlist автоматически. Кандидат без явной связи с нишей = `relevance_unknown`. Набор контрольных обычных роликов можно собирать только в последующем Analytics и при наличии прав; не смешивать с qualified KPI.

## 7. Data Contract: точные поля

### 7.1 CandidateEnvelope v1 (межмодульный, не сырая копия ответа API)
```json
{
  "schema_version": 1,
  "source_id": "demo_allowed",
  "run_id": 102,
  "request_key": "102:demo_allowed:keyword:p001",
  "provider_record_id": "external-123",
  "method": "keyword",
  "query_id": 25,
  "rank": 7,
  "platform": "youtube",
  "platform_video_id": "abc123DEF45",
  "canonical_url": "https://www.youtube.com/watch?v=abc123DEF45",
  "platform_creator_id": "UCexampleID",
  "title": "Example",
  "description": null,
  "published_at": "2026-10-17T09:00:00Z",
  "duration_seconds": 51,
  "shorts_classification": "unknown",
  "metrics": {
    "observed_at": "2026-10-18T08:00:00Z",
    "views": 1000,
    "likes": 0,
    "comments": null,
    "shares": null,
    "saves": null,
    "precision": "exact"
  },
  "fetched_at": "2026-10-18T08:00:03Z"
}
```

Пример учебный; ID платформы должен проверяться адаптером по конкретному формату. `shorts_classification` = `confirmed/likely/unknown/rejected`, с дополнительным `classification_reason`; без достоверного основания нельзя задавать confirmed. `region`, `language`, `paid_status` могут быть unknown. Продолжительность точная только если источник отдал duration.

**Обязательные для identity:** platform, platform_video_id, допустимый canonical URL, source_id, request/run context. Отсутствие опубликованной даты **не должно уничтожать запись**, но лишает её статуса «fresh qualified». Nullable creator_id -> ролик сохраняется, автор появляется позже (enrichment). Отсутствие любых metrics -> **не создаём фиктивный snapshot**.

### 7.2 Валидация/качество
- platform строго `youtube|tiktok|instagram`; ID из источника должен соответствовать allowlist длины, допустимых символов и известной схеме; ID — строка (TikTok большой integer не пропускать через JS float).
- `canonical_url` строится из проверенного stable ID; только `https`, проверенный host; обрезать tracking, запрещать внутренние/локальные сетевые URL и неожиданно перенаправленные домены.
- `published_at` timezone-aware; значения из будущего > допустимого дрейфа часов — `invalid_publication_time`, отправить на повторную проверку; не переписывать автоматически `now()`.
- Счётчики integer >=0 или null; никакие NaN, decimal-string с округлением в 0, отрицательные числа. Указывать `precision`, не вычислять чужие показатели по умолчанию.
- `observed_at` — время измерения, не смешивать с `fetched_at` и `first_seen_at`. Когда source даёт только момент ответа, явно помечать `observed_at=fetched_at` и `observation_time_assumed=true`.
- `creator_platform_id` может отсутствовать; имя автора не стабильный идентификатор.
- Разные источники могут расходиться в title/metrics; сохранять source-specific записи; не выбирать максимальные views и не усреднять без политики.
- `quality_state` = `valid|partial|rejected`, `reason_codes[]`, `fields_present[]`; каждый rejected суммируется по причине, сырые содержимое и персональные сведения не сохранять без прав.
- `null` ≠ `0`; `shares=null` не влияет на знаменатели. У полей могут быть разные сроки удаления.

### 7.3 Дедупликация: ДВА разных уровня
1. **Identity dedup:** уникально (`platform`, `platform_video_id`) — уже обеспечено `videos` в 001. Различные платформы не склеиваются автоматически, даже если клип визуально одинаковый.
2. **Occurrence dedup:** повтор загрузки той же API-страницы в том же run не должен плодить `discoveries`. Сформировать `discovery_key = sha256(run_id+source_id+query_id+logical_page_key+platform+platform_video_id)`; индекс UNIQUE для новых ненулевых ключей. **При другом query / run** новый occurrence допустим, хотя video один.

**Старые `discoveries` без ключа/`run_id` остаются валидными; при миграции новые столбцы nullable.** Успешный retry с теми же input возвращает прежние identity/occurrence IDs.

## 8. Слой хранения: конкретная миграция 002 (ещё не писать в D0)

**Не модифицировать `001_foundation.sql`, не удалять данные, не менять типы старых ключей.** PostgreSQL миграция транзакционна через существующий `migrate.py` с checksum и advisory lock. Перед D1 на Windows создать локальный pg_dump для восстановления и проверить резервное восстановление на тестовой базе.

### 8.1 Новые таблицы (проекты колонок)
| Объект | Ключевые поля и ограничения | Назначение |
|---|---|---|
| `search_runs` | `id BIGINT IDENTITY PK`, `idempotency_key TEXT UNIQUE`, `config JSONB NOT NULL`, `config_version INT`, `planner_version TEXT`, `state TEXT CHECK(...)`, `cancel_requested BOOLEAN`, `created_at,started_at,finished_at TIMESTAMPTZ`, `error_code TEXT`; counts пересчитываются из запросов/записей или кешируются транзакционно | неизменяемая конфигурация и статус запуска |
| `source_requests` | `id BIGINT PK`, `run_id FK`, `source_id FK`, `job_id FK NULL`, `logical_request_key TEXT`, `attempt_no INT`, `query_id FK NULL`, `cursor_hash TEXT`, `state TEXT`, `http_status INT NULL`, `provider_request_id TEXT NULL`, `started_at,finished_at`, `quota_units INT`, `cost_usd NUMERIC(12,6)`, `charge_state TEXT`, `error_code TEXT`; UNIQUE(logical_request_key,attempt_no) | факт попытки, аудит затрат; **не хранить ключи/API-secret или необработанный URL с credential** |
| `source_video_metadata` | `video_id FK`, `source_id FK`, `fetched_at TIMESTAMPTZ`, `title, description, creator_name TEXT NULL`, `duration_seconds NUMERIC NULL`, `published_at TIMESTAMPTZ NULL`, `expires_at TIMESTAMPTZ NULL`, `rights_version INT`, `is_current BOOLEAN`, `deleted_at TIMESTAMPTZ NULL`, UNIQUE(video_id,source_id,fetched_at) | поля с независимым происхождением и TTL |
| `creator_topics` | (`creator_id`,`topic_id`) PK; `reason TEXT`, `relevance_state TEXT`, `labeled_at TIMESTAMPTZ` | автор в нескольких нишах |
| `video_topics` | (`video_id`,`topic_id`) PK; `relevance_state TEXT`, `reason TEXT`, `label_origin TEXT`, `labeled_at TIMESTAMPTZ` | корректное включение в KPI |
| `monitor_targets` | `id BIGINT PK`, `target_type TEXT`, `platform TEXT`, `video_id/creator_id FK NULL`, `source_id FK`, `priority INT`, `next_due_at TIMESTAMPTZ`, `last_checked_at TIMESTAMPTZ NULL`, `cadence_policy JSONB`, `enabled BOOLEAN`; CHECK exactly one target FK | повторные замеры без нового поиска |
| `source_quota_ledger` | (`source_id`,`bucket_name`,`window_start_at`) PK; `window_end_at TIMESTAMPTZ`, `limit_units BIGINT`, `reserved_units BIGINT`, `consumed_units BIGINT`, `updated_at`, CHECK >=0 | атомарный budget gate, отдельные buckets |
| `data_quality_flags` | `id PK`, `video_id FK`, `source_id FK NULL`, `run_id FK NULL`, `code TEXT`, `details JSONB`, `created_at`, `resolved_at NULL` | проблемы качества без засорения бизнес-полей |
| `human_labels` | `id PK`, `video_id FK`, `topic_id FK`, `label TEXT`, `reason TEXT`, `created_at`, `superseded_at NULL`; журнал изменений | человеческая разметка, gold set для QA |
| `source_policy_versions` | `id BIGINT PK`, `source_id FK`, `version INT`, `capabilities JSONB`, `permissions JSONB`, `retention_policy JSONB`, `reviewed_at`, `reference_urls JSONB`, UNIQUE(source_id,version) | версионирование разрешений / trace |

### 8.2 Изменения старых таблиц
- `discoveries`: `run_id BIGINT NULL REFERENCES search_runs(id)`, `source_request_id BIGINT NULL REFERENCES source_requests(id)`, `discovery_key TEXT NULL`, индекс UNIQUE по `discovery_key WHERE discovery_key IS NOT NULL`.
- `jobs`: `lease_generation BIGINT NOT NULL DEFAULT 0` + доп. необходимые поля при реализации fencing; проверить существующие поля и логику UPDATE.
- `sources`: добавить **`can_store_metadata BOOLEAN NOT NULL DEFAULT FALSE`** и **`can_search BOOLEAN NOT NULL DEFAULT FALSE`**; существующие `can_store_metrics/can_derive_scores/can_analyze_media` оставить с default false. Регистрация источника + активная версия `source_policy_versions` дают **пересечение** разрешений (оба уровня должны разрешать действие), иначе **DENY**. Для `manual` D1 пользователь явно активирует разрешённые поля при настройке импортера; существующие строки не получают новых прав автоматически.
- `metric_snapshots`: `source_policy_version_id BIGINT NULL` и `observation_time_assumed BOOLEAN NOT NULL DEFAULT FALSE`. Запись снапшота допускается только при праве can_store_metrics. UNIQUE по (`video_id`,`source_id`,`observed_at`) из 001 сохраняем; коллизии одинакового времени от разных реальных измерений требуют явного retry/конфликт-правила, не overwrite.
- `videos`: сохраняем старые столбцы, но **новые внешние `title`, `description`, `published_at` и `duration_seconds` не писать в общие поля `videos` без явно проверенного источника/TTL**. Identity и допустимый canonical URL — минимальный слой; UI и KPI читают **временные действующие поля из `source_video_metadata`**. Если метаданные от разных источников расходятся, показывать выбранный source_id и правило выбора, не сливать молча. Если у видео больше нет ни одного законного source record, удалять или обезличивать его в соответствии с policy, включая связи и резервные копии. Старые значения из 001 не считать автоматически разрешёнными.
- Добавить индексы: `source_requests(run_id,started_at)`, `discoveries(run_id,video_id)`, `video_topics(topic_id,relevance_state)`, `monitor_targets(next_due_at) WHERE enabled`, `source_video_metadata(expires_at)`, `jobs(state,run_after)` уже есть.

### 8.3 Внешние ключи и атомарность
Порядок транзакционной записи: source rights snapshot → creator identity (если есть) → video identity → source metadata (только если разрешено) → topic link (если определена) → occurrence → snapshot (только разрешённый) → quality flags → page checkpoint. При любом SQL error rollback всей страницы (или savepoint для элемента с явным счётчиком, если выбрана per-item isolation). **Д1 принять одну стратегию и покрыть тестом, а не чередовать неявно.**

### 8.4 Нельзя физически «неудаляемое навсегда»
Expiration worker должен чистить по источнику/типу данных/сроку; если данные из другого источника законно сохранены, не удалять их вместе без необходимости. Отзываемые разрешения обязаны блокировать новые запросы и повлечь соответствующую очистку. Не маскировать персональные данные под `audit_events.details` или необработанные JSON. Бэкапы тоже подпадают под retention/удаление: спроектировать срок хранения и восстановление с post-restore cleanup.

## 9. Фоновая очередь, конкуренция, идемпотентность и «дорогие неизвестные исходы»

### 9.1 Job types
- `discovery.plan_run`
- `discovery.fetch_page`
- `discovery.hydrate_ids`
- `discovery.import_batch`
- `discovery.monitor_creator`
- `discovery.monitor_metrics`
- `discovery.expire_data`
- `discovery.finalize_run`

Стадии поиска/импорта/мониторинга выполняются раздельно. Задания содержат ссылку на run/параметры, а **не полный массив чужих API-данных**. Предел payload <= 64 KB на уровне приложения.

### 9.2 Состояния
Run: `created → queued → running → completed | partial | blocked | failed | cancelled`. `cancel_requested` — флаг, не «выполнено». Конечное состояние назначает финализатор после сравнения состояния всех jobs/tasks, а не после первой успешной страницы.

Job из 001: `queued → running → completed/failed/cancelled`; при временном сбое `running → queued` с `run_after`. Ошибки: `invalid_config` (нет retry), `unsupported_capability` (skip), `auth_required` (block until config change), `rights_denied` (block), `rate_limited` (по Retry-After), `quota_exhausted` (после границы bucket), `provider_timeout` (retry по правилам), `provider_5xx` (retry bounded), `schema_invalid` (quarantine), `db_unavailable` (не подтверждать success).

### 9.3 Обычные вызовы
- Postgres `SELECT ... FOR UPDATE SKIP LOCKED`, аренда 120 сек только пример; рабочий SLA > lease требует продления heartbeat.
- При выдаче увеличивать `lease_generation`. Complete/fail/heartbeat требуют (`job_id`,`worker_id`,`lease_generation`) и актуальной аренды. Stale worker не имеет права завершить новую аренду.
- Pagination cursor хранить только после успешной записи страницы. На crash/retry ту же страницу может прийти снова; `UNIQUE video` и `discovery_key` обеспечивают безопасное повторение.
- Retry capped, exponential backoff с jitter + provider Retry-After. Нет бесконечных повторов или параллельных pageToken-цепочек, нарушающих порядок.
- Worker не может молча игнорировать disabled source; before fetch повторный gate.
- После выхода из процесса «running» с expired lease восстанавливается, а не навечно висит.

### 9.4 Платные запросы
**Exactly-once сетевого списания обеспечить нельзя** без поддержки idempotency key у поставщика. При timeout после отправки платного запроса неясно, выставлен ли счёт. Алгоритм: создать `source_requests` intent → reserve budget атомарно → отправить → записать external request ID, если есть → при неопределённом ответе установить `charge_state=unknown`, не выполнять автоматический повтор платной операции, пока провайдер не подтвердит идемпотентность или оператор не разрешит reconciliation. Соотнести чеки/отчёт провайдера с ledger. Два одновременных воркера не могут зарезервировать один и тот же остаток бюджета.

## 10. Мониторинг и data expiry — отдельный поток

Ролики, созданные сегодня, необязательно нуждаются в такой же частоте проверок, как старые. Целевая *стартовая гипотеза* для допустимых источников: день 0–3 раз в сутки, дни 4–14 раз в 3 дня, затем по спросу/политике; **реальный режим утверждать только после оценки квоты/стоимости и ограничений retention**. Нельзя планировать бесконечное наблюдение за всеми 120k/year по умолчанию.

Мониторинг по автору: загружаем **новые** записи только если source поддерживает creator_listing. Мониторинг ролика: получаем новый snapshot по ID; если source запрещает историю, не сохраняем. История snapshot не редактирует старый снимок и не меняет первоначальную дату обнаружения.

Контроль: snapshot count и задержка до data expiry; UI предупреждает, если источник потерял авторизацию или история сокращается по retention.

## 11. HTTP API V1 — контракт поведения

Новые маршруты регистрируются **отдельно** от старых downloader-обработчиков в `app.py` (лучше тонкий dispatch модуль). Существующий `authorized()` для POST/изменений, `Host`/`Origin` и заголовок `X-ContentMachine: 1` сохраняются. Запросы JSON ограничены по размеру и типам; никакие секреты не возвращаются. Документировать GET, POST, status codes:

| Метод/путь | Вход | 200/201/202 | Ошибки |
|---|---|---|---|
| GET `/api/intelligence/sources` | — | capabilities, configured, rights-safe summary | 503 DB down |
| POST `/api/intelligence/preview` | SearchConfig | supported/skipped tasks + costs/quotas без создания задач | 400 validation |
| POST `/api/intelligence/search-runs` | SearchConfig + `Idempotency-Key` | 202, run_id, queued/blocked state, config hash | 400/409/422/503 |
| GET `/api/intelligence/search-runs?cursor=...` | filters, page size <=100 | pagination + statuses/counts | 400 |
| GET `/api/intelligence/search-runs/{id}` | id | state + stage counters + bounded errors | 404 |
| POST `/api/intelligence/search-runs/{id}/cancel` | id + reason | 202 cancel_requested, eventual terminal state | 404/409 |
| GET `/api/intelligence/videos?topic_id=...&cursor=...` | filters, limit <=100 | cards with provenance and nullable stats | 400 |
| GET `/api/intelligence/videos/{id}/trace` | id | why-found, source/query/request/snapshot/quality | 404 |
| GET `/api/intelligence/diagnostics` | — | sanitized source health + job/quota counters | 503 |
| POST `/api/intelligence/manual-imports` | CSV upload **не отправлять произвольный путь сервера**, file/batch by safe local flow | 202 job_id; позже status через run | 400/413/415 |
| POST `/api/intelligence/human-labels` | video_id, topic_id, enum label, reason | 201 label_event_id | 400/403 |
| POST `/api/intelligence/monitor-targets` | target, source, cadence | 201/200; rights-gated | 403/422 |

Для D1 реально включить **только** источники manual/fixture, импорты, диагностическое API и read/trace. `preview` позволяет запуск без external calls. Недоступные route в интерфейсе — hidden/disabled с честной подписью, не фиктивный успех.

### Общий формат ошибок
```json
{
  "ok": false,
  "error": {
    "code": "source_not_configured",
    "message": "Источник не настроен",
    "retryable": false,
    "correlation_id": "req-123"
  }
}
```
Никаких stack traces, DSN, API-key, raw third-party response в браузере. Статус 5xx означает ошибку сервера, а не «пустой поиск». D1 должен отделять HTTP ошибки на API level и item-level reject в результатах run.

## 12. UI: минимальный, но не black box

**Навигация:** новый независимый пункт `Discovery` рядом с существующими downloader-вкладками. Существующие экраны полностью сохраняются. В D1 нет видимости «активный массовый поиск», пока внешние source adapters не готовы.

Экраны/панели:
1. **New run:** тема; platform; keywords/hashtags; authors; язык; дата; Max candidates; лимит запросов; бюджет; preview; start. Запрещённые источники серые, причина понятна.
2. **Runs:** run_id, статус, прогресс по стадиям, queued/running/partial/failed, source-specific error, elapsed, quota, cost.
3. **Video catalog:** таблица с platform, автором, title из разрешённого source, URL, дата публикации, обнаружения, recent metrics (или «нет данных»), source, topic/relevance, Shorts label; фильтры и пагинация.
4. **Video trace:** «Почему найдено?» с source, query, cursor/page, request id, timestamps, правами и причиной quality flags.
5. **Diagnostics:** DB, worker, sources, config status, quotas, месячный план 10k, estimate cost, policy restrictions.
6. **Import:** безопасный импорт CSV/URL и журнал ошибок построчно. «Загружено» ≠ «зачтено в KPI».

Полоса прогресса: processed_pages / planned_pages, если planned неизвестно — **индикатор неопределённости**, не фальшивые 100%. Состояние может быть partial/blocked, и UI обязан показывать сколько данных реально сохранено.

Не сохранять токены в frontend localStorage. Ссылка на чужой сайт — HTTPS с безопасным target. JavaScript/HTML render через textContent/escaping, не доверять titles провайдеров.

## 13. Структура будущих исходников (создавать по D1–D7)

```text
intelligence/
  discovery/
    __init__.py
    domain.py            # pure dataclasses/enums/schemas
    config_schema.py     # validate JSON inputs
    planner.py           # pure SearchTask planning
    capabilities.py      # allowed methods and policy-gate
    normalize.py         # platform-specific ID, date, metrics
    validate.py          # item checks + reason codes
    dedupe.py            # stable keys; never depends on Python hash()
    persistence.py       # transaction-bound SQL repositories
    importers.py         # CSV / URL offline parser
    service.py           # run lifecycle and orchestration
    worker.py            # later background worker lifecycle
    monitoring.py        # later scheduling snapshots
    kpi.py               # SQL/report definitions, policy safe
    diagnostics.py       # trace + read models
    adapters/
      base.py
      manual.py          # D1
      fixture.py         # tests, explicitly non-live
      youtube.py         # D2 only
      commercial.py      # D3 only, concrete provider class
  migrations/
    002_discovery.sql     # D1 transactional additive migration
tests/
  discovery/
    test_contracts.py
    test_normalize.py
    test_dedupe.py
    test_plan.py
    test_import_csv.py
    test_rights.py
    test_kpi.py
    test_api.py
    test_pg_integration.py
    test_job_recovery.py
    fixtures/
site/
  index.html             # add Discovery tab, preserve Downloaders
  app.js                 # module UI or isolated new JS
  style.css
docs/
  DISCOVERY_ENGINE_D0_MASTER_SPEC.md  # this contract
  DISCOVERY_QA_REPORT.md             # generated after each delivery
```

По мере реализации можно разделить тестовые/производственные файлы иначе **только с обновлением этой схемы и объяснением в PR/коммите**. На D1 не начинать сразу с реализации полного multi-provider abstraction поверх заглушек, если достаточно domain+manual adapter, но интерфейс должен соответствовать контракту.

## 14. Обработка сбоев, предсказуемость и эксплуатация

| Сценарий | Что должно произойти |
|---|---|
| Нет PostgreSQL/Docker | GET health помечает unavailable; downloader продолжает работать; нельзя обещать successful import |
| Исчез интернет / таймаут бесплатного API | bounded retry; request/correlation id; stage частично завершён |
| Paid call: timeout после отправки | charge_state unknown; **никакого слепого повторного списания** |
| 401/403 | blocked_auth/rights; не делать 50 повторов |
| 429 | Retry-After / next quota window; не меняем прокси для обхода |
| Provider изменил JSON schema | parse error, quarantine счетчиками, source degraded; не подменять поля |
| У видео больше нет доступа | помечать недоступность; соблюдать retention/delete; не говорить «просмотры 0» |
| Повторной страницей пришёл тот же ID | одна video identity; occurrence dedup по logical key |
| Два worker одновременно обрабатывают ID | PostgreSQL UNIQUE+ON CONFLICT; source metrics отдельно; race test |
| Worker умер между HTTP и COMMIT | page retry возможен, но итоговые записи идемпотентны |
| Worker умер после COMMIT до ACK | та же страница безопасно импортируется повторно |
| Продлили аренду после передачи другой задаче | old lease_generation не может ACK |
| Нажали «Отмена» в UI | flag cancel_requested; прекращение новых вызовов, мягкий stop ongoing request |
| Получили заголовок с XSS или CSV formulas | HTML escaped; при CSV export префиксовать формулы; никогда не исполнять |
| Сменили права на источник | запрет новых работ, enforcement TTL/clean-up |
| Windows AutoUpdate делает Git Pull | не прерывать активный worker необратимым образом; drain+checkpoint |
| Внешний API дал 0 результатов | completed_empty, **не равно failed**, причина и request metadata видны |

Секреты: хранить локально вне Git, не в SQL audit/log; документация не содержит персональных ключей; POST импорт ограничен размером/типом, запрещены произвольные пути и server-side fetching произвольного URL (SSRF). Ограничение на localhost и Origin сохраняем, HTTP на внешних интерфейсах не публикуем.

## 15. QA D0: документация принимается по матрице, а не «выглядит готовой»

| ID | Требование | Где описано | Проверка при D0 | Test на D1/D2 |
|---|---|---|---|---|
| Q01 | 10k/мес = свежие уникальные, а не сырые строки | `2` | KPI formula и edge cases ревью | test_kpi, PG integration |
| Q02 | Zero != null | `7` | поле nullable, source identity | test_normalize |
| Q03 | Дубликаты из разных страниц/источников | `7/8` | два уровня ключей | test_dedupe + PG race |
| Q04 | Нельзя хранить неразрешённые metadata/stats | `5/8` | default deny и expiry | test_rights |
| Q05 | Неизвестная дата/Shorts не засчитываются ошибочно | `2/7` | review формулы | test_kpi |
| Q06 | Pause/Resume/Cancellation | `4/9/14` | state machine ревью | test_job_recovery |
| Q07 | Нет двойных платных списаний при timeout | `9` | unknown charge scenario | provider fake |
| Q08 | Лимиты per-source/per-method/window | `6/8` | бюджетные buckets | test_plan, PG ledger race |
| Q09 | Не открываем локальный сервер вовне | `1/11/14` | Host/Origin contract | test_api |
| Q10 | Downloader не ломается | `1/12` | integration boundaries | существующие tests + browser smoke |
| Q11 | Данные имеют provenance и trace | `4/7/8/11` | example request→video | test_pg_integration |
| Q12 | KPI считают лишь permitted data | `2/5/8` | source rights матрица | test_kpi + retention |
| Q13 | SQL миграция аддитивна и повторяемая | `8` | FK/index план ревью | integration 2x + checksum |
| Q14 | Worker/AutoUpdate не теряют задания | `3/9/14` | shutdown sequence | Windows live smoke |
| Q15 | Секреты/PII не попадают в API/журнал | `8/11/14` | threat model | test_api sanitization |

### Обязательные фикстуры D1
- 10 000 валидных платформенных ID, детерминированный seed генератора;
- идентичный import дважды;
- тот же video-ID от двух источников и в 3 разных query (identity одна, occurrence несколько);
- тот же ID в двух платформах (два видео);
- invalid URL, invalid ID, negative count, huge integer, int-vs-string TikTok ID;
- `views=0`, `views=null`, отсутствие metrics, конфликт stats разных source;
- UTC+02 offset в `published_at`, leap day, границы календарного месяца UTC, конец месяца;
- неизвестная дата публикации, метка релевантности unknown, неподтверждённые Shorts;
- restricted rights на title, metrics, analytics; expiry/revocation;
- транзакционная ошибка на 7-й странице;
- simultaneous upsert двух процессов в одну базу;
- stale worker ACK после lease rollover;
- failed provider/HTTP 429/401/500 (D2);
- paid unknown-state at timeout (D3).

### QA-команды (после реализации D1; сейчас файлов ещё нет)
```powershell
cd C:\ContentMachine
python -m intelligence.migrate
python -m unittest discover -s tests -v
python -m intelligence.health
# дополнительно: специализированный deterministic test generator / fixture load
# только в отдельной TEST PostgreSQL базе, НЕ засорять пользовательскую.
```

**Приёмка:** тестовый D1 прогон 10k данных в отдельной БД, потом 10k повторно: число уникальных identity остаётся 10k; source-specific provenance сохраняется; исторические загрузчики проходят smoke; GitHub Actions green **и** локальный Windows smoke. Фикстуры не должны считаться реальными 10k monthly KPI.

## 16. Декомпозиция D1: последовательные коммиты, тесты после каждого

**D1.0 — Baseline safety**
- Проверить `main`, состояние CI, подключение Postgres, число реальных строк в таблицах; прочитать текущие схемы и уже существующие тесты.
- Задокументировать команду pg_dump и репетицию восстановления в тестовую БД; проверить, что секреты не попадают в Git и логи.
- Подтвердить пользовательский локальный checkout: `C:\ContentMachine` (исторически установлен, но перепроверить, не предполагать навечно).

**D1.1 — Migration 002**
- Создать 002 additive schema, отдельные source metadata и permission versions, связь requests/runs, индексы, корректные nullable исторические данные.
- Проверить apply twice, checksum, FK, уникальность, старые таблицы и новую очистку в тестовой БД. Не править 001.

**D1.2 — Typed domain + validator**
- Расширить `contracts.py` или перенести в `discovery/domain.py` с обратной совместимостью; строгие enums, UTC, null, URL/ID, точность счетчиков.
- Тесты корректных и некорректных данных, некорректных timestamp и ID.

**D1.3 — Normalizer + dedup + repo**
- Сервис сохранения одной страницы в транзакции, upsert identity, field provenance, source rights, occurrence key и допустимого snapshot.
- Конкурентный PostgreSQL integration на 10k fixtures, второй import, тест rollbacks.

**D1.4 — Offline importer и fixtures**
- Ручные canonical URLs/CSV; импорт идёт через тот же pipeline и run journal, статусные counts, bounded error list.
- Нет сети, API keys и paid calls. Пример контрольного набора со стопроцентно синтетическими ID.

**D1.5 — Service / Read API**
- Локальные маршруты для импорта, списка, trace, диагностики. HTTP 4xx/5xx, pagination и защита UI.
- Не переименовывать старый `/api/discover`.

**D1.6 — Минимальный UI**
- Отдельная вкладка Discovery с импортом, результатом, «почему найдено», прогрессом и честной надписью «реальный внешний поиск ещё не подключён».
- Проверка на Windows и визуальный smoke.

**D1.7 — QA & release**
- CI (Python unit, PostgreSQL integration, JS syntax), 10k fixtures, повтор, данные не в production, stop/restart, безопасность API; GitHub commit hash + run URL + список непройденных ограничений.
- После успешной приёмки переходить к D2 только с разрешённым API/credentials.

## 17. После D1: точки расширения, НЕ реализуемые автоматически

- **D2 YouTube Collector:** source adapter с официальной документацией; keyword, creator-listing, page cursors, videos stats. Live smoke при собственном API key, отдельном policy review, явной атрибуции и правилах хранения. Не считать тестовый mock реальным поиском.
- **D3 Commercial Source:** только после пробных 200–300 кандидатов на источник и правовой/бюджетной проверки; первый провайдер выбирается по стоимости **полезного уникального видео**, не по рекламе охвата.
- **D4 Planner/Queue:** расписание, batch, quotas, fencing, cancel, per-source fallback, отдельный supervisor.
- **D5 Monitoring:** история снапшотов, creator watchlist, TTL.
- **D6 UI:** полнофункциональный диспетчер поиска и диагностика.
- **D7 Load/Live QA:** на test БД нагрузка 10k + 7-дневный живой пилот при доступных источниках. 10k/месяц подтверждается **только после полного месяца**, а 7 дней дают прогноз, не факт.

## 18. Стоп-условия и решения, которые нельзя выдумать

| Решение | Пока принято по умолчанию | Что согласовать |
|---|---|---|
| Формат «новый ролик» | свежий опубликованный и впервые найденный в одном месяце UTC | пользователь может выбрать rolling 30d |
| Длительность Shorts | `unknown` без явного доказательства | строго Shorts или все короткие ролики |
| Приоритет платформ | source-neutral, никакой квоты 60/20/20 | какая платформа приоритетнее |
| Ниша, язык, регион | не задано в архитектуре | какие темы на пилот |
| Платный лимит | **$0 / month** и paid adapters disabled | месячный бюджет |
| API ключи | не хранятся в Git и docs | локальная настройка на ПК |
| Derived YouTube analytics | **выключено** по умолчанию | явное разрешение / compliance |
| Provider choice | не выбран | D3 pilot, не раньше |
| Storage rights | deny by default | source policy version & allowed fields |
| Ручные метки релевантности | primary quality для gold QA; unknown не qualified | критерии релевантности каждой ниши |
| Сколько rechecks | мониторинг disabled в D1 | бюджет и source limits D5 |

Если открытое решение влияет на безопасность, правовой режим или стоимость, выбирать **DENY / disabled / $0**. Если влияет только на UI, можно использовать явно помеченный безопасный default.

## 19. Риски и действия против них

| Риск | Защита |
|---|---|
| 10k релевантных в нише физически мало | D2/D3 yield pilot; цель не обещать до измерений |
| Продавец данных отдаёт старые или повторные ссылки | KPI fresh filter + per-source quality |
| Правила YouTube запрещают желаемый Viral Score | отдельный Analytics gate; не включать производные метрики в Discovery |
| Разные лицензии/сроки хранения | source-specific metadata + TTL + revision |
| 10k фикстур проходят, а live API нет | раздельный CI и live QA |
| Конкурентные дубликаты | UNIQUE identity / occurrence keys + transactional upsert |
| Автообновление прервало работу | отдельный supervisor/drain + durable lease |
| Двойная стоимость paid API | unknown-charge reconciliation и hard budget cap |
| Слишком большой монолит `app.py` | thin routing + разделение модулей |
| Тайный «AI magic» | Planner deterministic, объяснение каждой задачи и результата |

## 20. Операционная инструкция следующему исполнителю (обязательная)

1. Прочитать **полностью** этот файл, `001_foundation.sql`, `contracts.py`, `jobs.py`, `auto_update.py`, существующие тесты; получить актуальный SHA `main`. Не работать по старой памяти без сверки.
2. Кратко сообщить пользователю **какой D1.N этап** сейчас делается, что именно изменится и какие риски.
3. Выявить противоречия между этим документом и текущим кодом; при необходимости до кода поправить спецификацию и обосновать решение.
4. Реализовать **только** согласованный этап, без скрытых интеграций, расходов, GPU, скачивания видео, изменения чужих downloader API.
5. Тестировать на mocks/fixtures и PostgreSQL отдельно; unit + integration + негативные сценарии; проверять CI по конкретному commit SHA.
6. Не заявлять «всё работает на Windows» без live подтверждения на ПК пользователя. Дать 1–3 ясные команды проверки.
7. Если тест сломан — исправить, повторить CI, сохранить ссылку на QA. Зелёный CI не означает, что сторонний API доступен.
8. В конце каждой поставки указывать **новый коммит**, что реально готово, что не готово, список QA PASS/FAIL/SKIP и следующий шаг.
9. Если обновляется этот документ, повысить версию и вести `Change Log`; не удалять уже утверждённые решения без согласования.
10. Никогда не включать платные requests и периодический массовый сбор без отдельного явно включённого Source Gate и бюджета.

## 21. Change log и критерий передачи

**v1.0.1 (2026-10-18):** уточнены источник и TTL для `published_at`, запрет записи непроверенных общих полей и default-deny flags для metadata/search.  
**v1.0.0 (2026-10-18):** составлен после аудита реальных файлов ContentMachine, уточнения цели 10k новых/мес, разделения D0 от D1/D2, пересмотра YouTube 2026 квот/политик. Это технический **план**, не новая работающая функция.

**D0 acceptance checklist:** [ ] документ прочитан [ ] KPI согласован [ ] схема и source gating согласованы [ ] D1.1–D1.7 QA подтверждены в плане [ ] открытые решения видны [ ] пользователь разрешил реализацию D1.  
При отсутствии последнего пункта создавать код D1 нельзя.

**Точка входа следующего шага:** `D1.0 — Baseline safety`, затем `D1.1 — 002 migration`. Всё остальное — после соответствующего QA-gate.
