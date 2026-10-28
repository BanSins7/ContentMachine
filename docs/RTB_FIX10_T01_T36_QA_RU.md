# RTB-FIX-10 — QA матрица T01–T36 и реестр доказательств

## Обновление 2026-10-27: FIX-10 — КОД ЗАВЕРШЁН, QA перенесена на ПК

По прямому решению владельца отказались от **ожидания CI** для закрытия инженерной реализации FIX-10. Все RTB-owned требования T01–T27/T30–T36 имеют привязанные исходные тесты, T08/T23 реализованы по коду; T28/T29 остаются внешней задачей Search Planner. Настроен локальный handoff: `python -m intelligence.taxonomy_builder.local_release_handoff` и опциональный `--run-basic`. Подробнее: [локальный FIX-10/11 handoff](RTB_FIX10_FIX11_LOCAL_HANDOFF_RU.md).

**Финальный статус: FIX-10 CODE [x], QA: DEFERRED_TO_OWNER_PC / NOT RUN.** Ни один PG17/Linux/Windows/Node test на текущем HEAD не назван прошедшим. Разделы ниже документируют исходную строгую QA-процедуру для дальнейшего запуска на ПК. `qa_matrix --strict` остаётся fail-closed, поэтому **не применять его как обязательное условие закрытия кода**. Release/merge остаётся отдельным решением после локальной проверки.


**Ветка:** `feature/rtb-module-v2`, Draft PR #32. **Исходная спецификация:** `ContentMachine_C_Corrector_FINAL_RU(2).txt`, раздел 13, T01–T36. **Статус:** реализация FIX-10-A (реестр) и FIX-10-B (расширение CI) выполнена **по коду**; фактическое выполнение Linux/Windows/PostgreSQL17 **НЕ ПОДТВЕРЖДЕНО**. Никакие платные OpenRouter вызовы не запускались этим этапом.

## Принцип аудита

Термин **«Код теста найден» не означает PASS**. Это означает, что у требования есть конкретный test method и он существует в текущем GitHub source tree; автоматический CI в отдельной job обязан реально выполнить его и выдать exit status. Для частичных или внешних случаев `--strict` **намеренно возвращает exit code 2** даже если все существующие тесты когда-либо прошли. Отдельный релизный DoD требует **фактическое прохождение всех suites**, clean Postgres migrations, старые Flat/Deep regression, no P0/P1, и осмысленную приёмку T28/T29 с владельцем Search Planner.

**Текущий source-coverage accounting:** 34/36 mapped тестов, 0 частичных, 2 внешних для отдельно развиваемого Search Planner (T28, T29). Была проведена проверка GitHub source против ВСЕХ 36 test-method names; отсутствующих тестов или файлов **0**, но это только проверка существования исходников.

## 36 требований и проверяемые доказательства

| Кейс | Ожидание из спецификации | Статус реализации теста | Конкретный тест |
|---|---|---|---|
| T01 | B9 approved plus genuine A8 frozen input | Код теста найден | `test_taxonomy_research_input.py::test_two_real_b9_facts_and_original_a_observations` |
| T02 | Empty A video permitted | Код теста найден | `test_taxonomy_research_input.py::test_zero_video_is_valid_and_stable` |
| T03 | Foreign video context fail-closed | Код теста найден | `test_taxonomy_research_input.py::test_rejected_unapproved_and_foreign_context` |
| T04 | B9/A hash drift blocks | Код теста найден | `test_taxonomy_research_input.py::test_handoff_digest_mismatch_is_blocking` |
| T05 | Wrong source schema version | Код теста найден | `test_taxonomy_research_input.py::test_wrong_b9_handoff_and_a_video_schema_versions_fail_closed` |
| T06 | Prompt-injection isolation | Код теста найден | `test_taxonomy_deep_v2_quote.py::test_malicious_source_instructions_remain_untrusted_user_data` |
| T07 | Read-only previews never pay | Код теста найден | `test_taxonomy_research_input.py::test_preview_idempotent_across_two_calls` |
| T08 | Frozen data survives source updates | Код теста найден | `test_taxonomy_deep_v2_binding_db.py::test_real_b9_storage_mutation_refused_after_binding_pin` |
| T09 | Pass1 separates B9 facts and A8 observations | Код теста найден | `test_taxonomy_deep_v2_quote.py::test_actual_video_evidence_passes_unchanged_as_untrusted_data` |
| T10 | Unknown fact/observation IDs rejected | Код теста найден | `test_taxonomy_deep_v2_validation.py::test_legacy_graph_rules_reject_bad_parent_and_fake_facts` |
| T11 | Competitor claim cannot authorize direct own product | Код теста найден | `test_taxonomy_deep_v2_validation.py::test_direct_claim_must_come_from_approved_own_product_fact` |
| T12 | Hypothesis valid with no observations | Код теста найден | `test_taxonomy_deep_v2_validation.py::test_valid_search_hypotheses_without_video_observation_references` |
| T13 | A8 observations do not prove performance | Код теста найден | `test_taxonomy_deep_v2_sre_projection.py::test_video_observation_is_only_search_hint_never_fact_or_verified_video` |
| T14 | Pass2 retains exact frozen facts and videos | Код теста найден | `test_taxonomy_deep_v2_followup.py::test_no_source_or_h1_mutation` |
| T15 | H1 approved nodes immutable in subsequent passes | Код теста найден | `test_taxonomy_deep_v2_followup.py::test_removed_or_changed_approved_node_is_rejected` |
| T16 | Two separately consented expansions maximum | Код теста найден | `test_taxonomy_deep_v2_expansion_db.py::test_two_paid_expansions_and_never_a_third` |
| T17 | Truncated provider result uncertain without retry | Код теста найден | `test_taxonomy_deep_v2_provider.py::test_malformed_paid_completion_retains_known_charge` |
| T18 | Legacy Deep V1 11 and V2 strict 14 | Код теста найден | `test_taxonomy_deep_contracts.py::test_valid_tree_and_coverage_matrix` |
| T19 | Hypothesis needs signals and focus | Код теста найден | `test_taxonomy_deep_v2_validation.py::test_hypotheses_must_have_unique_concrete_signals` |
| T20 | Nonhypothesis extensions empty | Код теста найден | `test_taxonomy_deep_v2_validation.py::test_additional_missing_or_invalid_extensions_rejected` |
| T21 | Duplicate or empty signals and observations blocked | Код теста найден | `test_taxonomy_deep_v2_validation.py::test_hypotheses_must_have_unique_concrete_signals` |
| T22 | Coverage and semantic duplicates gate | Код теста найден | `test_taxonomy_deep_v2_readiness.py::test_coverage_gap_blocks_a_publishable_tree` |
| T23 | Review UI add/edit/merge/reject provenance | Код теста найден | `test_taxonomy_deep_v2_review.py::test_compound_positive_add_edit_merge_reject_preserves_b9_a8` |
| T24 | Review2 readiness-invalidating edit blocks publication | Код теста найден | `test_taxonomy_deep_v2_review2_db.py::test_bad_search_signal_edit_is_blocked_and_rolls_back` |
| T25 | Published V2 retains all 14 fields and SHA | Код теста найден | `test_taxonomy_deep_v2_publication_db.py::test_publish_once_has_exact_14_and_11_field_immutable_views` |
| T26 | Exact V2 to V1 11-field core | Код теста найден | `test_taxonomy_deep_v2_sre_projection.py::test_full_tree_and_exact_11_field_core_without_mutation` |
| T27 | Legacy Search Planner accepts V2-compatible core | Код теста найден | `test_taxonomy_publication_export_v2.py::test_existing_search_planner_v1_contract_accepts_adapter` |
| T28 | SP V2 consumes observation signals and focus | Внешняя интеграция | `test_taxonomy_publication_export_v2.py::test_v2_export_keeps_14_keys_and_original_a_references` |
| T29 | Query generator uses V2 signals with query validation | Внешняя интеграция | `test_search_planner_query_validation.py::test_selection_deterministic_complete_and_non_mutating` |
| T30 | Approved SRE taxonomy and rules remain compatible | Код теста найден | `test_taxonomy_deep_v2_publication_db.py::test_sre_fact_ids_match_pinned_b9_mapping_and_video_is_not_fact` |
| T31 | Same B9 fact ID reusable across publications | Код теста найден | `test_taxonomy_deep_v2_publication_db.py::test_matching_preexisting_fact_is_reused_not_inserted_twice` |
| T32 | Different-content UUID/foreign reuse rejected | Код теста найден | `test_taxonomy_deep_v2_publication_db.py::test_conflicting_existing_fact_uuid_aborts_publication_and_sre_version` |
| T33 | Atomic rollback on publication failure | Код теста найден | `test_taxonomy_deep_v2_publication_db.py::test_failure_after_full_sre_write_rolls_back_everything` |
| T34 | Legacy Flat and Deep V1 unchanged | Код теста найден | `test_taxonomy_deep_db.py::test_complete_two_human_reviews_saturation_and_atomic_sre_tree` |
| T35 | Provider calls in CI mocked with usage and cost | Код теста найден | `test_taxonomy_deep_v2_provider.py::test_exact_pinned_payload_and_known_usage` |
| T36 | Every paid stage requires consent and budget | Код теста найден | `test_taxonomy_deep_v2_lifecycle.py::test_user_interaction_required_for_every_paid_pass` |

## Необходимые доработки, без которых FIX-10 нельзя закрывать

- **T08 (код реализован, PG17 QA ожидает):** новый SQL-тест изменяет `knowledge_approved_snapshots.snapshot` и `knowledge_c_handoffs.handoff` после создания RTB pin. B9 append-only triggers обязаны отклонить обе попытки; прежний `get()` и SHA обязаны остаться прежними. Программная имитация replay drift дополнительно покрыта существующим тестом `test_verified_replay_drift_does_not_overwrite_original`. Создание нового B9 snapshot при законном изменении источника — отдельная upstream B9 операция, не мутация старого snapshot.
- **T23 (код реализован, PG17 QA ожидает):** pure reducer теперь проверяет одну валидную комбинацию `approve/edit/merge/reject/add` и переподчинение нового узла, исходные B9/A8 неизменны. Положительный browserless UI-тест отправляет все пять действий одним HTTP Review1, ранее проверенный V8 mock прошёл без оплаты. Отдельный PG17 mocked-provider тест сохраняет mixed `add/edit/reject` H1 с неизменным бюджетом. Полный physical-browser + DB end-to-end остаётся общим релизным критерием.
- **T28:** сам RTB экспортирует 14 полей и оригинальные A8 refs, но **Search Planner V2 ещё не подключён к новому exporter**. Работа только в ветке SP. Не менять чужой модуль из RTB.
- **T29:** существующая Query Validation и legacy Search Planner остаются работоспособными, но **потребление `search_signals` внутри нового Query Generation V2** — незавершённая задача SP. Тесты его собственных контрактов не являются свидетельством end-to-end RTB→SP V2.
- Полные реальный Node/browser acceptance, clean PostgreSQL17 DB migrations 054–059, DB concurrency/fault injection, бюджетные/signed consent trace и genuine runner logs остаются релизными блокерами до завершения FIX-10-C/11.

## Реализованный исполняемый инструмент

`intelligence/taxonomy_builder/qa_matrix.py` использует только стандартную библиотеку. В нём содержатся ровно 36 записей с `id,title,status,refs,reason`. Проверка читает Python AST, убеждается, что `T01...T36` непрерывны, нет дублирования, каждый путь действительно существует и заявленная unittest-функция определена в class. **Ни одна функция провайдера при `--check` не вызывается.**

```bash
# Чистый offline-аудит таблицы, НЕ выполнение 36 тестов:
python -m intelligence.taxonomy_builder.qa_matrix --check --json-out reports/rtb_t01_t36.json
python -m unittest discover -s tests -p 'test_taxonomy_v2_qa_matrix.py' -v

# Только curated offline-модули Python + Node; нет live E2E/реального провайдера:
python -m intelligence.taxonomy_builder.qa_matrix --run-offline --json-out reports/rtb_offline.json

# Только в чистом PostgreSQL17 тестовом окружении после всех migrations:
python -m intelligence.migrate
python -m intelligence.taxonomy_builder.qa_matrix --run-postgres --json-out reports/rtb_db.json

# Строгая релизная проверка: ожидается код 2, пока T28/T29 не интегрированы в SP и CI не доказал PASS
# и нет подтверждённого полного выполнения:
python -m intelligence.taxonomy_builder.qa_matrix --strict
```

`SAFE_OFFLINE`, `SAFE_NODE`, `SAFE_PG` — явные allowlist тестов с mocked provider. Тесты с названиями `live`, `edema` или реальные OpenRouter E2E **никогда не подхватываются**, даже при наличии настоящего API token на CI. Отчёт не содержит секретов и не делает ложное заключение, что tests passed.

## CI-диагностика и legacy regression

В `.github/workflows/rtb-module-contracts.yml`:
- Linux: `test_taxonomy_v2_qa_matrix.py` + `qa_matrix --check`, 5 отдельных Search Planner V1 Python suites, четыре legacy Flat/Deep UI cjs, artifact `reports/rtb_t01_t36.json`.
- Windows: тот же реестр AST, 3 Search Planner contract/query suites, четыре legacy Flat/Deep UI cjs.
- PostgreSQL17: после применения всех migrations дополнительные legacy `test_taxonomy_builder_run_db.py` и `test_taxonomy_builder_pipeline_db.py`; существующие B9/Deep V2/atomic SRE/HTTP E2E остаются.
- Все референсы на `test_*.py` проверены непосредственно по загруженным Github-файлам и найдены. Нет свидетельства, что сам Python/PG/Node suite прошёл в заблокированном runner.

**Контроль release:** `main` не менять, Draft PR #32 не переводить в Ready, пока не устранены фактические QA-блокеры. После появления runner записать реальные job IDs, `conclusion`, счётчики прошедших/упавших/пропущенных тестов и исправленные SHA в этом документе и `RTB_IMPLEMENTATION_PROGRESS.md`.

## Закрытие FIX-10-C (реализация) — 2026-10-26

- T08: `tests/test_taxonomy_deep_v2_binding_db.py::test_real_b9_storage_mutation_refused_after_binding_pin`. Тест делает реальный `UPDATE` B9 immutable snapshot и handoff после pin, ожидает исключение immutable-trigger, затем подтверждает прежние `get()`, hash, один binding и idempotent pin. **PG17 запуск ещё не подтверждён**.
- T23: `tests/test_taxonomy_deep_v2_review.py::test_compound_positive_add_edit_merge_reject_preserves_b9_a8`; `tests/test_taxonomy_deep_v2_review_db.py::test_positive_add_edit_reject_persisted_review1_no_new_payment`; `tests/test_taxonomy_deep_v2_ui.cjs` содержит отдельную full-positive Review1 фикстуру с `approve/edit/merge/reject/add` и zero paid extra. **Browserless V8 mock фактически PASS**; Python/PG17 запуск на Github runner не подтверждён.
- `qa_matrix.py` и `test_taxonomy_v2_qa_matrix.py` обновлены на **34 mapped / 0 partial / 2 external SP T28/T29**. Метаданные 34/36 — не 34 passed runtime cases. `--strict` преднамеренно FAIL до полного SP consumer integration и подтверждённого реального CI.
