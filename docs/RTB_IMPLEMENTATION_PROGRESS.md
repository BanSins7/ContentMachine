# RTB — единый журнал реализации (FIX-00 → FIX-11)

> **Точка входа для нового чата.** Перед любыми действиями прочитай этот файл, затем [полный технический план](RTB_FULL_MODULE_IMPLEMENTATION_PLAN_RU.md), [аудит FIX-00](RTB_FIX00_AUDIT_RU.md) и [контракт FIX-01](RTB_FIX01_RESEARCH_INPUT_RU.md). После каждого коммита сверяй реальную ветку/CI и **обновляй этот файл в том же PR**.

## Где работать

- Репозиторий: `BanSins7/ContentMachine`.
- **Единственная активная ветка RTB:** `feature/rtb-module-v2`.
- **Единственный активный PR RTB:** [#32](https://github.com/BanSins7/ContentMachine/pull/32), Draft.
- База: `main@218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` — уже содержит A (#29), B (#30), Flat RTB и Deep V1.
- **Нельзя** менять `main`, выполнять merge PR #32 или совершать платные обращения к LLM без отдельного поручения.
- Код RTB V1 **уже есть в этой Git-ветке**; дублировать его в отдельной папке не нужно.
- Изменять преимущественно `intelligence/taxonomy_builder/**`, `tests/test_taxonomy*.py`, `site/taxonomy-*.js`, RTB SQL migrations, `docs/RTB_*.md`, собственный CI. Внутренности Search Planner/SRE/A8/B9/Collector — собственность их feature-веток.

## Как зачёркивать выполненные этапы

**Правило пользователя (2026-10-26):** статус реализации отделён от статуса QA. Если **весь код конкретного FIX завершён** по его DoD, ставь `[x]` и зачёркивай название даже если CI ещё не смог запуститься, но **обязательно** явно отмечай `КОД ЗАВЕРШЁН / QA: QUEUED (НЕ ПРОВЕРЕНО)` и конкретные незапущенные тесты. Если код лишь частичный — оставляй общий FIX с `[ ]`, зачёркивай только завершённые подэтапы. `[x]` не означает `tests passed`: отдельный статус QA может быть `PASS`, `FAIL`, `QUEUED`, `BLOCKED`, `NOT RUN`.

Проверки, которые действительно завершились с ошибкой, помечать `QA: FAIL` с логами и именем теста, исправлять в ветке; при отсутствии runners нельзя заявлять об успешном тестировании. Проверенные успешные jobs помечать `QA: PASS` только по GitHub conclusion=success или фактическому локальному test command.

## Все этапы

- [x] ~~**RTB-FIX-00 — Module Isolation + аудит Flat/Deep V1 + реальные A8/B9 контракты.**~~ **КОД ЗАВЕРШЁН; QA: QUEUED / НЕ ПРОВЕРЕНО**, Linux/Windows контрактные тесты не запускались на runner. Документ: [RTB_FIX00_AUDIT_RU.md](RTB_FIX00_AUDIT_RU.md).
- [x] ~~**RTB-FIX-01 — Frozen Research Input B9+C1 и оригинальные A8+C2 observations.**~~ **КОД ЗАВЕРШЁН; QA: QUEUED / НЕ ПРОВЕРЕНО**. Добавлены real resolver/SHA, offline + PostgreSQL17 тесты. Документ: [RTB_FIX01_RESEARCH_INPUT_RU.md](RTB_FIX01_RESEARCH_INPUT_RU.md).
- [ ] **RTB-FIX-02 — Deep V2 C-run, immutable B9/A8 input, quote/consent and paid lifecycle.** Реализованы по коду 02-A/B1/B2a/B2b/C1/C2a; **остаётся** Pass2/expansion paid advance и HTTP auth. **QA: QUEUED/НЕ ПРОВЕРЕНО**, см. [RTB_FIX02_C_RUN_RU.md](RTB_FIX02_C_RUN_RU.md).
- [ ] **RTB-FIX-03 — Strict 14-field Deep V2 Schema, grounded validation and Pass1.** 03-A validator/transport и 03-B внутренний single-call оплачиваемый Pass1 + сохранённое H1-кандидатное дерево **реализованы по коду**. **Осталось** авторизованное пользовательское API и QA; **QA: QUEUED/НЕ ПРОВЕРЕНО**.
- [ ] **RTB-FIX-04 — Human Review №1.** 04-A внутреннее полное approve/reject/edit/merge/add и неизменяемая SQL 056 публикация H1 с переходом `review1 → pass2_pending` **реализованы по коду**; **НЕ ГОТОВ** пользовательский UI и публичный auth-aware API. **QA: QUEUED/НЕ ПРОВЕРЕНО**.
- [ ] **RTB-FIX-05 — Opus Pass2 / ≤2 manual expansions.** Подэтапы 05-A (H1 lock) и 05-B (**internal explicit paid Pass2** с B9/A8 replay, H1 SHA, резервированием/одним POST/settlement/quality gate) **завершены по коду**; 05-C — до двух отдельно разрешённых expansion, UI и API **НЕ ГОТОВЫ**. QA: QUEUED/НЕ ПРОВЕРЕНО.
- [ ] **RTB-FIX-06 — Deterministic Quality Gate, Search Readiness Gate, Human Review №2.** Ссылки только на утверждённые B9 факты и pinned A observations, без приписывания наблюдениям статуса product truth.
- [ ] **RTB-FIX-07 — Atomic immutable Deep V2 Publication + 11-field V1 projection + SRE bridge.** Переиспользовать одинаковые SHA→UUID facts, reject divergent identity, не изменять history.
- [ ] **RTB-FIX-08 — RTB-owned read-only V1/V2 publication export для Search Planner и SRE.** Не вносить незапрошенные изменения в файлы `intelligence/search_planner/**` и `intelligence/relevance/**`.
- [ ] **RTB-FIX-09 — Unified Taxonomy UX и legacy hardening.** Flat/Deep V1, B9 Deep V2, user-selected model/consent/reviews, не перезаписывать общие UI других модулей.
- [ ] **RTB-FIX-10 — T01–T36 E2E, PostgreSQL17, Linux/Windows, Node UI и полная регрессия.** Только mocked providers в автоматическом QA.
- [ ] **RTB-FIX-11 — Release qualification автономного RTB PR.** Проверить latest main diff, additive SQL migrations, чистое развёртывание, historical upgrade, все API-контракты, CI и готовность к однократному интеграционному merge по запросу.

## Контроль реализованных подзадач (зачёркнут код, но не весь FIX)

- [x] ~~FIX-00: аудит границ модуля, подписи B9 и чистые вспомогательные контракты~~ — реализация готова, QA ещё ждёт GitHub Actions.
- [x] ~~FIX-01: PostgresAResolver + KnowledgeSnapshotRepository Research Input, stable SHA, offline/DB tests~~ — реализация готова, QA ещё ждёт GitHub Actions.
- [x] ~~FIX-02-A: неизменяемый Deep V2 preflight, проверка модели Opus 5.5 и explicit stage gates~~ — реализация готова, QA ещё ждёт GitHub Actions.
- [x] ~~FIX-02-B1: отдельная SQL migration 054, immutable replay-verified B9/A8 Research Input binding, `pin/get` repository, composite source integrity guard, PostgreSQL17 tests~~ — **код отправлен, CI queued; этот пункт не означает закрытие FIX-02**.
- [x] ~~FIX-02-B2a: независимая SQL-схема quote/consent/revoke/run/attempt с проверкой привязки B9/A8, бюджета, TTL, неповторяемости оплачиваемых стадий~~ — **код + DB-тесты добавлены; CI queued, самого FIX-02 не закрывать**.
- [x] ~~FIX-02-B2b: versioned strict 14-field Deep V2/1 transport, exact first-pass payload quote, conservative bounded Pass2 reserve, SHA-pinned current Opus catalog and immutable quote issuance~~ — **код и 7 offline + 9 PG tests отправлены; CI ожидается**. Зависит от окончательной FIX-03 validation и дальнейшей сверки prompt.
- [x] ~~FIX-02-C1: internal server-side quote → explicit consent/revoke → idempotent unpaid start~~ — **код отправлен, CI ожидается; HTTP API и paid stage dispatch не подключены**.
- [x] ~~FIX-02-C2a: внутренний explicit Pass1: atomic reserve/unique attempt → single V2 provider request → validated result/charge settlement → uncertain spend stop~~ — **код реализован, QA: QUEUED/НЕ ПРОВЕРЕНО**; `deep_v2_pass1.py`, `deep_v2_provider.py`, 7 offline и 9 PostgreSQL17 тестов.
- [ ] FIX-02-C2b: Pass2/explicit expansion paid stages, server-authenticated POST routes, persistent Review1 input and concurrency/unknown-spend reconciliation; running tests and no regression. **НЕ РЕАЛИЗОВАНО**.
- [ ] FIX-02-C3: настоящий two-pass backend в отдельном RTB-owned API без изменения внутренних файлов других модулей; passage к Human Review1/2 и quality gate остаётся FIX-03..06.
- [x] ~~FIX-03-A: strict Deep V2/1 14-field request + role-aware `validate_deep_v2_response`, B9 approved own_product rules, original A8 ref whitelist, no source drift, offline validation fixtures~~ — **код есть, CI queued; FIX-03 не закрыт**.
- [x] ~~FIX-04-A: полное принятие Human Review №1 (approve/reject/edit/add/merge) c 14-field B9/A8 graph validation, immutable SQL 056 H1 и атомарным `pass2_pending`~~ — **реализовано по коду, QA: QUEUED/НЕ ПРОВЕРЕНО**, UI/route в 04-B.
- [x] ~~FIX-05-A: чистая защита H1 на Pass2/expansion — точное сохранение одобренных 14 полей и исключение отвергнутых IDs~~ — **реализовано по коду, QA: QUEUED/НЕ ПРОВЕРЕНО**.
- [x] ~~FIX-05-B: внутренний отдельный оплачиваемый Pass2 с immutable H1, SHA B9/A8, fresh model/price, exact payload budget cap, единственной оплачиваемой попыткой и качественным gate~~ — **КОД РЕАЛИЗОВАН, QA: QUEUED/НЕ ПРОВЕРЕНО**; реальные OpenRouter запросы не совершались.
- [ ] FIX-05-C: максимум 2 явно подтверждённых expansion по отдельному резерву и контрактам качества, HTTP auth/UI и PG17 E2E ещё НЕ РЕАЛИЗОВАНЫ.
- [x] ~~RTB SQL static regression: миграция 055 PL/pgSQL dollar quote `$$`, миграция 056 Review1+2 и тест баланса delimiters~~ — **код исправлен, QA: QUEUED/НЕ ПРОВЕРЕНО**.
- [ ] QA-signoff FIX-00/01/02: получить фактические прогоны Linux/Windows/PostgreSQL17 и исправить ошибки. FIX-00/01 отмечены [x] за готовый **код**, но до QA PASS не являются подтверждённо протестированными.

## Журнал изменений / проверок

| Этап | Что реализовано | Коммиты / текущее состояние |
| --- | --- | --- |
| FIX-00 | `research_contracts_v2.py`: проверка подлинности B9 approval SHA; стабильный project-scoped fact UUID5, original A evidence index, 14→11 core. Реальная 2-fact B9 fixture, отдельный Linux/Windows workflow, аудит ownership/миграций. | Начальная точка `55ed13123fbd4999f70bc8d0780dadbaec2d04bf`. На тот момент CI queued. |
| FIX-01 | `research_input.py`: `RTBResearchInputService.preview(project_id,snapshot_id,reviewer_id)` через `KnowledgeSnapshotRepository.export_by_id` и `materialize_c_handoff` + `PostgresAResolver.resolve_context/resolve_video`. Exact ID/SHA pins, zero videos, 400 facts/24 videos/2400 observations/1.2 MB fail-closed; no spend/LLM. `tests/test_taxonomy_research_input.py` + `tests/test_taxonomy_research_input_db.py` + CI. | Последний документированный SHA до этого журнала `53cac1171c5eb68df1786b5419ad0f4431c70b06`. На нём CI queued; **не считать завершённым**. |
| FIX-02 | **По коду:** 02-A preflight, 02-B1 B9/A8 pinned PostgreSQL binding, 02-B2a 055 quote/consent/run ledger, 02-B2b precise Pass1 & bounded Pass2 quote, 02-C1 authorize/revoke/idempotent unpaid start, **02-C2a explicit Pass1 atomic reserve → one provider call → usage/unknown spend settlement**. Исправлено 12-decimal quote vs 6-decimal `check_budget` округлением вверх. **Нет:** Pass2/expansion runner, HTTP auth, Review1/Review2, concurrent test proof. | C2a code `f741a14ce4c58bc4535be95044fa73652b8d46e6` (provider) + `97bda5f4ea4109cc189ddd3c907011aeae8179fd` (Pass1); 7 mock provider tests `801dade8e6c656a5aa0eb6f3492c629e480e5b4f`, 7 PG tests `0961bbdc4c25742f5562044f2aa1a2e9f764618f`; CI QUEUED/NOT EXECUTED. |
| FIX-03 | Старт реализации: `deep_v2_transport.py` гарантирует strict JSON Schema, `deep_contracts_v2.py` валидирует факты B9 + evidence IDs A8 + роли direct/problem и ровно 14 полей, уникальность search_signals/validation_focus и корректность графа. **Paid Opus Pass1 и Human Review1 ещё отсутствуют**. | Validator `7326ce3a80f3f0a69648b9410c17d6c88fb13dad`; 7 offline tests `504878e4f09c0ae288ce52735a77138682a8de15`; Linux/Windows CI queued. |

## Команды для проверки

```bash
python -m unittest discover -s tests -p 'test_taxonomy_c_corrector_contracts.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_research_input.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_contracts.py' -v
# c CM_TEST_PG_DSN и развернутой PostgreSQL 17:
python -m intelligence.migrate
python -m unittest discover -s tests -p 'test_taxonomy_research_input_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_binding_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_quote.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_validation.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_provider.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_pass1_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_ledger_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_quote_db.py' -v
# полный CI:
python -m unittest discover -s tests -v
node tests/test_taxonomy_deep_ui.cjs
node tests/test_taxonomy_run_ui.cjs
```

## Не забывать при продолжении

1. **Сначала** проверь свежий HEAD ветки и заключения GitHub Actions на HEAD, а не только комментарии предыдущего чата.
2. Все GitHub Actions по последним коммитам показывали `queued`; **не утверждать, что тесты прошли**, пока нет `conclusion=success`.
3. У B9 `knowledge-snapshot/v1`, `facts[].fact_id` — SHA, а не UUID. У A8 `video-intelligence/v1` evidence — словарь, не `observations[]`. B Stage14 `knowledge_c_generation_runs` — Flat C, а не Deep V2.
4. Провайдер не вызывается на FIX-00/FIX-01; вся тарификация и согласие — отдельно и явно в FIX-02. Никогда не разрешать paid calls только на основании наличия `research_input_sha256`.
5. Отдельные ветки SRE, Search Planner, Video Collector, Analytics развиваются параллельно; RTB экспортирует контракты, но не изменяет их внутренности.
6. Новый чат, закрывая каждый этап, обязан добавить в этот журнал дату, исходный commit, список файлов, команды тестирования, **реальные** выводы CI, проблемы и SHA закрытия; только затем зачеркнуть его в общем списке.


## 2026-10-26 — продолжение FIX-02, DB freeze

- **Новые файлы:** `intelligence/migrations/054_tb_deep_v2_research_binding.sql`, `intelligence/taxonomy_builder/deep_v2_binding_repository.py`, `tests/test_taxonomy_deep_v2_binding_db.py`.
- **Область 02-B1:** исторически проверенная B9 snapshot/handoff связка теперь может быть закреплена одним неизменяемым RTB Research Input на один snapshot; повтор возвращает старую запись; ключи подписей и отношений перепроверяются на SQL INSERT trigger. Никаких quote/consent/provider POST.
- **Тесты:** 6 PostgreSQL17 сценариев (idempotent pin/replay, foreign project/actor, forged signed payload, SQL immutable UPDATE/DELETE, БД identity, no paid authorization) добавлены в отдельный workflow. Реального результата GitHub Actions **нет**: queued.
- **Исправления из инспекции:** `canonical()` A8 возвращает `bytes`, поэтому нельзя вызывать `.encode('utf-8')` после него. Исправлено в FIX-01 Research Input и FIX-02 lifecycle. Zero-video fixture переработана через реальный `build_snapshot()`, вместо недостоверного удаления видео из готового B9 snapshot.
- **Следующий шаг:** открыть и отладить CI на новом HEAD, затем подготовить FIX-02-B2 (точная JSON Schema и prompt version для стоимостного request; отдельная immutable quote) и FIX-02-C (платное execution с явным consent). **Не зачёркивать весь FIX-02**, пока эти блоки не закрыты.


## 2026-10-26 — продолжение FIX-02 B2a: денежный ledger, НЕ платный runner

- **SQL:** `intelligence/migrations/055_tb_deep_v2_budget_ledger.sql` (immutable quote, independent consent/revocation, project-scoped run + per-stage attempt). Legacy Deep V1 `tb_runs` и B Stage14 Flat C таблицы не изменены.
- **SQL проверки:** FK к закреплённому B9/A8 Research Input; verified model snapshot; quote TTL; consent min >= quoted amount и тот же SHA; запрет run после revocation; start только в `pass1_pending`; attempt только в подходящем `*_running` с заранее зарезервированной суммой; уникальность стадии; immutable identity; uncertain outcome не снимает автоматически reservation.
- **QA:** `tests/test_taxonomy_deep_v2_ledger_db.py` (8 PG scenarios) и job в `.github/workflows/rtb-module-contracts.yml`. CI в GitHub ранее `queued`, полных результатов нет; **не ставить FIX-02[x]**.
- **Нет реализации:** production quote issuer по действительной окончательной Deep V2 schema/prompt, серверное explicit consent + start endpoints, streaming/model dispatcher и money settlement. НЕЛЬЗЯ отправлять Opus до их реализации.
- **Next:** довести FIX-02-B2b и FIX-02-C. Новый чат должен прежде сверить CI и слепо не доверять миграции без настоящего PostgreSQL smoke.


## 2026-10-26 — FIX-02-B2b/C1: настоящая quote и неоплачиваемый start

- `deep_v2_transport.py`: закреплён transport contract `deep-v2/1`, 14 полей для каждого node, запрет дополнительных ключей, разделение approved B9 facts и original A8 evidence, строгая OpenRouter `json_schema`, оригинальные trust и SHA, user content трактуется как untrusted evidence. Второй проход пока только ограничен по размеру Human Review (24 kB) и coverage (12 kB); не заменяет валидатор результата FIX-03.
- `deep_v2_quote_repository.py`: `quote()` формирует реальный payload первого Opus-запроса, `reservation_cost` по точным сообщениям; второй проход резервирует консервативную верхнюю границу, потому что Review1 неизвестен на момент quote. SHA первой заявки, prompt, модель, version, B9/A8 binding и сумма сохранены иммутабельно. `authorize()` требует тот же quote SHA и актуальную цену/каталог, `revoke()` отзывает разрешение, `start()` идемпотентно создаёт только `pass1_pending`, не вызывает модель и не резервирует деньги.
- Добавлено 7 offline тестов (`test_taxonomy_deep_v2_quote.py`) и 7 PostgreSQL17 integration tests (`test_taxonomy_deep_v2_quote_db.py`); оба включены в RTB Linux/Windows/PG CI. Фактический результат CI не получен — `queued`.
- **Критический незакрытый блок FIX-02-C2:** безопасный единственный вызов провайдера на explicit advance, отдельная stage reservation, проверка exact request SHA / quote capacity / catalog, usage settlement / unknown-cost блокировка и double-submit tests.
- **Следующему чату:** сначала проверить CI (особенно DB 054/055), устранить найденные ошибки, затем реализовать C2. Не утверждать, что готов платный или E2E pipeline. Не сливать PR #32 в main.


## 2026-10-26 — начало FIX-03-A: строгая проверка результата Opus

- Создан `intelligence/taxonomy_builder/deep_contracts_v2.py`, **не изменяя** работающий `deep_contracts.py` Deep V1: проверка 14 обязательных полей без дополнительных свойств, project-owned B9 fact UUID, original A8 observation locators, двух направлений, иерархии, пустых V2-массивов у direction/branch, 1–6 search_signals + validation_focus у гипотез.
- `direct` требует `kind=function/product_type` **и** `source_role=own_product`, `subject_scope=own_product`, `may_support_own_product_claim=true`. Внешние утверждения не становятся свойствами продукта.
- Для `deep_second/deep_expand` проверяется отдельная и однозначная структура `duplicates`; нельзя передавать её в `deep_first`.
- `tests/test_taxonomy_deep_v2_validation.py`: 7 offline тестов + Linux/Windows CI.
- **Ограничения**: настоящая проверка provenance возможна только на Research Input, полученном от `RTBDeepV2BindingRepository.get()`, а не произвольного JSON клиента. Валидатор не делает semantic claim verification и не является Search Readiness Gate. Фактический платный Pass1 и ручной Review1 всё ещё не реализованы.
- **Следующему чату:** сверить GitHub Actions HEAD, разобраться с CI очередью, затем реализовать explicit paid advance Pass1 и атомарную фиксацию verified validated output. Без этого FIX-02/03 не закрывать.


## 2026-10-26 — пользователь изменил правило отметок и проверена очередь CI

- По просьбе пользователя полные FIX-00 и FIX-01 помечены `[x]` + зачёркиванием: **реализация по заявленным функциям выполнена**, QA отдельно `QUEUED / НЕ ПРОВЕРЕНО`.
- На HEAD `110ddd35cbcd53d910f2484fb8484f01c127d82f`: GitHub Actions runs 40299686256 (Windows), 40299686274 (ContentMachine checks), 40299686277 (RTB module: Linux/PG17/Windows), 40299686432 (B Stage14) — все `queued`. На run 40299686277 jobs 121494225049 / 121494225311 / 121494225369 также `queued`; runner не назначен, steps/logs отсутствуют. Это **не failure** тестов и не success. Retry failed job не применим к queued run.
- Исправление кода и новые contract-тесты продолжаются, но статус QA остаётся `NOT EXECUTED` до выдачи runner. Если Actions и дальше висит в очереди, проверить владельцу репозитория Settings → Actions → Runners, Actions allowance/billing, workflows permissions; самостоятельно причины без run-логов подтвердить нельзя.


## 2026-10-26 — завершён по коду C2a: один платный Deep V2 Pass1; QA остаётся QUEUED

- `deep_v2_provider.py` — не наследует Deep V1 transport: отправляет **только** заранее верифицированный `deep-v2/1` `json_schema` payload, один вызов OpenRouter, без fallback/paid retries; отличает полную подтверждённую usage.cost от неопределённой.
- `deep_v2_pass1.py` — callable ТОЛЬКО из явного server POST в будущем; до сети в транзакции сверяет scope, B9/A8 replay, quote SHA, актуальный Opus catalog и consent, атомарно резервирует бюджет и вставляет уникальную `deep_first` attempt. После единственного request валидирует 14-field дерево и отдельно завершает attempt, сохраняя `charged_usd` или `uncertain` с удержанным резервом. Никакого автоматического повтора. GET через `inspect_pass1()` только читает.
- `tests/test_taxonomy_deep_v2_provider.py` — 7 offline mocked cases, `tests/test_taxonomy_deep_v2_pass1_db.py` — 7 PostgreSQL17 mocked cases. Подключены к `.github/workflows/rtb-module-contracts.yml`, но workflow **НЕ запускался на runner**.
- Исправлена доказуемая несовместимость `deep_v2_quote_repository.quote().required_usd` (точное 12-значное значение) с `cost_estimator.check_budget()` (только 6 decimals). На клиент отдается округлённая **вверх** до 6 decimals сумма для согласия; точный 12-значный reservation + quote SHA сохраняются в SQL. `tests/test_taxonomy_deep_v2_quote_db.py` дополнен проверкой.
- **QA проблема:** GitHub run 40299686277 имел jobs `rtb-contract-linux`, `rtb-postgres-17`, `rtb-contract-windows` — все `queued`, `fetch_workflow_job_steps` для 121494225049 вернул `[]`. Это подтверждает отсутствие выполнения, а не неуспех теста. Retry failed не применим. Требуется runner/Actions quota/settings с правами владельца; новые commit runs тоже мониторить.
- **Следующая реализация:** полноценный Pass2/expansions после Human Review №1, SQL protected approval, authenticated route glue; QA и межэтапные тесты. FULL FIX-02/03 не зачёркивать, пока продуктовый контракт не закончен.


### Дополнительный аудит FIX-02-C2a, 2026-10-26

- Consistency: `start()` и `advance_pass1()` теперь захватывают lock в одном порядке `quote → run → consent → attempt` во избежание deadlock между повторным start и оплачиваемым advance. DB concurrency test подтверждение **НЕ ВЫПОЛНЕНО**, `queued`.
- Midflight reviewer/consent revoke: оплаченный ответ всегда должен попасть в ledger; post-request settlement не зависит от действительности старого reviewer authorization, поскольку больше не создаёт расхода. Если consent revoked во время вызова, статус run `cancelled` с сохранённым фактическим charge.
- OpenRouter API key: разрешение credentials единожды **до** reserve/attempt insert, чтобы не оставлять искусственные денежные блокировки от второго получения ключа.
- `tests/test_taxonomy_deep_v2_pass1_db.py`: теперь **9** тестов с конкурентным двойным advance и с отзывом consent во время provider request. Все используют mock sender и не делают платных запросов.
- [Диагноз очереди и команды локальных QA](RTB_CI_RUNNER_BLOCKER_RU.md): даже старые B Stage14/Windows workflows не получили runner; steps=[]; пользователь может проверить Actions settings/runners/billing, затем повторить тесты. Доказанного тестового FAIL нет.


## 2026-10-26 — продолжение FIX-04-A и FIX-05-A, исправлена реальная ошибка SQL 055

- **Обнаруженная ошибка:** в `055_tb_deep_v2_budget_ledger.sql` две триггерные функции были объявлены с `LANGUAGE plpgsql AS $` / `END $;`; это невалидный PL/pgSQL и заблокировало бы всю PostgreSQL-миграцию. Исправлено на `$$` / `END $$;`, commit `cba603de9e205d73dfcce5cd99c14899e246a2ed`.
- **SQL 056:** `056_tb_deep_v2_human_reviews.sql` хранит неизменяемый review1/review2 `candidate_sha256/approved_sha256/decisions/approved_nodes`, tenant composite FK, reviewer authorization и review state + prior Pass1 success. Никаких provider triggers. Commit `64e92982ac1d25b947a61f25cfd5ef93db6f8431`.
- **FIX-04-A internal:** `deep_v2_human_review.py` требует точного решения по каждому предложенному узлу, допускает ограниченные добавления/редактирования/merge, не сохраняет orphan children, валидирует все 14 полей и genuine signed B9/A8 refs. `deep_v2_review_repository.py` выполняет проверку и в одной транзакции immutable H1 INSERT + переход к `pass2_pending`; сохраняет deterministic coverage, проверяет Pass2 size caps. GET не вызывает модель. Commits `2c97dfcc7b2109828a95c395251ef14001edb19a` / `c20f83c0e000777c6469b6d39546e246fef81d80`.
- **FIX-05-A:** `deep_v2_followup.py` проверяет, что Pass2 и expansions содержат все утверждённые H1 nodes **строго без изменения всех 14 полей**, а отклонённые Pass1 IDs не возвращаются. Commit `01929337266a9fea9d7730bf5890cfed86ee6b10`.
- **QA:** 9 unit review1 tests (`test_taxonomy_deep_v2_review.py`), 6 PostgreSQL17 integration tests (`test_taxonomy_deep_v2_review_db.py`), 8 pure H1-lock tests (`test_taxonomy_deep_v2_followup.py`), 3 SQL static tests (`test_taxonomy_deep_v2_sql_static.py`); CI workflow обновлён. **Все данные о CI ранее показывали QUEUED, не PASS.** Нет утверждения о прохождении тестов.
- **Следующая работа:** paid explicit Pass2, reliable capacity bound and stage settlement; User-Facing Human Review1 UI, approved-H1 source graph lock; затем quality gate/Human Review2. Весь FIX-04 и FIX-05 оставить `[ ]` до полного DoD; уже реализованные кодовые подэтапы зачёркнуты.


## 2026-10-26 — FIX-05-B: оплачиваемый Pass2 реализован по коду

- `intelligence/taxonomy_builder/deep_v2_pass2.py`: внутренний метод `advance_pass2(project_id, run_id, reviewer_id, sender=?, api_key=?)`, требующий ровно состояние `pass2_pending`, неизменённые `B9/A8` Research Input + цитирования, подтверждённый H1 `approved_sha256` и существующую `deep_first` попытку, активный unrevoked consent и актуальные signed Opus 5.5 model/catalog/prices.
- Точный Pass2 `deep-v2/1` JSON payload строится только из замороженных фактов/наблюдений, утверждённого дерева и зафиксированной карты покрытия. Повторно считается `reservation_cost` и сравнивается с `required_pass2_reservation_usd` исходной сметы и остатком бюджета. Слишком крупный prompt блокируется ДО сети, без оплачиваемой попытки.
- Отдельная SQL транзакция `pass2_running` + `tb_deep_v2_attempts(stage=deep_second)` резервирует деньги до единственного API вызова. При подтверждённом расходе `charged_usd` и проверенный результат/quality сохраняются; invalid/rewrite H1 оплаченный ответ получает `failed`, без retry. При timeout или неизвестном usage `uncertain`, резерв удерживается. Одновременные POST ограничены SQL lock и UNIQUE(stage).
- Качество проверяется детерминированным `quality_gate` legacy V1 core, но с полной strict V2 в attempt: `review2` только при `passed`, иначе `expand_pending`; автоматический платный expansion **не запускается**. `inspect_pass2` читает status/usage/tree/quality без вызова Opus.
- ВАЖНО: quote TTL применяется к созданию consent/start; Review1 может длиться более 30 минут, поэтому Pass2 не блокируется истечением исходного TTL, **но** заново подтверждает актуальную версию модели, стоимость и предел бюджета. Это задокументированная продуктовая граница, проверяется тестом.
- `tests/test_taxonomy_deep_v2_pass2_db.py`: **11 PostgreSQL17 моковых** сценариев (one-shot, H1 rewrite rejection, unknown spend, timeout, budget cap, concurrent double-dispatch, revoke before call, duplicate/quality gate, exact B9/A8/H1 prompt, charged wrong model и delayed H1 with old quote TTL). Ни один тест не вызывает реальный OpenRouter.
- **QA:** workflow `.github/workflows/rtb-module-contracts.yml` пополнен Pass2 PostgreSQL тестом. GitHub Actions остаётся `queued`, поэтому **тесты НЕ подтверждены**. Общий FIX-05 НЕ завершён: остаются explicit expansion, UI/HTTP glue, full QA.
