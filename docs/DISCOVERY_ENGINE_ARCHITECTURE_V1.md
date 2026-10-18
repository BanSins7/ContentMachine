# ContentMachine — Discovery Engine V1: архитектурный контракт
Статус: **DRAFT / требуется согласование до реализации сборщиков**  
Дата: 2026-10-18. Базовая ветка: `main`. Рабочий checkout пользователя: `C:\ContentMachine`.

## 0. Назначение и границы
Discovery: принимает ниши, запросы, авторов и правила доступа; планирует вызовы источников; получает **кандидатов и метаданные**; проверяет, нормализует, сохраняет их происхождение; по разрешению периодически обновляет статистику; формирует очищенный список кандидатов.

Discovery **не** скачивает автоматически медиаконтент, не рассчитывает собственный Viral Score на запрещённых источниках, не создаёт сценарии и не выдаёт «вероятность вирусности». Эти задачи поручаются последующим блокам Analytics, Batches, AI с отдельными настройками и правами.

**Никакой black box:** любой найденный ролик должен иметь исходный URL, source_id, запрос/автора, ID запуска, время получения, доступные значения, причины пропущенных полей и историю статусов.

## 1. Уже существует (проверено в GitHub)
- `app.py` — локальный HTTP на `127.0.0.1:8765`; старые Downloader API остаются нетронутыми.
- `intelligence/migrations/001_foundation.sql`: `sources`, `topics`, `creators`, `videos`, `search_queries`, `discoveries`, `metric_snapshots`, `jobs`, `analytics_scores`, `batches`, `batch_videos`, `media_assets`, `video_analyses`, `audit_events`.
- `intelligence/contracts.py` — черновой Python Protocol для адаптеров.
- `intelligence/jobs.py` — очередь PostgreSQL с SKIP LOCKED, leases, retries.
- `intelligence/migrate.py` и `docker-compose.yml` — миграции и PostgreSQL.
- `GET /api/intelligence/health` — базовая проверка подключения.

**Нет**: универсального поискового API, подключённых Discovery-адаптеров, поискового планировщика, записей о запусках/страницах выдачи, сохраняемых курсоров, пользовательской витрины и расписания проверки кандидатов.

**Правило:** `001_foundation.sql` не редактировать после развёртывания; все изменения только в `002_discovery.sql` и следующих миграциях.

## 2. Поток и контракты этапов
```text
[UI: ниша/платформы/ключи/авторы/лимиты]
       ↓ POST /api/intelligence/search-runs
[Search Planner] — проверка capabilities/rights/quota, создание tasks
       ↓ [jobs + search_runs]
[Adapter: YouTube | TikTok | Instagram | Licensed Provider | Manual]
       ↓ CandidateEnvelope с source_id/run_id/query/context/raw fields
[Validator] — ID/UTC/схема/тип/качество/разрешения
       ↓ Valid / Partial / Rejected (с reason_code)
[Normalizer/Deduper] — (platform, platform_video_id), upsert
       ↓ DB transaction: creators + videos + discoveries + snapshots
[Monitoring Scheduler] — когда снова проверить сведения по ID/автору
       ↓ metric_snapshots (каждое наблюдение отдельной строкой)
[Read model/UI] — карточки, источники, логи, фильтры
       ↓ отдельный контракт для Analytics Engine, после разрешений
```

### Этапы и наблюдаемость
| Этап | Вход | Выход | Ошибка/контроль |
| --- | --- | --- | --- |
| Planner | SearchConfig | Пакет `SearchTask` | Нет прав/ключа/квоты -> `blocked` с причиной |
| Adapter | `SearchTask`, cursor | `CandidateEnvelope[]`, next_cursor, API metadata | 429 -> `rate_limited`, 401 -> `auth_required`, таймаут -> retry |
| Validator | Envelope | `valid/partial/rejected` + reason_codes | Нет видео-ID -> reject; нет shares -> null |
| Deduper | Valid candidate | Одно видео, множество discoveries | Повтор той же страницы не дублирует видео |
| Enrichment | ID списка | Извлечённые детали и creator ID | Нет подробностей -> partial, никогда не фантазировать |
| Snapshot | video/source/time | Запись счётчиков и времени | Источник запрещает storage -> пропустить |
| Monitoring | watchlist + budget | Запланированные обновления | Лимит -> отложить с next_run |
| Read API | filters/page | Набор карточек и trace | Списки пагинируются, без секретов |

## 3. Стратегии поиска (взаимодополняющие)
1. **Keyword/Hashtag**: пользовательские ключи, хэштеги, исключения, окна дат, поддерживаемые source sort modes (relevance/date/views). Поисковая выдача НЕ является всей платформой.
2. **Creator Watchlist**: отслеживаем известные аккаунты и новые ролики через доступные методы канала/поставщика. Это основной устойчивый контур.
3. **Creator Discovery**: из пригодных найденных кандидатов извлекаем авторов; оцениваем долю релевантных публикаций, затем предлагаем подписку на автора.
4. **Trend Signals**: официальные тренды или разрешённые провайдеры. Если API не позволяет массовый поиск, адаптер не обещает его.
5. **Query Expansion**: генерация синонимов и смежных намерений, связана с `parent_query_id`. Добавление в активный поиск только после semantic relevance check / подтверждения. Первый релиз: детерминированные шаблоны; локальная LLM — позже.
6. **Manual URL Seed**: пользователь добавляет URL, который нормализуется и попадает в тот же поток. Никаких фиктивных цифр.

**Исследование и эксплуатация:** на старте выбираем пилотную нишу, не обещаем полный охват; сохраняем статистику уникальных полезных находок для каждого поискового канала. Не допускаем бесконтрольный recursive expansion («drift»).

## 4. Реальные адаптеры и доступность
| Источник | Что реально делаем | Ограничение |
| --- | --- | --- |
| YouTube Data API | `search.list` по словам/датам, `channels.list` → uploads playlist, `playlistItems.list` для авторов, `videos.list` и `videos.batchGetStats` для метаданных | `search.list` default 100 **вызовов** в день отдельной квотой; дополнительные страницы тоже расходуют её; поиск может быть неполным |
| YouTube Shorts check | Кандидаты <4 минут и доступные признаки, статус `shorts_candidate` | `videoDuration=short` означает **<4 минут**, а не подтверждение формата Shorts. Нельзя маркировать каждый найденный ролик как Short |
| TikTok Display API | Только авторизованные публикации пользователя после Login Kit/`video.list` | НЕ общий поисковик по произвольным чужим аккаунтам |
| TikTok Research API | Не входит в коммерческую V1 | Предназначен для одобренных некоммерческих исследований |
| Instagram Graph API | Разрешённые данные профессиональных аккаунтов, хэштеги в доступных сценариях | Нет универсального доступа к потребительским аккаунтам и полной ленте |
| Сторонний провайдер | Отдельный адаптер после пилота и проверки лицензии/полей/стоимости | Не считать рекламное описание доказательством полноты и легальности |
| Manual | URL и пользовательские материалы | Если статистика недоступна — только ссылка, `null` для счётчиков |

**YouTube policy gate**: без отдельного допуска/разрешения на advanced derived metrics не запускаем кастомные outlier/viral score на неавторизованных данных YouTube API и не храним их статистику сверх сроков правил. Для разрешённого сценария фиксируем условия в `sources`. Данные от YouTube API не сливаем в произвольный единый рейтинг с другими платформами. Источник всегда виден.

Источники:
- https://developers.google.com/youtube/v3/docs/search/list
- https://developers.google.com/youtube/data/determine_quota_cost
- https://developers.google.com/youtube/v3/docs/playlistItems/list
- https://developers.google.com/youtube/v3/docs/videos/list
- https://developers.google.com/youtube/data/reference/rest/v3/videos/batchGetStats
- https://developers.google.com/youtube/terms/derived-metrics-policy
- https://developers.google.com/youtube/terms/developer-policies-guide
- https://developers.tiktok.com/docs/en/display-api-overview
- https://developers.tiktok.com/docs/en/research-api-faq
- https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api

## 5. Унифицированные данные
```python
@dataclass(frozen=True)
class DiscoveryCandidate:
    platform: Literal["youtube","tiktok","instagram"]
    platform_video_id: str
    canonical_url: str
    source_id: str
    run_id: int
    method: str
    query_id: int | None
    source_rank: int | None
    creator_platform_id: str | None
    published_at: datetime | None    # timezone-aware UTC
    title: str | None
    description: str | None
    duration_seconds: float | None
    views: int | None
    likes: int | None
    comments: int | None
    shares: int | None
    saves: int | None
    observed_at: datetime | None
    fetched_at: datetime            # UTC
    precision: Literal["exact","rounded","estimated","unknown"]
```
Это целевой контракт — текущий `intelligence/contracts.py` меньше и должен быть расширен совместимо.

**Правила:**
- `null` означает «источник не предоставил», `0` означает фактически измеренный ноль.
- Каждый `metric_snapshots` обязательно содержит `source_id`, `observed_at`, precision; `collected_at` — когда мы записали.
- `published_at` нельзя подменять временем обнаружения; неизвестная дата остаётся `null`.
- `platform_video_id` + platform — стабильный ключ ролика; URL канонизируется отдельно; не путать repost с тем же видео.
- Разные провайдеры могут выдавать противоречивые цифры: хранить отдельные snapshots и конфликт качества, не усреднять молча.
- Невалидные отрицательные метрики и будущее время публикации помечать; source controls retention и derived analytics.

## 6. Миграция №002 (проект, ещё не существует)
Не ломаем текущие `videos/creators/discoveries/metric_snapshots/jobs`.

| Новое/изменённое | Для чего |
| --- | --- |
| `search_runs` | Run id, ниша, старт/финиш, статус, counts, конфигурация и версия planner |
| `source_requests` | Каждая реальная API-операция, sort, cursor, latency, quota cost, provider response code, next cursor |
| `discoveries.run_id` | Связь одной находки с поисковым запуском |
| `creator_topics` | Автор может относиться к нескольким нишам с quality/confidence |
| `video_topics` | Видео может относиться к нескольким нишам и получать label/reason |
| `monitor_targets` | Что проверяем, priority, cadence, last/next check |
| `source_quota_ledger` | Бюджет запросов и сбросы по временным окнам провайдера |
| `data_quality_flags` | Детектированные проблемы метрик, провайдера, полноты |
| `human_labels` | Ручная разметка: релевантность, причина исключения, оценка качества |
| `sources.capabilities` | Список действительно поддерживаемых методов; policy gate |

**Дополнительно**: индексы по run_id, topic_id, monitor next_due, TTL; разные права записи в producer и read-процессах при необходимости.

**Защита очереди:** existing `jobs` полезен, но в миграции добавить `lease_token` / `lease_generation` и строгую проверку токена на completion/failure. Иначе просроченный воркер теоретически может завершить повторно взятую задачу при совпадении worker_id. Не считать existing job runner полноценным планировщиком — scheduler ещё не написан.

## 7. Search Planner — входы и алгоритм
```json
{
  "topic_id": 1,
  "platforms": ["youtube"],
  "languages": ["ru","en"],
  "region": "NL",
  "queries": ["AI automation","ChatGPT workflow"],
  "hashtags": ["aitools"],
  "watch_creators": [],
  "published_after_hours": 168,
  "max_search_calls_per_day": 20,
  "max_results_per_run": 250,
  "continuous_monitoring": false,
  "dry_run": true
}
```
**Planner**: (1) validate; (2) check source capabilities/rights and daily quota; (3) reserve budget for watchlists, keywords and exploration; (4) emit bounded paginated jobs; (5) persist exact config+version; (6) stop on quota; (7) report skipped channels and why.

Начальные лимиты — конфигурация пилота, не фактическое обещание платформы. Например 20 поисковых вызовов/сутки при отдельном YouTube лимите 100. Не строить 10 запросов * 3 сортировки * 5 страниц без учёта квоты. На успех запроса влияют unique relevant yield и coverage, а не просто число скачанных страниц.

Все ручные и авто запросы должны иметь: `run_id`, query, request id, query origin, source, pagination checkpoint и метрики исполнения.

## 8. Background processing
Предлагаемые types: `discovery.plan`, `discovery.search_page`, `discovery.hydrate_videos`, `discovery.fetch_creator`, `discovery.snapshot_metrics`, `discovery.revisit_creator`, `discovery.cleanup_expired`, `discovery.finalize_run`.

- Локальный отдельный CPU-worker, **не HTTP request thread**.
- 1 worker + bounded concurrency для первого релиза; PostgreSQL выполняет dedup и retry.
- 429 обрабатываем через `Retry-After`, плюс backoff/jitter. 401/403 не повторять бесконечно. Quota exhausted -> pause до сброса лимита.
- Ошибка единичной карточки не отменяет весь запуск. Задание получает reason+attempts и ссылку на run.
- Kill / restart посреди страницы не теряет всю сессию: cursor + job idempotency + DB transaction.
- Отказ БД: не терять молча результаты и не показывать успешный run; API сообщает degraded/unavailable.
- UI никогда не ждёт завершения большого поиска синхронно.

## 9. HTTP API, проект
Все endpoint local-only, с существующими проверками Host/Origin/`X-ContentMachine`; секреты не выводятся.
| Endpoint | Смысл |
| --- | --- |
| `GET /api/intelligence/sources` | Какие источники настроены; capabilities и статусы, без токенов |
| `POST /api/intelligence/search-runs` | Новый run; возвращает run_id и статус queued |
| `GET /api/intelligence/search-runs?id=...` | Прогресс, counts: fetched/unique/valid/relevant/partial/failed и diagnostics |
| `GET /api/intelligence/videos?topic_id=...&cursor=...` | Постраничные карточки + provenance |
| `GET /api/intelligence/videos/{id}/trace` | История обнаружений, snapshots, источники и ошибки |
| `POST /api/intelligence/search-runs/{id}/cancel` | Отменить дальнейшие jobs, не прерывая транзакции |
| `POST /api/intelligence/monitor-targets` | Включить/изменить наблюдение за автором |
| `GET /api/intelligence/diagnostics` | DB/job/source/quota status без секретов |
| `POST /api/intelligence/human-labels` | «Релевантно/не релевантно» для оценки поиска |

Route shapes предлагаются, но должны вписываться в текущий `BaseHTTPRequestHandler`.

## 10. UI Discovery: обязательно видно
**A. Настройка** — ниша, слова, исключения, языки, регионы, источники, аккаунты, бюджет, кнопка `Сухой запуск`.
**B. Запуски** — дата, программа-план, источник, `queued/running/completed/partial/blocked/failed/cancelled`, количество вызовов, расходы и прогресс.
**C. Результаты** — карточки/таблица: платформа, автор, URL, опубликовано, наблюдали, просмотры, лайки, комментарии, shares (или `нет данных`), причина находки, отметка Shorts `confirmed/likely/unknown`.
**D. Раскрытие карточки** — `Почему найден?`, query, source, request, точность, snapshot history, human label.
**E. Диагностика** — quota remaining, последние ошибки, состояние watchers, источник выключен/нет прав.
Без голой кнопки `Искать` и спиннера без объяснения того, что происходит.

## 11. KPI, реальная проверка качества
- `unique_valid_per_100_calls`, `unique_relevant_per_100_calls`, `duplicate_rate`
- `precision_at_50` на человеческой разметке; `new_creator_yield`; распределение авторов и тем.
- `field_coverage` по views/likes/comments/shares, `freshness_latency` по observed vs published.
- `source_error_rate`, `429_count`, `request_quota_used`, `cost_per_usable_candidate`.
- Не объявлять глобальный Recall без истинного полного набора платформы. В пилоте измерять на ограниченном контрольном наборе.

## 12. План инкрементов с критерием приёмки
| Этап | Реализовать | Приёмка |
| --- | --- | --- |
| D0. Architecture sign-off | Этот документ, решение об источниках, бюджете и политике | Согласованы контракты, boundaries и первая платформа |
| D1. DB+Replay MVP | 002, нормализатор, импорт fixture/manual URL, trace UI | 100 fixture rows -> корректные записи, второй импорт без дублей, `null != 0` |
| D2. YouTube API pilot | Adapter + quota+poll+source tracing; запросы и watchlist | Реальный run: список, ссылки, счётчики, виден error+quota; проверка Shorts как `candidate` |
| D3. Scheduler | Повторные snapshots, creator monitoring, policy TTL, durable resume | Stop worker/restart -> задачи возобновляются без дублей; 429 с backoff |
| D4. TikTok/Instagram | Тестовые адаптеры только для разрешённых источников | Поля/качество/лицензии подтверждены, неработающие capabilities явно выключены |
| D5. Search Quality | human labels, query expansion, измерения A/B | Улучшение precision/yield доказано против baseline keyword-only |
| Next block. Analytics | Формулы + батчи + обучаемые модели | Отдельная спецификация и политика derived metrics |

**Проверки**: pytest/unittest на fixture контракты, PostgreSQL integration в GitHub Actions, CLI smoke на локальном ПК, mock ошибки 401/429/5xx, старт без API токена, Stop/Resume, source restrictions, schema rollback strategy, секреты вне JSON/logs, matching source IDs. CI без настоящих API ключей. Случаи с внешним API отмечаем отдельно как live smoke (не выдавать CI mock за реальный запуск).

## 13. Решения до старта написания сборщиков
1. Пилотная ниша, RU/EN, целевые регионы и исключаемые категории.
2. Принять YouTube как первый реальный источник, подтвердить наличие доступного API ключа и разрешённый use case.
3. Доступность Meta/TikTok: пользовательские разрешения или платный лицензированный провайдер; выбор после теста.
4. Ограничения на стоимость, количество вызовов/сутки и максимальное число кандидатов в день.
5. Точка хранения приватных credentials, настройки и состояние источников (не в GitHub).
6. Нужна ли продвинутая YouTube-аналитика; если да — одобрение соответствующего режима. До этого custom Viral Score на YouTube API отключён.

## Архитектурные запреты V1
- Нет неразрешённой авторизации, обхода лимитов, капчи, блокировок.
- Нет «полного сканирования TikTok/Instagram» без лицензированного и проверенного источника.
- Нет неявного скачивания чужого видео.
- Нет записи `views=0` для неизвестных просмотров.
- Нет незаметного смешивания показаний разных провайдеров.
- Нет скрытого монолитного скрипта: модули разбиты, запуск имеет ID и проверяемый журнал.
- Нет Cloud LLM для подсчёта API-статистики. На этапе V1 Discovery GPU не требуется.
- Нет опасных перезаписей текущих Downloader модулей.
