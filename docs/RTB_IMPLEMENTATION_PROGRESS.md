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
- [ ] **RTB-FIX-02 — Deep V2 C-run, immutable B9/A8 input, quote/consent and paid lifecycle.** 02-A/B1/B2a/B2b/C1/C2a/C2b **завершены по backend-коду** (оба обязательных прохода и два paid expansion с отдельным согласием). **Осталось** authenticated HTTP routing, unknown-spend reconciliation и QA. **QA: QUEUED/НЕ ПРОВЕРЕНО**, см. [RTB_FIX02_C_RUN_RU.md](RTB_FIX02_C_RUN_RU.md).
- [ ] **RTB-FIX-03 — Strict 14-field Deep V2 Schema, grounded validation and Pass1.** 03-A validator/transport и 03-B внутренний single-call оплачиваемый Pass1 + сохранённое H1-кандидатное дерево **реализованы по коду**. **Осталось** авторизованное пользовательское API и QA; **QA: QUEUED/НЕ ПРОВЕРЕНО**.
- [ ] **RTB-FIX-04 — Human Review №1.** 04-A внутреннее полное approve/reject/edit/merge/add и неизменяемая SQL 056 публикация H1 с переходом `review1 → pass2_pending` **реализованы по коду**; **НЕ ГОТОВ** пользовательский UI и публичный auth-aware API. **QA: QUEUED/НЕ ПРОВЕРЕНО**.
- [ ] **RTB-FIX-05 — Opus Pass2 / ≤2 manual expansions.** Подэтапы 05-A (immutable H1), 05-B (explicit paid Pass2) и 05-C (**оба независимо оцениваемых/утверждаемых и оплачиваемых expansions; SQL 057, no-retry, max 2**) **РЕАЛИЗОВАНЫ ПО BACKEND-КОДУ**; HTTP auth/UI остаются в FIX-09, **QA: QUEUED / НЕ ПРОВЕРЕНО**. [FIX-05-C contract](RTB_FIX05_EXPANSIONS_RU.md).
- [ ] **RTB-FIX-06 — Deterministic Quality Gate, Search Readiness Gate, Human Review №2.** Подэтапы **06-A/B backend РЕАЛИЗОВАНЫ ПО КОДУ**: signed source-bound Readiness Gate, 14-field H1 replay, blocking search gaps/duplicates, SQL 058 append-only gate report, immutable H2 decisions + atomic `review2 → approved` (это НЕ публикация). **Остаётся** user-facing auth/UI и реальный QA. **QA: QUEUED / NOT EXECUTED**. [Подробности](RTB_FIX06_SEARCH_READINESS_REVIEW2_RU.md).
- [x] ~~**RTB-FIX-07 — Atomic immutable Deep V2 Publication + 11-field V1 projection + SRE bridge.**~~ **BACKEND КОД ЗАВЕРШЁН; QA: QUEUED / НЕ ПРОВЕРЕНО**. SQL 059 отдельная B9-native immutable publication + SRE link, atomic `approved → published`, signed H2/source pins, 14→11 exact V1 core, replay-verified read API, B9 SHA→UUID5 reuse, conflict rollback, no legacy overwrite. [Полный отчёт](RTB_FIX07_PUBLICATION_SRE_RU.md).
- [x] ~~FIX-07-A: 14-field V2 + 11-field V1 immutable publication, подписанная H2/Research Input/Readiness lineage и один project/run~~ — **КОД ВЫПОЛНЕН, QA QUEUED**.
- [x] ~~FIX-07-B: same-transaction SRE mirrored categories, stable project-scoped B9 fact UUID, reuse prior approved facts and fail closed on conflicting UUID/content~~ — **КОД ВЫПОЛНЕН, QA QUEUED**.
- [x] ~~FIX-07-C: authenticated read-only published Deep V2/V1 core, idempotent no-cost repeat, error rollback/concurrent publish regression fixtures~~ — **КОД ВЫПОЛНЕН, QA QUEUED**.
- [x] ~~**RTB-FIX-08 — RTB-owned read-only V1/V2 publication export для Search Planner и SRE.**~~ **BACKEND КОД ЗАВЕРШЁН; QA: QUEUED / НЕ ПРОВЕРЕНО**. Explicit `rtb-publication-export/v1` (11 ключей) и `/v2` (14 ключей + original A8), signed published DTO, server-authenticated read by publication UUID with B9/A8 replay, Search Planner V1-ready adapter, SRE readonly reference. Без модификаций файлов SP/SRE. [Технический отчёт](RTB_FIX08_PUBLIC_EXPORT_RU.md).
- [x] ~~FIX-08-A: версионированные, полностью source-pinned V1(11)/V2(14) publication DTO с отдельным deterministic export SHA и только явным version negotiation~~ — **КОД ВЫПОЛНЕН, QA: QUEUED**.
- [x] ~~FIX-08-B: read-only проектный lookup по immutable publication UUID, B9/A8 replay, H1/H2 gates, approved SRE link, никакого provider или мутации DB~~ — **КОД ВЫПОЛНЕН, QA: QUEUED**.
- [x] ~~FIX-08-C: consumer adapters: существующий Search Planner V1 validator + HypothesisContext без смены SQL input_loader; SRE version pointer без SRE INSERT~~ — **КОД ВЫПОЛНЕН, QA: QUEUED**.
- [ ] **RTB-FIX-09 — Unified Taxonomy UX и legacy hardening.** Подэтапы **09-A и 09-B реализованы по коду**: server-bound localhost-only reviewer, Origin + explicit paid HTTP, отдельная B9 Deep V2 панель на странице проекта с quote/consent, H1/H2, expansion, publish/export. Legacy Flat/Deep V1 routes не переписаны. **Осталось** полноценное 14-field form UX, 2 expansion UI edge cases, реальное PostgreSQL17/Node QA, enhanced security/reconciliation. **QA: QUEUED / НЕ ПРОВЕРЕНО**. [Документ](RTB_FIX09_HTTP_UI_RU.md).
- [x] ~~FIX-09-A: отдельный B9-native Deep V2 local HTTP adapter, серверный (не клиентский) reviewer ID, PostgreSQL project auth, localhost + Origin для всех mutations, explicit paid POST подтверждение, no GET-paid dispatch~~ — **КОД ВЫПОЛНЕН; QA: QUEUED/НЕ ПРОВЕРЕНО**.
- [x] ~~FIX-09-B: самостоятельная B9 Deep V2 UI панель на уровне проекта без legacy questionnaire, exact quote/consent/paid stage buttons, H1/H2 JSON edit/merge/add, no auto-paid refresh, отдельные expansions, publish/V1-V2 export и безопасный textContent~~ — **БАЗОВЫЙ КОД ВЫПОЛНЕН; QA: QUEUED/НЕ ПРОВЕРЕНО**.
- [x] ~~FIX-09-C1: браузерная проверка двух отдельно оплачиваемых Expansion с отдельными quote/consent и блокировкой третьей кнопки~~ — **тест написан и пройден в изолированной JS V8 mock-среде, GitHub Node runner всё ещё QUEUED**.
- [ ] FIX-09-C2: полноценный 14-field редактор полей/продвинутый review merge/reparent UI, остающиеся error/reload/cancel/adversarial UX tests; B9 reviewer configuration support; real PG HTTP E2E. **НЕ ЗАВЕРШЁН**.
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
- [x] ~~FIX-02-C2b backend: paid Pass2 и два отдельно разрешаемых expansions с atomically reserved unique stage attempts~~ — **КОД РЕАЛИЗОВАН, QA: QUEUED/НЕ ПРОВЕРЕНО**. 
- [ ] FIX-02-C2c: authenticated HTTP POST routes и controlled unknown-spend reconciliation остаются; внешнее API ещё НЕ РЕАЛИЗОВАНО.
- [ ] FIX-02-C3: настоящий two-pass backend в отдельном RTB-owned API без изменения внутренних файлов других модулей; passage к Human Review1/2 и quality gate остаётся FIX-03..06.
- [x] ~~FIX-03-A: strict Deep V2/1 14-field request + role-aware `validate_deep_v2_response`, B9 approved own_product rules, original A8 ref whitelist, no source drift, offline validation fixtures~~ — **код есть, CI queued; FIX-03 не закрыт**.
- [x] ~~FIX-04-A: полное принятие Human Review №1 (approve/reject/edit/add/merge) c 14-field B9/A8 graph validation, immutable SQL 056 H1 и атомарным `pass2_pending`~~ — **реализовано по коду, QA: QUEUED/НЕ ПРОВЕРЕНО**, UI/route в 04-B.
- [x] ~~FIX-05-A: чистая защита H1 на Pass2/expansion — точное сохранение одобренных 14 полей и исключение отвергнутых IDs~~ — **реализовано по коду, QA: QUEUED/НЕ ПРОВЕРЕНО**.
- [x] ~~FIX-05-B: внутренний отдельный оплачиваемый Pass2 с immutable H1, SHA B9/A8, fresh model/price, exact payload budget cap, единственной оплачиваемой попыткой и качественным gate~~ — **КОД РЕАЛИЗОВАН, QA: QUEUED/НЕ ПРОВЕРЕНО**; реальные OpenRouter запросы не совершались.
- [x] ~~FIX-05-C: отдельная immutable quote/consent и top-up резерва на каждое expansion; максимум 2, один платный POST на стадию, immutable прежнее дерево, quality gate, unknown charge stop~~ — **BACKEND-КОД РЕАЛИЗОВАН; QA: QUEUED/НЕ ПРОВЕРЕНО**. HTTP auth/UI отдельно FIX-09.
- [x] ~~FIX-06-A: отдельный deterministic Search Readiness Gate с source SHA, 14-field/H1 lock, B9/A8 roles, duplicates/coverage/specific search signals, signed blocking report~~ — **backend-код выполнен; QA: QUEUED / НЕ ПРОВЕРЕНО**.
- [x] ~~FIX-06-B: неизменяемый Human Review №2 с полным approve/reject/edit/add/merge, повторным Gate после правок, SQL 058 source-pinned passing report и атомарным `review2 → approved`~~ — **backend-код выполнен; QA: QUEUED / НЕ ПРОВЕРЕНО**. UI/auth отнесены к FIX-09.
- [x] ~~RTB SQL static regression: миграция 055 PL/pgSQL dollar quote `$$`, миграция 056 Review1+2 и тест баланса delimiters~~ — **код исправлен, QA: QUEUED/НЕ ПРОВЕРЕНО**.
- [ ] QA-signoff FIX-00/01/02: получить фактические прогоны Linux/Windows/PostgreSQL17 и исправить ошибки. FIX-00/01 отмечены [x] за готовый **код**, но до QA PASS не являются подтверждённо протестированными.

## Журнал изменений / проверок

| Этап | Что реализовано | Коммиты / текущее состояние |
| --- | --- | --- |
| FIX-00 | `research_contracts_v2.py`: проверка подлинности B9 approval SHA; стабильный project-scoped fact UUID5, original A evidence index, 14→11 core. Реальная 2-fact B9 fixture, отдельный Linux/Windows workflow, аудит ownership/миграций. | Начальная точка `55ed13123fbd4999f70bc8d0780dadbaec2d04bf`. На тот момент CI queued. |
| FIX-01 | `research_input.py`: `RTBResearchInputService.preview(project_id,snapshot_id,reviewer_id)` через `KnowledgeSnapshotRepository.export_by_id` и `materialize_c_handoff` + `PostgresAResolver.resolve_context/resolve_video`. Exact ID/SHA pins, zero videos, 400 facts/24 videos/2400 observations/1.2 MB fail-closed; no spend/LLM. `tests/test_taxonomy_research_input.py` + `tests/test_taxonomy_research_input_db.py` + CI. | Последний документированный SHA до этого журнала `53cac1171c5eb68df1786b5419ad0f4431c70b06`. На нём CI queued; **не считать завершённым**. |
| FIX-02 | Backend: frozen B9/A8 Research Input; signed Opus quote; consent/revoke/start; reserved exactly-once Pass1, Pass2 и отдельные explicit expansions, per-stage one-call settlement/uncertain stop. SQL 054–057. **Осталось:** auth-aware HTTP, reconciler для locked unknown-spend и фактические PG CI. | FIX-05-C SQL057 `8a4e6ec4b260874b4e91a834fc509f80bb59a618`, repository `11cb3a50d8864a8b60b35c2e200aa79bfaef3df9`, 12 mocked DB scenarios `7c9a471bfe61a19a258578e43cf823f514100fcb`, 4 pure transport cases `2b5dbbe3c111d1dce6b0fc56f6161040539d4447`. QA NOT EXECUTED. |
| FIX-03 | Strict 14-property B9/A8-grounded validator, Pass1 reserve/one Opus call, paid known-cost settlement and H1 candidate persistence **backend реализован**. HTTP endpoint и фактические CI ещё отсутствуют. | QA: QUEUED / NOT RUN. |
| FIX-07 | Код завершён: SQL059 автономные B9-native publication/SRE link, signed H2 source-mapping guard, 14-key DeepV2 + 11-key V1 lossless projection, transactional SRE fact reuse/new taxonomy and active pointer, SHA-checked reads and repeat no-op. 8 pure + 11 PG17 mocked tests, + SQL059 static. **QA: QUEUED / NOT EXECUTED**. | Code SQL `2209bbdc71b4c9d2096504c029df8c493b69437b`, SRE adapter `d062b6046d592a2e24aeadc29ab1f5979046f7af`, publication `f7f07171f3c0070162fe4924dbba200870943397`; [doc](RTB_FIX07_PUBLICATION_SRE_RU.md). |

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
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_expansion.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_expansion_db.py' -v
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
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_readiness.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_review2_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_sre_projection.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_v2_publication_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_publication_export_v2.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_publication_export_v2_db.py' -v
- **Следующая работа:** paid explicit Pass2, reliable capacity bound and stage settlement; User-Facing Human Review1 UI, approved-H1 source graph lock; затем quality gate/Human Review2. Весь FIX-04 и FIX-05 оставить `[ ]` до полного DoD; уже реализованные кодовые подэтапы зачёркнуты.


## 2026-10-26 — FIX-05-B: оплачиваемый Pass2 реализован по коду

- `intelligence/taxonomy_builder/deep_v2_pass2.py`: внутренний метод `advance_pass2(project_id, run_id, reviewer_id, sender=?, api_key=?)`, требующий ровно состояние `pass2_pending`, неизменённые `B9/A8` Research Input + цитирования, подтверждённый H1 `approved_sha256` и существующую `deep_first` попытку, активный unrevoked consent и актуальные signed Opus 5.5 model/catalog/prices.
- Точный Pass2 `deep-v2/1` JSON payload строится только из замороженных фактов/наблюдений, утверждённого дерева и зафиксированной карты покрытия. Повторно считается `reservation_cost` и сравнивается с `required_pass2_reservation_usd` исходной сметы и остатком бюджета. Слишком крупный prompt блокируется ДО сети, без оплачиваемой попытки.
- Отдельная SQL транзакция `pass2_running` + `tb_deep_v2_attempts(stage=deep_second)` резервирует деньги до единственного API вызова. При подтверждённом расходе `charged_usd` и проверенный результат/quality сохраняются; invalid/rewrite H1 оплаченный ответ получает `failed`, без retry. При timeout или неизвестном usage `uncertain`, резерв удерживается. Одновременные POST ограничены SQL lock и UNIQUE(stage).
- Качество проверяется детерминированным `quality_gate` legacy V1 core, но с полной strict V2 в attempt: `review2` только при `passed`, иначе `expand_pending`; автоматический платный expansion **не запускается**. `inspect_pass2` читает status/usage/tree/quality без вызова Opus.
- ВАЖНО: quote TTL применяется к созданию consent/start; Review1 может длиться более 30 минут, поэтому Pass2 не блокируется истечением исходного TTL, **но** заново подтверждает актуальную версию модели, стоимость и предел бюджета. Это задокументированная продуктовая граница, проверяется тестом.
- `tests/test_taxonomy_deep_v2_pass2_db.py`: **11 PostgreSQL17 моковых** сценариев (one-shot, H1 rewrite rejection, unknown spend, timeout, budget cap, concurrent double-dispatch, revoke before call, duplicate/quality gate, exact B9/A8/H1 prompt, charged wrong model и delayed H1 with old quote TTL). Ни один тест не вызывает реальный OpenRouter.
- **QA:** workflow `.github/workflows/rtb-module-contracts.yml` пополнен Pass2 PostgreSQL тестом. GitHub Actions остаётся `queued`, поэтому **тесты НЕ подтверждены**. Общий FIX-05 НЕ завершён: остаются explicit expansion, UI/HTTP glue, full QA.


## 2026-10-26 — FIX-05-C: до двух отдельно авторизованных платных расширений

- **Миграция 057:** `intelligence/migrations/057_tb_deep_v2_expansion_consent.sql`. Отдельные append-only `tb_deep_v2_expansion_quotes`, `tb_deep_v2_expansion_consents`, `tb_deep_v2_expansion_revocations`. Уникальность `(project_id,run_id,expansion_no)`, `expansion_no ∈ {1,2}`, SHA точного Opus request/prompt/previous-tree/quality, fresh model catalog. SQL 057 расширяет **только RTB-owned** триггеры `tb_deep_v2_run_guard` и `tb_deep_v2_check_attempt_insert`: увеличение разрешённого бюджета возможно лишь после immutable expansion consent, а attempt требует совпадающие stage/request/price и неснятый consent.
- **Новые методы:** `deep_v2_expansion.py`: `quote_expansion` (подтверждение не создаётся), `authorize_expansion` (отдельное человеческое согласие на верхнюю округлённую сумму, SQL transactional budget top-up), `revoke_expansion`, `advance_expansion` (проверка утверждённого H1 и предыдущего результата, exact request SHA, price/catalog refresh, один reserve → один paid POST → known usage settlement либо uncertain hold). После первого некачественного результата — `expand_pending`; после второго — `blocked_quality`, третьего платного вызова нет. При passing quality — `review2`.
- **Промпт `deep_expand`:** включает `last_approved_or_generated_tree` с SHA в quote и `review1` original; 14-field H1 + все ранее сохранённые узлы неизменны, отклонённые Pass1 ID запрещены. Неиспользуемый `expansion_base` в Pass1/Pass2 отклоняется.
- **Тесты:** 12 PostgreSQL17 mocked tests `test_taxonomy_deep_v2_expansion_db.py`: exact no-cost quote, explicit consent and budget top-up, wrong amount/sha, оба платных расширения/no third, previous tree, invalid H1 after charge, uncertain costs, revoked consent, concurrent double-advance. Четыре offline tests `test_taxonomy_deep_v2_expansion.py` + SQL057 static check; подключены в Linux/Windows/PG RTB workflow.
- **QA:** код выполнен и `[x]` зачёркнут именно подэтап 05-C. Полный FIX-05/02 остаётся `[ ]`: HTTP auth/UI, reconciliation и фактические PostgreSQL CI ещё не готовы. **TESTS QUEUED / NOT EXECUTED**, ни один реальный Opus вызов не выполнялся. Документ [RTB_FIX05_EXPANSIONS_RU.md](RTB_FIX05_EXPANSIONS_RU.md).

- **Дополнительные денежные adversarial cases:** отзыв expansion consent во время уже отправленного запроса не отменяет подтверждённое списание; новый immutable model catalog/цена после выпуска quote блокирует старую смету до подтверждения. `test_taxonomy_deep_v2_expansion_db.py` расширен до **12** PostgreSQL17 mocked scenarios; GitHub Actions всё ещё не выполнил их.


## 2026-10-26 — FIX-06-A/B: Search Readiness + Human Review2, backend закончен

- `deep_v2_readiness.py` — deterministic `rtb-search-readiness/v1`, source-bound strict 14-field validation, original A8 observation vs verified own-product fact separation, H1 immutable, rejected Pass1 IDs excluded, severe gap/duplicate/novelty blockers, distinct non-generic search signals and validation_focus; reproducible SHA отчёта. Нет оплачиваемых вызовов и нет гарантий реальных просмотров.
- `intelligence/migrations/058_tb_deep_v2_search_readiness.sql` — отдельный immutable `tb_deep_v2_readiness_reports`, FK run/project/actor, same B9/A8 source SHA, last completed paid Pass2/Expansion stage + `expansion_count`, passing report с точным candidate/approved SHA. Переписан только RTB-owned `tb_deep_v2_review_guard`, чтобы review_no=2 без passing report отклонялся PostgreSQL.
- `deep_v2_review2_repository.py`: `preview_review2` только читает, `submit_review2` атомарно (1) применяет human decisions, (2) перепроверяет H1/B9/A8, (3) повторно запускает Gate на **уже отредактированном** дереве, (4) сохраняет readiness report и immutable H2, (5) переводит run `review2→approved`. `get_review2` локально сверяет SHA. **`approved` НЕ является published taxonomy**, публикация FIX-07.
- Unit: 10 тестов `test_taxonomy_deep_v2_readiness.py`. PG17 mocked E2E: 9 тестов `test_taxonomy_deep_v2_review2_db.py`, SQL058 static review guard в `test_taxonomy_deep_v2_sql_static.py`. Linux/Windows/PG jobs подключены в `.github/workflows/rtb-module-contracts.yml`.
- **QA пока не пройдено:** GitHub Actions остаётся `queued`, локально нет PostgreSQL/psycopg, настоящая миграция 058 не выполнялась. Full FIX-06 остаётся `[ ]` до UI/auth, но оба **завершённых backend подэтапа [x] зачёркнуты**, как запросил пользователь. План следующего этапа — FIX-07 publication и SRE bridge.


## 2026-10-26 — FIX-07 публикация + SRE полностью реализованы по backend-коду

- `059_tb_deep_v2_publication_sre.sql`: `tb_deep_v2_publications`, `tb_deep_v2_sre_links`, `status='published'`, SQL FK/guard только для REAL B9-native V2 run (не legacy `tb_runs`). PostgreSQL на INSERT требует совпадающие неизменяемые H2 approved 14-field tree, Search Readiness SHA/approved status, source A context/B9 snapshot SHA, B9 fact mapping и утверждённую SRE checksum. Два immutable triggers.
- `deep_v2_sre_bridge.py`: стабильные category UUID5 project+node, reused UUID B9 fact SHA, не создавать дубликаты `sre_facts` если существующее утверждённое содержимое побайтно совпало; совпавший UUID с другой строкой/проектом → FAIL CLOSED + rollback. Original SRE quotes только из настоящих B9 `citations[].source_quote`; A8 видео — search hints, примеры остаются illustrative, не `verified_video`.
- `deep_v2_publication.py`: explicit `publish` только для `approved` и нулевого `reserved_usd`; replay genuine source и H2 readiness, atomic SRE profile/fact/category/version/active link + immutable RTB publication в одной транзакции; `get_publication(projection='deep-v2'|'legacy-core-v1')` SHA проверяет V2 14 и V1 11 ровно, SRE checksum/category count. Repeat publish no duplicate, no provider call.
- QA tests: 8 offline `test_taxonomy_deep_v2_sre_projection.py`, 11 PG17 mock `test_taxonomy_deep_v2_publication_db.py`: fail after SRE writes rollback, two parallel pub attempts, duplicate B9 SHA reuse, conflicting UUID content, project scoping, immutable SQL. SQL059 guard included in `test_taxonomy_deep_v2_sql_static.py`. Вся схема подключена к RTB Linux/Windows/PG17 CI, но на последнем осмотре workflow были `queued`.
- **Честный статус:** **BACKEND code [x]**, QA: **QUEUED/НЕ ПРОВЕРЕНО**, нет доказательства успешно применённой миграции 059 или прошедших тестов. Ни main, ни A/B/SP/SRE implementation в этой ветке не менялись. Следующий FIX-08 export, после восстановления runner устранить конкретные failures.


## 2026-10-26 — FIX-08 versioned Search Planner / SRE consumer export (backend-код завершён)

- Новый RTB-only `publication_export_v2.py`: `RTBDeepV2PublicationExportRepository.export(project_id,publication_id,server_authenticated_reviewer_id,accepted_versions=...)`; explicit `rtb-publication-export/v1` (ровно 11 V1 ключей) и `rtb-publication-export/v2` (исходные 14 V2 полей + A8 оригинальные observations). B9 human approved quote/fact SHA и A context/approved source SHA сохраняются. Неизвестная версия отклоняется — нет silent downgrade или generated data. Root source replay обязателен; серверный вызов только на `published` и два Human Review. SRE read-only pointer на уже существующую approved taxonomy.
- Legacy Search Planner продолжает использовать `input_loader.py` и `tb_deep_publications` V1. Новый `to_search_planner_v1_snapshot` **совместим по схеме с текущим validator/context_builder**, но **не заменяет SQL loader автоматически**. RTB только поставщик снимка; изменение поведения SP — задача ветки Search Planner. Не тронуты `intelligence/search_planner/**` и `intelligence/relevance/**`.
- QA код: **14 offline** `test_taxonomy_publication_export_v2.py` (включая фактический текущий SP contract и HypothesisContext), **8 mocked PG17** `test_taxonomy_publication_export_v2_db.py`, включены Linux/Windows/PG17 RTB CI. Попытка локального запуска: `git ls-remote` → DNS unable to resolve github.com; local `psycopg` и `psql` отсутствуют. Это НЕ значит, что тесты прошли или упали.
- **Backend FIX-08 code DONE [x]**, **QA: QUEUED / NOT EXECUTED** до реально полученных GitHub workflow logs. Документ [RTB_FIX08_PUBLIC_EXPORT_RU.md](RTB_FIX08_PUBLIC_EXPORT_RU.md). Следующий FIX-09 Auth/UI, затем FIX-10/11 и полноценное тестирование.


## 2026-10-26 — FIX-09-A/B local secure HTTP + независимый B9 V2 UI

- `deep_v2_http.py` смонтирован двумя минимальными GET/POST hooks в `app.py`. Пользовательский `reviewer_id` нельзя предоставить из JSON; identity берётся исключительно из `CM_RTB_REVIEWER_ID` локального server-process (fail-closed 503 если не задана) и проверяется B9 SQL authorization каждого проекта. Origin обязателен для ВСЕХ POST; paid stage требует строго `authorize_paid_call: true` и существующий отдельный budget consent. GET/read/preview не вызывают Opus. Backend не предназначен для публичного multi-user proxy без настоящих сессий/CSRF.
- `site/taxonomy-deep-v2.js`, `site/index.html`, `site/taxonomy-workspace.css`: отдельная B9 панель на странице проекта, не требует legacy questionnaire. Минимальное событие `cm-taxonomy-deep-v2-project-open` добавлено в `site/projects.js`; старый UI не заменяется. Явная смета/конкретная сумма и согласие, независимый paid Pass1/Pass2/до двух Expansion, H1/H2 decisions, explicit publish, no-cost GET/export; DOM только textContent. По возвращению в проект восстанавливается run ID без секретов.
- `tests/test_taxonomy_deep_v2_http.py`: 14 offline security scenarios. `tests/test_taxonomy_deep_v2_ui.cjs`: mocked Node browser path project open→pin B9→quote→consent→start→paid Pass1→H1→paid Pass2→H2→publish→V2 export, запрет автосписания и browser-supplied reviewer. Добавлены в Linux/Windows RTB GitHub Actions. **Тесты пока не выполнялись** из-за queued runners; не утверждать PASS.
- Подэтапы **09-A и базовый 09-B выполнены [x] и зачёркнуты**, весь FIX-09 остаётся **[ ]** до editor/error hardening, настоящего PG17 HTTP E2E и QA. [Подробности](RTB_FIX09_HTTP_UI_RU.md).

- **Узкое независимое исполнение JS mock (2026-10-26):** тест `tests/test_taxonomy_deep_v2_ui.cjs` выполнен в изолированном V8 с mocked Node `fs/vm/assert` адаптерами и реальным кодом `site/taxonomy-deep-v2.js`, `site/index.html`. Результат `exitCode=0`, `RTB Deep V2 B9 UI mock: H1/H2 + 2 separate expansions OK`. Проверено и начальное H1→H2→published дерево, и 2 отдельные quote/consent/paid Expansion до `blocked_quality` без третьего paid UI action. **Не подменять** этим узким результатом настоящий `node tests/test_taxonomy_deep_v2_ui.cjs` или PostgreSQL17 CI, который остаётся в очереди.
