# RTB-FIX-11-A — автономность модуля, аудит diff и подготовка выпуска

**Рабочая ветка:** `feature/rtb-module-v2`, Draft PR [#32](https://github.com/BanSins7/ContentMachine/pull/32). **Дата:** 2026-10-27. **Статус: 11-A реализован по коду, реальный Linux CI/PG17 QA не выполнен; общий FIX-11 НЕ ГОТОВ.**

## Подтверждённый аудит исходников

GitHub compare `main...feature/rtb-module-v2` на этапе 11-A показал **257 commits ahead, 0 behind, mergeable=true** и **78 изменённых файлов** (показатели будут меняться после последующих коммитов). Ни один изменённый путь не находится внутри `intelligence/search_planner/**`, `intelligence/relevance/**`, Knowledge Extraction/B9, A8/Video Collector или других приватных каталогов модулей. Это source/diff review, **не git merge и не runtime QA**.

Общие существующие файлы RTB затронул минимально:
- `app.py`: два адаптера `rtb_v2_get`/`rtb_v2_post` с ранним выходом и регистрация только одного нового static asset `/taxonomy-deep-v2.js`. Старые методы Flat V1 / Deep V1 сохраняются.
- `site/index.html`: добавлены отдельная панель Deep V2 на уровне проекта и один script, существующие строки не удаляются.
- `site/projects.js`: добавлено событие `cm-taxonomy-deep-v2-project-open`, существующая логика проектов сохранена.
- `site/taxonomy-workspace.css`: добавлены отдельные классы `.tb-v2-*`, существующие legacy правила не удаляются.

Новые RTB-owned файлы: `intelligence/taxonomy_builder/{research_input,research_contracts_v2,deep_v2_*,publication_export_v2,qa_matrix,migration_audit,release_boundary}.py`, `intelligence/migrations/054..059_tb_deep_v2_*.sql`, `site/taxonomy-deep-v2.js`, тесты `tests/test_taxonomy_*.py`/`.cjs`, `docs/RTB_*.md`, RTB-only workflow.

## Реализованный fail-closed контроль границ

`intelligence/taxonomy_builder/release_boundary.py`:

- Берёт `git diff --name-only origin/main...<PR_HEAD_SHA>` из **полной** git истории (в GitHub Actions явно используется `github.event.pull_request.head.sha`, а не synthetic merge HEAD), собирает конкретные изменённые пути и сверяет их с жёстким RTB allowlist. Любое изменение приватных каталогов SP, SRE, B9, Project Source Intelligence, иных миграций и UI других модулей **запрещено**.
- Для четырёх общих файлов получает версию `git show origin/main:<path>`, сравнивает все legacy строки с HEAD с сохранением их порядка. У `app.py` допускает ровно одно расширение старого mapping — регистрацию RTB V2 static asset. Пропавшие или изменённые legacy строки блокируют выпуск.
- Проверяет строго однократные new hooks, обязательные шесть migrations 054–059 и присутствие обязательных RTB source/export/audit файлов.
- Проверяет `HEAD..origin/main`: при отставании ветки от актуального main отмечает обязательный повторный конфликтный аудит. **Не делает auto rebase и не меняет main.**
- Формирует JSON-отчёт `schema_version=rtb-release-boundary/v1` с base/head SHA, количеством изменений, найденными нарушениями, `source_isolation_passed`. Даже при source PASS поле `release_ready=false` — строгое разделение isolation и production release.

`tests/test_taxonomy_release_boundary.py`: 7 source-only mock-git регрессий — разрешённый независимый набор, изменение чужих модулей, удаление legacy GET, удаление legacy script/CSS, дублирование платного hook/отсутствие migration, path traversal и неправильная миграция.

CI Linux запускает их вместе с `release_boundary --base origin/main`; `actions/checkout@v4` для этого job переключён на `fetch-depth: 0`. JSON `reports/rtb_release_boundary.json` добавлен в Actions artifact. **GitHub Actions runner на момент создания пока не выдал execution logs.** Не называть test suite PASS.

```bash
# Из checkout feature/rtb-module-v2 с доступным origin/main
git fetch origin main
python -m unittest discover -s tests -p 'test_taxonomy_release_boundary.py' -v
python -m intelligence.taxonomy_builder.release_boundary \
  --base origin/main --json-out reports/rtb_release_boundary.json

# Прочие обязательные, отдельные QA
python -m intelligence.taxonomy_builder.migration_audit --check
python -m intelligence.taxonomy_builder.qa_matrix --check
# ТОЛЬКО на disposable PG17 и уже установленных миграциях:
python -m intelligence.taxonomy_builder.migration_audit --postgres
```

## Контракты интеграции без hidden зависимостей

- Source: verified `knowledge-snapshot/v1` + `knowledge-c-handoff/v1` от уже интегрированного B9, A8 `video-intelligence/v1`/project context. Ни один непроверенный video observation не становится продуктовым фактом; pinned SHA обязателен.
- RTB Deep V2: `rtb-research-input/v2`, отдельный immutable binding, signed quote, отдельное согласие на 2 paid прохода, H1/H2 decisions, до **двух** согласованных expansions, signed readiness/published V2 tree и 11-field V1 core.
- Export: `rtb-publication-export/v2` (14-field, A8 evidence & SHA) и `rtb-publication-export/v1` (11-field compatibility). GET и выбор экспорта **не вызывают** платного Opus.
- SRE bridge: отдельные immutable publication/SRE link, project-scoped deterministic B9 fact UUID reuse и проверка конфликтов. Не переписывается внутренняя реализация `intelligence/relevance/**`.
- SP: RTB предоставляет V1 adapter и готовый V2 export. **T28/T29 downstream — пока НЕ реализованы** внутри отдельного Search Planner consumer и остаются внешними blockers для полноценного RTB→SP V2 workflow.
- Legacy Flat V1 и Deep V1: независимые endpoint/SQL/UI и прежние 11 полей остаются. Привязки B9 snapshot нельзя подменять legacy `tb_fact_review_submissions`.

## Что блокирует перевод PR в Ready / интеграцию

- [ ] **FIX-10-D2:** РЕАЛЬНЫЙ GitHub `rtb-contract-linux`, `rtb-contract-windows`, `rtb-postgres-17`, без failures и с сохранёнными job/artifact IDs. Статус сейчас `queued`, нет валидного полного PASS.
- [ ] Clean bootstrap PostgreSQL17 с миграциями 001...059 и `migration_audit --postgres` (6 SHA / 13 tables / 27 enabled security triggers).
- [ ] Historical upgrade PostgreSQL17 с legacy Flat/Deep, B9/A8 утверждёнными записями и фактами; сравнение старых публикаций и provider-mock budget ledger до/после.
- [ ] Actual Node/browser regression (без Opus), mixed human-review T23, paid timeout/uncertain, два expansions, SRE bridge, V1+V2 export и весь T01–T36 с реальными exit code.
- [ ] T28/T29 SP V2 consumption/query planner — вести **в отдельной SP ветке**, не менять чужие файлы из RTB.
- [ ] FIX-09-C4 authenticated HTTP/browser acceptance, правильная server-side payment reconciliation при unknown cost, UX обращения с ошибками.
- [ ] **FIX-11-B:** release SHA freeze, review изменений относительно последнего main, исчерпывающий конфликтный и security audit, подписанные QA доказательства и перевод Draft PR в Ready только после их прохождения.

### План безопасной интеграции по запросу

1. Сохранить и проверить текущий SHA ветки RTB. Если актуальный `main` ушёл вперёд относительно **исходного SHA PR** (не временного merge-коммита), проверить diff/конфликты и выполнить управляемый rebase только с явным одобрением стратегии сохранения истории.
2. На одноразовой PG17 выполнить чистую установку `main` и затем RTB миграции; повторить для копии historical legacy данных. Запретить real provider API в тестах.
3. Проверить реальное QA: Linux, Windows, PG17, локальный browser, с нулевым списком P0/P1, и отдельно SP V2 handoff.
4. Только после этого заморозить release SHA, приложить отчёты, перевести PR в Ready. **Не выполнять merge в main, пока пользователь явно не попросит.**

**D1 / FIX-11-A: код существует, static review проведён, QA queued. FIX-11-B и весь FIX-11 открыты.**

### 2026-10-27 — учёт synthetic GitHub PR merge ref

GitHub Actions для `pull_request` может проверить временный merge-коммит, который уже включает свежий `main`, и тем самым скрыть отставание настоящей feature-ветки. Поэтому `release_boundary.py` использует отдельный `--head` (SHA исходной PR ветки), получает файлы через `git show HEAD_SHA:<path>` и `git ls-tree HEAD_SHA`, вычисляет diff и `rev-list HEAD_SHA..origin/main`. Workflow явно передаёт SHA из `github.event.pull_request.head.sha` либо `github.sha` для ручного запуска. Добавлена седьмая mock-git проверка `test_pr_source_sha_not_synthetic_merge_is_checked_against_main`: любое `behind_main>0` ставит `source_isolation_passed=false`.
