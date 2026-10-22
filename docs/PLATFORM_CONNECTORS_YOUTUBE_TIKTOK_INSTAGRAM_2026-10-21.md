> **Актуализация на 22.10.2026:** документ описывает проектные решения и/или состояние на дату создания, а **не полный перечень готовых функций `main`**. В `main` реализован только offline/manual Discovery D1; автоматический поиск внешних платформ D2+, KPI 10 000 новых видео в месяц и live Video Collector не подтверждены и не запущены. Исследовательский PR #11 уже merged, draft PR #10 ещё не merged. См. [CURRENT_STATE_2026-10-22.md](CURRENT_STATE_2026-10-22.md).

---

# ContentMachine — YouTube, TikTok, Instagram: полная карта интеграций
Дата исследования: 21.10.2026
Статус: **исследование и проектное решение**. Код интеграций официальных API и коммерческого поиска в этом PR не создаётся. Живые вызовы источников и платные запуски не выполнялись.

## 0. Источники знания и точность статусов

**Из собственных материалов проекта**: `content_factory_summary_2026-10-17.txt` определяет исследование как три элемента: 3) сбор видео/статистики/транскриптов, 4) тренды, 5) библиотека форматов. `TikTok_Scraper_Apify_Research_2026-10-18.txt` исследовал Evil0ctal/Douyin_TikTok_Download_API, omkarcloud/tiktok-scraper и davidteather/TikTok-Api, а также функции Apify Clockworks. Тот документ отдельно утверждает, что **живые запросы к TikTok не выполнялись**, поэтому перечисленные API — не доказательство доступности на ПК владельца.

**В реальном main (проверено 21.10.2026)**:
- `app.py` + `worker/tiktok_worker.py`, `worker/youtube_worker.py` и Instagram-маршруты `app.py`: существующие загрузчики по известным ссылкам/профилям.
- `worker/requirements.txt`: `yt-dlp>=2026.8.19`, `faster-whisper>=1.1.0,<2`, `imageio-ffmpeg`; `worker/openrouter_transcribe.py` отдельно делает платные транскрипции с локальными лимитами/Whisper fallback.
- `intelligence/discovery/service.py`: D1 — **офлайн URL/CSV импорт**, нормализация, дедупликация, provenance/rights, история, очередь, diagnostics, UI. Поисковый live API нет.
- `intelligence/relevance/*`: контракты SRE, правила классификации, DB. Не подменять этим автоматическую обработку тысяч реальных видео.
- RTB PR #9 в main — опубликованная вручную иерархическая таксономия для направления поисков.
- PR #10 содержит **отдельный пока НЕ ОБЪЕДИНЁННЫЙ** offline Video Collector preview, не платный live Actor.

**Из внешних публичных источников (проверено 21.10.2026)**:
возможности библиотек/официальных API сверены ниже. «Заявлена операция» не равно «получены реальные данные в нашем окружении». Ссылки в конце.

## 1. Возможности по режимам доступа

Не обещать «считать все метрики» без различия:
1. Публичные метрики **чужого** контента (обычно views/likes/comments/shares, когда доступны) — одна область прав.
2. Приватные Insights **своего авторизованного** аккаунта (watch time, retention, охват, демография, доходы там, где API даёт) — другая. Нужны OAuth, права продукта/ревью приложения.
3. Метрики для внешней коммерческой аналитики через провайдера — **третья**, со своими договорами и retention.
4. Субтитры платформы и транскрипция локальным Whisper не одно и то же. Transcript/ASR возможен только когда доступны и разрешены входные данные.

| Операция | YouTube | TikTok | Instagram |
|---|---|---|---|
| Поиск видео по ключевому слову | Официальный YouTube Data API `search.list` | Нет универсального коммерческого keyword search в Display API; лицензированный поставщик (например Apify) или спорный community connector | Meta hashtag search / Business Discovery для Professional accounts в ограниченном объёме; произвольный поиск всех Reels не обещать |
| Видео/авторы по URL/ID | YouTube Data API `videos.list` / channels | Display API для согласившегося автора; внешние данные у провайдера | Meta API для собственных/доступных профессиональных аккаунтов; конкурентные данные ограничены |
| Публичные views/likes/comment counts | `videos.list(statistics)` с nullable полями | Display API для владельца / сторонний источник для чужого контента | Доступность и поля определяются Professional API и конкретным media type/доступом; альтернативный лицензированный источник |
| Комментарии с текстом и replies | `commentThreads.list` + `comments.list` (пагинация) | Research API не подходит для коммерческого продукта; поставщик или неофициальные скрейперы требуют отдельного решения о законности | Meta API: управление комментариями своих профессиональных media; чужие — ограниченно, не обещать все |
| Видео MP4 / аудио | существующий `yt-dlp` для доступного и **разрешённого** видео | существующий `yt-dlp`; альтернативные самохостируемые решения — только после rights review | существующая загрузка по известным ссылкам; `Instaloader`/`gallery-dl` как дополнительные варианты |
| Исходные субтитры | `yt-dlp --write-subs/--write-auto-subs` или `youtube-transcript-api` best-effort; официальный captions.download только при праве редактировать видео | Research `voice_to_text` не коммерческий доступ; из других источников — только если реально предоставляют | Универсального endpoint для субтитров чужих Reels нет |
| ASR, когда медиа законно получено | локальный `faster-whisper` (уже есть) | тот же `faster-whisper` | тот же `faster-whisper` |
| Удержание, watch time, аудитория, монетизация | YouTube Analytics API **для авторизованного канала**, не конкурента | Нет в Display API чужих видео | Instagram Insights для авторизованного Professional account; поля и сроки ограничены |
| Публикация от своего имени | Официальный YouTube API; отдельная область Distribution | TikTok Content Posting API после одобрения app/scopes | Instagram Content Publishing API для Professional accounts |
| Повторные снимки показателей | Наша очередь + API/договорный провайдер | Провайдер/владелец, не гарантируется реальное время | Владелец/договорный провайдер, не гарантируется реальное время |

«Полные комментарии» и «все метрики» — **не гарантируемая** функциональность на произвольном чужом видео: удалённые, скрытые, ограниченные или недоступные по API данные нельзя восполнить выдуманными нулями.

## 2. Открытые библиотеки и выбор по платформам

### Общая основа: yt-dlp + faster-whisper (уже установлены)

- https://github.com/yt-dlp/yt-dlp — видео, медиа-потоки, доступные субтитры, метаданные по URL. Не заменяет keyword search API, массовый учёт комментариев или private Insights.
- https://github.com/SYSTRAN/faster-whisper — локальная распознавалка аудио; в ContentMachine уже есть. Предпочтительно не делать повторный облачный ASR без согласования стоимости и правил.

### YouTube — официальный слой как основной

- **`google-api-python-client` + YouTube Data API**: `search.list`, `videos.list`, `channels.list`, `playlistItems.list`, `commentThreads.list`, `comments.list`. Проверять квоты и затраты каждого метода в актуальной консоли проекта Google.
- **YouTube Analytics API**: более глубокие авторские показатели только после OAuth данного канала. Нельзя получать удержание конкурентов по ключу Data API.
- https://github.com/jdepoix/youtube-transcript-api — если субтитры доступны, best-effort получение обычных/автоматических субтитров с временными интервалами; не официальный YouTube Data API, возможны ограничения/IP blocking.
- https://github.com/egbertbouman/youtube-comment-downloader — резервный community source комментариев, не нужен там, где официальный comments API подходит.
- `yt-dlp` уже делает скачивание по разрешённому известному URL.

### TikTok — коммерческий поиск отдельно от владельческих аккаунтов

- **Официальный TikTok Display API**: OAuth согласившегося пользователя, `/v2/user/info/`, `/v2/video/list/`, `/v2/video/query/`, включая доступные public counters авторизованных видео. Это **не** общий движок поиска чужих TikTok.
- **Официальный TikTok Research API** действительно имеет поиск видео, комментарии и `voice_to_text`, но по TikTok Research Tools доступен только квалифицированным независимым/некоммерческим исследованиям; для коммерческого ContentMachine этот путь считать недоступным.
- https://github.com/Evil0ctal/Douyin_TikTok_Download_API — внешний **самостоятельный сервис** REST/MCP/CLI с задачами, поиском, профилями, комментариями, загрузкой и PostgreSQL; в публичной документации v5 это десятки операций. **Не копировать чужой DB/worker/identity pool в нашу систему**: слишком много дублирования, а обращение к внутренним интерфейсам/сигнатурам платформы и хранение удалённых публикаций несут отдельные риски.
- https://github.com/omkarcloud/tiktok-scraper — API/исходный код с search + metadata. Описание 41 endpoint в проектном исследовании от 18.10 — историческая оценка; актуальное публичное README позиционирует и хостинговое коммерческое API, поэтому проверить self-hosting/no-cost условия и код конкретного релиза, прежде чем выбрать.
- https://github.com/davidteather/TikTok-Api — Python/Playwright: search, user posts, hashtags, comments/replies, video metadata. Неофициальный доступ через веб-сессии: стабильность и договорные права отдельно; не делать встроенным обязательным production dependency.
- **Apify Clockworks TikTok Scraper**: https://apify.com/clockworks/tiktok-scraper — платный внешний адаптер для поисков/авторов/хештегов/комментариев/субтитров, если тарифы и условия хранения соответствуют задаче. Имеется только offline-plan PR #10; нет выполненного платного live test.

### Instagram — официальные владельческие данные + отдельный discovery

- **Meta Instagram API (Facebook Login / Instagram Login в зависимости от продукта)**: свои Professional аккаунты, media, comments/replies, Insights, publishing, hashtag search и Business Discovery в **определённых** разрешениями границах. Для чужих частных/личных аккаунтов полного доступа нет. Требуются Instagram Business/Creator, Meta App, permissions и review для расширенного использования.
- https://github.com/instaloader/instaloader — умеет известные публикации/профили, фото/видео, подписи, некоторые hashtag feed/сторис и скачивание комментариев `--comments` при разрешённом login. Не официальный Meta API, есть `429` и проблемы с сессиями.
- https://github.com/subzeroid/instagrapi — методы поиска Reels/аккаунтов/хештегов, comments, Insights, media, upload, но используется private mobile API (чувствителен к обновлениям и логину). Для коммерческого ядра не выбирать, пока не пройдены security/rights/economic review.
- https://github.com/mikf/gallery-dl — дополнительный downloader медиа/публикаций; не источник insights. Разработка мигрировала на Codeberg.

## 3. Предлагаемый контракт `PlatformConnector`

Отдельный connector **без прямого SQL**; `Discovery D1` — единственное хранилище истины.

```text
Approved RTB taxonomy
 -> query planner (direction/hypothesis -> queries with stable ids)
 -> [YouTubeOfficialSearch | TikTokLicensedSearch | InstagramProfessionalDiscovery]
 -> raw response + provider version + request provenance + rights policy
 -> normalize/validate/dedup/retention (Discovery D1)
 -> optional enriching:
    (1) public counters snapshot
    (2) comments + replies with pagination
    (3) official captions -> authorized media -> local Whisper
 -> SRE classification + explicit unmatched
 -> Performance C / Video Features A, B (следующие блоки)
```

Методы адаптера (capability-gated):
`capabilities(identity,rights) -> enum supported/denied/unavailable/unknown`;
`search(query,cursor,limit)`;
`get_video(video_id)`;
`list_author_posts(author,cursor)`;
`get_metric_snapshot(video_id,observed_at)`;
`list_comments(video_id,cursor,limit)`;
`get_replies(comment_id,cursor,limit)`;
`get_caption_tracks(video_id)`;
`get_media(video_id)`;
`get_account_insights(account,dates)`.
Для write-операций (publish, reply, moderate) **отдельный** Distribution adapter и явное подтверждение владельца.

Стандартный результат:
`{platform, platform_video_id, canonical_url, author_id?, published_at?,
 title?, caption?, duration_s?, metrics?:[{name, value, observed_at, provider}],
 comments?:[{id,parent_id?,text,created_at,like_count?}],
 captions?:[{language,provenance,native_or_asr,segments}],
 source_id, policy_version_id, licensing_scope, fetched_at, schema_version,
 source_run_id?, raw_response_hash, eligibility, errors[]}`.

Требования:
- `0` только если источник явно сообщил реальный ноль; неизвестный счётчик = `null`.
- Нельзя утверждать, что выборка комментариев полна: хранить `next_cursor`, `fetched_count`, `total_hint`, `is_complete` только если провайдер это подтверждает.
- `observed_at` обязателен для метрик; timestamp публикации отдельно.
- Отдельные права `metadata`, `comments`, `media`, `transcription`, `analytical_derivatives`; чужой пользовательский контент не превращать в бессрочный архив без прав.
- OAuth токены в локальном секретном хранилище, не в HTML или URL; токен пользователя не выдавать внешним неподконтрольным сервисам.
- Динамические quotas/rate-limits, ограничение параллельных запросов, backoff на 429, circuit breaker на недоступные источники.
- Не выполнять «автоматический обход» CAPTCHA, подписи устройства, приватных аккаунтов, IP-bans или чужой авторизации.
- Платный внешний `POST`: одна idempotency/reservation запись ДО запроса; сетевой unknown -> `uncertain`, опрос/сверка по существующему provider run id, без автоматического повторного POST.
- Если право на медиа не подтверждено, поиск/метаданные могут продолжаться, но скачивание и ASR выключены.

## 4. Как закрывать блок без переписывания существующих загрузчиков

**Этап P0 — интеграционная основа (без сети, без платных вызовов):**
- `connectors/{contracts,registry,normalizer}` и DB schema для account connections, comments, caption tracks, metric snapshots, provider runs, rights, provenance.
- Внесение approved RTB tree в search planner; описание permission/capabilities и reasons для каждой платформы.
- Unit fixtures по трем платформам: null метрики, pagination, deleted video, 429, HTML challenge, stale policy, dedup.
- Существующий `yt-dlp`/Whisper остаётся отдельно используемым для **разрешённых** скачиваний.

**Этап P1 — первый реально работающий read-only YouTube:**
- Официальный YouTube Data API: поиск, video details/stats, posts по каналам, comments+replies, cursor/quotas.
- Субтитры: разрешённые оригинальные треки, затем Whisper только на доступных с разрешениями исходниках.
- Сохранение в D1 + SRE. Критерий: 20–50 живых результатов, dedup, комментарии, право/происхождение каждой записи, повторный metric snapshot, по согласованному ключу/квоте.

**Этап P2 — TikTok + Instagram с ограниченным live пилотом:**
- TikTok: RTB -> Apify offline preview -> explicit budget/rights approval -> оплачиваемый Actor async -> metadata, search, comments if licensed -> D1. Предварительно сравнить три open-source реализации на тестовых публичных данных, без предположения, что любая из них разрешена.
- Instagram: официальный OAuth для своего Professional аккаунта, media/insights/comments; hashtag/business discovery для третьих лиц в пределах политики. Для чужих Reels сначала проверить доступность источника и условия, без обещаний глобального keyword search.
- Пилот с 10–30 объектами на платформу, отдельно доля missing fields, комментарии и cost-per-qualified.

**Этап P3 — фоновые задания, наблюдаемость и коммерческая пригодность:**
- Supervision worker + per-provider budgets and quotas + retries only on безопасные read calls, cancellation, TTL cleanup, rights revocation.
- UI: три вкладки Платформы / Поиск / Видеокарточка; операции видны лишь по реально разрешённым capabilities.
- Мониторинг снимков статистики, конкурентов, cost-per-qualified и полноты комментариев, затем SRE+Performance C.
- QA: Windows+Linux PostgreSQL (mock source) и отдельно живой consent-based smoke. На первых тестах **не обещать 10k qualified/month**.

## 5. Явные нерешённые внешние зависимости

1. Google Cloud project + YouTube Data API key; OAuth для своего YouTube Analytics позднее.
2. Meta app + профессиональный Instagram account; соответствующие разрешения/одобрение приложения.
3. TikTok Display OAuth для собственных видео; для поиска чужих видео нужен договор с поставщиком или разрешённый исследовательский доступ.
4. Источник прав на сохранение текста чужих комментариев, медиа и сроки удержания данных в коммерческом приложении.
5. Для Apify — текущий тариф, endpoint, строгая верхняя оценка платных действий до запуска. **Ключи/токены не записывать в git и логи**.

## 6. Референсы

История проекта: `TikTok_Scraper_Apify_Research_2026-10-18.txt`, `content_factory_summary_2026-10-17.txt`, `docs/DISCOVERY_ENGINE_D0_MASTER_SPEC.md`, `docs/VIDEO_COLLECTOR_V1_RESEARCH_2026-10-21.md`.

Официально:
- https://developers.google.com/youtube/data/reference/rest/v3/search/list
- https://developers.google.com/youtube/data/reference/rest/v3/videos/list
- https://developers.google.com/youtube/v3/guides/implementation/comments
- https://developers.google.com/youtube/data/reference/rest/v3/captions/download
- https://developers.google.com/youtube/analytics/reference/reports/query
- https://developers.tiktok.com/docs/en/display-api-overview
- https://developers.tiktok.com/products/research-api/
- https://www.postman.com/meta/instagram/folder/u4g5a2a/instagram-api-with-facebook-login
- https://www.postman.com/meta/instagram/collection/6yqw8pt/instagram-api

Open source:
- https://github.com/yt-dlp/yt-dlp
- https://github.com/SYSTRAN/faster-whisper
- https://github.com/jdepoix/youtube-transcript-api
- https://github.com/egbertbouman/youtube-comment-downloader
- https://github.com/Evil0ctal/Douyin_TikTok_Download_API
- https://github.com/omkarcloud/tiktok-scraper
- https://github.com/davidteather/TikTok-Api
- https://github.com/instaloader/instaloader
- https://github.com/subzeroid/instagrapi
- https://github.com/mikf/gallery-dl
