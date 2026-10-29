# SRE M41 — автономная установка, диагностика, безопасный запуск и остановка

**Дата:** 2026-10-28. **Ветка:** `feature/sre-production-v1-20261023`, Draft PR #22. **Код M41 реализован [x]**; Linux/Windows exact-head тесты [ ] **не подтверждены как PASS**; реальный PostgreSQL17 / LIVE / хост Studio [ ] **BLOCKED**. Изменения только в SRE. Сервис не требует установленных VC, QDP или Analytics, не устанавливает зависимости других веток и не вызывает платных провайдеров.

## 1. Что означает «автономный» в M41

`python -m intelligence.relevance.cli` — отдельная точка входа SRE без запуска фоновых процессов при импорте. Доступны `self-check`, `migration-diagnosis`, `preview`, `worker-once`, `start` (= `worker-loop`), `stop`, `status`, `recover-stale`. CLI ничего не запускает автоматически при старте `app.py`, при открытии сайта или при пассивной проверке.

**Важное ограничение:** M41 запускает ТОЛЬКО ранее созданные OFFLINE_FIXTURE задания S4. Метки этого режима — **тестовая имитация**, НЕ реальная классификация Opus и НЕ результаты M30. LIVE worker с M14 lease, платные OpenRouter POST, бюджетные резервы, создание настоящего LIVE классификатора, автоотправка в QDP/Collector **не входят** в CLI. Это осознанный safety gate, а не отсутствующий флаг для оплаты.

Файлы:

- `intelligence/relevance/cli.py`: argparse, JSON-ответы/exit codes, явный выбор команд; `load_settings` с default-deny flags; отдельное подключение PostgreSQL **только при DB-командах**; миграционный preflight, проектный D1/C9 preview, scoped worker, cooperative loop.
- `intelligence/relevance/offline_scoped_worker.py`: S4-only работа по **одному конкретно названному проекту**, `WHERE r.project_id=%s` до блокировки; атомарная аренда исходного S4 item через `FOR UPDATE SKIP LOCKED` и fenced `worker_token/lease_generation`; `finish_item`/safe `fail_item` исходного S4. Никаких глобальных claim/reap задач других проектов.
- `intelligence/relevance/worker_control.py`: private owner/stop records, exclusive `O_EXCL`, nonce привязан к процессу и проекту, кооперативная остановка между заданиями; `stop` **НЕ** посылает SIGKILL/TerminateProcess. При power loss owner record остаётся и требуется ручная сверка. Windows PID проверяется через `OpenProcess/GetExitCodeProcess` без убийства процесса; сомнительные статусы fail closed.
- Исходные M38/M39 HTTP остаются **disabled-by-default**: M41 CLI НЕ выдумывает доверенную авторизацию хоста и НЕ подключает защищённый WSGI к существующему публичному серверу `app.py`. Это отдельный обязательный E2E gate; попытка обойти его ради UI неприемлема.

## 2. Установка (Linux/macOS, в корне репозитория)

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r intelligence/requirements.txt
# Никакой автоматической загрузки ML-модели или платного аккаунта.
python -m intelligence.relevance.cli --help
python -m intelligence.relevance.cli self-check
python -m intelligence.relevance.cli migration-diagnosis
```

По умолчанию `CM_SRE_ENABLED=0` (если переменная вообще не установлена). Для диагностики установленной БД оператор **самостоятельно** задаёт переменные:

```bash
export CM_DB_ENABLED=1
export CM_DB_DSN='postgresql://<локальный-оператор>:<секрет>@127.0.0.1:5432/<db>'
python -m intelligence.relevance.cli migration-diagnosis --database
python -m intelligence.relevance.cli self-check --database
```

Не вставляйте реальные DSN, ключи API или пароли в сообщения/логи. `self-check` по умолчанию не подключается к БД. `migration-diagnosis` без `--database` читает только исходники миграций и известные статические блокеры. `--database` открывает **только read-only** PostgreSQL preflight (SHA, applied/pending/retroactive). CLI никогда не выполняет `apply_migrations`, `CREATE TABLE`, удаление данных или обновление чужих веток.

**Проверенный в исходниках критический блокер (не обходить):** `062_sre_live_opus_receipts.sql` содержит оборванное `evidence_sha256 CHECK`/отсутствующий тип `input_tokens`. Соответственно `migration-diagnosis` возвращает `migration_062_known_malformed_check_sql`; полноценные команды preview/worker fail closed до checksum-safe ремонта исходного SQL и проверенного installed ledger. См. [forensics](SRE_M36_062_MIGRATION_BLOCKER_FORENSICS.md). Нельзя молча переписать применённую историческую 062 и выдавать такой upgrade за корректный.

## 3. Автономная локальная установка Windows (PowerShell)

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r intelligence/requirements.txt
python -m intelligence.relevance.cli --help
python -m intelligence.relevance.cli migration-diagnosis

# Только при явной диагностике настроенной БД:
$env:CM_DB_ENABLED = '1'
$env:CM_DB_DSN = '<выданная оператором локальная строка подключения>'
python -m intelligence.relevance.cli migration-diagnosis --database
```

Это **инструкции для машины оператора**, а не утверждение, что эти команды уже выполнены. Платные провайдеры и модели Opus при установке не вызываются. Windows использует проверенные права файловой системы пользователя; ACL приватного control-dir оператор обязан настроить сам.

## 4. Безопасный OFFLINE_FIXTURE preview и работа по одному проекту

Только ПОСЛЕ исправления 062, успешного PostgreSQL17 preflight и подтверждения прав специального DB-оператора:

```bash
export CM_SRE_ENABLED=1
export CM_SRE_OFFLINE_WORKER=1
# Настройте СВОИ project UUID/IDs, не копируйте условные значения ниже.
python -m intelligence.relevance.cli preview \
  --project <32-hex-project-id> \
  --publication <real-approved-C9-publication-UUID> \
  --content-ids 101,102

python -m intelligence.relevance.cli worker-once \
  --project <32-hex-project-id> --allow-offline-fixture
```

`preview` использует `read_dataset_preview`: READ ONLY, реальные утверждённые RTB C9 H1/H2 и D1 S3 права, отмечает заблокированные/отозванные источники, без полного сырого текста, денег и запуска расчёта. `worker-once`: обрабатывает максимум ОДНО заранее подготовленное offline_fixture задание выбранного проекта, не переключает LIVE. Требует одновременно `CM_SRE_ENABLED=1`, `CM_SRE_OFFLINE_WORKER=1` и `--allow-offline-fixture`. Получение credentials/DB-role — обязанность доверенного оператора; **ID проекта, переданный в командной строке, сам по себе не является аутентификацией**. Избегайте многопроектной PostgreSQL роли в общем сервисе: выдавайте минимальные DB права отдельному оператору.

## 5. Foreground start / status / cooperative stop

Создайте приватную папку управления, принадлежащую только пользователю процесса:

```bash
mkdir -p "$HOME/.contentmachine-sre-worker"
chmod 700 "$HOME/.contentmachine-sre-worker"

# Терминал 1. НЕ daemon и НЕ background auto-start; завершится по stop или Ctrl+C.
python -m intelligence.relevance.cli start \
  --project <32-hex-project-id> --allow-offline-fixture \
  --control-dir "$HOME/.contentmachine-sre-worker" \
  --poll-seconds 3

# Терминал 2:
python -m intelligence.relevance.cli status --control-dir "$HOME/.contentmachine-sre-worker"
python -m intelligence.relevance.cli stop --control-dir "$HOME/.contentmachine-sre-worker"
```

`worker-loop` — явный синоним `start`. `stop` создаёт запрос с UUID-подобным случайным nonce текущего процесса; worker читает его **до следующего задания**. Уже начатая транзакция S4 не прерывается, поэтому stop не означает мгновенный exit; сокеты и процессы не убиваются. `--max-cycles N` ограничивает количество опросов для локальных проверок; `0` означает foreground до stop. PID/nonce не передаётся как полномочие на проект или согласие на оплату; control-file нельзя помещать в публичный каталог.

При аварийном завершении foreground процесса owner-файл может остаться. Обычный `start` откажется его перезаписать. Только после отдельной сверки `sre_offline_items` и уверенности, что PID больше не жив, оператор может выполнить:

```bash
python -m intelligence.relevance.cli recover-stale \
  --control-dir "$HOME/.contentmachine-sre-worker" \
  --acknowledge-audited-stale
```

Это чистит ТОЛЬКО приватную локальную блокировку, **не** освобождает PostgreSQL аренды, не списывает/не возвращает деньги, не утверждает правильность состояния LIVE и не выполняет повторную отправку. Старая S4 аренда по-прежнему проверяется оригинальными retry/fence/TTL ограничениями; M26 paid intents при этом никак не обрабатываются. Если процесс существует или его существование нельзя достоверно проверить — отказ. Windows требует приватного ACL; при неопределённом native PID статусе recovery также запрещён.

## 6. Возвращаемые статусы, команды и QA

JSON выводит только публичные статусы/счётчики и код причины: **без DSN, секретов, provider payload и stack traces**. Exit `0` = команда выполнена/пассивная диагностика вернула допустимый статус, `2` = blocked/preflight denial, `3` = неожиданная ошибка с редактированием текста. Пассивная `self-check` может вернуть `disabled`, это **не** operational PASS; статус schema presence без подтверждения прав не авторизует запуск.

Тесты (исходники):
- `tests/test_sre_m41_cli.py`: passive no DB, malformed 062, принудительно выключенные флаги, явный single-project fixture, сбой worker/retry, memory/secret redaction, C9/D1 read-only preview, foreground loop/stop.
- `tests/test_sre_m41_scoped_worker.py`: scoped SQL claim/reaper с `FOR UPDATE SKIP LOCKED`, original S4 fenced finish/fail, чужой проект/неверная lease, нулевые LIVE/paid.
- `tests/test_sre_m41_worker_control.py`: private owner + nonce, duplicate lock, cooperative stop, foreign nonce denial, stale owner manual recovery, no PID kill, filesystem permission/size/symlink denial.
- `.github/workflows/sre-contracts.yml` расширен до **M02–M41** на Linux/Windows; это всё ещё ожидает фактического exact-head PASS, тесты не считать пройденными по факту создания.

**Итог статуса:** M41 CLI source [x], exact-head CI [ ] UNVERIFIED, настоящая установка и рабочий цикл PostgreSQL [ ] BLOCKED из-за malformed 062. Studio M40 остаётся защищённым standalone UI, пока хост не предоставляет сессию, CSRF, RBAC, quota и M38/M39 mount; никакого поддельного демо-включения не производилось. Для M42 нужно безопасное end-to-end тестирование на disposable PG17 с test-only synthetic-live, которое не попадёт в настоящие M30/QDP события.

**Общий прогресс по исходникам:** **41/47 ≈87%**, оставшиеся M42–M47 и накопленные QA/интеграционные блокеры.
