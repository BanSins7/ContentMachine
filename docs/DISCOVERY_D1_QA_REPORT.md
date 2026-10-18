# ContentMachine — Discovery D1.0–D1.7 / delivery and QA

**Status:** код D1.2–D1.7 опубликован; окончательные результаты CI сверять по актуальному SHA `main`. Проверка Windows пользователя — отдельный gate, не заявлять PASS без его ответа. Дата: 2026-10-18.

## Реальная поставка

| Этап | Файлы | Что можно проверить |
| --- | --- | --- |
| D1.0 | уже существующая PostgreSQL 17, GitHub CI | База подключена по сообщению пользователя, пусты videos/jobs/sources на момент проверки |
| D1.1 | `002_discovery.sql`, `test_discovery_schema.py` | 10 новых таблиц, source rights deny-by-default, FK, retries и индексы |
| D1.2 | `intelligence/discovery/domain.py`, `normalize.py` | typed Candidate, strict HTTPS/platform IDs, datetime UTC, null vs zero |
| D1.3 | `service.py` | PostgreSQL upsert, identity/occurrence dedup, provenance, trace, safe transactional per-chunk write |
| D1.4 | `importers.py` | URL lines + UTF-8 CSV, ≤10 000 rows, ≤2 MB, no external HTTP, persisted 50-row jobs |
| D1.5 | `http_api.py`, `app.py` | GET sources/runs/videos/trace/diagnostics, POST import/preview/labels/cancel |
| D1.6 | `site/index.html`, `site/app.js`, `site/discovery.js`, `site/style.css` | новый Discovery sidebar, импорт, статусы, карточки, «История», KPI |
| D1.7 | `tests/test_discovery_pipeline.py`, `kpi.py`, GitHub Actions | 10k synthetic candidates + reimport, rejection, rights, old worker lease recovery, regression |

**Прямые ограничения:** никто ещё не собирает реальных роликов из чужих платформ автоматически; YouTube/TikTok/Instagram внешние адаптеры — D2/D3. GPU-анализ, подписки/платные запросы, автоматический мониторинг, качество Shorts и ML-ранжирование не включены. Пользовательский контент хранится в локальной PostgreSQL, не публикуется в GitHub. Право хранить метаданные предоставляется **только явно при ручном импорте**, а право на метрики от manual-источника не предоставляется. Реальная частота обновлений и retention сторонних источников требуют отдельного гейта D2+.

## QA: регрессия и нагрузка

- `python -m unittest discover -s tests -v` в CI PostgreSQL 17: unit, database migration, old API, source rights и новые D1 тесты.
- Сквозной synthetic load: **10 000 уникальных видео + второй импорт тех же 10 000**, проверка количества новых (10k/0), дублей (0/10k), 400 durable 50-row jobs, журналы по двум run_id.
- Записи с отсутствующей published_at, неподтверждёнными Shorts и без человеческой метки релевантности **не входят** в `qualified_fresh`. Временный прогноз или синтетические фикстуры нельзя выдавать за реальную цель 10k/мес.
- Cancel test: отменённые задания не загружаются.
- Expired lease test: воркер перехватывает протухшую задачу и увеличивает lease_generation.
- Обработка нарушения прав: rollback всей страницы, retry после восстановления разрешений.
- JS syntax: `node --check site/app.js && node --check site/discovery.js`.
- Python syntax: `python -m compileall -q app.py worker/ intelligence/`.

## Локальный Windows smoke — пользователь выполняет после Fetch/Pull

1. `python -m intelligence.migrate` — вывести версию `002_discovery.sql` и `No pending migrations.`.
2. `python -m intelligence.health` — `database=connected`.
3. Открыть `http://127.0.0.1:8765`, открыть вкладку **Discovery Engine**.
4. Вставить реально известную HTTPS ссылку на ролик либо использовать тестовую синтетическую (она **не будет проверяться сетью**); нажать «Добавить в базу». Дождаться завершения run после нескольких секунд. Проверить карточку и «История».
5. Вставить ту же ссылку снова: новый run, старый video-ID в БД, `duplicate=1` и `new=0`.
6. Во время большого импорта остановить и перезапустить `RUN_AUTO.bat`: задания с истекшей арендой должны возобновиться; на текущей реализации recovery может ждать до 120 секунд.
7. Проверить, что все три существующих загрузчика открываются и работают по своим прежним API.
8. Проверить, что вручную импортированные записи не создают ложных метрик просмотров и не увеличивают `qualified_fresh` без даты публикации и метки темы.

### Команды

```powershell
cd C:\ContentMachine
git pull --ff-only origin main
python -m intelligence.migrate
python -m intelligence.health
```

Без резервного копирования PostgreSQL — согласно явному решению пользователя. Миграции аддитивны, `001_foundation.sql` не меняется.

## Open items (не скрывать)

- Windows smoke пока требует подтверждения пользователя.
- Это проверка **offline pipeline**, а не live QoS у TikTok/Instagram/YouTube.
- Нагрузочный тест на GitHub-hosted runner и на ПК Windows могут иметь разную скорость.
- `can_search`, budget gate и storage retention сторонних sources пока выключены; реализация дальнейшего использования — D2–D5.
- D1 import является локальной очередью с 1 worker; несколько параллельных самостоятельных воркеров и heartbeat с длинными внешними запросами относятся к D4.
