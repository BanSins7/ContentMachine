# AI Query Generator v1 — архитектура и контракт реализации

**Дата проектирования:** 23.10.2026. **Статус:** утверждаемая проектная спецификация; **не реализация в `main`**. Основана на исследовании `main` @ `c5d5eb515ff85e4df87ff2011ab571eaf5e23a59` и коде RTB. Читатель должен сначала прочитать [технологические адаптации](SEARCH_PLANNER_AI_QUERY_TECHNOLOGIES_V1.md), если реализует генерацию. Параллельные PR #10 (offline preview) и #13 (локальный DTK tool) **не являются работающим AI Query Generator**. Нет проверенного paid live E2E этого блока.

> **Цель:** из каждой опубликованной и утверждённой гипотезы RTB создавать несколько **кандидатных поисковых запросов** (keyword/hashtag) с точной привязкой к первоисточнику. Не выдавать найденные видео и не выбирать поискового провайдера.

## 0. Границы и последовательность

`Approved RTB Taxonomy → (1) Input Loader → (2) Hypothesis Context Builder → (3) Hypothesis Understanding → (4) Search Angle Discovery → (5) Query Synthesis → (6) Diversity Exploration → (7) Structured Output → (8) Transport/Technical Checks → (9) Persist & Handoff → Query Validation → Tool Registry → Task Planner → Video Collector`

Это **девять логических операций, НЕ девять LLM-вызовов**. Базовая конфигурация: Python для 1–2 и 7–9, **один структурированный LLM-вызов на пакет гипотез** для 3–6. Дополнительная платная итерация — только в отдельно разрешённом эксперименте/бюджете, никогда скрытая. Генератор **не выполняет** поиск по TikTok/YouTube/Instagram, не вызывает Apify/Bright Data/DTK, не читает найденные публикации, не скачивает видео, не делает creator discovery, не проводит новую Human Review и не применяет MMR/RRF. Одобренного RTB достаточно для автозапуска **в пределах заранее разрешённого отдельного поискового бюджета**; старое согласие на генерацию самой Taxonomy не разрешает новые расходы.

Контракт между блоками: ``AI Query → query_candidates/v1 → Query Validation``. Query Validation проверяет смысл, повторы, векторы и MMR; registry проверяет `keyword_search` / `hashtag_search` и доступность; planner готовит конкретные API payload. Генератор может произвести сомнительные кандидаты — важно сохранить их как кандидатов, не представлять их валидными.

## 1. Реальная граница RTB (source of truth)

Никакого чтения draft или принятых промежуточных Review: только join

- `tb_deep_publications`: `project_id`, `run_id`, `builder_publication_id`, `tree`, `published_at`; опубликованное дерево immutable (триггер).
- `tb_taxonomy_publications`: `id`, `project_id`, `run_id`, `taxonomy_version` — проверить совпадение публикации, версии, проекта, run.
- `tb_runs`: `project_id`, `id`, `status='approved'`, `profile_snapshot` — read-only контекст проекта на момент RTB.
- `tb_fact_review_submissions`: `approved_facts` для **того же** `run_id/project_id`, без unapproved/candidate facts.
- `tb_deep_reviews`: должны существовать `review_no=1` и `review_no=2` для данного run. `tree` последней опубликованной версии — истина, а не Review 1/2.

Эти таблицы и правила подтверждены в `intelligence/taxonomy_builder/deep_repository.py`, `deep_contracts.py` и миграции `014_tb_deep_hierarchical_taxonomy.sql`. Исключить cross-project IDs; чтение в одной согласованной БД-транзакции. Собрать канонический SHA-256 входа (например, используя `pipeline_contracts.canonical/sha256`). Любое изменение input snapshot или `taxonomy_version` требует **нового** generation ID.

Фактический RTB-node — объект с **одинаковыми полями независимо от вида**:
`id, parent_id, kind, relation, source_fact_ids, name, rationale, definition, include, exclude, examples`.
`kind ∈ {direction,branch,hypothesis}`, `relation ∈ {direct,problem_related,adjacent}`. Корневым бывает только direction; возможны пути `direction→hypothesis` и `direction→branch→hypothesis`. `source_fact_ids` — IDs, а не сами тексты фактов. Текст необходимо получить отдельно из **одобренных** `approved_facts` (обычно `id`, `kind`, `text`, возможно `source_quote`, `origin`).

**Единица генерации:** узел `kind=hypothesis`. Направления и ветки — контекст и границы, **не самостоятельные единицы генерации**. Проверка качества покрытия исходной RTB реализована до нашего блока; мы дополнительно проверяем, что все гипотезы имеют допустимый путь к direction. Порядок входных узлов фиксируется детерминированно по пути и ID, а не по случайному порядку JSON.

## 2. Расширенный HypothesisContext v1 — обязательный контракт

**Важно:** Context Builder **не переписывает** исходные поля. Он создаёт компактную **копию исходных полей трёх уровней**, разрешает ссылки на проверенные факты, добавляет разрешённые параметры поиска и детерминированные поля пути. Любая LLM-интерпретация находится **отдельно** в `semantic_analysis`, а не в авторитетном входе.

### 2.1 Поля `HypothesisContext`

| Поле | Тип / источник | Действие |
|---|---|---|
| `project_id`, `rtb_run_id`, `taxonomy_publication_id`, `taxonomy_version` | DB, authoritative | Строгая область проекта, immutable publication, ревизия |
| `taxonomy_sha256`, `context_schema_version` | Backend | Аудит, защита от смешения версий |
| `hypothesis` | Весь исходный RTB-node, `kind=hypothesis` | Не сокращать `definition/include/exclude/examples/rationale` |
| `direction` | Весь исходный RTB-node, `kind=direction` | Не сокращать; содержит общую цель/границы |
| `branch` | Полный `kind=branch` или `null` | Не подставлять искусственную подтему, если её нет |
| `ancestry_path` | Список ID root→leaf | Восстановить по `parent_id`; не искать по имени |
| `approved_facts` | ID+kind+text+provenance из approved facts | Объединение фактов трёх узлов (включая предков), без новых утверждений |
| `project_context` | Одобренный snapshot анкеты: `product_type`, фактические `product_functions`, `audience_problems`, `target_audience`, цель исследования, если есть | Отсутствующие сведения — `null`; вложенные файлы не анализируются |
| `search_policy` | Отдельная утверждённая конфигурация Search Planner | Языки, рынки (если заданы), режимы, бюджет и потолки |
| `neighbor_hypotheses` | ID, name, definition, include, exclude **соседей в той же ветке** | Только различение близких тем; ограничить например 5, не наделять отдельной авторитетностью |
| `derived_search_facets` | **Пусто на входе LLM** | Результат анализа 3–4, помеченный `generated`, не source facts |

Машинный контракт контекста: [HypothesisContext v1 JSON Schema](SEARCH_PLANNER_HYPOTHESIS_CONTEXT_V1.schema.json). JSON Schema проверяет форму; соответствие `ancestry_path`, approved facts и проекта проверяется **только сервером**.

**Контракт качества:** все 11 полей каждого RTB-node сохраняются; не заменять фактический `include` краткой LLM-выжимкой. Общий context ограничивается токенами **за счёт размера батча и сокращения списка соседей**, а не усечения `hypothesis`, `direction` или `branch`. Если полный контекст не помещается — уменьшить размер батча, затем явно `context_too_large`.

### 2.2 Полный пример HypothesisContext

Все ID и тексты ниже **синтетические**; фактические примеры пользователя не сохраняются. Поля `source_fact_ids` гипотезы/предков и `approved_facts` демонстрируют только разрешённый fact lineage.

```json
{
  "context_schema_version": "hypothesis-context/v1",
  "project_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "rtb_run_id": "11111111-1111-4111-8111-111111111111",
  "taxonomy_publication_id": "22222222-2222-4222-8222-222222222222",
  "taxonomy_version": 3,
  "taxonomy_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "ancestry_path": ["direction_home", "branch_no_equipment", "hyp_short"],
  "direction": {
    "id": "direction_home",
    "parent_id": null,
    "kind": "direction",
    "relation": "direct",
    "source_fact_ids": ["fact_product"],
    "name": "Домашний фитнес",
    "rationale": "Тема связана с подтверждённым видом продукта",
    "definition": "Видео об упражнениях, выполняемых дома",
    "include": "Тренировки дома для обычной аудитории",
    "exclude": "Только профессиональный соревновательный спорт",
    "examples": ["домашние тренировки"]
  },
  "branch": {
    "id": "branch_no_equipment",
    "parent_id": "direction_home",
    "kind": "branch",
    "relation": "problem_related",
    "source_fact_ids": ["fact_time"],
    "name": "Занятия без оборудования",
    "rationale": "Людям требуется доступный способ тренироваться",
    "definition": "Тренировки дома без тренажёров и покупки оборудования",
    "include": "Упражнения с весом тела",
    "exclude": "Ролики, для которых обязательны тренажёры",
    "examples": ["no equipment routine"]
  },
  "hypothesis": {
    "id": "hyp_short",
    "parent_id": "branch_no_equipment",
    "kind": "hypothesis",
    "relation": "problem_related",
    "source_fact_ids": ["fact_time"],
    "name": "Короткие тренировки востребованы среди занятых людей",
    "rationale": "Ограниченное время может менять предпочтения формата видео",
    "definition": "Исследовать короткие домашние занятия без оборудования для людей с нехваткой времени",
    "include": "5–15-минутные тренировки дома без специального инвентаря",
    "exclude": "Зал, часовые занятия, упражнения, требующие тренажёров",
    "examples": ["10 minute home workout", "пятиминутная зарядка"]
  },
  "approved_facts": [
    {
      "id": "fact_product",
      "kind": "product_type",
      "text": "Продукт связан с домашними тренировками",
      "source_quote": "Подтверждено владельцем в анкете",
      "origin": "explicit"
    },
    {
      "id": "fact_time",
      "kind": "problem",
      "text": "У части аудитории не хватает времени на спорт",
      "source_quote": "Утверждённая формулировка проблемы аудитории",
      "origin": "explicit"
    }
  ],
  "project_context": {
    "product_type": "сервис для домашних тренировок",
    "product_functions": null,
    "audience_problems": ["нехватка времени на занятия"],
    "target_audience": null,
    "research_goal": "исследовать виды существующих коротких видео"
  },
  "neighbor_hypotheses": [
    {
      "id": "hyp_beginner",
      "name": "Первые тренировки новичков",
      "definition": "Контент о начале домашних занятий",
      "include": "ролики для начинающих",
      "exclude": "продвинутые силовые программы"
    }
  ],
  "search_policy": {
    "languages": ["ru", "en"],
    "markets": [],
    "allowed_modes": ["keyword", "hashtag"],
    "target_candidates_per_hypothesis": 10,
    "max_candidates_per_hypothesis": 16,
    "max_total_candidates": 480
  },
  "derived_search_facets": []
}
```

`taxonomy_sha256` в примере — **синтетическое 64-символьное hex-значение**, а сервер обязан вычислять реальный SHA-256 точной публикации. Такой sample не является реальной опубликованной Taxonomy. Поля `project_context` читаются из существующего RTB profile snapshot через явное allowlist-маппирование; schema миграций проекта не предполагает, что у всех анкет есть `target_audience`/`research_goal`. Отсутствие ≠ утверждённое отрицание.

### 2.3 Построение контекста в Python

```python
def make_context(publication, approved_facts, node, by_id, settings):
    assert node["kind"] == "hypothesis"
    path, seen = [], set()
    cur = node
    while cur is not None:
        if cur["id"] in seen:
            raise ContextError("cycle")
        seen.add(cur["id"])
        path.append(cur)
        cur = by_id.get(cur["parent_id"]) if cur["parent_id"] else None
    path.reverse()
    if path[0]["kind"] != "direction":
        raise ContextError("missing_direction")
    if any(path[i]["parent_id"] != path[i-1]["id"] for i in range(1,len(path))):
        raise ContextError("broken_path")
    eligible_ids = set().union(*(set(n["source_fact_ids"]) for n in path))
    linked = [f for f in approved_facts if f["id"] in eligible_ids]
    if len(linked) != len(eligible_ids):
        raise ContextError("unknown_approved_fact")
    # Copy ALL RTB fields from source; no interpretation here.
    return build_typed_context(path, linked, publication, settings)
```

Пример иллюстрирует проверку; `build_typed_context` надо реализовать с явным контрактом и полной проверкой `project_id/run_id`. При прямом `direction→hypothesis`: `branch=null`, `ancestry_path` из двух ID. `neighbor_hypotheses` строятся только из той же публикации, не из прошлых запусков.

## 3. Подробный pipeline и ответственность

### 01 — Input Loader (`input_loader.py`, deterministic)

Проверить immutable publication, принадлежность проекту, статус run, обе Human Review, наличие фактов. Считать дерево **все узлы** один раз; `validate_tree` и проверка `quality_report` используются лишь как защита от порчи контракта — **не повторяем смысловую/человеческую экспертизу RTB**. Собрать ровно все `kind=hypothesis`, стабильный порядок. Пусто → `no_hypotheses` (blocked), не успешный пустой план.

### 02 — Context Builder (`context_builder.py`, deterministic)

Собрать полный `HypothesisContext`, текст approved facts, пути, до 5 соседей, allowlisted профиль, search_policy. Тексты из анкеты/RTB — **untrusted prompt data**, не системные инструкции: передавать как JSON-поля, запрещать модели выполнять содержащиеся в них команды. Не читать вложения и не выдумывать отсутствующие рынки/языки. Если languages нет в утверждённой конфигурации, поставить `needs_language_policy` (не делать молчаливый глобальный English).

### 03 — Hypothesis Understanding (`prompt_builder.py`, LLM)

Для каждой гипотезы определить **из имеющихся полей**, а не придумать:
- объект/действие; кто аудитория; проблема; решение; ситуации использования;
- mandatory anchors (нельзя потерять); optional search vocabulary;
- negative boundaries (`exclude`) и соседние гипотезы;
- возможность контента, который **опровергает** гипотезу.

Внутренний `semantic_analysis` может включать `audience/problem/action/setting/constraints`, `observability_notes` и `missing_information`; значения со статусом `model_derived`, не approved facts. Если сделать короткий запрос в пределах границ нельзя, вернуть `no_viable_queries`.

### 04 — Search Angle Discovery (LLM, тот же вызов)

Выбирать релевантные поисковые углы:
`direct, problem, solution, situation, format, comparison, experience, critical, other`.
`critical` полезен против confirmation bias. Для каждого угла сформулировать `angle_intent` и `grounded_anchor`. Угол ≠ готовая строка. Ни один угол не обязателен, модель может выбрать 3–6, если остальное искусственно. Запрещать придумывать тренды/виральность и несуществующие хэштеги как факт.

### 05 — Query Synthesis (LLM, тот же вызов)

По каждому выбранному углу создать **короткие, естественные, реально используемые в поиске видео** строковые запросы; не длинные псевдодокументы:
- `keyword`: свободная фраза до 120 символов, без API-операторов, не включать `site:`, URLs, JSON или инструкции.
- `hashtag`: одно словосочетание в форме тега без `#` (нормализация формы/пробелов — в следующем блоке); только если форма правдоподобна, а не утверждение о её популярности.
- `language`: из `search_policy.languages`; не переводить механически все варианты на все языки, если это уничтожает бюджет.
- `research_stance`: `support, challenge, neutral` — **тип искомого содержания**, не утверждение об истинности.
- `angle`: угол, `rationale`: краткое объяснение соответствия гипотезе.

**Пример:** `10 minute home workout` / `workout before work` / `no equipment 10 min` / `are short workouts enough`. Запрос `no time to exercise` без тренировочного anchor имеет высокий риск ухода от темы и будет проверен в Query Validation.

### 06 — Diversity Exploration (LLM, тот же вызов)

Применить **требование смысловой новизны**: варианты должны отличаться поисковой ситуацией, аспектом или форматом, а не перестановкой одних слов. После первоначального покрытия попросить модель проверить **пропущенные допустимые грани** и предложить дополнительные варианты лишь если они действительно добавляют покрытие. Никаких blind synonyms, forced quotas, обучения или скрытых поисковых вызовов. Применяем ThinkQE-**inspired** внутримодельную exploration, **не** полный corpus-feedback ThinkQE (подробнее во втором документе). Если новых граней нет — вернуть меньше кандидатов.

### 07 — Structured Output (`schemas.py`, Python)

OpenRouter LLM обязана вернуть **отдельный структурированный JSON для каждого батча**, где каждый `hypothesis_id` привязан к известному входу. **Для воспроизводимой реализации трёх адаптаций нужен inspectable raw trace**, а не только короткие итоговые запросы:

- `semantic_profile` — LLM-разбор границ гипотезы.
- `facets[]` — ThinkQE-inspired смысловые углы с `facet_key,angle,intent,anchor_terms`.
- `facets[].sketches[]` — MuGI-inspired **синтетические**, не найденные видео.
- `facets[].lexical_hints[]` — Jina-inspired поисковые слова и речевые варианты.
- `queries[]` — фактические короткие поисковые строки с `facet_key,text,query_mode,angle,language,research_stance,rationale`.
- `no_viable_reason` — nullable причина отсутствия поисковых вариантов.

**Канонический raw transport:** [JSON Schema](SEARCH_PLANNER_AI_QUERY_RAW_LLM_V1.schema.json); полный пример trace и **точная последовательность методов** — [TECH_IMPLEMENTATION_V1](SEARCH_PLANNER_AI_QUERY_TECH_IMPLEMENTATION_V1.md). Модель не назначает `project_id`, `publication_id`, `generation_id`, `candidate_id`, `source_fact_ids`: backend прикрепляет их на основании immutable `HypothesisContext`. Никакая синтетика не передаётся в Video Collector. JSON Schema должна использовать `strict` и `additionalProperties:false` (в пределах реально поддерживаемого model/provider subset), остальные cross-field инварианты проверяет Python.

### 08 — Technical Checks (`schemas.py` / `output_builder.py`, deterministic)

Проверить структуру, завершённый `finish_reason=stop`, совпадение модели/snapshot, `hypothesis_id` ровно из **этого** батча, уникальность IDs гипотез, валидность каждой строки/mode/language/length, total per-hypothesis ≤ max, отсутствие неразрешённых полей. Не принимаем partial JSON, неизвестные hypotheses, повтор элементов с тем же ID. Каждый батч должен покрыть **все** присланные hypotheses состоянием (`generated` с ≥1 candidates или `no_viable_queries` с объяснением). Действующая raw Schema задаёт поле `no_viable_reason`; соответствие количества и причины дополнительно проверяет Python. Нельзя тихо выбрасывать ошибочную гипотезу и считать batch успешным.

Здесь допускается только **точное** техническое выявление дублей в пределах одного батча для диагностики. **Смысловые** дубли, drift, MMR, embeddings, ranking → исключительно следующий блок `Query Validation`.

### 09 — Persist & Handoff (`repository.py`, `service.py`, deterministic)

Сервер собирает `query_candidates/v1`: фиксирует входные hashes/версии, добавляет свой UUID candidate ID и source lineage, записывает оригинальный структурированный response и нормализованный кандидатный набор с attempt/usage. Выход **не содержит выполненных результатов поиска**, `provider_id`, `apify_actor_input`, `popularity` или `relevance_score`. Результат передаётся в Query Validation из durable DB, а не по неявной памяти процесса.

## 4. Канонический результат — что получает следующий блок

`docs/SEARCH_PLANNER_AI_QUERY_CANDIDATES_V1.schema.json` — каноническая JSON Schema **успешно завершённого набора** (проверяется backend, а не вставляется полностью в вызов LLM). Пример:

```json
{
  "schema_version": "query_candidates/v1",
  "generation_id": "33333333-3333-4333-8333-333333333333",
  "project_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "taxonomy_publication_id": "22222222-2222-4222-8222-222222222222",
  "taxonomy_version": 3,
  "input_sha256": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "prompt_version": "ai-query-v1",
  "model_id": "configured/openrouter-model",
  "status": "completed",
  "candidates": [
    {
      "candidate_id": "44444444-4444-4444-8444-444444444444",
      "hypothesis_id": "hyp_short",
      "direction_id": "direction_home",
      "branch_id": "branch_no_equipment",
      "source_fact_ids": ["fact_time"],
      "text": "10 minute home workout",
      "query_mode": "keyword",
      "angle": "direct",
      "language": "en",
      "research_stance": "neutral",
      "rationale": "Домашнее занятие короткой продолжительности"
    },
    {
      "candidate_id": "55555555-5555-4555-8555-555555555555",
      "hypothesis_id": "hyp_short",
      "direction_id": "direction_home",
      "branch_id": "branch_no_equipment",
      "source_fact_ids": ["fact_time"],
      "text": "are 10 minute home workouts enough",
      "query_mode": "keyword",
      "angle": "critical",
      "language": "en",
      "research_stance": "challenge",
      "rationale": "Потенциально критический контент"
    }
  ],
  "hypothesis_coverage": [
    {
      "hypothesis_id": "hyp_short",
      "state": "generated",
      "candidate_count": 2,
      "reason": null
    }
  ]
}
```

`source_fact_ids` из **самой гипотезы** (не всех предков), весь разрешённый fact context берётся при необходимости по её `hypothesis_id + publication_id`. Статусы покрытия **успешно завершённой генерации**: `generated`, `no_viable_queries`; `failed`/`not_processed` относятся к состоянию незавершённого run и не допускаются в успешный envelope. Пример выше — subset от общего результата только для наглядности; весь `completed` generation требует записи покрытия для **каждой** гипотезы публикации, даже если она вернула 0. `status=completed` означает завершённость попытки генерации, **не** валидность или релевантность кандидатов. Если batch unresolved/uncertain, генерация **не** выдаёт успешный общий envelope.

Validation может прочитать этот контракт, связать candidate ID с исходными `definition/include/exclude`, отклонить неподходящее и применить embeddings/MMR. После Validation Tool Registry и Task Planner превратят принятую строку в, например, `{"tool_id":"dtk_tiktok","operation":"search_posts","params":{"keyword":"10 minute home workout"}}`. Этот payload **не создаётся генератором**.

## 5. Политики работы и стоимость

**Параметры v1 (настраиваемые, НЕ уже доказанные optimum):**
- Цель `8–12` кандидатов/гипотезу, потолок `16`; разрешено меньше без искусственных дублей.
- Верхний предел `max_total_candidates=480` на генерацию; `MAX_NODES=48` для всей Taxonomy — не надо приравнивать число узлов к числу гипотез.
- Начальный batch `3–5 гипотез`, адаптация под token estimate. Приоритет честного контекста, а не заполнения batch.
- Все гипотезы фиксируются в coverage; при бюджетном лимите часть остаётся `not_processed` на уровне run и **не помечается completed**.
- Языки берутся из allowlist search_policy, не из предполагаемой географии пользователя.
- `query_mode` отражает желаемый тип поиска, но **не означает**, что подключённый инструмент его поддерживает.
- Multi-sample (MuGI-inspired) **выключен по умолчанию** до измеримого улучшения на golden наборе и явного бюджета.

**LLM provider:** повторно использовать инфраструктуру каталога/цены/OpenRouter из `intelligence/taxonomy_builder/provider.py` лишь как референс интерфейса; **не** переиспользовать в обход role-spec существующий `request_payload`. Пинить `model_id`, provider, pricing_snapshot, `prompt_version`, JSON Schema hash, параметры temperature/reasoning/max_output_tokens, `require_parameters:true` / `allow_fallbacks:false`. Предварительно проверять поддержку `response_format: json_schema`. Хранить `response_id`, usage, rates snapshot, actual USD; предельные значения output/context.

**Никаких скрытых расходов:** новая отдельная authorisation для Search Planner (`authorized_usd` / scope / expiry / revocation); расчёт conservative reservation → DB запись с уникальным ключом до POST → 1 provider attempt → settle actual usage. Действующее право запускать автоматический pipeline **не заменяет** финансовое разрешение. Даже если обработка стартует автоматически после публикации RTB, без разрешения status `paused_budget`, оплачиваемых вызовов 0.

После timeout/connection loss/несогласованного HTTP не повторять платный POST: `uncertain`, сохранить reservation, попробовать безопасный provider reconcile по имеющемуся ID, иначе ручное расследование. Повторные GET/восстановление воркера не оплачиваются и не порождают новые генерации. Никакого `retry until valid JSON`, если ответ мог быть выставлен к оплате; корректирующий вызов — отдельная попытка с новым бюджетным резервом и явной политикой.

## 6. Хранение (новые таблицы, будущая миграция; НЕ существуют сейчас)

| Таблица | Обязательные поля / ограничение |
|---|---|
| `search_query_generations` | `id UUID`, `project_id`, `rtb_run_id`, `publication_id`, `taxonomy_version`, `input_sha256`, `prompt_version`, `model_id`, `schema_sha256`, `config_snapshot JSONB`, `authorized_usd`, `spent_usd`, `reserved_usd`, `status`, `created_at`, `completed_at`; UNIQUE(project_id, publication_id, config_hash, idempotency_key) |
| `search_query_batches` | `id`, `generation_id`, `batch_index`, `hypothesis_ids JSONB`, `request_sha256`, `reservation_usd`, `attempt_id`, `status`, `response_id`, `usage JSONB`, `charged_usd`, `error_code`; UNIQUE(generation_id,batch_index); отдельный attempt не перепосылается |
| `search_query_candidates` | `candidate_id UUID`, `generation_id`, `batch_id`, `hypothesis_id`, `direction_id`, `branch_id nullable`, `source_fact_ids JSONB`, `text`, `query_mode`, `angle`, `language`, `research_stance`, `rationale`, `raw_position`, `created_at`; UNIQUE(generation_id,batch_id,raw_position) |
| `search_query_hypothesis_coverage` | `generation_id`, `hypothesis_id`, `state`, `candidate_count`, `reason`; UNIQUE(generation_id,hypothesis_id) |

Обязательные индексы `project_id,publication_id,status` / `generation_id`; FK на существующие project/taxonomy сущности **в пределах их реального composite key**; append-only output/attempt audit, секреты никогда не писать в JSONB. Миграция аддитивна, создавать **только в будущей реализации**, использовать отдельное согласие для новых расходов, не изменять `tb_deep_publications`.

## 7. Предлагаемая структура кода

```text
intelligence/search_planner/
  ai_query/
    __init__.py
    input_loader.py        # immutable RTB, approved facts, project-scope
    context_builder.py     # полный HypothesisContext и ancestry
    prompts.py             # semantic angles + synthesis, версии
    generator.py           # OpenRouter single paid call / batch
    schemas.py             # raw LLM + final candidate contracts
    output_builder.py      # server IDs, lineage, order, coverage
    repository.py          # транзакции, snapshots, attempts, usage
    service.py             # state machine, authorization, batching, handoff
    diagnostics.py         # безопасная наблюдаемость, без ключей
  query_validation/        # ОТДЕЛЬНО: embeddings, MMR, drift, dedup
  registry/                # ОТДЕЛЬНО: capability/credentials
  task_planner/            # ОТДЕЛЬНО: API specific task routing
```

В коде лучше держать `Understanding/Search Angles/Synthesis/Diversity` в одном `prompts.py` с явными логическими инструкциями; отдельные физические функции не обязаны означать дополнительные оплачиваемые LLM-вызовы. `Search Planner` orchestration стартует по событию публикации/надёжному отложенному dispatch с идемпотентностью; **не** посредством GET, который читает историю.

## 8. Набор обязательных тестов (Definition of Done)

### Offline, без сетевых/платных вызовов

1. Только 2 Human Reviews + immutable публикация; чужой проект, draft, неутверждённый факт, подмена taxonomy_version, повреждённая цепочка/цикл → отклонены.
2. Прямой `direction→hypothesis` правильно даёт `branch:null`.
3. У всех трёх nodes **полные 11 RTB-полей**; `include/exclude/rationale/examples` сохраняются без перефразирования; canonical input SHA стабилен.
4. Fact IDs разрешаются лишь по approved facts того же `run_id`, never unapproved; provenance кандидата берётся backend, а не из LLM.
5. Batch split детерминированный, полный coverage на 1/2/48 nodes, нет усечения контекста; missing hypothesis flagged.
6. JSON Schema strict, response with extra key, unknown hypothesis, invalid mode/language, long/empty query, missing batch item, unfinished finish_reason → error, не silently successful.
7. Нулевые поисковые/DTK/Apify/Bright Data вызовы при генерации, n=1 paid call на разрешённый batch, GET никогда не вызывает LLM.
8. Consent revoked / no funds / budget cap → 0 paid calls; повтор webhook/worker crash/HTTP timeout → no repeat POST, статус `uncertain`, reserve сохранён.
9. Golden taxonomy с несколькими языками, похожими соседними гипотезами и `include/exclude`; snapshot ожидаемых семантических граней; sample запроса не должен только подтверждать гипотезу.
10. Query Validation contract test: `query_candidates/v1` валиден для handoff; не содержит provider payload/video/results/embeddings/MMR score.

### Controlled experiment (отдельно от unit)

Сравнить на одних и тех же опубликованных RTB fixtures: простой `hypothesis name` baseline, angle-based LLM, +ThinkQE-inspired exploration, +MuGI-inspired multi-sampling, +Jina-style vocabulary hints. Метрики: техническая валидность, novelty angles, semantic drift (размеченный golden set), duplicate share, число различных кандидатных формулировок на USD и *позже* unique qualified videos / provider / cost. До живого Collector никаких «+X% recall TikTok» не заявлять.

**Ограничение статусной честности:** документ является критериями реализации, а не заявлением, будто `intelligence/search_planner/ai_query/`, связанные таблицы или LLM-выход уже есть в `main`.
