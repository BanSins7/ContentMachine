# GC-MOD-17 — Сглаженные показатели вовлечённости (C5)

**Ветка:** feature/group-c-full-module. **Статус:** CODE IMPLEMENTED; exact-head PostgreSQL17 и CI QA пока NOT VERIFIED; реальные IAM/QDP-source E2E BLOCKED_EXTERNAL. Никаких изменений main/других веток и ранее применённых миграций.

## Расчёт (только дробные доли)

- Доля сглаженных лайков: (L + k * pL) / (V + k).
- Доля сглаженных комментариев: (Comments + k * pC) / (V + k).
- Доля сглаженных репостов: (Shares + k * pS) / (V + k).
- Взвешенная сглаженная вовлечённость: Ls + 1.5 * Cs + 3 * Ss.

Код intelligence/group_c/bayesian_engagement.py: compute_bayesian_engagement() не запрашивает авторизацию, но никогда не придумывает k/pL/pC/pS. k должен быть положительным, priors — явными конечными долями [0,1]. Все четыре параметра должны иметь не более 12 значащих десятичных знаков **после запятой**, соответствовать PostgreSQL NUMERIC(30,12), а V > 0 согласно C4/C7. Отсутствующие, отрицательные, float/bool, SQL NULL, скрыто округляемые значения и переполнение блокируются. Исходные V/L/C/S — неотрицательные точные PostgreSQL BIGINT; saves в формулу не входят.

Вычисления используют local Decimal context precision=80 и q() с HALF_UP, 12 знаками после запятой. Взвешенная величина вычисляется из **неокруглённых** частных, а не как сумма трёх отдельно округлённых значений. Не производится нормализация, clipping, умножение на 100 или приведение к C7 complete.

Эталон для V=100, L=10, Comments=3, Shares=2, k=50, pL=0.05, pC=0.015, pS=0.01:
- Ls=0.083333333333;
- Cs=0.025000000000;
- Ss=0.016666666667;
- weighted=0.170833333333.

## Авторитетная цепочка доказательств и IAM

PostgreSQLBayesianEngagement.derive принимает точные project/video/current raw snapshot/metadata/source/version/policy/config ID/prior freeze ID/review request ID и **обязательный внешний** BayesianEngagementApprovalVerifier.verify_c5_use. При отсутствии независимого trusted IAM компонента или отрицательном решении вызов завершается ContractError; провайдера по умолчанию не существует.

В одной независимой REPEATABLE READ READ ONLY транзакции адаптер:
1. Проверяет источник, проект, rights/TTL, метаданные и самый новый raw D1 snapshot без last-good fallback (C0 complete counters, exact time, metric version).
2. Читает точно утверждённый human review request + decision, единственную активную глобальную конфигурацию и подписанное activation event; пересоздаёт полный subject_sha256 с creator, engagement_prior и optional niche freezes, сравнивая payload, version, project и target.
3. Снова проверяет все замороженные группы через Stage11 _require_db_rights и source-policy adapter, включая оригинальные snapshot/metadata.
4. Проверяет legacy comparison_cohorts engagement_prior + каждый frozen member по проекту, типу контента, source/version/rights, идентичности original raw snapshot, exact counters и membership. Из неизменённых raw peer counters пересчитывает объединённые priors и требует полного совпадения с новыми freeze/legacy статистиками. Для C5 не подменяет verified priors значениями из пользовательского JSON.
5. Вызывает внешний IAM verifier для точного review_sha, actor proof, global activation ID и C5 prior freeze; строго принимает только boolean True.
6. Возвращает неизменяемый BayesianEngagementResult со всеми идентификаторами lineage, k/prior долями, четырьмя C5 полями и canonical evidence SHA256.

Статус результата: c5_derived_unpersisted. ready_for_breakout=False. Этот диагностический read-only этап ничего не пишет в calculated_metrics, outbox и не ACK-ает QDP; полноценная схема persistence наступает в GC-MOD-20.

## Проверки

tests/test_group_c_bayesian_engagement.py: 16 чистых сценариев Decimal/контрактов и 11 PG17 disposable сценариев, повторно использующих тестовые review+freeze+global activation фикстуры Stage11/12 (продуктовый IAM там не подменяется). Linux/Windows pure discovery использует test_group_c_*.py, PG17 fresh/upgrade включены отдельными шагами в .github/workflows/group-c-module.yml.

- Проверяется, что неподписанные, отозванные, superseded, чужие source/version/project/metric/cohort не производят результата; новый retracted raw snapshot блокирует старый.
- Отдельно проверяются отсутствие k/priors, NUMERIC precision, точная арифметика без premature rounding, исходная иммутабельность и отсутствие score persistence.
- Нельзя помечать QA PASS до исполнения всех тестов; в текущей среде точный runtime PostgreSQL17 и CI результат NOT VERIFIED. Реальные IAM/QDP E2E требуют согласования с соседними ветками.

**Следующий этап:** GC-MOD-18 — актуальность публикации (C6 Freshness) по verified published_at и observed_at.
