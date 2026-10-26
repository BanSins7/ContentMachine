# VC-MOD-00 — интеграционный аудит самостоятельного Video Collector

Дата аудита: 2026-10-26. Целевая ветка: `feature/video-collector-v1`. Один открытый Draft PR: [#33](https://github.com/BanSins7/ContentMachine/pull/33).

## Проверенные исходные ревизии

| Источник | SHA / статус | Использование |
|---|---|---|
| main | `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` | Основная база с PSI #29, KE #30 |
| VC V1 (до этого этапа) | `d56037fdf21bf554a95b77d73400da4852fa90c5` | Main + Collector Stage 2–9 |
| Старый Collector PR #20 | `a4f658de77fb82de2437474e60c2b917ae2a9811`; open/draft | Только исторический источник, больше не сливать |
| DTK PR #13 | open, не объединён | Его transport уже включён в VC |
| SRE PR #22 | open/draft, развивается отдельно | VC не копирует SRE-код |
| QDP PR #24 | open/draft, развивается отдельно | Не является частью VC |

Перед каждым новым VC-MOD этапом сверять новые SHAs; они могут изменяться.

## Комплектность 42 файлов исходного diff VC vs main

1. `intelligence/collector/*` — контракты, parser, registry, planner, policy gate, repository, execution, dedicated worker, строгий inspector, API, DTK adapter, preview CLI и package initializers.
2. `intelligence/connectors/{dtk.py,http_api.py,__init__.py}` — единственный локальный DTK transport и ручная диагностика.
3. `intelligence/discovery/http_api.py`, `intelligence/config.py` — узкий D1 route adapter, локальные настройки.
4. `intelligence/migrations/054_collector_planning.sql`, `055_collector_worker.sql` — план/worker; не дублируют `016_query_validation.sql`. Main 030–053 сохранены.
5. `app.py`, `site/{index.html,style.css,dtk-tool.js}`, `.github/workflows/tests.yml` — узкие overlays над свежим main.
6. `tests/test_video_collector_*.py` и `tests/test_dtk_connector.py` — тесты Stage 2–9, включая базовые интеграционные регрессии.
7. `examples/video_collector_offline_preview.json` — только изолированный offline preview.
8. `docs/DTK_CONNECTOR.md`, `docs/VIDEO_COLLECTOR_*.md`, `docs/DOCS_INDEX.md` — документация; старый `VIDEO_COLLECTOR_PIPELINE_V1.md` не включён как устаревший roadmap.

Ни один файл PSI, KE, Search Planner, approved C9 или старой D1 таблицы не заменён устаревшей версией при первоначальном объединении.

## Версионированные границы и блокеры

| Граница | Контракт | Состояние |
|---|---|---|
| RTB/SP -> VC | Main `qv_outbox` и `validated_queries/v1` | Upstream готов, trusted VC converter/publisher отсутствует (VC-MOD-03/04) |
| VC registry/worker -> DTK | `dtk_tiktok: keyword -> search_posts` | Код Stage 7–9 в ветке; настоящий TikTok E2E ещё не проверен |
| Inspector -> Discovery D1 | D1 `videos`, `discoveries` | **Нет** транзакционного ingest/checkpoint (VC-MOD-09/10) |
| D1 query identity | legacy BIGINT, QV candidate UUID | UUID остаётся в `collector_task_queries`, никакого UUID→BIGINT cast (VC-MOD-02/09) |
| VC -> SRE | project/video/snapshot versioned event | **Нет** долговечного SRE-ready outbox (VC-MOD-11); SRE работает независимо |
| SRE -> QDP | реальные подтверждённые классификации | VC не выполняет semantic match и не создаёт qualified |
| database migrations | Lexicographically ordered full filenames + sha256 | 054/055 после 053; clean PG17 и upgrade ещё должны быть подтверждены |

## QA и критерии

- `python -m compileall -q app.py intelligence/collector intelligence/connectors`
- `python -m unittest discover -s tests -p 'test_video_collector_integration_baseline.py' -v`
- `python -m unittest discover -s tests -p 'test_video_collector_*.py' -v`
- `python -m unittest discover -s tests -p 'test_dtk_connector.py' -v`
- `python -m unittest discover -s tests -v`
- `node --check site/dtk-tool.js`
- CI PostgreSQL 17: `CM_DB_DSN=postgresql://...DISPOSABLE... python -m intelligence.migrate`; штатный PostgreSQL service GitHub Actions.
- CI Linux ContentMachine, KE Stage14, Windows D1 smoke.

Важно: факт установки SQL на production DB не выводится из GitHub; для ранее применённых legacy 016/017 требуется отдельное согласование миграционной истории. Не считать queued/in_progress Actions статусом PASS.

**VC-MOD-00 означает сборку и сверку базовой кодовой базы; не означает завершение runtime E2E.** Следующий VC-MOD-01 определит стабильные внешние порты, а VC-MOD-02 добавит обязательные сохраняемые структуры.
