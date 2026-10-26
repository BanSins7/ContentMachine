# Search Planner — Generation Engine Stage C

**Дата:** 23.10.2026. **Статус:** код Stage C с FakeProviderPort в отдельном PR поверх main. Никаких реальных OpenRouter completion и платных вызовов. Используются таблицы sqg_* из миграции 015, новые миграции не нужны.

Полная архитектура: [Generation Engine Full Spec](SEARCH_PLANNER_GENERATION_ENGINE_V1_FULL_SPEC.md). Реализованные предыдущие этапы: [Stage A](SEARCH_PLANNER_ENGINE_IMPLEMENTED_STAGE_A.md) и [Stage B](SEARCH_PLANNER_ENGINE_IMPLEMENTED_STAGE_B.md).

## Компоненты

| Модуль | Реализация |
|---|---|
| provider_port.py | BatchRequest/ProviderResponse, FakeProviderPort, режимы success/no_viable/known_failure/uncertain/invalid_json/truncated. Ни одного HTTP client |
| raw_validation.py | Полный raw LLM v1 contract: exact batch hypotheses, запрет extra fields, facet/angle/language/modes, длины, coverage и caps |
| worker.py | Read-only planning из immutable SHA contexts, резерв Stage B, durable dispatch intent, один fake invoke, корректное разветвление outcomes |
| output_manager.py | Атомарно сохраняет verified cost/usage/raw JSON, кандидатов с серверными UUID и source fact lineage, coverage, статус Run и outbox |
| recovery_manager.py | Cancel, release только pre-dispatch, mark stale dispatch uncertain (без повторной оплаты), чтение/ACK outbox |
| tests/test_search_planner_engine_stage_c.py | Unit tests: fake-only, no viable, damaged/raw LLM contract |
| tests/test_taxonomy_deep_db.py | E2E: RTB два Human Review → план → quote/consent → 3 fake batches → complete/outbox → idempotent ACK, invalid/unknown/cancel |

## Один батч

1. planned_next_request читает PostgreSQL contexts, проверяет SHA всех HypothesisContext и batch SHA.
2. reserve_batch под lock generation→consent→batch формирует денежный резерв и attempt=reserved.
3. record_dispatch_intent выполняет commit ДО любых внешних side effects.
4. FakeProviderPort.invoke_once создаёт синтетический raw batch — никаких HTTP запросов/токенов OpenRouter.
5. При успехе OutputManager проверяет model/usage/charged_usd, raw JSON, hypothesis/facet keys и policy; одна транзакция сохраняет attempt=completed, raw result, кандидатов, coverage и списание.
6. Когда все batches completed и все source hypotheses покрыты, одна транзакция фиксирует generation=completed + sqg_outbox pointer.
7. При некорректном raw c known cost: failed_known и реальное (для fake — имитированное) списание в ledger, без handoff.
8. При неизвестном результате: uncertain, резерв НЕ освобождается; automatic retry отсутствует.

### Правила безопасности

- Stage C публичный интерфейс process_one_batch принимает строго тип FakeProviderPort. Реальный ProviderPort и HTTP adapter будут только в Stage D.
- GET output — только completed. Output представляет query_candidates/v1; источники source_fact_ids выведены сервером из approved ancestry, модель не вправе их назначать.
- No viable queries разрешено только с явным непустым reason; Run completed может содержать 0 queries при полном no_viable покрытии.
- Cancel не отменяет совершённые расходы и не удаляет задокументированный dispatch intent.
- recovery stale dispatch → uncertain; никаких новых paid POST для той же attempt.
- pending_outbox возвращает безопасный указатель; ACK доступен только после реальной durable обработки будущим Query Validation consumer. Stage C не выдаёт фиктивную доставку.
- Model pricing и токены Stage B всё ещё нуждаются в перепроверке по **точному** запросу перед платным Stage D; byte proxy ≠ фактический токенизатор.

## Вызов из доверенного Python-кода

~~~python
from intelligence.search_planner.generation_engine import (
    FakeProviderPort, drain_generation, get_candidates_bundle,
    request_cancel, mark_stale_dispatches, pending_outbox, ack_outbox,
)

# Предусловие: RTB approved, Run planned и отдельный consent Stage B.
outcomes = drain_generation(
    project_id, generation_id, transport=FakeProviderPort("success")
)
bundle = get_candidates_bundle(project_id, generation_id)  # only completed
events = pending_outbox(project_id)
# ACK только после подтверждённой записи consumer:
# ack_outbox(project_id, events[0]["outbox_id"])
~~~

**НЕТ** в Stage C: HTTP/UI routes, user authorization surface, background daemon, реальной OpenRouter генерации, Research Query Validation, TikTok/Apify/DTK. Нельзя подключить оплачиваемый транспорт к fake-only worker без отдельного PR и security review.

## Тесты

~~~bash
python -m unittest discover -s tests -p 'test_search_planner_engine_stage_c.py' -v
python -m unittest discover -s tests -p 'test_search_planner_engine_cost.py' -v
python -m compileall -q intelligence/search_planner
# Только disposable PostgreSQL:
CM_TEST_PG_DSN=postgresql://... python -m unittest discover -s tests -p 'test_taxonomy_deep_db.py' -v
~~~

Следующая фаза Stage D: OpenRouter adapter, actual prompt/version schema, ThinkQE/MuGI/Jina, точный cost safety check, user consent UI; затем Query Validation.
