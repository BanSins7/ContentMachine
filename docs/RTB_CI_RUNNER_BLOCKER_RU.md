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
