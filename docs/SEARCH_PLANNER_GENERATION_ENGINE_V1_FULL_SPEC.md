# ContentMachine · Search Planner · Generation Engine v1 — полный контракт реализации

> **Тип:** техническая спецификация «design → code», отдельный документ для разработчиков.  
> **Дата:** 23 октября 2026 года. **Основа кода:** `main` @ `c5d5eb515ff85e4df87ff2011ab571eaf5e23a59`.  
> **Зависимости в GitHub:** документация AI Query — PR [#14](https://github.com/BanSins7/ContentMachine/pull/14); реализуемый RTB Input Loader / HypothesisContext Builder — PR [#15](https://github.com/BanSins7/ContentMachine/pull/15).  
> **Статус:** Generation Engine, указанные ниже таблицы, API, worker и OpenRouter generation **ЕЩЁ НЕ РЕАЛИЗОВАНЫ**; это согласованный целевой контракт. Состояние `main` отличается от незамёрженных PR. Реальные платные испытания Search Planner не проводились.
>
> **Назначение файла:** по этому документу можно спроектировать миграцию, написать Python-модули и тесты без восстановления правил по переписке. Все неявные допущения, единицы измерения, восстановление, права и ответы API задаются ниже.

## Навигация

1. Назначение, границы, термины и источник истины.
2. Полная диаграмма и компоненты.
3. Входные контракты, настройки и неизменяемые снимки.
4. Batch Planning, Model Manager и Cost Estimate.
5. State Machines и транзакционные инварианты.
6. PostgreSQL: полная логическая модель, ограничения и миграция.
7. Financial Authorization, Budget Ledger и цена.
8. Execution Manager, очередь PostgreSQL и протокол HTTP.
9. Recovery / Reconciliation / Cancel.
10. LLM Transport Port, Output Manager и handoff.
11. HTTP API, UI, наблюдаемость и безопасность.
12. Test Matrix, rollout, Definition of Done, пример E2E.
13. Аудит документации и связь со смежными блоками.

---

## 1. Зачем существует Engine

`Generation Engine` — **детерминированный, транзакционный оркестратор оплачиваемой работы** для `AI Query Generator`.

**Не путать:**
- **RTB (Research Taxonomy Builder)** с двумя Human Review создаёт и публикует неизменяемое дерево исследования. Не ищет видео.
- **Input Loader + HypothesisContext Builder (PR #15)** читают только опубликованное RTB, собирают полные контексты гипотез. Не используют LLM.
- **Generation Engine (этот документ)** управляет жизненным циклом генерации, моделью, пакетами, деньгами, очередью, доставкой и восстановлением.
- **AI Query Generator** (следующий этап) формирует `semantic_profile / facets (ThinkQE-inspired) / sketches (MuGI-inspired) / lexical_hints (Jina-inspired) / queries`. Engine не знает внутреннюю семантику методов.
- **Query Validation** после генерации проверяет соответствие `definition/include/exclude`, дедупликацию, embeddings/MMR и только потом разрешает запросы.
- **Tool Registry + Task Planner** выбирают доступные поисковые возможности, платформы, финансовые правила и формируют конкретные задания.
- **Video Collector / Discovery / SRE** выполняют поиск роликов и анализируют реальные данные. Никакого Video Collector внутри Engine.

**Non-goals этапа №2:** писать настоящие промпты ThinkQE/MuGI/Jina; искать TikTok/YouTube/Instagram; выполнять Apify/Bright Data/DTK; строить векторное хранилище; считать viral/breakout; изменять `tb_deep_publications`; создавать второй Discovery; делать незапрошенные платные вызовы. На этом этапе все результаты LLM — **fake-provider fixtures**.

## 2. Общий runtime pipeline

```text
Approved immutable RTB publication [tb_deep_publications]
  │
  ▼
input_loader.load_approved_publication(project_id, publication_id)  ← existing PR #15
  │   verifies same project/run, 2 Human Reviews, approved_facts
  ▼
context_builder.build_hypothesis_contexts(snapshot, search_policy)  ← existing PR #15
  │   full RTB nodes × 11 fields + ancestor fact text + profile snapshot
  ▼
┌───────────── GENERATION ENGINE ─────────────────────────────────────────┐
│ Configuration Service  → immutable approved config/model/prompt/schema │
│ Estimator/Quote         → priced batches + expiry + verified catalog    │
│ Authorization           → explicit separate Search Planner consent      │
│ Run Manager             → stable generation_id + context snapshots      │
│ Batch Manager           → deterministic batches and exact input hashes  │
│ PostgreSQL Ledger       → money reservation, attempts, status, events    │
│ Worker/Executor         → one active dispatch, no silent paid retry      │
│ Recovery                → stopped/crashed/uncertain/price-changed        │
│ Output Manager          → complete coverage and query_candidates/v1      │
└─────────────────────────────────────────────────────────────────────────┘
  │                                            │
  │ one bounded provider port (stage 3)       │ durable records
  ▼                                            ▼
AI Query / OpenRouter                      PostgreSQL
  │  raw JSON, usage, model ID, finish_reason
  └─────────────────────────► Engine: settle, validate, persist
                                  │
                         complete candidate bundle
                                  ▼
                       Outbox (same DB transaction)
                                  ▼
                      Query Validation (consumer)
                                  ▼
                   Registry → Planner → Collector
```

### 2.1 Четыре главные границы

1. **Снимок RTB → Engine:** только `HypothesisContext/v1`; никакого draft-дерева. Нужен `taxonomy_publication_id` = `tb_taxonomy_publications.id`, связанный с `tb_deep_publications.builder_publication_id`; это НЕ `run_id`.
2. **Engine → LLM:** неизменяемая команда на известный батч, закреплённые версии prompt/model/schema и отдельная финансовая авторизация. Engine транспортно-нейтрален относительно промптов.
3. **LLM → Engine:** `raw_llm/v1` (см. `SEARCH_PLANNER_AI_QUERY_RAW_LLM_V1.schema.json`), usage, provider response ID, finish reason. **Ни один model-declared ID не считается источником истины.**
4. **Engine → Query Validation:** только серверный `query_candidates/v1` (см. `SEARCH_PLANNER_AI_QUERY_CANDIDATES_V1.schema.json`); без synthetic sketches, provider task payload, рейтингов релевантности, результатов видео.

### 2.2 Терминология / ID

| Термин | Определение | Кто выдаёт |
|---|---|---|
| `project_id` | локальный 32-hex идентификатор проекта | ContentMachine projects |
| `rtb_run_id` | UUID выполнения RTB | RTB |
| `taxonomy_publication_id` | UUID конкретной immutable публикации RTB | `tb_taxonomy_publications` |
| `taxonomy_version` | целочисленная версия **в пределах проекта** | RTB |
| `hypothesis_id` | RTB node ID, стабилен только внутри publication | RTB |
| `context_sha256` | SHA-256 одного полного HypothesisContext canonical JSON | Engine |
| `generation_id` | UUID запуска, привязанного к exact publication/config/consent | Engine |
| `batch_id` | UUID одной детерминированной группы гипотез | Engine |
| `attempt_id` | UUID отдельной финансово учитываемой попытки вызова | Engine |
| `candidate_id` | UUID строки поиска | Output Manager |
| `idempotency_key` | явно переданный ключ Create/Start операции | API client; проверяет сервер |
| `response_id` | opaque ID от OpenRouter, только если действительно получен | Provider |

**Правило идентификации гипотез:** вместе использовать `(project_id, taxonomy_publication_id, hypothesis_id)`, никогда только `hypothesis_id`.

## 3. Восемь обязательных сервисов (и внутренние порты)

| Сервис / модуль | Обязанности | Чего не делает |
|---|---|---|
| `RunManager` | проверка scope, создание run из approved context, идемпотентность, статусы, cancellation | не вызывает LLM |
| `BatchManager` | детерминированный stable sort, оценка размера, partitions, coverage manifest | не сокращает поля RTB |
| `ModelConfigurationManager` | выбор проверенной модели, закрепление provider/version/price/schema/prompt | не выбирает модель «на лету» |
| `BudgetManager` | quote, separate consent, funds reservation/settlement/revocation/overcharge | не считает estimated цену фактическим списанием |
| `ExecutionManager` | claim batch, atomic reserve, dispatch intent, вызов Port и persistence outcome | не применяет ThinkQE/MMR |
| `Repository` | сериализация снимков, constraints, locks, events, exact reads | не хранит секреты |
| `RecoveryManager` | детектирование stuck attempts, расследование uncertain, explicit resolution | не повторяет POST при неизвестной оплате |
| `OutputManager` | backend IDs/lineage, полное покрытие, schema validation, outbox | не выбирает DTK/Apify или quality relevance |

Физически `ModelManager/Estimator` могут использовать существующий read-only каталог OpenRouter из `intelligence/taxonomy_builder/catalog.py`, а транспорт — принципы `taxonomy_builder/provider.py` (single request, bounded response, no automatic retry, unknown billing). **Нельзя** переиспользовать оплачиваемый RTB-`request_payload()`: роли/промпты и budget authorization совершенно другие.

## 4. Входные и управленческие контракты

### 4.1 HypothesisContext (готовый PR #15)

Контекст содержит:
- `context_schema_version = hypothesis-context/v1`, `project_id`, `rtb_run_id`, `taxonomy_publication_id`, `taxonomy_version`, `taxonomy_sha256`;
- `ancestry_path` root→branch(optional)→hypothesis, полные `direction/branch/hypothesis` с **11 авторитетными RTB-полями**;
- approved facts трёх уровней с оригинальными `text/kind/source_quote/origin` (если значение отсутствовало, остаётся `null`);
- `project_context` по allowlist профиля, соседние гипотезы (до 5), явную `search_policy`, `derived_search_facets=[]`.
- **Никакой выдуманной аудитории, языка, географии или сокращения source facts.**

Engine сохраняет `context_sha256 = sha256(context)` и **сам полный JSON**. Все его поля, включая язык, входят в immutable run. Изменение RTB/config = новая generation; старые contexts не переписываются.

### 4.2 Generation Configuration (версия и согласие)

```json
{
  "schema_version": "search-generation-config/v1",
  "search_policy": {
    "languages": ["ru", "en"],
    "markets": [],
    "allowed_modes": ["keyword", "hashtag"],
    "target_candidates_per_hypothesis": 10,
    "max_candidates_per_hypothesis": 16,
    "max_total_candidates": 480
  },
  "model": {
    "provider": "openrouter",
    "model_id": "explicitly-verified-model-id",
    "catalog_snapshot_id": "catalog-snapshot-uuid",
    "reasoning": {"mode": "model-supported"},
    "temperature": null,
    "max_output_tokens": 8000,
    "prompt_version": "ai-query-v1",
    "strategy_version": "semantic-facets-v1",
    "raw_schema_version": "ai-query-raw/v1",
    "samples_per_batch": 1
  },
  "execution": {
    "target_hypotheses_per_batch": 4,
    "hard_max_hypotheses_per_batch": 5,
    "max_concurrent_provider_calls": 1,
    "automatic_paid_retries": 0,
    "require_explicit_budget_consent": true
  }
}
```

Здесь `model_id` — **placeholder**, не существующая конкретная модель. Поле `reasoning` подставляется только при подтверждённой совместимости выбранной модели и фактического OpenRouter API. Если конкретная модель не поддерживает `json_schema` / `max_output_tokens` / сочетание `temperature+reasoning`, конфигурация **не проходит approval**, а не переключается на fallback без согласия.

**Config lifecycle:** `draft → validated → approved_immutable`. Изменение вызывает новую ревизию (а не UPDATE существующей approved config). Для работы нужны точные `model snapshot, tariff snapshot, canonical prompt SHA, JSON Schema SHA, version strategy`, а не одни строковые имена.

### 4.3 Canonical fingerprint и повторяемость

```python
config_hash = sha256(approved_config)
input_hash = sha256({
    "project_id": project_id,
    "publication_id": taxonomy_publication_id,
    "taxonomy_version": taxonomy_version,
    "taxonomy_sha256": taxonomy_sha256,
    "context_hashes": [sha256(c) for c in contexts_in_stable_order],
    "config_hash": config_hash,
    "prompt_sha256": pinned_prompt_sha,
    "schema_sha256": pinned_raw_schema_sha,
    "model_catalog_sha256": pinned_catalog_sha
})
```

Использовать существующие `canonical()/sha256()` из `taxonomy_builder/pipeline_contracts.py` (canonical `ensure_ascii=False, sort_keys=True, separators=(',', ':')`). В `input_hash` **не** включать текущее время, случайный UUID, статус и расходы: тогда повторные чтения одного input устойчивы. Но `generation_id` и `idempotency_key` — независимые системные сущности; намеренно новый запуск с тем же input, но отдельным consent / new run разрешён, историю нельзя перезаписывать.

## 5. Batch Manager: детальный детерминированный алгоритм

1. Получить ровно все `kind=hypothesis` из одной approved publication, 1–48 узлов во всём RTB (не путать с количеством гипотез).
2. Отсортировать по `ancestry_path` + `hypothesis.id`, группировать по root `direction`. Внутри группы можно видеть siblings; **не** смешивать разные проекты/версии.
3. Оценить **байтовый/tokens upper bound** полного batch message + потенциального raw response; учитывать сообщения, system prompt, JSON Schema wrapper и reasoning/output reserve. Цена и лимиты — от pinned snapshot. Не использовать ненадёжное буквальное `1 word = 1 token`.
4. Добавить очередной контекст лишь если `size≤target_count`, `count≤hard_max`, и conservative `estimated_tokens+max_output_tokens≤model_context`.
5. Если следующий context не вмещается — закрыть текущий batch и начать новый. Если **одна** гипотеза целиком не вмещается — остановить с `blocked_config/context_too_large`. Не обрезать `definition/include/exclude/examples/approved_facts`.
6. Для каждого batch фиксировать `index`, `direction_id`, `hypothesis_ids[]`, `context_hashes[]`, `batch_input_sha256`, `max_output_tokens`, `estimated_input_tokens`, `estimated_reservation_usd`. Эти данные неизменяемы.
7. Распределение расходов по батчам не должно автоматически увеличивать лимит кандидатов при большой Taxonomy. Окончательная server-side проверка `max_total_candidates` проходит на output assembly.
8. `samples_per_batch=1` по умолчанию; если позже утвердим MuGI multi-sampling N=2, это **N отдельных попыток с отдельной ценой**, не скрытый вызов в рамках одного.

**Практические defaults для эксперимента (не научно доказанная оптимальность):** целевой batch=4, hard max=5, максимум кандидатов/гипотезу=16, target=10, max candidates all=480, одновременных оплачиваемых запросов=1.

### 5.1 Пример: 24 гипотезы

24 гипотезы по 4 → 6 batch при достаточном контекстном окне. Batch #3 содержит 4 **целых** HypothesisContext с их полями и фактами, а не только 4 строки `name`. Если из-за размера fit=3, реальное число batch станет больше; `6` — иллюстрация, а не жесткая гарантия.

### 5.2 Почему batch стабилен

После создания generation менять состав batch запрещено, иначе перестанет совпадать `request_sha256` и будет неясно, какой платный ответ относится к какому входу. Ребалансировка допускается только **до первого платного dispatch** с новой конфигурацией/новым run (или в явно версионируемом `planning revision`, если его специально введём; v1 не вводим).


---

## 6. State Machine — все состояния и допустимые переходы

**Нельзя** выводить статус generation из того, что HTTP request просто был начат или что UI показал 100%. Каждый статус — транзакционная запись PostgreSQL.

### 6.1 Generation Run

| Статус | Условие | Разрешённые переходы |
|---|---|---|
| `created` | Input/config проверены, контексты и план сохранены; оплаченных вызовов нет | `awaiting_authorization, blocked_config, cancelled` |
| `awaiting_authorization` | Нет действующего отдельного Search Planner consent | `queued, cancelled` |
| `queued` | Полная авторизация/верификация, хотя бы один pending batch | `running, paused_budget, paused_price, blocked_config, cancelled` |
| `running` | Идут/планируются batches; аттестован dispatch | `completed, paused_budget, paused_price, uncertain, failed, cancelled` |
| `paused_budget` | Недостаточно **разрешённого** оставшегося бюджета либо consent отозван | `queued` только при новой валидной authorisation; `cancelled` |
| `paused_price` | Snapshot цены/возможности модели устарел до следующей отправки | `queued` только после **новой quote+consent и нового run**; `cancelled` |
| `blocked_config` | Модель/контекст/схема несовместимы | `cancelled` или новая config+run; не менять уже pinned run |
| `uncertain` | Оплаченный вызов/результат неизвестен или потерян после dispatch intent | `running` только после документированного reconciliation; `failed, cancelled` после зафиксированного решения |
| `failed` | Известная ошибка, batch не завершён корректно | терминальный v1; исправление — отдельный run/вызов с новым бюджетом |
| `completed` | **Каждая** гипотеза имеет `generated≥1` или `no_viable_queries+reason`, нет unresolved attempts, валидный envelope/outbox сохранён | терминальный |
| `cancelled` | Не запускать новых попыток; already in-flight получают отдельный исход | терминальный после урегулирования ранее отправленного |

**Важная особенность:** при cancellation во время in-flight batch общий run может остаться `uncertain` с флагом `cancel_requested`; только после подтверждения или зафиксированного неизвестного расхода выставить итоговый `cancelled`. Не скрывать в UI неопределённую оплату.

### 6.2 Batch

| Статус | Значение |
|---|---|
| `pending` | Детерминированно создан, ничего не отправлено |
| `reserved` | Оплата зарезервирована, попытка создана; **dispatch_intent ещё НЕ установлен** |
| `dispatching` | В БД зафиксировано намерение отправки; внешний POST **мог произойти** |
| `completed` | Платная попытка завершилась с подтверждённым usage, сырой и проверенный JSON сохранился |
| `failed_known` | Подтверждённый ответ/расход, но JSON/контракт или известное исполнение не прошли |
| `uncertain` | Был dispatch intent, а результат/биллинг недоказуем |
| `cancelled` | Неотправленный пакет окончательно остановлен |

Если изменение цены/бюджета происходит между batches, pending batches остаются `pending`, а **RUN** получает `paused_budget` или `paused_price`; отдельный `paused` у каждого batch не нужен. Если метод multi-sample потребует >1 попытки, batch считается complete только после заранее указанного числа разрешённых samples и всех результатов; v1 `samples_per_batch=1`.

### 6.3 Provider Attempt

`reserved → dispatch_intent → completed`  
`reserved → abandoned_before_dispatch` (без POST, резерв освобождается документированной транзакцией).  
`dispatch_intent → failed_known` (биллинг определён).  
`dispatch_intent → uncertain` (неизвестен биллинг/результат).  
`uncertain → reconciled_completed / reconciled_failed` **только** с проверяемой provider evidence или явно зафиксированной ручной резолюцией (в последнем случае нельзя называть фактическую цену известной, если её не удалось установить).

Один и тот же `attempt_id` не выполняет два платных POST. Повторный эксперимент создаёт новый attempt (и, для v1 после failure, новый generation), если пользователь прямо разрешил отдельный расход.

### 6.4 Терминальные инварианты и сводные статусы

- `Generation completed` ⇒ все planned batches completed; число записей `coverage` = число source hypotheses; `candidates` соответствуют schema; сумма `candidate_count` совпадает; уникальные candidate IDs; нет unresolved reservation.
- `Generation failed/uncertain` ⇒ **НЕ** публиковать `query_candidates/v1` под видом completed; частичный результат только для диагностики.
- `Batch completed` ⇒ существует ровно один подтверждённый completed attempt в стандартном v1; `raw_result` сохранён и прошёл JSON Schema + cross-field checks.
- `Consent revoked` ⇒ блокируются **новые** отправки, но previously sent POST не может быть отозван гарантированно.
- Изменение любых approval/search policy/model/prompt/schema/source данных всегда означает новую immutable version; старые generation не подменяются.

## 7. PostgreSQL: логическая модель и точные связи

**Не создавать таблицы под именами `tb_*` RTB.** Для нового модуля один префикс `sqg_*` (*Search Query Generation*), чтобы было ясно, кому принадлежит жизненный цикл. Старые примеры `search_query_generations/search_query_batches` из предварительного плана — **концептуальные**, а не уже существующие таблицы; при реализации выбрать **одну** согласованную номенклатуру: ниже фиксируем `sqg_*`.

> Миграция должна быть аддитивной (например, следующий свободный `intelligence/migrations/015_search_query_generation.sql`; **перед написанием проверить, что номер свободен**). Существующий `intelligence/migrate.py` сортирует файлы лексикографически и запрещает изменение уже применённых миграций по SHA. Нет CREATE/DROP RTB таблиц. SQL ниже — **DDL-план**, а не применённая миграция; перед merge обязательно прогнать на disposable PostgreSQL 17.

### 7.1 Таблицы и реальные атрибуты

| Таблица | Первичный ключ / область проекта | Содержимое и инварианты |
|---|---|---|
| `sqg_config_versions` | `id UUID`; unique `(project_id,revision)` | `project_id`, `revision`, `config JSONB`, `config_sha256 CHAR(64)`, `model_catalog_snapshot_id`, `prompt_sha256`, `raw_schema_sha256`, `approved_at`, `created_at`; immutable после approval |
| `sqg_quotes` | `id UUID` | `project_id, publication_id, config_id, config_sha256, model_catalog_sha256, pricing_snapshot JSONB, batch_manifest_sha256, quote_low/expected/high_usd NUMERIC(18,8), expires_at, created_at`; quote привязана к batch плану |
| `sqg_consents` | `id UUID` | `project_id, quote_id, publication_id, authorized_usd, reserved_usd, spent_usd, authorized_at, expires_at, revoked_at`; aggregate authorization **только Search Planner** |
| `sqg_generations` | `id UUID`; unique `(project_id,idempotency_key)` | `project_id, rtb_run_id, publication_id, taxonomy_version, config_id, quote_id, consent_id, input_sha256, taxonomy_sha256, model_id, config_snapshot JSONB, status, cancel_requested, timestamps` |
| `sqg_contexts` | PK `(generation_id,hypothesis_id)` | полный `hypothesis_context JSONB`, `context_sha256`, `direction_id`, `branch_id NULL`, immutable; ID локален для publication |
| `sqg_batches` | `id UUID`; unique `(generation_id,batch_index)` | `generation_id, batch_index, direction_id, hypothesis_ids JSONB, input_sha256, output_token_cap, estimated_input_tokens, status, timestamps` |
| `sqg_attempts` | `id UUID`; unique `(batch_id,sample_index,attempt_no)` | `generation_id,batch_id,request_sha256,prompt_sha256,raw_schema_sha256,model_catalog_sha256,reservation_usd,charged_usd NULL,provider_response_id NULL,usage JSONB,raw_result JSONB,finish_reason,status,dispatch_intent_at,started_at,ended_at,error_code` |
| `sqg_candidates` | `id UUID` | `generation_id,batch_id,hypothesis_id,direction_id,branch_id NULL,source_fact_ids JSONB,text,angle,query_mode,language,research_stance,rationale,raw_position,created_at`; unique `(batch_id,raw_position)` |
| `sqg_hypothesis_coverage` | PK `(generation_id,hypothesis_id)` | `state = generated|no_viable_queries`, `candidate_count`, `reason nullable`, reference на `sqg_contexts` |
| `sqg_events` | `id BIGSERIAL` | immutable audit: `project_id,generation_id,batch_id?,attempt_id?,event_kind,payload JSONB,created_at`, payload ограничен allowlist и размером |
| `sqg_outbox` | `id UUID`; unique `(generation_id,event_type)` | событие `query_candidates_ready/v1`, `payload_pointer`, `delivered_at`, `delivery_attempts`, `next_delivery_at`; idempotent consumer |

**Итого: 11 таблиц.** Ранее обсуждавшиеся «9 сущностей» были обзором, здесь добавлены самостоятельные `quotes` и `outbox`, потому что без них финансовое согласие и доставка имеют слишком много неявных состояний. Это **разделение логики**, не требование создавать 11 ORM-классов на каждый маршрут.

### 7.2 Минимальный SQL-скелет и композитные FK

```sql
-- Ниже СХЕМА-ПРОЕКТ, не готовая миграция.
-- Доказать на CI: PostgreSQL 17, all referenced UNIQUE keys exist.

CREATE TABLE sqg_config_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id),
  revision INTEGER NOT NULL CHECK (revision > 0),
  config JSONB NOT NULL CHECK (jsonb_typeof(config) = 'object'),
  config_sha256 CHAR(64) NOT NULL,
  approved_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (project_id, revision),
  UNIQUE (id, project_id)
);

CREATE TABLE sqg_quotes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id),
  publication_id UUID NOT NULL REFERENCES tb_taxonomy_publications(id),
  config_id UUID NOT NULL,
  quote_low_usd NUMERIC(18,8) NOT NULL CHECK (quote_low_usd >= 0),
  quote_expected_usd NUMERIC(18,8) NOT NULL CHECK (quote_expected_usd >= 0),
  quote_high_usd NUMERIC(18,8) NOT NULL CHECK (quote_high_usd >= quote_expected_usd),
  immutable_snapshots JSONB NOT NULL,
  batch_manifest_sha256 CHAR(64) NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (config_id,project_id) REFERENCES sqg_config_versions(id,project_id),
  UNIQUE (id,project_id)
);

CREATE TABLE sqg_consents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id),
  quote_id UUID NOT NULL,
  publication_id UUID NOT NULL REFERENCES tb_taxonomy_publications(id),
  authorized_usd NUMERIC(18,8) NOT NULL CHECK (authorized_usd > 0),
  spent_usd NUMERIC(18,8) NOT NULL DEFAULT 0 CHECK (spent_usd >= 0),
  reserved_usd NUMERIC(18,8) NOT NULL DEFAULT 0 CHECK (reserved_usd >= 0),
  authorized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  revoked_at TIMESTAMPTZ,
  FOREIGN KEY (quote_id,project_id) REFERENCES sqg_quotes(id,project_id),
  UNIQUE (id,project_id)
);

CREATE TABLE sqg_generations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id),
  rtb_run_id UUID NOT NULL REFERENCES tb_runs(id),
  publication_id UUID NOT NULL REFERENCES tb_taxonomy_publications(id),
  taxonomy_version INTEGER NOT NULL CHECK (taxonomy_version > 0),
  config_id UUID NOT NULL,
  quote_id UUID,
  consent_id UUID,
  idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 12 AND 128),
  input_sha256 CHAR(64) NOT NULL,
  taxonomy_sha256 CHAR(64) NOT NULL,
  model_id TEXT NOT NULL,
  config_snapshot JSONB NOT NULL,
  status TEXT NOT NULL,
  cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  FOREIGN KEY (config_id,project_id) REFERENCES sqg_config_versions(id,project_id),
  FOREIGN KEY (quote_id,project_id) REFERENCES sqg_quotes(id,project_id),
  FOREIGN KEY (consent_id,project_id) REFERENCES sqg_consents(id,project_id),
  UNIQUE (project_id,idempotency_key),
  UNIQUE (id,project_id)
);

CREATE TABLE sqg_contexts (
  generation_id UUID NOT NULL REFERENCES sqg_generations(id),
  hypothesis_id TEXT NOT NULL,
  direction_id TEXT NOT NULL,
  branch_id TEXT,
  context JSONB NOT NULL CHECK (jsonb_typeof(context)='object'),
  context_sha256 CHAR(64) NOT NULL,
  PRIMARY KEY (generation_id,hypothesis_id)
);

CREATE TABLE sqg_batches (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  generation_id UUID NOT NULL REFERENCES sqg_generations(id),
  batch_index INTEGER NOT NULL CHECK (batch_index >= 0),
  direction_id TEXT NOT NULL,
  hypothesis_ids JSONB NOT NULL CHECK (jsonb_typeof(hypothesis_ids)='array'),
  input_sha256 CHAR(64) NOT NULL,
  estimated_input_tokens INTEGER NOT NULL CHECK (estimated_input_tokens >= 0),
  output_token_cap INTEGER NOT NULL CHECK (output_token_cap > 0),
  status TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (generation_id,batch_index),
  UNIQUE (id,generation_id)
);

CREATE TABLE sqg_attempts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  generation_id UUID NOT NULL REFERENCES sqg_generations(id),
  batch_id UUID NOT NULL,
  sample_index SMALLINT NOT NULL DEFAULT 0,
  attempt_no SMALLINT NOT NULL DEFAULT 1,
  request_sha256 CHAR(64) NOT NULL,
  prompt_sha256 CHAR(64) NOT NULL,
  raw_schema_sha256 CHAR(64) NOT NULL,
  model_catalog_sha256 CHAR(64) NOT NULL,
  status TEXT NOT NULL,
  reservation_usd NUMERIC(18,8) NOT NULL CHECK (reservation_usd > 0),
  charged_usd NUMERIC(18,8) CHECK (charged_usd >= 0),
  provider_response_id TEXT,
  usage JSONB,
  raw_result JSONB,
  finish_reason TEXT,
  dispatch_intent_at TIMESTAMPTZ,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at TIMESTAMPTZ,
  error_code TEXT,
  FOREIGN KEY (batch_id,generation_id) REFERENCES sqg_batches(id,generation_id),
  UNIQUE (batch_id,sample_index,attempt_no)
);

CREATE TABLE sqg_candidates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  generation_id UUID NOT NULL REFERENCES sqg_generations(id),
  batch_id UUID NOT NULL,
  hypothesis_id TEXT NOT NULL,
  direction_id TEXT NOT NULL,
  branch_id TEXT,
  source_fact_ids JSONB NOT NULL CHECK (jsonb_typeof(source_fact_ids)='array'),
  text TEXT NOT NULL CHECK (length(trim(text)) BETWEEN 1 AND 120),
  query_mode TEXT NOT NULL CHECK (query_mode IN ('keyword','hashtag')),
  angle TEXT NOT NULL CHECK (angle IN
    ('direct','problem','solution','situation','format','comparison',
     'experience','critical','other')),
  language TEXT NOT NULL,
  research_stance TEXT NOT NULL CHECK (research_stance IN ('support','challenge','neutral')),
  rationale TEXT NOT NULL CHECK (length(trim(rationale)) BETWEEN 1 AND 280),
  raw_position INTEGER NOT NULL CHECK (raw_position >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (batch_id,generation_id) REFERENCES sqg_batches(id,generation_id),
  FOREIGN KEY (generation_id,hypothesis_id)
    REFERENCES sqg_contexts(generation_id,hypothesis_id),
  UNIQUE (batch_id,raw_position)
);

CREATE TABLE sqg_hypothesis_coverage (
  generation_id UUID NOT NULL,
  hypothesis_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('generated','no_viable_queries')),
  candidate_count INTEGER NOT NULL CHECK (candidate_count BETWEEN 0 AND 16),
  reason TEXT,
  PRIMARY KEY (generation_id,hypothesis_id),
  FOREIGN KEY (generation_id,hypothesis_id)
    REFERENCES sqg_contexts(generation_id,hypothesis_id),
  CHECK ((state='generated' AND candidate_count>0 AND reason IS NULL)
    OR (state='no_viable_queries' AND candidate_count=0 AND
        length(trim(coalesce(reason,'')))>0))
);

CREATE TABLE sqg_events (
  id BIGSERIAL PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project_registry(id),
  generation_id UUID NOT NULL REFERENCES sqg_generations(id),
  batch_id UUID,
  attempt_id UUID,
  event_kind TEXT NOT NULL,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sqg_outbox (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  generation_id UUID NOT NULL REFERENCES sqg_generations(id),
  event_type TEXT NOT NULL CHECK (event_type='query_candidates_ready/v1'),
  payload_pointer JSONB NOT NULL,
  delivered_at TIMESTAMPTZ,
  delivery_attempts INTEGER NOT NULL DEFAULT 0 CHECK (delivery_attempts>=0),
  next_delivery_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (generation_id,event_type)
);
```

**Важный SQL-review перед миграцией:** этот скелет описывает структуру, но НЕ все constraints. Добавить `CHECK` по статусам и SHA regex `^[a-f0-9]{64}$`, размеры JSON, `UNIQUE` и scope guards, триггеры иммутабельности для `contexts`, approved configs, quotes, completed attempts/candidates; запрет `DELETE` audit. FK на `tb_taxonomy_publications(id)` **сам по себе** НЕ доказывает совпадение `project_id` — проверять через SQL JOIN и/или trigger, не считать ссылку на UUID защитой от смешения проектов. Создать индексы `sqg_generations(project_id,created_at DESC)`, `sqg_batches(generation_id,status,batch_index)`, `sqg_attempts(batch_id,status)`, `sqg_outbox(delivered_at,next_delivery_at)`. Таблицы попыток не должны содержать API key или auth headers.

### 7.3 Атомарность

Сохранение generation + contexts + batches + initial events = **одна transaction**. Резерв budget + создание attempt + update batch = **одна transaction под lock consent**. Закрытие attempt + начисление стоимости/снятие резерва + запись trace/candidates/coverage + обновление batch = **одна transaction**. Завершение generation + проверка полного покрытия + `sqg_outbox` запись = **одна transaction**. Нельзя сначала показать `completed` в UI, а outbox добавить позже: падение между действиями оставит «вечный» недоставленный результат.

### 7.4 Сохранение JSON и денежная точность

- JSONB исторического контекста содержит пользовательскую анкету. Доступ только в пределах проекта/локального администратора; обычные логи, метрики, диагностические ошибки не содержат всю анкету.
- `raw_result JSONB` может быть большим; enforce max accepted provider response bytes (ориентир 2 MB, по примеру существующего RTB provider) и sane max token/output counts. Не усекать успешный ответ.
- Деньги: `NUMERIC(18,8)` + `Decimal`; округление **вверх** при reservation до DB precision. Нельзя делать расчёт через JS float для расходных ограничений.
- Хранение по source policy; будущие сроки хранения и удаление проекта должны явно удалять либо анонимизировать связанные записи согласованно, не разрывая юридически необходимый audit. Процедуру удаления согласовать до запуска, не удалять «старые» попытки автоматически.


---

## 8. Financial Authorization, Quote и Budget Ledger

### 8.1 Порядок получения разрешения

`draft config → validate/approve config → build context/batches → estimate quote → пользователь явно разрешает budget → generation start → per-batch reserve/dispatch → final settlement`.

**Нельзя** считать RTB spending consent согласием для AI Query. Новый consent содержит точные `project_id, publication_id, config_id, quote_id, model snapshot, authorized_usd, expires_at`. Для v1 принять **один consent на один Generation Run**, не давать одной квоте незаметно финансировать несколько независимых запусков. В миграции добавить `UNIQUE(consent_id) WHERE consent_id IS NOT NULL` у `sqg_generations` (PostgreSQL partial unique index).

UI показывает **high quote**, валюту USD, версию модели, приблизительный максимум входа/выхода, количество batches и пометку о том, что estimate не гарантирует фактический invoice. Предлагаемый TTL quote: 30 минут по аналогии с существующей RTB; конкретное значение фиксируется конфигурацией, а не хардкодится в браузере. Expired/revoked consent ⇒ новых отправок нет.

### 8.2 Формулы

```text
authorized_usd = user-approved maximum (NUMERIC/Decimal)

available_for_consent =
    authorized_usd - spent_usd - reserved_usd

required_reservation(batch) =
    CEIL_DB_PRECISION(
      upper_input_tokens * pinned_input_price
      + (max_output_tokens + explicit_reasoning_budget_if_separately_priced)
        * pinned_completion_price
      + fixed_request_fee
      + configured_safety_margin
    )

Can dispatch:
    consent.active AND now < consent.expires_at
    AND model_quote_not_stale
    AND required_reservation > 0
    AND required_reservation <= available_for_consent
    AND batch.status = 'pending'
    AND generation.status in ('queued','running')
```

Правила `reasoning` зависят от OpenRouter snapshot, нельзя бездумно сложить цену reasoning с ценой completion, если provider уже включает reasoning токены в billed output. **Источник истины:** подтверждённый `usage.cost` от провайдера; тарифные расчёты — консервативный reserve, не выписка. В сравнениях использовать decimal string, не `float`. `spent_usd` может превысить approved лимит вследствие реального overcharge — **сохранить факт**, отметить `over_budget`, остановить новые отправки; не отклонять фактическую цену CHECK `spent <= authorized`.

**Важно о параллельности:** если один consent может покрывать несколько batches, `SELECT ... FROM sqg_consents WHERE id=? AND project_id=? FOR UPDATE` сериализует резервы/settlement. Даже если default concurrency=1, архитектура обязана выдерживать два запущенных процесса. Учитываем `reserved_usd` всех unresolved attempts до расчёта остатка.

### 8.3 Пример расхода, в котором не теряются деньги

```text
Consent: authorized = 2.00 USD
Before: spent = 0.60; reserved = 0.00; available = 1.40
Batch #5 reservation = 0.08
After reserve: spent = 0.60; reserved = 0.08; available = 1.32
OpenRouter returns confirmed actual charged = 0.05
After settlement: spent = 0.65; reserved = 0.00; available = 1.35
```

Если provider ответ не получен и мог выставить счёт: `spent=0.60, reserved=0.08` до reconciliation; NOT «автоматически возвращаем всё через 60 секунд». Если provider прислал действительную цену 0.10, учесть `spent += 0.10`, а не ограничивать запись величиной резерва.

### 8.4 Price / capability check

Перед **каждым** впервые оплачиваемым POST:
1. Проверить pinned model ID, catalog snapshot SHA, тарифы, freshness; новые параметры не пробрасывать автоматически.
2. Если OpenRouter catalog говорит «изменилось» → `paused_price`, запрос не отправлен; новый quote+consent/new run. Нельзя пытаться автоматически дешевле/дороже другую модель.
3. Если нет свежих метаданных → блокировать paid dispatch; не делать догадки о реальной поддержке `json_schema`.
4. Не использовать дефолтный provider fallback: при реальной интеграции задать `require_parameters=true`, `allow_fallbacks=false` (если API модели поддерживает такой контракт); каждая альтернатива требует нового согласования.
5. `response_format` структурированный; если provider отверг его — известная ошибка может уже сопровождаться биллингом, сохраняем audit и не делаем бессчётных retries.

## 9. Execution Manager: транзакционный протокол и очередь

### 9.1 Планировщик и worker

Для v1 подходит **PostgreSQL-backed queue**, без обязательных Celery/Redis. Но это не означает «случайный GET запускает job». Только выделенный worker/process либо разрешённый authenticated explicit Start обрабатывает команды; `GET /status`, `GET /batches`, `GET /results` — **read-only**. На Windows приложение должно корректно учитывать auto-update/restart: длительный провайдерный запрос не допускает тихого повторного отправления при перезапуске.

**Базовая worker policy:** single paid concurrency per project/generation, global limit 1 по умолчанию, очередность по `batch_index`, fair scheduling между проектами позже. Даже если несколько workers работают одновременно, уникальные row locks и CHECK блокируют дубли. В текущем v1 НЕТ автоматической бесконечной многократной рассылки задач.

### 9.2 Claim+Reserve (транзакция T1)

```sql
BEGIN;
-- В реальном коде порядок захвата: generation → consent → batch → attempt.
SELECT * FROM sqg_generations
 WHERE id = $generation_id AND project_id = $project_id
 FOR UPDATE;
SELECT * FROM sqg_consents
 WHERE id = $consent_id AND project_id = $project_id
 FOR UPDATE;
SELECT id FROM sqg_batches
 WHERE generation_id = $generation_id AND status = 'pending'
 ORDER BY batch_index
 FOR UPDATE SKIP LOCKED
 LIMIT 1;

-- Server verifies scope, cancellations, quote, rights, price, policy,
-- immutable hashes and available budget.
-- INSERT exactly one attempt reservation for this batch/sample.
-- Increment consent.reserved_usd atomically.
-- Set batch.status='reserved'; generation.status='running'.
-- Insert sqg_events(kind='attempt_reserved').
COMMIT;
```

Считать quote conservative ДО T1 можно только при сохранённом pinned request digest. Перед commit проверять, что request hash/цена не поменялись. Параллельные worker claims должны опираться на `UNIQUE(batch_id,sample_index,attempt_no)`, а не только на оптимистическую UI-проверку.

### 9.3 Dispatch Intent (транзакция T2)

```text
T2 BEGIN
 lock attempt (id + project scope)
 ensure attempt.status='reserved', no dispatch_intent_at
 commit attempt.status='dispatch_intent', dispatch_intent_at=now()
 commit T2
...
ONLY NOW perform one outbound paid HTTP POST
```

**Критическая граница:** после фиксации `dispatch_intent` Engine **не имеет права автоматически решать, что запрос не был отправлен**, даже если worker исчез до фактического `POST`. Это сознательный tradeoff: at-most-one automatic dispatch важнее случайного повторного биллинга.

Если crash произошёл **до** T2, попытка `reserved` и есть доказательство «намерение не публиковалось»; с соответствующим lock можно освободить резерв или безопасно продолжить **ту же** попытку. Если crash после T2, считать исход неизвестным и переходить к reconciliation.

### 9.4 Provider Call (вне транзакции)

```python
def execute_dispatch(attempt, transport):
    # No DB transaction held while waiting for OpenRouter.
    # attempt already in durable dispatch_intent state.
    try:
        reply = transport.invoke_once(
            model_id=attempt.model_id,
            payload=attempt.pinned_request,
            timeout_seconds=attempt.timeout_seconds,
        )
    except PossiblyBilled:
        return UnknownOutcome(reason="provider_or_transport_uncertain")
    return ProviderOutcome.parse(reply)
```

В v1 `transport` fake-provider. В v2 он будет отдельным OpenRouter adapter; ключ только серверный, запрос/ответ с верхней границей размера, `max_output_tokens` и строгим parser. Никаких автоматических HTTP retries в urllib/httpx, включая скрытые retries клиента.

### 9.5 Settlement (транзакция T3)

Если provider ответил с **доказанной стоимостью**:
1. Проверить, что `attempt.status = dispatch_intent` и связанные `batch + generation + project` совпадают.
2. Записать `provider_response_id`, оригинальный sanitized raw JSON, `usage`, `finish_reason`, `charged_usd`.
3. Отнять старый резерв именно этого attempt из `sqg_consents.reserved_usd`, увеличить `spent_usd` на **доказанный реальный charge**.
4. Применить JSON Schema raw response и backend cross-check: все переданные hypothesis ID ровно по одному разу, `facet_key` существует, `query.angle` согласован с facet, язык и `query_mode` разрешены; число кандидатов не превышает ограничения; `no_viable_reason` корректен.
5. При корректном ответе записать server-issued candidates и coverage, `batch.status='completed'`.
6. При некорректном ответе `batch.status='failed_known'`, деньги всё равно учтены, никаких fabricated query candidates/handoff.
7. Вставить audit event; commit.
8. При реальном overcharge принять стоимость в ledger, поставить `paused_budget` и блокировать новые отправки.

Если результат **не подтверждён**: `attempt.status='uncertain'`, `batch.status='uncertain'`, `generation.status='uncertain'`, `reserved_usd` остаётся (измеряется оставшаяся возможность обязательств), запись `reason/observed_at` в events. Последующий GET не должен выполнять paid retry.

### 9.6 Псевдокод верхнего уровня

```python
def process_one_batch(project_id: str, generation_id: str, *, transport) -> dict:
    job = repo.load_run_scoped(project_id, generation_id)
    if job.cancel_requested:
        return {"status": "cancelled_or_cancelling", "paid_calls": 0}

    # Atomically claims batch AND reserves consent balance.
    claim = repo.claim_and_reserve_next_batch(
        project_id=project_id,
        generation_id=generation_id,
        verify_current_model_and_quote=True,
    )
    if claim is None:
        return repo.summarize_status(project_id, generation_id)

    # Atomic durable marker BEFORE external call.
    repo.mark_dispatch_intent_once(project_id, claim.attempt_id)

    try:
        response = transport.invoke_once(claim.pinned_request)
    except PossiblyBilled:
        repo.mark_uncertain_keep_reserved(project_id, claim.attempt_id)
        return {"status": "uncertain", "paid_calls": 1}

    repo.settle_and_validate(project_id, claim.attempt_id, response)
    repo.finalize_if_full_coverage(project_id, generation_id)
    return repo.summarize_status(project_id, generation_id)
```

Этот псевдокод **не работающий Python**, он фиксирует порядок side effects. `mark_dispatch_intent_once` и `claim_and_reserve_next_batch` должны иметь транзакционные ограничения и idempotent response. Успех `mark_dispatch_intent_once` ещё не означает, что реально прошёл POST: именно поэтому нужна обработка `uncertain`.

## 10. Recovery Manager / Cancellation / Reconciliation

### 10.1 Матрица сбоев

| Где сбой | Что точно известно | Состояние после recovery | Можно ли повторять? |
|---|---|---|---|
| До создания `sqg_attempts` | POST не был разрешён | `pending` | worker может заново claim без оплаты |
| После reserve, **до** `dispatch_intent` | по нашему протоколу POST ещё не мог начаться | `reserved` или откат/освобождение | безопасно продолжить ту же attempt под lock |
| После `dispatch_intent`, до/во время POST | невозможно доказать факт отправки | `uncertain` | **НЕТ** автоматического paid retry |
| После provider HTTP 200, до commit settlement | ответ мог быть потерян в памяти процесса, биллинг мог быть | `uncertain` | только reconcile, не новый POST |
| Получен JSON, но `finish_reason != stop` | стоимость известна, результат неполный | `failed_known` (с оплатой) | новый run/явно разрешённый расход |
| Получен schema-invalid JSON с usage | стоимость известна, контракт нарушен | `failed_known` | новый run/явно разрешённый расход |
| Нет/неконсистентное usage | неизвестна фактическая стоимость | `uncertain` | сохранить reserve |
| Потеря БД во время network call | нельзя commit result | `uncertain` после восстановления | без paid retry |
| Quote/model changed до dispatch | POST не отправлен | `paused_price` | новый quote+consent/new run |
| Consent revoked до отправки | POST не отправлен | `paused_budget` или `cancelled` | платные вызовы запрещены |
| Consent revoked во время in-flight | отправка уже могла произойти | ждём исход, новые batches не отправлять | не отменяет отправленный POST |
| Дублирующий API `start` | существующий Run/Attempts известны | те же IDs | 0 дополнительных POST |

### 10.2 Периодический reconciliation

Сканировать `dispatch_intent` старше заданного `stuck_after` (например, provider timeout + допустимый запас; значение настраиваемое). Проверять **статус/счёт уже существующего provider response ID**, если это поддерживает конкретная разрешённая операция OpenRouter. **Не предполагать**, что любой потерянный запрос можно найти в OpenRouter по request SHA.

- Есть проверяемый `response_id` + достоверные `usage/cost/result` → settle в DB, сохранить evidence и timestamp.
- Получено достоверное подтверждение **невыполнения без выставления счёта** → разрешить документированный возврат резерва и статус `failed_known/abandoned`, **не автоматически пересылать POST**.
- Доказательств нет → продолжать `uncertain` и резерв; объяснить пользователю причину.
- Manual action: записать `resolution_code`, `resolved_by`, `reason`, доступные доказательства. Никогда не подменять unknown invoice фиктивным `charged_usd=0`.
- Нет фонового «retry until green» по error/timeout; финансовые ограничения выполняются даже при нескольких запущенных workers.

### 10.3 Cancel semantics

`POST /cancel` атомарно ставит `cancel_requested=true` и запрещает новые claims. Для `pending` batch выставляет `cancelled`. Для `dispatching` batch ожидает результат/uncertain; отмена внешнего запроса **не гарантирована**. Если in-flight response поступит после отмены, фактический charge и raw result записать; **не** публиковать финальный `query_candidates/v1` без политики полной завершённости. Повторная отмена возвращает тот же результат (idempotency).

### 10.4 Восстановление из PostgreSQL — не из браузера

Ни один restart, обновление страницы, повторное открытие сайта или worker wakeup не должен менять денежный статус без транзакционной причины. Браузер читает только status/read API. API `POST /start` запускает уже разрешённую работу и сам идемпотентен. Worker может запускаться как отдельный background service, но обязательно с DB checkpoints; Windows auto-updater не должен прерывать отправку «без следа».

## 11. LLM Transport Port и граница AI Query

### 11.1 Provider Port

```python
@dataclass(frozen=True)
class ProviderRequest:
    model_id: str
    request_sha256: str
    prompt_sha256: str
    schema_sha256: str
    messages: tuple[dict, ...]
    max_output_tokens: int
    temperature: float | None
    reasoning: dict | None

@dataclass(frozen=True)
class ProviderResponse:
    provider_response_id: str | None
    model_id: str
    finish_reason: str
    raw_json: dict
    usage: dict
    charged_usd: Decimal | None

class ProviderPort(Protocol):
    def invoke_once(self, request: ProviderRequest) -> ProviderResponse: ...
```

Подлинный `ProviderPort` не появится до третьего этапа AI Query; на втором используем `FakeProviderPort` с фиксированными paid/unknown/invalid responses. Adapter не вызывает search providers. Существующий RTB `provider.py` — полезный референс, но повторно использовать `request_payload()` с roles RTB запрещено.

### 11.2 LLM raw vs assembled output

Raw schema: [`SEARCH_PLANNER_AI_QUERY_RAW_LLM_V1.schema.json`](SEARCH_PLANNER_AI_QUERY_RAW_LLM_V1.schema.json):

```text
hypotheses [
  { hypothesis_id,
    semantic_profile,
    facets[ {facet_key, angle, intent, anchor_terms, sketches, lexical_hints} ],
    queries[ {facet_key, text, query_mode, angle, language, research_stance, rationale} ],
    no_viable_reason }
]
```

`facets/sketches/lexical_hints` — **модельная диагностика**, а не реальный контент. Они не являются отдельными вызовами: умственная последовательность ThinkQE → MuGI → Jina организована одним structured completion на batch. Позднее A/B может разрешить multi-sample, но тогда отдельный consent/budget reservation.

Output schema: [`SEARCH_PLANNER_AI_QUERY_CANDIDATES_V1.schema.json`](SEARCH_PLANNER_AI_QUERY_CANDIDATES_V1.schema.json). Сервер заполняет `generation_id`, `candidate_id`, `hypothesis_id`, `direction_id`, `branch_id`, `source_fact_ids`, версии RTB и immutable input SHA. Не доверять моделям проектный ID, claim популярности или fact IDs. Контракт не содержит ни URL видео, ни провайдерных заданий.

### 11.3 Как выглядит готовая передача

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
      "rationale": "Ищем короткие домашние занятия"
    }
  ],
  "hypothesis_coverage": [
    {
      "hypothesis_id": "hyp_short",
      "state": "generated",
      "candidate_count": 1,
      "reason": null
    }
  ]
}
```

Это **синтетический один-hypothesis пример**, соответствующий сокращённому выводу; фактически `status=completed` требует coverage всех гипотез данной публикации. Набор кандидатов не обязан содержать точно 10 строк/гипотезу.

### 11.4 Handoff через transactional outbox

В **той же DB transaction**, где `generation → completed`, создать запись `sqg_outbox(event_type='query_candidates_ready/v1', generation_id, payload_pointer)` с уникальным ключом на одну generation. Отдельный dispatcher передаёт/делает доступным событие Query Validation; при сбое ретраить **только доставку указателя** (бесплатная операция), не исходный OpenRouter POST. Consumer обязан дедуплицировать событие по `(generation_id,event_type)`. При ошибке Query Validation исходный Generation Run не отменяется и не перегенерируется: это новый workflow со своим status/history.


---

## 12. HTTP API: детальные контракты и безопасность

**API здесь проектируем, оно НЕ работает сейчас.** Routes будут встроены в существующий ContentMachine localhost сервер, с проверкой project scope/авторизованного владельца по **текущей** модели приложения. Дополнительная публичная облачная авторизация, если продукт станет multi-user, потребует отдельного security design; localhost **не** значит «любой POST на localhost безопасен».

| Метод | Предлагаемый маршрут | Side effect | Ответ |
|---|---|---|---|
| `GET` | `/api/search-planner/config?project_id=…` | нет | текущая конфигурация, approved revision, модель |
| `PUT` | `/api/search-planner/config` | новая draft/revision | config + validation feedback |
| `POST` | `/api/search-planner/config/approve` | immutable config approval | `config_id/revision/hash` |
| `POST` | `/api/search-planner/quotes` | вычисляет/сохраняет bounded quote | `quote_id,low/expected/high_usd,expires_at,model` |
| `POST` | `/api/search-planner/authorizations` | **явно** разрешает сумму | `consent_id,authorized_usd,expires_at` |
| `POST` | `/api/search-planner/generations` | создаёт immutable contexts/batches | `generation_id,status,batches_count,input_sha256` |
| `POST` | `/api/search-planner/generations/{id}/start` | explicit enqueue/dispatch **в рамках consent** | run status, accepted/paused |
| `GET` | `/api/search-planner/generations/{id}` | нет | status, cost, counters, reason |
| `GET` | `/api/search-planner/generations/{id}/batches` | нет | порядок, попытки, статусы |
| `GET` | `/api/search-planner/generations/{id}/results` | нет | completed candidate bundle или status failure |
| `GET` | `/api/search-planner/generations/{id}/events` | нет | bounded page событий без секретов |
| `POST` | `/api/search-planner/generations/{id}/cancel` | cancel_requested, остановка новых dispatch | status |
| `POST` | `/api/search-planner/generations/{id}/reconcile` | **только отдельная разрешённая** сверка known response | outcome/evidence |
| `GET` | `/api/search-planner/health` | нет | статус worker/DB/recovery без чувствительных данных |

**Не добавляем в v1** произвольный маршрут `POST /provider/retry`, запускающий оплачиваемый запрос без новой quote+consent: это обход budget guard. Можно добавить ручную repair операцию, но **только** как explicit new authorized generation.

### 12.1 Примеры API

```http
POST /api/search-planner/quotes
Content-Type: application/json

{
  "project_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "taxonomy_publication_id": "22222222-2222-4222-8222-222222222222",
  "approved_config_id": "66666666-6666-4666-8666-666666666666"
}
```

```json
{
  "quote_id": "77777777-7777-4777-8777-777777777777",
  "project_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "estimated_batch_count": 6,
  "hypothesis_count": 24,
  "expected_usd": "0.68",
  "high_usd": "1.05",
  "expires_at": "2026-10-23T14:00:00+02:00",
  "currency": "USD",
  "warnings": ["Estimated reservation is not a guaranteed provider invoice."]
}
```

Значения/время **условные**, quote не является актуальной ценой OpenRouter.

```http
POST /api/search-planner/authorizations
Content-Type: application/json

{
  "project_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "quote_id": "77777777-7777-4777-8777-777777777777",
  "authorized_usd": "1.20",
  "approve_exact_model_and_scope": true
}
```

```http
POST /api/search-planner/generations
Idempotency-Key: approved-search-generation-20261023-001
Content-Type: application/json

{
  "project_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "quote_id": "77777777-7777-4777-8777-777777777777",
  "consent_id": "88888888-8888-4888-8888-888888888888"
}
```

```json
{
  "generation_id": "33333333-3333-4333-8333-333333333333",
  "status": "queued",
  "hypothesis_count": 24,
  "batch_count": 6,
  "paid_calls": 0,
  "spent_usd": "0.00000000",
  "reserved_usd": "0.00000000"
}
```

```http
GET /api/search-planner/generations/33333333-3333-4333-8333-333333333333?project_id=aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
```

```json
{
  "generation_id": "33333333-3333-4333-8333-333333333333",
  "project_id": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "status": "running",
  "total_hypotheses": 24,
  "covered_hypotheses": 16,
  "total_batches": 6,
  "completed_batches": 4,
  "active_batches": 1,
  "spent_usd": "0.60000000",
  "reserved_usd": "0.08000000",
  "last_error_code": null
}
```

Если `GET /results` запросили до `completed`, отвечать структурированным `409 generation_not_complete` (или `202` с чистым status, выбрать один контракт в API tests), а не отдавать partial candidate bundle с `status=completed`. Выбранное для спецификации правило: **409** с `{"error":{"code":"generation_not_complete","generation_status":"running"}}`.

### 12.2 API invariants

- В каждом handler проверять `project_id` по данным authenticated project context; UUID в path не предоставляет право доступа.
- `Idempotency-Key` нужен на create quote/consent/generation/start/cancel/reconcile для мутаций, которым он полезен; repeated same key+same body→same resource. Same key+different body→`409 idempotency_conflict`.
- POST `start` лишь авторизует переход и enqueues; реальный paid POST делает только worker после ledger commit. Не блокировать HTTP request на 5-минутный OpenRouter вызов.
- `GET` никогда не читает stored OPENROUTER_API_KEY и не инициирует paid calls. Raw prompt и факты обычно недоступны без специального owner diagnostic permission.
- `result` получает `query_candidates/v1` **только** для `completed`; cursor/pagination для диагностики и batch list.
- Отмена/ревокация не сбрасывают уже выставленные charge.
- HTTP ошибки: `400` malformed ID/body; `403` project owner denial; `404` no scoped resource (также для чужого); `409` stale/invalid state/duplicate mismatch; `422` domain invalid config; `503` database/catalog/provider unavailable. Экспонировать стабильный machine `error.code`, не SQL/stack/secret.

## 13. UI — пользовательский сценарий и визуальная обратная связь

**UI интегрируется в существующий Search Planner**, не создаёт отдельное desktop-приложение.

1. **Settings** — выбранная утверждённая RTB, language policy, модель из проверенного OpenRouter catalog, strategy/prompt version (обычно read-only), target/max candidates, batch size, concurrency. Валидация поддерживаемых параметров до save approval.
2. **Estimate & Authorization** — count hypotheses / batches, pinned model, estimated token use, low/expected/high USD, quote expiry; явный ввод max USD и подтверждение **именно Search Planner**.
3. **Generation History** — таблица immutable запусков `created / awaiting_authorization / queued / running / paused...`, версии Taxonomy, model, snapshot SHA.
4. **Generation Detail** — status, completed batches / total, covered hypotheses / total, actual+reserved USD, error codes, explanation of uncertain.
5. **Batch Diagnostics** — для каждого batch: scope hypotheses, counts, attempts, safe model/version/usage. Approved facts/prompts показывать только владельцу; raw provider JSON не превращать в HTML без escaping.
6. **Results** — после `completed` экспорт `query_candidates/v1`, сгруппировать по hypothesis/angle/language. **Не** показывать как результаты TikTok/валидные запросы (они пока лишь кандидаты).
7. **Cancel** — явно объяснять, что запрещает новые отправки, но уже выполненные операции могут стоить денег.
8. **Reconcile** — отдельная диагностическая операция с доступом только владельцу. Никакой кнопки «повторить сразу» для `uncertain`.
9. **Outbox/Handoff** — отображать отдельно состояние передачи в Query Validation; ошибка consumer не делает Generation Run неуспешным задним числом.

**Достоверность прогресса:** `completed_batches/total_batches` — реальный счётчик в БД; `0.67` не доказывает 67% расходов или времени. Если 4 из 6 пакетов completed, UI сообщает «4/6», а не «67% общей сложности». `covered_hypotheses` отражает только persisted coverage, не обещает релевантные видео.

### Пример состояния

```text
Project: Fitness App     Taxonomy: publication UUID / version 3
Generation: 333...333    Model: pinned config
Status: running
Hypotheses: 24    Batches: completed 4 / 6
Actual charge: 0.60000000 USD
Reserved/uncertain exposure: 0.08000000 USD
Next: Batch 5 — dispatching, Batch 6 — pending
[Read-only batch details] [Request cancellation]
```

## 14. Monitoring / Observability

### 14.1 Метрики

| Метрика | Источник | Комментарий |
|---|---|---|
| `generation_runs_total_by_status` | generations | Состояния по версиям |
| `batches_total_by_status` | batches | Не врать о completed |
| `attempts_dispatched_total` | attempts dispatch intent | Маркер отправки, не proof реального POST |
| `attempts_uncertain_total` | attempts | Ключевая safety-метрика |
| `duplicate_dispatch_prevented_total` | unique/locks | Количество блокированных попыток повторного dispatch |
| `spent_usd, reserved_usd, available_usd` | consent ledger | Decimal string, одинаковая дата snapshot |
| `cost_per_completed_batch` | attempts/coverage | cost divided by completed batches |
| `model_context_rejections_total` | batcher | Указание на необходимость smaller batch/model |
| `schema_failure_total` | raw validator | Разделять malformed JSON и model response incomplete |
| `hypothesis_coverage_ratio` | coverage count/source count | Каждая гипотеза учтена, не равно recall |
| `handoff_outbox_pending_total` | outbox | Query Validation dependency |
| `worker_claim_latency` | batch queue | отдельно для Windows/local app |

### 14.2 Журнал событий

События: `generation_created, config_pinned, quote_created, consent_authorized, batch_planned, attempt_reserved, dispatch_intent_recorded, provider_completed, provider_unknown, charged, schema_rejected, batch_completed, generation_completed, handoff_queued, handoff_delivered, budget_paused, price_paused, consent_revoked, cancel_requested, reconciliation_recorded`.

**Уровни лога:** audit DB события без secrets + агрегированные операционные метрики. Не писать полный user questionnaire, raw tokens, OpenRouter Authorization header, API key, creator profile/video payload в stdout/CI/GitHub Actions. OpenRouter usage и billed USD можно записывать в защищённые DB-записи. Trace `sketches/facets` относится к AI Query diagnostics, а не к реальным видео.

### 14.3 Минимальный health/status

`health` отвечает: доступность DB, миграции, worker heartbeat возраст, число stuck attempts, число pending outbox events. Не должен автоматически ремонтировать оплачиваемые операции или раскрывать DSN.

## 15. Security, prompt injection, privacy, compliance

1. **Project isolation:** все SQL operations и HTTP reads строго по `(project_id,generation_id)`; ссылки UUID на чужой project не раскрывать даже через count/errors.
2. **Approved provenance:** только immutable RTB и human-approved facts, no draft/unapproved; fact text внутри prompt рассматривается как **untrusted data**, инструкции в нём игнорируются моделью согласно system prompt, backend защищает IDs/usage/schema независимо от послушности модели.
3. **Server-only keys:** OpenRouter key только из существующей приватной конфигурации, NEVER request body/history/billing payload/client/browser.
4. **External content:** Engine не отправляет пользовательские вложения; `HypothesisContext` allowlists profile fields и не включает локальные пути/содержимое файлов. Если будут изменения policy — отдельная approval.
5. **Data retention:** определить жизненный цикл `contexts/raw_result` при удалении проекта; приватные тексты исключаются из telemetry. Не архивировать личные вложения в Git.
6. **Access:** локальный сервер слушает `127.0.0.1`; также нужны CSRF/origin guards на мутирующих endpoints и текущее owner/session controls. Нельзя считать «локальность» достаточной авторизацией.
7. **Escaping:** raw hypothesis/examples/prompts никогда не встраивать в HTML/SQL как executable string; SQL параметризован, текст выводится escaped.
8. **Provider model pinning:** данные model catalog не исполняются как Python-код; fallback выключен; модель не назначает `candidate_id` или `source_fact_ids`.
9. **Operational rights:** consent охватывает только **генерацию запросов**, не права на TikTok video search/scraping/analytics; Collector запросит отдельный source budget/rights.

## 16. Testing — полная матрица

### 16.1 Unit (бесплатные, без PostgreSQL)

| ID | Тест | Ожидаемо |
|---|---|---|
| E-U01 | Canonical hash stable across equivalent dict key order | одинаковый SHA |
| E-U02 | Новая Taxonomy/publication | новый input hash |
| E-U03 | Отличается язык или quote policy | новый config/input hash |
| E-U04 | Missing/non-approved RTB | fail closed |
| E-U05 | Два parent пути и nullable branch | правильная ancestry |
| E-U06 | Весь full context превышает token window | explicit `context_too_large`, no truncation |
| E-U07 | 24 гипотезы fit 4 | 6 стабильных batches |
| E-U08 | Неровный размер | последняя группа <4, без пропусков |
| E-U09 | Разные directions | batching не склеивает разные root |
| E-U10 | Failed model capability/schema support | blocked_config, no call |
| E-U11 | Stale price snapshot | paused_price |
| E-U12 | Consent absent/revoked/expired | 0 paid calls |
| E-U13 | Decimal rounding of estimate/reservation | upward, no float |
| E-U14 | Usage cost > reservation | реальная переплата сохранена |
| E-U15 | Invalid schema / unknown hypothesis ID | failed_known после cost settlement |
| E-U16 | Missing batch hypothesis | no complete/handoff |
| E-U17 | Queries empty with null reason | rejected |
| E-U18 | Unknown facet key or wrong angle/language | rejected |
| E-U19 | Raw LLM invented project/fact IDs | discarded or rejected; server lineage wins |
| E-U20 | Duplicate start/cancel client key | no new provider call |
| E-U21 | GET status/results on pending | never charges |
| E-U22 | Candidate source_fact_ids | equals exact RTB hypothesis lineage |
| E-U23 | Candidate count ceiling per hypothesis/global | enforced |
| E-U24 | Fake transport not importing DTK/Apify/YouTube | no real search |
| E-U25 | No unknown implicit language/location | fail closed |

### 16.2 PostgreSQL integration (обязательно disposable PostgreSQL 17)

| ID | Конкурентный или DB-сценарий | Ожидаемо |
|---|---|---|
| E-D01 | Migrator twice | checksum/constraint stable, second = no-op |
| E-D02 | 2 workers claim same pending batch | exactly 1 successful claim |
| E-D03 | 2 batches reserve same consent | row lock prevents overspending reservation |
| E-D04 | Crash before attempt row creation | safe pending |
| E-D05 | Crash after reserved, before dispatch intent | safe reclaim/abort under lock |
| E-D06 | Crash after dispatch_intent | uncertain, NO automatic new POST |
| E-D07 | Provider response arrived, DB lost before commit | uncertain/reconcile |
| E-D08 | Unknown usage | keep reservation |
| E-D09 | Confirmed actual charge > reserve | record overcharge, stop |
| E-D10 | Cancel in-flight + provider reply | cost recorded, no new dispatch |
| E-D11 | Cross-project publication/consent/generation | reject |
| E-D12 | Revoke consent race vs batch claim | no new dispatch after revocation commit |
| E-D13 | Duplicate idempotency key + different body | 409; no duplicate run |
| E-D14 | Finalization + outbox insert crash | both committed or neither |
| E-D15 | Repeated outbox consumer delivery | one Query Validation logical input |
| E-D16 | Attempt settlement repeated | no double charged_usd |
| E-D17 | Approved config/quote/context mutated | DB immutable guard |
| E-D18 | Unmatched `hypothesis_id` candidate insert | FK reject |
| E-D19 | Completed generation with missing coverage | finalizer blocks |
| E-D20 | Reconcile unknown without evidence | still uncertain |
| E-D21 | Windows worker restart/update | no repeated paid dispatch |
| E-D22 | SQL migration does not alter RTB tables | before/after schema comparison |

### 16.3 Provider-contract integration (всё ещё бесплатно)

- Fake provider возвращает правильный `raw_llm/v1` и usage → `completed`.
- Fake provider HTTP 200 + malformed JSON + verified usage → `failed_known`, money recorded.
- Fake provider returns `finish_reason=length` → fail known, no handoff.
- Fake provider timeout/connection drop/ambiguous HTTP 400 → `uncertain`. **Не** повторять.
- Fake provider returns wrong model ID → fail-closed с учётом расходов.
- Fake provider response > configured bytes or lacks usage → uncertain/fail-closed согласно подтверждённости стоимости.
- Fixed docs JSON Schemas прогоняются валидатором; Python cross-field invariants отдельно тестируются.
- Ни одного реального OpenRouter API вызова в PR Engine. Никакого автоматического live smoke.

### 16.4 E2E перед подключением реального LLM

```text
2 approved RTB roots; 24 hypothesis contexts; one approved config
  -> 6 batches, exact input/model/schema hashes
  -> standalone quote + consent
  -> FakeProviderPort responds 6 paid-looking mock results
  -> 6 unique attempts; ledger settles, 0 real money
  -> 24 coverage rows, all candidates linked
  -> one completed query_candidates/v1 artifact
  -> one outbox handoff, consumer de-duplicates replay
  -> zero hits to search providers, zero real API keys
```

**Live tests:** только после отдельного разрешения пользователя на реальный счёт, проверенной новой модели, spend cap и egress policy. Test fixtures не должны случайно содержать боевой OpenRouter key.

## 17. Release / разработка по последовательным PR

### PR Engine-A: SQL + Contracts

- Проверить свободный номер migration после `014_tb_deep_hierarchical_taxonomy.sql`.
- Создать schema `sqg_*`, добавить immutable constraints, индексы и project-scope guards, отладить миграцию в disposable PG.
- Написать `generation_engine/contracts.py` с конфигурацией и состояниями; unit и DB tests.
- Никакого OpenRouter POST.

### PR Engine-B: Run + Batch + Quote/Consent

- `model_manager.py, run_manager.py, batch_manager.py, budget_manager.py`.
- Связать с `input_loader/context_builder` из PR #15; подписывать context/schema/config digest.
- Включить immutable config, quote expire, Search Planner consent, Decimal reserve guard.
- UI/API estimate/config/authorization; ещё нет provider dispatch.

### PR Engine-C: Execution, Recovery и Fake Provider

- `repository.py, execution_manager.py, recovery_manager.py, worker.py`.
- Реализовать T1/T2/T3, no implicit retry, event log, reconcile status, cancellation.
- С fake-transport протестировать idempotency, contention, unknown charges, Windows restart.

### PR Engine-D: Output + Outbox + Integration UI

- `output_manager.py`: backend candidate UUID/lineage, complete hypothesis coverage, final JSON schema.
- Atomic completion/outbox, diagnostic read API, safe UI + error surfaces.
- Controlled E2E fake-provider only. После CI merge можно подключать **следующий отдельный PR AI Query Generator**.

### PR AI Query (не часть Engine)

- `ai_query/generator.py, prompts.py, provider.py, schemas.py` — реальный OpenRouter adapter, model-specific structured output; ThinkQE-inspired facets, MuGI sketches, Jina lexical hints в одном completion.
- Отдельные платные smoke tests по согласованному бюджету.
- Затем Query Validation + embeddings/MMR, Tool Registry/Task Planner, Collector.

## 18. Структура каталогов и публичные Python-интерфейсы

```text
intelligence/search_planner/
  __init__.py
  input_loader.py           # PR #15; read-only approved RTB
  context_builder.py        # PR #15; full immutable context
  contracts.py              # PR #15; ancestry/source/policy
  generation_engine/
    __init__.py
    contracts.py            # enum transitions, typed inputs, safe parser
    run_manager.py          # create/idempotency, lock, cancel
    batch_manager.py        # deterministic partition and context estimates
    model_manager.py        # pinned OpenRouter catalog, pricing, schema
    cost_estimator.py       # quote and conservative reservation
    budget_manager.py       # separate consents and Decimal accounting
    execution_manager.py    # transactional T1/T2/T3
    repository.py           # SQL, scope, row-level locks, audit
    recovery_manager.py     # uncertain, reconciliation, crash handling
    output_manager.py       # backend provenance, complete coverage, outbox
    worker.py               # claim/dispatch, concurrency limit
    service.py              # orchestration and public server-facing API
    diagnostics.py          # allowlisted errors, metrics, event view
  ai_query/                 # next stage: LLM logic
    generator.py
    prompts.py
    schemas.py
    provider.py
  query_validation/         # later: relevance/embeddings/MMR
  registry/                 # later: tool capabilities
  task_planner/             # later: provider-specific tasks

intelligence/migrations/
  015_search_query_generation.sql  # proposed, check availability first

tests/
  test_search_planner_engine_contracts.py
  test_search_planner_engine_db.py
  test_search_planner_engine_races.py
  test_search_planner_engine_worker.py
  test_search_planner_engine_api.py
  test_search_planner_engine_output.py
```

**Предлагаемый публичный интерфейс:**

```python
create_quote(project_id, taxonomy_publication_id, approved_config_id) -> Quote
authorize_search_budget(project_id, quote_id, authorized_usd) -> Consent
create_generation(project_id, quote_id, consent_id, idempotency_key) -> Generation
start_generation(project_id, generation_id, idempotency_key) -> Generation
process_one_batch(project_id, generation_id, transport: ProviderPort) -> Status
get_generation(project_id, generation_id) -> GenerationStatus
get_batches(project_id, generation_id) -> list[BatchStatus]
get_candidates_bundle(project_id, generation_id) -> QueryCandidatesV1
request_cancel(project_id, generation_id) -> GenerationStatus
reconcile_attempt(project_id, attempt_id, evidence) -> ReconcileOutcome
```

**Уточнение по суммарной структуре:** первые четыре файла `search_planner/` из PR #15 не нужно переносить внутрь `ai_query/` или дублировать: Generation Engine использует их напрямую, а AI Query — порт Engine. В старых черновых спецификациях входной loader был показан внутри `ai_query/`; это **архитектурный устаревший путь**, исправляем документацией.

## 19. DoD — измеримые условия принятия

Generation Engine считается законченным лишь если одновременно:

1. **DB consistency:** миграция применяется дважды без побочных эффектов, все constraints/locks/FKs проходят PG integration tests.
2. **Safety:** без отдельного Search Planner spending consent происходит ровно 0 реальных платных запросов; проектные границы не обходятся через UUID или фальшивый HTTP-запрос.
3. **Idempotency:** concurrent workers и duplicate UI actions не создают повторной платной отправки одного attempt.
4. **Crash recovery:** после dispatch intent без result статус `uncertain`, резерв не освобождается автоматически, никаких повторных POST.
5. **Versioned reproducibility:** модель, полный HypothesisContext, batch fingerprint и цена остаются идентичными snapshot на всю генерацию.
6. **Correct handoff:** `query_candidates/v1` существует только после полного покрытого run и появляется одновременно с outbox событием.
7. **No semantic mixing:** Engine не внедряет `MMR`, platform search, hashtags popularity claims или fake видео.
8. **Production UI:** владелец видит status, actual+reserved spend, ошибки и диагностируемые причины pause/uncertain.
9. **CI:** Linux + Windows D1 smoke, PostgreSQL в disposable DB, все fake tests зелёные, no live billing.
10. **Documentation:** схемы и API не противоречат реальной миграции/коду; статус PR и фактическая работоспособность написаны честно.

## 20. Аудит и очистка соседней документации

Этот документ **заменяет только разрозненные предварительные описания Generation Engine** в переписке и в обзорном AI Query plan. Он не должен удалять/переписывать историю уже работающих RTB или Discovery.

- **Оставить** `docs/TAXONOMY_TWO_PASS_HIERARCHY.md` и `docs/CURRENT_STATE_2026-10-22.md`: это разные слои и историческое состояние; исправить индекс, а не делать вид, что они про новый Engine.
- **Оставить** `docs/SEARCH_PLANNER_AI_QUERY_GENERATOR_V1.md` и `docs/SEARCH_PLANNER_AI_QUERY_TECH_IMPLEMENTATION_V1.md`, потому что они описывают **другой** слой — логику LLM/ThinkQE/MuGI/Jina. Из overview удалить/исправить устаревшее положение loader внутри AI Query.
- **Оставить** `docs/SEARCH_PLANNER_HYPOTHESIS_CONTEXT_V1.schema.json`, `docs/SEARCH_PLANNER_AI_QUERY_RAW_LLM_V1.schema.json`, `docs/SEARCH_PLANNER_AI_QUERY_CANDIDATES_V1.schema.json`; каждый описывает **отдельную границу** данных и проверяется при реализации.
- **Удалить из активной ветки только дублирующий устаревший предварительный blueprint** `docs/SEARCH_PLANNER_AI_QUERY_TECHNOLOGIES_V1.md` **после** переноса и проверки содержательных ссылок в точное руководство `TECH_IMPLEMENTATION_V1`. Старый текст остаётся в истории Git/PR #14; это не удаление исследовательских источников и не изменение runtime.
- **Обновить** `README.md` и `docs/DOCS_INDEX.md`: новый главный документ Generation Engine, точные статусы PR #14/#15/#16, актуальные ссылки без 404. Не объявлять незамёрженные ветки частью `main`.
- **Не трогать** unrelated `docs/DISCOVERY_*, docs/RTB_*, tests, migrations`, пока нет доказательства, что они бесполезны. «Историческая спецификация» ≠ «ненужный файл».

### 20.1 Текущий status map (фиксирован 23.10.2026)

| Блок | Статус на дату написания |
|---|---|
| RTB published hierarchical source | код в `main`, 2 Human Review, immutable publication |
| Search Planner Input Loader + Context Builder | Python в **PR #15**, не `main` |
| AI Query + ThinkQE/MuGI/Jina | документация **PR #14**, кода нет |
| Generation Engine + sqg tables / worker | **этот документ**, кода/миграции/живых запросов нет |
| Query Validation / MMR | проектируется отдельно |
| Tool Registry + Task Planner / Collector | интеграции в разных стадиях (PR #10/#13 не заменяют E2E) |

**Итог реализации:** самый безопасный следующий coding PR — SQL + состояния + транзакционные контракты, затем Run/Batch/Consent Manager, затем fake-provider worker и outbox. Только после зелёных тестов включать OpenRouter и логические ThinkQE/MuGI/Jina. Один документ, один источник целевой архитектуры, никаких смешанных обещаний о готовности.
