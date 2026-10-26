# Content Machine — Video Collector V1 · единый план реализации и журнал прогресса

> **Единственный источник состояния работ по VC:** `feature/video-collector-v1`, PR [#33](https://github.com/BanSins7/ContentMachine/pull/33). Цель — **один полностью самостоятельный и готовый к merge модуль**, а НЕ только исправления. **main не менять.**
>
> Этот документ составлен по исходному полному русскому плану `ContentMachine_Video_Collector_FULL_INDEPENDENT_MODULE_IMPLEMENTATION_PLAN_RU.txt`; нумерация VC-MOD-00…16 сохранена. После каждого коммита обновлять **здесь**: **зачёркивать название этапа, когда реализация кода завершена**, даже если CI ещё ожидается. Проверки отмечать ОТДЕЛЬНЫМ статусом `QA PENDING` / `QA FAILED` / `QA PASS`, указывать SHA и доказательства. Не выдавать `queued` за PASS.

## Правила каждого прохода

1. **Один пользовательский запрос «продолжай» = один следующий незакрытый логический этап**. Если есть доказанная ошибка предыдущего этапа — сначала исправить; при `queued` просто отметить QA PENDING и продолжать следующий этап.
2. Перед изменениями прочитать текущий `main`, head ветки, draft PR #33, этот MD и актуальный CI. Синхронизировать код без потери других модулей PSI/KE/SP/SRE; `main` и параллельные feature branches не изменять.
3. Реализовать **код + SQL (при необходимости) + негативные тесты + DB QA + документацию** в том же VC branch. Коммитить. Не объявлять mock-provider результатом реального TikTok E2E.
4. После тестов: отметить каждый checkbox; **зачеркнуть строку после внесённого коммита с реализованными программными задачами**, а не ждать очереди CI; в таблице и чек-листе QA указывать **отдельно** `PENDING/FAILED/PASS`, SHA и реальные ограничения. Если тесты падают, этап остаётся реализованным, но не QA-проверенным.
5. Внешние системы: **Search Planner** владеет смысловой генерацией/QV, **VC** — техническим поиском и ingestion pipeline, **D1** — каноническими videos/discoveries/metadata, **SRE** — семантической релевантностью, **QDP** — квалифицированными данными. Не создавать их копий внутри VC.
6. Provider POST только после явного owner approval/held→release и durable intent; `unknown` результат не повторять автоматически. Никаких paid calls из offline tests/preview.
7. Source QV candidate_id — UUID, D1 video/discovery/source_request — BIGINT. Ни cast, ни фиктивных legacy search_queries. Сохранять полное query→task→video→hypothesis происхождение.
8. `provider done` не равен `video ingested`, а `ingested` не равен `qualified`. Без реальной SRE классификации `relevance_state=not_evaluated`.

## Журнал исполнения на 26.10.2026

| Этап | Состояние | Изменения | Подтверждение |
| --- | --- | --- | --- |
| **VC-MOD-00** | **CLOSED** | `d56037f`, `592b493`, `798304b` | Linux/Windows baseline CI успешно на 592b493 и 798304b; `main` baseline сохранён |
| **VC-MOD-01** | **IMPLEMENTED / Linux QA PASS at 2b6d8c2; HEAD pending** | `0784727`, `143b4b5`, `2b6d8c2` | `test_video_collector_ports_v1.py`, `test_video_collector_adapters_v1.py`, общий Linux regression PASS (run 40291296342, SHA 2b6d8c2); latest head e31e1e4 ещё queued |
| **VC-MOD-02** | **IMPLEMENTED / QA PENDING** | `31c0deb`, `8e1166c` | 056/057, PostgreSQL QA, история миграций; latest PG17 queued; не утверждать успешно применилось |
| **VC-MOD-03** | **IMPLEMENTED / QA PENDING** | `dd80a57` — `qv_reader.py`, 15 contract tests, CI и docs | доверенное чтение из actual DB, immutable RTB/source; **без ACK** |
| **VC-MOD-04** | **IMPLEMENTED / QA PENDING** | `7840286` — query_publisher.py, publisher_cli.py, 13 QA scenarios + docs | Atomic manifest/shards/ACK and offline rollback/replay tests added; actual PG17 seeded publisher and latest CI pending |
| **VC-MOD-05** | **IMPLEMENTED / QA PENDING** | manifest_planner.py, registry/planner comments, ten unit tests, CI/concurrency | All QV shards and UUIDs covered; global cap and unsupported operations explicit; workflow not yet passed |
| **VC-MOD-06** | **IMPLEMENTED / QA PENDING** | manifest_access.py, manifest API, owner site/collector.js, tests | Project-scoped manifests, preview, held commit and explicit release; exact-HEAD PostgreSQL/Windows QA pending |
| **VC-MOD-07** | **IMPLEMENTED / QA PENDING** | `21e5e64` — worker.py fencing/recovery, stage8 regression updates, 11 new resilience QA tests, runbook | Fresh Linux/Windows CI and real PG race proof still pending; no DTK POST executed |
| **VC-MOD-08** | **IMPLEMENTED / QA PENDING** | provider_inspector strict source/endpoints, inspector_contract safe DTO, 13 offline tests, pinned fixture and runbook | Exact current-head CI queued; no live provider or D1 persistence verified |
| **VC-MOD-09** | **IMPLEMENTED / QA PENDING** | `ingest.py`, D1 checkpoint, project-bound video/metadata/discovery/QV links, pending SRE event, 10 offline DB tests | PG17 seeded workflow and actual worker end-to-end pending; no false completed |
| **VC-MOD-10..16** | NOT STARTED | ниже | поэтапно |

## Причина отставания CI и исправление

На 26.10.2026 у VC PR #33 накопились десятки запусков `pull_request` на устаревшие коммиты: новые были `queued`, не `failed`. На `143b4b5` ContentMachine Linux CI **completed success**; `0784727` Linux CI и KE fast QA **completed success**; более новые сценарии нельзя считать PASS до выполнения. В ветке добавлена `concurrency: group: ${{ github.workflow }}-${{ github.ref }}, cancel-in-progress: true` в три workflow (`tests.yml`, `ke-stage14-fast.yml`, `windows-d1-smoke.yml`). Это отменяет будущие устаревшие запуски ТОЙ ЖЕ ветки/PR и workflow, не касается main/прочих модулей. Запуски, созданные ДО появления concurrency в их собственном workflow, могут остаться в очереди — они не гарантированно отменятся автоматически. Падений финальных VC-MOD-02/03/04/05 по факту пока не подтверждено; после выхода новых CI из очереди исправлять любые реальные ошибки и не писать ложно `QA PASS`.

### Этапы и Definition of Done

#### [x] ~~VC-MOD-00 — актуальная база и целостность ветки~~

- [x] Сверены SHA `main`, прежнего VC, PR #13/#20/#22/#24 и текущего draft #33.
- [x] Сохранён весь Collector Stage 2–9, единственный DTK transport, его API/UI/Worker/Inspector/tests/fixtures.
- [x] Сохранены PSI/KE/RTB маршруты `main`, не продублирован DTK connector.
- [x] Разведены конфликтные миграции старой ветки 016/017 в 054/055.
- [x] Проверены baseline SQL bootstrap и offline импорт (успешные CI в истории ветки).
- [x] Написаны `VIDEO_COLLECTOR_VC_MOD_00_BASELINE.md` и module integration guide.
- **DoD:** одна ветка, baseline tests и основной модуль доступны, `main` нетронут, старые PR не надо отдельно merge.

#### [x] ~~VC-MOD-01 — стабильные модульные контракты и независимые порты~~ · **код реализован, QA ожидается**

- [x] Сверены `validated_queries/v1`, `validated_query_set/v1`, SRE/D1 identity types.
- [x] Добавлены `collector/ports.py`, DTO QV/Discovery/SRE и версионирование событий/ошибок.
- [x] Добавлены lazy adapters `adapters/main_qv.py`, `adapters/dtk_provider.py`.
- [x] Feature flags QV consumer, DTK live, SRE delivery независимы, import не запускает сеть.
- [x] Добавлены негативные contract tests и `VIDEO_COLLECTOR_PORTS_V1.md`.
- [x] Linux unit ports/adapters и общая Python regression: PASS на `2b6d8c2`, GitHub Actions [40291296342](https://github.com/BanSins7/ContentMachine/actions/runs/40291296342).
- [ ] QA последнего HEAD: повторить Linux/Windows и полные миграции после VC-MOD-02..05; latest checks queued.

#### [x] ~~VC-MOD-02 — единая аддитивная PostgreSQL схема~~ · **код реализован, QA ожидается**

- [x] Нет изменений applied `main` SQL и уже существующих 054/055.
- [x] Созданы `056_collector_qv_manifest.sql`, `057_collector_discovery_outbox.sql`.
- [x] Таблицы manifest/shards/coverage, ingest page checkpoints, many-to-many D1 links, **один** SRE outbox.
- [x] Composite FK project/generation/publication, unique keys, hashes, immutability и NULL-safe SRE constraints.
- [x] Read-only `scripts/collector_schema_audit.py`; preflight останавливает несовместимые старые 016/017.
- [x] Созданы `test_video_collector_schema_v1.py`, `test_video_collector_schema_audit.py` и `VIDEO_COLLECTOR_SCHEMA_V1.md`.
- [ ] **DoD ещё открыть:** свежая PG17 база, upgrade, replay no-op, checksum failure, cross-project FK, Windows/Linux CI на актуальном SHA.
- **Не делать:** без доказательств переносить данные старой 016/017 prod БД в новый ledger.

#### [x] ~~VC-MOD-03 — Trusted Query Validation Reader и Converter~~ · **код реализован, QA ожидается**

- [x] Читать настоящий `qv_outbox` `validated_queries_ready/v1`, не пользовательский JSON.
- [x] Проверить completed production `qv_runs`, embedding_mode `semantic_local`, `sqg_generations` и исходный SQG outbox.
- [x] Проверить approved C9 `tb_deep_publications`/RTB, taxonomy SHA/version/tree и human-review ancestry.
- [x] Проверить исходный SQG bundle SHA и QV config/model fingerprint в одной PostgreSQL read-only transaction.
- [x] Проверить **все** selected/rejected decisions и coverage 0..480, contexts/source_fact_ids/branch/parent chain, без пропусков.
- [x] Конвертировать selected UUID в `ValidatedQuerySet/v1` ≤50 (без silent truncate); хранить scores/branch/facts для будущего manifest.
- [x] Negative QA: wrong-project, test_fixture, incomplete, tamper/checksum, forged browser accepted, zero, replay.
- [x] Добавлены qv_reader.py, 15 негативных/позитивных offline DB-stub тестов, документация и CI step.
- [ ] **QA проверка ожидается:** убедиться в успешном CI на текущем SHA, реальный PostgreSQL read contract и сохранение всех предыдущих модулей; никакого ACK или provider POST.

#### [x] ~~VC-MOD-04 — durable QV publisher, sharding 480, ACK~~ · **код реализован, QA ожидается**

**Файлы/коммит:** `7840286` (+ focused QA follow-up), `intelligence/collector/query_publisher.py`, `intelligence/collector/publisher_cli.py`, `tests/test_video_collector_qv_publisher_v1.py`, `docs/VIDEO_COLLECTOR_QV_PUBLISHER_V1.md`.

- [x] Один event→manifest; sorted (hypothesis,rank,UUID); deterministic shard size по default ≤20.
- [x] Создать QuerySet shards ≤50, без усечения всех 480; UUID linking + полная coverage.
- [x] Ноль selected → immutable `no_queries`, ноль tasks/POST.
- [x] В **одной DB транзакции** insert immutable manifest/query sets/coverage/links и ACK `qv_outbox.delivered_at`.
- [x] Atomic idempotent replay по SHA; `FOR UPDATE OF o` сериализует конкурентные writers; rollback/replay — offline QA. Настоящую PG race ещё проверить.
- [ ] **QA проверка ожидается:** actual PostgreSQL17 интеграционные тесты (0/1/20/21/480/481, race и crash), точный зелёный CI SHA; offline mock QA уже добавлены.
- **DoD:** настоящий QV event доступен Collector по backend trusted QuerySet ID/IDs без ручного JSON.

#### [x] ~~VC-MOD-05 — единый capability registry и технический Task Planner~~ · **код реализован, QA ожидается**

- [x] Один `registry.py` как authority по возможностям; source readiness ≠ rights/cost verification.
- [x] DTK TikTok `keyword → search_posts` только; unsupported hashtag не конвертировать молча.
- [x] Deterministic tasks: query grouping, all hypothesis/query IDs, fixed caps, stable hash.
- [x] Preview без сети; provider adapter smoke только отдельным явным opt-in.
- [x] QA multiple queries same task, unsupported operations, 480 shard boundaries, project split.
- **Программный DoD:** единый technical planner и registry, версионированный `manifest_technical_plan/v1`, документы + 10 тестов. **QA**: [ ] свежий Linux/Windows CI на точном head SHA, проверка результатов Actions. Реальный DTK smoke — отдельный opt-in.

#### [x] ~~VC-MOD-06 — trusted Preview → held Commit → explicit Release~~ · **код реализован, QA ожидается**

- [x] API/UI загружают только backend-sealed QV manifests/shards, показывают coverage.
- [x] Preview pinned query_set/source/policy/version, ноль платных действий.
- [x] Commit создаёт project-scoped `search_run` + `source_requests` + `jobs` + provenance атомарно и в `planned_held`.
- [x] Строгий plan_hash/optimistic concurrency/idempotency; нет dispatch при reload.
- [x] Отдельный owner release, quota consent and bound provider request cap; всё held until release.
- [x] Negative QA forged trusted state, stale hash, rights/limits, replay, cancelled/revoked.
- **Программный DoD:** реальные manifest/query_set ID идут в API/UI, владельцем сохраняются held run/task/provenance через существующий транзакционный репозиторий, отдельный owner release и quota gate; новый код не вызывает DTK submit. **QA:** [ ] полный PG17/Windows CI текущего HEAD; [ ] E2E позитивный C9→QV→VC→held→release с готовой конфигурацией разрешений; [ ] подтверждение отсутствия внешних POST.

#### [x] ~~VC-MOD-07 — DTK adapter и Worker resilience~~ · **код реализован: `21e5e64`; QA ожидается**

- [x] Один установленный Evil0ctal DTK transport; разрешённый автоматический вызов `search_posts`, readiness, явная конфигурация источника.
- [x] `submit_intent` фиксируется ДО POST, квота обновляется атомарно с проверкой фактического изменения двух таблиц; никаких автоматических повторных POST для `unknown`.
- [x] Повторная проверка перед POST: актуальный `released`, отсутствие отмены, права policy, owner+generation+expiry lease (fencing).
- [x] Проверка ответа submit/task ID и GET без повторного POST, безопасная отсрочка GET после ошибок сети или неверного payload.
- [x] Recovery после падения: повторная проверка просроченного generation под run-first row locks; старый Worker не может изменить новое владение; `submitted` с сохранённым ID — только GET.
- [x] Отмена/смена разрешений/quota, строгая одна страница без придуманного cursor, terminal cancellation код.
- [x] Обновлены `worker.py` и существующие Stage8 unit tests, добавлены 11 новых негативных unit tests и `VIDEO_COLLECTOR_WORKER_RESILIENCE_V1.md`; CI step.
- [ ] **QA отдельно:** зелёные тесты точного HEAD Linux/Windows, настоящая PostgreSQL17 конкуренция claim/recovery/cancel; provider smoke только operator opt-in. GitHub Actions queue — не PASS.
- [ ] **Оставшаяся межэтапная зависимость VC-MOD-09/10:** provider `done` пока ещё НЕ гарантирует persisted D1; завершение Worker нужно переключить на durable D1 checkpoint в этих этапах.
- **Программный DoD:** Worker не делает повторного отправления запроса при неопределённом результате; recovered GET имеет durable task ID; lease и quota имеют дополнительные проверяемые fences.

#### [x] ~~VC-MOD-08 — Response Inspector и Normalizer lock~~ · **код реализован, QA ожидается**

- [x] Точный DTK v5.1.7 `Page[Content]` contract: task ID/tool/endpoint, video/photo/live; одна страница и explicit overflow, без автоматической пагинации.
- [x] Canonical TikTok string ID/URL/kind, milliseconds duration, UTC publication/observation dates, author handle/id; `null` ≠ измеренный `0` для метрик.
- [x] `valid`/`partial`/`rejected`, детерминированные reason histograms, provenance проекта/QV/provider task; нет сырого ответа, media URLs, caption, tokens или cursor в экспортируемом DTO.
- [x] Версионированный `collector.inspected_page/v1` с SHA-256 и обратной строгой валидацией; SHA служит integrity, **не** доказательством авторизации или факта D1 ingest.
- [x] Зафиксирован fixture `tests/fixtures/vc_dtk_inspector_page_v1.json` (valid, partial, photo, duplicate) и 13 новых offline QA tests `test_video_collector_inspector_contract_v1.py`, существующий Stage9 Inspector suite сохранён.
- [x] Добавлена документация `docs/VIDEO_COLLECTOR_INSPECTOR_V1.md`, индекс и выделенный Linux CI step.
- [ ] **QA отдельно:** точный HEAD Linux/Windows CI и полный Python regression, disposable PostgreSQL интеграция с будущим D1 (VC-MOD-09/10); текущие очереди не считать PASS.
- [ ] **Межэтапная зависимость:** Worker пока ещё помечает provider `done` как completed; VC-MOD-09/10 должны требовать атомарной записи D1 checkpoint.
- **Программный DoD:** чистый, детерминированный и валидируемый `InspectedPage → safe DTO → ProviderPage` handoff без внешних побочных эффектов и без ложных claims о сохранении роликов.

#### [x] ~~VC-MOD-09 — atomic canonical Discovery Ingest~~ · **код реализован, QA отдельно ожидается**

- [x] Новый `collector/ingest.py` поверх существующих D1 `videos`, `source_video_metadata`, `discoveries`; **ни одного дублирующего видео-каталога или изменения applied migrations**.
- [x] Реальные canonical `video_id` / metadata / discovery в рамках одной SQL-транзакции, без записи метрик и ложной relevant classification.
- [x] Полные QV TEXT candidate→hypothesis→video many-to-many links через `collector_video_hypotheses` и `collector_page_video_queries`; проект из DB, не из пользовательского JSON.
- [x] Immutable `collector_ingest_pages` checkpoint вставляется **один раз с окончательными счётчиками**; SHA совпал → replay; иной SHA/owner → 409.
- [x] Zero/partial/duplicates, `null` vs `0`, одну страницу/overflow; сохранены глобальные canonical видео без per-project смешивания.
- [x] Транзакция включает все D1 записи + checkpoint + `collector_sre_outbox` с `relevance_state='not_evaluated'`, Worker generation+lease fencing и source policy validation.
- [x] Добавлены 10 offline DB-stub QA тестов, runbook `docs/VIDEO_COLLECTOR_D1_INGEST_V1.md`, отдельный CI step.
- [ ] **QA отдельно:** точный HEAD Linux/Windows CI и настоящие seeded PostgreSQL17 tests multiple projects/queries/replay/race/crash/rollback; offline фейки не доказывают PG isolation.
- [ ] **Следующий межэтапный gate VC-MOD-10:** Worker пока ещё может завершить provider `done` без D1 receipt. Должен быть вызов ingest и завершение только после committed page.
- **Программный DoD:** реальный D1 transactional writer/receipt/outbox, сам `ingest_page` никогда не делает `jobs.completed` без VC-MOD-10.

#### [ ] VC-MOD-10 — Worker completion и ingest recovery

- [ ] Terminal provider `done` → Inspector → durable D1 ingest → `completed` ONLY after commit.
- [ ] Explicit `ingest_pending`, `inspection_failed`, `unknown`, `failed`; never misreport.
- [ ] Known task GET recovery, no duplicate POST; lost worker ACK replays same checkpoint.
- [ ] Status/runs/counters read actually committed D1 occurrences.
- [ ] Recovery/cancel/crash PostgreSQL integration tests.
- **DoD:** exactly-once observable ingest semantics and truthful run state.

#### [ ] VC-MOD-11 — durable SRE handoff

- [ ] Project-scoped versioned `collector.sre_ready/v1` for each D1 committed video.
- [ ] D1 BIGINT, publication SHA/version, query IDs/hypothesis IDs, evidence type.
- [ ] Durable enqueue transaction with D1, SRE may be disconnected → `pending`.
- [ ] Idempotent subscriber/lease/retry/ACK and adapter to actual current SRE branch contract.
- [ ] Never set SRE match from keyword search; initial state `not_evaluated`.
- [ ] Integration QA with SRE present/absent/project isolation.
- **DoD:** one-way safe VC→SRE candidate handoff; QDP reads only real qualified SRE output.

#### [ ] VC-MOD-12 — complete API/UI, results/trace

- [ ] QuerySet list/get + lineage/coverage; real runs, task status, issues/videos/checkpoints.
- [ ] Owner/project-scoped same-origin endpoints and UI; no manual JSON production path.
- [ ] Trace C9→SP→QV→Provider→D1→SRE, counters not fabricated.
- [ ] Empty/failure/recovery states and explanation of no_queries.
- [ ] UI behavior/security/HTTP contract tests across project scopes.
- **DoD:** complete user workflow in Content Machine site, not just backend CLI.

#### [ ] VC-MOD-13 — recovery, quotas, retention

- [ ] Crash after submit, unknown POST reconciliation, retry GET only with known task ID.
- [ ] Durable lease fences, unknown/quota bounded, cancellation safe.
- [ ] Retention rules for raw/candidate metadata, cleanup without deleting audit identity.
- [ ] Explicit only one-page DTK V1 until real provider paging verified; no invented cursor.
- [ ] CI failure matrix for network/DB/process/perms/no-query.
- **DoD:** safe recovery, truthful statuses and bounded provider cost.

#### [ ] VC-MOD-14 — packaging, flags, CLI, operator docs

- [ ] One importable, independently configurable and feature-flagged VC module.
- [ ] DB migration order, publisher/worker/SRE run entrypoints, health/diagnostics/flags.
- [ ] Windows/Linux launch and environment examples; no credentials in repo/logs.
- [ ] Operational runbook and explicit rollout/rollback (roll-forward records).
- [ ] No reliance on old PR #13/#20 or simultaneous SRE/QDP merge.
- **DoD:** one branch can be installed/disabled/merged without manual file copy.

#### [ ] VC-MOD-15 — full E2E and compatibility QA

- [ ] Disposable PG17: RTB/C9 → SP/QV → publisher → Collector → mock DTK → Inspector → D1 → SRE-ready event.
- [ ] Zero, 1, 20, 21, 480 selected; multi-project, multiple hypotheses/video.
- [ ] Matrix T01..T100 incl. positive + negative + project-scope, durability, crashes.
- [ ] Linux + Windows Actions; second-run migration no-op, old db upgrade.
- [ ] Separately opt-in **real local DTK smoke**; mock CI does not validate public TikTok network.
- **DoD:** complete reproducible E2E with documented actual QA evidence.

#### [ ] VC-MOD-16 — final merge-ready release and rollback gate

- [ ] Sync with latest `main`; resolve ALL conflicts without overwriting parallel branches.
- [ ] CI green on exact final merge candidate SHA, incl PG17 and cross-module E2E.
- [ ] No old standalone PR dependency, no duplicate migrations/provider/queues.
- [ ] Release checklist, feature flags default off, explicit operator consent, no missing credentials.
- [ ] Deployment order/migration checksum audit, preflight/backups/rollback and support runbook.
- [ ] Final reviewer summary, clean PR #33 changed-files audit and compatibility attestations.
- **DoD:** **один** `feature/video-collector-v1` → `main` merge, standalone full VC product functionality.

## Test mapping исходной спецификации (100 проверок)

- T01–T16, T26–T28: VC-MOD-03 QV source provenance, real decisions, no fixture authorization.
- T17–T28: VC-MOD-04 manifest/shards/zero/ACK/duplicate.
- T29–T42: VC-MOD-05/06 registry, planner and strict user controls.
- T43–T46: VC-MOD-02 migration/fresh/upgrade/checksum.
- T47–T53: VC-MOD-06 commit/release/idempotence/quotas.
- T54–T60: VC-MOD-07/13 worker lease/unknown/cancel.
- T61–T69: VC-MOD-08 Inspector/Normalizer.
- T70–T81: VC-MOD-09/10/13 canonical ingest, replay/paging.
- T82–T84: VC-MOD-11 SRE durability.
- T85–T95: VC-MOD-12/13 UI/trace/observability.
- T96–T99: VC-MOD-15 DB/E2E multiple projects/shards.
- T100: VC-MOD-16 final one-PR gate.

## Где смотреть прогресс и обязательные доказательства

- PR: https://github.com/BanSins7/ContentMachine/pull/33
- Stage 00: `docs/VIDEO_COLLECTOR_VC_MOD_00_BASELINE.md`
- Stage 01: `docs/VIDEO_COLLECTOR_PORTS_V1.md`
- Stage 02: `docs/VIDEO_COLLECTOR_SCHEMA_V1.md`
- Stage 03: `docs/VIDEO_COLLECTOR_QV_READER_V1.md` (после реализации)
- Basic QA: `python -m unittest discover -s tests -p 'test_video_collector_*.py' -v`; full `python -m unittest discover -s tests -v`; `python -m compileall -q app.py intelligence/collector`; PG17 `CM_TEST_PG_DSN=...`; JS `node --check`.
- CI `queued` или `in_progress` — **не PASS**. Но реализованный в коде этап нужно зачёркивать и обозначать как **IMPLEMENTED / QA PENDING**, а не оставлять невыполненным. У каждого этапа отдельно хранить SHA тестов и их результат. Проверенный release gate VC-MOD-16 по-прежнему требует успешного CI.
