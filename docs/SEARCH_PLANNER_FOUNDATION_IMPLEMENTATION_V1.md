# Search Planner Foundation v1 — RTB Integration & HypothesisContext Builder

**23.10.2026 · Implementation PR (stacked above documentation PR #14).**
**Baseline:** `main` @ `c5d5eb515ff85e4df87ff2011ab571eaf5e23a59`.
**Status:** code in **feature/search-planner-foundation-v1**, not yet in `main`; no live paid execution. Uses schemas introduced in docs PR #14. No extra database migrations.

## Что реально реализовано

1. `intelligence/search_planner/input_loader.py::load_approved_publication(project_id,publication_id)` выполняет **read-only repeatable-read PostgreSQL** lookup, связывает `tb_deep_publications`, `tb_taxonomy_publications`, `tb_deep_runs`, `tb_runs`, `tb_deep_reviews`, `tb_fact_review_submissions` **по одному project/run**; обнаруживает flat/draft/чужие/неполные публикации, без эффекта чтения текущих незавершённых ревизий.
2. `contracts.py::validate_published_snapshot` повторно проверяет человеческие Review #1/#2, оба approved status, допустимую структуру иерархии через уже действующий `deep_contracts.validate_tree`, целостность фактов и наличие минимум одной гипотезы у каждой корневой темы.
3. `context_builder.py::build_hypothesis_contexts(snapshot,search_policy)` делает **один** полный `hypothesis-context/v1` на каждую утверждённую гипотезу: 11 полей гипотезы, всей темы, необязательной подтемы, все approved facts из ancestry, raw профиль по allowlist, соседние гипотезы (до 5), явный языковой policy, immutable source hash.
4. Stable order, deep copies, no truncation, no invented project context. `branch=null` для `direction→hypothesis`. Нет автоподстановки языка из IP/геопозиции.
5. Проверки: `tests/test_search_planner_foundation.py` (offline mocks и проверка JSON Schema при наличии установленной `jsonschema`); обновлён `tests/test_taxonomy_deep_db.py` с **дополнительной real PostgreSQL integration** проверкой после двух Human Reviews и fake-provider publication. Последний запускается **только** с `CM_TEST_PG_DSN` и пропускается без него.

### Публичный интерфейс Python

```python
from intelligence.search_planner import (
    load_approved_publication, build_hypothesis_contexts,
)

policy = {
    "languages": ["en", "ru"],  # обязательные, утверждённые настройки проекта
    "markets": [],
    "allowed_modes": ["keyword", "hashtag"],
    "target_candidates_per_hypothesis": 10,
    "max_candidates_per_hypothesis": 16,
    "max_total_candidates": 480,
}
snapshot = load_approved_publication(project_id, taxonomy_publication_id)
contexts = build_hypothesis_contexts(snapshot, policy)
# contexts: list[HypothesisContext/v1], 0 LLM calls, 0 provider searches
```

**Важно:** `taxonomy_publication_id` — UUID из `tb_taxonomy_publications.id`, привязанного к `tb_deep_publications.builder_publication_id`, **не** `run_id` и не SRE taxonomy version. `project_id` — именно 32-символьный hex ID ContentMachine. Ошибка, если нет full approved deep publication. Устаревшие flat taxonomy в этом v1 **не превращаются в гипотезы автоматически**.

### Почему так

- Нельзя считать контекстом только строку `node.name`; всё `definition/include/exclude/rationale/examples` необходимо AI Query Generator.
- Approved facts из ancestor path расширяют контекст **без добавления новых фактов**. `source_fact_ids` узлов остаются неизменными, собранный список facts является union по пути.
- JSON Schema: [HypothesisContext v1](SEARCH_PLANNER_HYPOTHESIS_CONTEXT_V1.schema.json). Сам backend проверяет ссылочные и проектные инварианты, которые schema не может выразить.

### Тесты и проверка

```bash
python -m unittest discover -s tests -p 'test_search_planner_foundation.py' -v
# Только на отдельной одноразовой PostgreSQL БД:
CM_TEST_PG_DSN=postgresql://... python -m unittest discover -s tests -p 'test_taxonomy_deep_db.py' -v
```

**Не запускайте integration fixture на рабочей базе!** Existing tests create/apply schema and insert Project/Taxonomy fixtures. Переменная `CM_TEST_PG_DSN` должна указывать на отдельную disposable DB. Внешних вызовов OpenRouter эти тесты не делают.

### Чего пока нет

- LLM Query Generator, ThinkQE/MuGI/Jina execution, MMR, Query Validation и Tool Registry.
- UI кнопки Search Planner, background worker или Search Planner REST API.
- Spend authorization, persistence of generated query attempts, output of `query_candidates/v1`.
- Сбор и анализ видео. DTK Tool PR #13 — отдельная самостоятельная интеграция.

### Следующий этап

Второй блок [**Generation Engine + PostgreSQL**](SEARCH_PLANNER_GENERATION_ENGINE_V1_FULL_SPEC.md): отдельная таблица генераций и журнал платных запросов, консервативные резервы бюджета, pin модели/промпта, статусы, exactly-once dispatch semantics и fail-closed reconciliation. Только после этого включать OpenRouter generation; не наследовать spend-consent из RTB.

### Проверяемость

Источник: исходники RTB `deep_contracts.py`, `deep_repository.py`, миграции `007_tb_runs_and_reviews.sql`, `014_tb_deep_hierarchical_taxonomy.sql`; гарантии из unit tests и PostgreSQL test (при наличии disposable DB). **Без live model/search доказательств.**
