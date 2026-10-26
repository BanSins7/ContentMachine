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

## Подэтап 02-B2a — отдельный Deep V2 денежный ledger

**Реализована структура PostgreSQL и отрицательные тесты; CI пока не подтверждён.**

- `055_tb_deep_v2_budget_ledger.sql`: таблицы `tb_deep_v2_quotes`, `tb_deep_v2_consents`, `tb_deep_v2_revocations`, `tb_deep_v2_runs`, `tb_deep_v2_attempts`. Ни одна прежняя B Stage14 Flat C или legacy Deep V1 таблица не меняется.
- Quote: composite FK к неизменяемому `tb_deep_v2_research_bindings`, pinned Opus 5.5 + snapshot ID/SHA, exact prompt SHA/version и request SHA, отдельные резервы под два обязательных прохода; проставление quote по-прежнему требует **будущего точного prompt renderer**, не предварительной оценки.
- Consent: независимый, неизменяемый, привязан к одному quote SHA; не меньше двух обязательных резервов и не допускает просроченного quote; revocation не удаляется.
- Run: один consent → максимум один run, уникальный project idempotency key, фиксированные source/quote/consent identity, невозможность списать/зарезервировать сверх authorized budget.
- Attempt: максимум одна запись на стадию `deep_first`, `deep_second`, `deep_expand_1`, `deep_expand_2`; INSERT разрешён лишь если run находится в соответствующем *_running состоянии и деньги заранее зарезервированы. Неизвестное списание `uncertain` не разблокирует резерв и не допускает автоматический retry.
- PostgreSQL17: 8 тестов в `tests/test_taxonomy_deep_v2_ledger_db.py`, включены в `.github/workflows/rtb-module-contracts.yml`.
- Эти таблицы сейчас **не имеют вызываемого из UI/API метода issuance/authorization/start/advance**. Синтетические INSERT в тестах проверяют ограничения, но не доказывают готовность пользовательского paid workflow.

Важно: SQL триггер ограничивает допустимые записи, но он сам по себе **не подтверждает актуальную цену OpenRouter** и **не заменяет расчёт реального prompt/request SHA, серверную auth и idempotency/reconciliation в приложении**. Продуктовый запуск остается запрещён.

## Подэтап 02-B2b/C1 — точный первый запрос Opus, quote и отдельное согласие

**Реализовано в коде; CI ещё не подтверждён.**

- `deep_v2_transport.py`: version `deep-v2/1`, строгая 14-field OpenRouter JSON Schema, `v2_request` и `v2_messages`. Отсутствует fallback на другую модель; фиксируется исходный B9/A8 SHA, untrusted evidence не является инструкцией модели.
- `deep_v2_quote_repository.py`: `quote(project_id,binding_id,reviewer_id)` использует реальный сериализованный Pass1 payload + `reservation_cost` и второй консервативный резерв из строго ограниченных Human Review1 <=24kB и quality report <=12kB. Оба прохода включены в `quoted_usd`, expansions не включены. Actual Pass2 request НЕ может быть оценён точно до утверждения Review1, поэтому перед его исполнением всегда сверять `reservation_cost` с утверждённым бюджетом.
- `authorize(project_id,quote_id,reviewer_id,authorized_usd,acknowledged_quote_sha256)` заново пересчитывает quote и проверяет current catalog, model/version/price/source pins; без совпадения SHA consent не выпускается. `revoke` записывает отдельный отзыв. `start` создаёт только `pass1_pending`, не вызывает модель, не резервирует средства, idempotency key привязан к quote и consent.
- Unit/DB QA: `test_taxonomy_deep_v2_quote.py` — 8 offline тестов, в том числе near-max Review1 capacity; `test_taxonomy_deep_v2_quote_db.py` — 7 PostgreSQL17 тестов реального B9 approval, Opus refresh, quote/consent, idempotent start, revoke, price drift; включены в Linux/Windows/PG workflow.
- `deep_contracts_v2.py` и `test_taxonomy_deep_v2_validation.py` — 03-A strict 14-field output validator (7 offline scenarios). Он ещё не выполняет полный human review/quality gate и не делает paid dispatch.
- Методы пока internal Python. Нет пользовательского HTTP API и нет `advance()` — считать модуль готовым к платному Deep нельзя.

## Оставшаяся работа FIX-02 (НЕ ЗАКРЫТА)

1. **Реальный quote:** РЕАЛИЗОВАН как internal Python, ещё не подтверждён CI: строго по замороженному \`research_input_sha256\` из FIX-01, snapshot/catalog SHA, **точным** \`deep-v2\` JSON Schema + prompt и размером двух обязательных POST. Для Pass2 нужен резерв на добавленное H1 tree + matrix. Пока schema/prompt FIX-03 не определены — запрещено выдавать advisory preflight как quote.
2. **SQL миграции:** исследовательская binding schema `054_tb_deep_v2_research_binding.sql` **написана**, CI ожидается. Отдельные quote, consent, revocation, run и attempt таблицы добавлены миграцией 055; ещё необходимы выдача quote из точных Deep V2 payload, серверное согласие и безопасный оплачиваемый runner с проверкой current model catalog. Старые таблицы не изменять.
3. **Quote TTL / model price:** консервативные reservations, snapshot версии каталога, срок действия сметы, выбранные model parameters и JSON hash; отказ при изменении тарифов, alias или capabilities.
4. **Explicit consent:** internal `authorize/revoke` РЕАЛИЗОВАНЫ; остаются HTTP auth integration, возможные ограничения UI и end-to-end checking. Отдельное разрешение Deep C, B3/B Stage14 consent не переиспользуется.
5. **Idempotent start:** запись запроса и состояния \`pass1_pending\` единственной транзакцией; project-specific idempotency key; повтор того же запроса возвращает run ID без paid API; другой payload под тем же ключом — 409.
6. **Advance paid stage:** только explicit POST из состояния pending, атомарно резервировать цену и stage attempt, контролировать текущий тариф, получать право ровно на один call, release только по подтверждённому usage/charge. У uncertain/no usage резервация остаётся заблокированной, retry без нового явного reconciliation не допускается.
7. **Mode-specific source loader:** для B9 Deep использовать immutable Research Input; для legacy \`tb_runs\` сохранить текущие \`_reviewed\`, \`deep-v1\` и контракт V1 полностью без изменения. В passer не использовать MiMo, B3 или случайную быструю LLM.
8. **PostgreSQL17 + concurrency:** двойной start, competing advance, cancellation/revoke, stale quote/price, crash-after-reservation, unknown cost, cross-tenant, no-fake-review, no implicit paid dispatch. Без P0/P1 закрыть нельзя.

## Стыковка с FIX-03

Финальный \`deep-v2\` prompt + JSON schema — задача FIX-03; реальный quote должен быть расчётом на точных payload/messages этой модели. Перед пользовательской авторизацией необходимо закончить оба контракта. Переход между подэтапами не может считаться завершением FIX-02.

## CI и ограничения результата

Сейчас GitHub Actions на предыдущих RTB HEAD долго имеют \`queued\`. Отсутствие результата не подтверждает прохождение тестов. Новые проверки запускаются на каждом PR commit, и текущий FIX не зачёркивается до фактического \`success\`.

Никакие реальные API вызовы Opus, финансовые записи и изменения \`main\` в подэтапе 02-A не выполнялись.
