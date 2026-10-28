# RTB-FIX-05-B — Deep V2 Pass2 с платной стадией и H1 Lock

**Статус:** КОД РЕАЛИЗОВАН, QA: QUEUED / НЕ ПРОВЕРЕНО. Ветка `feature/rtb-module-v2`, Draft PR #32. Считается завершённым только подэтап FIX-05-B; весь FIX-05 не готов до FIX-05-C (expansions).

## Контракты

- Вход: `tb_deep_v2_runs.status=pass2_pending`; сохранённый `deep_first` completed и H1 `tb_deep_v2_reviews(review_no=1)` с точным `approved_sha256`; B9/A8 immutable binding и актуальный Opus model catalog. Второй Human Review в этом подэтапе не создаётся.
- Внутренняя операция: `RTBDeepV2Pass2Repository.advance_pass2(project_id,run_id,reviewer_id,sender=?,api_key=?)`. Запускается **только при явном вызове**; никакого POST из GET/quote/Review1 или автоматического quality engine.
- Проверки перед сетью: авторизация reviewer; порядок SQL locks `quote → run → consent → attempt`; replay-verified immutable Research Input, H1 SHA, первичное предложение Opus, независимое H1 graph validation; live Opus model/catalog/price; отозванное согласие; Pass2 context size; `reservation_cost(actual_request)<=required_pass2_reservation_usd` и остатка `authorized_usd`.
- Prompt Pass2 содержит все human-approved 14-field узлы и coverage, а также `rejected_pass1_node_ids` + `h1_is_locked`. Закреплённые B9 facts/A8 observations сохраняются без редактирования; нет MiMo/fast-LMM.
- Сначала транзакционный `tb_deep_v2_runs.pass2_running` + фиксированная `tb_deep_v2_attempts(stage=deep_second,status=reserved)`; потом **ровно один OpenRouter Opus 5.5 POST**, без автоматического retry/fallback. Crash между транзакцией и transport → reserved attempt остаётся, повтор блокируется.
- Подтверждённая `usage.cost`: `validate_protected_followup` запрещает любое изменение утверждённых H1 nodes (всех 14 полей) и возвращение исключённых IDs, далее `quality_gate` проверяет факты, гипотезы, дубли/охват, `quality_report` сохраняется вместе с attempt output. `review2`, только если `quality.passed=true`; иначе `expand_pending` (расширение платно и не вызывается автоматически).
- Биллинг: фактическое известное списание сохраняется даже при плохом JSON/неверной модели/отзыве consent midflight; тогда состояние `failed`/`cancelled`/`paused_budget`. Если provider usage отсутствует или транспортный сбой: `uncertain`, резерв удерживается, повтор запрещён до отдельного reconciliation.
- Quote TTL: саму смету необходимо использовать вовремя при **создании consent/start**, но уже запущенный run может продолжиться после длительного Human Review1; Pass2 вновь проверяет текущую модель/тариф и точный предел резерва.
- `inspect_pass2()` — отдельный авторизованный read-only статус, попытка, списание, дерево и качество; **не вызывает Opus**.

## Проверки, добавленные по коду

`tests/test_taxonomy_deep_v2_pass2_db.py` содержит 11 PostgreSQL17 моковых сценариев: успешный one-shot, запрет повторного paid вызова, отвергнутый modified H1 после списания, uncertain/no cost, timeout, превышение quote/budget до вызова, concurrent double advance, revocation до сети, детерминированный quality gate/expand_pending, точный B9/A8/H1 prompt, wrong-model charged response, delayed Review1 после TTL. Все работают только с `sender=mock`. [Структурный набор фактически содержит 11 `test_` методов; обновляй счётчик при изменениях.]

Проверки подключены в `.github/workflows/rtb-module-contracts.yml` (PostgreSQL17), но **GitHub Actions до сих пор queued**; нельзя утверждать `tests passed`. Имеются реальные source inspection checks на корректные пары `$$` миграций 054/055/056.

## Осталось для полного FIX-05

1. FIX-05-C: максимум два явных оплачиваемых Expansion, отдельная оплата и cap с подтверждением пользователя, расширение quote/consent ledger без скрытого увеличения бюджеты.
2. Human Review1 UI + HTTP/auth bindings, без доверия к ID reviewer из клиентского JSON.
3. FIX-06: отдельный Search Readiness Gate и Review2. `quality_gate` здесь — промежуточный deterministic gate, не финальный release.
4. PostgreSQL17 actual runs, Windows/Linux regression после восстановления GitHub Actions runners; фиксация реальных ошибок. Команды и runner blocker: [RTB_CI_RUNNER_BLOCKER_RU.md](RTB_CI_RUNNER_BLOCKER_RU.md).

**Никаких реальных API-вызовов или merge в main в этом подэтапе не делалось.**
