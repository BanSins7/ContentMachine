# RTB-FIX-08 — версионированный read-only экспорт публикаций для Search Planner / SRE

**Статус:** BACKEND-КОД ВЫПОЛНЕН; **QA: QUEUED / НЕ ПРОВЕРЕНО**. Ветка `feature/rtb-module-v2`, Draft PR #32. Main, `intelligence/search_planner/**` и `intelligence/relevance/**` не менялись.

## Реальные границы между модулями

Существующий `intelligence/search_planner/input_loader.py` по-прежнему читает legacy `tb_deep_publications` вместе с `tb_runs`, `tb_deep_reviews` и `tb_taxonomy_publications`. Система B9-native Deep V2 использует отдельную `tb_deep_v2_publications` с другими FK. **Нельзя** выдавать эти новые ID за строки старых таблиц или скрыто менять Search Planner. Вместо этого отдельный RTB-owned adapter предоставляет готовый снимок, который принимает уже существующий `intelligence/search_planner/contracts.py::validate_published_snapshot` и `context_builder.py::build_hypothesis_contexts`. Встраивание экспортера в SQL loader Search Planner — задача его собственной ветки.

Аналогично SRE уже получил утверждённую taxonomy version в атомарном FIX-07. FIX-08 выдаёт ссылку на **уже существующую** SRE-версию, не создаёт новую и не меняет её active pointer.

## RTB-owned публичные схемы

Файл `intelligence/taxonomy_builder/publication_export_v2.py`.

| Конкретная версия | Содержание | Назначение |
|---|---|---|
| `rtb-publication-export/v1` | **Ровно 11** старых полей каждого узла, B9 approved facts, source lineage и SHA | Search Planner V1, фиксированные требования текущего `HypothesisContext` |
| `rtb-publication-export/v2` | **Все 14** полей Deep V2: дополнительно `source_observation_ids`, `search_signals`, `validation_focus`; B9 facts, оригинальные A8 observations, SHA | Search Planner V2 / аналитика с подтверждёнными входными ссылками |
| `rtb-sre-taxonomy-reference/v1` | project, publication UUID, **существующий** approved SRE taxonomy version ID, signed topics checksum и pinned B9→SRE fact UUID mapping | Read-only SRE taxonomy pointer без дополнительной публикации |

Каждый export имеет фиксированные `schema_version`, `publication_id`, `run_id`, `publication_status='published'`, `human_reviews=[1,2]`, `sre_taxonomy_version_id`, `tree_sha256`, `v1_core_sha256`, `approved_facts`, `source_lineage`, `export_sha256` и `provider_calls=0`. Для V1 `original_a_observations` вообще нет; у V2 они остаются оригинальными A8 evidence references, **не продуктовой истиной**. Имя продукта, проблемы и функции в `profile_snapshot` заполняются только из соответствующих утверждённых B9 facts; `research_goal`, `target_audience`, география/язык не выводятся из видео и остаются `None`, если не подтверждены отдельным источником.

### API внутри RTB

```python
from intelligence.taxonomy_builder.publication_export_v2 import (
    RTBDeepV2PublicationExportRepository,
    SCHEMA_V1, SCHEMA_V2,
    to_search_planner_v1_snapshot, to_sre_taxonomy_reference,
)

repo = RTBDeepV2PublicationExportRepository(connection_factory=trusted_factory)
# reviewer_id должен быть установлен серверной аутентификацией, НЕ из клиентского JSON
dto = repo.export(project_id, publication_id, authenticated_reviewer_id,
                  accepted_versions=[SCHEMA_V2, SCHEMA_V1])
legacy = to_search_planner_v1_snapshot(
    repo.export(project_id, publication_id, authenticated_reviewer_id,
                accepted_versions=[SCHEMA_V1]))
sre_ref = to_sre_taxonomy_reference(dto)
```

- `accepted_versions` ОБЯЗАТЕЛЬНО: только явный список из `v1`/`v2` схем; порядок — явное предпочтение клиента, никакой скрытой деградации V2→V1, отсутствие пересжатия или генерации LLM. Неизвестные версии дают 422.
- `export(project_id,publication_id,reviewer_id,accepted_versions=...)` принимает **ID immutable publication**, не ID run. Авторизует project-scoped reviewer; repeatable-read/read-only PG транзакция проверяет `status=published`, подписи дерева, `tb_deep_v2_sre_links`, утверждённую SRE checksum/version и оба Human Reviews. После этого отдельно запускается B9/A8 immutable binding replay. Ошибки lineage не могут тихо заменяться текущими или наиболее свежими контекстами.
- `validate_publication_export(dto)` — чистая внутренняя проверка схемы полей, 11-field graph, UUID/tenant, B9 fact mapping, V2 A8 observation IDs, payload/tree/core SHA. **SHA — защита целостности, а не криптографическая подпись доверенного отправителя**: настоящая авторизация и источник истины остаются на уровне DB loader и source replay.
- `to_search_planner_v1_snapshot(dto)` выпускает DTO с ключами `taxonomy_publication_id`, `rtb_run_id`, `taxonomy_version`, `tree`, `approved_facts`, `profile_snapshot`, `human_reviews=[1,2]`, `run_status='approved'`, `deep_status='approved'`. Последние два — **совместимые семантические статусы V1 «оба human gate прошли»**, а фактический статус V2 остается `published`. SQL loader старого Search Planner это автоматически не заменяет.
- `to_sre_taxonomy_reference(dto)` возвращает signed immutable указатель на существующую SRE taxonomy; **никаких INSERT/UPDATE/OpenRouter**.

## Защита от недостоверных источников и дрейфа

- Replay source hash: B9 approved snapshot + C handoff и A8 original approved analysis pins обязательно совпадают с опубликованными данными.
- Exact `b9_fact_sha_to_sre_uuid`; A8 original observation IDs разрешены только как pinned A8 evidence. `source_fact_ids` могут ссылаться лишь на B9 approved facts в том же project.
- Ошибочные состояния `approved` (ещё **не** опубликовано), `review2`, `uncertain`, чужой `project_id`, неизвестный `publication_id`, отсутствующие H1/H2, подмена UUID/fact SHA, незаконные поля или schema version — fail closed.
- Никаких умолчательных языков, маркетов, целей или дополнительных продуктовых свойств. Контракты не обещают поиск, просмотры или доказанную эффективность.
- Immutable publication является входным источником; клиентские DTO допускают deepcopy/локальные изменения без влияния на БД.

## QA, встроенное в workflow

**14 офлайн тестов** в `tests/test_taxonomy_publication_export_v2.py` покрывают обе версии, точный 11/14, реальный текущий Search Planner validator и HypothesisContext, явное согласование версий, H1/H2 gate, SHA, A8 и B9 scope, no mutation и SRE-reference SHA.

**8 PostgreSQL17 mocked тестов** в `tests/test_taxonomy_publication_export_v2_db.py`: считывание по publication ID, исходное B9/A8 replay, Search Planner V1 compatibility через реальную PG публикацию, cross-project / unauthorized отказ, no DB write/no provider, concurrent reads, изменение pinned source fail-closed.

Linux/Windows offline и PostgreSQL17 шаги добавлены в `.github/workflows/rtb-module-contracts.yml`. **GitHub Actions остаётся в queued: это НЕ PASS и не подтверждённый FAIL.** Локально GitHub clone недоступен из-за DNS, `psycopg` и `psql` отсутствуют; полноценный запуск здесь не выполнен.

## Последующие задачи

- FIX-09: authenticated HTTP GET экспорт только со **серверным** reviewer context, UI для бюджета/Consent/H1/H2/публикации и legacy mode. Не разрешать клиенту заявлять `reviewer_id` без проверки.
- В отдельной ветке **Search Planner**: по явному решению владельца интегрировать новый RTB export loader с сохранением legacy SQL path и схемы HypothesisContext; **не менять его файлы из ветки RTB**.
- FIX-10/11: полная T01–T36, PG migrations 054–059 в CI, Windows/Linux parity, независимый release/merge audit. Изменения в `main` только после прямой команды пользователя.

**Защитные уточнения:** исходные A8 observation IDs обязаны быть строками и не могут нарушать схему (вместо неотловленного Python TypeError — `PipelineError`). Адаптер `to_search_planner_v1_snapshot` проверяет верхние пределы профиля/цитат Search Planner V1 и **отказывает вместо усечения подтверждённой B9 информации**. Эти два случая покрыты отдельными офлайн-тестами (общее число 14).
