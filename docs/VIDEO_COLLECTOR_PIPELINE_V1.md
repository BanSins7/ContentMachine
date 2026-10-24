# Video Collector V1 — целевой pipeline и контракт модулей

**Проект:** ContentMachine  
**Рабочая ветка:** `feature/video-collector-dev-20261023`  
**Статус:** архитектурная спецификация; описывает целевое поведение, а не якобы реализованный live collector.  
**Приоритет V1:** TikTok через отдельный локальный Evil0ctal DTK v5; архитектура допускает другие источники позже.

## 0. Граница ответственности и реальные зависимости

```text
Approved Project + immutable RTB publication (main: реализовано)
  → Search Planner / SQG (main: contexts + planned generation; реального AI output ещё нет)
  → AI Query Generator (будущий)
  → Query Validation (будущий)
  → VALIDATED QUERY SET
  → VIDEO COLLECTOR:
     Query Intake → Source Registry → Task Planner → Policy Gate
     → Dry-run Preview → Authorized Commit → PostgreSQL Job Queue
     → Provider Adapter (DTK) → Async Task Poll → Page Inspect
     → Normalize/Quality → Rights Gate → Idempotent Persistence
     → Trace + Metrics/Run KPIs → SRE Handoff
  → SRE Relevance / Human Review (ядро правил есть, end-to-end вызовов нет)
  → Analytics / optional licensed Media Analysis (позже)
```

**Search Planner не вызывает TikTok. Collector не генерирует гипотезы и не решает маркетинговую релевантность. SRE не выполняет поиск.** Функция Collector — контролируемый сбор допустимых к хранению идентификаторов/метаданных с трассировкой происхождения и передача кандидатов на оценку.

В `main`: `intelligence/search_planner/{input_loader,context_builder,generation_engine}`, миграция `015_search_query_generation.sql` планируют `sqg_generations` со статусом `awaiting_authorization`, не выполняя LLM. `intelligence/discovery` умеет только ручной `manual_local_d1` ingest. PR #10 (draft) содержит `intelligence/collector/planner.py` и `apify_preview.py` — только offline Apify preview. PR #13 (open) содержит `intelligence/connectors/dtk.py` и `http_api.py` — ручной TikTok tool через DTK без автоматического ingest. Существующие функции не считать live-реализацией нового pipeline.

## 1. Принципы, которые нельзя нарушать

1. Source of truth — одобренная immutable публикация RTB и верифицированные сервером `project_id/publication_id/taxonomy_sha256`; никогда не принимать от браузера `publication_verified=true`.
2. `Search Planner → Collector` передаёт проверенные поисковые кандидаты, с `hypothesis_id`, `direction_id`, версиями модели/проверки, языком и режимом поиска. Collector не строит запрос из названия гипотезы повторно, кроме отдельно маркированного offline fallback.
3. Поиск может проходить только через зарегистрированный и разрешённый источник. Техническая capability **не равна** праву на коммерческое использование, поиск, хранение, аналитику, архивирование или скачивание видео.
4. До первого реального вызова DTK проверить его собственную конфигурацию хранения/архива (`archive.enabled` может быть включён), тип разрешённой identity и договорные условия TikTok; если нельзя подтвердить разрешения — только офлайн-режим.
5. Локальный DTK — отдельный процесс. ContentMachine хранит ключ в локальном gitignored `.env` и не пересылает в UI. Коннектор PR #13 ограничен `http://127.0.0.1:PORT`, `tiktok:read` и белым списком операций.
6. `GET` и Preview не запускают внешние поиски, не тратят деньги и не создают повторные задания.
7. Любой внешний submit — строго после отдельного Commit; ошибку/таймаут submit нельзя автоматически повторять, если неизвестно, был ли создан upstream task. Вести `uncertain` и reconciliation.
8. Идентичность видео: уникально `(platform, platform_video_id)`; попадания по разным запросам сохранять как независимый provenance, а не дубли видео.
9. Отсутствующие метрики/дата/длина = `null/unknown`, никогда ноль или выдуманная дата. `published_at`, `fetched_at`, `observed_at` — разные события.
10. Raw response и MP4 **не хранятся по умолчанию**. Метрики, снапшоты, анализ медиа — отдельно разрешаемые capability/rights.
11. Транзакционный commit + идемпотентные writes + lease fencing. Не публиковать `completed`, если на самом деле запись результатов не завершена.
12. Лимиты — per project, per run, per provider/day, per query/pages/results, бюджет с fail-closed поведением. Параллельность по умолчанию минимальная.

## 2. Pipeline по этапам (вход → действие → выход / блокировка)

### 01. Published Taxonomy Resolver [существует выше Collector]
**Вход:** `project_id`, утверждённые RTB `taxonomy_publication_id`, snapshot hash, approved facts, полная иерархия direction/branch/hypothesis и обе Human Review.  
**Действие:** Search Planner серверно проверяет проект, immutable publication и все связи.  
**Выход:** `HypothesisContext/v1` на каждую гипотезу. Нет разрешения → `blocked_taxonomy`.

### 02. AI Query Generator и Query Validation [пока будущие]
**Вход:** `HypothesisContext/v1`, `search_policy` с явно заданными языками, рынками и keyword/hashtag режимами.  
**Действие:** платная генерация только после согласия и budget gate, structured output; validation убирает некорректные, off-topic, противоречащие `exclude` и дублирующиеся запросы (семантические проверки/диверсификация только по фактически доступным моделям).  
**Выход:** immutable `validated_query_set/v1` с `query_id, text, mode, language, hypothesis_id, direction_id, taxonomy_publication_id, validation_version, generation_id`. Пустой валидный набор → `blocked_no_queries`. Важно: SQG в main сейчас **только планирует** генерации и батчи; stage с реальными запросами нужно написать.

### 03. Collector Query Intake [новый]
**Вход:** `validated_query_set/v1`, id владельца проекта / server auth context, конфигурация Collector.  
**Действие:** строгая серверная проверка scope проекта, версии таксономии, статуса валидации, устойчивых ID, схемы, границ query, timestamps; запрещены невидимые для владельца query sets и произвольный frontend JSON.  
**Выход:** детерминированный `collector_input_snapshot` + hash, никакого API-вызова.

### 04. Source Registry + Capability Matcher [новый]
**Вход:** набор keyword/hashtag/creator requests; конфигурация доступных источников и активных policy versions.  
**Действие:** сопоставить `mode` с разрешённой provider operation, проверить readiness, настройки, scope и технические capabilities; отделять поддерживается/не поддерживается/доступно лишь частично.  
**Выход:** кандидаты `source_task` либо machine-readable `unsupported_capability, blocked_config, blocked_auth, blocked_rights`.

**V1 registry:** `dtk_tiktok` (из PR #13) как основной источник для TikTok; используем `search_posts(keyword, cursor?)` для первого вертикального среза. Затем — `hashtag_posts` (через разрешение названия хэштега), `user_posts`, `related_posts` только как явные стратегии и при подтверждённых правах. `search_users`, `comments`, `music` доступны в ручном tool, но не включаются в автоматический V1 pipeline. Альтернативные Apify, YouTube, Instagram — отдельные будущие adapters, без тихого fallback на платный источник.

### 05. Task Planner / Coverage Planner [новый]
**Вход:** валидные query + eligible source.  
**Действие:** построить `(source, method, query_id, language, cursor_policy, project, hypothesis_id)`; обойти дублированные запросы, но сохранить все связи с гипотезами; явно разделить exploration и coverage; ограничить число задач, страниц и результатов. Не обещать фактическое число TikTok-результатов и отсутствие дублей.  
**Выход:** `CollectorPlan/v1`, `plan_hash`, `task_fingerprint`; каждая задача привязана к конкретному query и причине поиска. Неизвестная provider pagination должна быть отмечена как ограничение плана.

### 06. Source Policy / Rights / Quota / Budget Gate [новый]
**Вход:** planned tasks, `sources`, `source_policy_versions`, `source_quota_ledger`, external DTK configuration.  
**Действие:** отделить право на поиск, хранение метаданных, метрик, media, retention; проверить источник и архив upstream, лимиты, источник identity, rate limits; посчитать максимум доступных запросов и worst-case paid exposure при известной цене. Неизвестная цена для платного источника блокирует автозапуск.  
**Выход:** `allowed/blocked` и структурированные причины. Нельзя подменять TikTok provider `manual_local_d1`.

### 07. Dry-run Preview [новый]
**Вход:** снимок одобренного плана.  
**Действие:** полностью offline отображение: сколько гипотез/запросов/задач/страниц, какой источник, ограничения, права, квоты, возможные расходы, процент покрытия, отсутствующие возможности. Никакого DTK submit, сетевого поиска или резервирования денег.  
**Выход:** стабильные `plan_id, plan_hash, source_policy_version_id`, прозрачный preview. Изменение версии входа, политики или плана перед Commit → пересоздать preview.

### 08. Explicit Commit / Authorization [новый]
**Вход:** `plan_id`, `plan_hash`, `idempotency_key` и явное подтверждение владельца.  
**Действие:** в одной PostgreSQL транзакции проверить права ещё раз, закрепить immutable plan snapshot, активную source policy, создать `search_runs` и `jobs`, `source_requests` (или подготовленные записи для request lifecycle) и аудит; не выполнять внешний HTTP внутри транзакции. По повторному ключу вернуть тот же run, при другом hash — conflict.  
**Выход:** `search_run_id` в `queued`. Consent только на указанные source capabilities, лимиты и цену; отдельный approval для будущих платных providers.

### 09. Queue / Scheduler / Worker [новый]
**Вход:** записи `jobs` (существующая PostgreSQL lease queue).  
**Действие:** отдельный управляемый background worker берёт lease, проверяет `lease_generation` fencing, актуальную source policy, отмену, бюджет, rate limit и credentials; fairness по проекту/источнику. Логика сетевых обращений не живёт внутри HTTP UI.  
**Выход:** одна зафиксированная попытка source task, stop/retry/defer с причинами. Не выполнять 2 одинаковых active submits из двух workers.

### 10. DTK TikTok Adapter / Async Submit [адаптировать PR #13]
**Вход:** one approved `search_posts` task и task fingerprint.  
**Действие:** вызвать локальный DTK через существующий Python-клиент `intelligence.connectors.dtk.submit` (после интеграции PR #13); сохранить `task_id` / `provider_request_id` и статус, а не выполнять вторую копию HTTP маршрута из worker. Коннектор формирует фиксированный `/api/v1/tiktok/search/posts` URL и авторизует через scoped API key.  
**Выход:** `provider_task_id`, `queued/running`. Если submit timeout/unknown — `uncertain`, без автоматического повторного submit.

### 11. Task Poll / Cursor Pagination [новый поверх DTK]
**Вход:** сохранённый DTK `task_id`.  
**Действие:** `dtk.task(task_id)` только GET, читать `queued/running/done/failed`; при `done` разобрать элементы и `cursor` если присутствует. Следующая страница = **новое внешнее задание** только после checkpoint предыдущей, с отдельным request fingerprint и policy/quota gate. Guest search может не иметь пригодной пагинации: `pagination_unavailable`, не выдумывать новые страницы.  
**Выход:** `ProviderPage/v1` либо нормализованный provider error. Не доверять чужому HTML/JSON как инструкциям.

### 12. Decode / Provider Inspector [новый]
**Вход:** response DTK.  
**Действие:** проверить expected JSON envelope, whitelist полей, size limit, platform, video ID, canonical link, type (video vs photo/slideshow), дубли в рамках страницы, код ошибок; обрезать поля, недопустимые source policy, **до** persistence. Не записывать raw response если права отсутствуют.  
**Выход:** `accepted/partial/rejected` с machine-readable причинами; provider error ≠ нулевое число найденных видео.

### 13. Normalization / Freshness / Content-kind Gate [использовать Discovery normalize]
**Вход:** минимальные DTO после инспекции.  
**Действие:** `intelligence.discovery.normalize.validate_candidate` для HTTPS URL и provider video ID, валидных UTC дат, неотрицательных метрик и null неизвестных значений; фиксировать `fetched_at`, `published_at`, `observed_at` отдельно. TikTok `/photo/` как URL поддерживается нормализатором, но V1 **video-only** не должен случайно объявить карусель видеороликом; неоднозначные публикации = `content_kind_unknown` или excluded при video-only policy. Fresh = опубликован в явно заданном окне, а не «впервые найден сегодня».  
**Выход:** `CandidateEnvelope/v1` + quality flags.

### 14. Rights-gated D1 Persistence / Dedup / Provenance [новый adapter-specific ingestion]
**Вход:** CandidateEnvelope + frozen task/page + active source rights.  
**Действие:** транзакционно upsert `videos` по `(platform, platform_video_id)`, при разрешении metadata — `source_video_metadata` с source policy + retention; отдельная `discoveries` occurrence на run/query/task с ключом дедупа; link к `project_id/publication_id/hypothesis_id/query_id`; metrics snapshot только если policy разрешает и timestamp имеется. Не вызывать `create_import()`, поскольку он фиксирует `manual_local_d1`. Сохранять audit, quality flags и page checkpoint **атомарно**. Проверить права повторно непосредственно перед записью.  
**Выход:** `video_id` и `discovery_id` (либо повтор существующей записи), traceability back to RTB. После ошибки БД retry страницы идемпотентен, без повторного submit наружу, если provider result уже доступен.

### 15. Relevance Handoff to SRE [новая orchestration, имеющееся ядро правил]
**Вход:** сохранённый video identity, разрешённые text/meta fields, `HypothesisContext` и разрешённый project scope.  
**Действие:** определить `match/no_match/uncertain/not_evaluated` только моделью/правилами с известными версиями; не считать просто «получен по запросу» доказательством релевантности. Непроверенные → `not_evaluated`; сомнительные → human review. Не использовать поля/медиа, анализ которых запрещён.  
**Выход:** project-scoped relevance result и evidence; отдельный optional `human_labels` и версия результата.

### 16. Qualified Set / Analysis Handoff [новый]
**Вход:** релевантные, валидные, уникальные video IDs + политика хранения/аналитики.  
**Действие:** фильтровать по `valid identity + relevant + unique + permitted use + configured freshness`; подготовить view/batch для Analytics. Metrics, A/B features, Higgsfield/media/video transcription — **другой, opt-in pipeline**, не часть обязательного поиска.  
**Выход:** `qualified_videos` с исходными trace keys; если SRE нет, KPI `qualified_count` не выдавать как подтверждённый.

### 17. Monitoring / Replay / Retention [новая эксплуатационная часть]
**Вход:** run state, provider tasks, leases, retention policies, `monitor_targets`.  
**Действие:** recover abandoned leases, poll known external task IDs, отделить `partial` от `completed`, учесть `uncertain`; cancellation блокирует новые upstream submits, уже отправленный upstream task требует отдельного обращения, если provider это поддерживает; очистка истёкших metadata/metrics/media согласно source policy и revoke. Monitoring существующих видео отдельно от нового keyword-search.  
**Выход:** правдивый жизненный цикл, отсутствие дублирующих paid/provider requests и лишнего хранения.

### 18. UI / Operations Dashboard [новый]
В ContentMachine `Discovery Engine → Video Collector`:
- **Plan:** project/publication, выбранный query set, hypothesis coverage, sources/capabilities/limits.
- **Preview:** нулевые затраты, задачи/квоты/rights, hard blocks.
- **Run:** Start только с явным подтверждением, Stop/Resume через stateful service.
- **Progress:** running/partial/blocked/uncertain, provider tasks/page counts, ошибки, квоты, costs where known.
- **Videos:** уникальные кандидаты, confidence, relevance, freshness, content-kind, разрешённые метрики.
- **Trace:** для каждого видео показывать project → RTB hypothesis → query → collector plan → job/source task → provider task/page → discovery → classification.
- **Review:** human-label `relevant/irrelevant/unsure`, если у пользователя есть соответствующие права.

## 3. Межмодульные контракты

`validated_query_set/v1` (производит Query Validation, а не Collector):
```json
{
  "schema_version": "validated_query_set/v1",
  "project_id": "<project-id>",
  "taxonomy_publication_id": "<uuid>",
  "generation_id": "<uuid>",
  "queries": [
    {
      "query_id": "<stable-id>",
      "hypothesis_id": "<rtb-node-id>",
      "direction_id": "<rtb-node-id>",
      "mode": "keyword",
      "text": "10 minute home workout",
      "language": "en",
      "validation_state": "accepted"
    }
  ]
}
```

`collector_plan/v1` — immutable hash of project/pub/query set, source policy version, limits and tasks. **Do not trust client hash alone; server reconstructs.**

`source_task/v1`:
```json
{
  "source_id": "dtk_tiktok",
  "operation": "search_posts",
  "query_id": "<stable-id>",
  "params": {"keyword": "10 minute home workout"},
  "max_pages": 1,
  "max_candidates": 20,
  "task_fingerprint": "<sha256>"
}
```

`candidate_envelope/v1`: `project_id, taxonomy_publication_id, hypothesis_ids, query_id, source_id, search_run_id, source_request_id, provider_task_id, provider_video_id, canonical_url, platform, title?, published_at?, fetched_at, observed_at?, duration_seconds?, content_kind?, metrics? [only if allowed], quality, quality_reasons`. Per-record source data is untrusted; canonical identity checked by discovery normalizer.

Семантическая связь нескольких гипотез с одним query/video сохраняется отдельными links, без дублирования `videos`.

## 4. PostgreSQL ownership и миграции

**Повторно использовать существующее**, не размножать параллельные таблицы:
- `sqg_*` — создаёт и аудирует *генерацию query*, не является collector run.
- `sources` / `source_policy_versions` — source gates и versioned permissions.
- `search_queries` — поисковые строки с `parent_query_id`, если нужна discovery-проекция approved SQG queries.
- `search_runs` — один collector execution (run_id, config, status).
- `jobs` — очередь с lease-generation.
- `source_requests` — логические внешние обращения, attempt_no, quota/cost/charge-state.
- `videos`, `creators` — канонические сущности.
- `discoveries` — уникальная occurrence по source/run/query.
- `source_video_metadata`, `metric_snapshots` — только при разрешении на запись и retention.
- `data_quality_flags`, `human_labels`, `video_topics`, `audit_events` — качество, проверка, аудит.
- `monitor_targets` / `source_quota_ledger` — более поздний мониторинг, квоты.

**Новые additive tables V1 (минимально):**
- `collector_plans`: `project_id, publication_id, query_set_hash, plan_hash, snapshot, source_policy_version_id, created_at`; immutable после Commit.
- `collector_provider_tasks` или `collector_attempts`: FK на `source_requests`, `provider_task_id`, `request_fingerprint`, `submit_state`, `page_cursor_hash`, `checkpoint`, `uncertain_reason`. Выбрать **одну** таблицу, без второго конкурирующего attempt ledger.
- `collector_query_links`: `project_id, publication_id, query_id, hypothesis_id, source_request_id/discovery_id`, PK/unique; позволяет восстановить связь query/hypothesis без дубликата `videos`.

Существующая модель `source_requests` имеет статусы `planned,started,succeeded,failed,unknown`; наружу показать нормализованные UI состояния, не добавляя несовместимых строк в CHECK constraint без миграции. Миграции делать только новыми номерами после последней `015`, без изменения уже применённых `001/002/015`. До коммита migration проверить свежий main и параллельные ветки; номер согласовывать с интегратором.

## 5. Стейт-машины и идемпотентность

**Plan:** `draft → previewed → committed` или `blocked`; preview offline и неизменяемый snapshot.

**Search run (существующий DB enum):** `created → queued → running → completed | partial | blocked | failed | cancelled`. `partial` если часть задач завершилась, но есть блокировки, ошибки или нет проверенной полноты страниц; `completed` только после commit всех обязательных страниц и завершения jobs.

**Provider request:** `planned → started → succeeded | failed | unknown`. Внешний async task: `not_submitted → submit_intent → queued/running → done/failed`, при таймауте `uncertain` до reconcile. `done` у DTK **ещё не означает** persisted in PostgreSQL.

**Идемпотентные ключи:** `collector_run: hash(project+publication+query_set+plan+user_request_key)`; `logical_request: hash(run+source+query_id+page_cursor_hash)`; `discovery_key: hash(run+source+logical_request+platform+platform_video_id)`. Независимые query hits сохранять; повтор того же request не создаёт новую occurrence.

**Submit uncertainty:** хранить intent **до** внешнего вызова; при timeout/падении между submit и commit `task_id` не повторять submit, пока не проведена внешняя reconciliation и нет достоверного подтверждения, что он не исполнен. GET poll безопасен только по известному task ID. На этапе пилота такие ситуации требуют ручного вмешательства.

## 6. KPI: не путать просмотры, найденные и квалифицированные

На run, source, hypothesis и query отдельно считать:
- `requests_planned/started/succeeded/failed/unknown`
- `raw_items`, `accepted`, `partial`, `rejected`
- `unique_video_ids`, `duplicates`, `duplicate_rate`
- `fresh_unique` по **published_at**, `missing_published_at`
- `relevant_labeled`, `irrelevant_labeled`, `uncertain`, `not_evaluated`
- `qualified_fresh_unique` (целевая метрика: свежие + уникальные + релевантные + разрешённые)
- `coverage_by_hypothesis`, `pages_per_query`, `pagination_block_rate`
- `provider_latency`, `rate_limit_errors`, `recovery_time`, `quota_used`, `spend_usd`, `cost_per_qualified` когда данные реально имеются.

10 000 свежих уникальных релевантных видео в месяц — исследовательская **цель**, а не гарантированная производительность текущего кода. Без production SRE KPI qualified остаётся `not_evaluated`.

## 7. Предполагаемая структура исходников

```text
intelligence/
  collector/
    __init__.py
    contracts.py               # CollectorPlan, SourceTask, ProviderPage, CandidateEnvelope
    query_intake.py            # only validated SQG query sets
    registry.py                # source capabilities and source-policy IDs
    planner.py                 # deterministic plan; reconcile PR #10 preview
    policy_gate.py             # licensing, retention, budget and quote gates
    repository.py              # DB run, attempt, links, audit and checkpoints
    service.py                 # preview/commit/cancel/diagnostics
    worker.py                  # async task execution atop PostgreSQL jobs
    reconcile.py               # unknown task states, no blind re-submit
    provider_inspector.py      # schema/size/content-kind/rejection reasons
    normalize.py               # mapping -> discovery.normalize
    ingest.py                  # provider-specific D1 persistence, NOT manual D1
    handoff.py                 # SRE / project-hypothesis link
    http_api.py                # thin localhost routes
    adapters/
      __init__.py
      base.py                  # SourceAdapter protocol
      dtk_tiktok.py            # reuse intelligence.connectors.dtk
      apify_tiktok.py          # optional, not in V1 execution
      youtube_official.py      # later
      instagram_official.py    # later
  connectors/
    dtk.py                     # PR #13; no fork/copy of upstream DTK
  discovery/                   # current D1 canonical catalog
  relevance/                   # current SRE domain/core
  search_planner/              # current SQG source of search queries
```

`app.py` только маршрутизирует /api; UI в `site/`. Независимый worker не должен запускать оплачиваемую внешнюю работу при простом старте ContentMachine. Никакой синхронной TikTok загрузки в browser request. Секреты не попадают в журналы, DB JSON payload и клиент.

## 8. API контуры (предложения, ещё не реализованы)

- `GET /api/intelligence/collector/sources` — доступные provider/capabilities/rights summaries.
- `POST /api/intelligence/collector/preview` — offline snapshot and blocks.
- `POST /api/intelligence/collector/runs` — explicit authorized commit, idempotency key.
- `GET /api/intelligence/collector/runs/:id` — run, tasks, errors, KPI.
- `POST /api/intelligence/collector/runs/:id/cancel` — stop future submissions.
- `GET /api/intelligence/collector/videos?run_id=...` — videos with provenance/relevance only if user has rights.
- `GET /api/intelligence/collector/videos/:id/trace` — RTB→query→provider→discovery trace.
- `POST /api/intelligence/collector/runs/:id/reconcile` — explicit admin recovery for uncertain tasks.

Важно сохранять same-origin `X-ContentMachine: 1`, strict Host/Origin, per-project authorization. Граница между ContentMachine localhost и DTK localhost — server-to-server only.

## 9. Реализация по вертикальным срезам

**Phase A — офлайн контракт и планирование:** query-set fake fixtures (пока настоящего SQG output нет), серверная RTB linkage, registry, offline preview, gates, tests. Не делать provider calls.

**Phase B — DTK read-only connector adoption:** проверить свежий PR #13 и перенести/объединить без дублирования. Получить реальную диагностику availability, scoped token, исправный `search_posts` ручной E2E, только после прав и disabled unlicensed upstream archive.

**Phase C — одно управляемое live-задание:** 1 hypothesis → 1 approved keyword → 1 DTK `search_posts` → task_id → poll → sanitize → parse; никаких импортов до rights/ingestion tests.

**Phase D — transactional Discovery ingest:** allowed metadata, canonical dedup, discoveries provenance, per-hypothesis link, page checkpoint; rerun/replay and crash tests.

**Phase E — multi-query worker:** job leasing, quotas, pagination bounds, fair scheduling, restart, cancel, uncertainty/reconciliation. Тесты на конфликты с одновременными workers.

**Phase F — SRE and UX:** relevance/human review, qualified KPI, trace, UI, policy-aware content read.

**Phase G — optional sources/monitoring/advanced analytics:** YouTube/Instagram/Apify, provider-specific legal gates, metrics-refresh, media/video analyses only by authorization. Масштабирование после измеренного пилота.

## 10. Test & QA matrix

- Contracts: wrong project/publication, draft taxonomy, stale query set, unsupported mode, malformed URLs, mismatched TikTok IDs, malformed dates, photo vs video.
- Policy: disabled/expired source, read denied, metadata denied, metrics denied, retention expired, DTK upstream archive policy unknown, no credentials, price unknown.
- Preview: 0 upstream/LLM calls, deterministic plan/hash, quota breakdown, collision/fingerprint protection.
- Concurrency: duplicate POST commit returns same run, mismatched same key returns conflict; two workers cannot double submit, lease fence prevents stale commit.
- Network: DTK 401, 403, timeout-before/after-submit, 429, malformed JSON, no cursor, guest short result, missing task_id, task failed, pagination capped.
- Recovery: crash after submit before storing task ID ⇒ uncertain/no automatic duplicate submit; crash after page parse before DB commit ⇒ replay safely from provider result; cancellation blocks next tasks.
- DB: canonical video unique, discoveries distinct query occurrences, no false `manual_local_d1`, no stored forbidden metrics/raw, TTL/revoke enforced at read.
- SRE: not_evaluated != no_match, irrelevant not counted qualified, human labels preserved, cross-project separation.
- UI/API: same-origin protections, no secrets in responses/logs, read-only GET, user-specific errors, consistent KPI and trace.
- Smoke: `pytest/unittest` offline + PostgreSQL integration + opt-in local DTK live smoke (never silently run in CI).

## 11. Definition of Done (конец V1)

1. Владелец выбирает утверждённый query set и видит offline preview.
2. Без явного разрешения отсутствуют внешние requests.
3. После ручного Commit worker создаёт и отслеживает DTK task, используя существующий коннектор.
4. Полученные разрешённые TikTok video metadata безопасно записаны в D1; источник честно обозначен.
5. Повтор submit/worker retry/повтор страницы не создаёт дубликатов и не скрывает неизвестные результаты.
6. У каждого видео доступен provenance до RTB hypothesis/query/source task.
7. Неизвестная дата/число просмотров/классификация не превращаются в ноль или успешный match.
8. Ошибки/partial/cancel/uncertain и реальные лимиты отражены в UI.
9. Ветка содержит миграции, тесты, документацию и PR; merge в `main` только после ревью.
10. Продукт не обещает коммерческую полноту TikTok-поиска, обход ограничений, загрузку чужого MP4 или подтверждённые 10k qualified/month без полевых измерений.

## 12. Открытые вопросы до подключения реального DTK

- Разрешён ли выбранный способ обращения к TikTok и коммерческое хранение конкретных полей в проекте?
- Какую identity использует установленный локальный DTK и работает ли keyword/pagination на реальных запросах?
- Подтверждено ли отключение upstream archive/кэша, если лицензия запрещает их?
- Какую модель Query Validation и SRE запускать и каким отдельным бюджетом это разрешено?
- Точный контракт persisted approved query set ещё не реализован: согласовать со вторым чатом разработки Search Planner.
- Номера новых миграций и пересечение `app.py/site/index.html` согласовать с другими параллельными ветками перед merge.

Эта спецификация — **проектное решение для обсуждения и поэтапной реализации**, не утверждение о готовности production collector.
