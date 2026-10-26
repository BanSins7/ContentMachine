# RTB — единый журнал реализации (FIX-00 → FIX-11)

> **Точка входа для нового чата.** Перед любыми действиями прочитай этот файл, затем [полный технический план](RTB_FULL_MODULE_IMPLEMENTATION_PLAN_RU.md), [аудит FIX-00](RTB_FIX00_AUDIT_RU.md) и [контракт FIX-01](RTB_FIX01_RESEARCH_INPUT_RU.md). После каждого коммита сверяй реальную ветку/CI и **обновляй этот файл в том же PR**.

## Где работать

- Репозиторий: \`BanSins7/ContentMachine\`.
- **Единственная активная ветка RTB:** \`feature/rtb-module-v2\`.
- **Единственный активный PR RTB:** [#32](https://github.com/BanSins7/ContentMachine/pull/32), Draft.
- База: \`main@218f5f3163ccc7179a4809ba4386a1d1b6ff80f5\` — уже содержит A (#29), B (#30), Flat RTB и Deep V1.
- **Нельзя** менять \`main\`, выполнять merge PR #32 или совершать платные обращения к LLM без отдельного поручения.
- Код RTB V1 **уже есть в этой Git-ветке**; дублировать его в отдельной папке не нужно.
- Изменять преимущественно \`intelligence/taxonomy_builder/**\`, \`tests/test_taxonomy*.py\`, \`site/taxonomy-*.js\`, RTB SQL migrations, \`docs/RTB_*.md\`, собственный CI. Внутренности Search Planner/SRE/A8/B9/Collector — собственность их feature-веток.

## Как зачёркивать выполненные этапы

После окончания этапа и **фактического успешного CI** меняй его строку из \`- [ ] **RTB-FIX-NN ...**\` в \`- [x] ~~RTB-FIX-NN ...~~ **ГОТОВО** (...commit, тесты...)\`. Это одновременно даёт зачёркивание и понятный статус. Не зачёркивать этап, когда реализован только код, CI стоит в очереди или есть неисправленные регрессии. Состояние \`КОД ЕСТЬ / QA ОЖИДАЕТ\` не равно \`ГОТОВО\`.

Если CI заблокирован извне, явно фиксировать BLOCKED и причины; не подменять реальное тестирование утверждением о прохождении.

## Все этапы

- [ ] **RTB-FIX-00 — Module Isolation + аудит Flat/Deep V1 + реальные A8/B9 контракты.** Код/аудит/фикстуры сделаны, **QA ожидает GitHub Actions**. Закрыть после Linux/Windows legacy+new contract CI, корректности fixtures, отсутствия чужих файлов. Документ: [RTB_FIX00_AUDIT_RU.md](RTB_FIX00_AUDIT_RU.md).
- [ ] **RTB-FIX-01 — Frozen Research Input B9+C1 и оригинальные A8+C2 observations.** \`research_input.py\`, SHA, read-only reader, offline tests, PostgreSQL17 integration test, CI jobs добавлены. **QA ожидает GitHub Actions**. Документ: [RTB_FIX01_RESEARCH_INPUT_RU.md](RTB_FIX01_RESEARCH_INPUT_RU.md).
- [ ] **RTB-FIX-02 — Deep V2 C-run + immutable Research Input pin + model/price quote, separate consent, budget, idempotent one-paid-stage execution.** Нельзя переиспользовать B Stage14 single-pass Flat C-run как готовый Deep. Начинать с контракта состояния, миграции, планировщика этапов и отрицательных тестов; не слать реальных запросов.
- [ ] **RTB-FIX-03 — Строгая 14-польная Deep V2 JSON Schema, SHA/UUID/Observation validation, Claude Opus 5.5 Pass 1.** V1 строго 11 полей, его контракты неприкосновенны.
- [ ] **RTB-FIX-04 — Human Review №1.** Утверждение/отклонение/редактирование/добавление/слияние, immutable decisions, UI, protected H1 graph.
- [ ] **RTB-FIX-05 — Opus Pass 2 и максимум два явных Expansion.** Входные A8/B9 снапшоты неизменны, H1 нельзя переписать, ограничения затрат и retries.
- [ ] **RTB-FIX-06 — Deterministic Quality Gate, Search Readiness Gate, Human Review №2.** Ссылки только на утверждённые B9 факты и pinned A observations, без приписывания наблюдениям статуса product truth.
- [ ] **RTB-FIX-07 — Atomic immutable Deep V2 Publication + 11-field V1 projection + SRE bridge.** Переиспользовать одинаковые SHA→UUID facts, reject divergent identity, не изменять history.
- [ ] **RTB-FIX-08 — RTB-owned read-only V1/V2 publication export для Search Planner и SRE.** Не вносить незапрошенные изменения в файлы \`intelligence/search_planner/**\` и \`intelligence/relevance/**\`.
- [ ] **RTB-FIX-09 — Unified Taxonomy UX и legacy hardening.** Flat/Deep V1, B9 Deep V2, user-selected model/consent/reviews, не перезаписывать общие UI других модулей.
- [ ] **RTB-FIX-10 — T01–T36 E2E, PostgreSQL17, Linux/Windows, Node UI и полная регрессия.** Только mocked providers в автоматическом QA.
- [ ] **RTB-FIX-11 — Release qualification автономного RTB PR.** Проверить latest main diff, additive SQL migrations, чистое развёртывание, historical upgrade, все API-контракты, CI и готовность к однократному интеграционному merge по запросу.

## Журнал изменений / проверок

| Этап | Что реализовано | Коммиты / текущее состояние |
| --- | --- | --- |
| FIX-00 | \`research_contracts_v2.py\`: проверка подлинности B9 approval SHA; стабильный project-scoped fact UUID5, original A evidence index, 14→11 core. Реальная 2-fact B9 fixture, отдельный Linux/Windows workflow, аудит ownership/миграций. | Начальная точка \`55ed13123fbd4999f70bc8d0780dadbaec2d04bf\`. На тот момент CI queued. |
| FIX-01 | \`research_input.py\`: \`RTBResearchInputService.preview(project_id,snapshot_id,reviewer_id)\` через \`KnowledgeSnapshotRepository.export_by_id\` и \`materialize_c_handoff\` + \`PostgresAResolver.resolve_context/resolve_video\`. Exact ID/SHA pins, zero videos, 400 facts/24 videos/2400 observations/1.2 MB fail-closed; no spend/LLM. \`tests/test_taxonomy_research_input.py\` + \`tests/test_taxonomy_research_input_db.py\` + CI. | Последний документированный SHA до этого журнала \`53cac1171c5eb68df1786b5419ad0f4431c70b06\`. На нём CI queued; **не считать завершённым**. |
| FIX-02 | Ещё не закрыт. | — |

## Команды для проверки

\`\`\`bash
python -m unittest discover -s tests -p 'test_taxonomy_c_corrector_contracts.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_research_input.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_contracts.py' -v
# c CM_TEST_PG_DSN и развернутой PostgreSQL 17:
python -m intelligence.migrate
python -m unittest discover -s tests -p 'test_taxonomy_research_input_db.py' -v
python -m unittest discover -s tests -p 'test_taxonomy_deep_db.py' -v
# полный CI:
python -m unittest discover -s tests -v
node tests/test_taxonomy_deep_ui.cjs
node tests/test_taxonomy_run_ui.cjs
\`\`\`

## Не забывать при продолжении

1. **Сначала** проверь свежий HEAD ветки и заключения GitHub Actions на HEAD, а не только комментарии предыдущего чата.
2. Наши вызовы GitHub Actions на предыдущем HEAD показывали \`queued\`; **не утверждать, что тесты прошли**, пока нет \`conclusion=success\`.
3. У B9 \`knowledge-snapshot/v1\`, \`facts[].fact_id\` — SHA, а не UUID. У A8 \`video-intelligence/v1\` evidence — словарь, не \`observations[]\`. B Stage14 \`knowledge_c_generation_runs\` — Flat C, а не Deep V2.
4. Провайдер не вызывается на FIX-00/FIX-01; вся тарификация и согласие — отдельно и явно в FIX-02. Никогда не разрешать paid calls только на основании наличия \`research_input_sha256\`.
5. Отдельные ветки SRE, Search Planner, Video Collector, Analytics развиваются параллельно; RTB экспортирует контракты, но не изменяет их внутренности.
6. Новый чат, закрывая каждый этап, обязан добавить в этот журнал дату, исходный commit, список файлов, команды тестирования, **реальные** выводы CI, проблемы и SHA закрытия; только затем зачеркнуть его в общем списке.
