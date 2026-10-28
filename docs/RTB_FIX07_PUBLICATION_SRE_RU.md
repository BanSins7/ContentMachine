# RTB-FIX-07 — атомарная неизменяемая Deep V2 публикация + SRE bridge

**Статус:** BACKEND 07-A/B/C **РЕАЛИЗОВАН ПО КОДУ**; QA: **QUEUED / NOT EXECUTED**. Ветка `feature/rtb-module-v2`, Draft PR #32. Нет реальных вызовов Opus. `main` не менялся.

## Граница автономного RTB

Legacy `tb_taxonomy_publications` и `tb_sre_taxonomy_links` внешними ключами привязаны к legacy `tb_runs`, а новый B9-native Deep V2 run хранится в `tb_deep_v2_runs`. Их прямое переиспользование создаст неверные связи либо сломает исторический V1. Поэтому **новая миграция `059_tb_deep_v2_publication_sre.sql`** создаёт исключительно RTB-owned таблицы `tb_deep_v2_publications` и `tb_deep_v2_sre_links`, без изменения legacy публикаций или исходных A/B/SP/SRE модулей. Статус RTB run расширен на `published`.

## 07-A — корректная, целиком сохранённая публикация

`RTBDeepV2PublicationRepository.publish(project_id,run_id,reviewer_id)`:

1. Проверяет авторизованного reviewer и блокирует проектный run. Только `status=approved` после неизменяемого Review2; любой резерв `reserved_usd != 0` блокирует.
2. Заново читает real B9/A8 через `RTBDeepV2BindingRepository.get`; сверяет подписанный Research Input, H1/H2, `approved_sha256`, `readiness.report_sha256` и **детерминированно пересчитывает финальный Search Readiness**. Никакого нового MiMo или Opus.
3. Формирует единый канонический manifest: точное `v2_tree` из 14 исходных полей, неизменённое `v1_core` из ровно 11 старых полей, два отдельных SHA и полный `b9_fact_sha_to_sre_uuid`, source B9 snapshot/hand-off, A context SHA, readiness SHA.
4. **В одной физической PostgreSQL-транзакции:** пишет/reuses факты SRE, создаёт одну новую SRE taxonomy version и категории, утверждает SRE версию, переключает active taxonomy проекта, вставляет immutable `tb_deep_v2_publications`, вставляет `tb_deep_v2_sre_links` и переводит run в `published`. Любая ошибка в середине делает ROLLBACK **всего**, в том числе SRE profile/facts/version/active pointer.
5. Повторный вызов `publish` для уже `published` run возвращает прежний immutable snapshot (`created=false`), не создаёт новую SRE версию и не расходует деньги. Одновременные вызовы сериализуются row lock по run.
6. `get_publication(...,projection='deep-v2'|'legacy-core-v1')` — чистое чтение после auth. Проверяет SHA V2/V1 core, точное 14→11 преобразование, SRE link, `status=approved` у SRE версии, её checksum и число категорий. Для legacy потребителя отдаёт `nodes` **строго из 11 полей**, не требуя знать внутренние поля Deep V2.

SQL `BEFORE INSERT` публикации дополнительно требует `review2.approved_nodes = v2_tree`, совпадение human SHA/report/source B9/A8/fact mapping, approved SRE version и `snapshot_checksum`; обе таблицы immutable UPDATE/DELETE через `psi_block_immutable_write()`. Не допускается публикация из `review2` без согласия человека или из `uncertain` billing.

## 07-B — не дублировать факты B9 в SRE

`deep_v2_sre_bridge.py`:

- `b9_fact_sre_uuid(project_id,b9_fact_sha256)` повторно вычисляется для **каждого** B9 approved факта; итог обязан совпасть с pinned `rtb_fact_id` и со всей картой `b9_fact_sha_to_sre_uuid`. Факт SHA B9 остаётся в manifest и не подменяется UUID5.
- Источник `sre_facts.original_quote` — действительная цитата `citations[].source_quote`, НЕ придуманная цитата. Если её нет, публикация fail-closed. `normalized_text` — утверждённый B9 reviewed text.
- Для уже существующего `sre_facts.id` проверяются **тот же project_id**, kind, original quote, reviewed text, origin и `review_status=approved`. При полном соответствии reused; если UUID совпал, а содержимое/проект отличается — ошибка, транзакция полностью откатывается. Это исключает `INSERT` дубликатов тех же утверждённых фактов между последовательными C-runs.
- Для новых B9 фактов создаётся профильная версия SRE только на основании доказанного `product_type`/function/problem B9. Если SRE факты уже существовали, новая профильная версия не нужна. Новое `research_goal` не выдумывается.
- `sre_taxonomy_categories` используют детерминированный project+node UUID5, `relation_type` из допустимого SRE enum, parent/kind/rationale и новые V2 signals/validation_focus/observation IDs в версии rule DSL. A8 observations — **подсказки для исследования, не verified_video и не факты собственного продукта**.
- `node.examples` попадают только в `sre_category_examples(example_kind='illustrative',video_id=NULL)`; verified real video никогда не генерируется из текстового примера. `sre_category_fact_links` связывают только существующие утверждённые UUID B9.
- SRE approved version защищена уже действующими SRE SQL guards; RTB не меняет код `intelligence/relevance/` и Search Planner.

## 07-C — тесты

- `tests/test_taxonomy_deep_v2_sre_projection.py`: **8 pure** проверок SHA, project-scoped UUID5, 14/11 точности, отсутствия сфабрикованных A8 video facts/verified videos, неизменности входа и fail-closed на неправильные факты/цитаты.
- `tests/test_taxonomy_deep_v2_publication_db.py`: **11 PostgreSQL17 mocked E2E**: реальный H2-approved mock chain, one-shot publication, immutable V2/11 API, SRE category/fact links, reuse same B9 UUID, conflicting existing UUID content rollback, fault-injected rollback после полной SRE записи, конкурентная двойная публикация, immutable SQL DELETE и project isolation.
- `tests/test_taxonomy_deep_v2_sql_static.py`: новая migration 059 с guarded standalone foreign keys, H2/SRE source checks и `$$` балансом (суммарно по RTB SQL 054…059: 1/6/1/5/2/2 пары). RTB Linux/Windows offline и PostgreSQL17 CI workflows обновлены.

**QA НЕ ЗАВЕРШЁН:** GitHub Actions на последних коммитах остаётся в `queued`, PostgreSQL17/psycopg нельзя запустить в этом локальном окружении. Статическая проверка SQL delimiters — не равно успешная миграция или passed DB tests. Для FIX-07 backend code `[x]`, отдельно `QA: QUEUED / НЕ ПРОВЕРЕНО`.

## Следующий этап

1. Реально выполнить миграции 054–059 и все offline+PG тесты, исправить проявившиеся ошибки (особенно SRE FK / project isolation и mock H2).
2. FIX-08: самостоятельный versioned `publication_export_v2.py`, SRE + Search Planner V2 read-only contract с negotiated 14/11 field DTO без изменений потребителей.
3. FIX-09: UI/авторизованные backend routes, включая explicit budgets, H1/H2, publication, legacy Flat/Deep. Не допускать web route, доверяющий клиентскому reviewer_id.
4. FIX-10/11: T01–T36, интеграционный audit, merge-ready gates. Главную ветку до user explicit merge не менять.
