# Content Machine · Query Validation v1 — реализация, контракт, QA

**Дата:** 24.10.2026. **Статус:** новый отдельный PR, работающая реализация в Python + PostgreSQL после CI. Эта стадия находится ПОСЛЕ Stage D AI Query Generator и ПЕРЕД Tool Registry / Task Planner.

## Архитектура и границы

```text
RTB approved + Generation Engine A/B/C/D
  -> sqg_candidates and complete sqg_hypothesis_coverage
  -> sqg_outbox(query_candidates_ready/v1)
  -> Query Validation v1:
     1. Authenticated project-scoped read of completed source event
     2. Re-verify server candidate lineage, RTB contexts, source SHA and policy
     3. Embed original queries and grounded positive/negative references
     4. Relevance and include/exclude/neighbor scope gates
     5. Stable exact/semantic dedup by locale + query mode
     6. MMR ranking per hypothesis + angle diversity and explicit quotas
     7. Save one decision for EVERY candidate + full hypothesis coverage
     8. In one PostgreSQL transaction: validated run + decisions + coverage
        + downstream qv_outbox + ACK upstream sqg_outbox
  -> validated_queries/v1 -> future Tool Registry / Task Planner
```

**Полный источник истины RTB не заменяется LLM.** Контекст является тем же immutable HypothesisContext с полями direction / branch / hypothesis / 11 node fields, approved facts, соседними гипотезами и search policy. Ни один семантический порог не означает доказанную релевантность реальных видео.

## Реализованные файлы

- `intelligence/migrations/016_query_validation.sql`: 4 таблицы `qv_runs`, `qv_decisions`, `qv_coverage`, `qv_outbox`. Tenant-scoped FK на source generation, composite candidate-generation FK, immutable QA decisions/coverage, уникальный source_outbox_id.
- `query_validation/contracts.py`: валидируемая immutable политика и config SHA.
- `query_validation/embedding.py`: порт, настоящая **локальная** multilingual SentenceTransformer и явный deterministic TEST fixture. Production не загружает модель из интернета автоматически, не использует скрытые платные embeddings.
- `query_validation/selection.py`: строгие проверки lineage/policy, positive vs negative cosine, полнофразовое hard exclude, stable exact + near-duplicate, greedy MMR с angle bonus, целевые лимиты и все rejected reason codes.
- `query_validation/service.py`: project-safe и конкурентоустойчивое получение и обработка source outbox, цельный PostgreSQL commit и upstream ACK, idempotent duplicate processing, read-only validated bundle, downstream pending and ACK.
- `tests/test_search_planner_query_validation.py`: unit QA без PostgreSQL, без model download.
- `tests/test_taxonomy_deep_db.py`: полноформатный PostgreSQL E2E начиная от RTB двух Human Reviews и заканчивая Query Validation; race двух consumers, isolation, no-viable и неизменяемость результатов.

## Реальная семантическая модель

Production **не** использует хеширование слов как семантические embeddings. Для исполнения нужно заранее и отдельно (под контролем оператора) установить:

```bash
python -m pip install sentence-transformers
```

Затем скачать и проверить модель, соответствующую рынкам/языкам проекта, **до запуска** и хранить локально. Подойдёт multilingual SentenceTransformer, например `sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`. Точный путь, версия весов и fingerprint должны фиксироваться оператором в `model_key`. Во время вызова включены `local_files_only=True` и `trust_remote_code=False` — никаких внешних загрузок весов, silent fallback и автоматических платных API.

CI использует `HashingTestEmbeddings` **только** с `allow_test_embeddings=True`. Fixture выдаёт deterministic lexical overlap vectors, **это не семантическая модель**. Результаты fixture нельзя получить через стандартный production `get_validated_bundle()`, они скрыты из стандартного `pending_validated_outbox()` и не могут быть подтверждены через production `ack_validated_outbox()`.

## Критерии Query Validation

`DEFAULT_POLICY` version `query-validation-config/v1`:

| Поле | Значение | Описание |
|---|---:|---|
| `min_positive_similarity` | 0.36 | минимальный положительный cosine proxy |
| `max_negative_similarity` | 0.94 | порог сильного отрицательного якоря |
| `min_positive_negative_margin` | -0.12 | negative margin; консервативный порог исследования |
| `semantic_duplicate_similarity` | 0.94 | близкие фразы в одной locale+mode |
| `mmr_lambda` | 0.72 | вес релевантности против redundancy |
| `max_selected_per_hypothesis` | 8 | дополнительный cap вместе с RTB target |
| `angle_diversity_bonus` | 0.03 | поощрение новых исследовательских angles |
| `cross_hypothesis_dedup` | true | разрешить исключать повторяющиеся строки между гипотезами |

Эти начальные настройки — конфигурация, **не** экспериментально калиброванная точность. Relevance = cosine к имени/описанию/include/examples гипотезы; negative = исключённые темы direction/branch/hypothesis и sibling names. Hard exclude действует только на достаточно длинную **полную** запрещённую фразу, чтобы не путать похожие слова с реальным логическим запретом. Двухсторонний косинус не является классификатором логического противоречия. Для высокорисковых scope decisions потребуется отдельно валидация человеком/LLM-judge или проверенный датасет.

MMR внутри каждой гипотезы: `λ*positive_similarity − (1−λ)*max_similarity(selected) + first_angle_bonus`. Все tie-breaks по стабильным UUID; результаты не зависят от входного порядка кандидатов.

Решение на каждый кандидат: `selected`, `explicit_exclusion`, `low_relevance`, `negative_scope`, `exact_duplicate`, `semantic_duplicate`, `quota`. Source candidates никогда не теряются молча; выходное покрытие включает `selected`, `no_source_candidates`, `all_rejected`.

## Обработка события, idempotency, atomicity

**Consumer — явная серверная Python-функция, не фоновый сервис и не пользовательский HTTP endpoint.** Сначала читает готовый outbox и immutable contexts, считает embeddings вне DB-lock, затем берёт `FOR UPDATE OF o` на source event. Проверяет SHA исходного bundle, model/policy config. Один source outbox может породить **один** qv_run: при повторном вызове с тем же policy/model вернётся старый результат, с другим — 409. Все decisions, coverage, qv_outbox и upstream `sqg_outbox.delivered_at` фиксируются одной транзакцией. При DB падении source остаётся unacked, повтор безопасен. Следующий Task Planner обязан принять validated event и durable записать задачи ДО `ack_validated_outbox()`.

## Trusted Python API

```python
from intelligence.search_planner.query_validation import (
    DEFAULT_POLICY, SentenceTransformerEmbeddingProvider,
    validate_generation_outbox, get_validated_bundle,
    pending_validated_outbox, ack_validated_outbox,
)

provider=SentenceTransformerEmbeddingProvider(
    "/path/to/operator-reviewed-embedding-model",
    model_key="multilingual-model-sha256-and-revision-pinned",
)
# event_id берётся из Generation Engine pending_outbox(project_id)
result=validate_generation_outbox(
    project_id,source_outbox_id,embedding_provider=provider,policy=DEFAULT_POLICY,
)
bundle=get_validated_bundle(project_id,result["validation_run_id"])
events=pending_validated_outbox(project_id)
# Только ПОСЛЕ durable commit реальных Task Planner tasks:
# ack_validated_outbox(project_id,events[0]["outbox_id"])
```

Если 0 валидных запросов для гипотезы, это зафиксировано в coverage; автоматический "платный перегенерировать" не выполняется. Вся Query Validation бесплатна относительно OpenRouter — локальная обработка, но CPU/GPU модель может потреблять ресурсы.

## QA и эксплуатация

```bash
python -m unittest discover -s tests -p 'test_search_planner_query_validation.py' -v
python -m unittest discover -s tests -p 'test_search_planner_stage_d.py' -v
python -m compileall -q intelligence/search_planner/query_validation

# ONLY disposable PostgreSQL 17 + миграция 016:
CM_TEST_PG_DSN=postgresql://...disposable... \
  python -m unittest discover -s tests -p 'test_taxonomy_deep_db.py' -v
```

Проверки: policy и диапазоны, no source, invalid/NaN/zero/too large vectors, stable results при shuffled input, no cross-project or cross-hypothesis source facts, exact/semantic duplicates, quota/MMR, exclude, all-rejected coverage, test-fixture quarantine, конкурентный outbox-consumer, immutable DB rows, upstream ACK/ downstream outbox атомарно. CI Linux+Windows и реальные PostgreSQL tests до merge.

**Остаётся отдельно:** production-упаковка/скачивание и проверка весов модели, threshold calibration на размеченных релевантных/нерелевантных видео, Task Registry/Planner, Video Collector, полноценный authenticated owner API/UI и эксплуатационный фоновой worker. Никакого подтверждённого video precision/recall lift на этапе Query Validation v1 нет.
