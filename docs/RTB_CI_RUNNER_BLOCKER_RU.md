# RTB — GitHub Actions queue / QA blocker (2026-10-26)

## Evidence from actual GitHub API

Repo: `BanSins7/ContentMachine`, PR [#32](https://github.com/BanSins7/ContentMachine/pull/32), branch `feature/rtb-module-v2`.

On commit `110ddd35cbcd53d910f2484fb8484f01c127d82f`:
- Windows D1 smoke: run **40299686256**, `queued`.
- ContentMachine checks: run **40299686274**, `queued`.
- RTB module contract regression: run **40299686277**, `queued`.
- Knowledge B Stage14 fast QA: run **40299686432**, `queued`.
- RTB Linux job **121494225049**, RTB PostgreSQL17 job **121494225311**, RTB Windows job **121494225369**: `queued`, `runner_name` absent.
- `fetch_workflow_job_steps(121494225049)` returned `steps: []`.

Subsequent GitHub commits showed the same behavior. On `138774253791d6dfab9f0fcd5690b98b48e8ee7f`, all four workflows, including preexisting B Stage14 and Windows D1 smoke, are also queued. **No test logs can exist until a job is picked up.** Do not call queued tests failed, passed or completed.

This is a GitHub Actions queue/scheduling limitation, not currently a demonstrated Python/SQL regression. Since even unrelated workflows are queued, creating another workflow or repeatedly pushing commits cannot reliably repair the runner-side issue.

## Remediation to verify as repository owner

1. Open [PR #32 checks](https://github.com/BanSins7/ContentMachine/pull/32/checks) or GitHub repository **Actions** and open the oldest queued workflow. Note whether GitHub displays a `Waiting for a runner`, Actions-disabled, billing/usage-limit or job approval message.
2. Repository **Settings → Actions → General**: verify actions and third-party actions are allowed; for forks/public contributions check whether first-time contributor jobs require manual approval. Do not loosen security settings indiscriminately.
3. Repository **Settings → Actions → Runners**: check runner availability, labels and whether GitHub-hosted Linux/Windows runners are enabled for this account and repository. This repository's RTB job targets `ubuntu-latest` and `windows-2025`.
4. Account or organization **Billing and licensing → Actions**: check available GitHub Actions minutes and any spending/usage limit; verify no account or organization-wide restrictions. Only an owner can resolve restrictions.
5. Once unblocked, open the latest commit on PR #32, run all 4 workflows and inspect actual red step output before changing code. Re-run failed jobs only **after** runs complete with failure; queued jobs cannot be treated as failed.
6. If hosted Actions are unavailable, clone the branch on a developer machine/CI with PostgreSQL17 and run the same commands below. Report exact output and exit status, not a claimed success from an unexecuted suite.

## Standalone local commands (never invoke OpenRouter)

```bash
git switch feature/rtb-module-v2
python -m pip install -r intelligence/requirements.txt
python -m compileall -q intelligence/taxonomy_builder
python -m unittest discover -s tests -p 'test_taxonomy_c_corrector_contracts.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_research_input.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_lifecycle.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_quote.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_validation.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_provider.py' -v
# Only when a real PostgreSQL17 test database is configured via CM_TEST_PG_DSN:
python -m intelligence.migrate
python -m unittest discover -s tests -p 'test_taxonomy_research_input_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_binding_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_ledger_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_quote_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_pass1_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_db.py' -v
```

**Do not disable money safety gates or mark QA PASS to bypass a blocked CI.**
Mark `[x]` + ~~strikethrough~~ when implementation is complete, but QA remains explicitly `QUEUED / NOT RUN` until executed with `success`.

## Обновление от 2026-10-26 — проверка последних тестов и SQL исправления

- Последний проверенный HEAD: `4bd40343fac593b57ab1d878fd969990cad025ae`; четыре workflow всё ещё `queued`:
  - `ContentMachine checks`: run **40304811271**.
  - `RTB module contract regression`: run **40304811044**.
  - `Knowledge B Stage14 fast QA`: run **40304811084**.
  - `Windows D1 smoke`: run **40304811028**.
- Найден и исправлен **реальный исходный дефект миграции 055**: два `LANGUAGE plpgsql AS $` / `END $;` заменены на корректные `$$` / `END $$;`; commit `cba603de9e205d73dfcce5cd99c14899e246a2ed`.
- Статическая независимая проверка содержимого ветки через GitHub API: SQL 054 — 1 парная PL/pgSQL-функция, SQL 055 — 6 пар, SQL 056 — 1 пара, нет одиночных `$`-разделителей. **Эта узкая статическая проверка прошла**; ни PG миграции, ни Python-интеграции физически не запускались.
- CI расширен тестами `test_taxonomy_deep_v2_sql_static.py` (3 сценария), `test_taxonomy_deep_v2_review.py` (9), `test_taxonomy_deep_v2_review_db.py` (6), `test_taxonomy_deep_v2_followup.py` (8). На реальных runners результат остаётся `NOT EXECUTED`.
- Для next chat: прежде чем утверждать о готовом FIX-04/FIX-05, дождаться CI, проверить PG17 migrate и H1 snapshot immutability; user-approved H1 API/UI и оплачиваемый Pass2 ещё не закончены.

## Обновление FIX-05-B (2026-10-26)

- HEAD `b88f3f2295976525454e57d0fb08d6cecafab334`: workflow RTB **40306281266**, ContentMachine checks **40306281209**, B Stage14 **40306281213**, Windows smoke **40306281199** — все `queued`, `conclusion=null`. Нет фактического тестового результата.
- В текущем локальном рабочем контейнере не найдены `psql`, `pg_ctl`, `psycopg` и `pglast`; полноценный PostgreSQL17 integration и SQL execution невозможны без установки инфраструктуры. `pytest` имеется, но GitHub-исходники ветки здесь не клонированы. Это не тестовый FAIL.
- Новые `tests/test_taxonomy_deep_v2_pass2_db.py` (11 mocked PG17 scenarios) подключены к `.github/workflows/rtb-module-contracts.yml`. Дополнительно регрессии на quote TTL после долгого Human Review1 и на единственность вызова при конкуренции.
- Следующий чат: сперва проверь Actions runner и настоящие логи на самом новом HEAD. При первой возможности разверни тестовый PostgreSQL17, затем исправляй точные failing cases без подмены результата.

## 2026-10-26 — QA checkpoint после FIX-05-C (expansion)

- Проверен HEAD `6249da7791a21c125196a8c35095f40a6fc18b1b`: RTB **40309114888**, ContentMachine checks **40309114918**, Knowledge B Stage14 **40309114943**, Windows D1 **40309114889** — все `queued`, без `conclusion`.
- Созданы `intelligence/migrations/057_tb_deep_v2_expansion_consent.sql`, `deep_v2_expansion.py`, `test_taxonomy_deep_v2_expansion.py` (4 offline) и `test_taxonomy_deep_v2_expansion_db.py` (12 PostgreSQL17 mock). CI YAML обновлён, реальных ответов OpenRouter не отправляли.
- Для SQL 054/055/056/057 через GitHub API проверен статический баланс PL/pgSQL `$$`: 1/6/1/5 правильных пар; функция 057 объявляется после используемых таблиц. Это не PostgreSQL execution QA.
- **NEXT ACTION:** владелец репозитория проверяет Settings → Actions/Runners и Actions billing/quotas; после выдачи runner запускаются все проверки, затем исправляются любые реальные traceback. Не ставить `QA: PASS` без фактического run completion.

## 2026-10-26 — QA checkpoint FIX-06 Search Readiness / Human Review2

- Проверенный HEAD `14fb45288b81425e1652c966ea590114143ec0bf`, PR #32 Draft, main untouched: RTB workflow **40311703675**, ContentMachine checks **40311703673**, Windows smoke **40311703860**, B Stage14 **40311703844**, все `queued`, `conclusion=null`. Ничего из вновь добавленных тестов на runner не выполнялось.
- Реализация: `deep_v2_readiness.py` (source-bound signed gate), `058_tb_deep_v2_search_readiness.sql` (immutability + last successfully charged paid attempt + H2 only with passing report), `deep_v2_review2_repository.py` (one transaction edited H2 + rechecked gate, approved НЕ published).
- Тесты: `test_taxonomy_deep_v2_readiness.py` 10 offline cases, `test_taxonomy_deep_v2_review2_db.py` 9 PostgreSQL17 fake provider cases, `test_taxonomy_deep_v2_sql_static.py` extends SQL 058. Все добавлены в Linux/Windows/PG RTB workflow. При нулевом runner executions статусы остаются `QA: QUEUED/NOT EXECUTED`.
- Отдельно выполненная статическая проверка строк SQL через GitHub API: PL/pgSQL `$$` delimiters 054/055/056/057/058 имеют корректные 1/6/1/5/2 пары. Это **не** проверка выполнения миграций на PostgreSQL.
- Следующему чату: сначала сверить свежие GitHub Actions logs после назначения runner; затем исправить все фактические failing cases и продолжить FIX-07 atomic publication + SRE bridge. В рамках FIX-09 требуется auth-aware UI для human reviews; не следует выпускать реальный paid API без подтверждённого согласия.

## 2026-10-26 — CHECKPOINT FIX-07: автономная публикация и SRE

- Проверенный HEAD `4fce71fb42f44ba654d8c12fcf2b92cc174f7b41`: RTB module **40314626511**, Windows D1 **40314626494**, ContentMachine checks **40314626569**, Knowledge B Stage14 **40314626505** — все `queued` и `conclusion=null`. Никакие PostgreSQL17 tests на этом HEAD не прошли и не упали — нет исполнения.
- Ветка сейчас 154 коммита впереди `main`, 0 позади, Draft PR #32; `main` не менялся.
- RTB FIX-07: SQL `059_tb_deep_v2_publication_sre.sql`, `deep_v2_sre_bridge.py`, `deep_v2_publication.py`, 8 pure tests, 11 PG17 mock tests, SQL059 static checks. Contract: only `approved` H2, 14→11 exact nodes, SRE stable B9 UUID reused, atomic whole-transaction rollback, no provider.
- Source-inspection check SQL 054–059 has balanced `$$` delimiter pairs 1/6/1/5/2/2. This **does not prove successful PostgreSQL migration or unit tests**.
- Next action: repository owner resolves runner/Actions queue (see earlier guidance); then verify migration 059 and public GET's SRE checksum/read; fix any actual failing traceback. No `QA PASS` without `conclusion=success`.

## 2026-10-26 — CHECKPOINT FIX-08 read-only Search Planner / SRE exports

- Проверен HEAD `7a5a3a0ac27a854872668aff378568d139e3ea25`, Draft PR #32, `mergeable=true`, +164 commits против `main`, 0 behind. `intelligence/search_planner/**` и `intelligence/relevance/**` НЕ изменены в diff.
- GitHub Actions RTB contract regression **40316710485**, ContentMachine checks **40316710569**, Windows D1 smoke **40316710493**, Knowledge B Stage14 **40316710477**: `status=queued`, `conclusion=null` у всех. Нет фактических pytest/unittest/PG результатов. Блокирующая зависимость: доступные runners / очередь Actions, не подтверждённая программная ошибка.
- Новый `intelligence/taxonomy_builder/publication_export_v2.py`: explicit `rtb-publication-export/v1` exact 11 и `rtb-publication-export/v2` full 14+A original observations, Search Planner V1 contract adapter, read-only SRE approved version pointer. Только server-side reviewer auth и публикации `published`, B9/A8 original replay, SHA.
- `tests/test_taxonomy_publication_export_v2.py`: 14 pure fixture tests + реальные существующие `Search Planner validate_published_snapshot/build_hypothesis_contexts`; `tests/test_taxonomy_publication_export_v2_db.py`: 8 PG17 mock fixtures. Workflow RTB Linux/Windows/PG jobs обновлены.
- Локально проверено: `git ls-remote https://github.com/BanSins7/ContentMachine.git` -> `Could not resolve host: github.com`. Нет `psycopg` и `psql`; `pytest` существует, но репозиторий не клонирован. Полноценные тесты отсюда НЕ запускались.
- Next: fix actual failing cases only after runners are available; proceed FIX-09 user-facing authenticated UI while keeping project isolation, no-cost GET, explicit paid consent, and no changes to SP/SRE code. Не писать "QA passed", пока статус действительно не станет success.

## 2026-10-26 — FIX-09 HTTP/UX контроль

- Проверенный HEAD `79b62f705eeab01a9892a59e419994d5f6fae769`, Draft PR #32, `mergeable=true`, 190 коммитов впереди `main`, 0 behind; diff **не содержит изменений** `intelligence/search_planner/**` и `intelligence/relevance/**`.
- GitHub Actions: RTB module **40320743838**, ContentMachine checks **40320743816**, Knowledge B Stage14 **40320743830**, Windows D1 **40320743713** — все `queued`, `conclusion=null`. Тесты **не исполнялись**, поэтому ни PASS, ни FAIL заявлять нельзя.
- Реализовано FIX-09-A/B: `deep_v2_http.py` и минимальные hooks `app.py`, project-details B9 entry `site/taxonomy-deep-v2.js` + `site/index.html`, scoped CSS и единственный event hook `site/projects.js`. 14 offline Python HTTP security tests + mocked Node UI full two-pass/reviews/publish flow; новые Linux/Windows steps в RTB CI.
- Локально через JS парсер статически успешно разобраны `site/taxonomy-deep-v2.js`, `tests/test_taxonomy_deep_v2_ui.cjs`, `site/projects.js` и неизменённый legacy `site/taxonomy-deep.js`. Это **только проверка синтаксиса**, не реальное исполнение UI-тестов или PostgreSQL.
- **Следующий чат:** проверить актуальные CI job logs и тесты; затем FIX-09-C отдельный удобный редактор 14 полей, UI errors/reload/2x expansion, B9-authenticated PG HTTP E2E. Не закрывать весь FIX-09 и не выпускать приложение наружу без настоящей session authentication.

## 2026-10-26 — FIX-09-C2 two-expansion + H1 form QA

- HEAD на момент проверки: `d5945f3cb7847c7fd1108379dce3362c519252d3` (PR #32 Draft, mergeable, 208 коммитов впереди main, 0 позади; Search Planner/relevance implementation files не изменены).
- GitHub Actions: RTB module **40325805973**, Windows D1 **40325805975**, ContentMachine checks **40325805999**, Knowledge B Stage14 **40325806016** — все `status=queued`, `conclusion=null`. RTB module имеет все три job в queued; назначенного runner и логов нет, это НЕ PASS и НЕ подтверждённый FAIL.
- В контроллере `taxonomy-deep-v2.js` реализовано редактирование 13 отдельных полей + immutable ID; в JS-моке Human Review1 возвращает 14-field edit, некорректный JSON source_fact_ids блокируется до HTTP, 2 expansions получают разные quote UUID, quote SHA и consent UUID. UI скрывает попытку №3 при count≥2, **также** fail-closed когда count отсутствует; даже если сервер ошибочно вернул expand_pending. Текст запрета остаётся в run status после GET.
- Изолированный `functions.exec` JavaScript V8 emulator действительно исполнил полный browser-controller mock из `tests/test_taxonomy_deep_v2_ui.cjs`; вывод: `RTB Deep V2 B9 UI mock: H1/H2 + 2 separate expansions OK`, без ошибок. Это **не** исполнение `node` на штатной CI машине, не полноценные frontend/browser e2e, не SQL PG17 PASS.
- PG `test_taxonomy_deep_v2_expansion_db.py::test_two_paid_expansions_and_never_a_third` расширен проверкой persist+GET `expansion_count=0→1→2` и независимости quote SHA / consent UUID. Выполнение PostgreSQL не подтверждено.
- Следующие действия: получить работающие hosted runners, выполнить `node tests/test_taxonomy_deep_v2_ui.cjs`, миграции и PG17 `test_taxonomy_deep_v2_expansion_db.py`. Исправить фактические ошибки, если возникнут. Далее FIX-09-C3 advanced tree review / retry/error/uncertain / HTTP real PG E2E. Не изменять `main` без явной команды.

## 2026-10-26 — FIX-09-C3: аварийный HTTP spend hold / H1 merge/reparent / DB dispatcher

- HEAD при проверке: `890204c46adebfc90972cbcf004d311f26fb1906`, PR #32 Draft, mergeable, 220 коммитов впереди `main`, 0 behind. Файлы `intelligence/search_planner/**` и `intelligence/relevance/**` в diff НЕ менялись.
- GitHub runs: RTB module **40329037832**, Windows D1 **40329037532**, ContentMachine checks **40329037598**, B Stage14 **40329037585** — `queued/conclusion=null`. В RTB module конкретно jobs `rtb-contract-linux`, `rtb-contract-windows`, `rtb-postgres-17` — **все queued**, реальных выводов нет.
- FIX-09-C3: `site/taxonomy-deep-v2.js` дополняет browser-only route atomicity: persistent per-project/run `paidHold` до достоверной смены paid stage, `uncertain` и тайм-аут не запускают повторный Opus POST; блокировка переживает смену проекта и повторное открытие run. Исправлено зависание `busy` после смены проекта с pending request.
- H2 review только approve для авторизованных, одобренных H1 узлов, fetched из отдельного backend `GET review1`; parent/reparent dropdown и merge-target dropdown, локальная проверка DAG ссылок / source IDs до POST; PostgreSQL по-прежнему authoritative.
- Expansion `revoke` отправляет явный `consent_id` и удаляет stale `Оплатить Expansion` кнопку после ответа. Бухгалтерский расход уже отправленного запроса не обнуляется.
- `tests/test_taxonomy_deep_v2_ui.cjs`: симулированный браузерный тест из реально загруженных в GitHub HTML/JS файлов **исполнен** в V8, `exitCode=0`, stdout: `RTB Deep V2 B9 UI mock: H1/H2 + 2 separate expansions + uncertain/revoke OK`. Подтверждено: два разных Expansion quote/consent, тайм-аут paid POST, защита reload/uncertain, неверный merge до POST, reparent exact 14 fields, H1 locks, отзыв бюджета. **НЕ** равно Node/browser CI.
- Новый `tests/test_taxonomy_deep_v2_http_db.py` (5 PG17 scenarios using real connection_factory and mocked provider): add CI step `rtb-postgres-17`, статически source reviewed, **PG17 NOT EXECUTED**. Не утверждать DB PASS.
- Невыполненные Release gates: полноценный интерактивный браузерный тест, реальная проверка денег/reconciliation у provider и DB migrations, T01–T36, authentic multi-user sessions if ever externally accessible. Продолжать FIX-10 или FIX-09-C4 без изменения `main`.

## 2026-10-26 — FIX-10-A/B: честное покрытие T01–T36 vs queued runners

- Проверенный HEAD до этой записи `7125842929fa749bf8029b0a292eaf7ebcbc8070`, PR #32 `draft`, `mergeable=true`, 233 коммита впереди `main`, 0 behind. `intelligence/relevance/**` и `intelligence/search_planner/**` не менялись.
- GitHub Actions: RTB contract regression **40367249734**, ContentMachine checks **40367249745**, Windows D1 **40367249919**, B Stage14 fast QA **40367249742** — все `status=queued, conclusion=null`. Runner-based Python, Node, PG17 migrations до сих пор **не выполнялись** на этом HEAD. Не называть эти задания `passed` и не выдавать source-method checks за исполненные тесты.
- В `intelligence/taxonomy_builder/qa_matrix.py` реестр 36 source requirements (§13): AST-style GitHub source inspection обнаружила **все 36 привязанных unittest-методов в 16 файлах** после исправления неверного имени Search Planner query validation. Классификация **32 mapped**, 2 partial (`T08` frozen storage mutation, `T23` full positive UI review), 2 downstream Search Planner gaps (`T28/T29`). Это static coverage и ревизия ссылок **не runtime PASS**.
- Новые negative cases T05 (B9/A invalid schema), T06 (hostile source instructions stay in untrusted user prompt only), T12 (valid hypotheses with source_observation_ids=[]); 5 internal matrix contract cases; ещё не исполнялись на настоящем Python CI runner.
- RTB workflow расширен отдельными Linux/Windows fake Search Planner V1 и legacy UI cjs regression suites, PG17 historical Flat V1 run/pipeline suites. Artifact `reports/rtb_t01_t36.json` может содержать только статические ссылки, НЕ подменяет actual process exit codes.
- Проверка внешней среды: `git ls-remote` не работает — `Could not resolve host github.com`. В локальном Python **psycopg отсутствует**, хотя pytest/yaml/node установлены. Полную проектную среду выполнить здесь невозможно.
- Следующему чату: в первую очередь получить Actions runner execution/logs, исправлять только реальные failures; затем T08/T23 и координация с отдельной SP веткой для T28/T29. FIX-10-C/D остаются открытыми. Release/PR Ready только после честной CI acceptance, не только AST.

## 2026-10-26 — FIX-10-C: T08/T23 код готов, QA runner до сих пор не исполняет

- Проверенный HEAD до добавления этой заметки: `2105494eedeee8734138847b448f1b87aaa9e0f8`; PR #32 Draft, `mergeable=true`, 244 коммита впереди `main`, 0 behind; ни одного изменения `intelligence/search_planner/**` или `intelligence/relevance/**`.
- Запуски на HEAD: RTB contract **40390146211**, Windows D1 **40390146063**, ContentMachine checks **40390146323**, B Stage14 **40390146596**. **Все `status=queued / conclusion=null`**.
- RTB jobs: Windows **121808702720**, PostgreSQL17 **121808703143**, Linux **121808703335** — **все queued**, `started_at=null`, назначенного runner нет. То есть сведения о `test failure` отсутствуют: тесты не успели исполниться.
- **T08:** `test_taxonomy_deep_v2_binding_db.py::test_real_b9_storage_mutation_refused_after_binding_pin` выполняет реальный UPDATE B9 approved immutable snapshot и C handoff после создания pin (оба должны отклоняться PostgreSQL immutable triggers), сверяет оригинальный replay digest и ровно один binding. Код создан, **PG17 не прогонялся**.
- **T23:** `test_taxonomy_deep_v2_review.py::test_compound_positive_add_edit_merge_reject_preserves_b9_a8` + `test_taxonomy_deep_v2_review_db.py::test_positive_add_edit_reject_persisted_review1_no_new_payment` + положительный UI mock на все пять `approve/edit/merge/reject/add`. Mock **выполнен** в изолированном JavaScript V8 harness: `RTB Deep V2 B9 UI mock: H1/H2 + 2 expansions + uncertain/revoke + positive T23 OK`, `exitCode=0`. Но Python/DB и штатный GitHub Node runner **не выполнялись**.
- T01–T36 реестр: **34 mapped / 0 partial / 2 external SP T28/T29**. Это `source test coverage`, а не runtime `34 PASS`. Релизный `--strict` намеренно fail-closed до окончания SP V2 consumer integration и фактических QA runs.
- Контейнерная среда: repo не клонирован, доступного локального `psycopg` нет; только исходная текстовая спецификация. GitHub clone/DNS ранее блокировался. До возобновления Actions нельзя подтвердить все изменения на чистом PostgreSQL17; не закрывать FIX-10-D/11 и не переводить PR в Ready.

## 2026-10-27 — FIX-10-D1 установленное PG17 окружение: код аудита готов, runner НЕ СТАРТОВАЛ

- Текущий проверенный HEAD на момент записи: `11a4db39496449106eab3de09bb5d389597140c3`, Draft PR #32, mergeable=true, **252 ahead / 0 behind main**. В diff нет изменений `intelligence/search_planner/**` и `intelligence/relevance/**`.
- Workflow runs на HEAD: RTB module **40454249807**, Windows D1 smoke **40454249825**, ContentMachine checks **40454249819**, Knowledge B Stage14 QA **40454249858**. Все `queued`, `conclusion=null`.
- RTB job IDs: `rtb-postgres-17` **122023767297**, `rtb-contract-windows` **122023767577**, `rtb-contract-linux` **122023767635**. Все `status=queued`, `started_at=null`, `runner=null`. **Никаких реальных unit tests, миграций или Node browser mock в Actions на HEAD не выполнено**. No failed job available for retry. Блокировка находится в GitHub job scheduling/runners, а не доказанной ошибке кода.
- FIX-10-D1 [x] **CODE COMPLETE**: `migration_audit.py` (`--check`, `--postgres`, read-only), `test_taxonomy_deep_v2_migration_audit.py` (6 fail-closed static/mocked-PG cases), Linux/Windows preflight и PostgreSQL17 **post-migration** установленного schema_migrations SHA плюс enabled triggers. `pull_request.paths` теперь включает RTB-owned SQL 054–059 и web project entry.
- По загруженным GitHub SQL исходникам 054–059 прямо проверены **13 CREATE TABLE** и **27 CREATE TRIGGER**; найдено **0 операций ALTER/CREATE/DROP/TRUNCATE чужих таблиц**. PostgreSQL runtime не выполнялся — источник доказательства static source scan, НЕ live installed schema.
- После появления runner: проверить конкретные записи `rtb-postgres-17` начиная с `Apply all PostgreSQL17 migrations` и `FIX-10-D audit ... enabled PG17 security triggers`. JSON artifact `rtb-v2-postgres17-installed-schema-audit` будет содержать реальные SHA/guard inventory либо конкретное отсутствие; нельзя объявлять PG PASS, пока нет `conclusion=success` и соответствующего лога.
- **Не** отмечать полный FIX-10/11 завершённым; статус **D2 [ ] / runner QUEUED**. Следующие инженеры получают [RTB_FIX10D_MIGRATION_AUDIT_RU.md](RTB_FIX10D_MIGRATION_AUDIT_RU.md), `RTB_IMPLEMENTATION_PROGRESS.md`. Не вносить изменения в main и не запускать live Opus.

## 2026-10-27 — обнаружена массовая очередь GitHub Actions, добавлена RTB-only concurrency

- GitHub REST API `GET /repos/BanSins7/ContentMachine/actions/runs?status=queued&per_page=1` вернул `total_count=2500` queued runs (возможно ограничение выдаваемого счётчика при высокой нагрузке), `status=in_progress` — `total_count=19`; репозиторий **public**, workflow включены, новые runs создаются. Это обоснованное указание на огромный backlog, но **не подтверждение** конкретной root cause для каждого job. Не путать с TEST FAIL.
- RTB workflow `.github/workflows/rtb-module-contracts.yml` теперь имеет **per-PR `concurrency.group: rtb-module-${{ github.event.pull_request.number || github.ref }}`**, `cancel-in-progress: true`. Это не меняет другие ветки и workflows. Новые пуши в текущий RTB PR заменяют более старые pending/running RTB contract runs вместо накопления одинаковых проверок. **Ранее созданные runs, не состоявшие в этой concurrency group, GitHub автоматически не удалит**.
- По публичным инструкциям GitHub при таком массовом backlog у владельца репозитория имеет смысл проверить: Settings → Actions → Runners (active jobs/concurrency), Settings → Actions → General (доступность запуска), Settings → Billing and licensing → Actions (лимиты, если применимо), статус GitHub Actions и долговременные stuck runs. Поскольку репозиторий публичный, стандартные hosted runner minutes GitHub Actions в обычном случае бесплатны; мы **не утверждаем** наличие billing incident.
- GitHub repository-wide backlog нельзя безопасно сокращать из узкой RTB ветки: не отменять запускаемые jobs других модулей и не редактировать их CI без отдельного указания. Никакого POST `cancel` к чужим runs не производилось.
- FIX-10-D1 code done; real PG17/Node/Python proof по-прежнему **не получен**. Когда runner начнёт выполнять последний RTB PR SHA, снять точные PASS/FAIL, PG17 schema audit artifact и исправить ошибки; до этого FIX-10-D2 [ ] / FIX-11 [ ].
