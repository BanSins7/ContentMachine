# CONTENT MACHINE — GROUP B FULL STANDALONE IMPLEMENTATION PLAN
## Независимый модуль Content & Story Intelligence (B1–B8)
**Редакция:** 2026-10-26 · GB-MOD/1.0 · **Статус:** implementation roadmap, не подтверждение выполненного кода.
**Repository:** `BanSins7/ContentMachine`. **Canonical branch:** `feature/group-b-full-module`.
**Baseline:** `main` @ `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` (проверить при каждом stage).
**Existing authority:** `docs/CONTENT_FEATURES_B_SCHEMA_V1.md`, `intelligence/migrations/005_content_features_b.sql`, `tests/test_content_features_b_schema.py` уже в main. Ни одно из них не переписывать.
**Owner:** только группа B; не дублировать QDP/Group A/SRE/Analytics/PSI и их миграции.

## 0. Миссия и граница модуля

Цель: самостоятельно разрабатываемая ветка, которая непосредственно перед merge в main предоставляет **исполняемый** B1–B8 Engine для существующей source-first PostgreSQL схемы. Вход: QDP подготовил публикацию группы B; реально завершённая и текущая версия Group A предоставляет замеренные сцены, ASR, OCR, кадры/слайды и audio evidence. Выход: версии `content_b_analyses` + дочерние B1–B8, следы evidence, Human Review и надёжный указатель Unified Analytics. Group C выполняется параллельно и не входит в Group B.

**Непреложные правила:**
1. **Один этап GB-MOD-NN = один пользовательский запрос «продолжай/следующий этап».** В начале читать этот файл и `docs/GROUP_B_IMPLEMENTATION_TRACKER.md`. Выполнять текущий этап полностью насколько позволяют зависимости; затем commit и progress update. Если код этапа готов, а тесты последними не прошли/не запускались, отмечать IMPLEMENTED + QA BLOCKED/NOT RUN, а не оставлять `[ ]` без объяснения. Формально завершать только с зелёными проверками.
2. Только ветка `feature/group-b-full-module`; **никогда не commit в main или чужие feature branches** и никогда не сливать автоматически. Если upstream изменился, документировать handshake, а не копировать его.
3. Authority за публикацию: `videos.id` BIGINT; `project_id` 32 lowercase hex; `qdp_run_id` UUID; A analysis ID BIGINT; source_id TEXT; policy/version и SHA источников — не подменять одно другим. QDP `ready` НЕ означает выполненную Group A, а `B completed` НЕ означает положительный C/Analytics.
4. Историческая `005_content_features_b.sql` уже в main и содержит валидаторы/триггеры. **Никогда не менять её содержимое или SHA**. Новые runtime таблицы и исправления — только additive forward-only migrations с безопасным порядком после независимых модулей. Текущий мигратор: `sorted(glob("[0-9]*.sql"))`; полное имя + SHA в `schema_migrations`.
5. Не фабриковать speech/OCR/visual, URL, CTA, FPS, время просмотра слайдов, AI-generated provenance; `not_run` / `partial` / `unavailable` отличать от измеренного `false` / zero. Если raw media недоступны или источник неразрешён — fail closed. Не считать модельное описание автоматически верифицированным фактом.
6. `content_kind=video|slideshow_video` → B content_type `video`; `photo_carousel|mixed_carousel` → `carousel`; `image_post` пока **не поддержан утверждённым контрактом B2**. Не называть одиночное изображение каруселью.
7. Смысловые B1/B2/B3/B4/B7/B8 — мультимодальная LLM при наличии подтверждённых capabilities; B5 — Python по данным A, B6 — OCR/ASR/video style, допускается LLM только при необходимости. Модель Group B **не является** вторым SRE: SRE Embeddings + Opus 5.5 классифицирует гипотезы, B классифицирует особенности контента. Модель/slug, multimodal, цену и limits подтверждать перед paid run. Нет обязательной Fast LLM.
8. Денежный лимит/approval перед любой платной операцией; silent retry ambiguous charge запрещён. Не тратить реальные деньги в CI или без явного операторского согласия. Secrets не сохраняются в JSON/логах; клиентское содержание не интерпретируется как инструкции.
9. Evidence должен быть anchored к выбранному A/media-объекту: source span/word/ocr/frame-id/slide/timestamp с SHA, model saw manifest, review state; fabricated evidence → quarantine/review. Один confident JSON ≠ verified fact.
10. Переиспользовать `intelligence/project_sources` и Group A только через проверенные adapters и hash/ID mapping; не путать project_source_ids с D1 videos.id. Контракты QDP/A/Analytics остаются owned другими ветками; absent/old port -> explicit unavailable.
11. Релиз only after CI, PG17, isolated Worker, mocked LLM, performance, manual/gold evaluation and merge diff проверены. Если реальная модель/production DSN недоступны, честно фиксировать blocker; не утверждать полную готовность.

## 1. Уже реализовано и НЕ должно переписываться
- `main`: все B1–B8 таблицы в `005_content_features_b.sql`; триггеры `b_validate_analysis`, `b_validate_component`, views `content_b_story_patterns`, `content_b_cta_summary`. Unit/PG fixture tests в `tests/test_content_features_b_schema.py`.
- `feature/group-a-full-module`: Group A technical extraction и `group-a-to-b/v1` draft/interface; standalone works in progress. Не импортировать напрямую пока не подтверждено capabilities.
- `feature/qualified-data-pipeline-v1-20261023`: QDP `qdp-handoff/v1`/outbox proposal & producer work in progress; provider-ready не гарантирует A complete.
- `feature/analytics-engine-qualified-pipeline-20261023`: `_b_payload`, `evaluate_qualification` read all B1–B8, require specific A linkage and no review-needed flags; branch separate from main.
- `main`: Project Source Intelligence, transcript/v1 и frame/visual-preparation/v1 пригодны только при явной canonical ID mapping.

## 2. Утверждённая семантика B1–B8 (freeze)
| Блок | Строго допустимое | Данные и invariant |
|---|---|---|
| B1 Hook | 10 mechanisms: problem, promise, curiosity, myth_busting, mistake, surprise, story, challenge, comparison, proof | Multilabel without primary; 5 nullable flags, 4 delivery, source-exact parts; seconds OR slide_index |
| B2 Format | 12 video primary+optional distinct secondary / 8 carousel formats | Исключить cross-type, unknown ≠ arbitrary new label |
| B3 Story | 26 roles, one per sampled frame/slide and semantic interval | Сохранять повторы, before_after_phase, bounded sample w/ actual frame IDs |
| B4 Visual | ai_generated nullable; person_visible, product_visible, logo_visible | Только реально измеренная визуальная запись, иначе нет строки |
| B5 Editing | cut_count, cuts_per_minute, avg_shot_duration, cuts_first_5_sec | single_take:0; slow >0 & <10; medium >=10 & <30; fast >=30; video only |
| B6 Subtitles | none/standard/dynamic + position/font_size/font_style/text_color/highlight_color/background_style/display_mode | video only; screen text on slide ≠ subtitles |
| B7 CTA | 0..N of 12 types; delivery speech/on_screen_text, 9 destinations | absence only after complete scan, otherwise unknown; do not invent URLs |
| B8 Topic | stable CATEGORY > TOPIC > optional SUBTOPIC hierarchy | distinct from Search Planner topics and research C hypothesis; review merges/new proposals |

### B3 video semantics
26 exact roles: hook, context, problem, consequence, goal, myth, mistake, cause, explanation, fact, tip, step, demonstration, comparison, example, proof, testimonial, product_intro, product_demo, before_after, result, reveal, summary, offer, cta, other.
Sampling guideline: ~2fps first 5s, ~1fps later, target ~60 frames; sampling coverage recorded; video technical cuts ≠ semantic story boundaries.

### B8 observed topic ≠ C Research Taxonomy
B8 describes topic of observed post, not the approved research hypotheses from C or SRE; never silently derive B8 from query text. Stable registry ID survives rename/merge with explicit review.

## 3. Ownership и физическая архитектура

```text
intelligence/group_b/
  __init__.py contracts.py errors.py configuration.py ports.py
  adapters/qdp.py adapters/group_a.py adapters/media.py
  context/manifest.py context/builder.py context/sampling.py
  providers/protocols.py providers/openrouter.py
  blocks/b1_hook.py b2_format.py b3_sampling.py b3_story.py b4_visual.py
         b5_editing.py b6_subtitles.py b7_cta.py b8_topic.py
  validation/schema.py validation/evidence.py validation/quality_gate.py
  persistence/repository.py
  review/service.py review/registry.py
  integration/analytics.py integration/outbox.py integration/invalidation.py
  intake.py readiness.py budget.py ledger.py orchestrator.py worker.py
  monitoring.py http_api.py
tests/test_group_b_*.py
site/group-b.js (opt-in mounted from app.py)
docs/GROUP_B_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md
docs/GROUP_B_IMPLEMENTATION_TRACKER.md
docs/GROUP_B_INTEGRATION_CONTRACT.md
```
Это целевая структура; НЕ создавать заглушки с ложно-положительной готовностью. Файлы/папки появляются по этапам. B owns runtime, DB tables B-owned, budget and review. Existing D1 `videos`, QDP, A, C and SRE остаются external.

## 4. API, security, integrity и статусы

Proposed versioned API: `GET /api/projects/:pid/group-b/capabilities`, `GET .../preview`, `POST .../quote`, `POST .../runs`, `GET .../runs/:id`, `POST .../runs/:id/cancel`, `GET .../videos/:video_id/results`, `POST .../reviews`. Точная форма утверждается в GB-MOD-02/26; ни один HTTP route не вызывает paid LLM напрямую.

Runs: draft/quoted/queued/running/partial/completed/failed/cancelled/needs_review; semantic block stream: not_run/ready/running/complete/partial/failed/unavailable. Existing content_b_analyses.state: pending/partial/completed/failed; use separate owned runtime tables for more detailed statuses. `completed` только после full validated B1–B8 applicable blocks, no pending reviews. Multiple completed revisions immutable.
Idempotency per project, video, A analysis version, model+prompt configuration, QDP preparation hash, run request key. Lease fencing and transactional outbox. Reads always re-evaluate current media access and publication lineage; access revocation invalidates readable outcomes, not historical immutable record.
OpenRouter calls: exact observed model capability, quote expiry, hard USD ceiling/reserve, provider usage/cost capture, charge_state unknown quarantined, no background auto replay after ambiguous charge. Side-effectful operations require explicit approval; CI mocks provider.
Threat model: project isolation, poisoned transcript/OCR prompt injection, arbitrary media path traversal, replay/cross-source frame substitution, hallucinated URL/CTA, model JSON schema spoofing, stale A snapshot, expired rights/retention, unintended paid retries, race for jobs.

## 5. Миграции и инсталляция
Existing `005_content_features_b.sql` is in main and may already be applied to real DB. Never rename/change it. New SQL migrations are additive and belong only to Group B runtime/ledger/outbox/review; choose the prefix *after* auditing newest integrated migration at stage GB-MOD-03 (main current has migration 053...; QDP proposes 040 and GA may have later migrations). Because the migrator sorts complete filenames lexicographically, numeric clashes can reorder DDL: use dependency manifest/checks, fresh PG17 and seeded upgrade. Never copy a QDP/GA migration into B branch. If upstream schema missing, package imports and dry-run can work but consumer capability is unavailable.

## 6. Stage execution contract

При каждом запросе:
1. Read `docs/GROUP_B_IMPLEMENTATION_TRACKER.md`, это ТЗ, current HEAD/SHA, PR/CI; выяснить первый этап, у которого IMPLEMENTED нет или QA требует завершения. Учитывать параллельные изменения Group A/QDP.
2. Реализовать ТОЛЬКО этап текущего запроса, без фиктивных сгенерированных результатов. Не начинать соседний этап, пока текущий требует исправления своей реализации.
3. Добавить tests: happy+negative (для DB — только disposable PG17), запустить всё доступное, проверить отсутствие внешних вызовов/секретов.
4. Коммит в своей ветке, обновление checklist: `- [x] ~~GB-MOD-NN~~ (IMPLEMENTED; QA: PASS | FAIL | BLOCKED | NOT RUN)`. Логи/commands, SHA и blocker фиксировать в tracker. Если есть pending CI, не обозначать PASS.
5. Отдельно указать: что сделано, какие файлы, тесты по факту, что требуется далее. Не редактировать main/peer branches.

## 7. Поэтапная реализация GB-MOD-00 → GB-MOD-29
### GB-MOD-00 — Самостоятельная ветка и bootstrap
**Цель и реализация.** Создать ветку от main; сохранить существующую B schema 005; добавить план, трекер, package contracts и CI; перечислить зависимые upstream модули, не копировать их.

**Целевые файлы.** `docs/GROUP_B_*, intelligence/group_b/__init__.py, contracts.py, tests/test_group_b_foundation.py, .github/workflows/group-b-module.yml` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** Pure unit/static tests + compare main...branch без файлов QDP/A/Analytics; MR draft.

**Definition of Done.** Версия ветки зафиксирована, документы доступны через GitHub, только B-owned files, нет новых платных вызовов..

**Зависимости.** текущий main + schema 005.

### GB-MOD-01 — Аудит реального кода и заморозка B1–B8
**Цель и реализация.** Сверить 005_content_features_b.sql и test_content_features_b_schema.py по всем столбцам, check constraints, triggers, индексы, version fields. Записать матрицу полей, словарей, null/unknown и source-first lineage.

**Целевые файлы.** `docs/GROUP_B_CONTRACT_MATRIX.md, intelligence/group_b/contracts.py, tests/test_group_b_contracts.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** Negative tests: unknown label, dup mechanisms, carousel seconds, video slide index, unreviewed AI origin, image_post.

**Definition of Done.** Один authoritative contract; migration 005 остаётся побайтно прежней..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-02 — Структура независимого модуля и Ports
**Цель и реализация.** Разделить домены intake / classify / deterministic / validation / storage / review / delivery, определить Protocol-based ports для QDP, A, media, LLM, budget, analytics; отсутствующие зависимости unavailable.

**Целевые файлы.** `intelligence/group_b/ports.py, errors.py, configuration.py, adapters/, docs/GROUP_B_DEPENDENCIES.md` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** Mock/fake ports, import smoke без installed QDP/A, no circular imports.

**Definition of Done.** B импортируется на одном main без соседних feature-веток, нет копий их подсистем..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-03 — Собственные SQL migrations и upgrade
**Цель и реализация.** Проектировать только новые GB runtime/ledger/feedback таблицы, не дублировать content_b_*. Выбрать свободное имя миграции на текущем main с dependency-order audit. Актуальный мигратор сортирует полные имена и контролирует SHA.

**Целевые файлы.** `intelligence/migrations/<reviewed-next-sequence>_group_b_runtime.sql, intelligence/group_b/repository.py, tests/test_group_b_db.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** Fresh PG17, migration no-op second pass, checksum tamper rejection, seed-upgrade, concurrent install.

**Definition of Done.** Миграция additive, FK/project scoping, legacy 005 byte-for-byte preserved..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-04 — QDP Inbox и versioned preparation
**Цель и реализация.** Имплементировать qdp-handoff/v1 consumer с явным ACK после DB inbox/job commit, проверкой UUID project video group B input_sha. Отсутствующий QDP или неполный handoff -> unavailable.

**Целевые файлы.** `intelligence/group_b/adapters/qdp.py, intake.py, repository.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** Replay same event/hash idempotent, same event/different hash quarantine; invalid group/media/project -> reject.

**Definition of Done.** QDP может доставить указатель, не запуская платный анализ; никаких фиктивных ready..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-05 — Group A exact-version adapter
**Цель и реализация.** Читать только полностью проверенные Group A snapshots/stream statuses, а не только `qdp_preparations.state=ready`; зафиксировать конкретный A analysis ID, A SHA, media SHA, source_policy_version и coverage. GA ветка развивается независимо.

**Целевые файлы.** `intelligence/group_b/adapters/group_a.py, lineage.py, docs/GROUP_B_A_HANDSHAKE.md` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** A absent/partial/not_run, swapped analysis, revoked media, cross-video replay, carousel media mapping.

**Definition of Done.** B не принимает несовместимые A версии; отсутствующая A — typed blocker..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-06 — Media Evidence Manifest
**Цель и реализация.** Создать source-first перечень таймкодов, transcript segments, OCR spans, sampled frames, audio events, carousel slide refs с SHA каждого источника; не выдумывать время слайда или несуществующие кадры.

**Целевые файлы.** `intelligence/group_b/context/evidence.py, context/manifest.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** corrupted hash, duplicate frame ID, arbitrary path, missing image, A timeline vs clip-relative offsets.

**Definition of Done.** Все ссылки на наблюдения проверяемы, текст/медиа не копируется без необходимости..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-07 — Readiness & dependencies по B1–B8
**Цель и реализация.** Разработать отдельные состояния ready/blocked/partial для восьми блоков и отличать временную недоступность от измеренного отсутствия. B5 требует сцены; B6 OCR/subtitle; B3 sampling; B1/B7/B8 смысловые evidence.

**Целевые файлы.** `intelligence/group_b/readiness.py, dependency_rules.py, tests/test_group_b_readiness.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** missing audio with silent video, carousel no video B5/B6, unknown speech, image_post unsupported B2.

**Definition of Done.** Блокировки конкретны и не превращаются в `false`/`no_cta`..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-08 — Context Builder и budgetable manifests
**Цель и реализация.** Формировать LLM-пакеты по A refs + кадры/слайды + текст; проверять ограничения токенов/изображений/байтов; scene coverage и пробелы; контент пользователя как недоверенные данные.

**Целевые файлы.** `intelligence/group_b/context/builder.py, context/sampling.py, tests/test_group_b_context.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** Long 10min video, 100-slide carousel, unicode, prompt injection in OCR/transcript, frame cap.

**Definition of Done.** Контекст версионирован и содержит manifest того, что модель видела и чего не видела..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-09 — Model Provider и capability probe
**Цель и реализация.** Инкапсулировать OpenRouter chat completions; поддержать мультимодальные изображения, текст, strict JSON; model slug/limits/price получать из подтвержденного каталога до платных вызовов, без предположения что Opus 5.5 читает видео автоматически.

**Целевые файлы.** `intelligence/group_b/providers/openrouter.py, providers/protocols.py, tests/test_group_b_provider.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** Mock HTTP, unsupported images/JSON, malformed/truncated output, 429/timeouts, no leaked key.

**Definition of Done.** Никаких реальных расходов в тестах; модель не жёстко вшита в B..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-10 — Cost Manager и ledger
**Цель и реализация.** Quote по реальным limits/provider pricing, ceiling по проекту и run, approval, reserve, actual usage, reconciliation. Платный retry после ambiguous failure запрещён до reconciliation; provider request ID идемпотентен где поддерживается.

**Целевые файлы.** `intelligence/group_b/budget.py, ledger.py, repository.py, tests/test_group_b_budget.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** Expired quote, cap exceeded, double-click, ambiguous charge, unknown cost, concurrent reservations.

**Definition of Done.** Платный Worker не стартует без подтверждения и ресурсов..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-11 — Job engine и orchestrator
**Цель и реализация.** Durable GB run/jobs; claim SKIP LOCKED, lease/fencing, heartbeat, cancellation, dry-run, per-block DAG, dispatch only ready blocks; free vs paid task separation.

**Целевые файлы.** `intelligence/group_b/orchestrator.py, worker.py, repository.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** 2 workers claim, lease theft, power-off, cancelled run, partial block completion, replay.

**Definition of Done.** Падение не оставляет ложное completed, повтор бесплатной операции безопасен..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-12 — B5 Editing — полностью локальный
**Цель и реализация.** Измерить cut_count, cuts_per_minute, avg_shot_duration, cuts_first_5_sec по подтверждённым границам A. Типы: 0 single_take; >0 <10 slow; 10.. <30 medium; >=30 fast. Видеоконтент только.

**Целевые файлы.** `intelligence/group_b/blocks/b5_editing.py, tests/test_group_b_b5.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** 0 cuts, exactly 10/30, first_5 boundary, 0 duration, dubious scene count, Decimal reproducibility.

**Definition of Done.** Все 4 поля соответствуют 005; отсутствие А измерений блокирует B5..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-13 — B1 Hook semantic classifier
**Цель и реализация.** Подтверждённые 10 mechanisms, пять nullable flags, delivery 4 channels, ordered hook_parts, exact spoken/screen evidence, timestamps or slide_index. Нет primary hook type.

**Целевые файлы.** `intelligence/group_b/blocks/b1_hook.py, prompts/b1_*.txt, tests/test_group_b_b1.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** multilabel overlapping, quote fabrication, unknown channel, repeated parts, carousel indices.

**Definition of Done.** B1 результат проходит строгий schema/evidence gate..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-14 — B2 Format classifier
**Цель и реализация.** 12 video formats, 8 carousel formats, primary+optional distinct secondary. Video and carousel не смешиваются. Не добавлять UGC/people/layout/text_density без отдельно утвержденного контракта.

**Целевые файлы.** `intelligence/group_b/blocks/b2_format.py, prompts/b2_*.txt, tests/test_group_b_b2.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** video carousel label mismatch, same primary/secondary, unsupported image_post, low confidence.

**Definition of Done.** Сохранение только утверждённых меток и review при неоднозначности..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-15 — B3 Story sampling engine
**Цель и реализация.** Перепроверить выборку 2 fps первые 5 sec, 1 fps дальше, цель около 60, адаптивно у сцен/OCR. Каждый frame ID/slide ref остаётся прикреплён к своему A-media.

**Целевые файлы.** `intelligence/group_b/blocks/b3_sampling.py, tests/test_group_b_story_sampling.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** sampling under 5 sec, 200sec cap, missing frame, timestamp offset, mixed carousel.

**Definition of Done.** Ни один frame не заявлен увиденным без передачи модели..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-16 — B3 Story semantics & patterns
**Цель и реализация.** 26 фиксированных ролей frame/interval/slide, before_after_phase, повторение ролей сохраняется, intervals имеют boundary_quality. Визуальные кадры + transcript/OCR; Group A техническая сцена != смысловой ролик.

**Целевые файлы.** `intelligence/group_b/blocks/b3_story.py, prompts/b3_*.txt, tests/test_group_b_story.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** invalid role, gaps not invented, before_after unknown, repeated loops, partial story.

**Definition of Done.** B3 хранится в content_b_story_* и view даёт верную sequence..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-17 — B4 Visual content
**Цель и реализация.** Четыре поля ai_generated nullable, person_visible/product_visible/logo_visible — записывать только после реально выполненного visual pass. AI происхождение без подтверждения остаётся NULL.

**Целевые файлы.** `intelligence/group_b/blocks/b4_visual.py, prompts/b4_*.txt, tests/test_group_b_b4.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** title-only no row, sparse vision, false confidence, synthetic-style ≠ verified AI origin.

**Definition of Done.** Ложные отсутствия не записываются; возможен needs_review..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-18 — B6 Subtitle style
**Цель и реализация.** После A-subtitles detect различать none/standard/dynamic, 7 style fields (position, font_size, font_style, text_color, highlight_color, background_style, display_mode). Текст слайда не субтитры.

**Целевые файлы.** `intelligence/group_b/blocks/b6_subtitles.py, tests/test_group_b_b6.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** no subtitles, OCR unavailable, moving words, colors, carousel blocked.

**Definition of Done.** Тип и семь полей соответствуют БД и real visual evidence..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-19 — B7 CTA occurrences
**Цель и реализация.** 0..N подтверждённых CTA; 12 типов, 9 destinations, source verbatim, delivery 1..2 speech/screen, positions as seconds or slide. No CTA only if scan complete. Не изобретать URL/handle.

**Целевые файлы.** `intelligence/group_b/blocks/b7_cta.py, prompts/b7_*.txt, tests/test_group_b_b7.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** 0 CTA complete vs partial, repeated CTA, invalid URL, multi delivery, unverified destination.

**Definition of Done.** content_b_cta_summary корректно строит has_cta NULL/false/true..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-20 — B8 Topic registry & resolver
**Цель и реализация.** Модель предлагает смысловые темы, deterministic resolver связывает c существующей Category>Topic>Subtopic по ID. Исследовательские C hypothesis не заменяют B8. Новые topic proposals проходят explicit review.

**Целевые файлы.** `intelligence/group_b/blocks/b8_topic.py, topic_registry.py, tests/test_group_b_b8.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** duplicate semantic topics, cross-parent topic ID, merged alias, unknown/new proposals.

**Definition of Done.** Нельзя сохранять неутверждённую иерархию или произвольный slug..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-21 — Unified evidence/schema validator
**Цель и реализация.** Проверить exact B enum, A-evidence IDs/timestamps, delivered frame existence, hallucinated text, prompt/model versions, unknown/partial; модель не может объявить complete без необходимой coverage.

**Целевые файлы.** `intelligence/group_b/validation/schema.py, evidence.py, tests/test_group_b_validation.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** unknown labels, mismatched source, negative time, forged quote/frame, injection, incomplete visual coverage.

**Definition of Done.** Неподтвержденные выводы quarantined/needs_review, а не молча сохранены как факт..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-22 — Atomic write B1–B8 в schema 005
**Цель и реализация.** Transactionally сохранить parent+children; immutable completed rows; versioned analysis_key; rollback under error; per-block status in own runtime tables; read-only SQL views for consumers.

**Целевые файлы.** `intelligence/group_b/persistence/repository.py, tests/test_group_b_db.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** bad FK, concurrent idempotency, uncommitted child, rollback, read across projects, completed immutability.

**Definition of Done.** Read from existing content_b_* без второй БД..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-23 — Human Review, gold set, quality
**Цель и реализация.** Ввод исправлений для B1–B8, immutable review revisions, downstream current effective labels, спорные случаи, gold-set sampling, agreement/precision/recall per class, threshold gates.

**Целевые файлы.** `intelligence/group_b/review/*.py, tests/test_group_b_review.py, docs/GROUP_B_QUALITY_GATES.md` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** concurrent review, revision invalidation, gold version, invisible unreviewed outputs.

**Definition of Done.** Автоматическое completed не подменяет human-reviewed; качество измеримо..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-24 — Downstream invalidation & refresh
**Цель и реализация.** A/media/QDP/human review changes → revoke current B readiness, enqueue reanalysis with new lineage/version; historical immutable records remain audit-only; outbox to Analytics.

**Целевые файлы.** `intelligence/group_b/integration/invalidation.py, outbox.py, tests/test_group_b_invalidation.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** old media SHA, rights revoked, A re-run, human correction, outbox replay.

**Definition of Done.** Unified Analytics не получает stale B as qualified..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-25 — Analytics adapter & contract handoff
**Цель и реализация.** Дать Analytics verified completed B1–B8 exactly A pinned, same source policy; align with existing `_b_payload` and `evaluate_qualification`. Текущий PR #23 независим; compatibility probe required.

**Целевые файлы.** `intelligence/group_b/integration/analytics.py, docs/GROUP_B_ANALYTICS_HANDSHAKE.md` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** full B + A + C fixture; missing B6, B8 needs_review, mismatched A, carousel B5 not required.

**Definition of Done.** Только реальные completed B результаты проходят Analytics gate..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-26 — API & Studio
**Цель и реализация.** HTTP create quote/approve/start/status/cancel/review/pagination и UI B1–B8 + evidence. Existing app.py routes guarded; no provider calls in handlers; optional route registration idempotent.

**Целевые файлы.** `intelligence/group_b/http_api.py, site/group-b.js, app.py minimal additive registration, tests/test_group_b_http.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** cross-project denial, CORS/Host/CSRF, pagination, double-submit, empty permissions.

**Definition of Done.** Рабочий операторский интерфейс без вмешательства в чужие экраны..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-27 — Instrumentation, throughput & recovery
**Цель и реализация.** Отдельные CPU/model queues, concurrency, token/image limits, elapsed metrics, costs, trace IDs, model failure classification, cancellation & cleanup, retention/revocation read checks.

**Целевые файлы.** `intelligence/group_b/monitoring.py, worker.py, tests/test_group_b_recovery.py` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** process crash, worker lease expiry, 429, ambiguous charge, 1k-job batch, no data leak.

**Definition of Done.** Восстановление доказано тестами; исполнение наблюдаемо..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-28 — Полный QA и cross-platform CI
**Цель и реализация.** Синтетические gold videos/carousels (нет звука, no CTA, mixed carousel), PG17 local/CI, Windows/Linux, mock OpenRouter, Golden JSON, migration and HTTP tests, performance baselines.

**Целевые файлы.** `tests/test_group_b_*.py, .github/workflows/group-b-module.yml, docs/GROUP_B_RELEASE_EVIDENCE.md` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** all negative cases, clean PG17 schema + upgrade, CI statuses pinned to commit, optional opt-in real-model benchmark.

**Definition of Done.** Ни одно отсутствие провайдера не подменяется synthetic pass; качество real model независимо измерено..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.

### GB-MOD-29 — Final merge-readiness & standalone release
**Цель и реализация.** Аудит main diff, dependency versions/branch SHA, migration order, update docs, ready-only after tests, PR against main, no merge without user. Release/rollback checklist, parity with A/B/C.

**Целевые файлы.** `docs/GROUP_B_RELEASE.md, docs/GROUP_B_IMPLEMENTATION_TRACKER.md, PR` (ориентировочные; проверять актуальную структуру).

**QA и негативные сценарии.** git diff --name-only main...B; no foreign code; QA suite green; unresolved external blockers clearly listed.

**Definition of Done.** Отдельная merge-ready ветка B, готовая к интеграции после согласования зависимостей..

**Зависимости.** предыдущий GB stage; upstream ports — только при проверенном capability.
## 8. Финальная Release Gate
- 30/30 implementation items implemented, QA статусы правдивы; тесты локально/CI соответствуют изменённому SHA; PG17 fresh+upgrade, Windows/Linux and real-model opted-in golden evaluation (при наличии ключа и подтверждения денег).
- Только `intelligence/group_b/`, B-owned additive migrations, B docs/tests/UI + минимальная регистрация в app.py. Нет diff чужих QDP, A, SRE, C, Analytics подсистем.
- QDP → B intake, A exact-reference → B, B → Unified Analytics для video/carousel E2E подтверждены конкретным тестом. Пока peer PR не смержен — статус **integration blocked**, не «готово к main».
- Финальная выборка с precision/recall and per-block reliability, no evidence fabrication; quality thresholds фиксируются до оценки, модель-провайдер может быть unavailable.
- Stable source/current policy checks, encrypted or protected keys, no paid calls from UI or CI, charge disputes do not repeat provider calls.
- DB schema_migrations SHA invariant, all transactions + idempotent queue, retention/revocation and historical immutability, rollout/rollback documented.
- Только пользователь решает, сливать ли PR в main.

## 9. Команды и проверки
```bash
# pure unit/statics; no provider calls:
python -m unittest discover -s tests -p 'test_group_b_foundation.py' -v
python -m unittest discover -s tests -p 'test_content_features_b_schema.py' -v
# disposable PG17 ONLY (the DB test suite otherwise skips):
CM_TEST_PG_DSN='postgresql://.../contentmachine_test' python -m unittest tests.test_content_features_b_schema -v
# check only Group B owned commits:
git diff --name-status main...feature/group-b-full-module
git log --oneline main..feature/group-b-full-module
```
Отсутствие CM_TEST_PG_DSN делает PG tests SKIP, никогда PASS. Открытый CI run queued/pending — не провал тестов, но ещё и не success.
