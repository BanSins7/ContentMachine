# SP-16B — реальный PostgreSQL17 QV→SP Inbox/ACK integration gate

**27 October 2026 · branch `feature/search-planner-full-integration` · PR #34 Draft.**

## Границы проверяемого результата

Production SP12 код `ingest_qv_to_sp_inbox` и `get_sp_handoff_status` выполняется через настоящий `psycopg` в PostgreSQL17, а не фейковый SQL-объект. Применяется точный SQL из миграции `055_sp_handoff_inbox.sql` с реальными индексами, FK, CHECK и иммутабельными триггерами. Внешние upstream `project_registry`, `qv_runs`, `qv_outbox` для данного набора тестов — минимальные таблицы-свидетели в отдельной одноразовой схеме. Источник projection — внутренний проверяемый deterministic mock `synthetic_trusted_source`, НЕ результат production semantic embeddings. Этот test slice не заявляет полную C9→QV production E2E.

## Проверки — код реализован, реальный CI PASS ожидается

- [x] ~~`tests/test_search_planner_stage16_inbox_pg17.py`: одноразовая схема с новым UUID, реальный PostgreSQL17/`055` и авторитетная QV строка (project/run/generation/pointer), проверяемая по проекту.~~
- [x] ~~117 selected QV UUID → точные 50+50+17 сохранённых JSONB chunks и 117 original candidate/hypothesis/source fact hash links; один `qv_outbox.delivered_at`, `delivery_attempts=1`.~~
- [x] ~~Падение после вставки inbox и **до** QV ACK → rollback без частично записанных chunks/links/ACK, повторная обработка разрешена.~~
- [x] ~~Падение после QV ACK, но **до COMMIT** → rollback как inbox, так и ACK, успешный replay.~~
- [x] ~~8 независимых psycopg соединений одновременно: один authoritative SP event, ровно один QV ACK, одинаковые event UUID у всех читателей.~~
- [x] ~~Случай 0 выбранных QV → честный durable `no_work`, 0 chunks, 0 links и 0 Collector receipts, ACK ровно один раз.~~
- [x] ~~Чужой проект и испорченный `qv_outbox.payload_pointer` не могут ACK или создать inbox.~~
- [x] ~~Production immutable PG triggers блокируют UPDATE/DELETE уже сохранённых чанков.~~
- [x] ~~Default `EmptyCollectorAdapter`: внешний Collector не установлен, значит `awaiting_collector`, без fake receipt/accept или provider request.~~
- [x] ~~Новая обязательная CI команда добавлена в `sp-16a-postgres17-real` job, ​`CM_SP16_RUN_PG17=1` установлен автоматически в реальной PG17 service job.~~
- [ ] Фактический `success` именно на актуальном HEAD, работающий runner. `queued` / `in_progress` / `skipped` не равны PASS.

## Обязательные предохранители

Только `CM_TEST_PG_DSN`, причём SQL conninfo обязан содержать database `cm_sp16_test`, только major PostgreSQL 17, opt-in `CM_SP16_RUN_PG17=1`. Используются `CREATE SCHEMA sp16b_<random>` и `DROP SCHEMA` только для тестовой одноразовой схемы. Ни production БД, ни `main`, ни незамерженный Collector код не изменяются. Триггер `no_delete` на SP evidence сохраняется; при тестовом reset допускается `TRUNCATE` **лишь внутри UUID-схемы в disposable DB** (только QA).

## Чем эти проверки НЕ являются

Это **не** live OpenRouter, аутентифицированный Collector receipt, реальная production модель embeddings или end-to-end approved C9. Mock projection необходим только для воспроизводимого наполнения настоящих PG транзакций; нельзя трактовать его как реальные выбранные видео или фактический поиск. Тесты также НЕ заменяют отдельный SIGKILL/WAL recovery, PG17 schema upgrade, owner HTTP/browser и Windows/Linux deployed lifecycle. Глобальный SP16 остаётся открыт.

## Дальше

При первом выполненном CI проверить логи и исправить SQL/fixture расхождения до зелёного результата; затем SP-16C: real HTTP/browser/Windows/Linux smoke, SIGKILL/WAL и integration of authentic upstream QV source. Отдельные внешние блокеры SP-15D/E (реальный provider billing и Collector readiness) открыты.
