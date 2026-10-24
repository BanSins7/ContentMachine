# Qualified Data Pipeline v1 — SRE → Qualified Set → Enrichment → Analysis Preparation

**Ветка:** `feature/qualified-data-pipeline-v1-20261023` (23.10.2026).
**Статус:** реальный локальный backend + PostgreSQL migration 016 + dedicated worker + HTTP/UI + offline/DB tests. Это не Video Collector, не платное enrichment API и не запущенные мультимодальные анализаторы A/B/C.

## 1. Назначение и непреодолимые границы

Этот pipeline берёт **только** проверенные результаты одного *завершённого* SRE classification run и формирует пригодный для downstream analysis snapshot. Он не решает заново, релевантно ли видео, и не подменяет SRE поиск. Источник истины: `sre_classification_runs`, `sre_classification_results`, `sre_human_labels`, immutable approved `sre_taxonomy_versions`, `sre_readable_content_versions`.

```text
SRE completed run + approved taxonomy
    -> QDP Preview (read-only)
    -> explicit Create Run / idempotency
    -> Qualified Set resolver (human override / category evidence / rights)
    -> qdp_items + per-video durable jobs
    -> local dedicated worker (lease / project scope)
    -> Enrichment from authorized D1 snapshots
           source_video_metadata
           metric_snapshots
           media_assets
    -> rights, retention, publication freshness gates
    -> materialized qdp_enrichments
    -> A/B/C Analysis Readiness (no actual analysis)
    -> immutable qdp_preparations
    -> read-only operator UI / future consumers
```

**Нельзя утверждать:** наличие A/B/C результатов, 10 000 qualified в месяц, live TikTok/YouTube/Instagram запросов или права на использование чужого контента. Enrichment v1 = *read-only hydration already licensed/stored local records*, не очередной внешний провайдер. Само получение Source Snapshot не даёт разрешения на анализ медиа или производный скоринг.

## 2. Qualified Set: точный алгоритм

1. Проверить local project ID (32 lower-hex) и canonical UUID SRE Run.
2. В PostgreSQL убедиться, что run принадлежит проекту, `status=completed`, `sre_taxonomy_versions.status=approved`. Другой проект и незавершённый запуск — hard failure.
3. Прочитать классификации этого запуска только по approved `sre_taxonomy_categories`. Использовать текущую несуперседированную `sre_human_labels` для той же (project, video, taxonomy version, category), **человеческая метка заменяет модельную**.
4. `match` считается eligible только при наличии `sre_readable_content_versions` для указанной content revision (активные права и сроки); без неё `match` попадает в `needs_review`, не в qualified.
5. Для каждого video ID: если хотя бы одна категория eligible match → **qualified**; иначе `uncertain/not_evaluated` или unreadable match → **needs_review**; иначе **excluded**. Не умножать видео на число matches. Сохранить все совпавшие category keys / result IDs.
6. Пересчитать eligibility для **конкретного video ID** в транзакции перед подготовкой; human override или revocation может заблокировать worker.
7. Кандидаты со статусом needs_review или excluded не получают downstream jobs; их количества аудируются в qdp_runs.

Граница: `SRE taxonomy_category_key` — это projection RTB. v1 не приписывает выдуманный `hypothesis_id` напрямую к квалифицированному видео.

## 3. Enrichment: что именно извлекается

Источник canonical identity — `videos.id`, `(platform,platform_video_id)`. Выбор метаданных идёт из `source_video_metadata` с той же policy version, active policy, разрешённым `can_store_metadata`, активным source и свежестью данных. Сортировка по `fetched_at DESC,id DESC` задаёт детерминированный выбор последнего разрешённого снимка.

| Данные | Источник | Когда сохраняются |
| --- | --- | --- |
| platform, video ID, URL, content_kind | `videos` | Только в разрешённом metadata snapshot |
| title, description, creator_name, duration, published_at, fetched_at | `source_video_metadata` | Active approved source policy, unexpired retention |
| views, likes, comments, shares, saves, followers, observation time, provider metric definition/version | `metric_snapshots` | Только source.can_store_metrics + policy permission, exact source/policy, good quality, time not assumed; до 20 последних |
| media_asset_id | `media_assets` | Только source.can_analyze_media + policy permission + не metadata_only + not expired |
| source_id, policy ID, metadata ID, metric IDs | все записи | Всегда в provenance для доказуемого происхождения |

**Не делаем:** новый API fetch, скрытый сбор метрик, download MP4, автоматическую ASR/OCR/LLM, догадки о `published_at`, заполнение неизвестных значений нулями, доступ по обходному пути при revoke.

Настройки `max_age_days` (null или 1..365) и `require_published_at` (bool) — выбор пользователя. При включённом ограничении ролик без доказанной даты получает `published_at_unknown`; публикация в будущем запрещена всегда.

`qdp_readable_enrichments` повторно проверяет права и expiry **на чтении**, включая media/metric permissions, policy и права конкретных снимков. `purge_revoked()` физически удаляет устаревшие snapshots и preparations, оставляя минимальный audit + blocked item state. Следующий downstream processor также должен проверять live source rights непосредственно перед чтением исходных данных и записью результатов.

## 4. Analysis Preparation — не анализ

На каждый Qualified + enriched video создаются три независимые записи `qdp_preparations`:

- **Group A:** ready только если есть разрешённый, неистёкший `media_asset_id`. Иначе `media_rights_or_asset_missing`.
- **Group B:** те же media rights. B не исполняется автоматически; для собственно анализа также нужны A / мультимодальные processors.
- **Group C:** требуется право на derive scores, минимум две совместимые metric snapshots одного source/definition и активная calculation configuration с установленными alpha и bayesian_k. Иначе `score_rights_missing`, `score_configuration_missing`, `metric_history_insufficient` или `metric_definition_incompatible`. Даже `ready` не гарантирует наличия creator/niche cohorts и всех priors; C scorer обязан проверить их отдельно.

Каждая запись содержит immutable `inputs` (только IDs, policies, версии) и `input_sha256`. A/B/C имеют **свои** контракты и бюджеты; QDP никогда не создаёт несанкционированную платную обработку. `partial` означает, что только часть групп готова; `blocked` — ни одна не готова.

## 5. PostgreSQL ownership, idempotency и recovery

Миграция: `intelligence/migrations/016_qualified_data_pipeline.sql`.

- `qdp_runs`: authoritative SRE run + taxonomy FK same project, pinned config/fingerprint, идемпотентный ключ, totals, статус, cancel.
- `qdp_items`: один canonical video в одном run, matched category keys/results, lifecycle.
- `qdp_enrichments`: immutable authorized metadata/metric/media snapshot with source/version IDs, expiry.
- `qdp_preparations`: immutable per-group readiness, input hash and blockers.
- `qdp_events`: проектный событийный журнал.
- `jobs`: используется **существующая** durable очередь, type=`qdp.prepare_video`, idempotency key `qdp:{run_id}:{video_id}`; отдельный worker, не ворует `discovery.import` jobs.

`create_run` в одной транзакции сериализует создание по `project_registry` row lock, повтор ключа возвращает прежний run, другие параметры под тем же ключом отклоняет. No HTTP in transaction.

Worker: claim row `FOR UPDATE SKIP LOCKED`, `lease_generation++`, `locked_by`, `lease_until`; в новой транзакции проверяет fencing и rights перед writes. Enrichment, 3 readiness records, item state и completed job записываются **атомарно**. Неудачная локальная операция допускает ограниченный retry (нет внешних side effects/оплат). Неразрешимый случай остаётся blocked/failed, не «успешным видео».

`qdp_runs.status`: queued → running → completed/partial/failed/cancelled. Отмена запрещает новые jobs. Успешно materialized snapshots остаются доступными только пока разрешены источником.

## 6. API / UI

Один localhost Origin/Host; POST обязательно `X-ContentMachine: 1`; используется существующий CSRF/check приложения. Нет multi-user remote authentication: не публиковать порт наружу.

| Метод | URL | Результат |
| --- | --- | --- |
| GET | `/api/projects/:pid/qualified-data/preview?sre_run_id=:uuid` | Только read-only totals, 0 платных вызовов |
| POST | `/api/projects/:pid/qualified-data/runs` | `{"sre_run_id":"uuid","idempotency_key":"12+ chars","config":{"max_age_days":null,"require_published_at":false}}` |
| GET | `/api/projects/:pid/qualified-data/runs` | История runs |
| GET | `/api/projects/:pid/qualified-data/runs/:uuid` | Материализованные records, группы A/B/C; rights read-gated |
| POST | `/api/projects/:pid/qualified-data/runs/:uuid/cancel` | `{}`, прекращение новых jobs |

В боковом меню новая вкладка **Qualified Data**: выбрать проект, ввести завершённый SRE Run UUID, предварительно проверить, создать бесплатный run и наблюдать результаты. Экран не делает SRE классификацию — до наличия SRE completed run запуск блокируется. До реализации SRE UI/run orchestration UUID берётся из БД/тестового сценария.

## 7. Локальная эксплуатация и tests

PostgreSQL 17, существующий `CM_DB_ENABLED=1` и `CM_DB_DSN`. `python -m intelligence.migrate` применит 016 в порядке версионированных миграций. Старт `app.py` автоматически запускает QDP background thread (отдельно от D1). Для диагностики можно запускать из корня:

```bash
python -m unittest tests.test_qualified_data_pipeline -v
# ONLY disposable PostgreSQL 17; fixture inserts/deletes data:
CM_TEST_PG_DSN="postgresql://...test_db..." python -m unittest tests.test_qualified_data_db -v
python -m intelligence.qualified_data.worker --once
node --check site/qualified-data.js
```

Проверки должны охватывать human veto, unapproved/run mismatch, project isolation, expired permissions, no source/no metrics, идемпотентность, read-time revoke, A/B/C blocked-by-default, lease fencing, отсутствие сетевых вызовов, невозможность приравнять `ready` к анализу.

**QA ограничение:** интеграционная проверка доступна только при наличии disposable DB и CI; она не подтверждает, что на машине владельца миграция уже применена. Worker не запускает и не оплачивает внешние источники. Для production 10k/month дополнительно нужны стресс-тесты, пагинация статусного UI и проверки удаления в заданные сроки.

## 8. Следующие модули, не часть QDP

Real SRE orchestrator/collector, отдельная регистрация внешних Enrichment Providers и политика расходов, Group A extractors (FFmpeg/ASR/OCR), Group B classifiers, Group C scorer/cohorts, Unified Analytics, Patterns/Insights/Production. QDP намеренно не симулирует их наличие.
