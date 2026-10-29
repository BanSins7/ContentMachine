# CONTENT MACHINE — PATTERNS & INSIGHTS ENGINE (PIE)
## Полный независимый модуль · поэтапный план реализации / 1 запрос = 1 этап
**Версия:** PIE-MOD/1.0, 2026-10-26. **Статус:** архитектурный план; bootstrap implementation only. **Owner:** `intelligence/patterns_insights`.
**Repository:** `BanSins7/ContentMachine`.
**Canonical branch:** `feature/patterns-insights-full-module`, основана на `main` SHA `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`.
**НЕ изменять:** `main`, `feature/unified-analytics-full-module` PR #38, legacy UAE PR #23, Group A/B/C, QDP, SRE и прочие ветки.

## 0. Цель и граница ответственности

PIE — самостоятельный статистико-семантический аналитический движок, который **не повторяет Group A/B/C/UAE**. На входе ТОЛЬКО versioned UAE qualified snapshots, comparability manifests и утверждённые параметры исследовательского дизайна. В первом контуре **Patterns** он формирует осмысленные сравнимые группы, извлекает признаки, проверяет гипотезы, контролирует ложные находки и сохраняет подтверждённые/экспериментальные Patterns с exact provenance. Во втором контуре **Insights** сильная языковая модель объясняет *уже рассчитанные* результаты и предлагает осторожные практические эксперименты, а строгий валидатор и Human Review препятствуют фантазированию.

### Итоговый поток
```text
RTB / SP / VC / Discovery -> SRE -> QDP
                                 |
                         Group A + Group B + Group C
                                 |
                        UAE qualified/versioned snapshots
                                 |
                        PIE Dataset / Design / Cohorts
                                 |
                    Feature Matrix + Pattern Discovery
                                 |
                    Statistical QA + Holdout / Robustness
                                 |
                       Immutable Pattern Registry
                                 |
                LLM grounded Insight -> Validation -> Review
                                 |
                   Verified Insights -> Strategy & Ideation
```
PIE **не** запускает заново мультимодальное распознавание, не вычисляет C7 и не ищет новые исходные видео. Strategy/Ideation и Production — **отдельные будущие модули**, не реализуются внутри PIE.

## 1. Проверенная отправная точка в репозитории

- На `main` нет исполняемого PIE. Есть канонические `videos`, PostgreSQL/schema/metrics и собственные миграции, но **нет `intelligence/patterns_insights`**.
- Новый UAE: `feature/unified-analytics-full-module`, PR #38, содержит roadmap; его `UNIFIED_ANALYTICS_INTEGRATION_CONTRACT.md` предусматривает передавать PIE *evidence-rich* descriptive results. Это **проект интерфейса**, не готовый живой поставщик.
- Старый UAE PR #23 `feature/analytics-engine-qualified-pipeline-20261023` содержит `intelligence/analytics/aggregate.py` с qualified-only Decimal median/p25/p75, 8 dimensions, max 50000 posts, min sample 2..10000, сопоставимые strata. **PIE не копирует агрегатор**: читает подтверждённый UAE dataset и строит отдельную inferential/pattern аналитику. Существующее 2 записи в descriptive агрегаторе — не автоматический достаточный размер исследования.
- Group C Breakout Score = 0.45*creator_ratio + 0.30*views_growth_per_hour + 0.15*weighted_engagement_smoothed + 0.10*freshness_score; **не нормализован, не ограничен 0..100**, growth имеет raw views/hour, C2 niche_outlier не входит.
- Group B содержит hook/format/story/visual/editing/subtitles/CTA/topic, но качество и валидность этих признаков определяются B и UAE, не заполняются PIE из догадок.
- Попытка upstream интеграции на отсутствующем модуле должна вернуть `capability_unavailable`, а не молча подставить фиктивные записи.

## 2. Инварианты архитектуры

1. **Один PIE-MOD-NN = один следующий пользовательский запрос** «продолжай работу». Перед исполнением обязательно перечитать текущий HEAD, этот план и `docs/PIE_IMPLEMENTATION_TRACKER.md`, затем commit в каноническую ветку. Никаких изменений в `main`/чужих ветках, без явного отдельного merge.
2. Если код этапа полностью реализован, а тесты в конце FAIL/BLOCKED/NOT RUN — отмечать `[x] ~~PIE-MOD-NN~~ (IMPLEMENTED; QA: FAIL/BLOCKED/NOT RUN)` с конкретным объяснением, не отменять выполненный код и не притворяться, что QA PASS. Если код не закончен — `[ ]`. CI queued ≠ PASS. Каждый этап требует happy/negative/regression checks.
3. **Source-first, project scoped.** PIE читает только текущие authorized/qualified/version-pinned входы UAE через порт. Не читает Group A/B/C/SRE напрямую для обхода UAE quality gate, не объединяет разные platform/metric definitions/config versions.
4. Dataset identity = project, source policy/version, UAE manifest+snapshot IDs, RTB/SRE taxonomy, A/B analysis, C config, as-of time, selection criteria and SHA. Exact canonical `videos.id` only. Все группы и результаты сохраняют полный lineage и причины exclusions.
5. No fabricated metrics, control groups, effect sizes, sample sizes, confidence intervals, p-values or approved configurations. Unknown/null ≠ 0/false/no CTA. Lack of comparable peers = blocked/insufficient, not false negative.
6. **Post-selection inference:** discovery on same data does not turn posthoc p-value into independent proof; record hypotheses family, number of tests, multiple-comparison adjustment and independent holdout by creator/time where feasible. If data insufficient, results remain *exploratory*.
7. Statistical assumptions explicit: independence, creator clusters, distribution skew, representativeness, missingness and selection bias. Do not label associations as causal impact without validated causal identification. Avoid claiming “hook increases views by X%” based on simple comparisons.
8. Raw C7 blends units and often growth dominates. Do not normalize/force 0..100 inside UAE/PIE. If alternative outcome used, it is explicitly configured as a different outcome, not secretly substituted C7.
9. Feature B1 is multilabel; within each group count each publication once. B3 ordered semantic sequence; do not replace technical A scenes with story roles. `image_post` B2 unsupported and not auto cast to carousel.
10. No magic sample size, FDR alpha, model name, effect-size threshold, confidence level, period, 'top performers' percentile, Bayesian priors or cost budget invented as global defaults; must be explicit approved analysis configuration with version. Assumed settings allowed **only in labelled test fixtures**.
11. Reproducibility: deterministic Decimal stats when possible, recorded algorithm version, random seeds for resampling, input fingerprint, precise exclusion/strata/holdout split. Immutable Pattern + Insight versions, human review revisions and invalidation on withdrawn upstream inputs.
12. Explainable stats from Python, not generated by LLM. Only supported Pattern Registry results can reach the text model. All language numbers must match data, source provenance and interval semantics; unknown charging after network outage must prevent blind paid retry.
13. If third-party data permits processing but later rights/retention expires, both pattern and insight must stop being visible; do not leak evidence in public aggregate, export or review screens.
14. No paid model calls in CI or automatic application boot. Strong provider via configurable adapter with explicit capability+pricing/quote/approval, no key logging, request reservation and spend ledger.
15. PIE must be installable on existing `main` without other feature branches, declaring unavailable peers. Full runtime and DB merge-readiness require exact handshakes once UAE is genuinely integrated.

## 3. Proposed owner-owned package layout (not yet implemented)

```text
intelligence/patterns_insights/
  __init__.py, contracts.py, errors.py, settings.py, ports.py
  intake/uae.py, intake/provenance.py, intake/dataset.py
  design/cohorts.py, design/strata.py, design/outcomes.py, design/holdout.py
  features/matrix.py, features/sequences.py, features/missingness.py
  discovery/marginal.py, discovery/interactions.py, discovery/technical.py
  statistics/effects.py, statistics/uncertainty.py, statistics/multiplicity.py
  statistics/adjustment.py, statistics/robustness.py
  registry/repository.py, registry/versions.py, registry/invalidation.py
  insights/prompts.py, insights/provider.py, insights/generation.py
  insights/verification.py, insights/review.py
  runtime/jobs.py, runtime/worker.py, runtime/outbox.py, runtime/budget.py
  integration/strategy.py
  http_api.py, monitoring.py
tests/test_pie_*.py
site/pie.js
docs/PIE_FULL_STANDALONE_IMPLEMENTATION_PLAN_RU.md
docs/PIE_IMPLEMENTATION_TRACKER.md
docs/PIE_INTEGRATION_CONTRACT.md
```
Отдельные runtime SQL миграции выбираются только после просмотра последних upstream миграций. Никаких пустых файлов-заглушек с ложными `implemented` до соответствующего этапа.

## 4. Контракт UAE → PIE и критические ограничения

Предлагаемое название `uae-to-pie/v1` является **целевым контрактом PIE**, не объявлением, что его уже отдаёт UAE. Вход содержит `project_id` (32 lowercase hex), `source_id`, `source_policy_version_id`, `uae_manifest_sha256`, `uae_run_id` в фактически утверждённом типе, `cutoff/as_of`, `calculation_config_id`, версия аналитического словаря, complete flags, exact canonical post IDs, статистически корректный dataset с hash каждого source snapshot. `qualified=true` должен быть доказан чтением по текущей политике и не приниматься из непроверенного JSON.

**Уникальность исходов:** UAE descriptive `aggregate_project` может передать summary, но PIЕ не должно строить фиктивную независимую выборку из единственной summary-строки и придумывать N observations. Для проверки паттернов нужен signed row-level research manifest либо достаточные проверяемые агрегаты с реальными знаменателями, ковариациями/кластерной структурой. Первыми этапами согласовать exact row-level API, который UAE сможет реализовать без раскрытия лишних данных.

**Output PIE → Strategy:** только validated + human-reviewed patterns/insights (отдельно от exploratory), с exact source fingerprint, sample counts, caveats, measured effect, uncertainty and suggested test. Не генерация сценариев, не автоматическая публикация.

## 5. Stage implementation — PIE-MOD-00 → PIE-MOD-35

### PIE-MOD-00 — Bootstrap: clean PIE branch + execution contract
**Цель и конкретная реализация.** От main создать каноническую ветку, положить MD/TXT план, трекер, integration contract, автономный python package с input validation, pure tests и CI. Никакого импорта UAE/PR23 как реализованного API.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Ветка на main, собственные файлы/коммит/тесты, все будущие этапы tracked, PR Draft.

**Зависимость:** main + только read-only UAE/old PR23. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-01 — Inventory UAE & statistical ownership freeze
**Цель и конкретная реализация.** Сверить старый UAE PR23 и новый UA-MOD roadmap, его aggregate/segmentation, C score semantics, publisher versions, views/read gates. Разделить UAE descriptive versus PIE hypothesis-driven comparisons; закрепить API schema и data dictionary без изменения UAE.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Чёткая матрица upstream fields, known blockers и границ PIE, signed fixture snapshots.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-02 — Architecture, ports, dependency capability checks
**Цель и конкретная реализация.** Определить Python Protocols для UAE reader, result publisher, source rights, model provider, storage, approvals; typed unavailable/incompatible with version and reason; отсутствие upstream не блокирует unit без притворства, что интеграция готова.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** PIE импортируется в main без UAE кода; отсутствующий реальный UAE — capability unavailable.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-03 — Versioned PIE dataset input contract
**Цель и конкретная реализация.** Определить утверждаемый `uae-to-pie/v1`: project_id, source, source policy, UAE run/snapshot, exact canonical videos, config, A/B/SRE/C taxonomy revisions, as_of, manifest SHA, stats, lineage, sample IDs; ranges/duplicates/unknown validated.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Exact schema with JSON fixtures; negative tests on forged source, duplicate post and invalid sample sizes.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-04 — PIE-owned PostgreSQL and migrations
**Цель и конкретная реализация.** Сделать только additive PIE SQL: runs, dataset manifests, candidate patterns, comparison sets, validations, insights, reviews, budget/ledger, worker jobs, outbox; после аудита реальных миграций выбрать collision-free номер (main 053, peer modules растут). Не копировать UAE tables.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** PG17 fresh/upgrade/migration SHA and FK project isolation; main unchanged.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-05 — UAE integration reader & handshake
**Цель и конкретная реализация.** Реальный адаптер читает versioned qualified-only UAE snapshots/evidence; проверяет current policy/read gating и receipts; не берет напрямую старые A/B/C таблицы в обход UAE. Cross-module schema handshake only on observed version.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Отзыв UAE доступа => PIE blocked; fake single aggregate JSON does not magically prove input validity.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-06 — Provenance & source-eligibility gate
**Цель и конкретная реализация.** Проверять project membership, source policy/derived expiry и lineage каждого input, immutable UAE fingerprint, scores only complete, no expired or revoked evidence; no cross-project, no unapproved SRE relevance.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Fail closed and read-time check; all exclusions counted, never data leakage.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-07 — Reproducible analysis dataset builder
**Цель и конкретная реализация.** Строить immutable cohort dataset snapshot с canonical video IDs и exact UAE observation version, frozen included/excluded set, weights and feature/score dictionaries, checksum, selection date and reason; dedup versions deterministically.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Repeated same run reproducible; version changes force new dataset snapshot.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-08 — Comparable strata and source definitions
**Цель и конкретная реализация.** Разбивать по platform, kind, source view definition+version, observation age, creator & niche, A/B taxonomy revisions, C config, SRE taxonomy. Не смешивать несопоставимые сырые Breakout Scores.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Tests mixed platform/metric definition/C version fully partitioned or blocked.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-09 — Outcome and comparison policy
**Цель и конкретная реализация.** Задать исходы: Breakout Score (raw, NOT [0..100]), creator_ratio, creator-adjusted views; выбор pre-registered outcome, high/low grouping, common window; no implicit top percentile or arbitrary success thresholds.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Run can't start without explicit, versioned approved outcome design; baseline defined.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-10 — Controls and comparison group assembly
**Цель и конкретная реализация.** Сформировать явные reference/contrast группы из сопоставимых публикаций, creator and topic controls, age/time and observation cadence, within-stratum matches, leakage prevention, overlap and exclusion report.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Adequate comparison peers; score/current post never leaks into baseline/training.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-11 — Feature matrix A/B/C selection
**Цель и конкретная реализация.** Только наблюдаемые из UAE: B1 multilabel Hook, B2 format, B3 story sequences, B4 visibility, B5 editing, B6 subtitles, B7 CTA, B8 topics + A durations, speech metrics; typed categorical/numeric/sequence; null explicit.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** No inferred unsupported features or invented from filenames. Feature lineage pinned.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-12 — Missingness, sample and coverage QA
**Цель и конкретная реализация.** Матрица unknown/not_run/partial, false, zero, NA; report eligibility N, per-feature N, missing mechanism, subgroup balance and exclusions; avoid survivorship bias as proven check not assumption.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Missing inputs never filled with 0 by default, suppressed cells truthfully flagged.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-13 — Candidate discovery: marginal patterns
**Цель и конкретная реализация.** Generate governed hypotheses for single features: between-cohort frequency, median or distribution differences and counts; feature selection bounded and proposed search space recorded before testing.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Tests deterministic candidate order, allowed dictionary and sample gates.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-14 — Effect size estimation
**Цель и конкретная реализация.** Compute descriptive effects by feature type: risk difference/ratio (defined denominators), difference in medians, distribution shift; Decimal where appropriate and labelled units; no implied causal uplift.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Independent gold examples and zero denominator behavior verified.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-15 — Uncertainty estimation & confidence intervals
**Цель и конкретная реализация.** Select valid sampling uncertainty methods (bootstrap or interval formulas), stratified/clustered by creator when appropriate; set seeds/iterations explicitly; guard tiny samples, distribution skew and dependent posts.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Intervals reproducible with seeds; small-N blocks instead of confident claims.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-16 — Multiple testing and discovery correction
**Цель и конкретная реализация.** Control multiplicity (BH-FDR or approved family-wise method), preregistered tested hypotheses count, raw+adjusted p values and assumptions; prevent post-selection p-values shown as confirmatory.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** No false discoveries created by scanning many hooks without correction.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-17 — Holdout / cross-validation split
**Цель и конкретная реализация.** Separate hypothesis discovery vs confirmation by creator and temporal split, avoid same account/version in train and test, freeze split prior to pattern search; record data leakage checks.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Pattern must show out-of-sample evidence or marked exploratory only.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-18 — Confounder and adjustment strategy
**Цель и конкретная реализация.** Handle creator, niche, topic, age, posting time and platform effects with comparable design/regression only when support and assumptions permit; no casual claims of causal influence.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Results labeled association; no statistical adjustment fabricated on insufficient samples.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-19 — Temporal and cohort stability tests
**Цель и конкретная реализация.** Repeat estimates across publication windows and freshness cohorts, different providers, creator subgroups, compare stable/unstable directions with thresholds registered before testing.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Track unstable directions; don't promote short-lived correlations as universal.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-20 — Multifeature interactions
**Цель и конкретная реализация.** Bounded exploration hook×format, hook×subtitles, story×CTA and other predeclared interactions; sufficient support and multiple testing accounting; avoid combinatorial explosion.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Pairwise rule candidates have coverage, lift relative to matched baseline, corrected confidence.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-21 — Story and sequence pattern discovery
**Цель и конкретная реализация.** Find frequent B3 semantic sequences and transition motifs (e.g. hook→problem→demonstration→cta) with edit-distance or n-grams; preserve ordering/repeated roles and slide/timestamp distinction.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** No technical A scene conflated with semantic B3 story, genuine sequence coverage recorded.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-22 — Continuous technical factor patterns
**Цель и конкретная реализация.** Analyze A duration, speech_wpm, speech coverage, B5 cuts_per_minute and first-5 cuts with monotone/binned methods; bin definitions predeclared, avoid thresholds cherry-picked on same data.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Effect and uncertainty per comparable numeric stratum, no hidden optimizations.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-23 — Robustness, sensitivity and negative controls
**Цель и конкретная реализация.** Outlier sensitivity, alternate exposure definitions/outcome windows, creator leave-one-out, held-out subgroup, shuffled-label negative controls; score dominated by raw growth disclosed.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Fragile patterns downgraded/excluded with reproducible test log.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-24 — Pattern quality scoring and release gates
**Цель и конкретная реализация.** Define explicitly approved evidence quality rubric: sample size, effect magnitude and sign, corrected uncertainty, holdout confirmation, stability, missingness, multiple-testing, confounders; NO arbitrary numeric thresholds.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Exploratory vs supported vs rejected vs needs_review honest state transitions.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-25 — Immutable Pattern Registry
**Цель и конкретная реализация.** Persist candidate ID, hypothesis specification, feature/operator/contrast, provenance sample IDs/fingerprints, stratum config, statistics and validation ID, status/revision and owner; dedup/reanalysis version semantics.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** No silent overwrite; same evidence fingerprint idempotent and new input produces revision.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-26 — Pattern retrieval, explanations and dashboard queries
**Цель и конкретная реализация.** Read pattern registry by project, hypothesis, B-feature, platform, support, status, min-N, with rights re-evaluation and sensitive evidence redaction; pagination/export.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** No cross-project leak; filters require current eligible UAE evidence.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-27 — Pattern invalidation and drift monitor
**Цель и конкретная реализация.** UAE/QDP/A/B/C/SRE revisions, source expiry/retention, config update and new snapshots may invalidate/read-suppress patterns and enqueue recomputation; no edit of immutable old evidence.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Out-of-order revocation propagates, stale pattern cannot be promoted to Insight.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-28 — Insight semantic specification and prompt templates
**Цель и конкретная реализация.** Define `pattern-to-insight/v1`: grounded summary, evidence IDs, cohort scope, effect/stat uncertainty, caveats, counterexamples, actionable tests (not guaranteed wins). LLM output schema and prompt injection protections.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Model can't cite evidence not present; no invented percentages, sources or causal language.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-29 — LLM model adapter & spend governance
**Цель и конкретная реализация.** Configurable strong text model via OpenRouter with verified model slug/capabilities/price, quote/reserve/approval/actual cost and uncertain-charge quarantine; strict JSON, timeouts, no paid calls in CI.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** No unknown paid retries; tests mock provider, no secret exposure.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-30 — Insight Generation Engine
**Цель и конкретная реализация.** Generate natural-language insights only from validated Pattern Registry and evidence manifest; preserve exact stats and scope, confidence labels, suggested experiment ideas (ideas only, not production).

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Generated insight ties to pattern version/IDs; no pattern => no insight.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-31 — Insight verification, contradictions & hallucination guard
**Цель и конкретная реализация.** Check all numeric facts/units/quotations/versions/conditions against Pattern Registry, no unsupported ‘causes’, no conflating association with causation; score unknown -> quarantine; counterevidence required.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Contradiction, fabricated effect, false claim rejected before public read.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-32 — Human Review and feedback loop
**Цель и конкретная реализация.** Review proposed pattern/insight, approve/reject/suggest changes, version reviewer edits, calibration/gold sets, disagreements and appeals; define policy for report release.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Immutable review chain and separation of model proposal from approved insight.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-33 — Orchestrator, Workers, queue and retries
**Цель и конкретная реализация.** Durable scoped runs/jobs, lease fencing and SKIP LOCKED, idempotent hashes, separate deterministic vs paid LLM tasks, checkpoints, cancellation, outbox, retries only safe operations.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Multi-worker race recovery, no duplicate paid inference, crash-safe state.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-34 — HTTP API, Studio and Pattern Explorer
**Цель и конкретная реализация.** Project-scoped pattern-datasets/candidates/validation/results/reviews/insights API; Studio comparison charts, sample sizes, effect uncertainty, filter by type, traceability and explicit caveats.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** HTTP/JS tests including empty/not-ready/revoked, no source bypass.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

### PIE-MOD-35 — E2E research quality, performance and standalone release
**Цель и конкретная реализация.** Disposable PG17 and fixture UAE bridge, statistically independent gold datasets, multiple testing, holdout, drift, 10k+ posts resource limits, Windows/Linux CI, migration matrix + peer compatibility, final PR readiness.

**Обязательные проверки:** корректный positive case, отрицательный/враждебный case, reproducible identity/version/replay case. Для статистики — независимые контрольные датасеты с вручную рассчитанными expected outcomes, fixed random seeds и проверкой несоблюдения допущений. Для SQL — disposable PostgreSQL 17, свежая схема + upgrade + data isolation + immutability.

**Definition of Done:** Passed verified tests and real data only if available; no premature merge into main.

**Зависимость:** выполненные предыдущие PIE этапы; peer ports только через реальную проверку capabilities. В `main`, UAE, A, B, C, QDP, SRE не коммитить.

## 6. Stage execution protocol & reporting

Каждый запрос «продолжай работу» = прочитать текущую ветку и tracker, выбрать первый незакрытый по коду stage, реализовать ровно этот stage, добавить tests, выполнить доступные unit/static/PG/CI, исправить обнаруженные проблемы, сделать коммит и обновить tracker в GitHub. Если нет PG или upstream — записать BLOCKED/NOT RUN, но не фабриковать successful end-to-end. При изменениях upstream записывать проверенный SHA, обновлять handshake и не force-merge. Отдельный пользовательский запрос нужен для merge into main.

**Checklist template:** `- [x] ~~PIE-MOD-07 — ...~~ (IMPLEMENTED; QA: PASS/FAIL/BLOCKED/NOT RUN)`.

## 7. Release acceptance gates

- PIE truly standalone: owner code `intelligence/patterns_insights`, isolated additive migrations, tests/CI/API/UI; no duplicate UAE/Group C code.
- UAE qualified-only row-level evidence with source/version/cutoff/creator grouping, frozen dataset/research design and exact provenance. No live input? integration status BLOCKED, do not claim ready.
- Candidate space and tested hypotheses recorded; valid effects + uncertainty + multiple testing correction + discovery/holdout separation; no causal claims from associations; sensitivity/negative controls.
- Reviewable immutable Pattern Registry with raw/adjusted inferences, sample/counterfactual caveats, validated versions and current rights.
- Insight LLM cannot invent numbers/citations or modify patterns; clear text-generation uncertainty, review and provenance, spend governance and idempotent charging.
- Source policy, TTL, invalidation, project isolation enforced on reads/exports and derived data.
- PG17 fresh/upgrade, Windows/Linux, actual CI pinned SHA, deterministic gold sets, worker concurrency/recovery, performance benchmark, verified B/C/UAE contracts; Draft PR can be marked ready only after tests.
- No merge without explicit owner instruction.

## 8. Recommended commands (adjust to actual branch)

```bash
python -m unittest discover -s tests -p 'test_pie_foundation.py' -v
python -m unittest discover -s tests -p 'test_pie_*.py' -v
python -m compileall -q intelligence/patterns_insights
# PG17 fixture tests ONLY on disposable DB:
CM_TEST_PG_DSN='postgresql://.../contentmachine_test' python -m unittest tests.test_pie_db -v
git diff --name-status main...feature/patterns-insights-full-module
```
No DSN -> PG NOT RUN (not green). CI QUEUED is not PASS. Gold fixture is not evidence of real live metrics.

## 9. Supporting files verified 2026-10-26

- UAE standalone plan/contract `feature/unified-analytics-full-module/docs/UNIFIED_ANALYTICS_INTEGRATION_CONTRACT.md`.
- Historical UAE `feature/analytics-engine-qualified-pipeline-20261023/intelligence/analytics/aggregate.py`, `docs/ANALYTICS_ENGINE_UNIFIED_V1.md`.
- `main/docs/PERFORMANCE_C_DATABASE_V1.md` (unaltered C Breakout).
