# SRE M03 — безопасная конфигурация и диагностика автономного модуля

**Статус:** реализация M03 закоммичена в ветке `feature/sre-production-v1-20261023`. Статус `DONE` ставится **только после успешного CI на окончательном кодовом SHA**. Это отдельный этап из [главного чеклиста M01–M47](SRE_IMPLEMENTATION_CHECKLIST.md); S0–S4 и M02 не переписывались.

## Цель и ограничения

- SRE может быть импортирован без внешних веток Collector/QDP/Analytics, доступного API OpenRouter, установленной embedding-модели, работающей БД и любой загрузки медиа.
- Простая загрузка пакета и чтение manifest не читают конфигурацию окружения, не открывают соединений PostgreSQL, не запускают worker, не выполняют HTTP/paid POST.
- Любое изменение поведения требует явного opt-in. Настройка не является ни подтверждением source-rights, ни финансовым согласием, ни доказательством доступности Claude Opus.
- Точная единственная будущая LLM — `anthropic/claude-opus-5.5`; Fast LLM и fallback исключены. Локальная embedding-модель на этом этапе **не загружается**, сам inference — M16.

## Файлы

- `intelligence/relevance/config.py` — `SRERuntimeSettings`, `load_settings(source=None)`, `connect_for_probe(settings)`, `optional_dependency(name)`.
- `intelligence/relevance/health.py` — `diagnose(settings,db_probe=...,policy_probe=...,dependency_probe=...)` и явный `check_read_only_schema(conn)`.
- `tests/test_sre_runtime_config.py` — 16 offline unit methods, негативные значения, секреты и импорт без побочных эффектов.
- `tests/test_sre_health_pg.py` — настоящая одноразовая PostgreSQL17, read-only schema probe и блокировка при непроверенной source policy.
- `.github/workflows/sre-contracts.yml` — M03 unit на Linux/Windows и PG17 integration.

## Префикс и default-off environment

SRE использует только `CM_SRE_*` настройки и два уже существующих параметра DB-хоста `CM_DB_ENABLED`, `CM_DB_DSN`. Конфигурация в M03 **явно** читается только при вызове `load_settings()`, никаких скрытых вызовов на import. M03 не изменяет общий `intelligence/config.py`.

| Переменная | Default | Ограничения |
|---|---|---|
| `CM_SRE_ENABLED` | `false` | `1/0/true/false` lowercase |
| `CM_SRE_OFFLINE_WORKER` | `false` | Требует явного `CM_SRE_ENABLED=1` |
| `CM_SRE_LIVE_CLASSIFICATION` | `false` | `feature_unavailable` до полноценных LIVE этапов |
| `CM_SRE_COLLECTOR_EVENT_INGEST` | `false` | `feature_unavailable` до M35 |
| `CM_SRE_QDP_EVENT_DELIVERY` | `false` | `feature_unavailable` до M36 |
| `CM_SRE_MODEL_ID` | `anthropic/claude-opus-5.5` | Любая другая модель отвергается, никакого fallback |
| `CM_SRE_LOCAL_EMBEDDING_MODEL` | Не задана | Ограниченный slug/path имени; никакого скачивания/исполнения |
| `CM_SRE_WORKER_LEASE_SECONDS` | `60` | Целое 5–3600, без float/bool |
| `CM_SRE_MAX_BATCH_ITEMS` | `100` | Целое 1–1000 |
| `CM_SRE_MAX_ATTEMPTS` | `3` | Целое 1–10 |
| `CM_SRE_OPENROUTER_API_KEY` | Не задан | Секрет может быть указан, но не активирует live/POST; никогда не попадает в public manifest |
| `CM_DB_ENABLED` / `CM_DB_DSN` | По общей инфраструктуре | SRE не подключается без явного preflight; DSN не возвращается наружу |

Неподдерживаемые `CM_SRE_*` настройки отвергаются, чтобы ошибочный флаг не включал молча другой режим. Внешние переменные других модулей не меняются и не конфликтуют.

## Диагностика `sre-health/v1`

Допустимые статусы: `disabled`, `available`, `blocked_config`, `blocked_dependency`, `blocked_policy`. Ответ имеет только безопасные перечисления и boolean-поля, без DSN/ключей/exception traceback.

1. `disabled`: при дефолтных флагах без каких-либо SQL/dependency/policy checks.
2. `blocked_config`: PostgreSQL выключен/не указан, callback для preflight отсутствует или неуспешен.
3. `blocked_dependency`: PostgreSQL driver не установлен. Проверка optional packages через `find_spec`, без импорта/загрузки моделей.
4. `blocked_policy`: DB read-only preflight успешен, но нет положительного подтверждения source-policy для **авторизованного проекта**.
5. `available`: только после явных caller-supplied положительных read-only PostgreSQL и authenticated source-policy probes. Речь исключительно о **S4 offline fixture**, а НЕ о разрешении платных вызовов.

Ни один даже успешный ответ не ставит `can_dispatch_provider=True` или `can_start_live_worker=True`. Ни наличие API-ключа, ни локальная настройка проекта не обходят текущие S3 права: чтение и отправка проверяются заново на реальных данных. Будущий M09/S9/API слой обязан подставлять реальные аутентифицированные callbacks, никогда не доверять клиентскому `project_id` или `policy_probe=True`.

Пример (без соединения):

```python
from intelligence.relevance.config import load_settings
from intelligence.relevance.health import diagnose

settings = load_settings({})   # closed by default
assert diagnose(settings)["status"] == "disabled"
assert diagnose(settings)["can_dispatch_provider"] is False
```

Explicit PostgreSQL preflight выполняется только trusted service:

```python
from intelligence.relevance.config import connect_for_probe
from intelligence.relevance.health import check_read_only_schema

# settings must contain explicit host PostgreSQL enablement/DSN.
# This is a diagnostic, not an HTTP request handler:
with connect_for_probe(settings) as conn:
    schema_present = check_read_only_schema(conn)
```

Схема проверяется под `SET TRANSACTION READ ONLY` на наличие SRE, D1 и RTB таблиц/представлений. Это НЕ проверка SHA применённых миграций: её отдельно закроет **M05**.

## Тесты и честные ограничения

Ручной/CI: `python -m unittest discover -s tests -p test_sre_runtime_config.py -v`, плюс PostgreSQL17 fixture `CM_TEST_PG_DSN` для `test_sre_health_pg.py`. Целевой workflow выполняет S1–S4, M02 и M03. Полные Linux/Windows CI проверяют отсутствие регрессий внешних модулей.

**Ещё не реализовано на M03:** authenticated SRE HTTP mount, реальный запуск воркера по флагу, embeddings, OpenRouter transport, финансовые документы, human review, actual Collector inbox или QDP outbox. Наличие `CM_SRE_OPENROUTER_API_KEY` не запускает ни одного платного запроса. M04 — минимальный host mount без побочных эффектов.

Проверяй итоговые CI **на точном HEAD** до зачёркивания M03 в [главном чеклисте](SRE_IMPLEMENTATION_CHECKLIST.md).
