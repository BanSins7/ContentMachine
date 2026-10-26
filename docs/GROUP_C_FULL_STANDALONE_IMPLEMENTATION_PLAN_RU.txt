# CONTENT MACHINE — GROUP C FULL STANDALONE IMPLEMENTATION PLAN
## Исполняемая независимая система Performance Analytics C0–C7

Редакция 2026-10-26. Версия плана GC-MOD/1.0. Этот текст — ТЗ, не подтверждение выполнения всех этапов.
Repository: BanSins7/ContentMachine. Branch: feature/group-c-full-module. Baseline main SHA: 218f5f3163ccc7179a4809ba4386a1d1b6ff80f5.

## Миссия и границы
Group C получает реальные Metric Snapshots и QDP preparation C, проверенные publication metadata, утверждённую конфигурацию и frozen creator/prior/niche cohorts. Производит воспроизводимые C0–C7 метрики в calculated_metrics, предоставляет результат в Unified Analytics. Group A/B работают параллельно. Никакой LLM для расчётов.

### Уже реализовано, НЕ переписывать
- main/intelligence/migrations/003_performance_c.sql и 004_performance_c_integrity.sql: C schema и integrity; старые SHA должны остаться неизменными.
- main/docs/PERFORMANCE_C_DATABASE_V1.md и tests/test_performance_c_schema.py: утверждённый контракт и PG17 regression.
- Analytics PR23: intelligence/analytics/metrics.py содержит pure Decimal compute_c_metrics; code ownership переносится по этапам в C, Analytics становится потребителем C, без изменения Analytics branch во время самостоятельной разработки.
- QDP PR24: versioned preparation/handoff; Collector, SRE, A, B — независимые внешние модули.

### Неприкосновенная формула
 C0 = views,likes,comments,shares,saves,followers + observed_at/source_metric_definition/source_metric_version
 C1 creator_ratio = (V+alpha)/(creator_median_views+alpha)
 C2 niche_outlier = (V+alpha)/(niche_median_views+alpha), optional, NOT in C7
 C3 views_growth_per_hour = (V2-V1)/delta_hours
 C4 engagement_rate = (L+Comments+Shares)/V
    weighted_engagement_raw = (L+1.5*Comments+3*Shares)/V
 C5 L_s=(L+k*pL)/(V+k), C_s=(Comments+k*pC)/(V+k), S_s=(Shares+k*pS)/(V+k)
    weighted_engagement_smoothed = L_s+1.5*C_s+3*S_s
 C6 freshness = 100*2^(-hours_since_published_at_observed_at/48)
 C7 breakout_score = 0.45*C1 + 0.30*C3 + 0.15*C5 + 0.10*C6

No normalization, no clipping to [0,100], no hidden scaling, no addition of niche to Breakout Score. Unknown alpha/k/prior values/peers never receive default numbers. Saves excluded from specified weighted formula.

### Invariants
1. Canonical videos.id, metric_snapshots, source_video_metadata; do not duplicate publication or metric tables. Views != reach or impressions; full source_metric_definition and source_metric_version must match.
2. observed_at is actual measurement time; collected_at is collection time; assumed observation_time cannot be used for full growth. Raw snapshots append-only.
3. Publication evidence must have explicit metadata ID; missing/future published_at is blocker, never guessed from now().
4. Creator cohort = same creator/platform/kind/metric definition & version; frozen peers exclude target. Niche and engagement_prior cohorts likewise constrained.
5. Real configuration alpha>0, bayesian_k>0 and three priors require explicit review and approved as-of cohort. No automatic approvals.
6. Score complete requires verified views positive, counters, strictly increasing observations, published_at, approved config + frozen cohorts, source policy, all four components. Missing optional C2 is okay.
7. Use Python Decimal and quantization to 12 decimals for PostgreSQL NUMERIC(30,12); float approximations are not authoritative.
8. Each score records current/previous snapshot, publication metadata ID, config ID, cohort IDs, version and status; complete outputs immutable.
9. Current source policy/retention verified on both write and read. Expired source or retracted peer invalidates readable results; keep auditable historical references only according to retention.
10. Absence of external feature branch -> typed unavailable, not automatic fallback to fixtures. No paid provider requests in C.
11. One user request = one GC-MOD stage; after implementation commit only this branch and update docs/GROUP_C_IMPLEMENTATION_TRACKER.md. If final tests fail/skipped, strike through implemented code with QA FAIL/BLOCKED/NOT RUN; do not mark PASS.
12. No merge to main or editing upstream feature branches without explicit owner instruction.

### Planned module layout (not created yet)
intelligence/group_c/contracts.py, ports.py, sources/, cohorts/, math/, persistence/, runtime/, integration/, review/, monitoring.py, http_api.py.
tests/test_group_c_*.py, site/group-c.js, docs/GROUP_C_*, own additive SQL after migration ordering audit.

## Development stages (one stage per user request)
### GC-MOD-00 — Bootstrap ветки
Implementation: Создать ветку от main; разместить MD/TXT план, трекер, контракт интеграции, package, pure unit-test и CI; не копировать Analytics/QDP.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Чистый diff, документация и тестовая инфраструктура.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-01 — Аудит контрактов C0–C7
Implementation: Сверить 003/004 SQL, исходный TXT и compute_c_metrics из Analytics PR23, построить матрицу входов, NULL/unknown, версий, блокирующих причин, статусов.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Полевая и формульная совместимость заморожена.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-02 — Ports и независимый модуль
Implementation: Protocol-интерфейсы для QDP, Collector/D1, Creator/Topic registry, source-policy, cohort approvals, Analytics; ошибки недоступных peers.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Group C импортируется без feature branches, dependency unavailable не подменяется ready.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-03 — PG17 runtime schema и upgrade
Implementation: Новые C-owned таблицы run/job/inbox/outbox/revisions additive-only; выбрать незанятый номер миграции и проверить порядок и SHA в schema_migrations; не переписывать 003/004.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Fresh PG17 + upgrade успешно; миграции из других веток не скопированы.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-04 — QDP C-intake
Implementation: Проверка project/video/QDP run UUID/group=C/sha/source и проверенный consumer qdp-handoff/v1; durable inbox ACK после фиксации.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Replay idempotent, conflicting SHA/cross-project rejected.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-05 — Источник фактов D1
Implementation: Контракты videos, metric_snapshots, source_video_metadata, metric definition/version, publication kind, creator_id, platform/source-policy.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Нет второго каталога постов или metrics_snapshots, view != reach.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-06 — C0 Snapshot Read/Validation
Implementation: Читать сырые views/likes/comments/shares/saves/followers и observed_at; retracted/expired/assumed/partial признавать неполными.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Нуль не равен NULL, нельзя корректировать raw факты in-place.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-07 — Изоляция pure Decimal math
Implementation: Портировать проверенную чистую функцию compute_c_metrics из ветки Analytics в Group C с независимыми gold vectors и обратными parity тестами; Analytics позднее делегирует к C.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Без двух разных production scorer, исходная ветка не модифицируется.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-08 — Publication time evidence
Implementation: Проверенный published_at из source_video_metadata с ID/временной зоной/TTL; старый score не меняет возраст от wall clock.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: No now() вместо даты публикации, некорректные timestamps blocked.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-09 — Cohort rules
Implementation: Определить creator/niche/engagement_prior strata: same platform/kind/metric definition/version, compatible time & age windows, no target self inclusion.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Peer-совместимость формализована; без неподтверждённых дефолтов.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-10 — Freeze cohort membership
Implementation: Версионировать замороженные comparison_cohorts, members exact snapshot IDs, median/prior evidence и SHA выборки.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Повтор воспроизводим; missing peer не заменён средним по умолчанию.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-11 — Review config/cohort
Implementation: Человеческое одобрение alpha/k/prior, определения view, состава peers, размеров и версии конфигурации; не autoapprove.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Unreviewed configs/cohorts не дают complete.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-12 — Config activation
Implementation: Проверить строго фиксированные weights 0.45/0.30/0.15/0.10, normalization FALSE, freshness half-life 48, параметры alpha>0, k>0 и версии.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Изменение конфигурации создаёт новую версию, а не переписывает историю.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-13 — C1 Creator Ratio
Implementation: Decimal (views+alpha)/(creator_median+alpha) с approved frozen creator cohort того же автора и совместимых метрик.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Нет сравнения с самим собой или чужой площадкой.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-14 — C2 Niche Outlier
Implementation: Опционально (views+alpha)/(niche_median+alpha), отдельно от C7, с frozen niche cohort.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: NULL допускается; C2 никогда не добавляется в Breakout.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-15 — C3 Growth
Implementation: Разница views двух наблюдений / точное время в часах, совместимость same video/source/definition/version и valid observed_at.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Отрицательная коррекция views и одинаковые timestamps не обнуляются.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-16 — C4 Raw Engagement
Implementation: Like, Comment, Share rates; engagement=(L+C+S)/V; weighted=(L+1.5*C+3*S)/V; saves отдельно.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Невозможный division by zero блокирует; процент/доля не смешаны.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-17 — C5 Bayesian Engagement
Implementation: Ls=(L+k*pL)/(V+k), Cs=(C+k*pC)/(V+k), Ss=(S+k*pS)/(V+k); weighted=Ls+1.5Cs+3Ss.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: k и priors только из одобренной конфигурации/когорты.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-18 — C6 Freshness
Implementation: 100*(2^(-hours_since_published_at_observed_at/48)), Decimal 12 знаков, timezone-aware.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: 0/48/96 часов дают 100/50/25; никакого wall clock drift.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-19 — C7 Breakout Score
Implementation: 0.45*C1+0.30*C3+0.15*C5+0.10*C6; отдельные компоненты и consistent quantization.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Без нормализации и предела 0..100; C2 не входит.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-20 — Calculated metrics persistence
Implementation: Atomic insert calculated_metrics: snapshot, previous, published_metadata_id, approved config/cohorts, exact numeric components, statuses, ID lineage.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Immutable complete и unique version fingerprint; recovery idempotent.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-21 — Durable Worker
Implementation: Claim SKIP LOCKED, lease fencing, heartbeat, recovery, cancellation, concurrency budgets, retries transient, no LLM.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Один job не записывается двумя workers; fail closed.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-22 — Incremental recompute
Implementation: Новые настоящие snapshots, config/cohort revisions, publication corrections -> event driven recompute; outbox в Analytics.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Пересчёт не переписывает старый полный score.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-23 — Metric Quality Gate
Implementation: Incompatible definition/version, corrections, huge anomalies, insufficient peer/sample, expired source, missing counters -> explicit status.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Аномалии диагностируются, но реальные значения не подменяются.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-24 — Retention/revocation
Implementation: Current source-policy and TTL at ingest/read, invalidation on expired raw or cohort member, removal/erasure where required.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Expired score не читается как valid даже если раньше был complete.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-25 — Group C -> Unified Analytics
Implementation: Передача persisted C ID и lineage, exact config/snapshot/peer version; согласовать PR23 scorer replacement через adapter.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Analytics читает Group C, не рассчитывает C7 второй независимой реализацией.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-26 — HTTP API
Implementation: Project-scoped preview, cohort/config approvals, runs, status, cancel, scored results, pagination and typed errors.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: No external APIs/LLM in route, auth/CSRF/Host and cross-project negative tests.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-27 — Group C Studio
Implementation: График views во времени, когорты, config, four score contributions, data quality, approval and limitations.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Breakout Score не представлен вероятностью или скором 0–100.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-28 — Monitoring/recovery
Implementation: Logs, traces, metric freshness, worker lag, alarms, cleanup, concurrency and rollback doc.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Реальный recovery smoke и наблюдаемость подтверждены.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-29 — Load/performance QA
Implementation: Benchmark 10k+ posts, cohort query complexity, indexes, cold/warm runtime, queue backpressure.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Не утверждать throughput без реальных измерений.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-30 — Math gold-set QA
Implementation: Независимые exact Decimal oracles C0–C7, missing/zero, DST/tz, negative corrections, version mismatch, frozen peers.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Числовые и error parity тесты зелёные.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-31 — PG17 cross-platform E2E
Implementation: Fresh + upgraded PG17, Windows/Linux, queue racing, QDP fixtures, verified Analytics handoff, no mock=production claims.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: CI каждого важного SHA подтверждён.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md

### GC-MOD-32 — Final release gate
Implementation: Проверить diff, migrate collisions with other branches, documented feature flags, dependency handshakes, release/rollback, Draft PR; не делать merge.
Tests: happy path, adversarial negative and idempotent/version-boundary. DB stages must include disposable PG17 tests; math stages independent Decimal gold vectors; integrations exact provenance IDs.
Definition of Done: Отдельная merge-ready C ветка после устранения QA/integration blockers.
Status: see docs/GROUP_C_IMPLEMENTATION_TRACKER.md
## Standard commands and QA truth
python -m unittest discover -s tests -p 'test_group_c_foundation.py' -v
python -m unittest discover -s tests -p 'test_performance_c_schema.py' -v
CM_TEST_PG_DSN='postgresql://.../contentmachine_test' python -m unittest tests.test_performance_c_schema -v
git diff --name-status main...feature/group-c-full-module
PG tests skipped without CM_TEST_PG_DSN must be reported SKIP/NOT RUN, never PASS. CI queued/pending is not PASS. No production DB for synthetic tests.

## Merge-ready acceptance
Pure Group C math produces same values/error handling as approved PR23 vectors after ownership transfer; all C0-C7 validated.
QDP handoff to C, canonical D1 source facts, independent frozen approved cohorts, versioned calculation and downstream Analytics adapter tested.
Full PostgreSQL 17 fresh/upgrade, Linux/Windows CI, concurrency, source revocation, retention, idempotency and performance checks verified.
Independent feature branch has no modifications to main's applied 003/004 and no copies of QDP/Analytics code; no main merge without explicit request.

## Work protocol for next ChatGPT chat
Read current HEAD, tracker, this plan and latest upstream PR heads. Choose next code-unimplemented item, or fix last QA failures. Implement one scoped stage, add negative tests, run what environment permits, commit, update tracker with SHA and test truth. Never claim production readiness because a fixture passed.