# Content Machine — Search Planner: единый план и журнал выполнения
**Назначение:** это канонический файл для любого следующего чата. После каждого этапа **в той же рабочей ветке** обновить этот файл коммитом. Отмечать выполненное зачёркиванием `~~...~~` **только после фактической реализации и требуемого QA**, а не после написания документации.

**Репозиторий:** `BanSins7/ContentMachine`  
**Единственная SP-ветка:** `feature/search-planner-full-integration`  
**Черновой PR:** https://github.com/BanSins7/ContentMachine/pull/34 — не сливать до полного release gate и согласования владельца.  
**Не редактировать:** `main`, `feature/video-collector-dev-20261023`, `feature/rtb-c-corrector-v2`.  
**Правила:** один целостный SP внутри ContentMachine, без обязательного Collector-кода; никакой платной LLM без отдельного согласия, никаких правок применённых SQL-миграций. Внешние зависимости отмечать отдельно, не считать их реализованными.

## Этапный чеклист (порядок неизменен)

- [x] ~~**SP-00 — инвентаризация / freeze контрактов**: точные refs main/SP/C/Collector, актуальная SQL-карта, QA-baseline и ADR.~~ Выполнен (документы: `SEARCH_PLANNER_INTEGRATION_MAP.md`, `SEARCH_PLANNER_INTERFACE_MATRIX.md`, `SEARCH_PLANNER_SP00_BASELINE_QA.md`, `SEARCH_PLANNER_MODULE_ADR.md`; завершение на `195aff78`). Это **документальный freeze**, не runtime release.
- [ ] **SP-01 — полноценный модуль-фасад и dependency ports**: `service.py`, `api_contracts.py`, `configuration.py`, `ports.py`, `__init__.py`; контрактные тесты, старые V1 exports, Collector-free imports, зелёный CI на проверяемом HEAD. **Код реализован** (последний кодовый коммит `3fc56b66`); QA на этом SHA был поставлен в очередь, не подтверждён успешным. Файлы: `docs/SEARCH_PLANNER_STAGE01_FOUNDATION.md`.
- [ ] **SP-02 — доверенный SP1/SP2 C9 reader + HypothesisContext V1/V2**: проверить source row, два Review, published final tree, version/identity, исключить silent 14→11 truncation, tests v1. Включать реальный V2 только после появления upstream C9 Deep V2 schema/publication (PR #31 первоначально только freeze). **В работе с 26.10.2026.**
- [ ] **SP-03 — Generation Run/Batch/Quote/Budget/Consent**: server idempotency + exact pinned config, операции только после отдельного user consent; тесты.
- [ ] **SP-04 — Paid worker и recovery**: supervised queue/lease/one POST, unknown billing hold, durable restart, полный ledger.
- [ ] **SP-05 — AI Query + Output Manager**: pinned schema/model, research query traces, один completion на batch, full coverage/sqg_outbox.
- [ ] **SP-06 — Production semantic embeddings**: локальные multilingual weights, версии/digest, no silent download, реальная semantic provider; fixture запрещён в production.
- [ ] **SP-07 — Relevance/Dedup/MMR**: детерминированный selection, reject reasons, множественные связи query→hypothesis.
- [ ] **SP-08 — Validated Registry и QV Outbox**: project-scoped DB, per-candidate decisions, idempotent consumer, fail-closed concurrency.
- [ ] **SP-09 — Tool Capabilities Port/Matching**: внешний versioned capability snapshot, без копии Registry и без реального provider dispatch.
- [ ] **SP-10 — Trusted Task Planner**: preview, quotas/costs/block reasons/coverage, dedup без потери связи с гипотезами, immutable plan hash.
- [ ] **SP-11 — SP8→SP11 projection / chunking**: DB-verified QV/C9 SHA, original candidate IDs, 117→50+50+17, 0→no_work.
- [ ] **SP-12 — Durable SP inbox / optional Collector adapter**: атомарный QV→SP ACK, отдельная downstream receipt, replay/race/crash QA, awaiting_collector без Collector.
- [ ] **SP-13 — Owner HTTP API**: project ACL, idempotency, safe GET, bounded bodies, no unauthorised paid calls.
- [ ] **SP-14 — User Interface**: полный Search Planner UI внутри ContentMachine, budgets/approvals/runs/validation/plan/handoff.
- [ ] **SP-15 — Operations**: worker supervision, monitoring, logs redaction, secure recovery, Windows/Linux.
- [ ] **SP-16 — Полный release gate**: fresh/upgrade PostgreSQL, all-up E2E + mock Collector, Windows/Linux CI, 0 implicit spend, self-contained mergeable PR; **без автоматического merge**.
- [ ] **SP-17 — feedback от реальных Collector→Discovery→SRE результатов**: measured feedback contract, настоящий анализ только после появления downstream фактов; внешняя зависимость, не подменять mock данными.

## Протокол следующего чата

1. Сначала открыть **этот файл** из **SP-ветки** и проверить branch HEAD, PR #34, CI, состояние PR #31.
2. Открыть подробности задачи в `docs/SEARCH_PLANNER_STAGE_EXECUTION_ROADMAP_RU.md` и `docs/SEARCH_PLANNER_STANDALONE_MODULE_RELEASE_PLAN.md`.
3. Делать код, DB migration, тесты только в SP-ветке. Не переносить чужие Collector files и не менять main.
4. Запустить доступный offline/PG QA; проверить GitHub Actions на **точном SHA**. Не сообщать PASS, если queued/in_progress/not run.
5. По окончании этапа обновить именно этот чеклист: `- [x] ~~**SP-XX ...**~~` и добавить ссылку на commit/CI. При частичной работе оставить `- [ ]`, подписать реальный статус и блокер.
6. Зафиксировать SHA `main` и SP-ветки в финальном отчёте. Ни одно выполненное условие не должно зависеть от неслитой ветки Collector.

## Журнал проверок

- **SP-00:** завершён как аудиторская/документальная работа; source of truth — документы СП-00.
- **SP-01:** код foundation + unit tests/CI workflow уже в SP-ветке, но до проверки точного HEAD CI статус оставлен незавершённым. Не объявлять весь SP-01 релизом.
- **SP-02:** текущая работа. Legacy C9 V1 реализован ранее; запретить silent acceptance будущего Deep V2 и укрепить actual publication lineage. V2 не включать без реальной upstream публикации.
