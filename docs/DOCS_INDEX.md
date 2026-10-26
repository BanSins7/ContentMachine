# Карта документации ContentMachine

**Обновлено 24.10.2026**; `main` @ `28804ad` после PR #25. Search Planner Stage A–D включительно в `main` (нет подтверждённого live paid E2E). Query Validation объединён с `main` (PR #27), Linux/Windows PostgreSQL CI пройдены; производственная модель эмбеддингов требует отдельной установки. Главный вход — [README](../README.md). Текущее подтверждаемое состояние — [CURRENT_STATE_2026-10-22.md](CURRENT_STATE_2026-10-22.md). Документы ниже имеют **разные назначения и даты**: исторические спецификации сохраняются ради точности истории, не заменяя состояние `main`.

## Project Source Intelligence
- [Stage 11 — Project Context Assembly & Immutable Snapshots](PROJECT_CONTEXT_ASSEMBLY_STAGE11.md): deterministic profile/evidence/output source pinning, immutable JSON and SHA-256 snapshots, preview/build/compare/manifest/selection APIs and human full/partial approval. No LLM or automatic RTB publication.
- [Stage 10 — Evidence Normalization & Source Grounding](PROJECT_EVIDENCE_NORMALIZATION_STAGE10.md): profile-field version pins, grounded document/web/ASR/OCR/Vision/Fusion citations, independent exact-duplicate occurrences, reviewable conflict/lexical suggestions, evidence verification and human review. No model calls or RTB auto-approval.
- [Stage 09 — Timeline Fusion & Video Intelligence JSON](PROJECT_TIMELINE_FUSION_STAGE09.md): deterministic, fully pinned ASR+OCR+frame-scoped Vision timeline; immutable per-project versioned output, evidence provenance, coverage and unapproved creative hypotheses; no calls to providers and no automatic Group A/B writes.
- [Stage 08 — Multimodal Vision Engine](PROJECT_MULTIMODAL_VISION_STAGE08.md): bounded FFmpeg sampling, shot cuts, optional local OCR, source-pinned visual-scene outputs and explicitly consented Sonnet 4.6 image analysis with fail-safe provider accounting. Stage 09 fusion remains separate.
- [Stage 07 — Unified Transcription](PROJECT_UNIFIED_TRANSCRIPTION_STAGE07.md): SRT/TXT/ASR -> Transcript JSON v1; verified video hashes, monotonic timestamps and explicit speech statuses; durable local queue and optional local ASR without paid calls.
- [Stage 06 — Video Downloader Integration](PROJECT_VIDEO_DOWNLOADER_INTEGRATION_STAGE06.md): durable project source queue, existing TikTok/YouTube/Instagram exact downloader adapters, verified archive MP4 + metadata/SRT/TXT, cache, cancellation, restart and tests. No new platform downloader.
- [Stage 05 — Web Source Extractor](PROJECT_WEB_SOURCE_EXTRACTOR_STAGE05.md): Sonnet 4.6 + OpenRouter server-side web_fetch, explicit priced consent, durable single-attempt jobs, immutable model-reported text and provenance; no direct site fetch.
- [Stage 04 — Document Extraction Engine](PROJECT_DOCUMENT_EXTRACTION_STAGE04.md): local PDF/DOCX/TXT/MD/CSV/XLSX parsers, durable queue, exact locators, parser cache, optional local OCR and QA; no LLM automatic actions.
- [Stage 03 — Research File Library](PROJECT_RESEARCH_FILE_LIBRARY_STAGE03.md): safe local uploads, immutable versions, SHA256, legacy import, archive/restore and repair. No paid work.

- [Stage 02 — Project Reference Registry](PROJECT_REFERENCE_REGISTRY_STAGE02.md): project-scoped links, canonical IDs, idempotent legacy import, CRUD/archive, offline QA. Web AI processing (Sonnet 4.6 + OpenRouter web_fetch) begins only in a later stage.
- [Stage 01 — Architecture](PROJECT_SOURCE_INTELLIGENCE_ARCHITECTURE_V1.md): strict DTO, append-only evidence/context foundation.

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

## Search Planner — Foundation, Generation Engine, AI Query (разные стадии)

- **[Query Validation v1 — реализация, PostgreSQL, embeddings/MMR, QA](SEARCH_PLANNER_QUERY_VALIDATION_V1.md)** — **код в `main`, PR #27**; atomic generation-outbox consumer, deterministic quality gate, test fixtures isolated from production.
- [Validated Queries handoff JSON Schema](SEARCH_PLANNER_VALIDATED_QUERIES_V1.schema.json) — серверный контракт для будущего Task Planner.

- [Stage D — OpenRouter + ThinkQE/MuGI/Jina](SEARCH_PLANNER_ENGINE_IMPLEMENTED_STAGE_D.md) — код Stage D в отдельном PR, version pins, exact request quotes, strict JSON, one-shot HTTP, fake/mock QA; без live paid test.

- [Generation Engine Stage C — Worker/Output/Recovery](SEARCH_PLANNER_ENGINE_IMPLEMENTED_STAGE_C.md) — **объединён с main (PR #21)**; полный fake-only pipeline, строгое JSON и outbox.
- [Generation Engine Stage B — Cost & Budget](SEARCH_PLANNER_ENGINE_IMPLEMENTED_STAGE_B.md) — **реализовано в `main` (PR #19)**: quote+consent, price pinning, Decimal reserve/settle, uncertain, revoke; без OpenRouter/worker.
- **[Generation Engine v1 — FULL SPEC](SEARCH_PLANNER_GENERATION_ENGINE_V1_FULL_SPEC.md)** — **главный контракт полной целевой архитектуры; частично реализован Stage A в PR #17**: 11 сущностей PostgreSQL, конфигурации, quote/consent, state machines, worker, атомарные бюджетные транзакции, uncertain/reconciliation, outbox, API/UI, тесты и критерии приёмки.
- [Search Planner Foundation PR #15](SEARCH_PLANNER_FOUNDATION_IMPLEMENTATION_V1.md) — **Python-код в `main` (PR #15)**: read-only approved RTB loader + полный HypothesisContext, offline тесты и PostgreSQL integration; выполнен в `main`.
- [AI Query Generator overview](SEARCH_PLANNER_AI_QUERY_GENERATOR_V1.md) — **спецификация в `main` (PR #14)**: граница RTB → structured candidates → Query Validation; смысловая генерация, **без отдельной** конкурирующей финансовой системы.
- [ThinkQE / MuGI / Jina implementation](SEARCH_PLANNER_AI_QUERY_TECH_IMPLEMENTATION_V1.md) — **design PR #14**: адаптации исследовательских методов, raw trace, промпты и offline оценки, без работающего LLM-кода.
- [HypothesisContext input Schema](SEARCH_PLANNER_HYPOTHESIS_CONTEXT_V1.schema.json) — immutable полный контекст, все 11 RTB полей (ссылочные проверки — Python).
- [Raw LLM output Schema](SEARCH_PLANNER_AI_QUERY_RAW_LLM_V1.schema.json) — модельные facets/sketches/lexical hints и поисковые запросы.
- [Query Candidates handoff Schema](SEARCH_PLANNER_AI_QUERY_CANDIDATES_V1.schema.json) — серверный результат для будущего Query Validation.

**Статусы:** PR #14, #15, #16, #17 объединены с `main`. Foundation и Generation Engine Stage A (миграция/планирование запусков) существуют в коде; бюджетные операции уже объединены в `main` (PR #19); fake-only worker/output/recovery уже в main (PR #21). Реальный LLM AI Query, owner HTTP/UI и Query Validation не реализованы. Платного OpenRouter Search Planner E2E нет. Старый дублирующий `SEARCH_PLANNER_AI_QUERY_TECHNOLOGIES_V1.md` удалён из активной ветки после сведения уникальных идей в подробный `TECH_IMPLEMENTATION_V1`; история Git сохраняется.

## Исследования, проектные решения и release history

- [PLATFORM_CONNECTORS_YOUTUBE_TIKTOK_INSTAGRAM 21.10](PLATFORM_CONNECTORS_YOUTUBE_TIKTOK_INSTAGRAM_2026-10-21.md) — research после merged PR #11, не новые production адаптеры.
- [VIDEO_COLLECTOR_V1_RESEARCH 21.10](VIDEO_COLLECTOR_V1_RESEARCH_2026-10-21.md) — план v1, на main не реализован; draft PR #10 отдельно содержит offline preview.
- [RTB_EXTERNAL_ALGORITHMS 21.10](RTB_EXTERNAL_ALGORITHMS_2026-10-21.md) — сравнение алгоритмов, не результаты живого анализа.
- [RTB_RELEASE_GATE 21.10](RTB_RELEASE_GATE_2026-10-21.md) — **исторический** gate до фактического merge PR #9; не считать нынешним статусом ветки.

## Правило работы с документацией

Каждый новый документ должен указывать **дату, SHA/PR, тип доказательства** (источник, unit, PostgreSQL fake-provider, Windows smoke, live API), текущий статус **main / PR / research** и точные ограничения. Инструкция по конкретному блоку может описывать старый момент времени, но не вправе отменять живой код и актуальный [CURRENT_STATE](CURRENT_STATE_2026-10-22.md). Реальный live-расход требует явного согласия владельца.
