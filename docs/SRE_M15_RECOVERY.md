# SRE M15 — LIVE Cancel / Pause / Restart / Fail-Stop Recovery

**26 октября 2026 · Изолированная ветка:** `feature/sre-production-v1-20261023` · [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22)

**Статус:** код/SQL/тесты коммитнуты; CI на точном последнем SHA не подтверждён как SUCCESS. Никаких платных HTTP/LLM вызовов и merge в main.

## Контракт M15

Новый внутренний порт `intelligence/relevance/live_recovery.py` создаёт технический fail-stop для уже существующих M12–M14 LIVE-запусков. Все действия требуют отдельно аутентифицированного владельца проекта; `project_id` сам по себе не подтверждает права пользователя.

- **`cancel_live_run(conn, project, run_uuid)`**: атомарная отмена с полной инвалидизацией `worker_token`/lease и увеличением поколения при уже выданной аренде. Без признаков отправки → terminal `cancelled`; повтор отмены идемпотентен. Если существует хотя бы одна потенциально отправленная попытка → **`unknown` + `cancel_requested=true`**, не `cancelled`: нельзя утверждать, что деньги не списаны. Несовместимые terminal/unknown состояния не открываются.
- **`pause_live_run(...,reason=paused_rights|paused_budget|waiting_source)`**: отзывает технические аренды, переводит задание в соответствующую паузу. Если уже существует записанный `dispatch_intent`, применяется тот же fail-stop `unknown` — пауза не является основанием для повторной платной отправки.
- **`restart_live_run(...)`**: допускается только из паузы/ожидания *до любой потенциальной отправки*. Перепроверяет **фактические** H1/H2 approved C9, D1/S3 права (включая `can_external_llm`), expiry, source-policy и content SHA, сверяет полный pinned request digest M12. Отказывает при изменившемся входе, `unknown`/terminal ребёнке или заблокированной C9-паре. Сбрасывает старые leases и переводит родителя **обратно в `pending_consent`**, детей в `not_evaluated`, а группы в `queued`; **старое финансовое согласие никогда не переиспользуется**. После restart ничего не claim'ится, пока отдельный будущий механизм согласия снова не активирует очередь.
- **`recover_expired_live_leases(...)`**: при потере worker/истечении срока аренды использует часы СУБД; только pre-dispatch `reserved` можно необратимо `abandoned_before_dispatch` и безопасно вернуть в `queued` с новым fencing generation. Если **где-либо в запуске** есть `dispatch_intent`, `unknown` или иное потенциально списанное действие, recovery fail-stop'ит **весь запуск** в `unknown` и запрещает автоматический повтор. Подтверждение провайдера/финансовое reconciliation отложено на следующие этапы.

Результат каждого внутреннего API содержит `dispatch_allowed=False` и `automatic_retry_allowed=False`: ни lease, ни restart не является permission на paid POST.

## SQL-журнал намерения перед внешним POST

Новая аддитивная миграция **`056_sre_live_dispatch_fences.sql`** после уже существующей 055, без изменения исторических миграций:

- `sre_live_attempt_fences`: append-only `attempt_id`, `project_id/run_id/batch_id/item_id` по составному FK к M13 batch membership, `worker_token`, `lease_generation`, `request_sha256`, `state`, `intent_at`.
- Технический lifecycle: `reserved → abandoned_before_dispatch` (доказано, что намерение не зафиксировано) либо `reserved → dispatch_intent → unknown`. `unknown → reserved/dispatch_intent` запрещено; завершение `unknown` потребует в будущем отдельный audited reconciliation, который **не реализован** на M15. Никаких model output, провайдерских квитанций или цены эта таблица не создаёт.
- BEFORE INSERT/UPDATE: проверка активного run, разрешённого состояния, текущего неистёкшего item lease, совпадения UUID token и поколения. Дополнительно каждое изменение журнала берёт **`sre_workspaces FOR SHARE`**, чтобы его нельзя было изменить в обход синхронизации с exclusive cancel/pause. Поле `intent_at` после фиксации неизменно. Записи запрещено удалять; `PUBLIC` гранты отозваны.
- Техническое `dispatch_intent` ещё **не даёт** права обращаться к провайдеру и не создаёт бюджетного резервирования: для реального POST будущий отправитель обязан атомарно проверить отдельные consent, finance ledger, M08 rights и текущее fencing. До этих этапов ни один endpoint не должен отправлять данные внешним LLM.

## Почему нет race между cancel и claim

M14 `claim_live_item`, `heartbeat_live_item`, `release_live_item` теперь берут `sre_workspaces(project_id) FOR SHARE` **в рамках одной транзакции до захвата item**. Worker разных заданий по-прежнему допускаются параллельно (SHARE совместим с SHARE). M15 cancel/pause/restart/recovery берут тот же workspace **FOR UPDATE**, затем конкретный run, затем journal и child rows. Триггер dispatch-intent также держит workspace SHARE. Следствие: отмена дожидается завершения текущего claim/intent, отзывает выданные токены, и ни новая отправка (через future fenced paid dispatcher), ни heartbeat по старому токену не могут легально продолжаться.

Это сервисная защита PostgreSQL: privileged DBA теоретически может отключить триггеры и переписать историю; это не заменяет owner authentication, RLS и финансы.

## Проверки

- `tests/test_sre_live_recovery.py`: pure-unit с fake DB: pre/post-dispatch отмена; pause и restart только после проверки pinned manifest; присвоение `unknown`; stale lease recovery; terminal/restart denial; workspace→run→attempt lock order; отсутствие provider импортов и платных операций.
- `tests/test_sre_live_recovery_schema.py`: SQL FK, no-delete, immutable marker, generation/token guards, preflight next prefix `057`.
- Обновлён `tests/test_sre_live_lease.py`: M14 workspace-share транзакционный порядок.
- `tests/test_sre_offline_pg.py`: реальный disposable PostgreSQL17 C9 H1/H2+D1/S3: active claim→pause→reconsent, idempotent cancel, protected stale token; падение до отправки (`reserved` → abandon/reclaim); после `dispatch_intent` → run `unknown`, нет авто retry; два параллельных соединения cancel vs claim; никакого `sre_classification_results`.
- `tests/test_sre_migration_guard.py`, `test_sre_migration_guard_pg.py`, `test_sre_live_schema.py`, `test_sre_live_coverage_schema.py`: fresh install, upgrade с main-only, immutable 016/017 и next 057.
- `.github/workflows/sre-contracts.yml`: M15 unit Linux/Windows + существующий PG17 и полный regression.

Команды для отдельного disposable PostgreSQL17:

```sh
python -m unittest discover -s tests -p test_sre_live_recovery.py -v
python -m unittest discover -s tests -p test_sre_live_recovery_schema.py -v
python -m unittest discover -s tests -p test_sre_live_lease.py -v
CM_TEST_PG_DSN='<DISPOSABLE_POSTGRES17>' python -m unittest discover -s tests -p test_sre_offline_pg.py -v
```

**Приёмка:** наличие коммитов не доказывает PASS. Не закрывать QA, пока exact final SHA не проверен на GitHub Actions с реальными завершёнными результатами. Прежние M01–M14 acceptance также открыты.

**Следующий M16 (только по отдельному запросу):** локальный retrieval/embedding без автоматических платных вызовов, с обязательными pinned C9/D1 и дальнейшей связкой с Opus/провайдерами лишь после финансовых ворот.
