# RTB-FIX-00 — проверенный аудит и заморозка границ модуля
Дата: 2026-10-26. Репозиторий: BanSins7/ContentMachine.
Ветка разработки: feature/rtb-module-v2. База до разработки: main@218f5f3163ccc7179a4809ba4386a1d1b6ff80f5.
PR: #32 (Draft). Старый ограниченный PR #31 закрыт без merge, его начальные файлы унаследованы.

## Область завершения RTB-FIX-00

Это фиксация реально существующих интерфейсов/связей и проверяемые контрактные тесты, не функциональная реализация Deep V2.
НЕ создано: paid Deep C-run, дополнительная SQL-таблица или миграция, OpenRouter вызовы, Human Review V2, публикация V2, Search Planner V2.
Обязательный критерий закрытия: новое и историческое тестирование в CI; до его прохождения статус QA — ожидает подтверждения.

## 1. Уже существующий RTB в базовом main

| Подсистема | Авторитетные файлы | Наличие | Риски для V2 |
|---|---|---|---|
| Intake | intelligence/taxonomy_builder/intake.py | V1 готов, questionnaire confirmation | Не использовать legacy fact-extraction для уже утверждённых B9 знаний |
| Model catalog | catalog.py, pipeline_contracts.py | OpenRouter snapshot, capability checks | Закреплять тарифы, контекст и идентичность Opus 5.5 |
| Pipeline Studio | pipeline_repository.py, site/taxonomy-studio.js | Отдельные версии и human approval | Новый B9 mode не переписывает сохранённые legacy pipeline |
| Cost/budget | cost_estimator.py, cost_repository.py, site/taxonomy-cost.js | Quote + consent + charge | Не присваивать разрешение от B3/Flat другим paid стадиям |
| Flat engine | run_contracts.py, run_repository.py, site/taxonomy-run.js | Flat extraction/generation/reviews; optional critic | Прежние публикации неизменяемы |
| Deep V1 | deep_contracts.py, deep_repository.py, site/taxonomy-deep.js | Opus first/second, 2 reviews, quality, <=2 expansions | Использует _reviewed(conn,pid,rid) и deep-v1 (11 полей) |
| A approved context | context_binding.py | Ранее введён дополнительный подтверждённый A input mode | Он НЕ является B9 knowledge snapshot |
| B Stage14 Flat C | knowledge_binding.py, knowledge_execution.py | Уже имеет независимые B9 quote, consent и однопроходный flat taxonomy-v2 | Это НЕ Deep V2 и не два Deep Human Reviews |
| RTB/SRE | sre_bridge.py | Transactional mirror of approved taxonomy to SRE | INSERT sre_facts без reuse => конфликты повторных B9 fact UUID |
| Deep SQL | intelligence/migrations/014_tb_deep_hierarchical_taxonomy.sql | tb_deep_runs, attempts, reviews, immutable publications | Нельзя модифицировать историю или затронуть V1 constraints |
| B C SQL | intelligence/migrations/053_ke_c_qualified_execution.sql | knowledge_c_generation_* ledger | Не приклеивать 2-pass Deep к единственному flat attempt без миграции |
| HTTP glue | app.py, GET lines ~331-400; POST ~517-625 | Project-scoped endpoints /taxonomy-builder/* и /deep/{start,advance,review1,review2} | Shared app.py — только минимальное подключение собственного нового обработчика при нужде |
| UI | site/taxonomy-{studio,cost,run,deep}.js + site/taxonomy-workspace.css | Flat/Deep V1 | Не изменять shared site UI чужих модулей |

В Legacy Flat продукте есть отдельное извлечение фактов. В B9 mode эта стадия должна быть отключена; B8 уже дал verified human-approved факты.

## 2. Подтверждённые схемы A8 и B9 из GitHub

Project Source Intelligence A8:
- intelligence/project_sources/context_service.py: _snapshot содержит context_id (UUID), project_id, context_sha256, approval, context payload.
- intelligence/project_sources/timeline_fusion.py: результат video-intelligence/v1 с evidence словарём, а не самостоятельным observations[] array. Evidence keys: speech / frame / scene / ocr / model_vision, с source_job_id, locator, trust, timestamps. Sampled visual frame не доказывает полноценную характеристику сцены.
- intelligence/knowledge_extraction/intake.py: PostgresAResolver.resolve_video проверяет approved snapshot, pinned VideoAnalysisPin, project/source/version/output/SHA до возврата оригинального output. C обязан вызывать именно такой verified reader, а не просто доверять dict из HTTP.

Knowledge Extraction B9:
- intelligence/knowledge_extraction/knowledge_snapshot.py: build_snapshot -> knowledge-snapshot/v1, builder_version=b9-immutable/1.0.0, реальные факты facts[].fact_id являются SHA-256. Содержит citation provenance, reviewed_text, kind, source_role/scope/subject, human_approved, original B event SHA.
- Формула B9 fact SHA: canonical_sha({schema_version,submission_sha256,group_id,review_event_sha256}). Базовый C-FIX-00 теперь пересчитывает ID для детекции forged fact_id.
- build_c_handoff -> knowledge-c-handoff/v1, snapshot_id UUID, snapshot_sha256, facts, video_analysis_refs; video_to_mimo=false, independent_c_quote_required=true, independent_c_consent_required=true.
- B9 snap содержит context_id, context_sha256, profile_revision/hash и собственный human_review_status, но не hypothetical approved_facts[] from older C spec.
- Замораживается SHA самого snapshot; исходные facts, original A evidence и trust не изменяются.
- RTB/SRE source_fact_ids V2 будут проектным UUID5(adapter) поверх B9 SHA; исходный SHA сохраняется в mapping. Нельзя выбрать random UUID.

Реализация FIX-00: intelligence/taxonomy_builder/research_contracts_v2.py. Это pure Python без DB writes или provider requests. Проверяется полный B9 snapshot envelope, canonical SHA handoff, исходная формула fact_id, source metadata, immutable model call flags; индексируются неизменённые A evidence.
Ограничение: проверка разрешений/настоящего существования input в БД еще НЕ реализована — это RTB-FIX-01.

## 3. Legacy V1 vs новый V2

Legacy deep_contracts.py, NODE_PROPERTIES = 11: id,parent_id,kind,relation,source_fact_ids,name,rationale,definition,include,exclude,examples.
V2 (требования): 14 = V1 + source_observation_ids + search_signals + validation_focus. Детерминированная pure V2->V1 projection без удаления исходного V2.
V2 validation, source-observation whitelist, quality and publication => RTB-FIX-03/06/07, НЕ FIX-00.

Known divergence:
- deep_repository._stage всегда ставит deep-v1;
- deep_repository._input берет _reviewed из tb_fact_review_submissions, не B9;
- Search Planner input_loader.py INNER JOIN tb_fact_review_submissions и принимает ровно V1 schema;
- knowledge_execution реализует отдельный flat-путь, а не поддержку Deep 2-pass;
- sre_bridge inserts every fact each publication без проверки идентичного уже существующего UUID;
- knowledge_binding hardcodes health_fitness/maximize_views для старого Flat prompt, нельзя переносить это в новый Deep V2.

## 4. Source ownership, границы автономного RTB

RTB owns ONLY:
- intelligence/taxonomy_builder/** (все текущие и будущие внутренние файлы);
- site/taxonomy-*.js, site/taxonomy-workspace.css, scoped UX tests;
- tests/test_taxonomy*.py и изолированные RTB contract tests;
- docs/RTB_*.md, RTB migration files (новые additive);
- .github/workflows/rtb-module-contracts.yml;
- минимальный app.py route/bootstrap hook ТОЛЬКО когда иначе нельзя предоставить собственный API; эти common-file изменения выделять отдельно.

External inputs (read-only):
- intelligence/project_sources/* — контекст и immutable A videos;
- intelligence/knowledge_extraction/* — B9 approved snapshot и handoff.
External consumers:
- intelligence/relevance/* — SRE schema; RTB-owned bridge writes approved publication atomically, но не меняет internals SRE;
- intelligence/search_planner/* — RTB-owned read-only publication export, но не меняет internals Search Planner.
Не изменять internals collector, qualified_data, analytics, Project Source Intelligence и Knowledge Extraction.
Не pull/merge ветки иных модулей в RTB.

Git semantics: RTB branch — полный working tree, поэтому все V1 файлы уже есть в feature/rtb-module-v2 с ТЕМИ ЖЕ blob SHA, что и в main. Не дублировать пакет в новой директории. При интеграции diff — RTB-only новые файлы/правки, не весь исходный main.

## 5. Маршруты, тарифы и опасные побочные эффекты

Current app.py route ownership:
- GET /api/projects/{project}/taxonomy-builder/intake, /pipeline, /models, /cost, /context-preview;
- GET /api/projects/{project}/taxonomy-builder/runs, /runs/{id}, /runs/{id}/deep;
- POST intake/confirm, pipeline/draft, pipeline/approve, cost/quote, cost/consent, cost/revoke, runs start and advance, facts review, topics publish, deep/start|advance|review1|review2, model catalog refresh.
Никакой GET не должен вызывать Opus/OpenRouter.
Deep attempts and reviews persisted in PostgreSQL, explicit reserved/charged; uncertainty fail-closed and no duplicate payment. Test paths from prior PR #7/#9 preserved.

## 6. SQL migrations audit

intelligence/migrate.py loads SQL files sorted by full filename via glob [0-9]*.sql, with schema_migrations(version TEXT PRIMARY KEY, sha256 TEXT) and transaction advisory lock. Ключ миграции — **полное имя файла**, а не только число 016. Значит, совпадающий числовой префикс сам по себе НЕ ошибка. Но при объединении старых feature-веток новые файлы с префиксами 016/017 могут выполняться на уже обновлённой БД с зависимостями и именами таблиц из гораздо более поздних миграций. Это требует отдельного migration dry-run и проверки конфликтов объектов SQL, а не слепой массовой перенумерации.
- Existing legacy RTB migrations include 004..014, B9 adds 042..053, A adds 030..041.
- Отдельные ветки Collector, SRE, QDP, Analytics используют собственные 016/017 (и Analytics 018).
- RTB FIX-00 не создаёт SQL миграции. RTB-FIX-02/07 должны выбрать однозначные именованные additive migration paths после проверки актуального main и зависимостей. Не переписывать применённые миграции.

## 7. Тестирование и критерии закрытия

Offline tests: tests/test_taxonomy_c_corrector_contracts.py теперь строит действительно подписанные B9 факты через реальный upstream build_snapshot(), с двумя human-approved groups, одним pin video-intelligence/v1 и двумя A evidence entries. Тестируются 8 сценариев: metadata, role-preserving mapping, SHA->UUID stability/project isolation, malformed handoff/unapproved fact, forged ID recompute, removed snapshot envelope metadata, immutable original A observations, foreign/tampered/missing output, 11-field legacy tree projection.
Historical regressions: existing tests/test_taxonomy_deep_contracts.py, test_taxonomy_deep_db.py, test_taxonomy_builder_run.py, test_taxonomy_run_ui.cjs, test_taxonomy_deep_ui.cjs and all Python tests from .github/workflows/tests.yml.
New RTB own CI: .github/workflows/rtb-module-contracts.yml, Linux + Windows offline contract tests, no provider key/request. Existing ContentMachine checks runs PostgreSQL 17 migration + Python regression + Node/UI.
CI pending => not fully closed until a success on final commit. No live LLM/cost tests performed.

## 8. Work remaining and acceptance

RTB-FIX-01 will implement actual Postgres-bound materialization of B9+ A8, immutable C Research Input and fail-closed quotas. Only after that do real authorized C-runs, strict Deep V2, two human reviews, publication, Search Planner export and final acceptance happen (RTB-FIX-02..11).

CURRENT FIX-00 STATE: code, new regression and audit pushed to RTB feature branch; **QA final status depends on GitHub Actions conclusions**. The unit tests cannot be verified locally here without obtaining the full repository source; this must not be called a completed CI run until GitHub reports success.
