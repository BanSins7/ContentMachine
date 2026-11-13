# Group C — GC-MOD-05: источник фактов D1 / project-scoped provenance

**Branch:** `feature/group-c-full-module` (only branch written).  
**Code:** `intelligence/group_c/d1.py` (no SQL migrations).  
**QA:** `tests/test_group_c_d1.py`, CI `.github/workflows/group-c-module.yml`; PostgreSQL 17 checks in disposable fresh+upgrade databases.  
**Status:** CODE IMPLEMENTED; exact GitHub SHA CI currently queued/unchecked — PG17 PASS **не утверждается**. Нет живого source provider и end-to-end QDP producer.

## Главный принцип

- Источник идентичности публикации — только `videos.id` BIGINT. `video_id` нельзя вывести из URL, названия, платформенного ID или QDP event UUID.
- Канонические наблюдения — только `metric_snapshots.id`, `source_id`, `source_policy_version_id`, `source_metric_definition` и `source_metric_version`; `views` НЕ равно `reach`/impressions, запрещены подмены определения.
- Дата публикации — `source_video_metadata.id` на тот же `video_id` и `source_id`; поле `videos.published_at` и discovery timestamp не подменяют отсутствие verified `source_video_metadata.published_at`.
- `videos.content_kind` (legacy) и `videos.c_content_type` (C type) — **разные** поля. Если C-type пока NULL, возвращается NULL; `read_scoped_d1_bundle` не разрешит обработку без классификации. `slideshow_video` допустим с `c_content_type='video'`; `photo_carousel`/`mixed_carousel` — с `'carousel'`. Противоречие блокируется.
- Каноническая связь project-to-video — `sre_project_videos(project_id,video_id)`, проверенная совместно с `project_registry`; глобальное существование `videos.id` ещё не даёт права использовать его в чужом проекте.
- Отсутствующие creator/topic сохраняются `None`, не создаются фиктивные сущности. При указанном creator его `platform` должен совпадать с публикацией.

## Реализованные адаптеры и методы

| Port/класс | Методы | Инварианты |
|---|---|---|
| `d1` / `PostgreSQLD1FactsReader` | `capability`, `read_video(video_id)`, `read_snapshot(video_id,snapshot_id)`, `read_previous_snapshots(video_id,source_id,definition,version,before,limit)`, `read_publication(video_id,metadata_id)` | Все аргументы ID строгие положительные int (bool запрещён); snapshot и metadata читаются по точному ID+video, наблюдения не редактируются и не обнуляются; history <=64 сортируется по observed_at DESC,id DESC, строго по source+definition+version, без fallback |
| `registry` / `PostgreSQLCreatorTopicRegistry` | `capability`, `read_creator`, `read_topic`, `read_project_video` | Только явная project association; creator/video platform совпадает; никакого auto-topic assignment |
| `source_policy` / `PostgreSQLSourcePolicyReader` | `capability`, `read_effective_policy(source_id,policy_version_id)`, `assert_eligible(source_id,video_id,snapshot_ids,metadata_id,cohort_ids)` | Exact policy version, текущие права metadata/metrics/scores, active, source enabled, source/policy retention, metadata current/not deleted/not expired, все snapshots по тому же video/source/policy, quality='ok', precision='exact', observation_time_assumed=false, не просрочены |
| Компоновка | `local_d1_ports()`, `read_scoped_d1_bundle(conn,project_id,video_id,snapshot_ids,metadata_id,ports=None)` | Подключение к `GroupCPorts` только после runtime physical schema/capability probe; возврат `status=source_evidence_verified`, `score_approved=False`. Нет автоматического вызова `C7`, нет отложенных jobs или внешних запросов |

`GroupCPorts` остаётся fail-closed по умолчанию: все адаптеры нужно передать явно, кроме вызова `local_d1_ports()` в разрешённой D1-подсистеме, который всё равно проверяет фактические PostgreSQL таблицы/колонки. Неверная схема/отсутствующие колонки — `unavailable` либо `incompatible`, не healthy readiness.

### Example: самодостаточный источник, НЕ score engine

```python
from intelligence.group_c import local_d1_ports, read_scoped_d1_bundle

# conn — существующее, настроенное PostgreSQL connection.
ports = local_d1_ports()
evidence = read_scoped_d1_bundle(
    conn, project_id, video_id,
    snapshot_ids=(prior_snapshot_id, latest_snapshot_id),
    metadata_id=source_metadata_id,
    ports=ports,
)
assert evidence["status"] == "source_evidence_verified"
assert evidence["score_approved"] is False
```

Данные **не** нужно преобразовывать в новый `metrics_snapshots`: второго каталога Group C не создаёт. `snapshots` — отдельные исходные D1 dict, NULL значения (в т.ч. views и likes) остаются NULL. Счетчики, C1–C7 и сравнение peer-cohorts проходят отдельные проверки и этапы.

## Что намеренно ещё НЕ сделано

1. `GC-MOD-06`: полноценный C0 validator (mandatory counters / newer retracted or corrected observation, monotonically increasing time / definition and quality checks). В Stage05 reader **не скрывает** partial/retracted historical rows автоматически, чтобы Stage06 не взял фальшивую «последнюю хорошую» точку.
2. `GC-MOD-07..20`: Decimal scorer + review/frozen compatible cohorts + одобренная alpha/k/priors и запись immutable результата. Наличие source rights НЕ означает score approval.
3. `GC-MOD-09/10`: права, состав, одобрения и сопоставимость каждой peer cohort. Если `assert_eligible` получает непустой `cohort_ids`, он отклоняет вызов `cohort_approval_unavailable`, а не утверждает что peers проверены.
4. `GC-MOD-21/24`: перед score / при чтении результата заново проверять текущие rights/TTL/source retractions, чтобы исторический `complete` не стал правом на новые вычисления.
5. QDP producer + SRE/VC ветки не объединены, поэтому LIVE cross-branch E2E и provider accuracy не доказаны.

## Проверки и граничные случаи

`python -m unittest discover -s tests -p 'test_group_c_d1.py' -v` — D1 pure unit tests плюс disposable PG17, если `CM_TEST_PG_DSN` и `GC_TEST_DISPOSABLE_DB=YES`. Без обоих флагов БД-тесты пропускаются.

Проверяются missing table/column capability, wrong project, mismatched creator platform, separate `c_content_type`/legacy kind, missing exact observation/metadata, positive-ID rejects including bool, unknown-vs-zero raw counters, strict metric source/version/UTC time/limit, retention and source score permission revocation, retracted snapshot and deleted metadata. В PostgreSQL 17 CI используется только одноразовая synthetic fixture; её условный SUCCESS не доказывает реальные API наблюдения.

**Deployment:** Stage05 не добавляет миграций и не меняет D1. Текущие `003_performance_c.sql`, `004_performance_c_integrity.sql`, `056_group_c_runtime.sql` неизменны. Если после merged branches schema отличается, compatibility probe возвращает incompatible и требует явного contract revision, без автоматического fallback.

**Next:** `GC-MOD-06` — C0 strict read/validation of raw snapshots; список причин качества и времени, корректные NULL/zero, revisions of view definition, source expiry and observation timestamp. Только после C0 validation можно безопасно приступать к C1–C7.
