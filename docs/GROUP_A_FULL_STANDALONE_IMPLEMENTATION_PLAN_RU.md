# CONTENT MACHINE — GROUP A FULL STANDALONE MODULE
## Полный поэтапный план реализации / контракт для следующего ChatGPT-чата

**Редакция:** 2026-10-26, GA-MOD/1.0. **Статус:** ПЛАН — не утверждать, что описанные далее функции уже реализованы. **Язык исполнения:** Python 3.10+, PostgreSQL 17, локальный HTTP/UI Content Machine. **Место разработки:** собственная GitHub feature-ветка, никогда не `main` до отдельного merge.

## 0. Миссия и неоспоримые правила

Собрать законченный, автономно развиваемый и интегрируемый модуль **Group A — Technical & Visual Extraction**. Вход: подтверждённая QDP-подготовка плюс проверяемый медиа-манифест публикации. Выход: версионированные, воспроизводимые технические измерения и evidence, пригодные Group B и Unified Analytics. Поддерживать video, slideshow_video, photo_carousel, mixed_carousel, image_post. Реализовать: декодирование и сцены, sample кадров, audio/VAD, ASR, speech metrics, OCR, text tracking, subtitle presence, music/SFX events, изображения и видеослайды каруселей, storage, API/UI, workers, QA, жизненный цикл и handoff.

1. **Один GA-MOD-NN = один запрос пользователя** «Следующий этап». Не делать искусственных микроэтапов, не объединять два крупных этапа без указания; если фактическая реализация не закончена, следующий запрос продолжает незавершённый этап, а не перескакивает.
2. Любая работа только в канонической отдельной ветке Group A. В `main`, QDP, SRE, RTB, VC, Analytics и PSI ветках НЕ менять код, НЕ переписывать их миграции и НЕ делать merge без явного отдельного поручения. Совместимость обеспечивают версии контрактов и адаптеры, допускающие отсутствующий upstream.
3. Не копировать целиком чужие подсистемы в Group A. Собственность GA: `intelligence/group_a/**`, GA SQL, GA tests, GA-specific UI, docs, CI, минимальная точка подключения через публичный интерфейс host app. Любые чужие изменения оформляются отдельным RFC/merge guidance; при необходимости локальный bootstrap-адаптер/feature flag.
4. **Технические измерения ≠ смысловой анализ.** Group A измеряет, что/где/когда; Group B решает тип Hook/Story/CTA/Topic. Поля CTA в старой схеме не дают GA права классифицировать CTA. Не строить рекурсивную зависимость B→A→B.
5. Group A не ищет видео, не заново классифицирует SRE, не определяет успех по метрикам (C), не запускает Opus/другую платную LLM по умолчанию. Локальные VAD/ASR/OCR/аудиомодели — допустимые специализированные движки с контролируемой доступностью.
6. Фактические ограничения нынешнего A1: локальные MP4/MOV/MKV/WebM, ≤2 GiB, ≤600 s, ≤300 технических сцен, FFprobe 30 s, FFmpeg 210 s, lease 300 s; пересматривать только по замерам, с явным versioned config.
7. Никаких сфабрикованных наблюдений: `null`, `unknown`, `not_run`, `partial`, `unavailable`, `failed` имеют отличную от «измеренный ноль/отсутствие» семантику. Временные интервалы и confidence должны иметь источник и границу точности.
8. Сетевые/платные вызовы запрещены в автоматическом CI. Нет скрытых загрузок MP4, новых платных запросов и автоматических API-вызовов без отдельного контракта.
9. Публичные интерфейсы, миграции и версии старых записей обратно совместимы. SQL forward-only; применённые SQL файлы не редактировать (мigrator сверяет checksum полного файла).
10. Каждый этап заканчивается: код + тесты + проверенный diff + commit SHA + `docs/GROUP_A_IMPLEMENTATION_TRACKER.md` (отмеченный выполненный шаг) + краткое отчётное сообщение. Без коммита не говорить «завершено».

## 1. Подтверждённый исходный код и ветки (GitHub read-only 2026-10-26)

Repository `BanSins7/ContentMachine`. Последний проверенный `main`: `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`. В следующих сессиях **заново читать актуальные SHA** — они меняются параллельно.

- **Group A A1 source:** `feature/group-a-technical-extraction-v1-20261023`, SHA `ae9fa80f6b53eb4ba2fe16eb3ecf65102e13bc7d`, PR #26 открыт, база = QDP. Реализовано `intelligence/group_a/{contracts,extractor,repository,worker,http_api}.py`, `017_group_a_technical_extraction.sql`, `tests/test_group_a_{core,db}.py`, `docs/GROUP_A_EXECUTION_STAGE_A1.md`. Работают FFprobe/FFmpeg, QDP gate, локальная очередь, UI/API, идемпотентность, повторная проверка актуальности, частичная A003 запись.
- **QDP canonical branch:** `feature/qualified-data-pipeline-v1-20261023`, PR #24 открыт, SHA `19cffe41dafd81c48075fbc14e289c5600049a45`. Дополнен собственными `coverage`, `intake`, `integrations/contracts.py`, `schema`, миграцией `040_qdp_integration_foundation.sql`, inbox/outbox. НЕ переносить эти файлы в Group A.
- **SRE:** `feature/sre-production-v1-20261023`, PR #22, развивающийся самостоятельный модуль. Реальная конечная модель SRE — Embeddings + Opus 5.5 без Fast LLM. Group A не зависит от внутреннего оркестратора SRE.
- **Analytics:** `feature/analytics-engine-qualified-pipeline-20261023`, PR #23, готово ядро сборки существующих A/B/C наблюдений. Контракт A потребителя нужно проверить в момент GA-MOD-01 и GA-MOD-20, не копировать Analytics.
- **Main:** уже содержит `003_video_features_a.sql`, `004_carousel_feature_a.sql`, `005_content_features_b.sql`, `016_query_validation.sql`, PSI `030..041`, Knowledge Extraction `042..053` на проверенном SHA. Все префиксы миграций заняты/динамичны: резервировать имя новой GA миграции после сверки всех веток.
- **PSI (Project Source Intelligence):** в `main` имеются `intelligence/project_sources/transcript_jobs.py`, `transcript_normalizer.py` (SRT/local faster-whisper, контракт `transcript/v1`), `vision_engine.py` (видеокадры/FFmpeg, sparse OCR, контракт `visual-preparation/v1`), `video_jobs.py`. Эти **project_source_id/project_source_version** НЕ равны D1 `videos.id`/`media_assets.id`. Повторное использование — только через verified asset hash + явный bridge/capability, не по URL и не через неконтролируемые SQL join.

### Обязательное решение по ветке

**Предпочтительное:** создать `feature/group-a-full-module` от актуального `main` и выбрать в неё **только** GA-owned код из PR #26 (A1), не перенося старый QDP. Это безопаснее, чем force-rebase существующего открытого PR #26, который уже расходится на 143 коммита от текущего `main` и включает старый QDP. Старый SHA и PR #26 сохранить до завершения переноса, потом закрыть как superseded только после проверки и ссылки на новый PR. Новую ветку сделать единственной канонической Group A; не продолжать параллельную разработку двух GA веток.

Если владелец прямо потребует сохранение старого имени, тот же результат допустим через резервную ref + аккуратную пересборку branch diff и обновление PR базы после аудита; НЕЛЬЗЯ переписать опубликованную ветку без recovery ref, и НЕЛЬЗЯ автоматически подтянуть её старый QDP в main.

## 2. Целевой pipeline и границы ответственности

```
VC / Discovery / PSI Media Assets (canonical video + verified media manifest)
                        |
SRE completed LIVE ---> QDP Qualified + Enrichment + A readiness
                        |
                  QDP outbox/v1
                        |
              Group A QDP consumer
                        |
              Group A inbox + jobs
                        |
     manifest + content SHA + current eligibility validation
                        |
                  media probe
                        |
   +--------------------+---------------------+
   |                    |                     |
FFprobe/FFmpeg       Audio branch         Images / carousel
(duration, ratio,    audio decode, VAD,   slide-order, OCR,
scene cuts, frames)  ASR/align, events    frame manifest
   |                    |                     |
   +--------------------+---------------------+
                        |
         subtitle observation + temporal text merge
                        |
      typed validator + analysis version assembler
                        |
  A003/A004 feature schema + GA job/results/manifest tables
                        |
          Group A results outbox / query port
                        |
        Group B B1-B8 / Unified Analytics A lane
```

**Не должно быть:** `GA→B semantic call→rewrite A`. B может вывести CTA с evidence и ссылкой на A temporal spans; read-only проекция A+B допустима после обоих независимых immutable snapshots. Group A не запускает B автоматически при старых невалидных входах.

## 3. Domain ownership и контракты (freeze before implementation)

**Идентичности:** `project_id` = 32 lower hex TEXT, `qdp_run_id` UUID, `video_id` D1 BIGINT, `media_asset_id` BIGINT, `source_id` TEXT, `source_policy_version_id` BIGINT, `ga_run_id/ga_job_id` UUID либо существующий job BIGINT (один тип на таблицу), `A analysis_id` BIGINT, `frame_id` UUID/hash, `source_version` смежной PSI системы — отдельный namespace. Любой склейке нужен детерминированный hash и проверенная связь, никакой конвертации IDs по похожести.

**QDP intake** минимум:
`schema_version=qdp-handoff/v1, event_id UUID, project_id, qdp_run_id, video_id, analysis_group='A', preparation_input_sha256, event_payload_sha256, optional preferred_media_asset_id`. ACK — только после атомарной записи GA inbox+job, не после завершения анализа. Доверяем не event-полям сами по себе, а повторному DB re-read authoritative QDP readiness и текущих metadata/media revisions. QDP v1 может отсутствовать в clean main: выдавать `capability=unavailable` без подделок.

**Media manifest:** `ga-media-manifest/v1` из verified asset: canonical ID, `content_kind` (video/slideshow_video/photo_carousel/mixed_carousel/image_post), ordered `items[] {index, kind, media_asset_id, sha256, byte_size, verified_local_path_ref, optional_duration, timebase}`, `source_id`, provenance, `manifest_sha256`, `verified_at`. Разделить binary file и metadata pointer; размер/путь/sha всегда перепроверять непосредственно перед decode/commit. Для mixed timecodes — относительно каждого video slide; для статичных слайдов нет watch duration.

**Task API:** `group_a.prepare/v1` принимает project, QDP pointer, immutable pipeline config, media manifest hash; выдаёт `ga_run_id`, `state`, `reused`, `reason_codes`. Базовый legacy A1 API `/api/projects/{pid}/group-a/tasks` сохранить как совместимый адаптер/alias, пока миграция не завершена.

**Measure schemas:** `group-a-technical/v1`, `group-a-frame-manifest/v1`, `group-a-speech/v1`, `group-a-screen-text/v1`, `group-a-audio/v1`, `group-a-carousel/v1`, `group-a-assembly/v1`, each with analysis/media/config/model hashes, status and evidence. Не выдумывать «confidence» для FFprobe, где это точное машинное чтение, а не probabilistic model.

**B output:** `group-a-to-b/v1` только ссылки на immutable A003/A004 analysis IDs, ordered scene/slide references, transcript spans, OCR spans, frame manifest hashes, audio events and `completeness_by_stream`, `version_pins`, `qdp_lineage`. B не должен принимать `B-ready` только на основании `QDP A ready`.

**Технический набор A003 (14):** duration, display aspect, scene count, intervals, first OCR text, spoken_words, active speech WPM, has_subtitles, speech_coverage_pct, CTA evidence/time (только confirmed B-linked projection), timed transcript, timed OCR, first speech, music/SFX events. Не возвращать исключённые контрактом `avg_scene_sec`, stored FPS/width/height. Для carousel использовать A004 semantics, image_post только отдельный совместимый A contract.

**Status:** `queued/running/partial/completed/blocked/failed/cancelled/invalidated` у GA jobs; per-stream `not_run/complete/partial/unavailable`; `completed` Group A означает доказанное завершение нужных для *этого типа публикации* обязательных потоков, не «A1 сцен измерены». Отсутствие речи у действительно проверенного mute видео — `complete` с подтверждённым нулём, ASR failure — `partial/unavailable` и null metrics.

**Fingerprint:** `SHA256(canonical JSON of project/video/asset list+hashes, QDP input hash, pipeline version, relevant model versions and complete configuration)`. Результаты append-only; новая модель/видео/настройка — новый run/snapshot; результаты одного набора контракта не редактировать.

## 4. Физические границы модулей и пример структуры

```
intelligence/group_a/
  __init__.py
  contracts.py                    # public DTO, validation/status, canonical hashes
  ports.py                        # QDPReader, MediaCatalog, ASRProvider etc. protocols
  capabilities.py                 # runtime availability/version negotiation
  config.py                       # immutable per-run settings + runtime resource budgets
  repository.py                   # GA-only persistence + UoW (existing A1 refactor)
  service.py                      # start/cancel/retry/read orchestration
  task_queue.py                   # leases, fencing, idempotency, attempts
  worker.py                       # parent orchestrator
  media_manifest.py               # multi-asset verification / path safety
  extractors/
    technical.py                  # existing FFprobe/FFmpeg adapted; no behavior loss
    frames.py
    audio_decode.py
    vad.py
    asr.py
    speech_metrics.py
    ocr.py
    text_tracking.py
    subtitles.py
    audio_events.py
    carousel.py
  adapters/
    qdp.py                        # QDPHandoffPointer + authoritative checks
    media_assets.py               # D1 canonical asset read
    psi_reuse.py                  # optional verified PSI reuse, no domain id mix
    group_b.py                    # read-only consumer mapping
    analytics.py                  # A summary / version refs only
  assembly.py
  validation.py
  invalidation.py
  cost_resources.py
  observability.py
  http_api.py
  schemas/*.json
site/group-a.js                   # GA-owned UI; host integration minimal
intelligence/migrations/<NEXT_SAFE>_group_a_*.sql # new additive SQL, no old checksum edit
tests/test_group_a_*.py
.github/workflows/group-a-module.yml
docs/GROUP_A_FULL_IMPLEMENTATION_PLAN.md
docs/GROUP_A_IMPLEMENTATION_TRACKER.md
docs/GROUP_A_INTEGRATION_GUIDE.md
docs/GROUP_A_RELEASE_EVIDENCE.md
```

Не обязательно создавать все файлы заранее: добавлять по мере этапов. Сохранять старые import paths через shim / backwards-compatible export, а не ломать `group_a.repository.create_task/work_once`.

## 5. Разрешение миграций и зависимости

На проверенном `main` уже миграции вплоть до `053_ke_c_qualified_execution.sql`; QDP имеет `016_qualified_data_pipeline.sql` и `040_qdp_integration_foundation.sql`; старая GA ветка — `017_group_a_technical_extraction.sql`. `migrate.py` сортирует **полные имена файлов**, сохраняет SHA-256 и запрещает менять применённые SQL. Новая чистая GA ветка не должна зависеть от старой QDP 016 или пытаться редактировать историческую 017. Выбрать новую свободную миграционную последовательность **после фактических зависимостей**, зафиксировать с владельцами других модулей; рекомендуем резерв GA диапазона после максимального занятого номера на GA-MOD-03, но номер не хардкодить до проверки. Если таблица `group_a_tasks` уже существовала на развернутой базе после исторического PR, additive `IF NOT EXISTS`/data-preserving migration должны сохранить её содержимое/индексы и предусмотреть миграцию `legacy A1 → new GA run`, без потери записей. Fresh PG и upgrade PG тестировать оба; legacy миграции в branch не править.

## 6. План реализации: GA-MOD-00 — GA-MOD-24

Каждый этап ниже: **Объём → Файлы/контракты → Тесты → Definition of Done**. Выполнение одного этапа — один пользовательский запрос.

### GA-MOD-00 — Чистая ветка, перенос только собственного A1 и постоянный трекер
**Объём:** актуализировать main/QDP/GA SHA и открытые PR, создать canonical `feature/group-a-full-module` от свежего main, сохранить recovery pointer старого GA SHA; перенести собственные файлы A1, тесты, документацию, CI и минимальные UI hooks из PR #26 без старого QDP. Создать основную MD-инструкцию, трекер checklist, integration guide, release evidence. Провести diff audit: только GA-owned изменения; никакой PR #24 код не копируется. Временно отключить live QDP execution через `capability=unavailable`, сохранить standalone unit tests. Создать новый draft PR → main с правильным base; PR #26 оставить как reference до успешной миграции.
**Файлы:** `intelligence/group_a/**`, `docs/GROUP_A_*`, `tests/test_group_a_*`, `.github/workflows/group-a-module.yml`; host stubs только feature-gated.
**Тесты:** Python syntax; `tests/test_group_a_core.py` на перенесённых исходниках; compare diff main..GA excluding QDP/PSI/Analytics; проверить что main untouched.
**DoD:** чистая ветка и draft PR, старый A1 сохранён, progress MD существует; коммит и SHA в трекере.

### GA-MOD-01 — Контрактная матрица и truth semantics (freeze)
**Объём:** реальный аудит A003/A004, QDP `qdp-handoff/v1`, PSI `transcript/v1`, `visual-preparation/v1`, Group B A-FK, Analytics A reads; определить версионированные DTO JSON schemas, поля ids/hashes/status; сохранить обратную совместимость A1 и canonical video kind mapping. Зафиксировать `CTA` исключительно подтверждённым смысловым компонентом B (без мутации A); `no_speech` vs `asr_error`, complete vs partial; source provenance. Подготовить contract compatibility matrix; неизвестные схемы peer допускают `unavailable/incompatible`.
**Файлы:** contracts.py, `schemas/*.json`, docs integration guide, tests contracts.
**Тесты:** JSON Schema valid/invalid/missing, adversarial statuses, noncanonical IDs, cross-project, version mismatch, reference contract fixtures.
**DoD:** frozen v1 schemas, сознательно запрещённый silent coercion, отчёт зависимостей.

### GA-MOD-02 — Самостоятельная архитектура портов и адаптеров
**Объём:** интерфейсы `QDPReader`, `MediaCatalog`, `TranscriptReader`, `VisionReader`, `ResultWriter`, `HandoffConsumer`, `Clock`, `TaskQueue`. Разделить pure business logic от psycopg, file IO, subprocess, API. Runtime capability discovery: GA может устанавливать зависимости, но без готового QDP возвращает диагностическую блокировку и не запускается с фальшивыми видео. Feature flags; убираем прямой импорт чужих private functions.
**Файлы:** ports.py, adapters skeleton, service.py, capabilities.py, contracts.py.
**Тесты:** fake adapters, missing dependency, version negotiation, no network or DB required for pure tests.
**DoD:** собственный core импортируется и тестируется без QDP/PSI модуля.

### GA-MOD-03 — Только GA SQL, upgrade path и целостность PostgreSQL
**Объём:** forward-only миграции на чистом актуальном main: GA runs/steps/inbox/results/outbox/cache refs, stage statuses/attempt fences, canonical index, media hash+QDP hash FK/pin, deadlines, invalidation; связь с A003/A004 views. SQL constraints: immutability completed, same project/video/asset, no copied D1 video catalog; legacy A1 upgrade. Использовать безопасные новые имена и order; legacy applied checksums untouched.
**Файлы:** `intelligence/migrations/<reserved>_group_a_*.sql`, repository.py, test_group_a_db.py, migration tests.
**Тесты:** PG17 fresh, upgrading existing A1, repeat/no-op, checksum rejection, failed FK, project isolation, cascade/retention.
**DoD:** SQL работает без QDP в main через capability gate; `python -m intelligence.migrate` success в disposable DB.

### GA-MOD-04 — QDP handoff consumer / inbox без копирования QDP
**Объём:** принять `qdp.preparation_ready/v1` только по подготовке A, строго проверить pointer+hash+project+readiness через публичный QDP adapter, атомарно записать GA inbox/job и ACK receipt после durable commit; same event same hash idempotent, same event different hash quarantine; unavailable peer diagnostic. Legacy API `create_task` сохранить. No automatic paid/media acquisition calls.
**Файлы:** adapters/qdp.py, service.py, queue/repository, contracts, tests.
**Тесты:** fake QDP, replay, tamper, foreign project, missing outbox, invalidated preparation, concurrent inbox receives, PG.
**DoD:** QDP и GA независимо живут в ветках; integration может быть проверен через adapter fixtures.

### GA-MOD-05 — Media Manifest и верифицированный Input Resolver
**Объём:** проверить media_asset + actual bytes SHA/size + canonical video identity + media_kind, source provenance, path allowlist, symlink traversal, changed asset, weird extensions, file magic, disappearance between stages; multi-item manifest for carousel with preserved slide order and video segment timebase. Reuse existing A1 authorized_file, расширить его до manifest input without weakening. Не скачивать видео, не делать GitHub/DTK live calls.
**Файлы:** media_manifest.py, adapters/media_assets.py, contracts/schemas, tests.
**Тесты:** valid video, multi-images, bad hashes, malicious paths, missing video, changed bytes, cross-video asset, duplicate slides, stable manifest hash.
**DoD:** детерминированный GA media manifest/v1 и fail-closed.

### GA-MOD-06 — Восстановление и укрепление A1 (FFprobe/FFmpeg)
**Объём:** перенести FFprobe display aspect/timebase, duration, technical scene detection и `TechnicalMeasurement` без регрессий; версия инструмента/threshold/min-gap, ffmpeg process timeouts, scene monotonic/non-overlap, 600 s/300 scenes в baseline, при необходимости безопасно пересмотреть limits; A003 state=`partial`. Не считать сцены семантикой и не добавлять запрещённые resolution/FPS поля. Windows/Linux reproducible.
**Файлы:** extractors/technical.py, legacy extractor.py shims, validation, test_group_a_core.py.
**Тесты:** local generated MP4 controlled transitions, weird SAR, audio-only, malformed/long, abrupt/gradual cuts, scene boundary tolerances, binary not found, timeout.
**DoD:** A1 функционально не хуже PR #26 и проходит реальные offline-media smoke.

### GA-MOD-07 — Frame Sampling / Thumbnail & Evidence Manifest
**Объём:** scene-aware + periodic sampling, adjustable budget, early focus; deterministic timestamp mapping to exact source timebase, image bytes hashes, perceptual duplicate suppression, bounded JPG/PNG, frame manifest; retain enough samples for B3 and OCR, annotate coverage and uncertainty. Сохранение изображений вне SQL, atomic writes, cleanup, no fixed 8-frame limitation mistaken for full coverage (PSI current max 8 is separate sparse contract).
**Файлы:** extractors/frames.py, schemas frame manifest, media storage adapter, tests.
**Тесты:** deterministic frame count/timestamps, short/long, motion/low-light, video with scene boundary, corrupted/duplicate, atomic recovery, no bytes in DB.
**DoD:** versioned `group-a-frame-manifest/v1` with sampled coverage metadata.

### GA-MOD-08 — Shared Media Understanding / PSI bridge & cache
**Объём:** read-only adapter to previously verified PSI outputs `transcript/v1`, `visual-preparation/v1`; do not equate PSI `source_version_id` with Discovery `videos.id`. Require matching asset SHA-256, exact original media, model/config/version semantics; translate ms↔s exactly, pin confidence/time origin; reject sparse OCR as full temporal coverage. New local processing fallback only if capability unavailable; never auto-call PSI paid Sonnet vision analysis.
**Файлы:** adapters/psi_reuse.py, bridge mapping/contracts, tests.
**Тесты:** matched hash accepted, mismatched ID/hash rejected, unknown timestamps, sparse OCR never complete, PSI absent, no network, no unapproved LLM calls.
**DoD:** no second incompatible ASR/OCR data catalog; explicit reuse policy and shared-media cache semantics.

### GA-MOD-09 — Audio Decode + Voice Activity Detection
**Объём:** FFmpeg isolated audio decode, audio present/silent distinction, optional Silero VAD pinned version, rate/channel handling, union of speech intervals, overlap tolerance, corrupt stream handling, GPU/CPU config, true absence vs unobservable; per-step provenance. No full ASR claims.
**Файлы:** extractors/audio_decode.py, vad.py, schemas, tests.
**Тесты:** silence, speech+music, VAD overlaps, no audio, invalid codec, short clips, multilingual speech, repeated run hash.
**DoD:** reliable VAD event intervals and truthful statuses.

### GA-MOD-10 — ASR Pipeline + language, source selection, word alignment
**Объём:** faster-whisper local ASR behind provider port, PSI transcript reuse when verified, configurable language auto/ru/en where supported, exact segment/optional word timestamps, source SRT/TXT distinction, unsupported timing remains unknown; no guessed words; separate model/version/timing reliability; subprocess wallclock bounded and resource leases.
**Файлы:** extractors/asr.py, asr port, schemas speech, tests.
**Тесты:** Russian/English test voice assets, no voice, transcription failed, mixed speakers, malformed timestamps, PSI SRT reuse, timeout, no paid fallback.
**DoD:** immutable timed transcription contract with measured quality on fixture corpus.

### GA-MOD-11 — Speech metrics: word count, active-time WPM, first speech, coverage
**Объём:** exact utterance normalization and word counting language-aware, speech_duration as union of VAD/timed ASR windows with explicit policy, `spoken_words`, `speech_wpm=words*60/active_speech_sec`, `speech_coverage_pct=100*active_speech_sec/video_duration`, first_speech from trusted timing, zero/unknown semantics, decimals bounded; map to A003.
**Файлы:** extractors/speech_metrics.py, contracts, validation, tests.
**Тесты:** numerical gold cases incl overlap, 0 duration, silence, timestamps edge cases, repetitions/rounding, speech beyond duration rejection.
**DoD:** reproducible A003 `spoken_words/speech_duration_sec/summary` without false zero.

### GA-MOD-12 — OCR engine: multi-language frame text and geometry
**Объём:** PaddleOCR/Tesseract adapter with explicit engine/version/lang, fast image preprocessing, bounding boxes/text/confidence per frame, OCR budget, CPU/GPU caps; do not infer absent text from failed OCR; cache by frame SHA + model key; partial frame failure accounted.
**Файлы:** extractors/ocr.py, contracts, frame cache, tests.
**Тесты:** fixed ru/en text images, empty/noisy images, tiny subtitle lines, rotated images, model absent, frame corruption, multilingual fallback.
**DoD:** timestamped per-frame OCR observations and quality metrics.

### GA-MOD-13 — Screen Text Tracking / temporal OCR intervals
**Объём:** normalize text, track bounding boxes across sampled frames with spatial/text similarity, dedup continuous occurrences, preserve gaps and reappearances, start/end bounds and sampling uncertainty, `first_text_sec`, screen_word_count, A003 `video_feature_a_screen_text`; distinguish sparse PSI sample vs interval validated OCR; manual QC fixtures.
**Файлы:** extractors/text_tracking.py, validation, A003 writer, tests.
**Тесты:** same text across frames, one-frame flash, simultaneous two captions, fade/moving overlay, duplicates and edit-distance; interval precision cap.
**DoD:** tracked text spans do not double count each repeated sampled frame.

### GA-MOD-14 — Subtitle detection: true/false/unknown
**Объём:** detect embedded subtitle track, visually rendered subtitles (OCR trajectory), align speech text/time, distinguish headline/decoration/subtitles; validation confidence threshold calibrated on gold corpus. Group A only subtitle **presence**, NOT B6 style. `false` only after sufficient coverage; otherwise `unknown`.
**Файлы:** extractors/subtitles.py, contracts, A003 mapping, tests.
**Тесты:** burned-in captions, platform captions, title-only, dynamic word highlighting, missing audio, sparse OCR, multilingual, visually absent subtitles.
**DoD:** calibrated detection and no unjustified false/unknown.

### GA-MOD-15 — Audio Event Detection: music and SFX
**Объём:** audio classifier adapter (candidate YAMNet/PANNs after license/install/quality check), frame windows, threshold/version, music/sfx/other mapping to A003, speech overlap allowed, soundless video vs classifier failure, per-event source evidence and confidence.
**Файлы:** extractors/audio_events.py, schemas, tests.
**Тесты:** music only, music+speech, silent, transient SFX, background noise, overlaps, inference timeout/unavailable.
**DoD:** confirmed event timeline with honest coverage.

### GA-MOD-16 — Photo Carousel & Image Post Extraction
**Объём:** load image manifest ordered slides, determine kind by verified media not URL, OCR per image, image aspect ratio, ordered items, first_text_slide and total words only with OCR completeness, no slide playback duration; A004 `carousel_feature_a_analyses/items/summary` integrity.
**Файлы:** extractors/carousel.py, A004 writer, contracts, tests.
**Тесты:** 1 image_post, N photo_carousel, unordered and duplicated slide indices, text/no text, no media, mixed media rejected in this stage.
**DoD:** photo/image pipeline end-to-end locally passes PG fixtures.

### GA-MOD-17 — Mixed Carousel & Video Slides
**Объём:** mix image/video in ordered slides; video slide gets A003 analysis keyed to that exact slide media asset, relative timestamps for video segments, image steps have no duration; link `video_a_analysis_id` and `media_asset_id`, ensure source/video/slide provenance, global audio status if evidence. No fabricated swipe durations or conversions of slide indices to seconds.
**Файлы:** carousel.py, media manifest, A003/A004 mapping, tests.
**Тесты:** image+video+image, multiple video slides, audio on video slide, malformed sequence, FK mismatches, immutable completed carousel.
**DoD:** `mixed_carousel` complete only when required slide measurements valid.

### GA-MOD-18 — Atomic Assembly, quality/coverage gate & immutable snapshots
**Объём:** assemble technical, speech, OCR, subtitles, audio, carousel with explicit per-stream status/coverage and provenance; no fake CTA; write A003/A004 in durable unit-of-work with checksum lineage and partial records when appropriate; reject cross-version mixed media or missing mandatory streams. Record `measurement_methods`, `unavailable_reasons`, `measured_at`; immutable completed results, support future new version. Ensure existing A003 summary views remain accurate.
**Файлы:** assembly.py, validation.py, repository.py, integration tests.
**Тесты:** full video, technical-only partial, media without speech, mixed carousel complete, invalid times, duplicate rows, failed transaction/rollbacks.
**DoD:** complete/partial semantics proven against actual SQL views.

### GA-MOD-19 — Group B consumer contract; CTA circularity resolution
**Объём:** publish `group-a-to-b/v1` immutable pointer+coverage, stable A003/A004 IDs, frame/transcript/OCR/audio refs, failure status; B must pin same video/source/policy/version. Preserve B8 taxonomy independence. A003 legacy `cta_start_sec` remains NULL unless explicitly externally confirmed through compatible read-only projection; never let GA hallucinate CTA. Add B consumer fake adapter and integration sample; do not implement B1–B8 in GA.
**Файлы:** adapters/group_b.py, schemas, handoff, tests, integration guide.
**Тесты:** B known version, unknown B, partial A, stale A, mixed carousel; CTA no circular writes.
**DoD:** Group B can consume complete GA facts without importing GA internals.

### GA-MOD-20 — Runtime Orchestrator, retries, resource budgets, invalidation
**Объём:** multi-stage durable jobs, dependency DAG, CPU/GPU quotas, priority/concurrency, bounded subprocesses, lease renewal/fencing, safe retries only of non-paid local stages, cancel, pause/resume, process restart, per-stage cached reuse; revocation/invalidation from QDP/human review/asset hash changes, old snapshot preserved for audit but not exposed as current. No DB transaction while decoding video. Crash reconciliation, outbox ACK delivery semantics.
**Файлы:** service.py, worker.py, task_queue.py, invalidation.py, cost_resources.py, tests.
**Тесты:** 2+ workers exactly-once commit (at-least-once delivery), lease death, restart, worker cancellation, stale input, late invalidation, resource overload.
**DoD:** full GA worker durable across process crashes and safe for parallel workloads.

### GA-MOD-21 — HTTP API, UI, diagnostics & observability
**Объём:** GA-owned endpoint facade from host app with registration, GET runs/tasks/events/stream measurements, POST start/cancel/retry (manual paid operations none), cursor pagination, capped payloads, only project-scoped access, sanitized errors, local Host/Origin/X-ContentMachine protection. GA Studio: file readiness, per-stage progress, scene timeline, transcript timestamps, OCR spans, carousel slides, music events, evidence completeness; never expose private local file paths. Include readiness/capability page. Events, duration/CPU/GPU cost telemetry, counters.
**Файлы:** http_api.py, site/group-a.js, host registration shim, observability.py, JS + HTTP tests.
**Тесты:** API method/path validation, foreign project, CSRF/origin, XSS textContent rendering, JS syntax, pagination, absent QDP, real staged state UI.
**DoD:** operator can launch/read/debug GA without touching QDP UI or modifying upstream branch.

### GA-MOD-22 — Performance and accuracy benchmarking
**Объём:** representative fixed gold media corpus (video/ru/en/mute/noisy/OCR/dynamic subtitles/carousels); quality metrics: scene boundary precision/recall & tolerance, ASR WER/CER, OCR text CER/recall, subtitle TP/FP/unknown rates, event precision/recall, per-stage throughput, memory/GPU and stored bytes, 10k/month planning throughput *estimate* without claiming achieved KPI. Configure thresholds from baseline evidence; stable run against pinned tool versions on CPU and optional GPU.
**Файлы:** tests/fixtures (rights-cleared small media or synthetic), benchmarks scripts, docs quality.
**Тесты:** deterministic offline, tested environment and accuracy report in repo, file hashes; perf bottlenecks measured.
**DoD:** measurement report with evidence, thresholds, limitations and reproducible commands.

### GA-MOD-23 — CI, compatibility matrix, Windows/Linux/PG17 E2E
**Объём:** run full unit/contracts, PostgreSQL 17 fresh/upgrade, end-to-end media→GA A003/A004, QDP contract through fake/real integration sandbox, B adapter, Analytics reader compatibility, Windows smoke & Linux CI. Supply conditional full E2E that can test against current QDP/SRE/Analytics branches on **throwaway integration branch only**, never contaminate GA. Guard against regression of existing Query Validation, PSI, taxonomies. Record actual CI job URLs and commit SHA.
**Файлы:** `.github/workflows/group-a-module.yml`, test_group_a_e2e.py, docs release evidence.
**Тесты:** Python compileall, unittest, PostgreSQL isolation/migrations, JS checks, local video smoke, no external payments, no rights/data exposure regressions.
**DoD:** no known P0/P1, matrix shows PASS/FAIL/NOT-RUN honestly with exact SHA.

### GA-MOD-24 — Merge-readiness, dependency handshake, release documentation
**Объём:** compare clean branch `main...feature/group-a-full-module`: only GA-owned files + minimal reviewed host hooks, latest main compatibility; check migration namespace clashes, replay gate, A003/A004 checksum and no changed peer data, QDP version availability, B/Analytics ports, null capabilities, rollback/feature flag, backup and deployment. Update release evidence + README integration section and tracker. Draft PR full description with dependency matrix, tests, migration order and unresolved external blockers. Old PR #26 marked superseded only after GA replacement proven (no automatic deletion/merge of main).
**Файлы:** integration guide, release report, docs tracker, PR metadata (no production merge).
**Тесты:** exact release SHA smoke, review diff/CI, `main` remains untouched.
**DoD:** independent PR ready for review/merge, installation recipe and smoke steps; declare any unresolved QDP/B upstream integration explicitly rather than faking E2E.

## 7. Критерии завершения всего проекта Group A (Release Gate)

**Functional:** all video A003 14 feature positions covered honestly (CTA may remain pending B evidence, so GA doesn't assert semantic CTA completeness); all 5 content kinds handled within actual media applicability; A004 counts, orders and OCR valid; no fabricated fields or missing-as-zero. `state=completed` only with truly measured required streams.

**Integration:** QDP A-ready consumed via durable event/inbox and rechecked; media SHA and IDs precise; SRE classification untouched; B consumes GA v1 without copying GA code, Analytics reads A003/A004; PSI reuse only with verified hash bridge; no direct QDP/PSI internals imported.

**Durability:** exact idempotent fingerprints, append-only versions, lease fencing, safe crash recovery, retry scopes, invalidations, terminal states. DB migrations fresh+upgrade+repeat with checksum evidence.

**Quality:** gold fixtures + quantitative scene/ASR/OCR/subtitle/audio evaluations, performance notes, safe CPU/GPU limits, platform/carousel coverage; clearly note unmet metrics and unsupported cases.

**Independent branch:** GA PR based on main, GA-only diff; branch does not include old QDP migrations or worker code. Main safe until merge. Commit record for each stage and latest release evidence.

**No misleading declaration:** CI offline/mock ≠ live provider media downloads or real paid model QA; B consumer contract success ≠ B production inference; Group A stage A1 task completed ≠ all Group A measurement complete.

## 8. Стандартные команды тестирования (адаптировать к реальному состоянию ветки)

```bash
python -m compileall -q intelligence/group_a
python -m unittest discover -s tests -p 'test_group_a_*.py' -v
# disposable PG17 only (never real database):
CM_TEST_PG_DSN='postgresql://.../contentmachine_test' python -m unittest tests.test_group_a_db -v
# required when JS is added:
node --check site/group-a.js
# explicit local worker smoke (no paid calls):
python -m intelligence.group_a.worker --once
# inspect differences and current branch before/after every stage:
git branch --show-current
git status --short
git diff --stat main...HEAD
git log -5 --oneline
```

Не обещать PASS без фактического запуска. Если в среде нет PostgreSQL, FFmpeg, Python dependencies или GitHub write permissions, зафиксировать `NOT RUN`/`BLOCKED` в трекере и довести всё остальное доступное за один запрос; не закрывать этап, где обязательный DoD недостигнут.

## 9. Обязательный файл текущего прогресса

Канонический путь в ветке: `docs/GROUP_A_IMPLEMENTATION_TRACKER.md`. Обязательно обновлять в **каждом** этапе, не после нескольких. Содержимое: current branch, base SHA, HEAD SHA, last finished step, next step, 25 checkbox строк (GA-MOD-00..24), краткий change-log по этапам, команды и результаты тестов, blocker/dependency notes, fingerprint схем. **Завершённый пункт перечеркнуть**: `- [x] ~~GA-MOD-NN: ...~~` и записать commit SHA. Незавершённый: `- [ ] GA-MOD-NN: ...` с progress notes и без объявления complete.

### Инструкция чату на каждый запрос «следующий этап»

1. GitHub: прочитать `docs/GROUP_A_IMPLEMENTATION_TRACKER.md` из canonical Group A branch, `GROUP_A_FULL_IMPLEMENTATION_PLAN.md`, `git status`/last commits; проверить актуальный main/QDP/SRE/Analytics head SHA только как read-only зависимости.
2. Определить **первый незакрытый** GA-MOD-NN. Если предыдущий этап частично завершён — закончить его, не прыгать дальше.
3. Составить подзадачи, реализовать код именно GA этапа, написать тесты, запустить доступные проверки; исправить обнаруженные дефекты, не скрывать FAIL.
4. Проверить diff на попадание в соседние модули и миграционную историю; не редактировать main и чужие ветки.
5. Обновить markdown progress tracker и release evidence; закрыть только действительно выполненный этап. Commit изменений на canonical Group A branch и сохранить SHA.
6. Сообщить пользователю: `GA-MOD-NN`, что реализовано, какие файлы, что протестировано и фактические результаты, SHA коммита, блокеры, следующий шаг; **не** начинать следующий большой этап в том же запросе.

### Recovery / безопасность параллельной работы

- Если branch HEAD изменился из другого чата между read и write, повторно прочитать его, делать atomic expected-SHA write, никогда не перетирать чужие изменения.
- Если GitHub write/branch API недоступен, не выдавать локальные правки за push; предоставить применимый patch/файл с понятным блокером.
- Если новый `main`/QDP поднял несовместимую версию, включить capability diagnostic и обновить integration matrix, а не вписывать недостоверные shim поля.
- Ни на одном GA-этапе не делать автоматический merge/закрытие чужих PR. Любая операция, затрагивающая чужой модуль, должна быть отдельным явно описанным handshake, а не тихим side effect.

## 10. Definition of Done для проверки владельцем

Проверяемая «готовность интегрировать» означает: новая ветка действительно создана от main, в diff нет QDP/PSI/Analytics целиком, код GA deployable с текущей core схемой, QDP-facing публичный порт версии v1 работает при наличии upstream и безопасно сообщает unavailable без upstream, все локальные типы медиа обрабатываются, подтверждённые измерения записаны в A003/A004, B получает факты только по проверенным IDs, E2E CI green на зафиксированном SHA, миграции repeat-safe, есть документация и ручной smoke walkthrough. Ни одно готовое внешнее API не должно понадобиться для offline regression тестов. В случае незавершённого upstream показывать **integration-ready with declared dependency** вместо неподтверждённого «fully production integrated».

**Финальный результат работы 25 запросов:** один полноценный standalone Group A module и один draft/reviewable PR в `main`, плюс проверяемый трекер, матрица зависимостей, миграции, контракты, тесты и доказательства работы.