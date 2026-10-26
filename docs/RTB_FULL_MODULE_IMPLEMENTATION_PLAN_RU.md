# Content Machine — RTB FULL MODULE | DEVELOPMENT & INTEGRATION FIX PLAN
Version: 1.0 | 2026-10-26 | Language: RU

## 0. Зафиксированное состояние и стратегия Git

Repository: BanSins7/ContentMachine.
Единственная активная ветка полной разработки RTB: feature/rtb-module-v2.
Base main: 218f5f3163ccc7179a4809ba4386a1d1b6ff80f5 (в main уже объединены A #29 и B #30).
Ветка создана от feature/rtb-c-corrector-v2@deff6c9883229b37c4ebba52a1bce8061ecdbec6, то есть уже включает её 3 коммита Contract Freeze, а также весь RTB, ранее влитый в main.

КРИТИЧЕСКИ ВАЖНО: Git-ветка — полноценный снимок ВСЕГО репозитория. Код RTB в main уже находится в нашей ветке через наследование Git-истории. Физическое дублирование файлов RTB в другую папку не требуется и запрещено, чтобы не появились две расходящиеся реализации. При merge попадут только новые коммиты относительно базы.

Правило разработки: все новые изменения RTB делаются ТОЛЬКО в feature/rtb-module-v2. main не изменяется. Не создавать отдельные ветки для C Corrector, Flat/Deep, дизайна RTB или тестов без необходимости. Каждый FIX заканчивается проверяемым коммитом, отчётом о проверках и обновлением одного Draft PR.

## 1. Что является единым самостоятельным модулем RTB

RTB владеет полным циклом построения и утверждения исследовательской таксономии:

- Legacy questionnaire / approved project context / version-pinned B9 knowledge -> RTB input.
- RTB intake и Pipeline Studio: модель, конфигурация, каталог, подтверждения, immutable versioning.
- Flat Taxonomy V1: fact extraction -> Human Fact Review -> taxonomy generation -> Human Topic Review (+ optional critic), публикация.
- Deep Taxonomy V1: Opus 5.5 Pass1 -> Human Review1 -> Opus Pass2 -> проверка покрытия -> max 2 explicit expansion -> Human Review2 -> immutable publication.
- New Deep V2 / C Corrector: A8 original observations + B9 human-approved facts -> frozen Research Input -> 14-field tree -> 2 reviews -> publication.
- RTB cost/consent/attempt ledger, idempotency, recovery and audit.
- RTB-owned SRE projection adapter и опубликованные выходные контракты V1/V2.
- RTB own API and Taxonomy UI (Studio, cost, runs, Deep review).
- RTB migrations, documentation, offline/Postgres/UI tests and release gating.

Существующее в main: intelligence/taxonomy_builder/{intake,pipeline_contracts,pipeline_repository,catalog,cost_estimator,cost_repository,run_contracts,run_repository,provider,deep_contracts,deep_repository,sre_bridge,context_binding,knowledge_binding,knowledge_execution}.py и соседние версии; site/taxonomy-{studio,cost,run,deep}.js, site/taxonomy-workspace.css; historical tb_ migrations, test_taxonomy_* suites.
Уже написано в предыдущем C Corrector FIX-00: intelligence/taxonomy_builder/research_contracts_v2.py, tests/test_taxonomy_c_corrector_contracts.py, docs/RTB_C_CORRECTOR_STAGE01_CONTRACT_FREEZE.md. Код и тесты пока нельзя называть завершёнными до успешного CI и проверки реальных fixtures.

Только внешние входы RTB:
1. A8: approved immutable context и pinned video-intelligence/v1 evidence, без повторного AI анализа.
2. B9: approved knowledge-snapshot/v1 и knowledge-c-handoff/v1, без повторного MiMo.
3. Model provider: OpenRouter Opus 5.5 только при отдельном осознанном подтверждении расходов.

Только внешние выходы RTB:
1. Flat V1 / Deep V1 historical publication без изменения контракта.
2. Deep V2 immutable canonical 14-field publication + 11-field V1 core projection.
3. SRE export/bridge: stable approved fact IDs, nodes, lineage, taxonomy version; атомарная запись в существующие SRE таблицы там, где это владеет RTB.
4. Read-only published context contract для Search Planner V2 и legacy V1.

Границы владения: нельзя менять реализацию intelligence/project_sources, intelligence/knowledge_extraction, intelligence/relevance, intelligence/search_planner, intelligence/collector, intelligence/qualified_data, intelligence/analytics. При необходимости нового поведения downstream RTB добавляет отдельный RTB-owned export, DTO и contract test; изменения потребителя выполняются в соответствующей ветке. Исключение возможно только по отдельно согласованной интеграционной задаче с явным списком файлов. Для site/index.html и общих серверных роутеров допускаются минимальные RTB route/bootstrap hooks, отмеченные отдельно в PR и проверенные на конфликты. RTB никогда не подтягивает чужие feature-ветки целиком.

## 2. Фиксированные продуктовые принципы

- Не переписывать работающие Flat V1/Deep V1. Ни один старый run/publication не перепаковывается в V2.
- Один реальный источник истины для B9: approved knowledge-snapshot/v1, facts[].fact_id SHA-256; exact immutable snapshot_id и canonical SHA.
- A8 observations — оригинальные source-output evidence с исходным типом, locator и trust; не превращать в product facts, не придумывать полный vision analysis из sparse frames.
- SHA B9 -> project-scoped stable UUID5 только в RTB/SRE adapter; B9 SHA и citations сохраняются.
- Два Opus-pass + максимум два отдельным образом разрешённых expansion. Никакого дополнительного third-LLM или быстрого классификатора.
- Четырнадцать V2 node properties, V1 строго одиннадцать; три новых поля: source_observation_ids, search_signals, validation_focus. У directions/branches новые arrays пустые; hypotheses имеют 1-6 signals, 1-6 focus, 0-12 verified observation IDs.
- Одна иммутабельная база Research Input на весь C-run; все цены, токены, model, prompt version, approvals, SHA и snapshots закреплены.
- No hidden retries / hidden provider spend. В CI только fake provider, реальные расходы только вручную.
- Никаких numeric viral weights, выдуманной эффективности, изменения niche/research_goal без явно утверждённого источника.

## 3. Этапы реализации: один FIX = один контролируемый завершённый результат

### RTB-FIX-00 — Module Isolation, Baseline Audit, Contract Freeze
Текущий статус: КОНТРАКТЫ И АУДИТ РЕАЛИЗОВАНЫ, НО QA НЕ ЗАКРЫТ (GitHub Actions queued).
Задача: проверить всю legacy Flat/Deep инфраструктуру и frozen A8/B9 C contract, зафиксировать внешние зависимости и RTB-only файловую ownership матрицу.
Работы: инвентаризация роутов, UI, SQL, backends, model/config; отследить Flat и Deep V1 E2E; проверить реальный B9 handoff и A evidence; source SHA->UUID mapping; верифицировать уже добавленные C-FIX-00 Python helpers против реальных B9 и A payloads; проверить конфликты миграций 016/017 и 030-053; составить полный manifest существующих файлов и контрактов.
Файлы: intelligence/taxonomy_builder/research_contracts_v2.py; tests/test_taxonomy_c_corrector_contracts.py; docs/RTB_C_CORRECTOR_STAGE01_CONTRACT_FREEZE.md; RTB implementation plan.
QA: 2 B9 facts, 2 A original observations, 3-level tree; negative SHA/scope; V1 test suite; no network; branch vs main diff содержит только RTB-owned files.
DONE: все контракты реальны и тесты зелёные, старое поведение не изменено.

### RTB-FIX-01 — Frozen Research Input (B9 + A8 C1/C2)
Статус: КОД И ТЕСТЫ РЕАЛИЗОВАНЫ; CI QUEUED, QA НЕ ЗАКРЫТ. См. docs/RTB_FIX01_RESEARCH_INPUT_RU.md.
Задача: воспроизводимый вход модели из B9 approved knowledge и A immutable videos.
Работы: knowledge_c_handoffs materialize по id; проверить exact approved context ID, snapshot+output SHA; читать A через PostgresAResolver.resolve_video(), не latest; раздельные facts/observations, trust/role/citation, total input SHA; bounded context, никаких silently dropped facts/videos; read-only idempotent preview; PostgreSQL table for immutable RTB research binding при необходимости.
Файлы: NEW intelligence/taxonomy_builder/research_input.py; возможно RTB-prefixed SQL migration, tests/test_taxonomy_research_input.py.
QA: zero videos, 2 videos, cross-project, stale snapshot, lost output, hash mismatch, conflicting evidence IDs, oversize -> deterministic fail; no LLM/DB writes on read.
DONE: pinned Research Input reproducible, exact A/B provenance, no provider calls.

### RTB-FIX-02 — Deep V2 C Run, Price Quote, Consent & Budget
Статус: НАЧАТ. 02-A Preflight, 02-B1 immutable B9/A8 binding, 02-B2a isolated SQL money ledger, 02-B2b exact Pass1/bounded Pass2 quote, 02-C1 quote→authorize/revoke→unpaid start, **02-C2a internal explicit Pass1 reserve→single-call→usage settlement/unknown hold** реализованы по коду; тесты созданные, GitHub CI QUEUED/NOT EXECUTED. **Осталось:** paid Pass2/expansions, Review1/Review2 state gate, authenticated route, concurrency/DB pass. См. docs/RTB_FIX02_C_RUN_RU.md и docs/RTB_IMPLEMENTATION_PROGRESS.md.
Задача: B9-native Deep run без fictitious tb_fact_review_submissions и без дублирования бюджетных систем.
Работы: явные run modes legacy/deep-v2; B9-bound lifecycle; approved pipeline Opus 5.5 snapshot; fixed research input; stage aware quote+authorisation for Pass1/Pass2/expansion; reserve/charge/unknown errors; idempotency, concurrency, no auto retry; revoke/snapshot/model/pricing drift invalidates permission; preserve B Stage14 single-pass flat c-run, не называть его Deep.
Файлы: intelligence/taxonomy_builder/{deep_repository,run_repository,cost_repository,knowledge_execution,provider}.py по необходимости; RTB-prefixed additive SQL migrations; tests/test_taxonomy_deep_v2_budget*.py.
QA: full paid-stage matrix, stale prices/model/output cap, dual submit, crash after reservation, unknown usage, no double charging, old Flat/Deep untouched.
DONE: authorized Deep V2 run reaches pass1_pending; GET never makes a paid call.

### RTB-FIX-03 — Deep V2 14-field Strict Schema + Opus Pass 1
Статус: НАЧАТ. 03-A строгая V2/1 transport schema, B9/A8-aware Python validator и 03-B internal Pass1 provider invocation/persist validated output→review1 **реализованы по коду**. Нет user-facing authenticated route, Human Review1 редактора и полного Regression; CI QUEUED/NOT EXECUTED. Функциональная готовность FIX-03 ещё не подтверждена.
Задача: genuine 14-field hierarchical research taxonomy from verified A/B.
Работы: deep-v2 JSON Schema, strict validation for direction/branch/hypothesis, links, roles, provenance; distinct Approved Facts and A Video Observations prompt; model response parser and refusal/truncation; deterministic output and prompt/version/input audit; research_goal not guessed.
Файлы: NEW intelligence/taxonomy_builder/deep_contracts_v2.py, deep_prompts_v2.py if needed; update provider.py and deep_repository.py with isolated version dispatch.
QA: extra/missing keys, forged observation, nonexistent fact, improper competitor fact, invented KPI, malformed JSON, injection, max_tokens exhaustion; old deep-v1 output unchanged.
DONE: Pass 1 outputs validated hierarchy with usable search signals and validation focus.

### RTB-FIX-04 — Human Review 1, Versioned Mutations, Protected Tree
Статус: НЕ НАЧАТ.
Задача: human approve/reject/edit/merge/add on V2 tree with source-backed UI.
Работы: immutable review event, safe server-side edits, protected node ancestry, preservation and revalidation of all V2 fields, original A video locator and B9 quote display, project scope; explicit review1 -> pass2_pending.
Файлы: deep_repository.py, deep_contracts_v2.py, site/taxonomy-deep.js, site/taxonomy-workspace.css; tests/test_taxonomy_deep_v2_review1.py, tests/test_taxonomy_deep_v2_ui.cjs.
QA: add/edit/merge/reject, invalid new observation, parent cycle, foreign video, double review, refresh/stale frontend state, prompt injection in displayed text.
DONE: review1 immutable and the first approved hierarchy is protected from future model passes.

### RTB-FIX-05 — Opus Pass 2 + max 2 Explicit Expansions
Статус: НЕ НАЧАТ.
Задача: deepen only approved structure using SAME Research Input.
Работы: source-pinned Pass2 payload includes H1 decisions and coverage matrix; preserve approved IDs, names, roles, parent graph and rejected exclusions; action-specific incremental repairs; at most 2 separately consented expansions; bounded context and budgets.
Файлы: deep_repository.py, deep_prompts_v2.py, deep_contracts_v2.py, provider.py; tests/test_taxonomy_deep_v2_pass2.py.
QA: no moving protected node, no rejected reappearance, no source drift or prompt switch, expansion hard cap, billing ambiguity safety, deterministic pass order.
DONE: Pass2 produces a valid candidate with H1 lock, no hidden spend.

### RTB-FIX-06 — Quality/Search Readiness Gate + Human Review 2
Статус: НЕ НАЧАТ.
Задача: Python validates not just pretty taxonomy but a publishable, searchable one.
Работы: fact coverage and tree shape, novelty/non-duplicate tests, 14-field reference validation against exact A observation index, search_signals/validation_focus uniqueness and specificity, product claim role rules, error codes attached to node, structural and semantic boundaries, rerun gate on every review2 edit, immutable decisions.
Файлы: deep_contracts_v2.py, deep_repository.py, site/taxonomy-deep.js; tests/test_taxonomy_deep_v2_gate.py.
QA: missing/duplicate signals, nonexistent observation, wrong role, empty include/exclude, locked review mutation, no model dispatch during gating.
DONE: two human reviews persisted and no unresolved blocking errors before publication.

### RTB-FIX-07 — Atomic Immutable C Publication, Legacy Core, SRE Bridge
Статус: НЕ НАЧАТ.
Задача: publish once; canonical V2 14 fields and backward compatible V1 11 fields.
Работы: single PostgreSQL transaction: approved review2 + exact Research Input pins + quality report + V2 source tree SHA + V1 core SHA + SRE projection; stable B9 fact UUID mapping, reusing identical same-project SRE facts instead of duplicate INSERT; mismatch fails closed; repair concurrency and rollback; historical Flat/Deep unchanged.
Файлы: deep_repository.py, sre_bridge.py, research_contracts_v2.py, RTB-prefixed additive migrations, tests/test_taxonomy_deep_v2_publication_db.py.
QA: identical fact twice, conflict same UUID different content, same project concurrent publication, foreign project, injected mid-transaction crash/rollback, old publication read, exact graph and source lineage.
DONE: canonical RTB V2 publication is immutable and SRE V1-compatible projection persists atomically.

### RTB-FIX-08 — Versioned RTB Public Contract for Search Planner / SRE
Статус: НЕ НАЧАТ.
Задача: RTB provides stable read-only exports without owning another module's implementation.
Работы: RTB-owned publication loader / API for approved V1 core and V2 full tree; exact UUID, B9/A lineage, verified observation refs, fixed search_signals and validation_focus; version-negotiation, schema and hash; consumer contract fixtures. Existing Search Planner V1 must continue receiving 11 fields.
Файлы: NEW intelligence/taxonomy_builder/publication_export_v2.py, schemas/tests; minimal route registration where necessary; NO changes in intelligence/search_planner/* or intelligence/relevance/* in this branch.
QA: V1/V2 API contract, old snapshots, tenant isolation, illegal unpublished run, malformed 14-field source, hash drift and idempotent reader; mocks simulating Planner.
DONE: Search Planner developers can consume RTB V2 contract without reading internal RTB tables. Real Search Planner V2 ingestion remains the responsibility of its OWN feature branch.

### RTB-FIX-09 — Unified RTB UX & Flat/Deep Legacy Hardening
Статус: НЕ НАЧАТ.
Задача: fully usable single RTB module with a clear legacy and B9-based path.
Работы: independent project entry, 3 explicit input modes where applicable (questionnaire/approved context/B9), Pipeline Studio, quotation, payment authorization, Flat/Deep tree review and approved publication status; clear explanations of provider failure/cost/Review states; no cross-module UI overwrites; preserve old approved runs and render V2 originals.
Файлы: site/taxonomy-{studio,cost,run,deep}.js, site/taxonomy-workspace.css and minimal RTB route adapters; targeted Node/UI tests.
QA: clickthrough with mocked server; cancel, old/new review, multi-project UI, history, reload, unknown spend, blocked quality, safe XSS rendering, no GET-paid dispatch.
DONE: RTB complete end-user workflow, old Flat/Deep V1 unaffected.

### RTB-FIX-10 — Complete Offline/DB/UI Regression and T01–T36
Статус: НЕ НАЧАТ.
Задача: formal QA of independent RTB branch.
Работы: ALL 36 cases T01–T36 from ContentMachine_C_Corrector_FINAL_RU, adapted to actual B9/A8 schemas. Coverage across Research Input, Pass1, two reviews, Pass2/expansion, gates, publication, SRE bridge and mock Planner consumer. Run all pre-existing Flat/Deep/SRE/Planner contract tests without making non-RTB code changes. PostgreSQL 17 real migrations and rollback tests, Windows/Linux.
QA: full python unittest + Node UI tests + Postgres CI, mocked OpenRouter only. Report precise commands, pass/fail counts, SHA, test names, actionable residual findings.
DONE: no P0/P1 unresolved; no historical regression; all required stage and consumer contract tests green.

### RTB-FIX-11 — Standalone Module Release / Merge Qualification
Статус: НЕ НАЧАТ.
Задача: make RTB independently integrable into main on request.
Работы: diff against latest main; classify each changed path: RTB-owned code, RTB-prefixed migration, RTB UI, allowed minimal integration hook, tests/docs. Verify no edits to other modules. No hidden dependency on unmerged SRE/Collector/SP branches. Migration ordering, rollback strategy, smoke fixtures, readme, user operation and explicit costs/permissions. Rebase/merge from latest main only if required and only after backup and conflict audit. Freeze release SHA and turn Draft PR into Ready when CI passes.
QA: dry-run compare, staging database with clean main + RTB branch, clean bootstrap, old data upgrade, V1/V2 mode, full suite and executable smoke. Contract test doubles for optional downstream module consumers.
DONE: one PR contains entire RTB module changes, mergeable with main, documented semver-ish published contracts, CI green; DO NOT MERGE until user explicitly asks.

## 4. Stage gate / discipline for every step

For each RTB-FIX:
(A) inspect actual merged main + current RTB branch and relevant spec.
(B) make focused Python / SQL / JS changes; never change another module's private files.
(C) write unit + adversarial fixtures; PostgreSQL tests where DB touched; frontend tests where UI touched.
(D) run relevant test matrix; inspect CI logs and correct problems before closing.
(E) commit changes to feature/rtb-module-v2; record commit SHA, paths, test commands/results and remaining risks.
(F) update PR description and stage checklist with completed vs not-started vs blocked status.
(G) only then accept next stage request.

No stage is marked complete solely because code was pushed. GitHub Actions pending != passing. If a test runner is unavailable, record BLOCKED instead of claiming success.

## 5. Source/reference alignment and direct conflicts to avoid

- A8: Project Source Intelligence merged in PR #29; authoritative pinned context and video outputs in project_sources/ and B's PostgresAResolver.
- B9: Knowledge Extraction merged in PR #30; knowledge-snapshot/v1, knowledge-c-handoff/v1; existing knowledge_execution.py is SINGLE-PASS flat C, do not treat as completed Deep V2.
- Existing RTB: merged PR #7 foundation, #8 UX, #9 hierarchical two-pass; deep-v1 uses 11 node keys and _reviewed(conn, pid, run_id) INNER JOIN legacy facts.
- Search Planner V1: input_loader.py JOIN tb_fact_review_submissions; contract expects exactly 11 keys; enhancement to consume V2 belongs in Search Planner branch, not silently in RTB.
- SRE: sre_facts.id UUID, mirror_approved_taxonomy currently unconditionally INSERTs approved facts; RTB bridge must reuse stable facts and fail on mismatch, without editing relevance module internals.
- Migrations: A/B already added 030..053; legacy main has tb migrations including 004..014 and other unique names. Other feature branches use overlapping numerical prefixes 016/017. Inventory before choosing any next migration name.
- Do not invent JSON keys or add side effects to GET routes. Do not change existing niche/research_goal values via implicit fallback. Preserve exact JSON output and older URLs for V1.

## 6. Integration handoff expected by other component developers

When RTB is ready, its exported contract must identify:
- project_id, publication_id, RTB run ID and immutable publication version/hash;
- mode deep-v1/deep-v2, source B9 snapshot ID+SHA and A approved context ID+SHA if applicable;
- canonical 14-key tree only for deep-v2; approved_v1_core 11-key projection;
- exact lineage mapping B9 SHA -> RTB/SRE UUID, human review metadata and gate report;
- optional genuine A observation index locator with original evidence trust/locator; no invented description;
- search_signals and validation_focus for V2 hypothesis nodes, separate from generated queries and verified facts;
- read-only, project scoped stable API and fixture version so Search Planner can adopt in its branch.

The RTB PR must be safe to merge even if Search Planner V2 work is not yet merged: V1 remains operational and V2 export remains optional until a consumer opts into it.

## 7. Ordered road map

FIX-00 implementation committed: verified B9 signatures, A evidence, owner map, Linux/Windows tests. FINAL QA pending actual GitHub Actions success.
FIX-01 implementation committed: read-only live B9/A8 Research Input, deterministic SHA, offline and PostgreSQL17 tests, no model calls. FINAL QA pending actual GitHub Actions success.
Do not begin FIX-02 paid-run ledger work until FIX-00/FIX-01 tests are green or explicit blockers are recorded.
Then FIX-02 -> 03 -> 04 -> 05 -> 06 -> 07 -> 08 -> 09 -> 10 -> 11.
Total 12 logically distinct engineering checkpoints; DO NOT artificially split or compress.
A user may request precisely one FIX at a time. Always finish/repair the prior FIX before accepting completion of the next.
The product is complete only when RTB-FIX-11 passes; until then the PR remains DRAFT and main remains untouched.
