# RTB FIX-10-D1 — Проверяемый preflight миграций и установленной схемы PostgreSQL 17

**Ветка:** `feature/rtb-module-v2`, Draft PR #32. **Статус:** реализация D1 **выполнена по коду**; **D2 (реальный прогон PostgreSQL17/Linux/Windows и анализ FAIL) НЕ ВЫПОЛНЕН — GitHub Actions queued**. Это не релизное подтверждение и не разрешение на merge или платные вызовы.

## Что именно проверяем

Старый `python -m intelligence.migrate` действительно последовательно выполняет все SQL миграции под PG advisory transaction lock и записывает SHA в `schema_migrations`. Но до D1 workflow проверял только баланс `$$` и отдельные SQL guards. Отсутствовала верификация после установки, что весь RTB-owned каталог в чистой PG17 существует с точным SHA и включёнными охранными триггерами.

Добавлен `intelligence/taxonomy_builder/migration_audit.py` с двумя независимыми режимами:

| Проверка | `--check` (без БД) | `--postgres` (тестовая PostgreSQL17) |
|---|---|---|
| Шесть миграций 054–059 в правильном порядке | Да | Да |
| Наличие предшественников B9 052 и Flat B Stage14 053 | Да, локальный файл | Да, также установленный `schema_migrations` |
| Изолированные 13 таблиц `tb_deep_v2_*` | Точное наличие `CREATE TABLE` в SQL | Реальное наличие всех 13 таблиц в каталоге |
| 27 RTB-owned охранных триггеров | Точное наличие `CREATE TRIGGER` в SQL | Все присутствуют и включены `tgenabled = O/A` на *правильных* RTB таблицах |
| Изменение чужих SQL таблиц (B9, SP, SRE, legacy RTB) | Отклонение `CREATE TABLE` / `ALTER TABLE` для не-`tb_deep_v2_*`, запрет `DROP/TRUNCATE TABLE` | Истинный SQL применён до read-only проверки; метаданные и SHA сверены |
| Целостность миграций | Точный SHA-256 каждого SQL файла | Проверка всех 6 checksum против установленной `schema_migrations` |
| Платные модели / отправка запросов | 0 | 0 |
| Изменение БД при аудите | 0 | 0: `SET TRANSACTION READ ONLY` |

**Важно:** проверка исходного SQL — *ограниченный контрактный сканер*, а не PostgreSQL SQL parser. Только actual PG17 выполнение миграций и suite констатируют реальную успешность. SHA каталога — контроль целостности относительно локальных файлов, не подпись от внешнего независимого доверенного источника.

Сверка исходных SQL через GitHub connector обнаружила ровно **13 таблиц / 27 триггеров** в шести миграциях. Не выявлено `DROP TABLE`, `TRUNCATE TABLE` или `ALTER TABLE` вне `tb_deep_v2_*`. Это **выполненная статическая сверка, но не PostgreSQL тест**.

## Как запускать

```bash
# Без БД, без секретов, без провайдера
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_migration_audit.py' -v
python -m intelligence.taxonomy_builder.migration_audit --check \
    --json-out reports/rtb_v2_migration_source.json

# ТОЛЬКО disposable PostgreSQL17, с установленными migrations 001...059
export CM_TEST_PG_DSN='postgresql://<test_user>:<test_password>@127.0.0.1:5432/<test_database>'
export CM_DB_DSN="$CM_TEST_PG_DSN"
python -m intelligence.migrate
python -m intelligence.taxonomy_builder.migration_audit --postgres \
    --json-out reports/rtb_v2_migration_pg17.json

# Затем полный RTB PG17 mocked provider набор из workflow
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_binding_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_review_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_publication_db.py' -v
```

`--postgres` **отказывается запускаться**, если не указан `CM_TEST_PG_DSN`, и не использует fallback production credentials. Это не гарантирует, что администратор не перепутал DSN: использовать только одноразовую тестовую БД.

## Что добавлено в CI

`.github/workflows/rtb-module-contracts.yml`:

- Linux: шесть offline тестов `test_taxonomy_deep_v2_migration_audit.py`, затем `migration_audit --check`; JSON-отчёт включён в `rtb-t01-t36-reference-audit-linux`.
- Windows: тот же offline audit в новой отдельной стадии.
- PostgreSQL17: `python -m intelligence.migrate`, затем **раньше прочих DB-тестов** `migration_audit --postgres` со сравнением установленного SHA и 27 фактически включённых триггеров. JSON отправляется в отдельный Actions artifact даже при последующем FAIL.
- В `qa_matrix.py::SAFE_OFFLINE` добавлены соответствующие no-live migration audit и QA registry suites.
- Исправлены `pull_request.paths`: изменение любого RTB SQL 054–059, `site/projects.js` или `site/index.html` теперь активирует RTB CI. Раньше только часть этих путей была учтена.

## Какие риски остаются

- **D2 НЕ СДЕЛАН:** ни один GitHub Actions runner на текущем HEAD не начал настоящий Linux/Windows/PG17 прогон; нет `pytest/unittest` PASS, миграционного SQL протокола и счётчиков ошибок.
- **Проверка реального исторического upgrade** с заполненными legacy Flat/Deep/B9 данными остаётся отдельным acceptance case. Наш аудит подтверждает устанавливаемые объекты и guard status, а не полную семантику миграции старых записей.
- **FIX-10 strict release и FIX-11** остаются блокирующими до устранения внешних T28/T29 (Search Planner V2), реальных QA logs и финального security/merge audit.
- Сначала устранять **фактические** SQL/runner failures. Не переименовывать статус queued в failed либо passed. Не менять `main` и не выполнять реальный Opus.

**Следующий этап:** D2 — получить реальный run, проверить конкретные строки `rtb-contract-linux`, `rtb-contract-windows`, `rtb-postgres-17`, скачать JSON artifacts и исправить все P0/P1 с новым кодом и повторным прогоном. D1 по коду пометить [x] отдельно.
