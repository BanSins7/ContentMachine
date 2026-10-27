# RTB FIX-11-B1 — Проверяемое свидетельство выполнения GitHub Actions

**Ветка** `feature/rtb-module-v2`, **PR #32**: `https://github.com/BanSins7/ContentMachine/pull/32`. Дата: 2026-10-27. **Статус подэтапа B1: код реализован; исполнение Python в штатном GitHub runner НЕ подтверждено**. FIX-11-B целиком, FIX-10-D2 и право на merge остаются **НЕ ВЫПОЛНЕНЫ**.

## Зачем добавлен этот контроль

Ранее GitHub Actions были `queued` по RTB, Windows, ContentMachine checks и Knowledge B Stage14; никакой общий dashboard не доказывал, что именно **текущий SHA feature-ветки** успешно проверен. Более старый зелёный run, пропущенный тест (`skipped`), отменённая job (`cancelled`) или PR synthetic merge SHA **не являются** CI доказательством релизной готовности.

Добавлен `intelligence/taxonomy_builder/release_ci_evidence.py`. Он **read-only** вызывает GitHub REST API, а не принимает произвольный JSON с утверждением «passed». Сначала заново получает PR metadata, `head.sha`, текущий `main`, запуски workflow с фильтром `head_sha`, затем все jobs по каждой требуемой проверке и сравнивает точные версии.

**Четыре обязательных workflow**:
1. `RTB module contract regression`
2. `ContentMachine checks`
3. `Windows D1 smoke`
4. `Knowledge B Stage14 fast QA`

В каждом обязательном workflow берётся **последняя попытка** именно этого `head_sha` и `event=pull_request`. Запуск обязан иметь `status=completed`, `conclusion=success`. Каждая job обязана быть `completed/success` и принадлежать тому же `run_id`. Отсутствие нужной job, чужой SHA, отсутствие pagination-данных или отставание feature branch от `main` дают FAIL. Инструмент не отменяет, не перезапускает и не создаёт workflow; никакой API POST.

Текущую ветку сравнивает с актуальным `main`, не с временным merged pull request SHA. GitHub API ошибки / rate limit / отсутствие доступа приводят к fail-closed.

## Как выполняется проверка

```bash
# С реальным доступом к api.github.com — только read-only REST GET.
# Для приватного репозитория/лимитов API можно задать GITHUB_TOKEN
# с минимальными read-only правами на actions, contents и pull requests.
python -m intelligence.taxonomy_builder.release_ci_evidence \
  --repo BanSins7/ContentMachine --pr 32 \
  --json-out reports/rtb_release_ci_evidence.json

# Отдельные no-network регрессии локальной логики:
python -m unittest discover -s tests -p 'test_taxonomy_release_ci_evidence.py' -v
```

В `tests/test_taxonomy_release_ci_evidence.py` семь случаев: ровно четыре успешных current-head workflow, pending/queued/skipped/cancelled/failure, старый зелёный run при новом failed, foreign push/head, неправильная `run_id` в job, feature behind latest main, read-only API маршрут и защита от неполной paginated выдачи. Эти **offline** тесты включены в Linux/Windows RTB CI и в curated `qa_matrix.SAFE_OFFLINE`.

**Важно:** даже при `observed_workflow_success=true` JSON **всегда содержит** `release_ready=false`, `manual_release_approval_required=true`, `rtb_sp_v2_t28_t29_verified=false`. Это не программная лицензия на merge: остаются T28/T29 внутри самостоятельного Search Planner, historical PG17 upgrade, стоимость/неопределённые provider charge, фактический проверенный SHA релизного кандидата, QA acceptance и отдельное явное решение пользователя.

## Дополнительный реальный дефект безопасности аудита миграций, устранённый в этой стадии

Аудитор `migration_audit.py::verify_postgres` прежде проверял наличие каждого имени защитного триггера на **любой таблице из соответствующей миграции**. Ошибочная привязка immutable/insert guard к соседней RTB таблице оставляла исходную таблицу незащищённой, но могла давать ложный PASS.

Теперь статический SQL preflight разбирает `CREATE TRIGGER ... ON <exact_table>` и запрещает привязку к не-RTB/внешней таблице. Read-only PG17 каталог сверяется с **каждой точной SQL-декларированной парой (таблица, триггер)**. Добавлены ещё два негативных теста в `test_taxonomy_deep_v2_migration_audit.py`: misbound trigger на соседнюю RTB таблицу и SQL trigger на `knowledge_approved_snapshots`. Итого 8 статических/fake-PG тестов (раньше 6).

**Проверено по загруженному source SQL:** для шести миграций 054–059 существуют 27 деклараций `CREATE TRIGGER` с однозначными целевыми RTB таблицами; наличие включённых триггеров в реальной PostgreSQL17 остаётся неподтверждённым до запуска CI.

## Remaining gates: не ставить release ready

- [x] ~~FIX-11-B1: код read-only GitHub CI attestation и exact 27 trigger/table guard validation, offline adversarial tests и интеграция offline Linux/Windows~~ — **по коду сделано; GitHub Python тесты в очереди**.
- [ ] FIX-11-B2: реальный `rtb-contract-linux`, `rtb-contract-windows`, `rtb-postgres-17` и все четыре обязательных `completed/success` на **конкретном current HEAD**; реальные outputs, artifacts, SHA и задачи `test_taxonomy_release_ci_evidence.py`. **BLOCKED: runners queued**.
- [ ] FIX-11-B3: clean PG17 bootstrap/historical upgrade на legacy данных, реальный HTTP/browser full acceptance, no-cost provider mocks, T28/T29 Search Planner V2 integration и пользовательское решение о merge. **НЕ ЗАВЕРШЁН**.

Держать PR #32 Draft, `main` не изменять. Отдельно фиксировать `код реализован / тесты не исполнялись` в общем журнале.
