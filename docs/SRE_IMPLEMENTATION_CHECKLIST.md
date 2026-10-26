# SRE — основной Markdown-план разработки и журнал выполнения

> **Единая точка продолжения для любого нового чата.** Перед работой прочитай этот файл, [полный исходный FIX/атомарный план](SRE_STANDALONE_MODULE_IMPLEMENTATION_PLAN_RU.txt), [матрицу интеграции R01](SRE_INTEGRATION_COMPATIBILITY_MATRIX.md) и [трекер](SRE_IMPLEMENTATION_TRACKER_V1.md), затем сравни актуальные SHA веток и последние GitHub Actions. **Работай исключительно в `feature/sre-production-v1-20261023`, PR #22. Не меняй main и другие feature-ветки.**

**Обновлено:** 2026-10-26. **Источник этапов:** `SRE_STANDALONE_MODULE_IMPLEMENTATION_PLAN_RU.txt` (M01–M47), без изменения требований. **Mxx считается завершённым и зачёркивается только после прохождения всех его DoD/тестов на точном кодовом SHA.** Один запрос — один следующий этап; исключение: проверка/исправление CI незакрытого ранее этапа.

**Текущий статус:** M01–M12 — код этапов внесён; полный CI на последнем SHA не подтверждён. M12 — транзакционное LIVE-планирование из DB-authorized C9 + M08 D1, canonical SHA запроса и project-level serial idempotency с conflict на изменённых входах; элементы остаются not_evaluated до отдельного consent. **M12 реализация [x], QA [ ]; M01–M12 не объявлены QA DONE.** Следующий этап M13 только по новому запросу.

**Готовое наследство проекта:**
- [x] ~~S0–S4 — основные контракты, approved RTB, D1 intake, безопасный offline worker~~; подтверждение до M01: [SRE CI](https://github.com/BanSins7/ContentMachine/actions/runs/40071341191).
- [x] ~~R01 — аудит веток, миграций, PR и зависимостей~~: [матрица](SRE_INTEGRATION_COMPATIBILITY_MATRIX.md). R-серия — историческая, *не активный план*.
- [ ] **MODULAR_READY:** SRE автономен, latest-main rehearsal зеленый, можно merge по запросу владельца.
- [ ] **LIVE_VERIFIED:** отдельно разрешённый paid smoke на exact Opus.
- [ ] **ECOSYSTEM_CONNECTED:** реальная внешняя Collector/QDP интеграция (не блокирует автономный SRE merge).

## Блокер CI — обновлено по M12 2026-10-26

**M01–M12 QA OPEN:** на кодово-документальном SHA `d631957e38928e374bb9bdab814c163c7707fb55` GitHub Actions были `queued` c `conclusion=null`: [SRE M02–M12 40319238936](https://github.com/BanSins7/ContentMachine/actions/runs/40319238936), [Linux full 40319238972](https://github.com/BanSins7/ContentMachine/actions/runs/40319238972), [Windows 40319239155](https://github.com/BanSins7/ContentMachine/actions/runs/40319239155), [Knowledge B 40319239096](https://github.com/BanSins7/ContentMachine/actions/runs/40319239096). После коммита checklist проверить CI на точном обновлённом HEAD. Написанный тест ≠ успешно выполненный тест.

### Архив M11 CI

## Блокер CI — обновлено по M11 2026-10-26

**Все M01–M11 остаются QA-OPEN до фактических SUCCESS.** На кодовом SHA `22792d6f40d912a97d02e3a7f46fed3d95f6143e` GitHub Actions были в статусе `queued`, `conclusion=null`: [SRE M02–M11 40317254958](https://github.com/BanSins7/ContentMachine/actions/runs/40317254958), [Linux full 40317254896](https://github.com/BanSins7/ContentMachine/actions/runs/40317254896), [Windows D1 40317254852](https://github.com/BanSins7/ContentMachine/actions/runs/40317254852), [Knowledge B 40317254873](https://github.com/BanSins7/ContentMachine/actions/runs/40317254873). После последующего тестового/документального коммита проверить Actions на итоговом HEAD. Новый SQL не считался проверенным на реальной PG17 до окончания job.

### Архив очереди M10

## Блокер CI — обновлено по M10 2026-10-26

**M01–M10 не имеют полного подтверждённого PASS на точном M10 SHA.** На M10 код+документация SHA `1ece3bbaef85a4c6c9931713ae88408e99c694a9` GitHub Actions показал `queued`, `conclusion=null`: [SRE M02–M10 40314726975](https://github.com/BanSins7/ContentMachine/actions/runs/40314726975), [Linux full 40314726652](https://github.com/BanSins7/ContentMachine/actions/runs/40314726652), [Windows D1 40314726592](https://github.com/BanSins7/ContentMachine/actions/runs/40314726592), [Knowledge B 40314726803](https://github.com/BanSins7/ContentMachine/actions/runs/40314726803). Важно проверять **последний** HEAD после документационных коммитов. Реализация M10 [x], DoD/тестовая приёмка [ ]; не заявлять CI PASS.

### Архив M09 CI

## Блокер CI — обновлено по M09 2026-10-26

**M01–M09 не имеют полного подтверждённого PASS на актуальном SHA.** На M09 код+документация HEAD `444ea59c9b041815ce1664cfc145a8427f915892` проверки были в очереди: [SRE-focused 40312175160](https://github.com/BanSins7/ContentMachine/actions/runs/40312175160), [Linux full 40312175229](https://github.com/BanSins7/ContentMachine/actions/runs/40312175229), [Windows D1 40312175155](https://github.com/BanSins7/ContentMachine/actions/runs/40312175155), [Knowledge B 40312175153](https://github.com/BanSins7/ContentMachine/actions/runs/40312175153). `status=queued`, `conclusion=null`. M09 реализация [x]; точная test acceptance [ ]. После документационных коммитов проверять CI на последнем HEAD заново.

### Архив предыдущей очереди (M08)

## Блокер CI — обновлено по M08 2026-10-26

**M01–M08 не имеют полного PASS на актуальном SHA.** На кодово-документальном M08 SHA `de35aefd61fa93f08dfdea24c1c54d0b8952bfe4` GitHub Actions показывали `queued`: [SRE 40309646556](https://github.com/BanSins7/ContentMachine/actions/runs/40309646556), [Linux full 40309646654](https://github.com/BanSins7/ContentMachine/actions/runs/40309646654), [Windows D1 40309646550](https://github.com/BanSins7/ContentMachine/actions/runs/40309646550), [Knowledge B 40309646546](https://github.com/BanSins7/ContentMachine/actions/runs/40309646546). Отсутствие запусков с `conclusion=success` нельзя выдавать за пройденный тест. Следующий этап M09 возможен отдельно, но QA и очередь нужно перепроверять.

### Архив проверки M07

## Блокер CI — обновлено по M07 2026-10-26

**Все M01–M07 пока не имеют полного QA PASS на точном SHA.** На кодово-документальном HEAD `dc95ab2535223aa14d67138af52f6f4949974bcd` GitHub Actions в очереди: [SRE-focused 40306458333](https://github.com/BanSins7/ContentMachine/actions/runs/40306458333), [Linux full 40306458424](https://github.com/BanSins7/ContentMachine/actions/runs/40306458424), [Windows D1 40306458312](https://github.com/BanSins7/ContentMachine/actions/runs/40306458312), [Knowledge B Stage14 40306458496](https://github.com/BanSins7/ContentMachine/actions/runs/40306458496), все `queued`, `conclusion=null` на момент просмотра. M07: код [x], unit/PG тесты [x] внесены, фактический результат тестов пока **не подтверждён**. Чистая ветка от актуального `main`, отставание 0; PR22 по-прежнему Draft.

### Архив прежнего CI-блокера (M06 и ниже)

**Все M01–M06 на старом кодовом SHA также не получили полный QA PASS.** Реализация каждого этапа отмечена отдельно от зелёного DoD. Последний проверенный HEAD по M06: `e3529975971e1dbe40d2bc7d015d445696307263`. GitHub Actions по этому HEAD: [SRE targeted 40303995170](https://github.com/BanSins7/ContentMachine/actions/runs/40303995170), [Linux full 40303995358](https://github.com/BanSins7/ContentMachine/actions/runs/40303995358), [Windows 40303995260](https://github.com/BanSins7/ContentMachine/actions/runs/40303995260), [Knowledge B 40303995272](https://github.com/BanSins7/ContentMachine/actions/runs/40303995272) — `queued`, conclusion=null на момент проверки. Не путать `queued` с `failed` или `passed`.

**Историческая диагностика от 10:10–10:20:** GitHub Actions API показал общую очередь **567 `queued` и 15 `in_progress`** в репозитории. На ветке SRE последние обязательные запуски для M04 имеют статус `queued`, jobs `runner_name=""`, `conclusion=null`: [targeted 40297270236](https://github.com/BanSins7/ContentMachine/actions/runs/40297270236), [Linux 40297270317](https://github.com/BanSins7/ContentMachine/actions/runs/40297270317), [Windows 40297270252](https://github.com/BanSins7/ContentMachine/actions/runs/40297270252). **Эта очередь — блокер внешней CI-инфраструктуры**, а не успешный тестовый результат. Не создавать новые фиктивные или дублирующие workflow для имитации зелёного CI.

Ранее M01 на merge SHA `09a8c7542abafa7c856e3555581a70ea52b87796`: [Linux full](https://github.com/BanSins7/ContentMachine/actions/runs/40289331759) закончил **1733 Python unittest OK**, но сам workflow `failure` из-за ошибочного PSI Stage14 `legacy_unchanged()`, который принимал новые миграции SRE 016/017 за модификацию старых. [Windows full](https://github.com/BanSins7/ContentMachine/actions/runs/40289331770) упал на том же PSI gate. [SRE targeted Linux/Windows/PG17](https://github.com/BanSins7/ContentMachine/actions/runs/40289331842) — `SUCCESS`, [B Stage14](https://github.com/BanSins7/ContentMachine/actions/runs/40289331751) — `SUCCESS`. В текущей SRE ветке [`scripts/psi_release_gate.py`](../scripts/psi_release_gate.py) исправлен через Git `--diff-filter=DMRT --name-status --find-renames` с новым [тестом](../tests/test_sre_psi_gate_compat.py). Локальная независимая проверка на временном Git-репозитории показала `git diff` пустым для двух добавленных SQL и строку `M\t.../010_original.sql` для изменения старого SQL. **Исправленный исходный код ещё не проходил полный GitHub CI.**

Перед зачёркиванием проверить **конкретно на проверяемом code SHA**: статус targeted Linux, Windows, PG17; полный Linux/Windows + PSI Stage14 и Knowledge B; совпадение SHA кода и отсутствие непроверенных коммитов в runtime/CI. Если всё зелёное — поменять M01–M06 по одному с реальными ссылками; иначе исправлять фактические failures, а не убирать тесты. При очереди >500 возможно потребуется освободить GitHub Actions runners, отменив **только устаревшие промежуточные прогоны** через GitHub UI; текущие проверки и чужие работы не отменять без решения владельца.

## Правила фиксации результата

После каждого этапа **в том же коммите либо следующем документальном коммите**:
1. Убедись, что тесты действительно завершились успешно (или отмечай `CI PENDING`/`BLOCKED`).
2. Меняй соответствующую строку ниже с `- [ ] **Mxx**` на `- [x] ~~Mxx — Название~~` **только при реальном DONE**.
3. Добавляй в журнал внизу номер этапа, полный SHA кода, реальные GitHub Actions ссылки, все обнаруженные ограничения и то, что не делалось.
4. Обновляй `docs/SRE_IMPLEMENTATION_TRACKER_V1.md` и заголовок draft PR #22. **Без автоматического перехода к следующему Mxx, платных POST или слияния в main**.
5. Если исторические/новые CI стоят в очереди, не зачёркивай и не заявляй `DONE`, даже если код готов.

## Полный список M01–M47


### Фаза A — МОДУЛЬНЫЙ КАРКАС И БАЗОВЫЙ MAIN

- [ ] **M01 — Безопасная синхронизация upstream main ВНУТРЬ ветки SRE** *(merge сделан; исходный полный CI упал на ошибочном PSI release gate, поправка в новом коде — CI ожидается)*
  > Выполнить: проверить main/PR/sha; совместить в ветке SRE уже merged A/B,
  > Query Validation и текущие app.py/CI без перезаписи S0–S4. Если merge
  > требует много конфликтов — разбить на reviewable commits В ЭТОМ M01,
  > а не заявлять DONE до green CI.
  > DoD: ветка SRE содержит актуальную базу main, diff относительно main
  > остаётся SRE-only; полный Linux/Windows/PG17 CI на точном SHA green.
  > Запрещено: изменять main, брать незамерженные PR20/24/23 целиком.
  > **Фактический статус на дату документа:** Код слияния `09a8c7542abafa7c856e3555581a70ea52b87796`; целевой SRE CI [PASS](https://github.com/BanSins7/ContentMachine/actions/runs/40289331842); полный Linux/Windows/B14 CI ещё не завершён.

- [ ] **M02 — Публичный module manifest / feature flags / порты v1** *(код внесён; CI ожидается)*
  > Выполнить: strict contracts для capabilities, installed/enabled/readiness;
  > input C9 approved, D1 content, optional VC event; output SRE LiveResult,
  > optional QDP event. Typed IDs (C source node, UUID5 category, D1 BIGINT),
  > schema_version, tenant, rights, expiry, sources.
  > DoD: модуль импортируется без VC/QDP; ошибочные UUID/BIGINT/schema/version
  > отвергаются; 0 доступа к провайдерам при импорте. Контрактные fixtures.
  > FIX:00,01,03..06,16. QA:001,004,011,013..016,075.
  > **Фактический статус на дату документа:** Код `module.py`, `ports/*`, 15 unit tests, реальные PG S2/S3 assertions; точный CI [ожидает](https://github.com/BanSins7/ContentMachine/actions/runs/40292151245).

- [ ] **M03 — Автономная конфигурация и optional dependency isolation** *(код внесён; CI ожидается)*
  > Выполнить: namespace CM_SRE_* для config/model/worker/limits; default
  > disabled, safe secrets/log redaction, optional embeddings/OpenRouter loader;
  > health: available / blocked_config / blocked_dependency / blocked_policy.
  > DoD: чистый main + SRE устанавливается без сторонних feature-веток и без
  > ключа OpenRouter; disabled startup = 0 SQL mutating/paid calls.
  > FIX:00,15,18. QA:081,089,090,092.
  > **Фактический статус на дату документа:** Код `config.py`, `health.py`, `test_sre_runtime_config.py`; целевой и полный CI должны подтвердить завершение.

- [ ] **M04 — Минимальная точка подключения к локальному приложению** *(код HTTP/Studio/CI внесён; проверка ожидается)*
  > Выполнить: изолированный SRE API dispatch adapter, 1 небольшой mount в
  > app.py и отдельный статический UI entry; no side effects on startup.
  > Не реализовывать весь API здесь — только health/discoverability и 404/503.
  > DoD: SRE включается флагом; существующие D1/A/B endpoints не меняются;
  > GET-импорт и обычный старт не тратят токены; host smoke Windows/Linux.
  > FIX:15. QA:081,082,089.

- [ ] **M05 — Migration compatibility + forward-only SRE upgrade baseline** *(код preflight, harness и CI внесён; PASS ожидается, см. [M05 doc](SRE_M05_MIGRATION_COMPATIBILITY.md))*
  > Выполнить: миграционный manifest SRE, checksum policy, inspect union
  > 016/017 SRE+QV+main 030..053; preflight хешей на existing DB; отдельный
  > upgrade dry-run и fresh PostgreSQL17 compatibility harness.
  > Не переписывать 016/017; выбрать номер для будущих SRE SQL по актуальному
  > списку, а не по устаревшему FIX.
  > DoD: fresh+upgrade+reapply no-op; unknown applied checksum => BLOCKED с
  > actionable diagnostic; main CI unaffected.
  > FIX:02,18. QA:071..074.


### Фаза B — РЕАЛЬНЫЕ ВХОДЫ C9 И DISCOVERY D1 (НИКАКИХ ЧУЖИХ ВЕТОК)

- [ ] **M06 — C9 approved publication port, verified v1 only** *(CODE COMMITTED; полный QA PENDING, см. [M06 документацию](SRE_M06_C9_APPROVED_PORT.md))*
  - [x] ~~Реализация: DB-verified C9 v1 port, H1/H2, all-leaf UUID5 и ancestry, 11-полевые контексты, негативные и PG17 тесты, workflow.~~
  - [ ] Тестовая приёмка: SRE-targeted + Linux/Windows/PG17 и полный регрессионный CI на точном кодовом SHA — **не PASS, текущие запуски queued**.
  > Выполнить: обернуть текущий S2, проверять H1/H2, UUID5 leaf/ancestor,
  > 11 полей и approved source facts, version/hash; no mutable pointer.
  > DoD: tampered/draft/foreign taxonomy отвергаются, all leaves preserved.
  > FIX:05,17(v1 part). QA:001..010.

- [ ] **M07 — D1 canonical video provenance reader** *(реализация [x], полный CI [ ]; [M07 документ](SRE_M07_D1_PROVENANCE.md))*
  - [x] ~~Реализован единый DB-verified проектный D1 reader: discovery/source_request/query/run/observation/video/policy, true BIGINT, direct D1, idempotent content version replay.~~
  - [x] ~~Внесены unit-негативные и PostgreSQL17 тесты, настроен SRE Linux/Windows/PG17 workflow.~~
  - [ ] Проверка приёмки на точном SHA: SRE-focused + full Linux/Windows/PG17 + Knowledge B CI PASS — до завершения Actions считать **CI PENDING**.
  > Выполнить: source-specific D1 source_request/discovery/video/policy IDs,
  > exact project scope; не путать Collector UUID query с BIGINT legacy FK.
  > Входы могут быть напрямую D1 даже БЕЗ Collector.
  > DoD: genuine project ownership, correct source lineage, duplicate D1
  > references idempotent, no global-title fallback.
  > FIX:03,06. QA:011..018.

- [ ] **M08 — Rights/expiry/content versions/live read gate** *(код [x], полный CI [ ]; [M08 документ](SRE_M08_LIVE_CONTENT_GATE.md))*
  - [x] ~~M08 код: LIVE D1/S3 source-rights/retention/observation, pinned revision/SHA, metadata_only vs partial/full without fabricated media, outbound external grant, offline plan/worker/result read-time gate.~~
  - [x] ~~Внесены pure негативные, real PostgreSQL17 expiry/source revocation, late-worker mismatch тесты и Linux/Windows CI wiring.~~
  - [ ] Приёмка: SRE, Linux, Windows, PostgreSQL17, full regression и Quality B на точном SHA с **фактическим SUCCESS**.
  > Выполнить: укрепить S3 source review, metadata_only/partial/full mapping,
  > no invented transcript/scenes, content_SHA, D1 observation-specific expiry,
  > source policy recheck at send/publish; dynamic revocation.
  > DoD: revoke/delete/expiry denies outgoing data and late worker, no implicit
  > permission for OpenRouter merely from storing metadata.
  > FIX:06,18. QA:015..020,052..056.


### Фаза C — ПОЛНОЦЕННЫЙ LIVE RUN ENGINE

- [ ] **M09 — Dataset preview без сайд-эффектов** *(код [x], QA [ ]; [M09 описание](SRE_M09_DATASET_PREVIEW.md))*
  - [x] ~~Read-only M09: exact approved RTB C9 H1/H2, all leaf contexts, SRE-owned D1 content versions, deterministic one-version-per-video dedup (reported discarded refs), revoked/expired/invalid all-pair blocked statuses, no fake result.~~
  - [x] ~~Implemented 100 × 20 = 2000 complete pairs and negative/idempotency/tenant unit tests, PostgreSQL17 actual C9 + D1 revision/rights tests, Linux/Windows+PG workflow.~~
  - [ ] Verify exact SHA SRE-focused + full Linux/Windows + PostgreSQL17 + related regression CI **PASS**; queued != passed.
  > Выполнить: explicit D1 version selection, video_id dedup, every approved
  > hypothesis leaf, blocked vs eligible pairs, counts, null-data warnings.
  > DoD: 100*20=2000 pairs, no hidden skip; 0 writes/no paid calls.
  > FIX:07. QA:021..025,081,111..112.

- [ ] **M10 — Typed LIVE FSM/DTO and technical outcome taxonomy** *(код [x], полный CI [ ]; [M10 документ](SRE_M10_LIVE_FSM.md))*
  - [x] ~~Отдельный LIVE-only контракт run/item/batch/attempt, закрытые FSM-графы, strict UUID/SHA/BIGINT/NaN/boolean/extra-key валидаторы; C9 leaf coverage, partial/unknown/terminal states, immutable outcome groups.~~
  - [x] ~~Внесены техническая таксономия и запрет автоматического повторного платного POST при ambiguous send; переход из unknown только после trusted reconciliation; negative Linux/Windows unit suite и workflow.~~
  - [ ] Test acceptance: GitHub Actions SRE/Linux/Windows/PG17 и полный regression на точном SHA должны реально завершиться SUCCESS (currently QUEUED).
  > Выполнить: states for run/item/batch/dispatch attempts, pending consent,
  > waiting_source, paused_budget/rights, unknown, partial, cancelled;
  > strict boundary offline_fixture != live.
  > DoD: transition/schema/NaN/UUID/bool-as-int negative tests; 0 HTTP.
  > FIX:01,07,13. QA:021,024..030,070.

- [ ] **M11 — Core LIVE PostgreSQL tables and immutable pins** *(реализация [x], полный QA [ ]; [M11 runbook](SRE_M11_LIVE_POSTGRESQL.md))*
  - [x] ~~Новая forward-only миграция `054_sre_live_core.sql`: LIVE runs/items/batches, immutable pinned project/C9/D1 source-policy/content-version/SHA, JSON manifests, strict states, immutable origin triggers, monotone lease_generation, no PUBLIC grants. Исторические SRE 016/017 не изменялись.~~
  - [x] ~~Тестовые контракты `test_sre_live_schema.py`, реальный PG17 C9+D1 tenant/FK/lease/tamper в `test_sre_offline_pg.py`, fresh/upgrade/reapply/checksum PG17 в `test_sre_migration_guard_pg.py`, unit manifest guard и workflow Linux/Windows/PG17.~~
  - [ ] Acceptance: точный кодовый SHA должен иметь настоящие SRE-targeted, full Linux, Windows, PG17, PSI/B14 PASS; GitHub Actions пока queued.
  > Выполнить: additive SRE live_run/item/batch tables + composite project FK,
  > content/taxonomy/source/pipeline SHA, lease_generation, typed status,
  > immutable manifest and origin metadata; NO finance here.
  > DoD: new DB, DB upgraded from S4 and permissions, tenant/FK/tamper tests.
  > FIX:02,07. QA:026..030,071..074.

- [ ] **M12 — LIVE run planning and idempotent enqueue** *(реализация [x], тестовая приёмка [ ]; [M12 runbook](SRE_M12_LIVE_PLANNER.md))*
  - [x] ~~`live_planner.py`: pinned approved C9 H1/H2/leaf contexts, project-owned D1 exact versions/SHA/rights, canonical selected+deduplicated version manifest, validated SRE pipeline SHA, transactional row-lock `(project,idempotency_key)` replay and `idempotency_conflict` for changed inputs.~~
  - [x] ~~Сохраняются только `sre_live_runs.state=pending_consent` + `sre_live_items.state=not_evaluated`; никаких батчей/платных отправок/результатов. Негативные/fake unit QA, PostgreSQL17 C9+D1 real permission/revoke/duplicate/concurrent-lock тесты, Linux/Windows/PG17 CI.~~
  - [ ] Приёмка: реальные SRE-targeted + full Linux/Windows/PG17/PSI/B14 jobs должны иметь SUCCESS на точном SHA; `queued` не PASS.
  > Выполнить: pin C9/D1/project and exact desired classifications,
  > stable request_digest + (project,idempotency key), per-video work.
  > DoD: same key/content -> same run; changed inputs -> 409; no paid POST.
  > FIX:07. QA:021..025,104.

- [ ] **M13 — Exact batch manifest + coverage accounting**
  > Выполнить: deterministic expected leaf keys, group videos, exact input
  > digest, no label omitted silently; explicit not_evaluated/blocked.
  > DoD: worker cannot mark completed on partial N/M output.
  > FIX:07,11,13. QA:029,041..050,111.

- [ ] **M14 — Claim/lease/heartbeat and fencing for LIVE items**
  > Выполнить: SKIP LOCKED, deterministic lock order, durable token/gen/expiry;
  > concurrent DB workers, no paid calls.
  > DoD: only one owner per item, stale token rejected; Linux+PG17 race tests.
  > FIX:07,18. QA:026..028,091,098.

- [ ] **M15 — Cancel/pause/restart and safe recovery before dispatch**
  > Выполнить: terminal state control, expiry/restart/fail-stop; distinguish
  > before vs after dispatch_intent. НИКОГДА автоматически не повторять
  > unknown paid attempt после POST.
  > DoD: crash/restart/cancel preserves database and budget invariants.
  > FIX:07,09,12,18. QA:027..030,069,093..100.


### Фаза D — ЛОКАЛЬНЫЙ RETRIEVAL И ВХОД OPUS

- [ ] **M16 — Локальная embedding-модель и воспроизводимый engine**
  > Выполнить: real local semantic embedding backend, pinned model name,
  > revision/checksum, deterministic inference and health/preflight;
  > no cloud embeddings; fast unit fixture separate from actual model.
  > DoD: real model inference in controlled test environment or BLOCKED;
  > tests no silent model substitute.
  > FIX:10. QA:031..035.

- [ ] **M17 — ALL-LEAF similarity ranking, исключения отдельно**
  > Выполнить: rank positive meaning vs exclusion, full tree directions,
  > stable tie-break and all hypothesis keys, no threshold filtering.
  > DoD: number of output candidates = approved leaves, including low scores.
  > FIX:10. QA:031..040,111.

- [ ] **M18 — Embedding cache/pinning/revocation**
  > Выполнить: project/model/taxonomy/content hashes in cache keys, dedup,
  > bounded storage, rights expiry and deletion; no cross-tenant leak.
  > DoD: restart reproducible, revoked content embeddings unavailable.
  > FIX:10,18. QA:034,036..040,057,117.

- [ ] **M19 — Complete C9 classifier context and prompt injection isolation**
  > Выполнить: exact hypothesis direction/branch/rules/includes/excludes/facts
  > and siblings, untrusted D1 content as DATA, multilingual/metadata_only
  > semantics; no search-query label shortcut.
  > DoD: full 11 C fields, quote source refs, no invented facts/fields.
  > FIX:05,11. QA:041..046,051..060.

- [ ] **M20 — Deterministic Opus request/batch packer**
  > Выполнить: bind model+catalog revision, exact contexts and content chunks,
  > token count bounds and overflow split with unchanged expected leaf coverage;
  > every request hash reproducible.
  > DoD: batch manifest deterministic, no lost keys on split/long data.
  > FIX:11. QA:041..050,111..116.

- [ ] **M21 — Strict output/evidence verifier**
  > Выполнить: parse exact enum match/no_match/uncertain; evidence exact
  > content span/version; expected category UUID set; no invented timestamps,
  > not_evaluated ONLY system; errors technical not semantic no_match.
  > DoD: adversarial JSON/quotes/extra keys/partial output all fail closed.
  > FIX:11,12,13. QA:047..060,112..120.


### Фаза E — COST, СОГЛАСИЕ И ONLY-OPUS LIVE EXECUTION

- [ ] **M22 — OpenRouter exact Opus 5.5 catalog preflight**
  > Выполнить: optional catalog fetch/cached snapshot/model capabilities &
  > prices at time; exact anthropic/claude-opus-5.5, no fallback.
  > DoD: unavailable model, missing JSON support or stale catalog blocks quote.
  > FIX:08,11. QA:061,062,066.

- [ ] **M23 — Final immutable quote ПОСЛЕ exact batch packing**
  > Выполнить: Decimal price/tokens/reserve upper bound on exact batch digests,
  > expiry, explicit minimum/maximum and catalog pinned hash.
  > DoD: quote != actual manifest => no start; GET quote 0 provider POST.
  > FIX:08,09. QA:061..067.

- [ ] **M24 — Dedicated SRE consent grant and revoke**
  > Выполнить: project/actor-authorized consent bound to quote_hash,
  > USD cap, expiry and explicit stage. Do not reuse C/Query Planner consents.
  > DoD: wrong/foreign/expired/revoked consent denied; no implicit yes.
  > FIX:09. QA:062..068,082..088.

- [ ] **M25 — Decimal budget ledger and atomic reserve**
  > Выполнить: reservations, spent, released, item/batch upper bound, budget
  > comparison with PostgreSQL row-lock and DB constraints.
  > DoD: concurrent reserve cannot exceed cap; negative/float/overspend rejected.
  > FIX:09. QA:063..070,095,100.

- [ ] **M26 — Committed dispatch intent BEFORE every provider POST**
  > Выполнить: request_sha/attempt identity, amount reserved and durable
  > provider dispatch intent committed; provider HTTP happens only AFTER commit,
  > never holding business transaction open.
  > DoD: timeout/restart after dispatch => uncertain and reserved retained;
  > no blind retry, idempotent reconciliation.
  > FIX:09,12,18. QA:068..070,091..100.

- [ ] **M27 — Only OpenRouter Opus transport adapter + fake transport**
  > Выполнить: strict HTTPS transport, exact model id, headers/secrets policy,
  > request timeout, cancel awareness, cap and telemetry; injected fake supports
  > end-to-end zero-cost QA, no alternative provider.
  > DoD: fake never opens network; live transport available only with consent
  > + intent; missing exact model => BLOCKED, no fallback.
  > FIX:11,12. QA:043..050,061..070.

- [ ] **M28 — Provider response validation and atomic cost/result receipts**
  > Выполнить: response id, usage tokens, actual Decimal charge from receipt,
  > structured labels+evidence check, journal append, stage receipts and hashes.
  > DoD: bad output no match written, completion row & settlement atomic,
  > all cost remains within authorized ledger rules.
  > FIX:12,13. QA:047..060,064..070.

- [ ] **M29 — Unknown outcome reconcile / no double charge**
  > Выполнить: timeout/unknown attempt manual or verified provider-status
  > reconciliation; immutable receipts, never guess free failure or rePOST.
  > DoD: crash-after-POST tests, consent cancellation and unknown balance
  > accounted with no second paid dispatch.
  > FIX:09,12,18. QA:068..070,093..100.

- [ ] **M30 — LIVE coverage completion gate + partial status**
  > Выполнить: must account for every planned (video,hypothesis), unique
  > decision/evidence, approved pinned publication and rights freshness;
  > only then run completed, else partial/blocked/failed.
  > DoD: 1/3 batches cannot become completed; all statuses truthful.
  > FIX:07,12,13. QA:029,047,075,104,111.

- [ ] **M31 — Run observability, immutable audit and zero-cost diagnostics**
  > Выполнить: receipt/time/model/prompt hashes, counts, retries and rights,
  > no raw keys/excess D1 content in logs; counters and alerts.
  > DoD: operator can explain exact cost and state, trace one video/hypothesis.
  > FIX:09,13,18. QA:064,069,070,090,095..100.


### Фаза F — КАЧЕСТВО, RETENTION И ВХОД/ВЫХОД МОДУЛЯ

- [ ] **M32 — SRE Human Review, immutable correction and supersession**
  > Выполнить: review queue, verified human label, original model verdict
  > preserved, exact source quote references; reject nonproject/edit history.
  > DoD: effective label deterministic, no invisible overwrite.
  > FIX:14. QA:051..060,103..106.

- [ ] **M33 — Gold benchmark, calibration и experiment comparison**
  > Выполнить: reproducible small stratified gold set, quality checks,
  > precision/recall/F1 with explicit denominator and model/prompt version.
  > DoD: no synthetic accuracy claims; null/uncertain case measured.
  > FIX:14. QA:037..040,057..060,113..118.

- [ ] **M34 — Full retention and revocation purge**
  > Выполнить: source-rights callback, hide immediately, cancel pending,
  > physically purge permitted content-derived embeddings, prompts, evidence,
  > outputs, caches; tombstone+outbox invalidation where required.
  > DoD: no post-revoke text leak via API, export, analytics or logs;
  > recover after mid-purge crash; DB retention tests.
  > FIX:06,18. QA:052..058,094,117..120.

- [ ] **M35 — Optional Collector inbox WITHOUT Collector dependency**
  > Выполнить: schema version `sre.discovery_ready/v1`, verify project +
  > approved C + real D1 discovery/source_request/provenance/policy; idempotent
  > inbox ACK, quarantine, backfill from existing D1 records and replay.
  > No code in Collector branch. D1 direct import remains first-class.
  > DoD: 0 Collector installations needed to run SRE; if compatible event
  > exists, commit to D1/SRE once; never trust UUID-as-BIGINT query.
  > FIX:03,04. QA:011..020,079,101,105.

- [ ] **M36 — SRE LIVE-ONLY result outbox and QDP contract kit**
  > Выполнить: versioned immutable `sre.classifications_ready/v1` and
  > `sre.results_invalidated/v1`; emitted only after true LIVE completed,
  > review/rights validated, outbox with lease/ACK/retry and checksum;
  > consumer reference code/fixtures and live-only SQL read view.
  > DoD: offline_fixture/legacy cannot emit ready; duplicate delivery harmless;
  > no QDP module imports required for SRE startup.
  > FIX:16. QA:075..080,104,119,120.

- [ ] **M37 — Cross-branch contract verification, no changes to other branches**
  > Выполнить: SRE-owned tests against exact available Collector/QDP source
  > refs via fixture/mirror, verify query ID bridge and QDP LIVE-only gate
  > requirements; publish integration guide/patch proposal for module owners.
  > If external branch lacks required gate, mark only ECOSYSTEM_CONNECTED
  > BLOCKED, NOT autonomous SRE merge-ready.
  > DoD: typed compatibility report with SHA, failing scenario, owning team,
  > adapter alternative; no cherry-pick, no fabricated integrated E2E.
  > FIX:03,04,16. QA:075..080,101..110.


### Фаза G — ПОЛЬЗОВАТЕЛЬСКИЙ И ОПЕРАЦИОННЫЙ ИНТЕРФЕЙС

- [ ] **M38 — Read-only localhost SRE API**
  > Выполнить: `/api/sre/v1/` manifest/health, taxonomy, content previews,
  > quotes preview, run status, results, history, review queue; pagination,
  > project authorization tied to trusted host identity.
  > DoD: GET cannot mutate, call paid provider or disclose revoked quotes.
  > FIX:15. QA:081,083,084,089,090.

- [ ] **M39 — Mutation API + explicit owner/consent gates**
  > Выполнить: attach selected D1, create quote/consent/run, cancel, review,
  > reconcile; same-origin/CSRF/actor checks and true idempotency;
  > no authorization solely from a caller-provided project_id/header.
  > DoD: unauthorized/foreign/forged/flood/expired requests blocked;
  > no spending without persisted authorized quote.
  > FIX:15. QA:082,084,086..088.

- [ ] **M40 — Self-contained SRE Studio frontend**
  > Выполнить: site/sre-studio.js/css UI for actual C/D1 selection,
  > rank-all scores, exact Opus config, preview, quote, consent, progress,
  > evidence/results, human review and exports; no fast model selector;
  > disabled state and source rights visible.
  > DoD: UI uses only SRE API, Node22 interactive behavior tests; no direct
  > SQL/other-branch imports or external model call on page load.
  > FIX:15. QA:081..090.

- [ ] **M41 — Independent install/start/stop/worker CLI and diagnostics**
  > Выполнить: `python -m intelligence.relevance.cli` commands self-check,
  > preview, worker-once, worker-loop, stop, migration diagnosis; supported
  > env flag, no paid automatic start; fresh installation instructions.
  > DoD: SRE works without VC/QDP/Analytics installed; daemon restart safe;
  > disabled by default and no network on passive boot.
  > FIX:07,15,18. QA:025..030,089..100.


### Фаза H — ЧЕСТНЫЕ GATES ДЛЯ ОДНОГО MERGE

- [ ] **M42 — Autonomous SRE full E2E in latest-main-compatible PostgreSQL**
  > Выполнить: real approved two-human-review C9 publication + real D1
  > PostgreSQL source video/rights + local rank-all + FAKE Opus HTTP adapter
  > + quote/consent/reserve/dispatch intent/receipt simulation + persisted
  > TEST-ONLY simulated-live result + human review + SRE outbox in disposable
  > database (NO paid network). Test transport MUST require test-only runtime
  > flag and create explicit synthetic/test provenance; it MUST be IMPOSSIBLE to
  > enable in production or leak its results through live-only consumer/export.
  > Это полноценный SRE fixture E2E, а не внешний VC/QDP E2E.
  > DoD: full data path, multi-label, cost records/coverage, rights/purge,
  > offline_fixture excluded, repeatable idempotent replay.
  > FIX:00..16. QA:001..070,075..080,101,104,111.

- [ ] **M43 — Fresh/upgrade migrations + main merge rehearsal + CI**
  > Выполнить: on exact current main commit TEST combining SRE diff with main
  > in disposable GitHub CI context; verify clean PG17 and upgraded DB,
  > schema checksums, Python3.12 Windows/Linux, Node22 UI, no missing imports,
  > same-origin API and legacy host smoke. Main itself untouched.
  > DoD: simulated PR merge clean or explicit conflicts fixed IN SRE branch,
  > no assumed compatibility merely from mergeable=true; test SHA pinned.
  > FIX:00,02,18,19. QA:071..074,081..090.

- [ ] **M44 — Chaos / concurrency / restart / actual load limits**
  > Выполнить: concurrent claims, lease death, ambiguous paid outcome,
  > redacted logs, revoked source race, backup/restore and outbox ACK retry;
  > 100/1000/10000 video planning (streamed/chunked, bounded memory/time),
  > thresholds and test logs measured on disposable environments.
  > DoD: zero P0/P1 safety failures, deterministic rollback/recovery,
  > explicit performance measurements, no fake scalability numbers.
  > FIX:18. QA:091..100,110..120.

- [ ] **M45 — QA-001..120 traceability and independent module security gate**
  > Выполнить: map EACH original QA ID to test path/command, OS, fixture,
  > exact CI link/SHA, result PASS/SKIP/BLOCKED, owner and acceptance;
  > fix residual standalone P0/P1 and document external-only blocked QA
  > separately without hiding or falsely passing them.
  > DoD: QA matrix 120 rows, no invented 120/120 claims; independent module
  > requirements green on exact release candidate SHA.
  > FIX:18,19. QA:001..120.

- [ ] **M46 — Genuine paid Opus smoke, OPTIONAL until explicit new user consent**
  > Выполнить: ONLY after user explicitly authorizes final immutable quote
  > and small USD limit; one approved D1 item, provider exact model, receipt,
  > evidence, cost reconciliation, rights and no unexpected retry.
  > DoD: LIVE_VERIFIED only if real run passed. Without permission:
  > PAID_SMOKE_NOT_AUTHORIZED; this does not secretly allow POST and does
  > not make standalone disabled-by-default integration impossible.
  > FIX:12,19. QA:061..070,102..103.

- [ ] **M47 — SRE standalone release dossier / single PR merge-ready**
  > Выполнить: checklist of versioned I/O, SRE-only diff, install/upgrade/
  > rollback, one minimal host mount, green exact main-merge rehearsal,
  > isolated SRE migrations and CI, cost flags, limitations, optional
  > external adapters; no read/write dependence on feature branches.
  > DoD: MODULAR_READY declared only with green test artifacts + PR22
  > conflict-free against LATEST main, zero unexpected altered other modules,
  > owner-ready single PR. Do not merge without explicit user instruction.
  > FIX:19. QA:all required core, external E2E separately conditional.

## Журнал завершений и наблюдаемые доказательства

| Этап | Статус | Код / подтверждение | Блокер / дальнейшее действие |
|---|---|---|---|
| S0–S4 | DONE | [CI SRE pre-merge](https://github.com/BanSins7/ContentMachine/actions/runs/40071341191) | Offline fixture, не LIVE |
| R01 | DONE | [Матрица совместимости](SRE_INTEGRATION_COMPATIBILITY_MATRIX.md) | Перед каждым этапом обновлять SHA |
| M01 | MERGE COMMITTED / FULL CI FAIL (PSI compatibility fixed, RE-RUN PENDING) | `09a8c7542abafa7c856e3555581a70ea52b87796`, [целевой SRE CI PASS](https://github.com/BanSins7/ContentMachine/actions/runs/40289331842), [полный Linux](https://github.com/BanSins7/ContentMachine/actions/runs/40289331759), [Windows](https://github.com/BanSins7/ContentMachine/actions/runs/40289331770), [B14](https://github.com/BanSins7/ContentMachine/actions/runs/40289331751) | Исходный Linux/Windows full CI на 09a8c754 FAIL на PSI release-gate после успешного 1733-test прогона; новая версия gate разрешает только ADD новых миграций; ждём CI на новом SHA |
| M02 | CODE COMMITTED / CI PENDING | `75e17f2e67c6deb9bd6e3500c7c5cf49ad7f29de`, [M02 CI](https://github.com/BanSins7/ContentMachine/actions/runs/40292151245), [документ](SRE_M02_MODULE_CONTRACTS.md) | Не считать контрактные события подтверждённым D1/QDP E2E |
| M03 | CODE COMMITTED / CI PENDING | Код и PG тесты в `intelligence/relevance/config.py`, `health.py`, `tests/test_sre_runtime_config.py`, `tests/test_sre_health_pg.py`, итоговый кодовый SHA `9802b9628c2477a55e5bf96ea35c1f87c80c9d6e`; [документация](SRE_M03_RUNTIME_CONFIG.md), [итоговый SRE CI](https://github.com/BanSins7/ContentMachine/actions/runs/40294735685) | 17 offline + 2 PG17 M03 tests (scheduled). Дождаться PASS Linux/Windows/PG и [full CI](https://github.com/BanSins7/ContentMachine/actions/runs/40294735714) на одном code SHA; пока НЕ зачёркивать |
| M04 | CODE COMMITTED / CI PENDING | Итоговый проверяемый кодовый SHA `5496a29022134dbec46b2e744aa1eabdf374779c`; SRE localhost API + Studio + тесты: `intelligence/relevance/http_api.py`, `tests/test_sre_bootstrap_http.py`, `site/sre-studio.*`, host hooks. [M04 документация](SRE_M04_HOST_MOUNT.md), [целевой CI](https://github.com/BanSins7/ContentMachine/actions/runs/40297067246), [Linux](https://github.com/BanSins7/ContentMachine/actions/runs/40297067242), [Windows](https://github.com/BanSins7/ContentMachine/actions/runs/40297067293); PSI gate regression `tests/test_sre_psi_gate_compat.py` и forward-only исправление скрипта в SRE-ветке | PASS Linux/Windows/PG17/Node22 на одном кодовом SHA ещё не получен; НЕ ЗАЧЁРКИВАТЬ |
| M05 | CODE COMMITTED / CI PENDING | [M05 документ](SRE_M05_MIGRATION_COMPATIBILITY.md), `intelligence/relevance/migration_guard.py`, `tests/test_sre_migration_guard.py`, `tests/test_sre_migration_guard_pg.py`; PG17 fresh + main-only upgrade и checksum/drift негативные сценарии включены в `sre-contracts.yml`; кодовые коммиты `8468c61`, `71ea857`, `b988f45`, `11dd6b7` | GitHub Actions на новом SHA ещё не PASS, production DB не проверялась; 016/017 не переписывались, `main` без изменений |
| M06 | CODE COMMITTED / CI PENDING | [M06 описание](SRE_M06_C9_APPROVED_PORT.md); `ports/approved_c9.py` and `ports/taxonomy.py` single DB-authoritative C9 bundle, complete UUID5 ancestor projections + all hypothesis contexts; unit tests `test_sre_c9_approved_port.py`, real C9 PG17 extension `test_sre_taxonomy_adapter_pg.py`, Linux/Windows focused workflow; code commits `17fb334`, `346c861`, `c674cd0`, `f23c2b4`, `62b3214`, `aa5740a` | Реализация отмечена как выполненная; до настоящих GitHub Actions PASS чекбокс этапа не закрыт. C9 external branches, D1/QDP live, paid Opus не использовались |
| M07 | CODE COMMITTED / CI PENDING | [M07 описание](SRE_M07_D1_PROVENANCE.md), `ports/d1_provenance.py` + alias `ports/discovery.py`; canonical D1 video/project/content read, D1 provider request/run/query/source + source-specific observation cross-check, UUID-vs-BIGINT denial, direct D1 and replay; `tests/test_sre_d1_provenance.py`, real PG17 additions to `tests/test_sre_intake_pg.py`, dedicated workflow; code commits `0f39d42`, `7cd64cb`, `8f8d9fe`, `f88cfee`, `1b1665c`, `89b338b`, `20d33e3` | Код [x], тесты не завершились: нельзя зачёркивать M07 и объявлять MODULAR_READY; main/другие ветки/SQL/provider не трогали. |
| M08 | CODE COMMITTED / CI PENDING | [M08 документ](SRE_M08_LIVE_CONTENT_GATE.md), `live_content_gate.py`; source-specific D1 v1 provenance + live S3 authorization/expiry/version/hash, metadata_only/partial/full guard and mandatory external grant at `before_external_send`; S4 `offline_runs.py` + `offline_worker.py` real plan/worker/result gate, tests `test_sre_live_content_gate.py` and PG17 additions in `test_sre_intake_pg.py`, `test_sre_offline_pg.py`; focused workflow; commits `7caf685`, `8a2cc7c`, `2213d88`, `0b1867e`, `7b3eba2`, `666b0fe`, `d11339a` | Code and tests written, GitHub Actions QUEUED not PASS. No paid calls, SQL edits or merge; concurrent paid dispatch safety and public auth belong to later stages. |
| M09 | CODE COMMITTED / CI PENDING | [M09 spec](SRE_M09_DATASET_PREVIEW.md); `intelligence/relevance/dataset_preview.py` (S2 C9 approved one transaction + M08 D1 right version/expiry, unique video dedup, all leaf pairs and explicit blocked reasons/NULL warnings), `tests/test_sre_dataset_preview.py` 100×20 complete matrix/foreign-project/duplicate/invalid/expired QA, `tests/test_sre_offline_pg.py` real C9+D1 PG17 tests, SRE cross-platform workflow; commits `fb3cce0`, `9336263`, `fc752d7`, `f9c7938`, `4c0c983`, `58fa729`, `7d402e6` | Code [x], CI queued; no SQL writes or provider spend. Future M10 LIVE run status contracts not yet built. |
| M10 | CODE COMMITTED / CI PENDING | [M10 spec](SRE_M10_LIVE_FSM.md); `intelligence/relevance/live_fsm.py`: LIVE-only typed frozen DTOs, run/item/batch/attempt state graphs, exact sorted UUID leaf accounting, unknown billing outcome, transition reconciliation; `tests/test_sre_live_fsm.py` validates all allowed/forbidden transitions, type/NaN/UUID/BIGINT, fake-vs-LIVE, immutable verdict groups, receipt spoof, no paid import; `.github/workflows/sre-contracts.yml` Linux+Windows. Code commits `3ade06c`, `5ecc895`, `6b7042f`, `c711375`, `5b50de3`, `3c65514`. | Реализация [x], кодовые CI запуски QUEUED; DB reconciliation/atomic lease and finance belong to M11+; никакого paid call/SQL/merge. |
| M11 | CODE COMMITTED / CI PENDING | [M11 runbook](SRE_M11_LIVE_POSTGRESQL.md); additive migration `intelligence/migrations/054_sre_live_core.sql`: 3 LIVE only tables, C9 composite FK, source-authoritative exact S3 content/D1 observation and SHA FK, project isolation, immutable JSON provenance/manifest SQL guards, monotone lease generation and PUBLIC revoke; updated preflight `migration_guard.py`, `tests/test_sre_migration_guard.py` + fresh/main-only upgrade PG17 `tests/test_sre_migration_guard_pg.py`; static `tests/test_sre_live_schema.py`, real PG17 `test_sre_offline_pg.py` forged owner/origin/version/SHA/leases/deletes; workflow Linux Windows PG17. Commits `00eb6d7`, `1983887`, `7dac50f`, `5f6a2f4`, `aa3a742`, `35d7cdb`. | CODE [x], QA CI PENDING; no finance, paid model POST, legacy migration rewrite, main/peer branch edit or merge. |
| M12 | CODE COMMITTED / CI PENDING | [M12 runbook](SRE_M12_LIVE_PLANNER.md), `intelligence/relevance/live_planner.py` transactional project lock + actual C9 H1/H2 + M08 external-permitted D1/expiry/SHA + exact C9 leaf coverage, canonical request/manifest SHA, immutable consent-gated LIVE run and per-video not_evaluated items. Same key/input returns same run, changed input = `idempotency_conflict` (future HTTP 409), cannot overwrite. `tests/test_sre_live_planner.py` negative/fake, `test_sre_offline_pg.py` true PG17 rights/revoke/2-client race, `sre-contracts.yml` Linux Windows PG17. Commits `84a0d9c`, `15e9856`, `a95f4f7`, `9a66147`, `5efc9b6`, `e233f54`, `77f173e`. | CODE [x], tests committed, CI QUEUED. No batch dispatch, provider POST, spend, schema rewrite or merge. |
| M13–M47 | TODO | См. этапы выше | Один этап — один следующий запрос |

**Правило merge:** DRAFT PR до запроса владельца. **Правило платного вызова:** без отдельного consent и budget никакого OpenRouter POST. Реально совместный E2E с внешними модулями не обещается, пока их ветки/контракты не проверены.

