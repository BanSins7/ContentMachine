# RTB-FIX-05-C — два независимых платных Expansion (Deep V2)

**Состояние: BACKEND-КОД РЕАЛИЗОВАН; QA: QUEUED / НЕ ПРОВЕРЕНО.**

Ветка: `feature/rtb-module-v2` · [Draft PR #32](https://github.com/BanSins7/ContentMachine/pull/32). Этот документ — часть [общего трекера](RTB_IMPLEMENTATION_PROGRESS.md). **Никакие реальные вызовы OpenRouter не выполнялись.**

## Контракт

Pass1 + Pass2 имеют исходную B9 Deep V2 quote/consent. Expansions НЕ включены в исходную цену и не разрешаются ею. Когда Pass2/Expansion возвращает `quality.passed=false` и run находится в `expand_pending`, пользователь вправе провести *не более двух* дополнительных отдельных платных вызовов:

1. `quote_expansion(project_id,run_id,reviewer_id,max_output_tokens=8192)`: read-only проверка B9/A8, immutable H1, `deep_second` или `deep_expand_1` completed output, SHA prior tree и текущей quality, жёсткий Opus 5.5 catalog, точный JSON prompt `deep_expand` и точный `reservation_cost`. Quote сохраняется append-only на 30 минут и **не вызывает модель**.
2. `authorize_expansion(project_id,quote_id,reviewer_id,authorized_usd,acknowledged_quote_sha256=...)`: отдельное человеческое подтверждение отображённой суммы, округлённой **вверх до шести знаков USD**. Не выдаётся при несовпадении SHA, просроченной смете, новом тарифе, отсутствии разрешения, изменении prior-tree/quality/Research Input или уже подтверждённом расширении на эту позицию. Отдельный consent хранится навсегда; SQL `AFTER INSERT` атомарно увеличивает `tb_deep_v2_runs.authorized_usd` ровно на утверждённую сумму. Самостоятельный SQL run guard допускает увеличение только при наличии связанного immutable consent, иначе отклоняет.
3. `revoke_expansion(...)`: неизменяемый отзыв конкретного expansion consent. Одно лишь исходное B9 C согласие не разрешает expansion, а отзыв основного consent блокирует все будущие платные этапы.
4. `advance_expansion(project_id,run_id,expansion_consent_id,reviewer_id,sender=?,api_key=?)`: исключительно по **отдельному пользовательскому действию**. Сверяет все SHA и модель/тариф на момент отправки; в одной транзакции создаёт `expand_running`, денежный резерв и `deep_expand_1` либо `deep_expand_2` attempt. Затем **ровно один** вызов Opus без auto-retry/fallback. Второй транзакцией фиксирует известный provider charge и валидированное дерево с отчётом качества либо удерживает резерв при `uncertain`.
5. После 1-го успешного expansion `quality.passed=false` → `expand_pending` для возможной отдельной 2-й quote/consent. После 2-го и `quality.passed=false` → `blocked_quality`. Если gate пройден → `review2`. Нет 3-го расширения, невозможен оплачиваемый запуск из GET/preview/quality calculation.

## Неизменяемые данные

- `tb_deep_v2_expansion_quotes` привязывает `research_input_sha256`, `previous_tree_sha256`, `quality_sha256`, `request_sha256`, `prompt_sha256`, Opus catalog ID/SHA и лимит output.
- Исходный H1 сохраняется без изменения любого из **14 полей**. Pass2 и последующий Expansion-1 output также остаются защищены в последующем expansion. `rejected_pass1_node_ids` отдельно переданы модели и дополнительно запрещаются валидатором.
- `tb_deep_v2_expansion_consents` уникален по `(project_id,run_id,expansion_no)`. `tb_deep_v2_expansion_revocations` append-only. SQL триггер для попыток требует правильной дополнительной подписи/суммы и незаблокированного consent.
- Неизвестное списание, отсутствие usage, неопознанная цена, сетевой timeout: `uncertain`, резерв не освобождается, **повтор запрещён** до отдельной процедуры подтверждения списания.

## Исходники

- `intelligence/migrations/057_tb_deep_v2_expansion_consent.sql` — isolated additive RTB SQL migration (5 пар `$$` PL/pgSQL), composite foreign keys, auditable budget top-up, expansion stage guard.
- `intelligence/taxonomy_builder/deep_v2_expansion.py` — internal server-side quote/authorize/revoke/advance, no public routing.
- `intelligence/taxonomy_builder/deep_v2_transport.py` — дополнительное строгое поле `last_approved_or_generated_tree` только у `deep_expand`; обычные Pass1/Pass2 отказываются принимать этот параметр.
- `tests/test_taxonomy_deep_v2_expansion_db.py` — 10 PostgreSQL17 моковых тестов (обе стадии, никогда не третья, отдельная смета/согласие, бюджет, H1 locked, no retry, unknown usage, revocation, concurrent duplicate).
- `tests/test_taxonomy_deep_v2_expansion.py` — 4 offline теста transport contract; `tests/test_taxonomy_deep_v2_sql_static.py` — проверка 057 и исходных триггеров.
- `.github/workflows/rtb-module-contracts.yml` — Linux/Windows unit и PG17 mocked integration.

## Что пока НЕ готово

- REST API / безопасный authenticated reviewer binding, UI для пользовательского consent на каждое расширение (план FIX-09).
- Финальный Search Readiness / Human Review №2 / immutable publication (FIX-06/07).
- Уточнённая финансовая reconciliation при `uncertain` (невозможно повторно отправить запрос без независимого доказательства стоимости).
- Фактические прогоны CI: GitHub Actions до сих пор `queued`; PostgreSQL17 тесты не были выполнены. **Нельзя отмечать QA PASS.**

Следующий чат: прежде всего сверить SHA ветки и CI, затем перейти к FIX-06 (Search Readiness + Review2), не изменяя main и внутренности модулей A/B/SP/SRE.
