# ContentMachine

**Локальное приложение для исследования короткого видеоконтента:** управление проектами и документами, загрузка отдельных публикаций Instagram / TikTok / YouTube, субтитры, ручной каталог Discovery и Research Taxonomy Builder с проверкой человеком и контролем расходов на OpenRouter.

**Состояние `main`:** проверено 23 октября 2026, интеграционный merge-коммит `24257d7` (PR #14–#17). Историческое развернутое состояние за 22 октября: [CURRENT_STATE](docs/CURRENT_STATE_2026-10-22.md). Это работающий локальный прототип с отдельными реализованными подсистемами, **не готовая end-to-end фабрика контента**. Фактические возможности и неопределённости подробно: [Актуальное состояние](docs/CURRENT_STATE_2026-10-22.md). [Карта документации](docs/DOCS_INDEX.md).

## Реализовано / не реализовано

| Подсистема | На `main` сейчас | Чего это **не** означает |
| --- | --- | --- |
| Downloaders | Instagram Reels/post, TikTok video/profile, YouTube Shorts/channel: получение доступных публичных URL/видео, локальное хранение, MP4 и транскрипты | Не массовый поиск трендов; внешние платформы могут отказать в доступе |
| Projects | Мастер из 9 шагов, просмотр/редактирование профиля, ревизии и вложения на локальном диске | Сохранение анкеты не запускает LLM или Discovery автоматически |
| PostgreSQL | Локальная PostgreSQL 17, версионированные миграции, источники, метаданные, задания и структуры аналитики | Наличие таблиц не означает наличие автоматического анализатора |
| Discovery D1 | Ручной импорт URL/CSV, нормализация, дедупликация, provenance, очередь, локальный каталог, диагностика и human labels | **Нет** production-поиска YouTube/TikTok/Instagram, живых метрик и подтверждённых 10 000 роликов/месяц |
| Research Taxonomy Builder | Подтверждение анкеты, Pipeline Studio, каталог OpenRouter, смета, согласие на бюджет, ручные LLM-стадии, Human Review, сохранение версии и связь с SRE | Не анализирует реальные видео и не использует содержимое прикреплённых документов в промптах |
| Hierarchical Taxonomy | Опциональные два прохода Opus 5.5, два Human Review, проверки покрытия/пробелов/дубликатов, дерево и публикация | Реальный платный E2E всей двухпроходной цепочки **не подтверждён** |
| SRE / Relevance | Контракты, Python-логика, миграции, записи экспериментов и связь с опубликованной Taxonomy | Полностью подключённый production-классификатор реальных видео пока не завершён |
| Video Collector | Исследование интеграций и отдельный черновой PR #10 с offline preview | В `main` пока нет платного/живого Apify-коннектора |
| Performance/Group A/B/Analytics | Структуры и ограничения для будущей аналитики | Не готовый автоматический подсчёт viral/breakout score и анализ медиаконтента |

Дальнейшие компоненты Content Production / Batches и масштабный сбор — **планы**, а не работающий путь от идеи до публикации.

## Установка и запуск

### Windows: установка один раз

1. Установите Python **3.10+** и Git. Запустите `INSTALL_CONTENTMACHINE_ONCE.bat`. Установщик клонирует репозиторий в `%LOCALAPPDATA%\ContentMachine`, создаст ярлык запуска при входе в Windows и вызовет `RUN_AUTO.bat`.
2. Откройте `http://127.0.0.1:8765`. Сервис слушает **только localhost**. Автообновление сверяет `origin/main` примерно раз в 60 секунд и не переписывает локальные изменения отслеживаемых файлов.
3. Если уже работает прежний экземпляр на порту 8765, сначала остановите его. Для диагностики установленной копии и checkout GitHub Desktop: `CHECK_CONTENTMACHINE_WINDOWS.bat`.

Для запуска из текущей папки без автообновления есть `START_CONTENTMACHINE.bat`. Для macOS/Linux существует `START_MAC_LINUX.sh` (ручной запуск, без Windows-автоустановщика).

**Важно:** прежде чем включать новые платные LLM-операции, учитывайте известный риск автообновления: наблюдатель занятости не отслеживает активные Taxonomy OpenRouter requests. Не полагайтесь на него как на гарантию отсутствия перезапуска во время длинного платного запроса; см. [технические ограничения](docs/CURRENT_STATE_2026-10-22.md).

### PostgreSQL: нужна для Discovery D1 и Research Taxonomy

1. Установите и запустите Docker Desktop (на Windows с WSL2).
2. Из папки репозитория запустите `SETUP_POSTGRES_WINDOWS.bat`. Он создаст локальный игнорируемый `.env`, развернёт PostgreSQL **17** и применит миграции.
3. Проверьте `http://127.0.0.1:8765/api/intelligence/health` — при правильной настройке `database` должен быть `connected`.
4. Альтернативные команды диагностики: `python -m intelligence.health`, миграции: `python -m intelligence.migrate`, статус контейнера: `docker compose ps`.

База доступна по `127.0.0.1:54329`. Настройка БД **не обязательна** для существующих Downloaders и сохранения черновиков проектов, но нужна для подтверждения Taxonomy-профиля, Pipeline/Run и импортов Discovery.

### OpenRouter и локальная транскрибация

Укажите OpenRouter API key в **Параметры** локального сайта. Для Downloaders общий ключ хранится в игнорируемом файле `worker/openrouter_key.txt`, расходы — в `worker/openrouter_usage.json`. В отсутствие доступного облачного распознавания можно использовать локальный faster-whisper; для downloaders учёт расходов локальный, а не подтверждённый баланс провайдера.

В Taxonomy Builder каждый LLM-вызов выполняется **после отдельного пользовательского действия**. Система запрашивает каталог и цены OpenRouter, рассчитывает примерную стоимость, требует отдельного согласия на бюджет, записывает фактическое usage и блокирует автоматические оплачиваемые повторы, если результат запроса неопределён. Не приравнивайте предварительную смету к гарантированному чеку.

## Основные пользовательские сценарии

### 1. Загрузить видео или транскрипт

Выберите Instagram / TikTok / YouTube Downloader, вставьте ссылку, нажмите **Найти**, затем **Скачать**. Результаты сохраняются в отдельных каталогах:

- `~/Downloads/InstagramArchive/`;
- `~/Downloads/TikTokArchive/`;
- `~/Downloads/YouTubeArchive/`.

В зависимости от платформы и состояния доступа будут сохранены MP4, SRT, TXT, JSON/метаданные. YouTube позволяет отдельно выбирать источник cookies, язык и способ транскрибации. Ни один метод не гарантирует обход ограничений внешних платформ.

### 2. Создать проект

В **Мои проекты → Новый проект** заполните 9 шагов. Доступны ниша, цель, продукт, реальные функции, проблемы аудитории, конкуренты, аккаунты, примеры видео и локальные файлы исследований (не более 10 файлов, каждый до 10 МиБ, суммарно до 24 МиБ).

- Windows: `%LOCALAPPDATA%\ContentMachineData\projects\<project-id>\`;
- macOS/Linux: `~/.contentmachine/projects/<project-id>/`;
- переопределение: `CM_PROJECTS_DIR`.

Данные профиля хранятся в `project.json`, вложения — в `research/`. Это отдельное хранилище **вне Git checkout**; обновление приложения не является резервной копией проектов.

### 3. Создать Research Taxonomy

1. Подтвердите текущую ревизию анкеты (**для текущего Builder нужна ниша Health & Fitness и обязательные сведения о продукте/аудитории**).
2. В **Taxonomy Pipeline Studio** выберите модели для Fact Extraction → Taxonomy Generation → optional Critic, настройте reasoning/temperature/max output, сохраните и утвердите Pipeline.
3. Ознакомьтесь со сметой, укажите лимит и явно разрешите расходы. Создайте Run — создание Run бесплатно.
4. Вручную запустите Fact Extraction; проверьте, исправьте или отклоните **каждый** факт. Затем вручную запустите генерацию тем, при необходимости Critic.
5. После проверки категорий человеком опубликуйте неизменяемую версию Research Taxonomy в PostgreSQL (с SRE projection).
6. Опционально вместо плоского построения выберите двухпроходное дерево **direction → branch → hypothesis**: отдельные оплачиваемые запросы Opus, Human Review 1/2, Quality Gate, до двух дополнительных отдельно разрешённых проверок.

**Прикреплённые документы не читаются моделями в этом pipeline.** Темы — исследовательские гипотезы и классификационные критерии, а не найденные тренды/реальные подтверждённые ролики. Подробнее: [Пошаговый Test Studio](docs/TAXONOMY_BUILDER_IN_PRODUCT_TESTS.md) и [Двухпроходное дерево](docs/TAXONOMY_TWO_PASS_HIERARCHY.md).

### 4. Ручной Discovery (D1)

Через раздел **Discovery** добавьте URL либо CSV, задайте тему; PostgreSQL-worker нормализует ссылки, удаляет дубликаты, записывает источник/политику, версии метаданных, импорт и замечания. Доступны каталог, история, диагностика и ручные метки. Данные от сторонних провайдеров и живой поиск **не включаются** от одного импорта. План внедрения: [D0 Master Spec](docs/DISCOVERY_ENGINE_D0_MASTER_SPEC.md) и [Исследование Video Collector](docs/VIDEO_COLLECTOR_V1_RESEARCH_2026-10-21.md).

## Архитектура и исходники

- `app.py`: единый локальный HTTP-сервер `127.0.0.1:8765` и API.
- `site/`: HTML/CSS/vanilla JS для Downloaders, Projects, Discovery, Taxonomy Studio и Human Review.
- `worker/`: загрузка, транскрибация и локальные задания.
- `projects.py`: локальные проекты и версионированные анкеты.
- `intelligence/discovery/`: ручные импорты и обработка каталога D1.
- `intelligence/taxonomy_builder/`: pipeline, каталог, бюджет, LLM runner, deep taxonomy и публикации.
- `intelligence/relevance/`: SRE-контракты, хранилище и алгоритмическая база.
- `intelligence/migrations/`: расширения схемы PostgreSQL.
- `tests/`: Python, PostgreSQL integration и Node.js UI проверки.
- `docs/`: спецификации, отчёты и исследования, в том числе исторические.

Сервер проверяет локальные Host/Origin и заголовок `X-ContentMachine: 1` для изменений. **Это однопользовательское localhost-приложение**; публиковать его в сеть без отдельной аутентификации/авторизации нельзя.

## Проверки без платных API-вызовов

На машине с нужными зависимостями:

```bash
python -m unittest discover -s tests -v
python -m compileall -q app.py projects.py worker intelligence
node --check site/app.js
node --check site/discovery.js
node --check site/projects.js
node --check site/taxonomy-studio.js
node --check site/taxonomy-cost.js
node --check site/taxonomy-run.js
node --check site/taxonomy-deep.js
node tests/test_taxonomy_deep_ui.cjs
```

PostgreSQL integration-тестам требуется **отдельная тестовая БД** и `CM_TEST_PG_DSN`. Не направляйте разрушаемые test fixtures на реальную базу. Обязательные CI-проверки описаны в `.github/workflows/tests.yml` и `windows-d1-smoke.yml`. Наличие тестов не равнозначно подтверждённому PASS для текущего SHA; документация по прежним запускам относится только к их коммитам.

## Search Planner: Foundation, Generation Engine и бюджет

Проектируем автоматический Search Planner после **опубликованной immutable Research Taxonomy**. AI Query Generator получает **полный HypothesisContext**: поля `id/parent_id/kind/relation/name/definition/include/exclude/rationale/examples/source_fact_ids` гипотезы, её темы и (если существует) подтемы, плюс тексты human-approved facts и search-policy. Он предлагает структурированные keyword/hashtag запросы; **Query Validation** отдельно проверяет релевантность, дубли и разнообразие (embeddings/MMR), затем Registry/Task Planner выбирают инструмент и задачи Collector. Генератор сам не ищет видео.

В `main` уже есть read-only RTB Foundation, миграция `015` и Stage A — Run/Batch Planning. Stage B (смета, отдельное согласие и финансовый ledger) реализован в отдельном PR; реальных LLM вызовов пока нет. Для чтения реализованного первого этапа: [Search Planner Foundation](docs/SEARCH_PLANNER_FOUNDATION_IMPLEMENTATION_V1.md). **Документация реализуемого Stage B:** [Cost Estimator + Budget Manager](docs/SEARCH_PLANNER_ENGINE_IMPLEMENTED_STAGE_B.md). **Полная архитектура:** [Generation Engine v1](docs/SEARCH_PLANNER_GENERATION_ENGINE_V1_FULL_SPEC.md) (PostgreSQL, финансы, батчи, исполнение, восстановление и API; **пока проект**, не рабочий код). Отдельно: [AI Query Generator — семантический pipeline](docs/SEARCH_PLANNER_AI_QUERY_GENERATOR_V1.md), [реализация ThinkQE / MuGI / Jina](docs/SEARCH_PLANNER_AI_QUERY_TECH_IMPLEMENTATION_V1.md), [Raw LLM Schema](docs/SEARCH_PLANNER_AI_QUERY_RAW_LLM_V1.schema.json), [HypothesisContext Schema](docs/SEARCH_PLANNER_HYPOTHESIS_CONTEXT_V1.schema.json), [Query Candidates Schema](docs/SEARCH_PLANNER_AI_QUERY_CANDIDATES_V1.schema.json). Следование чужим исследованиям не означает гарантированный выигрыш на TikTok.

## Разработка, открытые PR и документация

- [PR #14: AI Query contracts и research](https://github.com/BanSins7/ContentMachine/pull/14) — **объединён с `main`**; спецификация, не запущенная генерация.
- [PR #15: RTB Input Loader + HypothesisContext](https://github.com/BanSins7/ContentMachine/pull/15) — **объединён с `main`**; Python-код read-only Foundation.
- [PR #16: Generation Engine full spec и документационная очистка](https://github.com/BanSins7/ContentMachine/pull/16) — **объединён с `main`**; технический контракт без оплачиваемого выполнения.
- [PR #17: Generation Engine Stage A](https://github.com/BanSins7/ContentMachine/pull/17) — **объединён с `main`**; миграция `015`, Run/Batch Manager, idempotency, тесты. Budget/worker/OpenRouter ещё не реализованы.
- [PR #6: Discovery SRE foundation](https://github.com/BanSins7/ContentMachine/pull/6) — **открытый draft**, не считать целиком объединённым, хотя часть родственной SRE-структуры уже есть в `main`.
- [PR #10: Video Collector preview](https://github.com/BanSins7/ContentMachine/pull/10) — **открытый draft**, offline-планировщик/просмотр результатов, без живого Apify.
- PR #7, #8 и #9 **объединены** с `main` 21 октября 2026. Не путать с формулировками в исторических этапных документах.
- Документ [RTB release gate от 21 октября](docs/RTB_RELEASE_GATE_2026-10-21.md) фиксирует **историческое** состояние CI до merge; сам merge уже состоялся, отдельная полная проверка релиза остаётся необходимой.

Начните с [CURRENT_STATE_2026-10-22.md](docs/CURRENT_STATE_2026-10-22.md) и [DOCS_INDEX.md](docs/DOCS_INDEX.md). Датированные требования и старые QA-отчёты сохранены как снимки истории; их ранние формулировки «ещё не реализовано» не описывают автоматически текущий `main`.
