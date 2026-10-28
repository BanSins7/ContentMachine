# GC-MOD-19 — Breakout Score C7: фиксированные компоненты и проверка lineage

**Ветка:** \`feature/group-c-full-module\` (только Group C). **Код:** \`intelligence/group_c/breakout_score.py\`. **Тесты:** \`tests/test_group_c_breakout_score.py\`. **Статус:** CODE IMPLEMENTED; exact-head PG17/CI QA NOT VERIFIED; production IAM/QDP/Analytics E2E BLOCKED_EXTERNAL. Не меняем \`main\`, ветки соседних модулей, а также миграции 003/004/056–059.

## Чистая арифметика — совместимость с существующей C7-схемой

\`\`\`text
C7 Breakout Score =
    0.45 * C1 creator_ratio
  + 0.30 * C3 views_growth_per_hour
  + 0.15 * C5 weighted_engagement_smoothed
  + 0.10 * C6 freshness_score
\`\`\`

C2 niche comparison — **диагностическая** метрика, не учитывается в формуле. Нет нормализации, верхней границы 100, деления на сумму весов, дополнительного вектора контекста или произвольных округлений. C7 вправе быть больше 100. Значения C1, C3, C5, C6 обязательны, finite, неотрицательные, строго Decimal/NUMERIC(30,12), без float/bool/str/None, автозаполнения или скрытого округления утверждённых исходных результатов. Единственная допустимая верхняя граница — C6 <= 100. PostgreSQL numeric overflow блокируется.

\`compute_breakout_components\` применяет строго утверждённый \`BREAKOUT_WEIGHTS=(0.45,0.30,0.15,0.10)\` из Group C contracts и точную локальную арифметику Decimal precision=80. Каждый из четырёх отдельных компонентов округляется \`ROUND_HALF_UP\` до **12 знаков**, и только затем складывается в \`breakout_score\` с финальным q(), **в точности согласно** \`intelligence/group_c/pure_math.py\`, SQL NUMERIC(30,12) и \`004_performance_c_integrity.sql\`.

Чистая функция возвращает frozen \`BreakoutComponents\` с полными четырьмя исходными значениями, \`score_creator_component\`, \`score_growth_component\`, \`score_engagement_component\`, \`score_freshness_component\`, \`breakout_score\`; \`normalized=False\`. Отдельный regression сравнивает все пять C7 полей с независимыми GOLD в \`tests/test_group_c_pure_math.py\`, полученными ранее из Analytics reference scoring. Пример: C1=2, C3=10, C5=0.2, C6=50 => C7=8.930000000000.

## Сборка только из однотипных исходных результатов

\`assemble_breakout_preview(creator, growth, engagement, freshness)\` принимает **только** dataclass receipts исходных GC13/15/17/18 с ожидаемой schema_version и *diagnostic-only* status. Сборщик заново вычисляет каждый исходный C1, C3, C5, C6 и сверяет canonical SHA-256 с оригинальным алгоритмом соответствующего модуля; это защита от случайной подмены/расхождения результата, но **НЕ** криптографическое подтверждение текущей IAM-авторизации.

Проверяются в явном виде:

- project_id, video_id, current snapshot_id, source_id/source_policy_version_id, metadata_id, source metric definition/version одинаковы во всех четырёх receipts;
- previous_snapshot_id совпадает у C3 и C6, C3 current_observed_at равен C6 observed_at, а original C1.views == C3.current_views == C5.rates.views;
- C1 и C5 ссылаются на **одни и те же** config_id, calculation_version, review_request_id, review_decision_id, activation_event_id, review_subject_sha256;
- исходные C1 median/alpha/ratio, C3 microsecond delta/observations/raw views, C5 counters/k/approved priors/smoothed components и C6 published_at/observed_at/age/freshness корректны математически и имеют те же контрольные суммы;
- обязательные IDs и SHA-256 не могут отсутствовать или иметь некорректный формат.

Результат \`BreakoutPreview\` неизменяем, содержит полный список согласованных lineage identifiers, всех пяти source SHA-256, четыре компонента C7, total C7 и собственный canonical SHA-256. **Статусы:** \`status="breakout_preview_unpersisted_unverified"\`, \`score_status="pending_review_and_atomic_revalidation"\`, \`ready_for_persistence=False\`.

**Критическое ограничение:** существующие PostgreSQL reader-адаптеры C1/C3/C5/C6 выполняют свои **отдельные** repeatable-read транзакции. Сборщик в GC19 не создаёт фиктивное утверждение, будто они разделяли один атомарный snapshot/достоверный IAM proof. Текущая конфигурация могла быть переключена, право источника отозвано, источник переиздан/ретрагирован между чтениями или после них; SHA-256 dataclass не является подписью оператора. Поэтому GC19 не устанавливает \`score_status=complete\`, не пишет \`calculated_metrics\`, не завершает QDP ACK и не публикует в Analytics.

**Требование для GC-MOD-20:** в одной защищённой PostgreSQL transaction заново прочитать исходные D1 facts, актуальный latest-raw, quality/retention/source rights, publication metadata, frozen creator+prior/niche, reviewer и IAM/global activation; пересчитать все C1,C3,C5,C6,C7 по canonical формуле, затем атомарно создать immutable \`calculated_metrics\` с точными полями, ID lineage и версиями. Нельзя просто persist offline \`BreakoutPreview\`.

## Тесты и QA

\`tests/test_group_c_breakout_score.py\` проверяет numeric gold parity с Stage07, FULL/HALF_UP precision, overflow, missing/NaN/float/bool и неотрицательность, отсутствие C2, raw C7 > 100, стабильный Decimal контекст, immutable receipts и digest, mismatched config/approval IDs, snapshot/version/source/policy, tampered source evidence. Disposable PostgreSQL17 tests повторно используют existing тестовые frozen cohorts + signed human-review/activation fixtures (не продуктовый IAM) и реальные raw D1 snapshots для C1/C3/C5/C6, проверяют подтверждённую сквозную композицию и невозможность считать старые receipts актуальным DB authorization после global switch/source revoke/retraction.

CI Linux/Windows запускает Group C pure glob; отдельные PG17 fresh/upgrade шаги с \`GC_TEST_DISPOSABLE_DB=YES\` добавлены в \`.github/workflows/group-c-module.yml\`. Точный запуск этих suites и GitHub CI в текущем окружении НЕ ПОДТВЕРЖДЁН. Production IAM/QDP/source E2E BLOCKED_EXTERNAL; QA PASS не выставлять без наблюдаемого результата.

**Следующий этап GC-MOD-20:** атомарная запись \`calculated_metrics\` и защита complete-score immutable/idempotency.
