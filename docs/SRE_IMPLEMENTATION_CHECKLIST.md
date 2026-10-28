# SRE — основной Markdown-план разработки и журнал выполнения

> **Единая точка продолжения для любого нового чата.** Перед работой прочитай этот файл, [полный исходный FIX/атомарный план](SRE_STANDALONE_MODULE_IMPLEMENTATION_PLAN_RU.txt), [матрицу интеграции R01](SRE_INTEGRATION_COMPATIBILITY_MATRIX.md) и [трекер](SRE_IMPLEMENTATION_TRACKER_V1.md), затем сравни актуальные SHA веток и последние GitHub Actions. **Работай исключительно в `feature/sre-production-v1-20261023`, PR #22. Не меняй main и другие feature-ветки.**

**Обновлено:** 2026-10-27. **Источник этапов:** `SRE_STANDALONE_MODULE_IMPLEMENTATION_PLAN_RU.txt` (M01–M47), без изменения требований. **Mxx считается завершённым и зачёркивается только после прохождения всех его DoD/тестов на точном кодовом SHA.** Один запрос — один следующий этап; исключение: проверка/исправление CI незакрытого ранее этапа.

**Текущий статус на 2026-10-28:** M01–M29 — code committed в отдельную SRE feature-ветку, но **exact-head CI PASS и полный DoD НЕ подтверждены**. M24 explicit owner consent; M25 Decimal project reserve; M26 immutable M15+M26 one-intent request fence; M27 OpenRouter Opus 5.5 fake/real POST disabled; M28 generation metadata GET, provider inference cost/receipt and atomic reserved→spent/released; **M29 — безопасная сверка UNKNOWN: неизвестный generation ID ⇒ удерживать резерв без rePOST; известный ID ⇒ отдельный authenticated M28 GET, поддержка утерянного completion body и идемпотентная M29 audit SQL 063; read-only список недостающих M20 request ordinals**. Ни один этап не запускает платный POST. Бюджет M28 не равен верификации account fees/taxes. **M29 multi-request execution/true single-use paid send DoD остаётся [ ] BLOCKED**: M26 one-intent-per-run и M25 one-reserve-per-consent сознательно не сняты, все запросы C9 не выполняются. M16–M18 реальный pretrained ONNX [ ] BLOCKED. Код этапа M29 [x], CI [ ], полный DoD [ ]; следующий **M30** — строгая проверка полноты/partial status без фальшивого completed. PR #22 остаётся DRAFT, main и другие ветки не менялись.

**Готовое наследство проекта:**
- [x] ~~S0–S4 — основные контракты, approved RTB, D1 intake, безопасный offline worker~~; подтверждение до M01: [SRE CI](https://github.com/BanSins7/ContentMachine/actions/runs/40071341191).
- [x] ~~R01 — аудит веток, миграций, PR и зависимостей~~: [матрица](SRE_INTEGRATION_COMPATIBILITY_MATRIX.md). R-серия — историческая, *не активный план*.
- [ ] **MODULAR_READY:** SRE автономен, latest-main rehearsal зеленый, можно merge по запросу владельца.
- [ ] **LIVE_VERIFIED:** отдельно разрешённый paid smoke на exact Opus.
- [ ] **ECOSYSTEM_CONNECTED:** реальная внешняя Collector/QDP интеграция (не блокирует автономный SRE merge).

## CI M23 — обновлено 2026-10-27

**M23 CODE [x], CI [ ] PENDING.** На source/docs SHA `cad61f75c15364bb6b19d56666cff71636945b6e` GitHub Actions `queued`, `conclusion=null`: [SRE M02–M23 40463746166](https://github.com/BanSins7/ContentMachine/actions/runs/40463746166), [full Linux 40463746297](https://github.com/BanSins7/ContentMachine/actions/runs/40463746297), [Windows D1 40463746162](https://github.com/BanSins7/ContentMachine/actions/runs/40463746162), [Knowledge B 40463746187](https://github.com/BanSins7/ContentMachine/actions/runs/40463746187). После MD-коммитов перепроверить точный HEAD; `queued` и написанные тесты НЕ `PASS`. Котировка M23 в этой реализации — условный **консервативный токенный USD ceiling**, фактические токены и дополнительные сборы аккаунта OpenRouter не подтверждены. M24 consent/M25 reserve отсутствуют. M16–M18 real pretrained ONNX [ ] BLOCKED.

### Архив M22

## CI M22 — обновлено 2026-10-27

**M22 CODE [x], QA [ ] PENDING.** На SHA `8fe69b6e09374a2e787071a6d15698cbc44fb038` GitHub Actions `queued`, `conclusion=null`: [SRE M02–M22 40460274114](https://github.com/BanSins7/ContentMachine/actions/runs/40460274114), [full Linux 40460274232](https://github.com/BanSins7/ContentMachine/actions/runs/40460274232), [Windows D1 40460274155](https://github.com/BanSins7/ContentMachine/actions/runs/40460274155), [Knowledge B 40460274140](https://github.com/BanSins7/ContentMachine/actions/runs/40460274140). Exact final SHA перепроверить после документов. Offline fixture tests ≠ live Opus capabilities or authenticated account status. Никаких оплачиваемых POST/quote/consent. Реальная pretrained ONNX M16–M18 [ ] BLOCKED.

### Архив M21

## CI M21 — обновлено 2026-10-27

**M21 code [x], тестовая QA [ ] PENDING.** На source+docs SHA `e8f9a2ed751f1632f4324303e14d97ff7db016e5` GitHub Actions `queued`, `conclusion=null`: [SRE M02–M21 40458681030](https://github.com/BanSins7/ContentMachine/actions/runs/40458681030), [full Linux 40458681063](https://github.com/BanSins7/ContentMachine/actions/runs/40458681063), [Windows D1 40458681106](https://github.com/BanSins7/ContentMachine/actions/runs/40458681106), [Knowledge B 40458680935](https://github.com/BanSins7/ContentMachine/actions/runs/40458680935). Проверять exact окончательный SHA после tracker/checklist. Добавленные тесты и `queued` не равнозначны `PASS`. Локально нет доступного GitHub checkout. M16–M18 реальное pretrained ONNX остаётся отдельным BLOCKED.

### Архив M20

## CI M20 — обновлено 2026-10-27

**M20 code [x], CI [ ] PENDING.** Непроверенные реальные токены Opus сознательно не называются точным биллинговым подсчётом. На кодовом SHA `5aca68d17e32f64dd2274702d8c9916917e6048b` GitHub Actions `queued`, `conclusion=null`: [SRE M02–M20 40455803866](https://github.com/BanSins7/ContentMachine/actions/runs/40455803866), [full Linux 40455803669](https://github.com/BanSins7/ContentMachine/actions/runs/40455803669), [Windows D1 40455803774](https://github.com/BanSins7/ContentMachine/actions/runs/40455803774), [Knowledge B 40455803782](https://github.com/BanSins7/ContentMachine/actions/runs/40455803782). После этого коммита проверить exact final HEAD и не считать `queued` или наличие unit-тестов SUCCESS. Локальный git clone заблокирован DNS. M16–M18 реальное ONNX [ ] BLOCKED.

### Архив CI M19

## CI M19 — обновлено 2026-10-27

**M19: код [x], реальный CI PASS [ ] PENDING.** На source/docs SHA `169bafb9e09814ac6e981461517b3498dbbeb220` GitHub Actions `queued`, `conclusion=null`: [SRE M02–M19 40454110744](https://github.com/BanSins7/ContentMachine/actions/runs/40454110744), [Full Linux 40454110753](https://github.com/BanSins7/ContentMachine/actions/runs/40454110753), [Windows D1 40454110803](https://github.com/BanSins7/ContentMachine/actions/runs/40454110803), [Knowledge B 40454110742](https://github.com/BanSins7/ContentMachine/actions/runs/40454110742). Проверить exact new HEAD после tracker/checklist. Контейнер не может клонировать GitHub: DNS `Could not resolve host: github.com`. Код тестов добавлен, но не выдавать за выполненный PASS. M16–M18 реальное ONNX inference остаётся отдельным BLOCKED, M19 не исправляет эту проблему.

### Архив M18

## Блокер CI / настоящей модели и persistence — обновлено по M18 2026-10-26

**M18 code [x], реальный CPU-ONNX/cache-after-restart DoD [ ] BLOCKED, CI QA [ ] PENDING.** Без operator-approved pretrained `model.onnx` и `tokenizer.json` с фиксированной ревизией/SHA тест cold/hot в настоящем PG17 намеренно пропускается — mock вектор не считается inference. На source/docs SHA `0c7453e90a9978d36eb2f0241c7c28f780064059` GitHub Actions всё ещё `queued`, `conclusion=null`: [SRE M02–M18 40390414594](https://github.com/BanSins7/ContentMachine/actions/runs/40390414594), [Linux full 40390414591](https://github.com/BanSins7/ContentMachine/actions/runs/40390414591), [Windows D1 40390414685](https://github.com/BanSins7/ContentMachine/actions/runs/40390414685), [Knowledge B 40390414571](https://github.com/BanSins7/ContentMachine/actions/runs/40390414571). Проверять exact HEAD после нового tracker/checklist коммита. Не выдавать в процессе `queued` за `success`.

### Архив M17

## Блокер CI / настоящих embeddings — обновлено по M17 2026-10-26

**M17 SOURCE CODE [x], QA [ ] и REAL MODEL [ ] BLOCKED.** Реальная семантическая приёмка M16/M17 невозможна без независимо зафиксированной pretrained CPU ONNX `model.onnx` + `tokenizer.json` и checksum+revision. Быстрые 48-leaf тесты проверяют **алгоритм**, а не настоящие vectors. На кодовом SHA `f7610b042c539c43dfd026ba50282b9d12dc540f` GitHub Actions `queued`, `conclusion=null`: [SRE M02–M17 40384891884](https://github.com/BanSins7/ContentMachine/actions/runs/40384891884), [Full Linux 40384891945](https://github.com/BanSins7/ContentMachine/actions/runs/40384891945), [Windows D1 40384891847](https://github.com/BanSins7/ContentMachine/actions/runs/40384891847), [Knowledge B 40384891853](https://github.com/BanSins7/ContentMachine/actions/runs/40384891853). После документационного коммита проверять exact new HEAD. Наличие кода/тестов и `queued` не означает PASS; никаких fake embeddings или paid POST.

### Архив M16

## Блокер CI и реального inference — обновлено по M16 2026-10-26

**M16 реализация завершена, проверка настоящей модели BLOCKED:** контролируемая среда не содержит утверждённых pretrained ONNX весов + tokenizer JSON + immutable upstream revision/SHA. Unit/health тесты не вправе подменять inference; отдельный `CM_SRE_M16_REAL_MODEL_REQUIRED=1` тест из [runbook](SRE_M16_LOCAL_EMBEDDING.md) необходимо реально пройти перед acceptance. Для текущего кодового SHA `c40de3baf0f8fb4b133b4ffcb651f1467d9998b7` Actions `queued`, `conclusion=null`: [SRE M02–M16 40365869883](https://github.com/BanSins7/ContentMachine/actions/runs/40365869883), [Linux full 40365870002](https://github.com/BanSins7/ContentMachine/actions/runs/40365870002), [Windows D1 40365869763](https://github.com/BanSins7/ContentMachine/actions/runs/40365869763), [Knowledge B 40365869757](https://github.com/BanSins7/ContentMachine/actions/runs/40365869757). Проверить на окончательном HEAD после документационных коммитов. Code [x], QA [ ], REAL INFERENCE BLOCKED. Никакого fake success / платной модели / merge.

### Архив очереди M15

## Блокер CI — обновлено по M15 2026-10-26

**M01–M15 кодовые этапы внесены, QA открыто.** На кодовом SHA `d353f7c5bfc504c10ac2b77a8a2b62094f29c470` GitHub Actions: `status=queued`, `conclusion=null`: [SRE M02–M15 40329915948](https://github.com/BanSins7/ContentMachine/actions/runs/40329915948), [Full Linux 40329916174](https://github.com/BanSins7/ContentMachine/actions/runs/40329916174), [Windows D1 40329916103](https://github.com/BanSins7/ContentMachine/actions/runs/40329916103), [Knowledge B 40329916215](https://github.com/BanSins7/ContentMachine/actions/runs/40329916215). После этого документационного коммита проверить проверки на **последнем** HEAD. Наличие новых тестов и SQL не является выполненным PASS; без действительного PostgreSQL17 CI нельзя помечать DoD как завершённый.

### Архив очереди M14

## Блокер CI — обновлено по M14 2026-10-26

**M01–M14 QA OPEN:** для M14 источников и документации SHA `7664dc10317d444b2d2ccbe1286f59a411f5561e` workflow были `queued`, `conclusion=null`: [SRE M02–M14 40324552968](https://github.com/BanSins7/ContentMachine/actions/runs/40324552968), [Linux full 40324553096](https://github.com/BanSins7/ContentMachine/actions/runs/40324553096), [Windows D1 40324553018](https://github.com/BanSins7/ContentMachine/actions/runs/40324553018), [Knowledge B 40324552948](https://github.com/BanSins7/ContentMachine/actions/runs/40324552948). После коммитов checklist/tracker **повторно проверять exact HEAD**. Локально git clone невозможен по причине DNS отсутствия выхода контейнера к GitHub; локальный unit/PG тест не выполнялся. Unit+PG код написан, но не считать QA PASSED до фактических CI результатов.

### Архив M13 CI

## Блокер CI — обновлено по M13 2026-10-26

**M01–M13 код внесён, но QA не закрыт:** для кодового SHA `c92c920a613c558c14173e6f7abc582bde16af65` workflow имели `status=queued`, `conclusion=null`: [SRE M02–M13 40321590283](https://github.com/BanSins7/ContentMachine/actions/runs/40321590283), [ContentMachine Linux 40321590263](https://github.com/BanSins7/ContentMachine/actions/runs/40321590263), [Windows D1 40321590243](https://github.com/BanSins7/ContentMachine/actions/runs/40321590243), [Knowledge B 40321590091](https://github.com/BanSins7/ContentMachine/actions/runs/40321590091). После checklist/tracker коммитов **повторно проверить последний HEAD**. Тесты подготовлены, не утверждать что пройдены. Текущий M13 открывает путь к M14, не выполняет платные модели/изменения main.

### Архив очереди M12

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

- [ ] **M13 — Exact batch manifest + coverage accounting** *(код [x], CI QA [ ]; [M13 runbook](SRE_M13_BATCH_COVERAGE.md))*
  - [x] ~~`live_batch_manifest.py`: deterministic UUID5 batch+input SHA, project-scoped transactional membership lock and full video×C9 hypothesis matrix, idempotent replay with exact DB row verification, explicit not_evaluated/blocked/partial coverage, no provider spend.~~
  - [x] ~~Новая только-добавочная миграция `055_sre_live_batch_coverage.sql`: composite project/run/batch/item FK, one-batch-per-item, distinct hypothesis pair PK, immutability, DB completed/partial physical-coverage triggers. PG17 2×3, concurrent replay and false-completion denial + unit 100×20 coverage and migration/CI updates.~~
  - [ ] **Приёмка:** SRE Linux/Windows + PG17 + full Linux + Windows D1 + Knowledge B должны реально завершиться SUCCESS на актуальном SHA; queued != PASS.
  > Выполнить: deterministic expected leaf keys, group videos, exact input
  > digest, no label omitted silently; explicit not_evaluated/blocked.
  > DoD: worker cannot mark completed on partial N/M output.
  > FIX:07,11,13. QA:029,041..050,111.

- [ ] **M14 — Claim/lease/heartbeat and fencing for LIVE items** *(код [x], полный QA [ ]; [M14 runbook](SRE_M14_LIVE_LEASE.md))*
  - [x] ~~`intelligence/relevance/live_lease.py` — deterministic `FOR UPDATE SKIP LOCKED`, atomic PostgreSQL claim, new UUID token + generation+1, expiring DB-clock lease, CAS heartbeat/release, explicit `lease_lost`, project+run+batch+full-C9 matrix gate and D1 rights recheck. pending_consent не захватывается, `dispatch_allowed=False`.~~
  - [x] ~~Без новых миграций и платных вызовов: negative offline unit `test_sre_live_lease.py`, настоящий PG17 `test_sre_offline_pg.py` (два конкурирующих worker, token/gen/reclaim, revocation), Linux/Windows/PG17 workflow.~~
  - [ ] **QA acceptance:** реальные SUCCESS по SRE Linux+Windows+PG17, full Linux, Windows D1, Knowledge B на точном последнем SHA. Сейчас QUEUED, не заявлять PASS.
  > Выполнить: SKIP LOCKED, deterministic lock order, durable token/gen/expiry;
  > concurrent DB workers, no paid calls.
  > DoD: only one owner per item, stale token rejected; Linux+PG17 race tests.
  > FIX:07,18. QA:026..028,091,098.

- [ ] **M15 — Cancel/pause/restart and safe recovery before dispatch** *(реализация [x], QA [ ]; [M15 runbook](SRE_M15_RECOVERY.md))*
  - [x] ~~Новая только-добавочная миграция `056_sre_live_dispatch_fences.sql`: проектно-изолированный, неизменяемый pre/post-dispatch witness, token/gen/expiry checks, append-only attempt, workspace-share lock against cancel; без платных вызовов.~~
  - [x] ~~`intelligence/relevance/live_recovery.py`: cancel/pause with exclusive project lock and lease invalidation, restart only with exact C9/D1+pipeline pins and new pending_consent, pre-dispatch crashed reservation recovery, post-intent UNKNOWN fail-stop and no paid auto-retry.~~
  - [x] ~~M14 claim/heartbeat/release workspace-shared fencing и fail-closed M14 auto-reclaim при reserved/dispatch_intent; M10 typed FSM recovery states. Linux/Windows `test_sre_live_recovery.py` + `test_sre_live_recovery_schema.py`, настоящий PG17 `test_sre_offline_pg.py` (race/restart/pre-post intent), fresh/main-only migration tests and CI.~~
  - [ ] **QA acceptance:** Linux+Windows+PostgreSQL17 SRE, full Linux, Windows D1, Knowledge B must actually be SUCCESS for the exact final SHA. Сейчас все CI QUEUED / test pass НЕ подтверждён.
  > Выполнить: terminal state control, expiry/restart/fail-stop; distinguish
  > before vs after dispatch_intent. НИКОГДА автоматически не повторять
  > unknown paid attempt после POST.
  > DoD: crash/restart/cancel preserves database and budget invariants.
  > FIX:07,09,12,18. QA:027..030,069,093..100.


### Фаза D — ЛОКАЛЬНЫЙ RETRIEVAL И ВХОД OPUS

- [ ] **M16 — Локальная embedding-модель и воспроизводимый engine** *(реализация [x], DoD реальная inference [ ] BLOCKED, CI [ ]; [M16 runbook](SRE_M16_LOCAL_EMBEDDING.md))*
  - [x] ~~`local_embedding.py`: реальный ONNX CPU-only backend, mandatory operator-pinned model/revision full 40hex / model.onnx SHA / tokenizer.json SHA, streaming preflight, no symlinks/extra weights/remote paths, tokenization, mask-pooling, L2 normalized finite vectors, reproducibility check. No cloud/model download or silent fallback.~~
  - [x] ~~`local_embedding_health.py`: zero-cost local CLI exit 0 **только** после реального повторяемого вывода, иначе BLOCKED/exit 3. Optional version-pinned `requirements-local-embedding.txt`; `test_sre_local_embedding.py` быстрые Linux/Windows негативные unit и `test_sre_local_embedding_real.py` настоящий явно требуемый semantic smoke (отдельно). Workflow и runbook.~~
  - [ ] **Реальная M16 inference DoD BLOCKED:** в рабочей среде отсутствуют независимо утверждённые pretrained `model.onnx`/`tokenizer.json` с точными SHA+revision, а также CPU ONNX runtime. Fake fixture или просто `skip` не являются PASS. Запустить операторскую команду из runbook на контролируемой среде и записать наблюдаемый real SUCCESS/модель/sha/кодовый HEAD.
  - [ ] **CI acceptance:** GitHub Actions Linux/Windows/PG17/full Linux/Knowledge B на точном HEAD должны реально завершиться SUCCESS; текущий status queued.
  > Выполнить: real local semantic embedding backend, pinned model name,
  > revision/checksum, deterministic inference and health/preflight;
  > no cloud embeddings; fast unit fixture separate from actual model.
  > DoD: real model inference in controlled test environment or BLOCKED;
  > tests no silent model substitute.
  > FIX:10. QA:031..035.

- [ ] **M17 — ALL-LEAF similarity ranking, исключения отдельно** *(реализация [x], реальный ONNX semantic DoD [ ] BLOCKED, CI QA [ ]; [M17 runbook](SRE_M17_ALL_LEAF_RANKING.md))*
  - [x] ~~`live_leaf_ranking.py`: полный approved C9 H1/H2 deep direction→branch→hypothesis + verified fact provenance; positive/exclude отдельными ONNX текстами, signed/finite cosine, deterministic tie by category UUID; strict full expected C9 leaf set (1–48) and exact matrix; no top_k/threshold/drop or fake `no_match`.~~
  - [x] ~~Trusted `rank_live_run_item`: только настоящий project-scoped S2 C9 + M12 immutable LIVE run/item SHA + M08 D1 current rights/source/observation; **before** loading pinned M16 ONNX, no HTTP/provider/results/paid calls. Unit `test_sre_live_leaf_ranking.py` (3 approved leaves + математическая 48-leaf coverage, low negative similarities, stable ties, exclusions), actual PG17 negative tests in `test_sre_offline_pg.py`; `test_sre_live_leaf_ranking_real.py` separate required real pretrained ONNX test and CI Linux/Windows/PG17.~~
  - [ ] **REAL model DoD BLOCKED:** требуется genuine locally pinned pretrained `model.onnx`/`tokenizer.json`, exact source revision+SHA, CPU inference and observed reproducible all-leaf result. Pure mathematical/mock tests или пропущенный real тест НЕ являются PASS.
  - [ ] **CI acceptance:** exact final branch SHA SRE Linux/Windows/PG17, full Linux, Windows D1, Knowledge B all `conclusion=success`; сейчас QUEUED.
  > Выполнить: rank positive meaning vs exclusion, full tree directions,
  > stable tie-break and all hypothesis keys, no threshold filtering.
  > DoD: number of output candidates = approved leaves, including low scores.
  > FIX:10. QA:031..040,111.

- [ ] **M18 — Embedding cache/pinning/revocation** *(код [x], real inference/restart DoD [ ] BLOCKED, GitHub CI [ ]; [M18 runbook](SRE_M18_EMBEDDING_CACHE.md))*
  - [x] ~~`local_embedding_cache.py`: trusted PostgreSQL transaction + project workspace lock for dedup; mandatory actual DB C9 H1/H2+M12 pins and current D1/source policy SHARE lock **before** cache read, same checks before return; M16 operator model file SHA checked on hot and cold path; actual ONNX only populates misses.~~
  - [x] ~~Forward-only `057_sre_local_embedding_cache.sql`: project+approved taxonomy FK, immutable D1 version/project/video/source/policy/SHA FK, ON DELETE CASCADE for content removal, exact project/model/C9/context/D1/semantic-input SHA unique key, JSONB numeric shape and 2MB/24h DB caps, no public grants. Bounded 1000 entries/project, TTL min(requested expiry, D1 content expiry), expiration purge and explicitly scoped revocation/deletion callback.~~
  - [x] ~~Pure `test_sre_local_embedding_cache.py` no-real-model cache math/rights/fingerprint tests; `test_sre_local_embedding_cache_schema.py` 057 migration/tenant SQL; actual PG17 `test_sre_offline_pg.py` FK, immutable, TTL, source revocation and purge; separately gated **real ONNX cold/hot across two PG connections** test, Linux/Windows/PG17 CI, migration guard next 058 and runbook.~~
  - [ ] **M18 real DoD BLOCKED:** получить независимо утверждённые pretrained `model.onnx`/`tokenizer.json`, source revision/SHA, прогнать actual local CPU ONNX + PostgreSQL17 cold/hot after new connection and rights revocation. Пропущенный тест или искусственные векторы НЕ PASS.
  - [ ] **CI QA:** все SRE Linux/Windows/PG17 и full Linux/Windows D1/Knowledge B должны реально завершиться `success` на точном кодовом SHA; сейчас `queued`.
  > Выполнить: project/model/taxonomy/content hashes in cache keys, dedup,
  > bounded storage, rights expiry and deletion; no cross-tenant leak.
  > DoD: restart reproducible, revoked content embeddings unavailable.
  > FIX:10,18. QA:034,036..040,057,117.

- [ ] **M19 — Complete C9 classifier context and prompt injection isolation** *(код [x], CI QA [ ]; [M19 runbook](SRE_M19_COMPLETE_C9_CONTEXT.md))*
  - [x] ~~`live_classifier_context.py`: детерминированные exact 1-per-approved-leaf контекстные пакеты, копирование всех 11 RTB node fields для direction/optional branch/hypothesis, siblings (name/definition/include/exclude), approved facts с ID, source_quote, source_field и origin, полные C9 H1/H2 publication, project и content pins/SHA; никакого summarization/threshold/filter.~~
  - [x] ~~M08/D1 metadata_only действительно без придуманного transcript/scenes, multilingual Unicode; фиксированная system role и JSON user DATA isolation против role/XML/JSON prompt injection, `external_dispatch_allowed=False`, no provider/paid/finance; context UTF-8 overflow fail-closed. Production DB entry использует _authorize_live_rank_inputs из M17, без client-controlled self-attested C9/D1.~~
  - [x] ~~`test_sre_live_classifier_context.py` с full-11-field, quotes, siblings, tamper/injection/multilingual/metadata_only, unit no cloud; реальный PG17 `test_sre_offline_pg.py` с actual RTB dual H1/H2 и D1 revocation/tenant, workflow Linux+Windows+PG17, документация. Новая миграция не требовалась.~~
  - [ ] **QA acceptance:** exact SRE Linux/Windows+PG17, full Linux, Windows D1, Knowledge B Github Actions `conclusion=success`; сейчас `queued`, local git clone blocked by DNS. Реальная ONNX проверка M16–M18 остаётся отдельным BLOCKED; не объявлять её решённой.
  > Выполнить: exact hypothesis direction/branch/rules/includes/excludes/facts
  > and siblings, untrusted D1 content as DATA, multilingual/metadata_only
  > semantics; no search-query label shortcut.
  > DoD: full 11 C fields, quote source refs, no invented facts/fields.
  > FIX:05,11. QA:041..046,051..060.

- [ ] **M20 — Deterministic Opus request/batch packer** *(код [x], полный CI QA [ ]; [M20 runbook](SRE_M20_OPUS_REQUEST_PACKER.md))*
  - [x] ~~`opus_request_packer.py`: trusted operator `anthropic/claude-opus-...` model/revision/catalog revision/SHA pin, deterministic requested temperature=0, full SHA sealed M19 system+JSON packet validation, exact all 1–48 C9 hypothesis keys, no top_k/threshold/drop or malformed role elevation.~~
  - [x] ~~UTF-8 system+JSON+framing byte proxy with bounded context/output safety, `token_count_exact=False` and mandatory future official provider preflight; **not** a real Opus tokenizer or permission to spend. Unicode D1 text and measured segments losslessly sliced with original offsets, timestamps and SHA; C9 hierarchy/siblings/facts repeated unchanged per chunk. Refuse if complete C9 alone is too large.~~
  - [x] ~~M19 opt-in `split_ready=True` up to 2MiB for safe M20 upstream overflow (default M19 remains 128KiB). Deterministic local batches max 16 requests/250000 proxy units, no lost or duplicated request ordinals/hashes; per-request and batch SHA; `dispatch_allowed=False`, `paid_calls=0`. Pure `test_sre_opus_request_packer.py` (Unicode/130-media-segment >128KiB full reconstruct, pins/negative/injection), real PG17 `test_sre_offline_pg.py` H1/H2 C9 + D1/revoke/cross tenant, Linux+Windows/PG17 workflow, documentation; no migration.~~
  - [ ] **QA acceptance:** actual `conclusion=success` across SRE Linux/Windows/PG17 and full Linux/Windows D1/Knowledge B exact final SHA; currently `queued`, local clone blocked by DNS.
  - [ ] **Exact token acceptance intentionally deferred:** M20 only guarantees byte-proxy bounds and `requires_provider_preflight=True`; actual Opus token counting/budget/provider send cannot be marked completed at M20 without authorized future provider-specific verification.
  > Выполнить: bind model+catalog revision, exact contexts and content chunks,
  > token count bounds and overflow split with unchanged expected leaf coverage;
  > every request hash воспроизводим.
  > DoD: deterministic batch SHA, lossless coverage; exact provider tokens require later dispatch preflight.
  > FIX:11. QA:041..050,111..116.

- [ ] **M21 — Strict output/evidence verifier** *(код [x], CI QA [ ]; [M21 runbook](SRE_M21_OPUS_EVIDENCE_VERIFIER.md))*
  - [x] ~~`opus_evidence_verifier.py`: strict bounded whole-object JSON parser (duplicate keys/fences/NaN/extra fields rejected), exact category C9 UUID and enum `match/no_match/uncertain`; `not_evaluated` only system state. Match requires evidence; no technical failure maps to `no_match`.~~
  - [x] ~~Verbatim D1 `title/description/hashtag/transcript/scene` citations with exact `index,start_char,end_char,quote` within **that** M20 request chunk, exact observed `start_ms/end_ms` for media, no invented speech/quotes/timestamps. Whole M20 SHA, model/catalog pin, request/batch ordinals, original D1 spans and **all 1–48 C9 leaf/fragment responses** checked without omission. Mixed chunk decisions → reviewed `uncertain`; missing response or invalid citation → technical fail-closed.~~
  - [x] ~~Trusted `verify_live_opus_responses` rederives exact pinned M20 plan via current real project C9 H1/H2 + D1/S3 rights/version before any acceptance; external provider receipt identity and model semantic truth explicitly **not** asserted. No DB write/Opus/financial/paid calls. `test_sre_opus_evidence_verifier.py` adversarial JSON/quote/timestamp/partial/mixed/role tests, actual PG17 `test_sre_offline_pg.py` revoked source/cross-project/plan drift, Linux+Windows/PG17 CI and runbook.~~
  - [ ] **QA acceptance:** real `success` for exact last SHA SRE Linux/Windows/PG17, full Linux, Windows D1 and Knowledge B. At last check workflows `queued`, `conclusion=null`; local checkout unavailable.
  - [ ] **External provider/semantics acceptance belongs to future stages:** M21 only checks evidence and structure; it does not authenticate Opus receipts, verify semantic truth or authorize post-intent retries.
  > Выполнить: parse exact enum match/no_match/uncertain; evidence exact
  > content span/version; expected category UUID set; no invented timestamps,
  > not_evaluated ONLY system; errors technical not semantic no_match.
  > DoD: adversarial JSON/quotes/extra keys/partial output all fail closed.
  > FIX:11,12,13. QA:047..060,112..120.


### Фаза E — COST, СОГЛАСИЕ И ONLY-OPUS LIVE EXECUTION

- [ ] **M22 — OpenRouter exact Opus 5.5 catalog preflight** *(код [x], CI QA [ ]; [M22 runbook](SRE_M22_EXACT_OPUS_CATALOG_PREFLIGHT.md))*
  - [x] ~~`opus_catalog_preflight.py`: optional operator-triggered **GET only** to exact HTTPS `/api/v1/models/anthropic/claude-opus-5.5/endpoints`, no credentials or paid POST, redirects/non-JSON/malformed/oversized denied; serializable offline cached snapshot with SHA and UTC timestamp. External operator-approved SHA required, self-signed user JSON not authority; expiry <=900s and future skew <=30s.~~
  - [x] ~~Only exact `anthropic/claude-opus-5.5`, single operator-pinned revision/endpoint tag, no `latest`, model/provider auto fallback or ambiguous tag; endpoint status 0, both `response_format` and `structured_outputs`, context/output caps and five independently validated `Decimal` USD/token price fields. Missing model/JSON/rate or stale catalog fail-closed; no quote or consent implicitly granted.~~
  - [x] ~~`preflight_exact_opus_batch`: verifies COMPLETE SHA-sealed M20 requests/local batch/C9 coverage and exact model/version/catalog/limit/batch SHA against fresh M22 endpoint; returns stable preflight SHA and decimal rates **only for future M23 quote** with `quote_ready=False`, `dispatch_allowed=False`, `paid_calls=0`, `requires_true_token_check=True` and explicit no-fallback routing.~~
  - [x] ~~`tests/test_sre_opus_catalog_preflight.py`: offline GET mock, exact model/endpoint JSON cap, stale/future/spoofed catalog, SHA model/price mismatch, Decimal tamper and complete M20 batch gates. Linux+Windows SRE workflow, runbook. No new migration.~~
  - [ ] **M22 QA acceptance:** exact SHA SRE Linux+Windows+PG17, full Linux, Windows D1, Knowledge B workflows must actually finish `conclusion=success`; currently queued. Public GET page observed but **operator-authorized live catalog routing/account access not proven**.
  - [ ] **M23/M24 deferred:** exact provider token checks, final immutable money quote, budget reservation and SRE-specific owner consent; M22 is not authorization to spend or send D1 to Opus.
  > Выполнить: optional catalog fetch/cached snapshot/model capabilities &
  > prices at time; exact anthropic/claude-opus-5.5, no fallback.
  > DoD: unavailable model, missing JSON support or stale catalog blocks quote.
  > FIX:08,11. QA:061,062,066.

- [ ] **M23 — Final immutable quote ПОСЛЕ exact batch packing** *(код [x], CI QA [ ]; [M23 runbook](SRE_M23_FINAL_IMMUTABLE_QUOTE.md))*
  - [x] ~~`opus_final_quote.py`: exact project/content/C9/M20 request+batch SHA and fresh M22 exact Opus 5.5 endpoint/catalog/rates mandatory, only plain Decimal USD (reject float/negative/scientific/nonfinite), `ROUND_CEILING` 9 fractional digits, 0 minimum and explicit USD operator cap / maximum reserve. If calculated reserve exceeds cap — `quote_budget_exceeded`, no hidden batch shrinking, no paid provider POST.~~
  - [x] ~~No false token estimate from UTF-8: upper input bound is **entire pinned endpoint context minus output tokens and headroom** per request, all four possible input/cache rates **conservatively summed**, output completion rate added; output explicitly `token_count_exact=False`, account fees/price/real token validation required before send. Quote TTL <=300s and <= remaining trusted M22 catalog <=900s, floor to seconds to avoid expiry extension.~~
  - [x] ~~Forward-only `058_sre_live_opus_quotes.sql`: PostgreSQL17 quote with composite FK project/run/item, unique project+quote SHA, JSONB bound to price/manifest/model metadata, money `NUMERIC(24,9)`, immutable UPDATE trigger; DELETE allowed for privacy/retention + parent cascade (deleted quote cannot grant consent). Trusted `issue_live_opus_quote` stores quote only after real C9/D1/M12 read and SHA-validated M20 plan; `read_live_opus_quote` re-reads exact existing DB quote and current C9/D1/source rights, catalog and USD cap.~~
  - [x] ~~`tests/test_sre_opus_final_quote.py` deterministic Decimal/caps/expiry/catalog/model/manifest SHA tamper; `test_sre_opus_final_quote_schema.py` and actual `test_sre_offline_pg.py` project-owned approved C9+ D1 real PostgreSQL immutable/foreign/revoked/retention tests. Migration guard 058, next 059, Linux/Windows/PG17 CI wiring, full runbook.~~
  - [ ] **CI QA:** exact HEAD SRE Linux/Windows+PG17, full Linux, Windows D1, Knowledge B actual `conclusion=success`. Пока `queued` и локальный clone недоступен из-за DNS.
  - [ ] **Real paid readiness NOT completed:** exact actual Opus tokenizer/true token limits, selected-account pricing/taxes/extra fee verification, M24 user SRE consent, M25 atomic budget reserve and future fenced send; M23 quote is a *conditional conservative token-only upper bound*, NOT an invoice or consent.
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
| M13 | CODE COMMITTED / CI PENDING | [M13 runbook](SRE_M13_BATCH_COVERAGE.md); `intelligence/relevance/live_batch_manifest.py` deterministic per-video exact C9 pair manifests, UUID5 input SHA and idempotent row-locked batch registry. New `intelligence/migrations/055_sre_live_batch_coverage.sql`: project-owned composite FK and immutable membership/expected-pair rows, no synthetic model labels, database trigger rejects incomplete completed/partial item/batch/run updates; `tests/test_sre_live_batch_manifest.py` (100×20 and 2/3), `test_sre_live_coverage_schema.py`, `test_sre_offline_pg.py` PG17 genuine RTB-C9+D1, 2×3 coverage and concurrent replay; migration guard/tests updated next prefix 056, CI Linux Windows PG17. Commits `666d4e5`, `c50d738`, `66871cb`, `a3714a9`, `c8b1ebf`, `ec59155`, `0287a5c`. | CODE [x], QA QUEUED. Pair decisions remain not_evaluated/blocked pending provider-evidence/consent/ledger in future; no cost, merge or main edit. |
| M14 | CODE COMMITTED / CI PENDING | [M14 runbook](SRE_M14_LIVE_LEASE.md), `intelligence/relevance/live_lease.py`: atomic SKIP LOCKED claim deterministic (run,video,item) lock order; PostgreSQL DB-time lease, strict UUID token and monotone generation, CAS heartbeat/release, source revoked→paused_rights without token; claim only run queued/running, no consent override or dispatch. `tests/test_sre_live_lease.py` fake unit, real PostgreSQL17 race/expiry/release/revoke additions to `tests/test_sre_offline_pg.py` and CI Linux Windows PG17. Commits `0f531f2`, `5340841`, `c676e05`, `67c81bb`, `7664dc1`. | CODE [x], QA still QUEUED. No new migration, no provider, budget consent or main edit. |
| M15 | CODE COMMITTED / CI PENDING | [M15 runbook](SRE_M15_RECOVERY.md); additive `056_sre_live_dispatch_fences.sql` journal `sre_live_attempt_fences` (immutable tenant/item/batch/token/gen/intent time, no-delete, workspace-shared trigger). `live_recovery.py`: exclusive workspace→run→attempt ordering, cancel/pause invalidate leases, restart requires exact approved C9+D1/content+pipeline and returns only pending_consent, expired pre-dispatch reservation recoverable, POST-intent→UNKNOWN run+item/batch and never blind retry. M14 workspace-share locks and no auto-reclaim with any pending/possibly paid intent; M10 FSM updated. `tests/test_sre_live_recovery.py`, `tests/test_sre_live_recovery_schema.py`, `test_sre_live_lease.py`, real PG17 `test_sre_offline_pg.py` cancel/claim race and pre/post dispatch, migration guard fresh/main-only upgrade next 057, Linux/Windows/PG17 workflow. Commits `6916dcb`, `a1e5815`, `b7ab3be`, `1095136`, `1036868`, `c592f1a`, `938b83b`, `a3b06f9`. | CODE [x], QA [ ] (queued), no paid providers and no main merge. |
| M16 | CODE COMMITTED / REAL INFERENCE BLOCKED / CI PENDING | [M16 runbook](SRE_M16_LOCAL_EMBEDDING.md). `intelligence/relevance/local_embedding.py` implements real local ONNX CPU-only embedding inference requiring exact pinned model name/full immutable 40hex revision and two SHA256-protected local weights/tokenizer files, deterministic single-thread runtime, mask pooling, L2/NaN safeguards, no fake/cloud backend. `local_embedding_health.py` real-model CLI, optional `requirements-local-embedding.txt`, fast negative `tests/test_sre_local_embedding.py`, explicit actual semantic `tests/test_sre_local_embedding_real.py` and CI contract suite Linux+Windows. Code commits `cc50a8a`, `30f66a3`, `3c0f4cc`, `faf6d20`, `bf454f1`, `8481f20`, `1d7ab20`, `c40de3b`. | Implementation [x], QA [ ]; real pretrained model never supplied/executed, so real DoD BLOCKED even if fast unit tests later pass. No cloud, paid call or merge. |
| M17 | CODE COMMITTED / REAL SEMANTIC MODEL BLOCKED / CI PENDING | [M17 runbook](SRE_M17_ALL_LEAF_RANKING.md). `live_leaf_ranking.py`: H1/H2-authorized C9 full deep ancestry/fact semantic text; separate exclusion text/penalty and null when absent; full 1–48 leaf candidate retention even negative similarity, UUID deterministic ties; strict comparison against complete independent C9 approved leaf keys; trusted read-only PostgreSQL exact M12 run/item + C9 publication + D1 version/rights before real local SHA-pinned M16 ONNX. No threshold/drop/no_match, no provider POST or DB results. Unit `test_sre_live_leaf_ranking.py` (three approved and math 48 leaves, negative/ties/missing), genuine PG17 source/revocation foreign tenant negative `test_sre_offline_pg.py`, separate `test_sre_live_leaf_ranking_real.py` actual ONNX acceptance BLOCKED without vetted weights. Linux/Windows+PG17 CI wiring; commits `9bbe477`, `6e49762`, `af35f1b`, `fca35f6`, `00151a0`, `1e2bc5a`, `d11cce0`, `e15ca55`, `f7610b0`. | Code [x], real DoD [ ] BLOCKED, QA [ ] QUEUED. M18 next; main unchanged. |
| M18 | CODE COMMITTED / REAL ONNX RESTART BLOCKED / CI PENDING | [M18 runbook](SRE_M18_EMBEDDING_CACHE.md); `intelligence/relevance/local_embedding_cache.py` transactional PostgreSQL17 workspace-locked dedup and cache recheck: M12 exact item + live C9 H1/H2 + current D1/S3 rights/policy `FOR SHARE`, same checks before return, M16 actual SHA-pinned local model files even on hits. Immutable scoped `057_sre_local_embedding_cache.sql`: C9/D1 FKs, DELETE CASCADE source cleanup, unique project/model/taxonomy/context/video/version/policy/content/input SHA, finite numeric shape, ≤2MB/24h, no public grants. TTL bounded to D1, max 1000 entries/project, explicit project-content-policy scoped purge; M17 scoring factored for safe pure stored-vector reuse without mock production fallback. `test_sre_local_embedding_cache.py` pure math/fake DB, `test_sre_local_embedding_cache_schema.py`, actual PG17 `test_sre_offline_pg.py` FK/revoke/delete + separate operator-only genuine ONNX cold/hot-after-reconnect smoke; migration preflight next 058; workflow Linux/Windows/PG17. Implementation commits `1606a8b`, `d648d92`, `a0ee75a`, `fcfd698`, `dbcf2bb`, `559ec70`, `67eed00`, `f456786`, `1e60d87`, `96aca42`, `0c7453e`. | Code [x], QA [ ] QUEUED, REAL pretrained model/restart [ ] BLOCKED; no paid calls, no merge or main edits. |
| M19 | CODE COMMITTED / CI PENDING | [M19 runbook](SRE_M19_COMPLETE_C9_CONTEXT.md); `intelligence/relevance/live_classifier_context.py` strictly 1 packet per approved C9 leaf (all 11 fields for direction/branch/hypothesis; original approved sibling ids/name/definition/include/exclude; source-backed approved fact id/kind/text/source_quote/source_field/origin), stable exact C9 H1/H2 and D1 provenance/source-policy/content hashes, no rank/drop/truncation. Trusted M12 DB C9/D1 read port through M17; role-isolated static system text and JSON `user` untrusted content, explicit metadata_only absent transcript/scenes and multilingual, no search-query label shortcut, 128 KiB cap fail closed, `external_dispatch_allowed=False`, no provider or paid action. `tests/test_sre_live_classifier_context.py` negative injected system/JSON role, C9 tamper, full facts and replay, `test_sre_offline_pg.py` real H1/H2 C9 + D1 source revoke/cross project, Linux+Windows/PG17 workflow; no migration. Commits `e705ae1`, `c2b5e35`, `2a7b7c9`, `1658226`, `169bafb`. | Code [x], full QA [ ] QUEUED. M16–M18 real ONNX acceptance still independently BLOCKED. Next M20; main/other features untouched. |
| M20 | CODE COMMITTED / CI PENDING / PROVIDER TOKENS NOT VERIFIED | [M20 runbook](SRE_M20_OPUS_REQUEST_PACKER.md): `intelligence/relevance/opus_request_packer.py` deterministic pinned catalog/model Opus transport, genuine M19 complete C9 11-field hierarchy+siblings/facts in every chunk, single authorized M12/D1/C9 production entry, preserved all leaf UUIDs, exact source D1 text/offset/time reconstruction after safe split, SHA-chained requests and local work batches (16 requests/250000 bytes proxy), immutable no-provider/no-spend. Token accounting is conservative `UTF8 bytes(system+payload)+512` proxy, NOT real provider tokenization; `requires_provider_preflight=True`. M19 split-ready optional envelope for 128KiB+ media without changing default behavior. Pure `test_sre_opus_request_packer.py` long multilingual text, 130 measured segments, C9 tamper, model revisions and SHA; PG17 `test_sre_offline_pg.py` actual approved two-human C9, D1 source rights revoke and cross-project; CI Linux/Windows/PG17. No migration, paid calls or main/peer changes. Commits `63f90d6`, `9766b08`, `7612ec4`, `5a4b68e`, `b21fbd6`, `1934412`, `58e7abc`, `d6670d6`, `f7b2754`, `9681d60`, `5dbc66f`, `1f9b73e`, `5aca68d`. | Code [x], QA [ ] QUEUED, exact token count [ ] intentionally deferred to paid dispatch gates; M16–M18 real ONNX [ ] BLOCKED. Next M21. |
| M21 | CODE COMMITTED / CI PENDING | [M21 runbook](SRE_M21_OPUS_EVIDENCE_VERIFIER.md). `intelligence/relevance/opus_evidence_verifier.py`: exact JSON no markdown/duplicate fields/non-finite constants; enum `match/no_match/uncertain`, `not_evaluated` forbidden to model; exact M20 category UUID+request/manifest SHA, model/catalog pins and all request/batch/leaf/chunk coverage; strict D1 source `kind,index,Unicode offsets,verbatim quote` and observed timestamps, no fabricated quotes, technical errors do not become semantic `no_match`. Mixed complete partial decisions => `uncertain` review; missing/duplicate response => typed `incomplete_coverage`. Trusted LIVE entry re-reads current M12 approved C9 H1/H2 and D1 rights and rebuilds exact M20 SHA before verifying; neither provider receipts nor model semantic truth authenticated. `tests/test_sre_opus_evidence_verifier.py` adversarial suite, real PG17 `test_sre_offline_pg.py`, Linux+Windows+PG17 workflow. No migration/provider/spend/DB result writes. Commits `7172108`, `a3dd1b0`, `3c5d0f1`, `378f393`, `c967487`, `1ed98b0`, `e8f9a2e`. | Implementation [x], CI QA [ ] QUEUED; genuine M16–M18 pretrained ONNX [ ] BLOCKED. M22 next; main untouched. |
| M22 | CODE COMMITTED / CI PENDING / CATALOG UNPROVEN FOR LIVE ACCOUNT | [M22 runbook](SRE_M22_EXACT_OPUS_CATALOG_PREFLIGHT.md): `intelligence/relevance/opus_catalog_preflight.py` operator-only optional public HTTPS GET (zero credentials/paid POST) + offline SHA-pinned cached snapshot with UTC TTL<=900s. Exact `anthropic/claude-opus-5.5`, immutable model revision and SINGLE endpoint tag/status=0, no fallback, both JSON `response_format` and `structured_outputs`, context/output constraints and all five provider prices validated as strict Decimal USD/token strings. Missing model, missing JSON, stale snapshot, altered SHA/provider/price all typed blocked. `preflight_exact_opus_batch` binds precise M20 SHA-sealed all-C9 request/batch manifest and per-request bounds to model/catalog endpoint identity, no self-authorization of price, no token usage or permission granted. `test_sre_opus_catalog_preflight.py` offline mocked GET / stale/price/caps/endpoint/full M20 batch SHA tests, SRE Linux+Windows workflow, runbook. No SQL migration/paid/financial writes/main merge. Code commits `1693fb3`, `610f174`, `111bf86`, `e5b9b65`, `60f0d0f`, `6cc65ff`, `8fe69b6`. | Code [x], QA [ ] QUEUED, authenticated account endpoint/pricing [ ] not yet independently verified; actual immutable quote M23 and consent M24 TODO, real pretrained ONNX M16–M18 still BLOCKED. M23 next. |
| M23 | CODE COMMITTED / CI PENDING / ACCOUNT TOKEN FEES NOT VERIFIED | [M23 runbook](SRE_M23_FINAL_IMMUTABLE_QUOTE.md): `intelligence/relevance/opus_final_quote.py` validates exact complete M20 C9/D1 pack+batch SHA and selected pinned fresh M22 Opus 5.5 endpoint/catalog/Decimal prices BEFORE quote; worst-case token ceiling from full endpoint context minus max-output and 1024 headroom, all four possible input/cache rates summed, completion rate added, 9-place `ROUND_CEILING`, min USD 0 and explicit operator cap. Quote SHA ties project/publication/video/version/source SHA, model revision, request SHA, price/catalog SHA, UTC issue/expiry<=300sec. Lack of true token counting or account fees explicit `requires_exact_token_check_before_send=True`, `requires_account_price_and_extra_fee_check=True`, no consent or paid dispatch. `058_sre_live_opus_quotes.sql` immutable UPDATE trigger, `NUMERIC(24,9)`, composite project/run/item FK, unique project+quote hash; DELETE/cascade allowed for GDPR retention and quote invalidation. Trusted issued/read ports require real H1/H2 C9 and current M12 D1 rights plus exact M20 replan. Unit quote+schema and actual PG17 FK/immutable/revoke/privacy tests, required migration 058 next 059, CI Linux/Windows/PG17. Code commits `aeafc9a`, `6fa3b36`, `14accfa`, `e89f673`, `ea753d8`, `9825b4a`, `b85c0c9`, `4ce4b73`, `d5445d6`, `4c789f6`, `d449f41`, `94c06f1`, `be54c65`, `46feb2b`, `e960054`, `7625b44`, `5067e91`, `cad61f7`. | Code [x], CI QA [ ] QUEUED, no true Opus tokens/account fees or consent, M16–M18 real ONNX [ ] BLOCKED; no paid calls or main merge. M24 next. |
| M24 | CODE COMMITTED [x] / CI QA [ ] | [M24 runbook](SRE_M24_DEDICATED_SPENDING_CONSENT.md): `opus_consent.py`, forward-only `059_sre_live_opus_consents.sql`, unit/schema and PG17 C9/D1 owner grant/revoke tests; исходный HEAD M24 `2db1a0e`. | Полноценный host CSRF/authenticated click не проверен; CI не PASS; no ledger/POST. |
| M25 | CODE COMMITTED [x] / CI QA [ ] | [M25 runbook](SRE_M25_ATOMIC_BUDGET_LEDGER.md); forward-only `060_sre_live_budget_ledger.sql`, `opus_budget.py`, `test_sre_opus_budget.py`, `test_sre_opus_budget_schema.py`, PG17 approved C9/D1 reserve/release test; миграционный preflight/Windows/Linux workflow. | Финансовое исполнение закрыто: no provider POST, real account fees, actual spent settlement or human UI; CI PASS и конкурентные multi-connection измерения не подтверждены. M26 next. |
| M26 | CODE COMMITTED [x] / CI QA [ ] | [M26 runbook](SRE_M26_COMMITTED_DISPATCH_INTENT.md): `opus_dispatch_intent.py`, additive `061_sre_live_opus_dispatch_intents.sql`, M15+M25 durable same-transaction intent, exact sealed M20 request SHA/ordinal and batch SHA, M23 quote + M24 consent + budget/worker lease/rights checks. `tests/test_sre_opus_dispatch_intent.py`, schema test, PG17 real C9/D1 independent connection/replay crash-fence, Linux/Windows workflow. | No provider HTTP/M27 adapter, no verified Opus tokenizer/account extra fees, M28 settlement or M29 authenticated receipt/reconcile. Only one intent per run is intentionally allowed; code [x], test outcomes [ ] QUEUED/UNVERIFIED; no paid calls. M27 next. |
| M27 | CODE COMMITTED [x] / CI QA [ ] | [M27 runbook](SRE_M27_OPUS_TRANSPORT_FAKE.md): `opus_transport.py`, pinned `anthropic/claude-opus-5.5` / HTTPS / `provider.only` with strict fallback/parameter/ZDR, bounded timeout/body, private one-shot HTTPS primitive, explicitly blocked live send, zero-cost FakeOpusTransport, all-three-leaf C9→M20→fake→M21 unit test, real PG17 M26 cross-connection fake integration, Linux/Windows workflow. | Exact billable tokens, account fees, M28 settlement, M29 unknown-outcome one-shot/reconcile and all-request LIVE production still [ ]; full CI [ ] UNVERIFIED. Zero paid calls, no credential usage. Next M28. |
| M28 | CODE COMMITTED [x] / CI QA [ ] | [M28 runbook](SRE_M28_PROVIDER_RECEIPTS.md): `opus_receipt.py`, forward-only `062_sre_live_opus_receipts.sql`, отдельный read-only authenticated OpenRouter generation GET, exact Decimal provider `total_cost`/native token bounds, one receipt per M26 intent/gen/reserve, PG atomic M25 reserved→settled (actual spent + unused released), event journal; no results without full C9. Unit/schema/PG17 mocked-authenticated-generation scenarios in SRE CI. | Real live provider GET and tax/account-wide fees not validated; CI queued/unverified, no paid POST. M29 UNKNOWN reconciliation and true multi-request gates required; M16–M18 ONNX BLOCKED. |
| M29 | CODE COMMITTED [x] / CI QA [ ] / FULL DoD [ ] | [M29 runbook](SRE_M29_UNKNOWN_RECONCILIATION.md), `opus_reconciliation.py`, new forward-only `063_sre_live_opus_reconciliation.sql`, M28 authenticated known-generation settlement even with missing original completion, no-ID/404/overcap UNKNOWN retains original reserve, one-per-intent append-only SQL receipts matching exact generation and project, idempotent audit, source-pinned missing M20 ordinals read only; unit/schema/real PG17 C9+D1 simulated-generation regression, Linux/Windows workflow. Also bugfix M25 release rejects already settled account. | UNKNOWN receipt reconciliation code in place, but **multi-request send, one-shot live pre-send authorization, real tax/account fees and full C9 paid execution remain BLOCKED** (M26 unique per run, M25 one reserve per consent). No actual paid call; no CI PASS, final DoD [ ]. Next M30 coverage. |
| M30–M47 | TODO | См. этапы выше | Follow-on stages, not production-ready; M16–M18 pretrained ONNX acceptance separately BLOCKED. |

**M15 PostgreSQL CI blocker fixed in SRE branch:** ранее в `056_sre_live_dispatch_fences.sql` были незакрытый regex CHECK и дублированный `CREATE TABLE/FUNCTION`; workflow 40548967816 завершился FAIL при применении 056. Исправлено коммитом `cf95d8c4` (только непубликованная SRE-миграция 056). Независимый PASS новой миграционной цепочки ещё не получен. Старые checksum-pinned 016/017 неизменны.

**Правило merge:** DRAFT PR до запроса владельца. **Правило платного вызова:** без отдельного consent и budget никакого OpenRouter POST. Реально совместный E2E с внешними модулями не обещается, пока их ветки/контракты не проверены.

