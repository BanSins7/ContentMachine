# Карта документации ContentMachine

**Обновлено 22.10.2026**, базовая ветка `main` @ `d0c8eca`. Главный вход — [README](../README.md). Текущее подтверждаемое состояние — [CURRENT_STATE_2026-10-22.md](CURRENT_STATE_2026-10-22.md). Документы ниже имеют **разные назначения и даты**: исторические спецификации сохраняются ради точности истории, не заменяя состояние `main`.

- [DTK_CONNECTOR](DTK_CONNECTOR.md) — подключение self-hosted Evil0ctal v5 к Discovery, доступные операции, конфигурация и ограничения (отдельный инструмент, не автоматический D2 поиск).

## Основные инструкции (использовать сейчас)

| Документ | Назначение / актуальный статус |
| --- | --- |
| [README](../README.md) | Запуск, обзор модулей, ограничения, карта маршрутов |
| [CURRENT_STATE_2026-10-22](CURRENT_STATE_2026-10-22.md) | Источник текущих статусов, PR, реализованного/планируемого и рисков |
| [TAXONOMY_BUILDER_IN_PRODUCT_TESTS](TAXONOMY_BUILDER_IN_PRODUCT_TESTS.md) | Рабочий пользовательский сценарий Flat Builder, manual live calls |
| [TAXONOMY_TWO_PASS_HIERARCHY](TAXONOMY_TWO_PASS_HIERARCHY.md) | Рабочий опциональный deep flow (код есть в main; live Opus E2E не подтверждён) |
| [TAXONOMY_WORKSPACE_UX](TAXONOMY_WORKSPACE_UX.md) | Принципы текущего review UI |

## Исторические этапы уже объединённого Taxonomy Builder

- [BLOCK1_ARCHITECTURE](TAXONOMY_BUILDER_BLOCK1_ARCHITECTURE_V1.md) — исходное проектирование 20.10; отдельные описания будущего **уже устарели** после merge #7–9.
- [BLOCK2_INTEGRATION](TAXONOMY_BUILDER_BLOCK2_INTEGRATION_V1.md) — профиль / подтверждение; функция в main.
- [BLOCK3_PIPELINE](TAXONOMY_BUILDER_BLOCK3_PIPELINE_V1.md) — конфигурация моделей и pipeline; функция в main.
- [BLOCK4_STUDIO](TAXONOMY_BUILDER_BLOCK4_STUDIO_V1.md) — редактор; функция в main.
- [BLOCK5_COST](TAXONOMY_BUILDER_BLOCK5_COST_V1.md) — котировки/согласие; функция в main.
- [BLOCK6_RUNNER](TAXONOMY_BUILDER_BLOCK6_RUNNER_V1.md) — ручные LLM-этапы и review; функция в main.
- [INTAKE_CONTRACT](TAXONOMY_BUILDER_INTAKE_CONTRACT_V1.md) — контракт хранения/анкеты, в main.
- [BLOCK6_LIVE_SMOKE 20.10](TAXONOMY_BUILDER_BLOCK6_LIVE_SMOKE_2026-10-20.md) — **историческое доказательство ОДНОГО real API call**, не полного E2E.

## Discovery: спецификации и QA

- [DISCOVERY_ENGINE_D0_MASTER_SPEC](DISCOVERY_ENGINE_D0_MASTER_SPEC.md) — проектный master-plan D0; D1 в main, D2–D7 не реализованы целиком.
- [DISCOVERY_ENGINE_ARCHITECTURE_V1](DISCOVERY_ENGINE_ARCHITECTURE_V1.md) — исходный план live-сборщиков; **не реализация**.
- [DISCOVERY_ENGINE_V1_10000_PER_MONTH](DISCOVERY_ENGINE_V1_10000_PER_MONTH.md) — целевой KPI; **не подтверждённый результат**.
- [DISCOVERY_D1_1_QA](DISCOVERY_D1_1_QA.md), [DISCOVERY_D1_QA_REPORT](DISCOVERY_D1_QA_REPORT.md), [D1_QUALITY_AUDIT 18.10](DISCOVERY_D1_QUALITY_AUDIT_2026-10-18.md) — история D1, выводы QA на датированных коммитах.

## База данных и контракты хранения (структуры, а не готовые анализаторы)

- [VIDEO_FEATURES_A_SCHEMA_V1](VIDEO_FEATURES_A_SCHEMA_V1.md) — схема видеопризнаков A.
- [CAROUSEL_FEATURES_A_SCHEMA_V1](CAROUSEL_FEATURES_A_SCHEMA_V1.md) — фото/смешанные карусели A.
- [CONTENT_FEATURES_B_SCHEMA_V1](CONTENT_FEATURES_B_SCHEMA_V1.md) — группа B, source-first.
- [PERFORMANCE_C_DATABASE_V1](PERFORMANCE_C_DATABASE_V1.md) — структура C/score, не запущенный скоринг.

## Исследования, проектные решения и release history

- [PLATFORM_CONNECTORS_YOUTUBE_TIKTOK_INSTAGRAM 21.10](PLATFORM_CONNECTORS_YOUTUBE_TIKTOK_INSTAGRAM_2026-10-21.md) — research после merged PR #11, не новые production адаптеры.
- [VIDEO_COLLECTOR_V1_RESEARCH 21.10](VIDEO_COLLECTOR_V1_RESEARCH_2026-10-21.md) — план v1, на main не реализован; draft PR #10 отдельно содержит offline preview.
- [RTB_EXTERNAL_ALGORITHMS 21.10](RTB_EXTERNAL_ALGORITHMS_2026-10-21.md) — сравнение алгоритмов, не результаты живого анализа.
- [RTB_RELEASE_GATE 21.10](RTB_RELEASE_GATE_2026-10-21.md) — **исторический** gate до фактического merge PR #9; не считать нынешним статусом ветки.

## Правило работы с документацией

Каждый новый документ должен указывать **дату, SHA/PR, тип доказательства** (источник, unit, PostgreSQL fake-provider, Windows smoke, live API), текущий статус **main / PR / research** и точные ограничения. Инструкция по конкретному блоку может описывать старый момент времени, но не вправе отменять живой код и актуальный [CURRENT_STATE](CURRENT_STATE_2026-10-22.md). Реальный live-расход требует явного согласия владельца.
