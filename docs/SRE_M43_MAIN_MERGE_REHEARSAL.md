# SRE M43 — интеграция с latest main без ожидания CI

Дата: 2026-10-29. Статус: **SOURCE PARTIAL / CODE COMMITTED**, exact-HEAD merge-tree / PG17 clean+upgrade / Windows+Linux / Node22 / localhost host acceptance **НЕ ЗАПУСКАЛИСЬ**. Это документальное и кодовое продвижение M43, а не DoD PASS или разрешение merge.

Рабочая ветка только `feature/sre-production-v1-20261023` (Draft PR #22); `main`/peer branches не изменялись. На момент трёхсторонней сверки:

- **current main** `e647060d69c00a5988773ca335564c47cd397103`.
- **SRE перед M43 host-sync** `bead99519e3c2d8df77b6f6cc75e3c6c4587f454`.
- **merge-base** `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5`.
- В сравнении treesha обоих потомков с merge-base только **3 совместно изменённых пути**, по Git blob SHA: `app.py`, `site/index.html`, `.github/workflows/windows-d1-smoke.yml`. 318 изменений путей только в main и 227 только в SRE на снимке до M43. **Совместное изменение не равно автоматически Git-конфликту**.
- PR #22 в GitHub API сообщает `mergeable=false/dirty`, причём его `base.ref=main` metadata всё ещё показывает старый SHA `218f5f3`, отличный от реально прочитанного current-main `e647060`. **Нельзя выдавать этот ответ за окончательное испытание exact-current-main merge.**

## M43 SOURCE выполнено в SRE ветке

Только в **трёх SRE-копиях** общих файлов заново взят точный текст актуального main и поверх добавлена минимальная функциональность SRE:

1. `app.py` — сохранены новые маршруты `/api/tools/dtk/`, RTB deep v2 и Search Planner вместе с существующими source handler; после host `authorized()`, до общего `/api/state`, добавлен **только GET** `/api/sre/v1/` через `intelligence.relevance.http_api.handle_get`. Это M04 default-off read route; M38/M39 authenticated POST/GET v2 mount отдельно, без допущения, что старый host-API поддерживает их автоматически.
2. `site/index.html` — сохранён новый полный интерфейс `main` (включая Search Planner/Collector/RTB), добавлен единственный SRE Studio navigation `/sre-studio.html` после Discovery.
3. `.github/workflows/windows-d1-smoke.yml` — сохранены concurrency-cancel и все актуальные Windows/Collector тестовые команды main, добавлена только `node --check site/sre-studio.js`.

Эти GitHub-коммиты затрагивают **только SRE branch**, не переписывают исходные файлы `main` и не выполняют PR merge. Синхронизация main в копии SRE означает, что некоторые импортируемые host-компоненты (DTK/SP/Collector) присутствуют только в latest main до merge; standalone SRE branch без merged main не доказывает полную UI доступность. **Требуется обычное объединение деревьев на точных SHA**.

## Миграции: Git filenames без конфликта, но upgrade/ordering риск

Проверка дерева `main` и SRE нашла **8 пересекающихся числовых префиксов 054–061**:

| Префикс | Latest main | SRE |
|---|---|---|
| 054 | Collector planning, SP held task plans, RTB deep v2 research binding | sre_live_core |
| 055 | Collector worker, SP handoff inbox, RTB deep v2 budget ledger | sre_live_batch_coverage |
| 056 | Collector QV manifest, SP HTTP paid idempotency, RTB deep v2 human reviews | sre_live_dispatch_fences |
| 057 | Collector discovery outbox, SP supervisor leases, RTB deep v2 expansion consent | sre_local_embedding_cache |
| 058 | SP invoice reconciliations, RTB deep v2 search readiness | sre_live_opus_quotes |
| 059 | RTB deep v2 publication SRE | sre_live_opus_consents |
| 060 | SP RTB deep v2 pipeline | sre_live_budget_ledger |
| 061 | Collector/SP durable intake | sre_live_opus_dispatch_intents |

`intelligence.migrate` сортирует **полные имена файлов**, ledger version также полное имя. Поэтому одинаковый числовой префикс НЕ обязательно конфликт имени файла, однако порядок SQL на чистой базе и post-main retroactive additions должен быть проверен в PG17. Изменять чужие миграции нельзя. Историческая **`062_sre_live_opus_receipts.sql` всё ещё повреждена**, поэтому успешный `python -m intelligence.migrate` и M43 acceptance **не заявляются**. Строки `schema_migrations` никогда не подменять и SHA не переписывать ради зелёной отметки.

## Manual exact-SHA merge-tree rehearsal (без CI, checkout, merge commit и push)

Добавлен инструмент `scripts/sre_main_merge_rehearsal.py`:

- Принимает обязательные exact SHA main/SRE и optional `--expected-main-ref` для обнаружения движения remote-tracking main.
- Требует локально уже полученные объекты git; не делает `git fetch` и не запрашивает network.
- Делает `git merge-base`, diff paths и `git merge-tree --write-tree --messages`, выводит JSON с реальным статусом merge-tree и 054–061 prefix collisions. `merge-tree --write-tree` может создать только Git objects, но **не меняет ветки, refs или working tree**.
- На конфликте возвращает exit 2 и не подменяет FAIL положительным `mergeable`; при чистом слиянии возвращает 0 только для **узкого merge-tree**. `production_ready` остаётся `false`, `PG17/OS/UI` остаются `not_executed`.

Запуск на машине владельца после ручного обновления remote refs (`git fetch origin main feature/sre-production-v1-20261023`) и checkout SRE:

```bash
python -m scripts.sre_main_merge_rehearsal \
  --repo . \
  --main-sha e647060d69c00a5988773ca335564c47cd397103 \
  --sre-sha "$(git rev-parse HEAD)" \
  --expected-main-ref refs/remotes/origin/main
```

Если `main` после ревизии `e647060` сдвинулся, необходимо **сначала обновить фактический main SHA и повторить сверку**, не использовать старый immutable report как доказательство для нового main.

## M43 будущие критерии (отложены по просьбе не ожидать тесты)

- [x] Static latest-main 3-way tree overlap inventory
- [x] SRE-only shared host files updated to include current main behavior
- [x] Manual exact-SHA, no-merge rehearsal tool + SQL prefix collision discovery
- [ ] Actual `git merge-tree` in repo clone on HEAD + exact current main SHA: result **UNVERIFIED**
- [ ] PG17 clean and upgraded-main checksum-safe migrations: **BLOCKED BY 062**
- [ ] Linux Python 3.12, Windows Python 3.12, Node 22 UI, authenticated same-origin host smoke: **UNVERIFIED**
- [ ] Full M43 DoD: **NOT COMPLETE**

**SRE M42 partial и M43 source partial не превращаются в M42/M43 finished.** Общий прогресс по полноценно зачтённым кодовым этапам остаётся M01–M41/47 ≈87%, production readiness отдельно не утверждается.
