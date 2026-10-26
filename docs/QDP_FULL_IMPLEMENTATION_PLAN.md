# CONTENT MACHINE — QDP COMPLETE INDEPENDENT IMPLEMENTATION PLAN

> **Главный файл для нового чата.** Ветка: `feature/qualified-data-pipeline-v1-20261023`, PR #24. **Никогда не создавать вторую QDP-ветку, не переименовывать текущую и не менять main/peer-ветки по умолчанию.**
>
> Источники требований: `ContentMachine_QDP_COMPLETE_IMPLEMENTATION_PLAN_RU(1).txt` (Q1–Q7, FIX-00…19, QA-001…120), согласование пользователя о независимом integration-ready модуле. Это план для QDP, а не заявление, что реальные SRE/Collector/A/B/C уже подключены.

## КАК ИСПОЛЬЗОВАТЬ ЭТОТ ФАЙЛ

1. Перед любой командой «следующий этап»: открыть **этот файл**, `QDP_IMPLEMENTATION_TRACKER.md`, `QDP_RELEASE_EVIDENCE.md` и актуальный HEAD данной ветки. Не доверять старым SHA, не приписывать другим модулям готовность.
2. Найти **первый незачёркнутый FIX**; если у более раннего FIX есть незакрытые проверки, сначала закрыть их, не выдавая за успешные тесты предыдущих коммитов.
3. Один FIX — один полноценный проход: аудит → код/миграции → unit+PG17 tests → CI exact SHA → отчёт → обновление плана.
4. Только когда **все QDP-owned DoD данного FIX подтверждены**, заменить `- [ ] **FIX-NN**` на `- [x] ~~FIX-NN~~`; добавить Git SHA, результаты тестов и дату. Внешнюю LIVE-интеграцию вести отдельно как BLOCKED/непроверенную — отсутствие соседней ветки не запрещает завершить QDP адаптер.
5. Нельзя зачеркивать этап, если тесты только добавлены, но не запускались; частично готовое фиксировать `IN_PROGRESS` в tracker. Никогда не подменять provider data, результат A/B/C, SQL release migration или пройденные тесты fixtures.
6. Коммитить изменения в **этой же QDP-ветке**, учитывать возможные конкурентные изменения HEAD через expected SHA. По умолчанию не делать merge в main.
7. В конце каждого этапа обновить этот Markdown, tracker и evidence. Следующий чат должен иметь достоверную точку продолжения.

**Статусы:** `TODO` — ещё нет; `IN_PROGRESS` — код/тесты частично готовы; `BLOCKED_EXTERNAL` — QDP адаптер готов, реальный peer отсутствует; `DONE` — QDP-owned реализация и её QA доказаны. Зачёркивать только `DONE`.

## Архитектура, которую нельзя нарушать

`C9 publication → Search Planner validated_queries/v1 → Video Collector → D1 canonical videos/discoveries → SRE LIVE (embeddings rank-all + Opus 5.5 classify-all) → QDP qualified/review/excluded → exact D1 enrichment → independent A/B/C preparations → durable outbox/ACK → Group A/B/C executors → verified Unified Analytics`.

- QDP **не** классифицирует видео, не запускает Opus/DTK, не скачивает MP4, не придумывает метрики, scores, ASR/OCR или downstream completions.
- `SRE match` ≠ `QDP qualified` ≠ `QDP A/B/C input-ready` ≠ `Group completed` ≠ `Analytics qualified`.
- Всегда 1 `qdp_items(run_id,video_id)` на canonical D1 publication; хранить ВСЕ matched leaf category UUIDs, human overrides, источники, версии и хеши.
- Unknown/null не превращать в zero/no_match. Исторические snapshots immutable, чтение current требует revalidation, изменения human/source → invalidate.
- Браузерные labels и claims о completed не являются авторитетными. Всё перепроверяется по PostgreSQL.
- Тесты с mock/fixtures доказывают контракт QDP, но не реальное платное LIVE E2E.

## ФАЗА I — TRUSTED SRE + PostgreSQL + QUALIFIED SET

- [ ] **QDP-FIX-00 — Baseline Audit, Ownership & Contract Freeze.** Зафиксировать QDP/main/peer SHAs, текущую схему, реальный PR/CI baseline; создать dependency matrix, typed integration ports, ID/event/schema contracts и guide. **DoD:** QDP-owned файлы в существующей ветке, regression unit + disposable PG17 + CI exact stage SHA, никакого повреждения main. **Реализовано в коде:** коммит `c3132d85`, 10 файлов. **Незакрыто:** новый CI SHA не появился; поэтому пока не зачёркивать.

- [ ] **QDP-FIX-01 — Strict LIVE SRE Intake.** `intake.py`: status completed + execution_mode live, project scope, approved taxonomy, verified models, no offline/legacy/synthetic; direct DB reader + eligible-run picker + fail-closed при отсутствии колонок peer. **DoD:** fixture/legacy/unapproved/foreign/failed reject; live metadata-only valid; не было provider calls. **Реализовано в коде:** `cc861d98`, `0d95525f`; требует независимой CI/PG17 проверки, НЕ считать реальным SRE E2E.

- [ ] **QDP-FIX-02 — Exact Video×Hypothesis Coverage & Evidence/Provenance.** Принять СТРОГО `sre-run-plan/v1` items (или SRE-owned `content_versions` с теми же полями): каждая пара из immutable manifest имеет ровно один result; проверять content ID/hash, все approved leaf category UUID, SRE project video link, grounded evidence, stage provenance, human label scope. **DoD:** 100×20=2000 пар, пропуск/дубль/подмена источника/синтетический stage fail closed; targeted per-video reread работает. **В коде:** `coverage.py`, integration в `intake.py`/`repository.py` и тесты добавлены в коммитах `280c3fa9` и `17e1a553`; QA на новом SHA ожидается. Перед зачёркиванием обязательно проверить совместимость с будущим LIVE producer.

- [ ] **QDP-FIX-03 — Forward-only PostgreSQL Schema Reconciliation.** Добавить QDP-owned migration: incoming SRE inbox, immutable manifests, normalized lineage, QDP outbox/deliveries, receipts, invalidation/current views/индексы. Совместить с main `030–039` и peer `016/017/018` в dependency-safe порядке, не переписывая применённые файлы и чужую SQL-ветку. **DoD:** fresh PG17 + upgrade seeded PG17 + repeat no-op + checksum failure tests; schema owner handoff.

**FIX-03, кодовая часть IN_PROGRESS (26.10.2026):** миграция `040_qdp_integration_foundation.sql` добавляет `qdp_sre_inbox`, `qdp_run_manifests`, `qdp_item_lineage` (для positive/negative/review), `qdp_outbox`, `qdp_outbox_deliveries`, `qdp_delivery_receipts`, `qdp_invalidations`, `qdp_unrevoked_preparations` и индексы. `schema.py` добавляет read-only preflight и защиту неизменности оригинального Git blob `016`. Добавлены static/PG contract tests. Коммиты: `19cffe41`, `a2414dac`, `bb2a6144`, `64491da1`. **Не зачёркивать до успешного exact-SHA PG17 fresh/upgrade/checksum QA**; в QDP-ветке пока нет live SRE-полей и внешних consumer схем.

- [ ] **QDP-FIX-04 — Deterministic Qualified Set Resolver.** Вынести `qualification.py`; 1 canonical video, N positive H, latest human override/veto, unknown→review, all final no_match→excluded; SHA/lineage/quality flags и exact category mapping. **DoD:** multi-label, human match/veto, unreadable content, no query-label inference, deterministic hashes.

**FIX-04, кодовая часть IN_PROGRESS (26.10.2026):** создан `qualification.py` с `resolve_qualified_set(rows, strict=True)`; legacy `contracts.qualify_sre_rows()` сохранён через совместимый wrapper. Детерминированное решение по canonical `video_id`, sorted multi-label refs, human correction per-leaf, `no_match` veto / `match` promotion только с читаемым content; `uncertain`, `not_evaluated`, inaccessible match не считаются excluded. Вычисляются `selection_sha256`, per-video SHA и `review_watermark`. В `repository.create_run` fingerprint теперь включает SRE manifest + selection + review hash и повторный idempotency key после коррекции даёт 409, Worker сверяет matched category/result snapshot. Добавлены `tests/test_qdp_qualification.py` и human veto PG17 tests. Коммиты: `31f5e199`, `4054a3ec`. **НЕ ЗАЧЁРКНУТО:** точный CI/PG17 ещё не прошёл, GitHub Actions в очереди. Настоящая SRE-классификация не симулируется.

- [ ] **QDP-FIX-05 — SRE Outbox → QDP Inbox.** Транзакционно принять подтверждённое SRE completion/event: authoritative DB reread, event hash, replay, quarantine, ACK only after QDP durable commit; AUTO paid disabled. До готового SRE producer — реализовать адаптер и contract tests, не маскировать его тестовым producer в prod. **DoD:** duplicate/changed fingerprint/crash safe.

- [ ] **QDP-FIX-06 — Immutable QDP Run/Jobs/Idempotency.** Пинить SRE taxonomy/content/manifest/review watermarks и config; создать 1 dedup job per qualified video транзакционно, корретная empty/no-work и >10k behavior; reuse existing API. **DoD:** concurrent same key yields 1 run, conflict on changed config, exact counts, reproducibility.

## ФАЗА II — EXACT D1 HYDRATION + A/B/C PREPARATION

- [ ] **QDP-FIX-07 — Metadata Exact Source Enrichment.** `provenance.py`/`enrichment.py`: SRE content version → exact metadata observation/source/version/discovery ID, permission/freshness; no cross-source silent fallback. **DoD:** M1 vs later M2 cross-source, unknown published_at stays NULL, D1 FK/lineage verified.

- [ ] **QDP-FIX-08 — Metric Snapshot Enrichment.** Bounded same-video/source/policy metric observations, version/precision/real timestamp/nullable views/score config; Group C gets ≥2 compatible measured observations, no computed breakout_score. **DoD:** 0/1/2 points, definitions mismatch, correction, revoked metrics, no A/B side effect.

- [ ] **QDP-FIX-09 — Media Asset Readiness.** Actual existing asset ID/source/kind/expiry/path acceptance for Group A/B; no raw URL or phantom MP4; carousel unsupported by video A1 is blocked; Media Acquisition remains separate owner. **DoD:** absent/wrong-source/expired/video/carousel cases and independent C readiness.

- [ ] **QDP-FIX-10 — Three Independent Preparation Contracts.** `preparation.py`: exact input IDs/hash, individual A/B/C readiness and reason codes, group-specific currently-readable view; immutable partial/blocked states. **DoD:** all/any/none ready combinations; missing C policy cannot suppress A/B read.

## ФАЗА III — RELIABLE EXECUTION, CONSUMERS, INVALIDATION

- [ ] **QDP-FIX-11 — PostgreSQL Worker Hardening.** SKIP LOCKED claims, generation fencing, immutable prepare+3 group+outbox commit, exhausted job reaper, bounded retries/backoff, cancel/restart, truthful run states. **DoD:** 2/10 workers; injected crash before/after commit; no duplicate/write after lost lease; never execute another module's job.

- [ ] **QDP-FIX-12 — Transactional QDP Outbox + Consumer ACK.** Per A/B/C event+delivery journal, attempts/backoff, event payload pointers/hash, independent durable receipt and ACK after own job transaction. **DoD:** 50 duplicate replays→1 consumer job; crashes and mixed A-success/B-failure handled.

- [ ] **QDP-FIX-13 — Group A A1 Real/Contract Handoff.** Adapter to Group A PR #26 `create_task`/`group_a.analyze_video`; exact media FK/eligibility, job idempotency, no paid calls from QDP. **DoD:** QDP-side contract and PG tests; FFprobe genuine MP4 E2E when peer integration possible; do not claim full A OCR/ASR.

- [ ] **QDP-FIX-14 — Group B/C and Unified Analytics Compatibility.** Typed B/C inbox+job interfaces, B waits compatible A prerequisites, C requires compatible metrics/cohorts/config; analytics accepts actual completed outputs only. **DoD:** contract tests, no fake B/C completed, versioned payloads, clear peer handoff blockers.

- [ ] **QDP-FIX-15 — Human Veto, Source Invalidation, Explicit Refresh.** Event-driven invalidation+generation watermark, current-read re-evaluation; send revocation to downstream and keep permitted historical immutable rows; refreshed input only in new revision/run. **DoD:** final match revoked disappears immediately from current, race cannot resurrect stale readiness.

## ФАЗА IV — USER CONTROL + OPERATIONS

- [ ] **QDP-FIX-16 — API + Full QDP Studio.** Eligible LIVE SRE run picker (no UUID paste), preview/config/create, per-video/evidence/provenance, independent readiness, handoff/diagnostics/events/history/refresh; preserve local Host/Origin/mutation protections. **DoD:** safe DOM, pagination and no paid GET; UX doesn't fake completion.

- [ ] **QDP-FIX-17 — Observability, Scaling, Lifecycle.** Counters for qualified vs ready vs delivered vs completed, outbox backlog, failures/stale lease, cursor pagination, shutdown/startup, measured 10k videos and EXPLAIN PG indexes. **DoD:** bounded memory, non-quadratic queries, measured throughput recorded (no invented SLO).

## ФАЗА V — VERIFICATION + RELEASE HANDOFF

- [ ] **QDP-FIX-18 — Acceptance & Chaos Matrix.** Implement/run all QA-001..QA-120 against exact code SHA, disposable PG17 fresh+upgrade, CI Linux/Windows, concurrency/restart. LIVE C→SP→VC→D1→SRE→QDP→A1 only when peers actually exist and paid smoke explicitly approved. **DoD:** evidence report, every unavailable live peer explicitly marked, no false green.

- [ ] **QDP-FIX-19 — Release-Ready Standalone Module.** Final schema manifest, changelog, updated user docs, consumer ABI, dependency matrix, reviewer signoff, rollback/runbook and main integration guide. **DoD:** QDP-owned tests green, all P0/P1 QDP defects resolved; main merge later only after peer integration owner review. **Never merge automatically.**

## ОБЯЗАТЕЛЬНЫЙ ПОРЯДОК И ПЕРЕДАЧА ДРУГОМУ ЧАТУ

`00 → 01 → 02 → 03 → 04 → 05 → 06 → 07 → 08 → 09 → 10 → 11 → 12 → 13 → 14 → 15 → 16 → 17 → 18 → 19`.

Рядом с этим файлом:
- `docs/QDP_IMPLEMENTATION_TRACKER.md` — SHA/status per stage и текущие blocker.
- `docs/QDP_RELEASE_EVIDENCE.md` — реальная QA/CI доказательная база.
- `docs/QDP_DEPENDENCY_MATRIX.md` — ownership всех соседних модулей.
- `docs/QDP_EVENT_AND_ID_CONTRACTS.md` — версионированные ID/events.
- `docs/QDP_MIGRATION_RECONCILIATION.md` — порядок SQL и исторические checksums.
- `docs/QDP_INTEGRATION_GUIDE.md` — как подключить законченный QDP к будущему SRE/Collector/A/B/C.
- `docs/QUALIFIED_DATA_PIPELINE_V1.md` — сохранённая исходная архитектура.

**Актуальное ограничение:** GitHub Actions на новых коммитах после `f40949c8` пока не обнаружены. Предыдущий зелёный CI проверял лишь базовый QDP; FIX-00/01/02 не должны быть зачёркнуты без выполнения собственных test gates.
