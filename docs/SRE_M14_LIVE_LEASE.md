# SRE M14 — LIVE Worker Claim, Lease, Heartbeat and Fencing

**Дата:** 2026-10-26 · **Ветка:** `feature/sre-production-v1-20261023` · **Draft PR:** [#22](https://github.com/BanSins7/ContentMachine/pull/22).

**Состояние:** код и тесты внесены; фактический SUCCESS GitHub Actions пока не подтверждён. SRE LIVE остаётся **без платного вызова**, секретов модели, бюджетного списания и реального исполняющего worker. Этот этап НЕ активирует `pending_consent` и НЕ даёт согласие на траты.

## Контракт

Новый модуль `intelligence/relevance/live_lease.py` использует **уже существующие** колонки `sre_live_items.worker_token`, `lease_generation`, `lease_until`, `attempts`. Новая миграция не нужна; исторические 016/017 и LIVE 054/055 не переписываются. Вызовы предназначены только для авторизованного внутреннего SRE worker service; проектный ID — не доказательство аутентификации пользователя.

- `claim_live_item(conn, project_id, lease_seconds=60)`: атомарно выбирает **один** подходящий элемент из полностью созданной M13 C9-парной матрицы через `SELECT ... FOR UPDATE OF i SKIP LOCKED`, порядок `run_id,video_id,item_id`. Допускает `item.state=queued` или `running` с просроченным lease. Только если `run.execution_mode=live`, `run.state IN (queued,running)`, `cancel_requested=false`, batch queued, нет blocked/отсутствующих ожидаемых пар и попыток уже менее 100. Атомарно устанавливает свежий случайный UUID, `lease_generation+1`, время окончания по `clock_timestamp()` на сервере PostgreSQL и увеличивает счётчик попыток. Нет подходящего — `None`, не fallback на чужой project.
- Сразу **в той же транзакции** M08 `read_live_content_gate(phase=before_external_send)` проверяет реальные текущие D1/S3 права, внешний LLM grant, источник, observation expiry, неизменяемый video/content SHA. Если права отозваны или версия подменена, lease снимается, элемент переходит в `paused_rights`, токен клиенту не выдаётся. Неизвестные ошибки — rollback всей операции. Успешный claim содержит **`dispatch_allowed=False`**: это lease на планирование будущей работы, а не бюджет, не право на отправку и не вызов провайдера.
- `heartbeat_live_item(conn, project_id, item_id, token, generation, lease_seconds=60)`: одна атомарная SQL CAS UPDATE по project+item+текущему UUID+generation, `lease_until > clock_timestamp()`, `state=running`, run active and not cancelled. Нельзя воскресить истёкшую, отозванную, заменённую или отменённую аренду. Нулевое число обновлённых строк → `SREContractError(code=lease_lost)`.
- `release_live_item(conn, project_id, item_id, token, generation)`: та же проверка CAS, сбрасывает token/expiry и возвращает item в `queued`; следующий claim увеличивает generation. Чужой или просроченный token не может освободить новый worker. Никаких labels или результата от release не создаётся.

**Важная граница M14:** будущая платная отправка должна *снова* в своём атомарном fenced-dispatch участке проверить consent/budget, lease, run cancellation, источник (M08) и свой ledger. `dispatch_allowed=False` жёстко подчёркивает, что M14 не реализует эти будущие финансовые и провайдерские механизмы. Прямая ручная SQL смена `pending_consent` в `queued` используется **только в тестовом PG17 fixture**; в production эту операцию должен выполнять будущий одобренный и аудируемый механизм, которого на M14 ещё нет.

## Свойства безопасности

1. Один item не может одновременно быть выдан двум worker: `FOR UPDATE SKIP LOCKED` + атомарный `UPDATE RETURNING`; при конфликте кандидата можно пропустить, не блокируя весь пул. Отбор стабильный по ID.
2. ABA/stale token: каждый новый claim инкрементирует `lease_generation` (включая повторный захват после истечения). Token уникален; CAS heartbeat/release проверяет **оба** значения и действующий lease по часам СУБД.
3. Не возникает бесконечного paid auto-retry: нет платного кода вообще; максимум 100 технических захватов, что не даёт права ни на один LLM POST. `unknown charge` из M10 по-прежнему нельзя трактовать как разрешённую повторную отправку.
4. Отозванные права или истекшая source observation переводят элемент в `paused_rights` без выдачи токена. Последующий ручной re-enable — будущая работа; не происходит автоматического возврата в очередь.
5. Кросс-проектные обращения, неверный UUID/boolean/integer, lease менее 5 или более 300 секунд и устаревшая generation отклоняются. Heartbeat/release ничего не меняют при отменённом или приостановленном родительском run.

## QA

- `tests/test_sre_live_lease.py`: no-DB/paid/provider unit suite — strict types, zero/missing claim, revocation, token/generation CAS, expiry SQL, lock order, `dispatch_allowed=False`, import без psycopg/provider.
- `tests/test_sre_offline_pg.py`: настоящий disposable PostgreSQL17, approved C9 + проверенные D1/S3 и пакеты M13. Проверяет отсутствие claim при `pending_consent`; два item/две worker; heartbeat; освобождение и новый token/gen; forced expiry с отказом старому токену; запрет heartbeat/release через другой проект; два конкурентных независимых PostgreSQL connection через `SKIP LOCKED`; отзыв D1 policy → `paused_rights` без токена; отсутствие классификационных строк.
- `.github/workflows/sre-contracts.yml`: Linux + Windows pure M14 suite и имеющиеся PostgreSQL17 integration/upgrade jobs. Не запускать оплачиваемых провайдеров.

```bash
python -m unittest discover -s tests -p test_sre_live_lease.py -v
CM_TEST_PG_DSN='<DISPOSABLE_POSTGRES_17>' python -m unittest discover -s tests -p test_sre_offline_pg.py -v
```

Локальная сеть контейнера не позволила загрузить репозиторий из GitHub (DNS); поэтому **не было фактического локального запуска**. Только GitHub Actions по точному коду являются источником QA PASS. Ни `queued`, ни наличие тестовых файлов не означает, что тесты прошли.

**Следующий этап:** M15 — cancel/pause/restart и race-safe invalidation, только по следующему запросу. M14 не выполняет часть этого этапа.
