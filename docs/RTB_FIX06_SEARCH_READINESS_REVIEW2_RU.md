# RTB-FIX-06 — Search Readiness Gate и Human Review №2

**Статус:** 06-A/06-B **BACKEND-КОД РЕАЛИЗОВАН**, QA: QUEUED / NOT EXECUTED. Ветка `feature/rtb-module-v2`, [Draft PR #32](https://github.com/BanSins7/ContentMachine/pull/32). В реальный OpenRouter не обращались.

## 06-A. Отдельный детерминированный Search Readiness Gate

Файл: `intelligence/taxonomy_builder/deep_v2_readiness.py`.

- Повторно читает тот же immutable B9/A8 `research_input`; проверяет строго 14 полей и существование ссылок на утверждённые B9 facts / оригинальные A8 observations с отдельным уровнем доверия. A8 video observation **не становится** доказанным свойством продукта.
- Проверяет, что каждое поле H1 по всем 14 ключам сохранено, отвергнутые из первого Pass1 IDs не возвращены, граф ацикличен, есть минимум два research directions и конкретные гипотезы; `validate_deep_v2_tree` отказывает при несоответствии.
- Запускает существующий `quality_gate` по V1 core-проекции, но не заменяет им Search Readiness: проверяет severe fact-coverage gaps, orphan directions без исследуемых гипотез, неопределённые дубли, новое крупное направление/неподтверждённую насыщенность.
- Search signals / validation focus гипотез должны иметь минимум два непустых смысловых термина после нормализации; одинаковые поисковые сигналы/критерии между гипотезами блокируются. Оригинальная строгая V2 schema требует 1–6 значений этих массивов у каждой гипотезы, 0–12 проверенных IDs исходных A8 наблюдений.
- Gate возвращает детерминированный `rtb-search-readiness/v1` report с `tree_sha256`, `research_input_sha256`, `h1_sha256`, `first_pass_sha256`, coverage, `blocking_errors` и подписанным `report_sha256`. Платных запросов нет; `passed=false` означает **не допускать публикацию**.
- Это **структурная и evidence-based проверка готовности к поиску**, а не гарантия просмотров, виральности, фактического поиска или успешности контента.

## 06-B. Неизменяемые Search Readiness и Human Review №2

Файлы: `intelligence/migrations/058_tb_deep_v2_search_readiness.sql`, `intelligence/taxonomy_builder/deep_v2_review2_repository.py`.

- Внутренний `preview_review2(project_id,run_id,reviewer_id)` только читает текущий `review2` run, последний **completed and charged** `deep_second` либо `deep_expand_1/2`, итоговые `nodes/duplicates/quality_report`, H1 и оригинальный Pass1. Сравнивает сохранённую quality с run quality. Возвращает готовность и исходный SHA без LLM вызова.
- `submit_review2(...,decisions)` требует явных человеческих решений по каждому candidate node (approve/reject/edit/add/merge), заново валидирует все 14 полей, H1 lock, исходные цитирования, B9/A8 и absence of rejected Pass1 IDs. **После ВСЕХ human edits gate пересчитывается на уже утверждаемом дереве**, а не на предыдущей версии модели.
- SQL 058 хранит отдельный append-only `tb_deep_v2_readiness_reports`, включая SHA candidate и утверждённого дерева, source SHA, signing SHA и исходную last paid stage. `BEFORE INSERT` trigger проверяет `review2` state, actor authorization, B9/A8 frozen source SHA, что stage совпадает с `expansion_count` и имеет completed paid attempt. H2 `tb_deep_v2_reviews(review_no=2)` невозможно INSERT без **проходящего подписанного readiness report с обоими теми же SHA**.
- В единой DB транзакции: сохранить report → сохранить H2 decisions + approved_nodes → `tb_deep_v2_runs.status='approved'`, `quality_report=gate`. Ошибка любого звена откатывает всё. GET `get_review2` проверяет hashes отчёта и дерева. SQL запрещает UPDATE/DELETE report и review.
- `approved` — **только успешное Human Review №2**, это **НЕ** публичная версия, export или публикация. Immutable publication + 11-field projection + SRE bridge входят в отдельный FIX-07.

## Добавленные QA (весь провайдер только mock)

- `tests/test_taxonomy_deep_v2_readiness.py`: 10 offline сценариев (прошедшее дерево, generic/repeated search terms, uncovered B9 facts, fake A8 observation, 14-field H1 changes, rejected nodes, model duplicates, replay SHA).
- `tests/test_taxonomy_deep_v2_review2_db.py`: 9 PostgreSQL17 моковых интеграций, в том числе real B9 H1 reviewer edits, завершённый fake Pass2, read-only preview, двойное/неполное рассмотрение, невалидные правки, отсутствие подписанного SQL gate, SQL immutable UPDATE/DELETE, проектная авторизация и неизменность расходов после review.
- `tests/test_taxonomy_deep_v2_sql_static.py`: SQL 058 и соответствующие проверяемые stage/consent/report guards; `.github/workflows/rtb-module-contracts.yml`: Linux/Windows pure tests и PostgreSQL17 fake E2E.
- **Нет CI PASS:** GitHub Actions на последних SHA оставались `queued`, PostgreSQL/psycopg в локальном окружении отсутствуют. Выполнение миграций 054–058 и Python-тестов не подтверждено. Это не считается фактическим FAIL.

## Следующее

1. Получить выполняемые runners и проверить реальные PG17 миграции/интеграции. Не ставить QA PASS без вывода.
2. FIX-07: атомарная immutable Deep V2 publication, 14→11 V1 core, точный SHA/UUID B9 mapping и SRE export.
3. FIX-09: пользовательский Human Review1/2 UI и безопасный authenticated server adapter. Никогда не доверять user-supplied `reviewer_id`.
4. Если требуется полноценное семантическое утверждение реальной эффективности, это отдельный контракт — текущий Gate не должен его придумывать.
