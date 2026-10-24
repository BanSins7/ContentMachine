# Video Collector — этапы 2–4: реализованный offline pipeline

**Ветка:** `feature/video-collector-dev-20261023`. Этот документ описывает то, что уже реализовано в модуле `intelligence/collector/`, а не будущий запуск TikTok. Целевая end-to-end архитектура: [VIDEO_COLLECTOR_PIPELINE_V1.md](VIDEO_COLLECTOR_PIPELINE_V1.md).

## 1. Границы этапа

Готовы **контракты данных**, строгий **Query Intake**, статический **Source Registry**, детерминированный **Task Planner** и **Offline Preview**, запускаемый как Python API или CLI. Отдельные провайдеры, PostgreSQL, worker, UI, реальный Query Validation и источник credentials не вызываются ни напрямую, ни косвенно.

`main` пока не производит persisted immutable `validated_query_set/v1`: `sqg_generations` только планирует будущие LLM jobs. Поэтому вход текущего этапа — **синтетический fixture / переданный вручную структурный снимок**, а `validation_state="accepted"` всего лишь проверяемое **поле JSON, не доказательство одобрения**. Каждый Preview всегда содержит `query_provenance_verified=false`, `execution_enabled=false`, `source_policy_not_verified` и никогда не может запустить provider.

## 2. Контракты

`intelligence/collector/contracts.py`:
- `ValidatedQuerySet` — scoped `project_id, taxonomy_publication_id, taxonomy_version, taxonomy_sha256, generation_id, validation_version` и queries.
- `ValidatedQuery` — `query_id, hypothesis_id, direction_id, text, mode, language, validation_state`.
- `CollectorLimits` — строгие границы задач, страниц, результатов и requests.
- `SourceDescriptor` — декларативные capabilities и явные `operationally_verified=false`, `source_policy_verified=false`.
- `SourceTask` — независимое детерминированное задание с full query/hypothesis provenance.
- `CollectorPlan` — полный preview и его content-addressed SHA-256.
- `ProviderPage`, `CandidateEnvelope` — DTO для будущих этапов декодирования/ingest; нормализованный URL проверяется `intelligence.discovery.normalize`.

Python dataclasses `frozen=True, slots=True`; входной JSON проходит строгие allowlist, type и bounds checks. Фальшивые признаки доступа/доверия отклоняются как неизвестные поля. Прямо создать объект Python может доверенный внутренний вызывающий код, но `build_preview` **перепроверяет** Query Set.

## 3. Query Intake

`parse_validated_query_set(raw, expected_project_id=None, expected_publication_id=None)` требует:
- только `schema_version="validated_query_set/v1"`, без дополнительных полей;
- project ID — 32 lowercase hex; publication и generation IDs — канонические UUID; taxonomy hash — 64 lowercase hex;
- taxonomy version >0; validation_version — непустая строка не длиннее 120;
- 1–50 queries с уникальными stable query IDs; каждый query содержит только семь разрешённых полей;
- `validation_state="accepted"` как *декларативное поле fixture*, mode `keyword|hashtag`, language `xx|xxx|xx-YY`, text длиной 1–160 без C0/C1 controls;
- explicit project/publication scope mismatch → ошибка; никакой клиентский флаг `verified` не принимается.

Одинаковые текстовые строки по **разным** гипотезам допустимы: Planner объединяет физический поиск и сохраняет все связи.

## 4. Source Registry

`list_sources()` и `match_source(source_id, mode)` **ничего не опрашивают по сети**.

| Source ID | Платформа | V1 textual keyword mapping | Статус |
|---|---|---|---|
| `dtk_tiktok` | TikTok | `keyword → search_posts` | Есть коннектор в **отдельном PR #13**, не интегрирован в эту ветку |
| `apify_tiktok` | TikTok | нет реализации | reserved |
| `youtube_official` | YouTube | нет реализации | reserved |
| `instagram_official` | Instagram | нет реализации | reserved |

DTK `hashtag_posts` требует upstream hashtag ID, а не просто `#тег`; поэтому automatic `hashtag` **не поддерживается** до разработки resolve step. Система не подменяет неподдерживаемый hashtag на keyword и не перенаправляет молча на Apify.

Присутствие source в Registry **не** даёт разрешения на поиск или сохранение чужих метаданных. Эти права, как и стоимость, должны быть проверены серверно и отдельно перед будущим Commit.

## 5. Task Planner

`build_preview(query_set, source_id="dtk_tiktok", limits=None)`:

1. Повторно проверяет Query Set и optional expected project/publication.
2. Выбирает зарегистрированный source и operation, а неподдерживаемые запросы возвращает в `rejected_queries`.
3. Объединяет keyword queries с одинаковой строкой по Unicode NFKC + casefold и одинаковым language, но сохраняет исходные поисковые формулировки и query/hypothesis/direction IDs.
4. Сортирует задания детерминированно; `task_fingerprint` и `plan_hash` = SHA-256 от канонических snapshot, версии registry/planner, ограничений и связей.
5. Считает `input_query_count`, `planned_task_count`, `covered/uncovered_hypothesis_ids`, `max_external_requests_upper_bound`, `requested_result_upper_bound`, `rejected_query_count`.
6. Лимиты: max_tasks 1–50, max_pages_per_task 1–5, max_results_per_page 1–50, max_total_requests 1–250, max_total_candidates 1–2500. При превышении количества задач и requests показывает `blocks`, **не обрезает** план молча.
7. Даже при отсутствии ошибок выполнения `execution_enabled=false`. Ни источник, ни количество страниц/результатов, ни стоимость не считаются проверенными.

Это верхняя граница **запрошенных**, а не фактически полученных результатов. Некоторым поставщикам невозможно задать page size; `max_results_per_page` здесь локальный лимит будущего исполнителя, а не подтверждение provider pagination.

## 6. Запуск CLI

В корне репозитория:

```bash
python -m intelligence.collector.preview_cli examples/video_collector_offline_preview.json
```

Печатает `{"ok": true, "preview": {...}}`; выходной код 2 при ошибке входа. Файл входа <=256 KiB, строго с тремя ключами: `query_set, source_id, limits`. CLI ничего не подключает и не изменяет.

Python API:

```python
from intelligence.collector import build_preview
from intelligence.collector.preview_cli import preview_document
import json

with open("examples/video_collector_offline_preview.json", encoding="utf-8") as f:
    result = preview_document(json.load(f))
assert result["execution_enabled"] is False
print(result["plan_id"], result["summary"])
```

## 7. Tests and acceptance

```bash
python -m unittest tests.test_video_collector_offline_v1 -v
python -m compileall -q intelligence/collector/
```

Покрываются strict schema, project/publication scope, дубли ID, запрет лишних полей, typestate, immutable copy, deterministic ordering, Unicode matching, язык, hypothesis lineage, ограничения task/request, future sources, отсутствие network, JSON serialization, valid/invalid CandidateEnvelope и ProviderPage.

**Готовность к следующему этапу:** нельзя создавать live search run из этого preview. Сначала Search Planner должен публиковать серверно верифицированный query set, а Collector — поднимать source policies/rights из PostgreSQL, получать точное подтверждение затрат, проверять DTK и иметь безопасный commit/attempt ledger. См. целевой [pipeline](VIDEO_COLLECTOR_PIPELINE_V1.md).

**Проектная защита:** ветка независима от `main` и не запускает никакие provider/API calls; не выдаёт успешную запись video или релевантность SRE.
