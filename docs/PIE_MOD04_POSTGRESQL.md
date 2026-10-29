# PIE-MOD-04 — PostgreSQL 17: независимая схема и договор на миграции

**Дата:** 2026-10-26. **Ветка:** `feature/patterns-insights-full-module`. **Статус:** схема/тесты реализованы; реальное выполнение PostgreSQL в этом сеансе недоступно; CI требует фактического PASS. **Next:** PIE-MOD-05 UAE reader/handshake.

## Номера и независимость

Аудит `main` показал максимум `053_ke_c_qualified_execution.sql`. В ветках SRE, Group A/B/C заняты `054`–`056`; QDP пользуется собственной `04x_` группой. Для отдельного PIE выделено имя `200_pie_standalone_foundation.sql`, отсутствующее в просмотренных ветках на момент аудита. Применяется общим `python -m intelligence.migrate` **после** всех миграций main; этот мигратор поддерживает транзакции, advisory lock, SHA256-сверку и неизменяемую историю. После merge в `main` нужно **ещё раз** проверить коллизии имени миграции и необходимые upstream-объекты. Это не резерв GitHub-namespace и не доказательство совместимости с будущими ветками.

Ни один SQL-файл `001`–`053` не меняется; никакие исторические `016_analytics`, `017_analytics`, `018_analytics` не копируются; UAE/SRE/QDP/Group A/B/C не импортируются и не переписываются. Новые объекты имеют префикс `pie_`.

## Схема (14 новых таблиц)

| Таблица | Владение и ограничения |
|---|---|
| `pie_research_runs` | UUID, project ID, source и **конкретный** source-policy ID, версия C config, план исследования, исследовательское `as_of`, исходное состояние `draft`; source-policy-source mismatch блокируется trigger |
| `pie_dataset_manifests` | точный run, SHA256 dataset/manifest, declared included/excluded counts, provenance; по умолчанию `evidence_state=unverified`, а не `qualified` |
| `pie_dataset_members` | canonical `videos.id`, member version/hash, included/excluded с явным reason; уникальность видео и snapshot в рамках dataset |
| `pie_comparison_sets` | dataset-scoped immutable сравнение/определение групп |
| `pie_pattern_candidates` | гипотезы и evidence SHA, **только exploratory**; DB не производит статистическую валидацию |
| `pie_pattern_validations` | append-only verdict/status/evidence statistics, но не автоматическое право публикации |
| `pie_insight_versions` | append-only proposed insight, кандидат, версия и evidence; `published/approved` нельзя указать напрямую |
| `pie_insight_reviews` | запись human-review decisions; DB не удостоверяет реальную identity/authority ревьюера до PIE-MOD-32 |
| `pie_spend_authorizations` | явный проект, approver, budget и expiry; поля metadata и FK, не самостоятельный provider consent |
| `pie_model_attempts` | request SHA уникален на проект; `planned`, budget trigger сериализует и суммирует резервы, неизвестные оплаты автоматически не высвобождаются |
| `pie_model_attempt_events` | append-only ledger событий reserved/charged/refunded/failed/uncertain |
| `pie_jobs` | queued/leased/terminal состояние, project/run scope, idempotency, fence/lease поля; SQL сам не запускает worker |
| `pie_outbox` | append-only event payload+hash; транзакционный publisher будет в PIE-MOD-33 |
| `pie_invalidation_events` | append-only reason+input fingerprint; runtime propagation будет в PIE-MOD-27 |

Все PIE-таблицы, ссылающиеся на другие PIE-объекты, используют составные `(project_id, id)` или `(project_id, parent_id, dataset_id)` внешние ключи. Это обеспечивает **структурную** изоляцию между проектами. Наличие валидного foreign key на `source_policy_versions` **не означает** текущих прав, одобрения, retention или qualified dataset. PIE-MOD-05/06 обязан заново проверять актуальные права, versioned UAE evidence, состояние QDP/A/B/C/SRE и отзыв доступа перед каждым чтением/экспортом.

Исключение из append-only триггера — `pie_jobs`, поскольку будущему worker нужна смена состояния и lease fencing. Прочие 13 таблиц защищены `BEFORE UPDATE` триггером; явное DELETE/каскадное удаление оставлено для авторизованного retention/erasure flow. Отдельный future grants/RLS и проверка authorization на чтение API также обязательны: **FK не является RLS**. `verified` в поле `evidence_state`, review=approved или сохранённый SHA сами по себе не делают данные доверенными.

## QA

**Реально выполнено локально:** `cd /mnt/data/pie_stage04 && PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_pie_*.py' -q` — 52 теста всего, 8 PG-зависимых skipped, 44 пройдено без ошибок. `python -m compileall -q intelligence/patterns_insights tests/test_pie_db_schema.py` — PASS. **На этой машине отсутствуют PostgreSQL server и psycopg**, поэтому фактический SQL execution/PG17 migration и CHECK/trigger runtime **NOT RUN**, не PASS.

**CI workflow:** отдельный Ubuntu PostgreSQL 17 service job с disposable `pie_test` + `pie_upgrade_test` БД, явным `psycopg[binary]`, настоящим `intelligence.migrate` и SQL тестами. Тесты: база с нуля, повтор миграции с checksum, legacy-main→PIE upgrade, source-policy/source mismatch, два проекта, неверные FK, duplicate samples, UPDATE immutability, no aggregate-only shortcut, budget reservation/replay limits, leased job constraints и outbox immutability. Проверки выполняются только при установленных `CM_TEST_PG_DSN` и `CM_TEST_PG_UPGRADE_DSN` и требуют имён БД, содержащих `test`. Пока CI не закончится, PG17=**NOT VERIFIED**.

В документах и tracker не утверждать `release-ready при незавершённом PG17 QA и отсутствии реального UAE. Не делать merge в `main` без отдельного согласования.
