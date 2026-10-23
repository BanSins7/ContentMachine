# Search Planner — Generation Engine Stage B: Cost, Consent, Ledger

**23.10.2026 · реализация Stage B, отдельный PR; рабочий OpenRouter transport ещё не подключён.**

Полная архитектура: [Generation Engine Full Spec](SEARCH_PLANNER_GENERATION_ENGINE_V1_FULL_SPEC.md). Stage A: [Run/Batch Manager](SEARCH_PLANNER_ENGINE_IMPLEMENTED_STAGE_A.md). Stage B использует таблицы `sqg_*` миграции 015; новой миграции нет.

## Реализованные интерфейсы

| Python функция | Что делает |
|---|---|
| `model_manager.current_priced_model` | Получает **свежий** verified catalog snapshot, сравнивает точную модель/SHA/цены/возможности structured output |
| `cost_estimator.estimate_generation` | Рассчитывает low/expected/high USD и upper reserve для **каждого** сохранённого Batch; Decimal, округление до 8 знаков, +20% margin |
| `budget_manager.create_generation_quote` | Создаёт или повторно использует неизменяемую смету, привязанную к точным generation/context/config/batch SHA; TTL 30 минут |
| `budget_manager.authorize_generation_budget` | Сохраняет **отдельное явное** согласие для Search Planner (не RTB), сумма ≥ high quote и ≤ $10 000; Run → queued, но платных вызовов **нет** |
| `budget_manager.revoke_generation_budget` | Транзакционно отзывает разрешение и запрещает новые отправки |
| `budget_manager.get_generation_budget` | Только чтение spent/reserved/available и состояния |
| `repository.reserve_batch` | Под блокировкой Generation → Consent → Batch сохраняет резерв и попытку, проверяет актуальность модели, price, quote и остаток |
| `repository.record_dispatch_intent` | Только долговечная запись **намерения** будущего HTTP POST, без сети; после этого retry может удвоить платёж и запрещён |
| `repository.settle_known_failure` | Учитывает достоверное списание за **неуспешную** попытку (в том числе over-reserve), без второго списания при повторе |
| `repository.mark_uncertain_attempt` | Отмечает неопределённую оплату, **сохраняет резерв**, повторного POST нет |
| `repository.release_unsubmitted_attempt` | Освобождает резерв только ДО отправки (без dispatch intent) |

## Граница исполнения и безопасности

**Stage B ничего не отправляет OpenRouter и не запускает worker.** Вызов `authorize_generation_budget` должен происходить **только** через будущий защищённый пользовательский action с подтверждением владельца; сама Python-функция — не замена HTTP auth/CSRF.

`estimated_input_tokens` из Stage A является консервативным proxy **в байтах UTF-8**, не фактическим токенизатором. Поэтому high quote — **не гарантия счёта**. Перед реально оплачиваемым POST будущий Stage C обязан:
1. проверить точный сериализованный prompt/request, max output/context, pricing tier и capabilities;
2. сохранить транзакционный резерв и dispatch_intent;
3. отправить ровно один HTTP POST;
4. выполнить settlement фактического usage и проверку полного LLM raw output;
5. при неизвестной оплате сохранять резерв до reconciliation, никогда не повторять оплаченный вызов автоматически.

`settle_known_failure` не создаёт успешных запросов: успешный raw JSON → кандидаты, покрытие и outbox будут реализованы в Stage C Output Manager.

## Контроль в тестах

- `tests/test_search_planner_engine_cost.py`: Decimal, upper quote, стабильный SHA, нулевой тариф, валидация недопустимых и некорректных сумм, ограничения размера.
- `tests/test_taxonomy_deep_db.py` (disposable PostgreSQL): после полного двухэтапного RTB и Engine A строит quote, проверяет повторное использование, project isolation, under-budget rejection, consent/reuse, конкурентный reserve **одного batch** двумя потоками, unknown retain, revocation, actual charge больше резерва (учитывается один раз), pre-dispatch release.
- CI Linux и Windows обязателен до объединения.
- Во всех тестах отсутствуют реальные OpenRouter completion и сбор видеороликов.

## Следующий шаг

Stage C — Execution Manager, fake-provider, управляемая worker-очередь, разбор успешного ответа, atomic outbox, восстановление и пользовательский API с реальным подтверждением расходов. Реальный OpenRouter + ThinkQE/MuGI/Jina — отдельный этап после этих проверок.
