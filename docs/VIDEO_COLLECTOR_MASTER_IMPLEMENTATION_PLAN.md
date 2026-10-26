# Content Machine — Video Collector V1 · единый план реализации и журнал прогресса

> **Единственный источник состояния работ по VC:** `feature/video-collector-v1`, PR [#33](https://github.com/BanSins7/ContentMachine/pull/33). Цель — **один полностью самостоятельный и готовый к merge модуль**, а НЕ только исправления. **main не менять.**
>
> Этот документ составлен по исходному полному русскому плану `ContentMachine_Video_Collector_FULL_INDEPENDENT_MODULE_IMPLEMENTATION_PLAN_RU.txt`; нумерация VC-MOD-00…16 сохранена. После каждой реализации обновлять **здесь**, в той же ветке: выполненный пункт зачёркивать **только после фактического DoD**, рядом писать SHA и результат тестов. Не закрывать этап из-за `queued` GitHub Actions.

## Правила каждого прохода

1. **Один пользовательский запрос «продолжай» = один следующий незакрытый логический этап**. Если остались ошибки/блокеры предыдущего — сначала закрыть их, затем начать следующий, без ложного `PASS`.
2. Перед изменениями прочитать текущий `main`, head ветки, draft PR #33, этот MD и актуальный CI. Синхронизировать код без потери других модулей PSI/KE/SP/SRE; `main` и параллельные feature branches не изменять.
3. Реализовать **код + SQL (при необходимости) + негативные тесты + DB QA + документацию** в том же VC branch. Коммитить. Не объявлять mock-provider результатом реального TikTok E2E.
4. После тестов: отметить каждый checkbox; **зачеркнуть строку этапа только когда DoD подтверждён**; записать точный commit SHA, passed/skipped/failed, реальные ограничения; следующий этап оставлять незачёркнутым.
5. Внешние системы: **Search Planner** владеет смысловой генерацией/QV, **VC** — техническим поиском и ingestion pipeline, **D1** — каноническими videos/discoveries/metadata, **SRE** — семантической релевантностью, **QDP** — квалифицированными данными. Не создавать их копий внутри VC.
6. Provider POST только после явного owner approval/held→release и durable intent; `unknown` результат не повторять автоматически. Никаких paid calls из offline tests/preview.
7. Source QV candidate_id — UUID, D1 video/discovery/source_request — BIGINT. Ни cast, ни фиктивных legacy search_queries. Сохранять полное query→task→video→hypothesis происхождение.
8. `provider done` не равен `video ingested`, а `ingested` не равен `qualified`. Без реальной SRE классификации `relevance_state=not_evaluated`.

## Журнал исполнения на 26.10.2026

| Этап | Состояние | Изменения | Подтверждение |
| --- | --- | --- | --- |
| **VC-MOD-00** | **CLOSED** | `d56037f`, `592b493`, `798304b` | Linux/Windows baseline CI успешно на 592b493 и 798304b; `main` baseline сохранён |
| **VC-MOD-01** | CODE COMMITTED; CI PENDING | `0784727`, `143b4b5`, `2b6d8c2` | `test_video_collector_ports_v1.py`, `test_video_collector_adapters_v1.py` в CI; latest run queued |
| **VC-MOD-02** | CODE COMMITTED; CI PENDING | `31c0deb`, `8e1166c` | 056/057, PostgreSQL QA, история миграций; latest PG17 queued; не утверждать успешно применилось |
| **VC-MOD-03** | CODE COMMITTED; CI PENDING | `dd80a57` — `qv_reader.py`, 15 contract tests, CI и docs | доверенное чтение из actual DB, immutable RTB/source; **без ACK** |
| **VC-MOD-04** | CODE COMMITTED; CI/PG17 PENDING | `7840286` — query_publisher.py, publisher_cli.py, 13 QA scenarios + docs | Atomic manifest/shards/ACK and offline rollback/replay tests added; actual PG17 seeded publisher and latest CI pending |
| **VC-MOD-05..16** | NOT STARTED | ниже | поэтапно |

### Этапы и Definition of Done

#### [x] ~~VC-MOD-00 — актуальная база и целостность ветки~~

- [x] Сверены SHA `main`, прежнего VC, PR #13/#20/#22/#24 и текущего draft #33.
- [x] Сохранён весь Collector Stage 2–9, единственный DTK transport, его API/UI/Worker/Inspector/tests/fixtures.
- [x] Сохранены PSI/KE/RTB маршруты `main`, не продублирован DTK connector.
- [x] Разведены конфликтные миграции старой ветки 016/017 в 054/055.
- [x] Проверены baseline SQL bootstrap и offline импорт (успешные CI в истории ветки).
- [x] Написаны `VIDEO_COLLECTOR_VC_MOD_00_BASELINE.md` и module integration guide.
- **DoD:** одна ветка, baseline tests и основной модуль доступны, `main` нетронут, старые PR не надо отдельно merge.

#### [ ] VC-MOD-01 — стабильные модульные контракты и независимые порты

- [x] Сверены `validated_queries/v1`, `validated_query_set/v1`, SRE/D1 identity types.
- [x] Добавлены `collector/ports.py`, DTO QV/Discovery/SRE и версионирование событий/ошибок.
- [x] Добавлены lazy adapters `adapters/main_qv.py`, `adapters/dtk_provider.py`.
- [x] Feature flags QV consumer, DTK live, SRE delivery независимы, import не запускает сеть.
- [x] Добавлены негативные contract tests и `VIDEO_COLLECTOR_PORTS_V1.md`.
- [ ] **DoD ещё открыть:** зафиксировать итоговые PASS на текущем коммите тестов ports/adapters и регрессий; при ошибке исправить.

#### [ ] VC-MOD-02 — единая аддитивная PostgreSQL схема

- [x] Нет изменений applied `main` SQL и уже существующих 054/055.
- [x] Созданы `056_collector_qv_manifest.sql`, `057_collector_discovery_outbox.sql`.
- [x] Таблицы manifest/shards/coverage, ingest page checkpoints, many-to-many D1 links, **один** SRE outbox.
- [x] Composite FK project/generation/publication, unique keys, hashes, immutability и NULL-safe SRE constraints.
- [x] Read-only `scripts/collector_schema_audit.py`; preflight останавливает несовместимые старые 016/017.
- [x] Созданы `test_video_collector_schema_v1.py`, `test_video_collector_schema_audit.py` и `VIDEO_COLLECTOR_SCHEMA_V1.md`.
- [ ] **DoD ещё открыть:** свежая PG17 база, upgrade, replay no-op, checksum failure, cross-project FK, Windows/Linux CI на актуальном SHA.
- **Не делать:** без доказательств переносить данные старой 016/017 prod БД в новый ledger.

#### [ ] VC-MOD-03 — Trusted Query Validation Reader и Converter

- [x] Читать настоящий `qv_outbox` `validated_queries_ready/v1`, не пользовательский JSON.
- [x] Проверить completed production `qv_runs`, embedding_mode `semantic_local`, `sqg_generations` и исходный SQG outbox.
- [x] Проверить approved C9 `tb_deep_publications`/RTB, taxonomy SHA/version/tree и human-review ancestry.
- [x] Проверить исходный SQG bundle SHA и QV config/model fingerprint в одной PostgreSQL read-only transaction.
- [x] Проверить **все** selected/rejected decisions и coverage 0..480, contexts/source_fact_ids/branch/parent chain, без пропусков.
- [x] Конвертировать selected UUID в `ValidatedQuerySet/v1` ≤50 (без silent truncate); хранить scores/branch/facts для будущего manifest.
- [x] Negative QA: wrong-project, test_fixture, incomplete, tamper/checksum, forged browser accepted, zero, replay.
- [x] Добавлены qv_reader.py, 15 негативных/позитивных offline DB-stub тестов, документация и CI step.
- [ ] **DoD pending:** убедиться в успешном CI на текущем SHA, реальный PostgreSQL read contract и сохранение всех предыдущих модулей; никакого ACK или provider POST.

#### [ ] VC-MOD-04 — durable QV publisher, sharding 480, ACK

**Файлы/коммит:** `7840286` (+ focused QA follow-up), `intelligence/collector/query_publisher.py`, `intelligence/collector/publisher_cli.py`, `tests/test_video_collector_qv_publisher_v1.py`, `docs/VIDEO_COLLECTOR_QV_PUBLISHER_V1.md`.

- [x] Один event→manifest; sorted (hypothesis,rank,UUID); deterministic shard size по default ≤20.
- [x] Создать QuerySet shards ≤50, без усечения всех 480; UUID linking + полная coverage.
- [x] Ноль selected → immutable `no_queries`, ноль tasks/POST.
- [x] В **одной DB транзакции** insert immutable manifest/query sets/coverage/links и ACK `qv_outbox.delivered_at`.
- [x] Atomic idempotent replay по SHA; `FOR UPDATE OF o` сериализует конкурентные writers; rollback/replay — offline QA. Настоящую PG race ещё проверить.
- [ ] **DoD:** actual PostgreSQL17 интеграционные тесты (0/1/20/21/480/481, race и crash), точный зелёный CI SHA; offline mock QA уже добавлены.
- **DoD:** настоящий QV event доступен Collector по backend trusted QuerySet ID/IDs без ручного JSON.

#### [ ] VC-MOD-05 — единый capability registry и технический Task Planner

- [ ] Один `registry.py` как authority по возможностям; source readiness ≠ rights/cost verification.
- [ ] DTK TikTok `keyword → search_posts` только; unsupported hashtag не конвертировать молча.
- [ ] Deterministic tasks: query grouping, all hypothesis/query IDs, fixed caps, stable hash.
- [ ] Preview без сети; provider adapter smoke только отдельным явным opt-in.
- [ ] QA multiple queries same task, unsupported operations, 480 shard boundaries, project split.
- **DoD:** один технический planner с документированным contract и без дублирования SP.

#### [ ] VC-MOD-06 — trusted Preview → held Commit → explicit Release

- [ ] API/UI загружают только backend-sealed QV manifests/shards, показывают coverage.
- [ ] Preview pinned query_set/source/policy/version, ноль платных действий.
- [ ] Commit создаёт project-scoped `search_run` + `source_requests` + `jobs` + provenance атомарно и в `planned_held`.
- [ ] Строгий plan_hash/optimistic concurrency/idempotency; нет dispatch при reload.
- [ ] Отдельный owner release, quota consent and bound provider request cap; всё held until release.
- [ ] Negative QA forged trusted state, stale hash, rights/limits, replay, cancelled/revoked.
- **DoD:** реальный SP → QV → VC held/released jobs, provider mock still zero POST before release.

#### [ ] VC-MOD-07 — DTK adapter и Worker resilience

- [ ] Один transport Evil0ctal DTK, capability `search_posts`, explicit readiness scope.
- [ ] Submit intent persisted before POST; lease generation/fencing, no auto second POST in `unknown`.
- [ ] GET poll known `task_id`, retry transient GET, clear terminal errors.
- [ ] Cancellation, budget/permissions recheck, provider clock/paging/cursor explicit.
- [ ] Tests restart/unknown submit/GET outage/leasing/concurrent workers, no live charge.
- **DoD:** безопасный provider executor, bounded external operations and retry policy.

#### [ ] VC-MOD-08 — Response Inspector и Normalizer lock

- [ ] Точный DTK v5.1.7 Page[Content] contract, video/photo/live filters.
- [ ] Canonical TikTok ID/URL/kind/duration/date/author; null ≠ 0 metrics.
- [ ] Сохранять valid/partial/rejected counts, provenance; no raw response leakage.
- [ ] Зафиксировать parser regression fixtures и inspector round-trip DTO.
- **DoD:** provider page отвечает однозначным нормализованным кандидатом без ложной «записи в D1».

#### [ ] VC-MOD-09 — atomic canonical Discovery Ingest

- [ ] Новый `collector/ingest.py` поверх существующего D1 без копии `videos`.
- [ ] Фактический upsert `videos`, observations `source_video_metadata`, `discoveries` и project scope.
- [ ] Все query→hypothesis→video many-to-many links, сохранение разных sources/occurrences.
- [ ] Page SHA checkpoint; duplicate replay same hash = reuse, mismatch = fail.
- [ ] Zero/partial/duplicates, date/metadata/metrics-null correctness.
- [ ] DB transaction wraps ingest page + checkpoint + pending SRE events; worker lease fence.
- [ ] PostgreSQL17 tests multiple projects/queries per video/idempotency/crash/rollback.
- **DoD:** реальные canonical video IDs по итогам provider result, без ложных completed.

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
- CI `queued` или `in_progress` — **не PASS**. У каждого этапа хранить SHA проверки.
