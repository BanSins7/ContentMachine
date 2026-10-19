# ContentMachine — C (Performance Metrics & Breakout Score): PostgreSQL V1

**Основание:** файл пользователя \`ContentMachine_C_Performance_Breakout_DB_Spec_v1.txt\`, версия 1.0, 19.10.2026. Этот документ — **карта уже реализованной структуры БД**, а не утверждение готового C-аналитического движка.

## Решение по формуле — не менять

\`\`\`text
breakout_score = 0.45 * creator_ratio
               + 0.30 * views_growth_per_hour
               + 0.15 * weighted_engagement_smoothed
               + 0.10 * freshness_score
\`\`\`

- Нет нормализации, нормализованных переменных со звёздочкой, min-max/z-score/log и скрытого обрезания.
- \`views_growth_per_hour\` измеряется в просмотрах/час; \`weighted_engagement_smoothed\` — доля; \`freshness_score\` — 0–100. **Breakout Score не обязан лежать в 0–100.** При большой скорости роста именно Growth будет доминировать, это осознанное следствие формулы.
- \`niche_outlier\` хранится самостоятельно и **не участвует** в Breakout Score.
- Вклад каждого слагаемого сохраняется отдельно; незаполненные обязательные показатели → \`NULL\`, не 0. \`score_status='complete'\` требует все обязательные компоненты.

## Что реально есть в PostgreSQL

| Из спецификации C | Таблица/поле в ContentMachine | Решение |
| --- | --- | --- |
| \`content_posts\` | существующая \`videos\` | НЕ создавать вторую identity; \`platform_video_id\` — external post ID; \`creator_id\` — account FK; \`c_content_type\` = \`video/carousel/NULL\` |
| \`metrics_snapshots\` | существующая \`metric_snapshots\` | отдельные исходные факты, время наблюдения, источник, тип статистики, idempotency key и provenance; счётчики NULL или неотрицательные BIGINT |
| \`comparison_cohorts\` | новая таблица | creator/niche/engagement_prior, версия, сопоставимая платформа/тип, окна, медианы и отдельные priors |
| \`comparison_cohort_members\` | новая таблица | FK на **тот же видео/post ID** и конкретный снимок, точное восстановление выбранной базы |
| \`metric_calculation_configs\` | новая таблица | формула/веса, alpha/k, политики когорт, версия; нормализация принудительно FALSE |
| \`calculated_metrics\` | новая таблица | typed NUMERIC значения C1–C7, snapshot_id, previous_snapshot_id, config_id, cohort IDs, компоненты и статусы |
| Откуда \`published_at\` | \`source_video_metadata\` из D1 | \`calculated_metrics.published_metadata_id\` FK — для воспроизводимого Freshness; зависимый score удаляется, если удалены метаданные источника |
| Права источника | \`sources\`, \`source_policy_versions\` | уже были DENY по умолчанию; future C scorer обязан проверять \`can_derive_scores\` и сроки хранения |
| Старый \`analytics_scores\` | остаётся без изменений | не смешивать с новой типизированной таблицей до отдельного интеграционного решения |

### Миграции

- \`intelligence/migrations/003_performance_c.sql\`: вся основная схема, индексы, типы, NULL, versioned uniqueness \`NULLS NOT DISTINCT\`, неизменность raw-снимков, source definition.
- \`intelligence/migrations/004_performance_c_integrity.sql\`: не изменяет ранее применённую 003; immutability конфигурации и готовых результатов, фиксирование/заморозка когорт, ссылка на evidence даты публикации, защита смешивания разных source/view definitions и «полного» Score без обязательных данных.
- Встроенный мигратор \`python -m intelligence.migrate\`: advisory lock, одна транзакция, checksum. **Ни одной старой таблицы не удаляется и существующие сырые значения не подменяются.**

**Важно:** \`metric_calculation_configs\` создаётся **пустой**. Нет установленного \`alpha\`, \`k\`, \`p_L/p_C/p_S\` и **нет активного автоматического scorer**. В БД не записаны фиктивные значения и не производится ранжирование.

## Политика C: свойства структуры и открытые настройки

**С0:** \`views\`, \`likes\`, \`comments\`, \`shares\`, \`saves\`; наблюдения \`observed_at\`, source/version, metadata rights. Missing → NULL. View не подменять reach. \`source_metric_definition\` обязателен для будущего полноценного Score.

**С1:** \`creator_ratio = (V+alpha)/(M_c+alpha)\`; \`alpha\` только из утверждённой конфигурации; creator cohort того же автора/платформы/типа/возраста, без текущего поста.

**С2:** \`niche_outlier = (V+alpha)/(M_n+alpha)\`, отдельная вычисляемая метрика, не компонент Score.

**С3:** \`growth=(V2-V1)/delta_hours\`, две разные точки, один post, один source и одна definition; отрицательная коррекция счётчика → pending/error, не ноль.

**С4:** raw rates для L/C/S и ER=(L+C+S)/V; веса interactions 1/1.5/3; saves отдельно.

**С5:** \`(L+k*p_L)/(V+k)\`, \`(C+k*p_C)/(V+k)\`, \`(S+k*p_S)/(V+k)\`; \`weighted_engagement_smoothed=L_s+1.5*C_s+3*S_s\`. Bayesian priors и k пока не утверждены.

**С6:** \`freshness=100*2^(-t_hours/48)\`; t часов от publication evidence до снимка; «сейчас» не используется для старых снимков.

**С7:** прямая формула вверху. БД сохраняет отдельные компоненты, итог, статусы, version и precise lineage. Отсутствие обязательного source data -> score=NULL.

**Пока открыто по пользовательскому документу:** \`alpha\`, \`k\`, правила оценки prior rates, границы/минимальный размер когорт, возрастные окна, совместимость определения views, политика корректировок отрицательных счётчиков, случай V=0, правила публикации score при поздних обновлениях, лицензии источников. Не заполнять по умолчанию.

## QA и граница этапа

Файл \`tests/test_performance_c_schema.py\` запускается в существующем PostgreSQL 17 CI и проверяет создание/повтор миграций, неотрицательность фактов, append-only историю, FK snapshot/post/cohort, \`NULLS NOT DISTINCT\`, \`normalization=false\`, frozen cohorts, бессрочность настроек версии, ограничения complete-score и удаление зависимого Score при отзыве publication metadata.

Это **QA схемы**. Проверки C1–C7 на математических контрольных значениях, выбор возрастных когорт, source-rights enforcement перед расчётом, расчёт при 10 000 публикаций/месяц и GPU/UI ещё предстоит реализовать и согласовать отдельным планом. Юридические/платформенные права на score нельзя выводить из факта наличия FK в БД.

## Проверка на Windows (когда пользователь решит развернуть)

\`\`\`powershell
cd C:\ContentMachine
python -m intelligence.migrate
python -m intelligence.health
docker exec contentmachine-postgres psql -U contentmachine -d contentmachine -c "SELECT version FROM schema_migrations ORDER BY version;"
\`\`\`

Ожидаются \`003_performance_c.sql\` и \`004_performance_c_integrity.sql\` после \`001\`/\`002\` (и иных более ранних версий в актуальном репозитории). В этой поставке нет источников C, планировщика C, новой страницы интерфейса и платных вызовов.

**Следующее решение** — выбрать, с какого подблока C0–C7 начинать код, только после подтверждения статуса БД и открытых параметров.
