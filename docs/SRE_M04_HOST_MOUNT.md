# SRE M04 — минимальное подключение к localhost Content Machine

**Ветка:** `feature/sre-production-v1-20261023`, PR #22 (Draft). **Статус:** код M04 внесён, завершённость определяется GitHub Actions на точном SHA; до полного PASS пункт не зачёркивается в [основном чеклисте](SRE_IMPLEMENTATION_CHECKLIST.md).

## Цель

SRE должен присутствовать в приложении как **самостоятельный отключённый по умолчанию модуль**, не переписывая интерфейсы C9/D1, PSI, B, QDP или Collector и не перетягивая целиком чужие ветки. M04 предоставляет ровно **health/discoverability и статическую read-only страницу**, но НЕ реализует боевое управление классификацией — это M38–M41.

## Изменения

| Файл | Назначение |
|---|---|
| `intelligence/relevance/http_api.py` | Собственный GET-only контроллер SRE `/api/sre/v1/*`. При отсутствии флага SRE остаётся выключенным. Не импортирует воркер, базу, Collector, QDP, OpenRouter |
| `app.py` | Только два маленьких dispatch-хука после существующего Host/Origin/X-ContentMachine gate, плюс три статических пути SRE. Все чужие маршруты неизменны |
| `site/index.html` | Одна ссылка на отдельный SRE Studio entry |
| `site/sre-studio.html` | Самостоятельная страница состояния SRE и навигация обратно |
| `site/sre-studio.js` | Только GET manifest/health, никаких POST, запросов к моделям, localStorage или автоматических задач |
| `site/sre-studio.css` | Собственные стили, внешний CSS, без inline script/style и ослабления CSP |
| `tests/test_sre_bootstrap_http.py` | Реальный ThreadingHTTPServer, HTTP deny/allow, Host/Origin, API default-off, ошибки конфигурации, статические файлы |
| `tests/test_sre_psi_gate_compat.py` | Regression старого PSI release-gate: новые SRE SQL — добавления, не изменения старых; M/D/R/T старых файлов блокируются |
| `scripts/psi_release_gate.py` | Точечная межмодульная совместимость M01: `git diff --name-status --find-renames --diff-filter=DMRT` вместо ошибочного `--name-only`; сохраняет запрет изменения/удаления/переименования исторических миграций |
| `.github/workflows/sre-contracts.yml` | M04 smoke на Linux + PostgreSQL17; SRE pure + PSI gate contracts на Linux/Windows |
| `.github/workflows/windows-d1-smoke.yml` | Добавлена `node --check site/sre-studio.js` в существующую проверку Node22; остальные шаги не удалялись |

**Почему точечная правка PSI:** совместная сборка M01/старый Windows CI провалилась после прохождения 1733 тестов только на PSI Stage14 release gate, который ошибочно записывал новые SRE миграции `016_sre_d1_intake_rights.sql` и `017_sre_offline_run_engine.sql` в «изменённые старые». Это не разрешение переписывать исторический SQL: проверка теперь игнорирует **только новые добавления**, все исторические изменения остаются блокирующими. Содержимое миграций не трогали.

## Безопасные HTTP-маршруты

| Метод/маршрут | Когда SRE отключён | После `CM_SRE_ENABLED=1` |
|---|---|---|
| GET `/api/sre/v1/manifest` | 200: неплатный, строго ограниченный manifest с default-off flags | 200: тот же manifest и `runtime_preflight_required` |
| GET `/api/sre/v1/health` | 503, `status=disabled` | 503 до реального project-specific DB+source preflight, без утверждения LIVE-готовности |
| GET `/api/sre/v1/other` | 404 | 404 до M38/M39 |
| POST `/api/sre/v1/*` с авторизованного localhost | 405 | 405, **ни одного платного вызова/воркера** |
| POST без X-ContentMachine или с чужим Origin | 403 | 403 через прежний host gate |
| GET с чужим Host или Origin | 403 | 403 через прежний host gate |
| GET `/sre-studio.html`, `.js`, `.css` | Статические файлы, ничего не запускают | Те же файлы, только GET статуса |

Host-authorization на уровне `app.Handler.authorized()` проверяется **до** подключения SRE. M04 намеренно не принимает `project_id` и не делает пользовательские операции; никакая конфигурация API ключа не предоставляет полномочий. Не используйте чистый `validate_*_event` как доверенный полученный D1/QDP контент — M35/M36 обязаны проверить БД и реального владельца проекта.

### Почему health при включённом SRE пока 503?

M03 реализует `diagnose` с возможностью **явно** предоставить проверенные read-only DB/source-policy callbacks. Но M04 не обладает аутентифицированным project context и НЕ должен подделывать проверку `policy_probe=True`. Без такого контекста он честно сообщает `blocked_config` или `blocked_policy`. Реальные API M38/39 будут использовать project-scoped authorization. Включённый флаг не значит, что можно запускать классификатор.

## QA (обязательные подтверждения, НЕ утверждения без логов)

```sh
python -m unittest discover -s tests -p test_sre_psi_gate_compat.py -v
python -m unittest discover -s tests -p test_sre_bootstrap_http.py -v
node --check site/sre-studio.js
python -m unittest discover -s tests -v
```

Первые PSI тесты исполняются и в целевом Linux/Windows workflow, HTTP smoke — в целевом Linux PG17 и в полном Linux/Windows CI. Ни один не требует реального ключа OpenRouter, не вызывает платных провайдеров, не требует веток Collector/QDP.

**Ничего из M04 не создаёт** LIVE Worker, paid LLM, бюджет/consent, самообслуживание проекта, человеческую проверку или полноценный Studio UI. Это интерфейсы M09+, M22+, M32+ и M38–M41.

## Отчёт после этапа

По завершении точного CI сохранить SHA в `docs/SRE_IMPLEMENTATION_CHECKLIST.md` и `docs/SRE_IMPLEMENTATION_TRACKER_V1.md`. При PASS зачеркнуть **только** этапы, чей DoD действительно выполнен; при pending/failure оставить незачёркнутым с причиной и ссылкой. Не трогать `main` или другие ветки. PR #22 остаётся Draft до M47/разрешения владельца.
