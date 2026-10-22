# ContentMachine — фактическое состояние на 22.10.2026

> **Источник:** статический обзор `main` @ `d0c8eca43230da93c117c6aa4cca537795736ab2` (последний merge 21.10.2026). Этот документ — **сводка проверенного исходного кода и GitHub-статусов**, а не результат запуска приложения на Windows пользователя или полного live E2E. При новом commit, изменении статуса PR или запуске тестов актуализируйте дату и SHA. Исторические спецификации не переопределяют реальную текущую ветку.

## Уровни готовности

- **Есть в main:** маршрут API/исходники/миграция/интерфейс присутствуют. Это **не** гарантия успешного запуска при любых настройках.
- **Проверено ранее:** есть датированный отчёт или ранее завершённый тест на конкретном SHA; нельзя автоматически переносить PASS на `main`.
- **Draft PR:** изменения вне `main`, использовать только по отдельному checkout и только в соответствии с описанным объёмом.
- **Research / future:** спецификация либо исследование, не утверждать как работающую систему.

## Что находится в main

| Область | Файлы / граница | Статус и смысл |
| --- | --- | --- |
| Локальное приложение | `app.py`, `site/index.html` и `site/*.js`/`*.css` | Реализован однопользовательский сайт `127.0.0.1:8765` с GET/POST API; без облачного SaaS-хостинга |
| Instagram/TikTok/YouTube | `worker/local_worker.py`, `worker/tiktok_worker.py`, `worker/youtube_worker.py`, `worker/openrouter_transcribe.py` | Downloaders, верификация URL/video ID, локальные MP4/субтитры; ограничения доступа третьих площадок сохраняются |
| Projects | `projects.py`, `site/projects.js` | 9-шаговая анкета, создание/редактирование, локальное сохранение файлов, `profile_revision`/hash, mirror в БД при включении |
| PostgreSQL | `docker-compose.yml`, `intelligence/migrate.py`, `intelligence/migrations/*` | PostgreSQL 17, аддитивные миграции, контроль SHA и транзакционная установка |
| Discovery D1 | `intelligence/discovery/{service,normalize,importers,http_api,kpi}.py` | URL/CSV offline-import, видеокаталог, provenance, источник/права, дедупликация, очередь, trace, human labels; нет D2 real platform search |
| Taxonomy Builder (flat) | `intelligence/taxonomy_builder/{intake,catalog,pipeline_repository,cost_repository,run_repository,provider}.py` | Подтверждение анкеты, версия Pipeline, OpenRouter, смета/согласие, ручные оплачиваемые стадии, факт/тема human review и публикация |
| Hierarchical Builder | `deep_contracts.py`, `deep_repository.py`, `site/taxonomy-deep.js`, `014_tb_deep_hierarchical_taxonomy.sql` | Два прохода Opus, две Human Review, quality checks, версия дерева и SRE projection; нет подтверждённого полного **реального** paid Opus E2E |
| Relevance / SRE | `intelligence/relevance/*`, миграции `006–011_sre*`, `sre_bridge.py` | Контракты, классификационный pipeline с injected adapters и хранение; не production-система массовой классификации реальных роликов |
| Group A/B/Performance C | миграции `003_video_features_a`, `004_carousel_feature_a`, `005_content_features_b`, `003_performance_c` и др. | БД/constraints/schema, не автоматический извлекатель всех визуальных/производительных признаков |
| Auto Update | `auto_update.py`, `RUN_AUTO.bat` | Fast-forward `main`, период 60 сек, валидация синтаксиса, рестарт/откат при некоторых ошибках |

### Преднамеренно не реализовано целиком

- D2–D7 Discovery: живой поиск YouTube/TikTok/Instagram через платный/официальный API, регулярный мониторинг статистики, подтверждённый KPI 10 000 качественных новых видео в календарный месяц.
- Video Collector production: нет живого Apify Actor-dispatch, согласованных прав на медиа/комментарии, API quota/budget policy и полноценного ingestion в `main`.
- Полный SRE content-classification от найденного внешнего видео до проверенного production результата и измеренной точности.
- Автоматическая multimodal feature extraction Group A/B, полнофункциональные Performance Analytics, Batches, генерация/монтаж/публикация видео.
- Мультипользовательская авторизация, публичный HTTP-хостинг, управляемая внешняя инфраструктура.
- Анализ содержимого PDF/Word/Excel/других вложений Taxonomy Builder: файлы **только сохраняются**; в текущие LLM-промпты не передаются.

## История интеграции PR

| PR | Статус на дату обзора | Практический вывод |
| --- | --- | --- |
| [#7 Taxonomy Blocks 1–6](https://github.com/BanSins7/ContentMachine/pull/7) | merged 21.10.2026 | Flat Builder и live runner **в main**, хотя старые `BLOCK2–6` документы писались при draft |
| [#8 Taxonomy Workspace](https://github.com/BanSins7/ContentMachine/pull/8) | merged 21.10.2026 | UI Human Review **в main** |
| [#9 Two-pass Opus](https://github.com/BanSins7/ContentMachine/pull/9) | merged 21.10.2026 | Deep Builder **в main**; тестовый release gate перед merge фиксировал нерешённые CI-проблемы |
| [#11 Platform integrations research](https://github.com/BanSins7/ContentMachine/pull/11) | merged 21.10.2026 | Исследовательская документация; **не** автоматическая установка новых официальных коннекторов |
| [#6 Discovery SRE](https://github.com/BanSins7/ContentMachine/pull/6) | open draft | Не считать **весь PR** выпущенным. Некоторые файлы SRE есть в main из параллельной интеграции |
| [#10 Video Collector offline preview](https://github.com/BanSins7/ContentMachine/pull/10) | open draft | Только вне main: без оплаты/провайдерных вызовов |

## Архитектура данных и поток

```text
9-step project intake (local project.json + research/)
     | confirm profile revision (PostgreSQL)
     v
Taxonomy Pipeline Studio -> cost quote -> separate budget consent
     | manual LLM call: Fact Extraction -> Human Review facts
     | manual LLM call: Taxonomy Generation (+ optional Critic)
     | final Human Review
     +--> immutable flat Taxonomy + SRE projection
     \--> optional deep Opus 1 -> Human Review 1
                            -> Opus 2 [up to 2 optional manual expansions]
                            -> Quality Gate -> Human Review 2
                            -> immutable tree + SRE projection

Offline URL/CSV import -> Discovery D1 -> PostgreSQL videos/occurrences
                                   -> trace / labels / diagnostics

Future: approved Taxonomy -> external Video Collector -> SRE -> Analytics
        [NOT automatically connected end-to-end in current main]
```

Содержимое источников и версии профилей различаются: `project.json` хранится локально, PostgreSQL хранит подтверждения/версии и запуск/финансовый журнал. Приложение должно корректно работать в деградированном режиме без БД для Downloaders и черновиков, но не для Taxonomy workflow.

## Проверки, подтверждения и ограничения QA

- В репозитории есть Linux workflow `.github/workflows/tests.yml` (Python 3.12, PostgreSQL 17, Python+JS проверки) и Windows `windows-d1-smoke.yml`.
- Отчёт `docs/DISCOVERY_D1_QUALITY_AUDIT_2026-10-18.md` документирует **113 пройденных тестов** на старом SHA, **не текущий CI**.
- `docs/TAXONOMY_BUILDER_BLOCK6_LIVE_SMOKE_2026-10-20.md` документирует один реальный запрос DeepSeek Fact Extraction через OpenRouter с учётом расхода; **не полный живой Taxonomy E2E**.
- `docs/RTB_RELEASE_GATE_2026-10-21.md` фиксирует проблему GitHub Actions с `steps=[]` в PR #9. PR #9 был позже **merged**. Не считать это доказательством успешного прогонa CI для текущего `main`.
- Offline/PostgreSQL tests с fake provider могут подтверждать состояние автомата и контракты без расхода средств; они **не доказывают качество реального Opus**.
- Для итогового релизного заключения нужны Linux PostgreSQL CI и Windows smoke на точном проверяемом SHA, ручная проверка UI и отдельно санкционированный live E2E, если он нужен.

## Риски, требующие решения

1. **P0 — AutoUpdate vs paid requests.** `auto_update.is_busy()` смотрит `jobs`/`discovery` из `/api/state`, не состояние `tb_runs`/`tb_deep_runs`. Длинный оплачиваемый вызов может попасть под рестарт. Не заявлять безопасность обновлений для всех LLM-run до исправления.
2. **P0 — Release QA.** Нужно выяснить состояние GitHub Actions runner, прогнать актуальные тесты и не выдавать старую документацию за текущий PASS.
3. **P1 — Real collection.** Нужны разрешения провайдеров, смета, quotas, capped executions, дедупликация, provenance, хранение и отзыв прав на данные.
4. **P1 — Draft SRE PR #6.** Проверить конфликты миграций/контрактов с `main` и единую authoritative схему Taxonomy/SRE.
5. **P1 — Deep estimation.** Flat quote исторически не считает все дополнительные Opus-проходы; UI предупреждает, но нужна единая явная смета по выбранному режиму.
6. **P2 — Backups.** Раздельно резервировать `project.json`/`research/`, PostgreSQL Docker volume, а при необходимости локальные архивы видео/транскриптов.
7. **P2 — Developer/documentation clarity.** Не путать документированные планы D2–D7/Group A/B/Performance C с действующими вычислительными конвейерами.

## Проверка и обновление этого документа

Источники: дерево `main`, `app.py`, `auto_update.py`, `intelligence/discovery`, `intelligence/taxonomy_builder`, `intelligence/relevance`, `site`, `tests`, `.github/workflows` и GitHub PR #6–11.

При изменениях:
1. Определить текущий SHA `main` и статусы PR.
2. Проверить точные файловые пути/API, назвать тестовый SHA и тип теста.
3. Обновить таблицу возможностей и рисков, отдельно указать новые API/модели/расходы.
4. Затем синхронизировать `README.md`, `docs/DOCS_INDEX.md` и соответствующую рабочую инструкцию. Не переписывать задним числом старые результаты тестов.
