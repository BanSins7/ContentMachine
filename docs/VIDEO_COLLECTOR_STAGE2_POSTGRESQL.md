# Video Collector Stage II — PostgreSQL, held run, Policy/Budget Gate and API

**Область:** этапы 5–6 Video Collector. **Статус:** код планирования записей и API; live DTK execution отсутствует. Все внешние запросы, списания, retries и скачивание медиа **запрещены**.

## Таблицы и миграция

Аддитивная `intelligence/migrations/054_collector_planning.sql` (при интеграции перепроверить номера миграций в main). Повторно используем:
- `project_registry`, `sqg_generations`, опубликованную RTB,
- `sources`, `source_policy_versions`, `source_quota_ledger`,
- `search_runs`, `source_requests`, `jobs`, `videos`, `discoveries`, `audit_events`.

Новые:
- `collector_query_sets`: будущий **только доверенный** Query Validation publisher пишет сюда immutable `validated_query_set/v1`, связанную с **completed** SQG generation и approved RTB. **Нет пользовательского HTTP endpoint для записи**. Таблица пуста, пока Search Planner не реализует publisher.
- `collector_plans`: неизменяемый content-addressed planning snapshot, FK на query set/source/policy version, `plan_sha256` и server-side source policy.
- `collector_runs`: один Collector-specific run поверх `search_runs`, pin plan + rights snapshot, `planned_held` или cancelled.
- `collector_provider_tasks`: логическая DTK-задача с заранее зафиксированными request/job IDs, `not_submitted` без provider task id.
- `collector_task_queries`: связи одной физической задачи с несколькими query/hypothesis.
- `collector_video_hypotheses`: FK связи video/query/источника для будущего live ingest.

База запрещает UPDATE и DELETE `collector_query_sets` и `collector_plans`: snapshot нельзя подменить после commit.

## API (localhost, использует Host/Origin/X-ContentMachine из app.Handler)

- `GET /api/intelligence/collector/sources`: offline список декларированных adapters.
- `POST /api/intelligence/collector/preview`: два формата:
  - `{"query_set": {...}, "source_id":"dtk_tiktok", "limits":null}` — бесплатный структурный Preview; **нельзя** совершить Commit на основе браузерной JSON.
  - `{"query_set_id":"<uuid>", "source_id":"dtk_tiktok", "limits":null}` — DB-verified Preview; читает PostgreSQL, настоящий `sqg_generations.status=completed`, полный RTB snapshot, проверяет связи гипотез и источник/квоту. Нет сетевых провайдеров, записи в БД, оплаты.
- `POST /api/intelligence/collector/runs`: только `query_set_id, source_id, limits, plan_hash, idempotency_key, confirm=true, max_spend_usd="0.00"`. Подлинность query set сверяется с DB/RTB/SQG; устаревший hash или права → 409. Финансовое согласие **только на план**, не на DTK provider request.
- `GET /api/intelligence/collector/runs/<numeric_run_id>?project_id=<32hex>`: статус, jobs, request IDs, task state и отсутствие внешних вызовов.
- `POST /api/intelligence/collector/runs/<numeric_run_id>/cancel`: `{"project_id":"<32hex>"}` — идемпотентная отмена неотправленных задач.

POST всё ещё требует `X-ContentMachine: 1` на уровне `app.Handler`; `Content-Length` <=256KiB; ошибки не содержат ключи/SQL.

## State machine и финансовые гарантии

`preview_only → (только trusted QuerySet) → search_runs.created + collector_runs.planned_held → cancelled`.

**Критическое свойство:** при Commit в ОДНОЙ транзакции создаются план, run, `source_requests`, `jobs`, task lineage, audit. Jobs имеют `job_type='collector.search.held'` и `run_after='infinity'::timestamptz`, так что существующий `jobs.lease_next` НЕ может получить их ни после перезапуска, ни по истечении обычного времени. **Ни одного вызова DTK в API и worker этой стадии нет**.

На этапе III отдельный provider execution worker обязан заново проверить active source policy, provider readiness, rights, quota и новый финансовый/операционный consent ДО снятия hold. Из этого этапа нельзя запустить TikTok даже через изменение внешнего JSON. Установленный `dtk.py` PR #13 остаётся отдельным.

Идемпотентность: `collector:<project_id>:<client_key>` в `search_runs` и сохранённый `plan_sha256`. Повтор того же ключа и плана → тот же `run_id`; другой план → 409. Другие подсистемы (D1) используют иные namespaces.

## Источник и quota prerequisite

Источник **не включается автоматически**. Для будущего held Commit администратор должен отдельно настроить:
- `sources.id='dtk_tiktok'`, `status='enabled'`, `can_search=true`, `can_store_metadata=true`, конкретный `retention_days`;
- одну активную `source_policy_versions` с `reviewed_at`, `capabilities.keyword_search=true`, `permissions.can_search=true`, `permissions.can_store_metadata=true`, явным `retention_policy.retention_days` не больше source allowance;
- текущую запись `source_quota_ledger` для `bucket_name='collector.requests'` и положительной достаточной `limit_units`.

Важное: это **административные сведения о реально подтверждённых разрешениях**, не способ обойти условия TikTok/DTK. Прежде чем включать live поиск, отключить неразрешённую архивизацию DTK и проверить права. В отличие от execution, held Commit **не резервирует** provider quota: у plan `quota_reserved=0`, будущий worker проверяет заново. `max_spend_usd` обязан равняться строго нулю. Не выдаём бесплатность будущих external calls как подтверждённый факт.

## Проверки

```bash
python -m intelligence.migrate
python -m unittest discover -s tests -p "test_video_collector_stage2.py" -v
python -m unittest discover -s tests -v
python -m compileall -q intelligence/collector/
```

Linux CI применяет миграции на PostgreSQL 17, запускает все Python/UI tests; Windows CI проверяет D1. Оба должны пройти до merge. Полный E2E с SQL sealed QuerySet возможен после завершения Query Validation publisher и закрытия SQG generation; это зависимость от параллельного Search Planner.

Следующий этап — интеграция проверенного `intelligence.connectors.dtk` из PR #13 (или после его merge в main), а затем отдельный worker execution и ingest. **Не выполнять API-вызов из нативного HTTP POST маршрута.**
