# Search Planner · Generation Engine — Engine-A / Planned Runs (реализованный этап)

**Дата:** 23.10.2026. **Статус:** реализовано в `main` через объединённый PR #17. Миграция `015` прошла GitHub CI на тестовой PostgreSQL; применение к локальной базе пользователя не подтверждено. **Платных вызовов нет.**

Полный целевой design-to-code contract: [Generation Engine FULL SPEC](SEARCH_PLANNER_GENERATION_ENGINE_V1_FULL_SPEC.md). AI Query/ThinkQE/MuGI/Jina — другой блок: [AI Query implementation](SEARCH_PLANNER_AI_QUERY_TECH_IMPLEMENTATION_V1.md).

## Что реализовано сейчас

1. Аддитивная миграция `intelligence/migrations/015_search_query_generation.sql` создаёт 11 изолированных сущностей `sqg_*`: `config_versions,quotes,consents,generations,contexts,batches,attempts,candidates,hypothesis_coverage,events,outbox`. Фактических бюджетных операций и вызовов провайдера пока нет. В БД заложены project-scoped keys, проверки публикации RTB, ограничения статусов/стоимостей, защита immutable approved context/quote/config/event/candidates и индексы.
2. `generation_engine/contracts.py`: отдельные `RunState / BatchState / AttemptState`, явные переходы и строгий `search-generation-config/v1`. Конфигурация требует существующий числовой `tb_model_snapshots.id` и SHA-256 model catalog/prompt/schema. Важно: у реального RTB `tb_model_snapshots.id` — **BIGINT**, не UUID.
3. `batch_manager.py::make_batches()`: сохраняет целые HypothesisContext, сортирует по RTB ancestry и группирует по direction; cap 4/5 настраиваемый, ограничение входа байтами без урезания фактов. `estimated_input_tokens` в этой версии — **пессимистичная байтовая оценка для планирования**, не точные tokenizer tokens. Перед реальным вызовом OpenRouter потребуется проверка exact messages+schema+max_output_tokens.
4. `run_manager.py::register_config_version()`: серверная запись immutable approved configuration после проверки существующего OpenRouter model snapshot и заявленной `response_format` capability. Approval конфигурации **не разрешает расходы**.
5. `run_manager.py::create_planned_generation()`: в **одной PostgreSQL-транзакции** читает утверждённую immutable RTB из PR #15, создаёт contexts, batches, event, snapshot/hashes и generation. Идемпотентный ключ повторно возвращает прежнюю generation; другой input с тем же ключом отклоняется.
6. `get_planned_generation()` — только чтение, **не запускает фоновые/платные вызовы**.

## Главное ограничение этапа

Генерация создаётся **только** со статусом `awaiting_authorization`, батчи только `pending`. НЕ реализованы `budget_manager.py`, quote/consent/authorization API, worker/claim, provider transport, recovery, semantic AI Query, outbox dispatch, Query Validation. Создание таблицы `sqg_attempts` не означает, что вызов OpenRouter уже возможен.

**Это слой Engine-A и минимальное создание планов из Engine-B**, без выполнения. Остальные стадии FULL SPEC предстоит реализовать.

## Серверный Python интерфейс (не HTTP API)

```python
from intelligence.search_planner.generation_engine import (
    register_config_version, create_planned_generation, get_planned_generation,
)

# config содержит model/catalog id/SHA/prompt hash/raw schema hash,
# search_policy с явно разрешёнными языками и безопасные лимиты.
saved = register_config_version(project_id, approved_config)
planned = create_planned_generation(
    project_id,
    taxonomy_publication_id,
    saved["config_id"],
    "a-unique-idempotency-key-at-least-12-chars",
)
# planned.status == "awaiting_authorization"
# planned.paid_calls == 0
status = get_planned_generation(project_id, planned["generation_id"])
```

Здесь `approved_config` должен быть составлен trusted server-side кодом/владельцем, а не принят как raw HTTP без project auth. Для реального UI потребуется отдельная config draft + approval path; в этой версии функции Python server-only и не зарегистрированы в app routes.

## Тесты

```bash
python -m unittest discover -s tests -p 'test_search_planner_engine_contracts.py' -v
python -m unittest discover -s tests -p 'test_search_planner_foundation.py' -v
python -m compileall -q intelligence/search_planner

# ТОЛЬКО disposable PostgreSQL 17, мигратор создаёт 015_*:
CM_TEST_PG_DSN="postgresql://...disposable..." \
  python -m unittest discover -s tests -p 'test_taxonomy_deep_db.py' -v
```

Test matrix: строгая config validation; fail-closed state transitions; полный контекст без усечения; стабильное разбиение 24→6; запрет дублей и невозможности вместить single-context; fingerprint от модели/промпта; 0 provider imports. PostgreSQL test проходит полный RTB с fake LLM, два Human Review, затем `register_config_version` и `create_planned_generation`, проверяет 3 contexts, 3 batches, 0 attempts/consents/outbox, повторный idempotency key и project isolation. Без `CM_TEST_PG_DSN` PostgreSQL-проверка **SKIP**, не PASS.

## Границы риска и следующий PR

- Фактические HTTP/DB costs, quote TTL, contract-safe reasoning/temperature и exact token counting **не проверяются** до подключения real transport, который также требует explicit search spending consent.
- `register_config_version` доказывает соответствие сохранённому catalog snapshot, но **не гарантирует его свежесть**. Перед любым paid dispatch реализовать `ModelManager` и проверку актуальной цены/provider capabilities.
- Пока pending, нельзя трактовать `sqg_quotes/sqg_consents` как полученное разрешение; таблицы пусты.
- Создание повторного Generation Run с **другим** idempotency key допустимо как новый самостоятельный план и не приводит к оплате.
- При нескольких процессах конкурирующие config writes сериализуются по `project_registry` row lock; generation idempotency определяется unique `(project_id,idempotency_key)` и той же row lock.

**Следующий код:** полноценные `CostEstimator + BudgetManager` (quote, expiry, отдельное согласие, Decimal, reserve/settle/uncertain), затем `ExecutionManager` и fake provider. Переходить к реальному OpenRouter можно только после проверки этих защит на PostgreSQL race/crash tests.
