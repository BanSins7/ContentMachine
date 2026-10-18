# Discovery D1.1 — результат и QA

Дата: 2026-10-18. Область: только **PostgreSQL migration 002** и интеграционные тесты.

## Что было добавлено

- `intelligence/migrations/002_discovery.sql`: аддитивная миграция для `source_policy_versions`, `search_runs`, `source_requests`, `source_video_metadata`, `creator_topics`, `video_topics`, `monitor_targets`, `source_quota_ledger`, `data_quality_flags`, `human_labels`.
- `sources.can_search` и `sources.can_store_metadata`: **FALSE по умолчанию**.
- `discoveries.run_id/source_request_id/discovery_key` и уникальный индекс для новых occurrence.
- `jobs.lease_generation`: **только структура для будущего fencing**, обработчик очереди ещё не использует его.
- `metric_snapshots.source_policy_version_id/observation_time_assumed`.
- `tests/test_discovery_schema.py`: тесты реальной PostgreSQL 17 миграции, повторяемости, FK/UNIQUE/CHECK, прав, лимитов, нулевых/отсутствующих метрик, совместимости старых записей.

## QA

| Проверка | Доказательство | Статус |
|---|---|---|
| PostgreSQL 17: применение 001 и 002 | GitHub Actions workflow `tests.yml` | CI, проверить по коммиту |
| Повтор миграций ничего не меняет | `test_all_tables_exist_and_migration_is_reentrant` | CI |
| Новые source rights по умолчанию запрещены | `test_new_source_rights_default_to_deny` | CI |
| Один активный policy version, корректный JSON | `test_one_active_policy_and_typed_capabilities` | CI |
| Дубликаты source requests и discoveries | `test_run_request_and_attempt_uniqueness`, `test_discoveries_keep_legacy_and_dedupe_by_new_key` | CI |
| Ограничения quota и бюджетных сумм | `test_quota_and_spend_limits_are_enforced` | CI |
| Старые jobs работают с новым столбцом | `test_job_generation_defaults_zero` | CI |
| Старые загрузчики / JS регрессии | общий набор существующих тестов | CI |
| Применение миграции **на Windows пользователя** | не выполнялось мной удалённо | PENDING |
| Живой импорт 10 000 роликов | относится к D1.3–D1.7 | NOT STARTED |
| Сбор реальных роликов по внешнему API | D2 и далее | NOT STARTED |

**Внимание:** схема БД сама по себе не предотвращает запись запрещённых метаданных: DB права по умолчанию выключены, но обязательный enforcement в persistence-коде — D1.3. До него никаких импортеров/сборщиков не включено. Равно и `lease_generation` пока не защищает очередь без будущих проверок токена на уровне SQL update.

## На компьютере пользователя

Пользователь **явно отказался от backup** на этом этапе. Перед миграцией сообщил: PostgreSQL connected, schema_version `001_foundation.sql`, `videos=0`, `jobs=0`, `sources=0`. В остальных таблицах наличие записей отдельно не проверялось. Скрипт не содержит DROP/TRUNCATE/DELETE; существующая 001 не изменена.

После Fetch/Pull:

```powershell
cd C:\ContentMachine
python -m intelligence.migrate
python -m intelligence.health
docker exec contentmachine-postgres psql -U contentmachine -d contentmachine -c "SELECT version FROM schema_migrations ORDER BY version;"
```

Должны появиться `001_foundation.sql` и `002_discovery.sql`, `database=connected`.

**Следующий этап:** D1.2 — типизированные модели и строгий валидатор. Не считать D1 целиком готовым.
