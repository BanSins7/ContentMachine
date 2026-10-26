# RTB-FIX-02 — Deep V2 C-run, согласие и бюджет (поэтапная реализация)

Branch: \`feature/rtb-module-v2\`, Draft PR #32. Этот файл уточняет [общий план](RTB_FULL_MODULE_IMPLEMENTATION_PLAN_RU.md) и [живой трекер](RTB_IMPLEMENTATION_PROGRESS.md).

## Текущий подтверждённый объём

Подэтап 02-A **реализован, но CI не подтверждён**:
- \`intelligence/taxonomy_builder/deep_v2_lifecycle.py\`: чистый preflight, связывающий один immutable \`rtb-research-input/v2\` SHA с моделью \`anthropic/claude-opus-5.5\`, catalog version/SHA, ценообразованием и консервативным лимитом контекста.
- Explicit next-action states: pass1_pending → review1 → pass2_pending → quality-dependent expand_pending (max 2) → review2; terminal cancelled/approved/failed/uncertain/blocked_quality.
- 12 offline unit tests \`tests/test_taxonomy_deep_v2_lifecycle.py\` добавлены в Linux/Windows RTB CI.
- \`prepare_deep_v2_preflight()\` **не создаёт quote, consent, run и не разрешает paid dispatch**. Поле \`provider_dispatch_allowed\` всегда false; возвращаемые суммы являются advisory, а не списанием или гарантией точного upper bound для Pass2.

## Почему нельзя использовать уже существующую B Stage14 quote

B Stage14 \`knowledge_c_generation_quotes/consents/runs/attempts\` и \`knowledge_execution.py\` принадлежат однопроходному Flat taxonomy-v2 C. Они ограничены одним оплачиваемым попыткой генерации и единственным topic review; их прямое повторное использование для двухпроходного Deep нарушило бы SQL UNIQUE, audit и пользовательский consent.

Deep V1 \`tb_runs\` + \`tb_deep_runs\` в свою очередь требует legacy \`tb_fact_review_submissions\` и generation-pending, чего законный утверждённый B9 не содержит.

В FIX-02 нужен явный самостоятельный mode \`knowledge_snapshot_v1/deep-v2\` и подчинённый Deep ledger, не использующий фиктивный Fact Review. Допустимо наследовать существующие криптографические/бюджетные примитивы (\`rates\`, \`reservation_cost\`, \`latest_verified\`, provider uncertain handling), но не наследовать согласие другого режима.

## Подэтап 02-B1: сохранение B9/A8 Research Input в PostgreSQL

**Реализован в коде, финальный QA ещё не подтверждён.**

- `054_tb_deep_v2_research_binding.sql`: новая `tb_deep_v2_research_bindings` с составными FK к точному B9 knowledge snapshot SHA, handoff, A context и reviewer; SQL `BEFORE INSERT` сверяет, что именно этот handoff принадлежит именно этому B9 snapshot/A8 контексту, и что reviewer enabled. UPDATE/DELETE блокируются immutable trigger.
- `deep_v2_binding_repository.py`: `pin(project_id,snapshot_id,reviewer_id)` пересоздаёт проверенный read-only Research Input, записывает его в БД идемпотентно по `(project_id,snapshot_id)`, сохраняет исходный SHA, отказывает при конфликте. `get` повторно верифицирует B9/A8, включая отзыв прав. Никакого quote/consent/LLM.
- `tests/test_taxonomy_deep_v2_binding_db.py`: настоящая B9 публикация, два одинаковых pin, signed mismatch при попытке перепривязки, SQL UPDATE/DELETE, фальшивый handoff SHA, чужой проект/пользователь, отсутствие paid runs. Добавлено в отдельный Linux PostgreSQL17 CI.
- Исправлены две блокирующие ошибки старого preflight-кода: A8 `canonical()` возвращает байты, поэтому повторный `.encode()` был ошибкой; zero-video fixture теперь реально построена B9 без видео.

**Вся FIX-02 не закрыта:** здесь нет сметы на реальные API payloads, согласия, оплачиваемого Deep-run или ревизии бюджета. Это только документально и SQL-защищённый input freeze.

## Оставшаяся работа FIX-02 (НЕ ЗАКРЫТА)

1. **Реальный quote:** строго по замороженному \`research_input_sha256\` из FIX-01, snapshot/catalog SHA, **точным** \`deep-v2\` JSON Schema + prompt и размером двух обязательных POST. Для Pass2 нужен резерв на добавленное H1 tree + matrix. Пока schema/prompt FIX-03 не определены — запрещено выдавать advisory preflight как quote.
2. **SQL миграции:** исследовательская binding schema `054_tb_deep_v2_research_binding.sql` **написана**, CI ожидается. Ещё требуется **отдельный** immutable Deep quote, consent, revocations, run/attempt ledger с tenant-composite FK к этой binding. Старые таблицы не изменять.
3. **Quote TTL / model price:** консервативные reservations, snapshot версии каталога, срок действия сметы, выбранные model parameters и JSON hash; отказ при изменении тарифов, alias или capabilities.
4. **Explicit consent:** проверенный reviewer, budget >= стоимость двух обязательных запросов с запасом; отдельное разрешение C Deep; revoke и never reuse B3/B Stage14 consent. Никакого запуска из quote или GET.
5. **Idempotent start:** запись запроса и состояния \`pass1_pending\` единственной транзакцией; project-specific idempotency key; повтор того же запроса возвращает run ID без paid API; другой payload под тем же ключом — 409.
6. **Advance paid stage:** только explicit POST из состояния pending, атомарно резервировать цену и stage attempt, контролировать текущий тариф, получать право ровно на один call, release только по подтверждённому usage/charge. У uncertain/no usage резервация остаётся заблокированной, retry без нового явного reconciliation не допускается.
7. **Mode-specific source loader:** для B9 Deep использовать immutable Research Input; для legacy \`tb_runs\` сохранить текущие \`_reviewed\`, \`deep-v1\` и контракт V1 полностью без изменения. В passer не использовать MiMo, B3 или случайную быструю LLM.
8. **PostgreSQL17 + concurrency:** двойной start, competing advance, cancellation/revoke, stale quote/price, crash-after-reservation, unknown cost, cross-tenant, no-fake-review, no implicit paid dispatch. Без P0/P1 закрыть нельзя.

## Стыковка с FIX-03

Финальный \`deep-v2\` prompt + JSON schema — задача FIX-03; реальный quote должен быть расчётом на точных payload/messages этой модели. Перед пользовательской авторизацией необходимо закончить оба контракта. Переход между подэтапами не может считаться завершением FIX-02.

## CI и ограничения результата

Сейчас GitHub Actions на предыдущих RTB HEAD долго имеют \`queued\`. Отсутствие результата не подтверждает прохождение тестов. Новые проверки запускаются на каждом PR commit, и текущий FIX не зачёркивается до фактического \`success\`.

Никакие реальные API вызовы Opus, финансовые записи и изменения \`main\` в подэтапе 02-A не выполнялись.
