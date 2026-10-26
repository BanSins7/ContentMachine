# PIE-MOD-02 — Standalone ports, capability checks and opt-in settings

**Дата:** 2026-10-26. **Ветка:** `feature/patterns-insights-full-module`. **Владелец:** `intelligence.patterns_insights`. **Статус:** интерфейсы и локальные контракты реализованы; реальный UAE reader и генерация Insights пока не реализованы.

## 1. Архитектура и разделение ответственности

PIE импортируется в `main` без необходимости устанавливать код UAE PR #38 / исторический PR #23 и не импортирует Group A/B/C, QDP, SRE, PostgreSQL или платный LLM SDK. `PIEPorts()` — frozen dependency container. Все шесть интерфейсов по умолчанию равны `None`; неизвестная зависимость возвращает typed `capability_unavailable`, без фиктивного fallback.

| Порт PIE | Собственный adapter schema | Будущий метод / ответственность |
|---|---|---|
| `uae` | `pie-uae-reader/v1` | `read_qualified_manifest(pointer)`; только действительно квалифицированный row-level research manifest |
| `source_policy` | `pie-source-policy/v1` | `check_current_rights(pointer)`; актуальные project/source/policy/retention на каждом read |
| `store` | `pie-artifact-store/v1` | `write_immutable(project_id,kind,sha256,data)`; immutable research artifacts |
| `publisher` | `pie-insight-publisher/v1` | `publish_reviewed_insight(project_id,version_id)`; только approved downstream output |
| `review` | `pie-human-review/v1` | `get_review_status(project_id,version_id)`; human-approval lifecycle |
| `model` | `pie-grounded-model/v1` | `generate_grounded_insight(project_id,evidence,approval_id)`; future opt-in LLM |

Это **локальные версии адаптеров**, а не подтверждение существования API соседних веток. Будущий UAE wire `uae-to-pie/v1` остаётся *предложенным*, не реализованным. Существующий у UAE `uae-pattern-handoff/v1` — pointer/descriptive handoff, **не** подтверждённая построчная выборка и по правилам PIE не совместим с `uae-to-pie/v1`.

Пока schema/DTO настоящего UAE output не утверждены, бизнес-методы в `Protocol` возвращают `object`, **не принимаемый за валидные данные**. В PIE-MOD-03 появится строгий типизированный input contract; в PIE-MOD-05 — адаптер actual UAE; в PIE-MOD-06 — проверка provenance/rights по текущим источникам.

## 2. Capability matrix / fail closed

| Условие | Status | reason_code | Result `PIEPorts.require()` |
|---|---|---|---|
| Не передан адаптер | `unavailable` | `not_connected` | `capability_unavailable` |
| Нет обязательного callable метода | `incompatible` | `missing_methods` | `capability_incompatible` |
| Exception из `capability()` | `unavailable` | `capability_probe_failed` | `capability_unavailable` |
| Wrong port / descriptor type | `incompatible` | `invalid_descriptor` | `capability_incompatible` |
| Adapter version mismatch | `incompatible` | `adapter_schema_mismatch` | `capability_incompatible` |
| UAE peer wire absent/mismatch | `incompatible` | `peer_schema_mismatch` | `capability_incompatible` |
| Adapter explicitly offline | `unavailable` | safe adapter-provided code | `capability_unavailable` |
| Exact adapter + peer versions заявлены | `declared` | `None` | Возвращает порт **только для будущего trusted caller** |

Метод `PIEPorts.status()` вызывает **только** `capability()`; никаких `read_qualified_manifest`, правовых запросов, SQL, сохранений, публикаций, платных model inference, процессов/воркеров. Ошибки probe санитизируются без раскрытия текста исключений (который может содержать credentials). Структурные `@runtime_checkable Protocol` и version-match **не являются producer authentication**. Для любого `PortStatus`, даже `declared`, `verified_live=False`; `can_attempt_call=True` означает *только* возможность будущего вызова, не реальную проверку прав или qualified status. Все статусы и декларации frozen, schema-таблицы read-only, fingerprint/pointer не удостоверяют живой источник.

## 3. No accidental spend or autonomous work

`PIEExecutionSettings()` без параметров задаёт `enable_workers=False`, `enable_paid_model=False`, `approved_spend_limit_usd=None`. Если явно указан `enable_paid_model=True`, обязателен положительный конечный `Decimal` budget. Конфигурация **не** заменяет одобрение конкретного запроса, финансовые резервации и защиту от повторных платных вызовов; это задачи PIE-MOD-29 и PIE-MOD-33. Модуль не вызывает API, не выполняет SQL, не добавляет scheduler, не делает статистических утверждений.

## 4. QA и ограничения

**Локально выполнено:** Python 3.13 из изолированного набора PIE-контрактов и воспроизведённых тестовых fixtures предыдущих этапов:

```bash
PYTHONDONTWRITEBYTECODE=1 python -m unittest discover -s tests -p 'test_pie_*.py' -q
python -m compileall -q intelligence/patterns_insights
```

**Результат:** 20/20 PASS (foundation 4 + audit 5 + PIE-MOD-02 ports/settings 11); compileall PASS. Покрыты пустая установка, валидный mock descriptor, подмена UAE wire, отсутствующие методы, broken/hostile descriptors, ошибка probe без утечки, неизменность/повторяемость, все шесть протоколов, принудительно запрещённые business calls и paid defaults. Локальный subset не равен E2E по реальным данным или зелёному CI на Windows/Linux.

**GitHub CI:** после code commit `d04389de1a2d3da97f982f2028c5d102096af18a` workflow `PIE standalone module` #40314820541 наблюдался `queued`; **не PASS**. **PG17:** NOT RUN. **UAE live handoff / LLM E2E:** BLOCKED/NOT RUN. Ветка UAE на момент начала этапа уже имела UA-MOD-02 (HEAD `d3ac81c841f6900a7f8da915de2019087b56ccf0`), но её qualified row-level output ещё не готов. Ни одна чужая ветка не изменена.

**Следующий этап:** PIE-MOD-03 — versioned dataset contract, exact videos IDs / verified qualified snapshots / missingness / lineage / producer hashes. Нет настоящего UAE порта — typed unavailable, а не превращение synthetic sample или агрегатов в production evidence.
