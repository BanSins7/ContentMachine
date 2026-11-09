# Marketing Group C — самостоятельный расчёт C0–C7, без QDP

## Реальный pipeline

`100 видео → Marketing A → Marketing B → D1 Bright Data observations → C0 → C1/C2/C3/C4/C5/C6 → C7 → immutable Market Intelligence snapshot`.

Расчёт Group C локальный и не отправляет данные в LLM. Он использует собственные Marketing-модули, импортирующие **математические ядра из локальной копии** `intelligence/market_intelligence/_copied/group_c/`; чужие Group C workers, QDP, Group C IAM-таблицы, QDP receipts и QDP adapters **не подключаются**.

### Аналитические этапы

| Этап | Реализация в Marketing | Проверки |
|---|---|---|
| C0 | `group_c_quality.py`: последнее raw D1 наблюдение | Точная целочисленная метрика, `platform_post_views`, provider/view version, data quality, `precision=exact`, `observation_time_assumed=false`, сроки действия, timestamp collected/observed, актуальная policy, source ID. Нет перехода к старому «хорошему» наблюдению |
| C1 | `performance.py`: оригинальный `compute_creator_ratio` | Медиана как минимум 5 других публикаций **того же автора**, одной платформы, совместимого D1 provider/definition/version, сравнимого возраста. Точное Decimal значение не округляется перед C7 |
| C2 | `group_c_cohorts.py`: оригинальный `compute_niche_comparison` | Тема только из verified Marketing A+B SHA receipts и разрешённой собственной B8 taxonomy. Как минимум 5 других публикаций и 2 автора на той же платформе, с тем же провайдером и возрастным окном. Диагностический коэффициент **не включён** в C7 |
| C3 | `performance.py`: оригинальный `compute_views_growth_per_hour` | Два непосредственно соседних raw замера публикации, один source ID и provider definition/version, строго возрастающий таймкод, без падения счётчика и без пропуска исправленных значений |
| C4 | `extended_c.py`: оригинальный `compute_raw_engagement` | Реальные views, likes, comments, shares, без выдуманного retention и без подмены NULL нулём |
| C5 | `extended_c.py`: оригинальный `compute_bayesian_engagement` | Детерминированная, SHA-замороженная для отчёта выборка, минимум 5 публикаций от минимум 2 **других авторов**, одинаковый provider/unit/version/platform/возраст. Приорные ставки и K основаны на реально полученных счётчиках; статус `unapproved` |
| C6 | `extended_c.py`: оригинальный `compute_freshness_score` | Публикация и snapshot time, реальные UTC даты. Отсутствующий `published_at` => UNKNOWN |
| C7 | `extended_c.py`: оригинальный `compute_breakout_components` | Точные `Decimal` C1/C3/C5/C6, фиксированные веса 0.45/0.30/0.15/0.10, без C2. Если недоступен хотя бы один из четырёх входов, итог `NULL` |

### Самостоятельная проверка и сохранение

`group_c_report.py` **независимо пересчитывает** доступные C1/C3/C4/C5/C6/C7 по исходным D1 snapshot bytes/времени и сравнивает значения. Для каждого видео вычисляет `mi-group-c-c0-c7-native-proof/v1` с SHA полной C payload и snapshot/cohort lineage. Отсутствующие/недостоверные значения не «подтверждаются». `service._read_dataset()` запускает этот gate в той же транзакции `REPEATABLE READ`, что читает D1 + текущую Marketing A/B запись.

`MarketIntelligenceService.freeze` сохраняет итог в существующую `market_intelligence_snapshots`, защищённую триггером от UPDATE/DELETE. Новая таблица официальных `calculated_metrics` или копия Group C schema **не создаётся**: это отдельный источник диагностического рыночного исследования, а не оригинальный production Group C.

Для пакетного режима `batch.finalize_once` автоматически проверяет: **все pinned video ID получили A/B**, незавершённые Bright Data jobs отсутствуют, **все выбранные D1 snapshots прошли C0**, текущие метрики переизвлечены и пересчитаны в transaction; только после этого запускается `freeze(video_ids=[...],require_all_exact=True)` и UUID+SHA immutable snapshot сохраняется в `jobs.result`. UI показывает C0/C2/C5/C7 coverage, без нажатия дополнительных кнопок.

### Критически важные границы

- Ни `reviewed/approved production C5 priors`, ни утверждённый полноплатформенный target cohort не создаются из пользовательской выборки вирусных видео автоматически. Гипотезы Marketing могут быть смещены способом отбора; это не устраняется верной формулой.
- Оригинальная сертификация C7 требует отдельно проверенного review, активированного config, frozen independent raw D1 cohort, аудита качества и прав. Этого нельзя заменить подписью собственного JSON или словами LLM. **Поэтому C7 в Marketing = `diagnostic_unapproved_not_persistable`, `original_full_c_certified=false`, официальные calculated_metrics не публикуются.**
- В реальных публичных данных Bright Data может не быть retention/watch-time. Group C в таком случае не подставляет изобретённые значения.
- Если source data неполные, batch остаётся в `awaiting_exact_metrics_or_media`, а не объявляется завершённым.
- Для production приёмки нужны успешный CI, проверка реальных D1 provider responses и E2E прогоны 1/5/100 видео. На данный момент они не доказаны.

### Тесты

`tests/test_market_group_c_native.py`: 21 записи от 3 авторов, реальная copied-математика C1/C2/C3/C4/C5/C6/C7, fingerprints, изменение/исправление метрик, несовместимый provider, отсутствие одобрения и подмена SHA.

`tests/test_market_group_c_batch_finalize.py`: автоматический freeze 100-video batch по exact-ID, отказ при незавершённых A/B и Bright Data jobs, отказ при неполном C0. Оба включены в `.github/workflows/rtb-market-intelligence.yml`.
