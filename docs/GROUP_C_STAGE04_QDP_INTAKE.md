# Group C — GC-MOD-04: транзакционный intake QDP C

**Ветка:** `feature/group-c-full-module`.  
**Этап:** GC-MOD-04 CODE IMPLEMENTED, live QDP producer/consumer E2E **BLOCKED_EXTERNAL**.  
**Реализация:** `intelligence/group_c/qdp_intake.py`.  
**Проверки:** `tests/test_group_c_qdp_intake.py`, отдельный PostgreSQL 17 job в `.github/workflows/group-c-module.yml`.

## Контракт входа и версия

- Внешний `QDPHandoffNotice` — строгий DTO QDP `qdp-handoff/v1`: `event_id`, `project_id`, `qdp_run_id`, `video_id`, `analysis_group='C'`, `input_sha256`, `schema_version`. `GroupCInputPointer` — **другой**, внутренний объект. Не копировать из notice отсутствующие source/snapshot IDs.
- QDP outbox-строка: `qdp_outbox.event_id` с `event_type='preparation_ready'`, `schema_version='qdp-event/v1'`, точными `project_id/run_id/video_id/group`, `payload_sha256`. Это **отдельная версия** от `qdp-handoff/v1`.
- Требуется **сам факт** существования события в QDP outbox. Нельзя принять только пользовательский JSON notice. QDP `payload` должен иметь `project_id,run_id,video_id,analysis_group,input_sha256` и точно соответствовать событию. Пересчитывается canonical SHA-256 JSON; объявленный SHA должен совпадать с сохранённым.
- Проверенная подготовка: `qdp_group_current_preparations` (текущий, а не исторический `qdp_preparations`), точные project/run/video/C + `state=ready`. `inputs.result_schema=qdp-preparation/v2`, точная SHA по `inputs`, pinned `sre_content_version_id/sha`, `sre_discovery_id`, `enrichment_source_id`, `source_policy_version_id`, `metadata_id`, `metric_history_sha256`, два snapshot ID по возрастанию, `metric_definition(name/version)`, `score_config_id`.
- Конкретный QDP `QDPCurrentPreparationAdapter.capability()` проверяет наличие нужных relations и колонок через текущую БД. При отсутствии/несовместимости возвращает `unavailable/incompatible`, **не** fixtures и не pseudo-ready.
- Ни `alpha`, ни `k`, ни `pL/pC/pS` не придумываются. Активная config ID необходима на входе, но **не** является одобрением человека и **не** разрешает полный C7 без cohorts/review в GC-MOD-09..12.

## Повторная проверка достоверности, внутри транзакции

1. PostgreSQL соединение обязательно `autocommit=True`; `receive` сам открывает **внешнюю** transaction, никогда не выдаёт ACK после одного лишь SAVEPOINT внутри незавершённой пользовательской транзакции.
2. `SELECT ... FROM qdp_outbox WHERE event_id=... FOR UPDATE` сериализует одновременные доставки. Нет строки — typed `dependency_unavailable`, ни одной локальной записи.
3. Если event ID уже принят, идентичная доставка обязана **заново** пройти QDP/current D1 validation. Иначе ACK не выдаётся после revocation. Изменённые SHA или identity сохраняются в отдельном `group_c_inbox_conflicts` без изменения исходного receipt; ответ `ack=false,reason_code=handoff_conflict`.
4. Новая доставка проверяет outbox/event SHA, current group C preparation, точные D1 source policy+expiry, metadata provenance+published_at, pair source/definition+version/observed_at и config reference. Никакой подмены latest provider/video/source и assumed observation timestamp.
5. В **одном** коммите: `group_c_inbox(state=accepted)`, `group_c_runs(state=queued, exact evidence IDs)`, `group_c_jobs(state=queued,job_kind=score)`. Все FK, unique hashes и triggers 056 применяются; при исключении вся транзакция откатывается.
6. **Только после** возврата из transaction `receive()` возвращает `ack=true,event_id,receipt_key=run_id,run_id,job_id,replayed`. External QDP dispatcher должен **после** этого записать `qdp_delivery_receipts` и состояние delivery своим отдельным механизмом. Consumer сам не меняет внешние QDP delivery rows.
7. `read_receipt()` позволяет QDP прочитать прежнее durable C confirmation без отправки score; receipt — факт intake, **не** complete C0–C7.

## Настоящий dependency blocker

Аудит актуальной QDP ветки (`6743f6ee9d63009579b728a155a6082f6d1e99d5`) подтвердил `qdp_group_current_preparations` в `042_qdp_group_current_preparations.sql` и contract `qdp-handoff/v1`; QDP пока **не имеет подтверждённой записи реальных `preparation_ready` в `qdp_outbox` в проверенном рабочем producer**. Поэтому `QDPGroupCConsumer` теперь реализован и fail-closed, но **LIVE delivery E2E невозможно считать завершённым** до выпуска QDP producer + действующего SRE/D1 интеграционного набора, версии event payload и настройки upstream delivery ACK.

Требуемый совместимый QDP outbox payload, который QDP producer/интегратор обязан проверить и согласовать до live интеграции:

```json
{
  "project_id": "lowercase-32-hex",
  "run_id": "canonical-qdp-uuid",
  "video_id": 123,
  "analysis_group": "C",
  "input_sha256": "64-character-lowercase-sha256"
}
```

Это **schema proposal** для QDP producer, не утверждение существующего события. Для будущей версии wire/producer необходим explicit handshake, не silent fallback к QDP fixtures.

## Security и scope

- Проект и video provenance берутся из **QDP outbox + current project-scoped QDP view**, а не входящего параметра самостоятельно.
- D1 facts читаются только exact ID. Убедиться в `source.status=enabled`, `can_store_metadata`, `can_store_metrics`, `can_derive_scores`, `source_policy_versions.is_active` + разрешениях этих трёх прав; проверяется сохранённый `fetched_at` и оба срока хранения.
- Две записи `metric_snapshots`: `views` integer, valid, exact, non-assumed, non-expired, same source/policy/view-definition/version; строго возрастающие times от verified publication, без отрицательного view correction.
- Конфликтный re-delivery не модифицирует immutable originals и не получает ACK; duplicate с тем же SHA не создаёт второй run/job.
- Не вызываются paid API, LLM, social platforms, FFmpeg, scoring or Analytics dispatch. `state=queued` не значит complete C7.
- Currentness нужно проверить **повторно в GC worker перед фактическим score и при чтении результатов**, включая new QDP invalidations, peer cohort rights и разрешения на чтение (GC-MOD-21/23/24).

## QA truth

- Pure negative tests: `tests/test_group_c_qdp_intake.py` (schema, hashes, groups, source/pair IDs, unavailable QDP, non-autocommit ACK guard).
- PG17 disposable tests (флаги `CM_TEST_PG_DSN`, `GC_TEST_DISPOSABLE_DB=YES`): accept + replay 1 receipt/run/job, conflict audit, revoked source, mismatched hash/project, retracted snapshot, absent real outbox event, revoked retry, racing 2 consumers.
- CI job `pg17-runtime` запускает обе серии на disposable **fresh** и **upgrade** PG17. Fixtures создают только в disposable DB имитацию QDP outbox/current-view, **никаких fake source metrics в production**. Синтетический тест не доказывает работоспособность настоящего upstream QDP producer.
- До подтверждения GitHub Actions на **точном текущем SHA** записывать: **CODE IMPLEMENTED; pure tests NOT VERIFIED; PG17 NOT VERIFIED/CI QUEUED; live QDP E2E BLOCKED_EXTERNAL**.
- Если CI выявит ошибку, исправить её на новой ревизии той же ветки и отдельно обновить тестовый статус; уже реализованный stage не скрывать/не считать QA PASS раньше факта.

## Следующий этап

**GC-MOD-05:** авторитетный D1 facts reader/registry и source-policy ports, project/creator/platform/content-kind/metadata lineage, все source facts без второго каталога публикаций; actual C0 read/validation — GC-MOD-06. QDP inbox из Stage04 можно использовать как вход, но C7 не выполнять до одобренных cohorts/config и точного Decimal engine.
