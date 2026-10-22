> **Актуализация на 22.10.2026:** документ описывает проектные решения и/или состояние на дату создания, а **не полный перечень готовых функций `main`**. В `main` реализован только offline/manual Discovery D1; автоматический поиск внешних платформ D2+, KPI 10 000 новых видео в месяц и live Video Collector не подтверждены и не запущены. Исследовательский PR #11 уже merged, draft PR #10 ещё не merged. См. [CURRENT_STATE_2026-10-22.md](CURRENT_STATE_2026-10-22.md).

---

# ContentMachine / Discovery Engine — V1.0 / 10 000 новых роликов в месяц

Статус: **утверждаемая архитектурная спецификация** (ещё НЕ реализация). Дата: 2026-10-18.  
Основа: `docs/DISCOVERY_ENGINE_ARCHITECTURE_V1.md` (подробный черновик), `intelligence/migrations/001_foundation.sql`, `intelligence/jobs.py`.

## 1. Цель / границы

**Цель:** за календарный месяц получить **≥10 000 уникальных валидных видео** из разрешённых источников, с известным source provenance, URL, platform+video_id, точным временем обнаружения и тематической релевантностью. Для KPI «новые ролики» требуем подтверждённую дату публикации в последних 30 днях; публикации с неизвестной датой сохраняем отдельно, **не засчитываем в KPI**. Считать одно видео один раз по ключу `(platform, platform_video_id)`. Кроссплатформенные перезаливы — отдельная задача; на V1 не склеиваем их слепо.

Планирование: **~333 зачтённых/день**. При эффективности полезных находок 40–60% потребуется **~17–25 тыс. сырых кандидатов/месяц**; резервный сценарий до 30 тыс. Количество неподтверждено, устанавливается по фактическим пилотным yield/coverage/cost. 10 000 — *целевой KPI*, не гарантия провайдера.

**Не входит в Discovery:** загрузка/сохранение MP4, автоматическая обработка 10 000 видео GPU-моделями, произвольное обучение и viral scoring. Аналитика и обработка медиа — отдельные пайплайны с собственными правами и бюджетами.

## 2. Компоненты / физическое размещение

```text
ContentMachine UI (127.0.0.1:8765)
  ├─ Search Config + Creator Watchlist + Budget / Source Rights
  ├─ Runs dashboard + Diagnostics + Quality Flags
  └─ Read API: runs, videos, provenance, snapshots, quota/cost
                    │ enqueue jobs
                    ▼
PostgreSQL 17 (127.0.0.1:54329)
  ├─ jobs (lease-token + retry), search_runs, source_requests
  ├─ topics / queries / creators / videos / discoveries
  ├─ metric_snapshots / monitor_targets / rights/retention
  └─ quality_flags / quota_ledger / human_labels
                    ▲
           discovery_worker.py (1 процесс)
                    │
Search Planner -> Source Capability/Policy Gate -> Quota Gate
                    │
  YouTube Data API | Commercial Provider A | CSV/URL | Provider B (fallback)
                    │
 Adapter + Paginated Cursor + API Audit -> Validator -> Normalizer
                    │
            Idempotent DB Upsert + Trace
                    │
           Adaptive Monitor Scheduler
                    │
  Eligible records -> Analytics Engine (отдельно, policy gate)
```

**Запуск на Windows:** текущий `RUN_AUTO.bat` остаётся управляющим веб-сервером; включаем отдельный Discovery worker/supervisor с восстановлением после перезапуска. 1 CPU worker, 2–4 одновременных сетевых обращения максимум на старте, per-source throttling. RTX 3090 **не требуется** Discovery. Docker нужен только PostgreSQL.

## 3. Источники / приоритет

1. **YouTube Data API**: новый контент по `search.list` (keyword+date) и эффективный обход uploads-playlist отслеживаемых каналов через `playlistItems.list`; получение подробностей пачками через `videos.list`. **По правилам, действующим с июня 2026: стандартный отдельный лимит 100 `search.list` вызовов/день, стоимость 1 unit/call; другие эндпоинты — общий пул 10 000 units/day.** Ранее встречавшаяся оценка «100 units за search.list» устарела. Реальная квота определяется проектом в Google Cloud.
2. **Коммерческий провайдер A: Apify *или* Bright Data**, решение только после пробного набора 200–300 записей по платформам и сверки **unique-relevant yield, допустимости, фактических оплаченных запросов и field coverage**. Сначала один, не подключаем оба вслепую. TikTok и Instagram — через подтверждённый набор поддерживаемых методов, а не через обещания «доступно всё».
3. **Manual CSV / URL seeds**: дешёвый дополнительный канал, общий Validator/Normalizer/Deduper.
4. **Provider B** — fallback после доказанного недостатка охвата первого и подтверждённой экономической целесообразности.
5. Официальные TikTok/Instagram API — для доступных им авторизованных сценариев; они **не** заменяют массовый общий поисковик.

**Источник всегда имеет capabilities/rights/retention.** Права на storage, discovery, derived metrics и медиа проверяются независимо. Рейтинг/аналитика на YouTube API запрещены по умолчанию до принятия/подтверждения соответствующего дополнения к правилам; неавторизованные API-данные не держим сверх разрешённых сроков. Коммерческий источник не предоставляет автоматически право нарушать условия платформы. Чувствительные данные и credentials — локально, не в GitHub/логах.

Источники: https://developers.google.com/youtube/data/getting-started ; https://developers.google.com/youtube/data/determine_quota_cost ; https://developers.google.com/youtube/v3/docs/playlistItems/list ; https://developers.google.com/youtube/v3/docs/videos/list ; https://developers.google.com/youtube/terms/developer-policies ; https://developers.google.com/youtube/terms/derived-metrics-policy ; https://brightdata.com/products/data-feeds/web-data-api

## 4. Карта одного запуска

```text
Config → Dry-run plan (capabilities/rights/quotas/budget)
  → Create run_id → lease jobs
  → Source fetch [query/channel/cursor; request_id, latency, costs]
  → Validate [status: valid/partial/rejected; reason code]
  → Normalize [UTC timestamps, exact/estimated/null, canonical platform+id]
  → Dedup + Upsert (transactionally, preserve discoveries/provenance)
  → Quality + Topic relevance checks
  → Monitor targets (priority + next_due + permission)
  → Read UI / monthly KPI / export allowed fields
```

**Повторная проверка — отдельное задание**, не повтор поиска. Стартовая adaptive cadence: свежие ролики — 1/сутки первые 3 дня, затем 1/3 дня до 14 дня, дальше реже/по разрешению источника. Нагрузка должна вычисляться по cohort и доступному бюджету; не назначать бессрочные ежедневные проверки всему растущему архиву. `NULL != 0`; missing shares не заменять `0`; хранить observed_at и source_id каждого snapshot; одновременно у нескольких провайдеров сохранять независимые наблюдения и конфликт качества. Для YouTube соблюдать хранение/обновление/удаление в соответствии с авторизацией и правилами.

**Идемпотентность:** `(platform, platform_video_id)` в `videos`, `(source_id, query_id, provider_record_id, observed_at/page_key)` для events по конкретным условиям; retry не должен создавать бессмысленные дубликаты discoveries. У заданий lease_generation/token, чтобы stale worker не завершил переарендованное задание.

## 5. DB / HTTP API / UI

Не менять deployed `001_foundation.sql`. Добавить `002_discovery.sql`:
- `search_runs` + `source_requests`: run status, batch, cursor/checkpoint, response code, request cost, latency;
- `discoveries.run_id`, `creator_topics`, `video_topics`, `quality_flags`, `human_labels`;
- `monitor_targets` + `quota_ledger` + `jobs.lease_generation`;
- source capabilities, compliance/retention flags, indexes, expiry clean-up jobs.

API (local-only): `POST /api/intelligence/search-runs`; `GET /api/intelligence/search-runs`; `GET /api/intelligence/videos`; `GET /api/intelligence/videos/{id}/trace`; `GET /api/intelligence/sources`; `GET /api/intelligence/diagnostics`; `POST /api/intelligence/monitor-targets`; `POST /api/intelligence/search-runs/{id}/cancel`.

UI: **план до запуска** (план вызовов и бюджет), **статус в реальном времени** (queued/running/partial/blocked/failed), **число уникальных свежих и релевантных за день/месяц**, таблица карточек, страница «почему найдено», field coverage, ошибка + request_id, quota и стоимость по источнику. Можно остановить поиск или убрать источник без потери уже подтверждённых записей.

## 6. Инкременты и QA-gates (без «чёрного ящика»)

| Gate | Реализация | Обязательный QA / критерий приёмки |
| --- | --- | --- |
| **D0 — Source feasibility** | Договорённости по правам, пилот YT и провайдера A, реестр capabilities, смета | 200–300 реальных кандидатов / источник при доступе. Замер релевантности, дубликатов, заполненности, цены за 1000 **полезных уникальных**. Источник без доказательств не включать |
| **D1 — Storage core** | 002, contracts, normalize/validate/upsert, audit/trace | 10k синтетических строк + повторная загрузка без прироста уникальных; null/zero; UTC; FK; 3 platform ID; CI PostgreSQL integration; existing downloaders не сломаны |
| **D2 — YouTube adapter** | search/channel/playlist/videos batch/cursor/quota | Реальный поиск ≥100 проверяемых кандидатов при ключе, правильное число запросов/квот, 401/403/429/500 tests, явный `shorts_candidate`; нет ложного confirmed Shorts |
| **D3 — Commercial adapter** | Один платный источник с budget circuit-breaker; graceful fallback | Live 200–300 кандидатов, фактическая цена и качество; исчерпание бюджета -> stop, не повторять платные вызовы при replay |
| **D4 — Planner+Queue** | scheduling, parallelism 2–4, checkpoint, lease token, quotas | Kill/restart mid-page, 2 workers, stale-worker fencing, no duplicates, 429 Retry-After, источник offline -> partial run |
| **D5 — Monitoring** | creator watchlists, snapshots, adaptive rechecks, TTL/cleanup | 3 измерения тестового ролика на разных временах; правильная история; 30-day data-policy checks; источник запретил storage -> запись исключена |
| **D6 — UI/Diagnostics** | Search form, run tracker, gallery, traces, costs, quotas | Пользователь за ≤3 клика получает от карточки video_id/source/request/time; все ошибки понятны, API/лог не раскрывают ключи |
| **D7 — Scale acceptance** | Индексы, batch insert, лимиты памяти, 7-дневный pilot | 10k fixture-inserts, restart soak, объём RAM/time измерен на Windows; 7 суток реального сбора с темпом ≥333 валидных свежих/день, без превышения бюджета/квот. Месячный KPI подтверждается только после полного месяца фактических данных |

**CI vs LIVE**: зелёный GitHub Actions = unit/integration/mock тесты, **не** доказательство доступности соцсетей. Для каждого D-гейта публикуем commit, ссылку на Actions и фактический протокол live QA. Не объявляем D2/D3/D7 готовыми только по mocked тестам.

## 7. Метрики управления
- Бизнес: `monthly_new_qualified_unique / 10_000`, `daily_new_qualified_unique / 333`, `active_watch_creators`.
- Качество: `unique_yield`, `human_label_precision@100`, `duplicate_rate`, `published_at_coverage`, `field_coverage`, `provider_conflict_rate`.
- Надёжность: `failed_runs`, `stale_jobs`, `429_rate`, `mean_recovery_time`, `quota_used`, `monthly_spend`, `cost_per_qualified_unique`.
- Вывод: `projected_monthly = 30 * rolling_7d_daily_mean`; пометка **прогноз**, пока месяц не закрыт.

## 8. Нужные решения пользователя до подключения платных источников

1. Максимальный **месячный бюджет** Apify/Bright Data; до ответа `paid_spend_cap = 0` (никаких платных запусков).
2. Какие платформы приоритетнее — первоначально делаем архитектуру нейтральной, распределение объёмов не выдумываем.
3. Ниша/языки/регионы, чтобы проверить достижимость 333 свежих тематически релевантных роликов в день.
4. Доступ к YouTube Data API (пользователь подключает ключ локально); согласование YouTube derived-metrics authorization отдельно от Discovery.

**Не меняем работающий сайт, Docker, OpenRouter key storage или downloader behavior до отдельной реализации D1.**
