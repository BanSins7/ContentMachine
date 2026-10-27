# GC-MOD-16 — Сырые показатели вовлечённости (C4)

Статус: CODE IMPLEMENTED. Исполняемая приёмка PostgreSQL17 и GitHub CI — NOT VERIFIED; реальные QDP/IAM E2E — BLOCKED_EXTERNAL.

## Математический контракт

| Название показателя | Доля (fraction) | Отображение в процентах |
| --- | --- | --- |
| Доля лайков | likes / views | 100 * likes / views |
| Доля комментариев | comments / views | 100 * comments / views |
| Доля репостов | shares / views | 100 * shares / views |
| Общая вовлечённость | (likes + comments + shares) / views | 100 * (likes + comments + shares) / views |
| Взвешенная вовлечённость | (likes + 1.5 * comments + 3 * shares) / views | 100 * (likes + 1.5 * comments + 3 * shares) / views |

Код: intelligence/group_c/raw_engagement.py. Функция compute_raw_engagement принимает четыре неотрицательных точных BIGINT, при этом views > 0. SQL NULL, float, bool, отрицательные значения, переполнение NUMERIC(30,12), нулевой знаменатель и неявная подмена отсутствующих данных запрещены. Saves сознательно исключены. Доли выше единицы возможны и не обрезаются.

RawEngagementRates хранит поля *_fraction и unit=fraction. Метод as_percentages явно создаёт отдельный RawEngagementPercentages с полями *_percent и unit=percent. Проценты вычисляются из исходных точных счётчиков (не из округлённых долей), поэтому нет double rounding. Формулы исполняются с локальной Decimal precision=80, ROUND_HALF_UP, scale=12; контекст вызывающего кода не влияет.

## D1, источник, доказательства

PostgreSQLRawEngagement.derive повторно использует read_c0_pair: один независимый REPEATABLE READ / READ ONLY проход по проекту, D1 данным, метаданным публикации, правам и retention источника, строго совпадающим source/metric definition/version/policy, реальным observed_at и качеству. Проверяются два самых новых НЕОТФИЛЬТРОВАННЫХ raw observations; retracted/corrected/new-version observations не могут быть обойдены через last-good fallback.

C4 использует только текущие значения views/likes/comments/shares. Предыдущий снимок служит связке currentness и совместимости с будущим C7. Результат содержит exact current/previous snapshot IDs, project/video/source/policy/metadata/metric-version, current observed_at, Decimal-показатели и deterministic SHA256. Статус raw_engagement_derived_unapproved, ready_for_breakout=False, записи в calculated_metrics/Analytics outbox/QDP ACK отсутствуют.

Вспомогательная чистая функция compute_verified_raw_engagement проверяет тип и внутреннюю согласованность C0Pair, но не доказывает актуальность внешних разрешений без авторитетного D1 reader.

## QA и ограничения

tests/test_group_c_raw_engagement.py: 13 pure и 11 disposable PG17 методов: эталонные Decimal-векторы, проценты/доли, отсутствие двойного округления, нули/NULL, исключение saves, превышение 100%, overflow, source-history replay, revoked rights, version/policy, TTL, отсутствие записи score. Pure discovery охвачен Linux/Windows через test_group_c_*.py, PostgreSQL17 fresh+upgrade добавлены отдельными шагами workflow group-c-module.yml.

Запуск точных тестов и PostgreSQL17 в текущей среде не подтверждён; только инспекция кода и привязки тестовых фикстур. Статус QA не обозначать как PASS до реального результата. Реальные provider/QDP/IAM end-to-end проверки требуют внешней интеграции. Соседние ветки и main не изменяются.

Следующий шаг: GC-MOD-17 — сглаженная вовлечённость (Bayesian) с явно утверждёнными k, prior лайков, комментариев и репостов без придуманных значений.
