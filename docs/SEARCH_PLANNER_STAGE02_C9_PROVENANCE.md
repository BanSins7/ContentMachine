# SP-02 — C9 V1 trusted publication and context provenance / Deep V2 gate

**Ветка:** `feature/search-planner-full-integration`. **Дата:** 26.10.2026. 
**Результат:** реализовано усиление C9 V1; Deep C9 V2 **не подключён**, пока соответствующий C Corrector PR не предоставит реальную утверждённую versioned публикацию.

## Что именно изменено

| Файл | Ответственность |
| --- | --- |
| `intelligence/search_planner/publication_versions.py` | Отдельный `require_deep_v1_tree()`: 11 исходных полей по каждому узлу, запрет дополнительных полей; если встречены `source_observation_ids`, `search_signals`, `validation_focus` — явная ошибка `409`, вместо потери данных в legacy `validate_tree()`. `taxonomy_version` не используется для угадывания версии Deep-дерева: это порядковая версия публикации. |
| `intelligence/search_planner/contracts.py` | Выполнить shape-check **до** нормализации; отклонить опубликованные raw узлы, которые были бы изменены legacy-нормализацией (пробелы/подменённые поля); сохранить точный V1 contract. |
| `intelligence/search_planner/input_loader.py` | Добавить `EXISTS` по настоящей последней Human Review #2: `approved_nodes=d.tree` в одной паре `(project_id,run_id)`; `EXISTS` для Review #1. Это дополняет действующую Python-проверку `human_reviews == [1,2]`; публикация из B9 flat C не подставляется в C9 Deep. |
| `intelligence/search_planner/context_builder.py` | `require_published_context_batch()`: полная сверка каждого контекста с заново вычисленным чистым V1 контекстом из утверждённого snapshot. |
| `intelligence/search_planner/service.py` | Применить глубокую проверку реальных `HypothesisContext/v1` перед возвратом данных public facade. |
| `tests/test_search_planner_stage02_compatibility.py` | offline regression V1/V2, fake/foreign publication, lossy normalization, review2 SQL verification, context siblings/exclusions/approved facts/provenance. |
| `.github/workflows/sp-stage01-fast.yml` | Регрессионная матрица SP-01 + SP-02 с импортом реального пакета и контрактами V1. |

### Контрактное правило

Узел V1 — всегда **ровно** `id, parent_id, kind, relation, source_fact_ids, name, rationale, definition, include, exclude, examples`. Включение новых полей из экспериментального Deep V2 нельзя считать утверждённой V1 публикацией. Поле `taxonomy_version=2` или `999` — счётчик версий таксономии, а НЕ схема Deep V2.

`read_published_snapshot()` требует в PostgreSQL одновременного выполнения:
- того же `project_id`, `run_id`, `publication_id`;
- статуса `approved` у `tb_runs` и `tb_deep_runs` (Python guard);
- существования обоих Human Reviews, а также **буквального JSONB-равенства** утверждённых узлов второго Human Review опубликованному дереву;
- полноты фактов и гипотез (существующая проверка `validate_published_snapshot`).

Любая модификация `neighbor_hypotheses`, `include/exclude`, `approved_facts`, `search_policy`, `derived_search_facets` в сгенерированном batch приведёт к отказу, даже если метаданные содержат правильный `taxonomy_sha256`.

## Следующая интеграция C9 Deep V2 (ожидает внешний C-branch)

В `feature/rtb-c-corrector-v2` PR #31 на момент проверки присутствовали только контракты `research_contracts_v2.py` и первый freeze. Базовые B9 `knowledge-snapshot/v1`, `knowledge-c-handoff/v1` и A `video-intelligence/v1` НЕ эквивалентны утверждённому Deep V2. Нельзя угадывать способы доступа к V2 или добавлять фактическую реализацию до появления точного published schema + double Human Review + SHA provenance.

**Ожидаемые признаки утверждённого upstream C9 V2:** Deep 14 полей (V1 + 3), реально версионированная immutable публикация и режим чтения, оригинальные A observation refs с output_id, факты с B9 SHA↔SRE UUID mapping, два Human Reviews. Тогда сделать отдельный V2 reader/context schema, а не конвертировать V2 в V1 молча. Генератор SP3–SP5 должен поддержать V2 только после обновления своих schema pins, prompts и миграционных контрактов.

## Как проверить

```bash
python -m unittest discover -s tests -p 'test_search_planner_stage02_compatibility.py' -v
python -m unittest discover -s tests -p 'test_search_planner_foundation.py' -v
python -m unittest discover -s tests -p 'test_search_planner_service_stage01.py' -v
# With disposable PostgreSQL 17 and installed migrations:
python -m unittest discover -s tests -p 'test_taxonomy_deep_db.py' -v
```

### Правдивый QA статус

Новая быстрая матрица GitHub Actions запущена после создания draft PR #34. Её итог на HEAD нужно проверить отдельно перед зачёркиванием этапа. `main` и ветки Video Collector/C Corrector не изменялись. Отдельного live OpenRouter, DTK, video-provider и модельного теста здесь нет.

**Этап закрывать только после:** QA точного HEAD + наличия и интеграции утверждённого C9 Deep V2. До этого V1 hardening может быть готов, но SP-02 в master progress остаётся частичным.
