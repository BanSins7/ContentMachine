# CONTENT MACHINE — UNIFIED ANALYTICS ENGINE FULL STANDALONE MODULE
## Implementation plan v1.0 · UA-MOD-00 → UA-MOD-32
**Date:** 2026-10-26. **Repository:** BanSins7/ContentMachine.
**Canonical new branch:** `feature/unified-analytics-full-module`. **Base:** main SHA `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` (verify current heads on every stage).
**Legacy source branch (PRESERVE):** `feature/analytics-engine-qualified-pipeline-20261023`, SHA at audit `12921968ef3d5c6f5e706fd8e625c54733405f3b`, PR #23. Do NOT close, delete, overwrite or force-rebase it.
**Stage policy:** one stage = one user request; each stage implemented, tested as available, committed and marked in tracker.

## 0. Product mission
UAE — единственный слой квалифицированного объединения: получает verified SRE relevance & QDP provenance + Group A measured facts + Group B semantics + Group C persisted C0-C7 metric scores, связывает exact canonical ID/version/source/policy и создаёт неизменяемые Unified Analytics Snapshots. Затем выполняет *descriptive analytics* по сопоставимым группам, выдаёт доказательную базу для будущих Patterns & Insights. UAE НЕ является второй SRE, новым Collector, Group A/B LLM или Group C scorer. Не выполняет LLM, ASR/OCR, скачивание медиа, сбор метрик с платформ или генерацию публикаций.

## 1. Non-negotiable architecture rules
1. Работа только в `feature/unified-analytics-full-module`. **main, PR #23 и все соседние branches read-only**, до отдельного поручения о merge. Не копировать QDP, A, B, C, SRE или PSI code.
2. Источник старого UAE PR #23 сохраняем. Переиспользовать код только после аудита: `intelligence/analytics/contracts.py, service.py, aggregate.py, queue.py, worker.py, http_api.py, site/analytics.js`; `metrics.py` принадлежит Group C и не должен стать второй live scoring implementation. Перенос в новый owner `intelligence/unified_analytics/` с сохранением API compatibility через минимальный adapter.
3. **Старые миграции 016/017/018 из PR #23 не копировать как активные в `intelligence/migrations`.** `main` уже имеет `016_query_validation.sql` и десятки поздних миграций. Только заново проверенные *forward-only additive* UAE SQL после dependency ordering audit, возможен один новый UA migration set.
4. Главный ID публикации — `videos.id` (BIGINT). `project_id` — 32 lowercase hex, run IDs — проверяемый UUID там, где реально он UUID. A/B/C/SRE/QDP имеют отдельные ключи и хеши и никогда не взаимозаменяются.
5. UAE может считать публикацию qualified **только** при наличии текущего trusted SRE match/approved review, Qualified QDP trace, Group A completed, Group B completed/no review, Group C score complete и всех необходимых exact evidence refs; совместимость source/policy/version обязательна.
6. Missing/unknown/not_run/partial не превращаются в false, 0, no_match or success. image_post не притворяется каруселью, поскольку у B2 нет утверждённого image_post. C не ограничен [0,100] и не нормализован; UAE его не пересчитывает.
7. Descriptive metrics — медианы, перцентили, соотношения и распределения по качественной сопоставимой выборке. Отдельно документировать sample sizes, missingness, multi-label membership и ограничения. Никаких заявлений о причинности и статистической значимости без собственного вычислительного доказательства.
8. Reads re-evaluate current source policy, retention and evidence eligibility; записи с отозванными/истёкшими исходными данными не публикуются как valid. Исторические immutable records сохраняются лишь согласно действующим правилам хранения и удаления.
9. Queue/skipl-locked + lease fencing/retries/idempotency; transactional outbox; every run/audit has pinned config/version. No paid provider calls or background autopoll without explicit opt-in.
10. Каждый пользовательский запрос «продолжай работу» означает один текущий этап `UA-MOD-NN`. Сначала читать HEAD, plan и `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`, затем точечно реализовать, добавить тесты, запустить доступные, закоммитить и обновить tracker. **Код выполнен, тесты не прошли/недоступны** → `[x] ~~UA-MOD-NN~~ (IMPLEMENTED; QA: FAIL/BLOCKED/NOT RUN)` с точным объяснением; PASS только по фактически зелёным тестам. Незаконченный код остаётся `[ ]`.
11. PR к main сначала Draft, не merge. Полная готовность только после реального межмодульного handoff E2E или честной фиксации внешних blockers.

## 2. Current inventory, verified 2026-10-26
- `main`: project_registry, D1 videos, SRE/A/B/C foundational migrations already exist; main migrations up to `053_ke_c_qualified_execution.sql`. `main` пока не содержит полноценного `intelligence/analytics` runtime.
- PR #23: 18 own commits, 143 behind current main in compare; its 016–018 migrations conflict in naming/order; QA and user UI present; do not mutate.
- PR #24 QDP: Qualified Set / prep and versioned integration, independent ongoing implementation.
- PR #35 Group A, PR #36 Group B, PR #37 Group C; each is independently developed and not automatically integrated.
- SRE PR #22 uses Embeddings+Opus 5.5 for relevance by design; UAE uses its verified output only.
- Group C independent branch now owns pure scorer after GC-MOD-07; UAE must not own `compute_c_metrics`.

## 3. Integration chain
```text
Research C Taxonomy → SP/VC → D1 video_id + SRE classifications
                                      |
                                  QDP Qualified
                                      |
                       ┌──────────────┼────────────────┐
                  Group A         Group B          Group C
              technical/media   B1..B8 meaning   C0..C7 score
                       └──────────────┼────────────────┘
                                      v
                       UAE exact-version Join + Gate
                                      |
                      immutable qualified/partial snapshot
                                      |
                     source-policy filtered read + descriptive
                                      |
                       Patterns & Insights (future consumer)
```
Group B technically consumes Group A, Group C can run in parallel. UAE cannot claim a completed B just from QDP B-ready flag. Gold fixture E2E is necessary but not proof of live deployments.

## 4. Proposed module structure, not yet all implemented
```text
intelligence/unified_analytics/
  __init__.py, contracts.py, errors.py, configuration.py, ports.py
  adapters/sre.py, qdp.py, group_a.py, group_b.py, group_c.py, source.py
  context/lineage.py, context/join.py
  validation/qualification.py, validation/evidence.py
  snapshots/assembler.py, snapshots/repository.py
  aggregation/descriptive.py, aggregation/segments.py, aggregation/strata.py
  runtime/orchestrator.py, runtime/queue.py, runtime/worker.py
  integration/inbox.py, integration/outbox.py, integration/patterns.py
  api/http_api.py, monitoring.py
tests/test_unified_analytics_*.py
site/unified-analytics.js
docs/UNIFIED_ANALYTICS_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md
docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md
docs/UNIFIED_ANALYTICS_INTEGRATION_CONTRACT.md
```
No empty stubs that claim completion. Implement subfiles only when their respective stage is implemented.

## 5. Backend schema and current OLD PR migration hazards
Old `016_analytics_unified.sql` creates `analytics_unified_snapshots` and `analytics_visible_snapshots` with DB write/read guards. `017_analytics_jobs_and_read_gates.sql` creates `analytics_qualification_jobs`. `018_analytics_audit_hardening.sql` also modifies `calculated_metrics`, changes C cohort integrity, adds approval tables and retention gates. Group C now owns C & approval logic. **Review one statement at a time**: retain UAE-owned snapshot/worker logic, move C-owned fields/approvals through Group C contracts, no duplicate DDL. Existing `schema_migrations` uses SHA of full file; never rename an installed migration in place. Migration dependent on QDP table must be staged and ordered after actual upstream installation. Use disposable PostgreSQL 17 to test fresh install and upgrade from accepted previous heads.

## 6. Stage execution standard
At each new request: inspect current HEAD/SHA, tracker+plan, prior CI exact SHA, and peer branch heads; implement only first incomplete stage, add happy/adversarial tests; mark verified tests truthfully; commit only UAE-owned changes + own doc tracker, and report next stage. If a stage implementation is complete but CI still queued, do not declare QA PASS. If tests are failing, record and attempt repair.

## 7. Step-by-step roadmap
### UA-MOD-00 — Чистая ветка, план, tracker, CI bootstrap
**Работы:** Создать ветку от main; добавить полные MD/TXT, tracker, integration guide, dependency-free package и pure tests; сохранить PR23 без каких-либо изменений.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** CI/static и diff main...UA: только UAE-owned файлы, нет копии старых конфликтующих миграций.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** main and read-only old PR23.

### UA-MOD-01 — Точное наследие PR23 и source-of-truth map
**Работы:** Просмотреть старые analytics/contracts, service, aggregate, queue, worker, http_api, UI, tests, migrations 016/017/018; выделить переносимое UAE-owned, что уходит Group C, что нужно переписать. Pin old branch SHA и составить migration dependency graph.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** docs/UA_LEGACY_PORT_MATRIX.md со ссылками на файлы и определением 1:1 reuse/adapter/rewrite/drop.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-02 — Standalone Ports, DTO & capabilities
**Работы:** Контракты чтения SRE, QDP, A, B, C, D1, source-policy + outbound Patterns. Protocols, typed unavailable, exact version, signed/hashed lineage; конфигурация без импорта peer feature branches.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Unit mock adapters; UAE импортируется на одном main без peer modules.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-03 — Legacy pure qualification gate migration
**Работы:** Перенести/адаптировать чистые validate_request, canonical_hash, evaluate_qualification из PR23 без DB/network; сравнить решения с legacy test vectors, добавить explicit C complete gating; не переносить analytics.metrics scorer.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Результаты по лицензированным фикстурам совпадают с legacy/новой truth semantics; ни один null не превращается в false/0.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-04 — UAE-owned SQL schema и миграции
**Работы:** Проанализировать старые 016–018 и текущий мигратор sorted filenames/sha; создать новые forward-only UAE миграции после зависимостей, без конфликтов 016_query_validation и без владения calculated_metrics/cohort approvals Group C.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** PG17 fresh/upgrade & reapply; старые checksum не меняются, UF FKs по текущим контрактам.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-05 — Canonical ID/project/source lineage
**Работы:** Проверять project_registry.id, videos.id, project membership, source_id, source_policy_version_id, QDP run, A/B/C content version IDs, taxonomy. Поддержать video/carousel; image_post зависит от согласованного B2.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Cross-project, stale policy/source mix, unknown content kind denied.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-06 — SRE current approved relevance adapter
**Работы:** Читать завершённые LIVE SRE решения по approved C hypotheses, human overrides, evidence/context version; never synthetic fixture or stale taxonomy; query via SRE port and read-time reevaluation.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Mock/PG tests: no_match, uncertain, not_evaluated, human revoke, synthetic run all block qualification.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-07 — QDP handoff & qualified set verification
**Работы:** Принимать QDP run/preparation/binding через versioned pointer, confirm unique video, qualified SRE provenance and evidence hash; fail closed if QDP incomplete, revoked or another project.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Duplicate handoff idempotent, different SHA conflict/quarantine.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-08 — Group A exact-version verified facts
**Работы:** Read video/carousel A complete analysis, scene/speech/OCR stream status, exact media SHA and A evidence; unknown vs absent; slide item positions; do not require fabricated swipe time.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Tests missing A, incomplete stream, swapped video, unsupported image_post B2.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-09 — Group B B1–B8 exact-version reader
**Работы:** Read completed B, needs_review=false, B1/B2/B3/B4/B5/B6/B7/B8 applicable per content kind, exact Group A FK and method/taxonomy version. Count zero CTA only when scan complete.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Schema fixture tests, cross A/B ID mismatch and partial B review blocked.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-10 — Group C persisted score adapter
**Работы:** Read owned Group C calculated_metrics as completed, with current/previous snapshot, published metadata, config ID and frozen cohorts; ensure source and metric definition. Do NOT run compute_c_metrics in UAE.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** No second scorer, C2 niche optional, no 0–100 cap, missing approvals blocks qualification.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-11 — Current source policy, expiry, revocation
**Работы:** Explicit source permission intersection, policy version, retention, rights at intake and every read; project isolation and evidence expiration. Changes invalidate current readable snapshots.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Permission revoked after computation hides output, expiry propagates, no unauthorized partial output.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-12 — Joined input context and exact version selection
**Работы:** Find compatible *same video/source/policy/media lineage* SRE-QDP-A-B-C combination rather than naïvely take latest each; deterministic tie-breakers, record all candidate exclusions.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Mixed-version negative tests; no accidental selection of stale B or incorrect C observation.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-13 — Unified Qualification / Status & blockers
**Работы:** Implement pure deterministic qualified/partial/blocked with machine-readable blockers, strict required evidence; partial never treated as qualified, no eligible content kind guesses.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Golden fixtures for every missing component and incorrect status; no model calls.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-14 — Immutable unified snapshot assembly
**Работы:** Assemble typed A/B/C/relevance payload with references and SHA over normalized JSON; preserve exact IDs, source policy and engine version, no raw media copies or synthetic metrics.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Byte-identical replays same fingerprint, stable decimals, immutable record.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-15 — Transactional persistence & versioned snapshots
**Работы:** Write analytics_unified_snapshots in atomic DB transaction with UNIQUE(project,request_key/fingerprint), insert guard; no UPDATE completed; concurrent retry safe; preserve historical versions.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** PG17 duplicate/rollback/parallel test and direct SQL forged qualified refusal.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-16 — Event inbox/outbox and invalidation lifecycle
**Работы:** Consume QDP/A/B/C/SRE revision events and source retention; transactional outbox for downstream; pin revision and propagate invalidation, without rewriting old snapshots.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Replay, out-of-order event, revoked C cohort and re-run with new version.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-17 — Durable qualification Job Queue
**Работы:** Port analytics_qualification_jobs and skip-locked claim, fenced leases, 1..N batches, retries, cancel, recovery, idempotent keys, project/source validation; never allow old lease commit.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** 2 workers race, process crash, DLQ, duplicate batch, cancellation.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-18 — Dedicated Python Worker and scheduler control
**Работы:** Implement standalone UAE Worker --once/--limit/--watch (watch opt-in), bounded resource usage; safe purge or retention job, no network/LLM, no boot auto-start.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Unit/PG worker tests and graceful stop, no external calls.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-19 — Read-side visibility views
**Работы:** Create/port analytics_visible_snapshots or equivalent current-evidence materialized read gate with project/current policy checks, revoked/expired A/B/C/SRE exclusion.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Read-time permission checks even for historical qualified, no side-channel through aggregate.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-20 — Descriptive aggregation baseline
**Работы:** Port `summarize_qualified` to UAE: qualified-only, counts, medians, percentiles using Decimal, meaningful sample size. No causal claims or normalization of C score.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Independent golden aggregates; 0/1 item strata and unknown data.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-21 — Eight typed segmentation dimensions
**Работы:** Group by kind, B1 hook, B2 format, B3 story role, B5 editing, B6 subtitle, B7 CTA, B8 topic; duplicate multilabel membership intentional but per-stratum unique videos.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** No double counting of one video twice within same category; exact allowed dimensions.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-22 — Compatible strata & cohort comparisons
**Работы:** Compare only same platform/content kind/metric definition A/B taxonomy version/C config, stratify source; author controls for author and time; no pooled incomparable metrics.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Negative tests for cross-platform or mixed metric definitions; ordered deterministic bins.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-23 — Sample size and statistical uncertainty
**Работы:** Implement explicit sample minima, missingness report, confidence/intervals where justified, baseline contrast and multiple-testing notes; distinguish descriptive association from causation. No new arbitrary causal interpretation.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Gold tests known distributions, n<min gate and no false confidence.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-24 — Project-level summaries & rankable views
**Работы:** Versioned aggregate run snapshots with D1/RTB/SRE/QDP lineage, sorting where score complete and policy allows, stable pagination, descriptive export.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Qualified only when queried by default; partial separate diagnostics, reproducible ordering.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-25 — Patterns Engine handoff contract
**Работы:** Produce evidence-rich, versioned aggregate/dimension comparisons for future Patterns & Insights module, not discover/generate insights in UAE; signed manifest for samples & exclusion rules.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Consumer can trace every insight input to actual qualified rows and statistics.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-26 — HTTP API integration
**Работы:** Project-scoped preview, single/batch qualify, job status/cancel, snapshot list/details, aggregate query; local app.py minimal route register, Host/Origin/auth/CSRF rules reused.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** HTTP tests for 403/404 cross-project, no mutation on GET, pagination, bad version, stable error codes.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-27 — Unified Analytics Studio
**Работы:** UI of A/B/C joined evidence, score components, blockers, timeline status and aggregate dimensions; no ‘Breakout 0–100’ misleading badge, no paid model/marketing promises.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Browser UI component smoke, screenshot/JS tests, empty/error/partial states.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-28 — Observability & operational CLI
**Работы:** Metrics for join failures, stale source, queue lag, missing A/B/C, read revocations, quality strata, PG retries. Trace IDs and bounded logs without raw secrets.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Operational diagnostics and manual recovery documented; graceful shutdown.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-29 — Source lifecycle & retention purge
**Работы:** Retention and deletion rules across immutable read history, cascade references, A/B/C/SRE changes; distinguish hard delete and not-readable current state, enforce policies.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** PG revoke/delete/expiry e2e leaves no unauthorized aggregate leakage.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-30 — Full fixture E2E QDP→A/B/C→UAE
**Работы:** Test disposable PG17 with one complete qualified video and carousel, wrong-version/partial cases, strict source policies and human review revisions. Explicit mocks for missing peers.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** E2E demonstrates exact lineage and no fabricated values; peer integration pending separately.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-31 — Scale & adversarial QA
**Работы:** Load test 10k/50k qualified snapshots, concurrent Workers, failure injection, adversarial/malformed JSON, SQL injection, project isolation, precision and percentile reproducibility, CI Windows/Linux.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Measured throughput documented; all required smoke/DB/statistical checks verified.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

### UA-MOD-32 — Independent release & merge-readiness
**Работы:** Audit diff main...feature/unified-analytics-full-module, reconcile migrations with evolving QDP/GA/GB/GC/SRE, PR old 23 preserved, dependency matrix, canary/rollback and final verified tests.
**Контракты/файлы:** Только UAE-owned `intelligence/unified_analytics`, test, docs, own additive migrations + минимальный guarded `app.py` / Studio hook, когда прямо требует этап.
**Проверки:** позитивный, негативный, идемпотентность/версионирование. Для DB — disposable PG17 + duplicate/concurrent fixtures; для math/aggregation — independent golden values и sample-size gates.
**Definition of Done:** Standalone merge-ready PR after QA and compatibility; no merge without explicit instruction.
**Текущее состояние:** см. `docs/UNIFIED_ANALYTICS_IMPLEMENTATION_TRACKER.md`.
**Зависимости:** preceding UA-MOD stages; upstream ports strictly capability-verified.

## 7. Default verification commands
```bash
python -m unittest discover -s tests -p 'test_unified_analytics_foundation.py' -v
python -m unittest discover -s tests -p 'test_unified_analytics_*.py' -v
CM_TEST_PG_DSN='postgresql://.../contentmachine_test' python -m unittest discover -s tests -p 'test_unified_analytics_db.py' -v
git diff --name-status main...feature/unified-analytics-full-module
```
Only disposable PG17 for DB fixtures. If no DB, label PG NOT RUN/SKIP. CI queued is not PASS.

## 8. Final module acceptance
- One UAE engine owns join/qualification/snapshot/descriptive logic, no replicated C scorer and no altered old PR #23 branch.
- Same-source/project/video/approved versions for SRE/QDP/A/B/C; no fabricated evidence, score or missing attributes.
- Completed qualified requires approved relevance and all applicable streams; partial stays diagnostic and is never rankable as qualified.
- Exact lineage/provenance, immutable snapshot ID, current authorization and expiry on every read; invalidation and outbox re-evaluation.
- Descriptive aggregate reports stable stratification and sample sizes, no causal claims or invented significance.
- Windows/Linux unit and CI, PostgreSQL 17 fresh+upgrade E2E, concurrent/recovery and source-rights negative tests all observed.
- Scalable batches, API/Studio, operator guide, migrate upgrade/rollback, dependency handshake, complete tracker.
- Branch is ready to integrate only after peer contract compatibility; PR remains Draft until separately requested, and never auto-merges.

## 9. Clarifications for next implementation chat
1. Preserve original branch `feature/analytics-engine-qualified-pipeline-20261023` and PR #23 unchanged — the user explicitly asked.
2. If main/peer branches advance, update compatibility matrix, do not force-push or overwrite original source.
3. If all code for an item exists but final tests fail, record implemented with QA FAIL and repair next; do not reset work or falsely report pass.
4. Implementation of patterns detection, generative insights, content ideation/production is OUTSIDE UAE; UAE produces their trusted evidence and aggregate inputs.
