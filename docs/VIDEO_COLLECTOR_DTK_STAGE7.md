# Video Collector Stage 7 — локальный Evil0ctal DTK v5

## Реализовано

В ветку `feature/video-collector-v1` адаптированы файлы из самостоятельного PR #13 (не скопирован сам upstream DTK):
- `intelligence/connectors/dtk.py`: 12 whitelist read-only операций, локальный HTTP клиент, API-key только на backend, no-proxy/no-redirect, async task submit/status, ограничение длины и таймаутов;
- `intelligence/connectors/http_api.py`: ручные эндпоинты `GET /api/tools/dtk/status`, `POST /api/tools/dtk/run`, `GET /api/tools/dtk/tasks/<uuid>`;
- `site/dtk-tool.js`, разметка и CSS `Discovery Engine → TikTok Tool`: формы всех 12 операций, явный submit, ручная проверка и polling существующего task id, без повтора submit;
- `intelligence/collector/adapters/dtk_tiktok.py`: `prepare_keyword_task()` строго связывает approved-like task DTO с `dtk.validate_action("search_posts")`; не принимает дополнительные поля/неподдержанные `hashtag`/pagination, никогда не отправляет запрос;
- `intelligence/collector/registry.py`: DTK теперь **установленный локальный коннектор** (`local_rest_connector_present_manual_only`), а не ещё не включённый PR;
- `GET /api/intelligence/collector/sources/dtk_tiktok/diagnostics`: явная диагностика доступности локального DTK посредством **одного разрешённого GET `/api/v1/auth/me`**, без TikTok keyword search или импорта.
- `GET /api/intelligence/collector/sources` по-прежнему работает полностью **без сетевого обращения**.

## Граница между Manual TikTok Tool и автоматическим Collector

`POST /api/tools/dtk/run` отправляет ручной запрос **только после явного действия пользователя и конфигурации `CM_DTK_ENABLED=1`**. Он не связан с `collector_runs`. У ручной операции нет автоматической записи результатов в Discovery и нет разрешения на скачивание медиа.

`POST /api/intelligence/collector/runs` по-прежнему создаёт **только** `collector.search.held` jobs с `run_after='infinity'`. Stage 7 не внедряет worker, не снимает hold, не запускает search из планировщика, не списывает деньги и не меняет source policy. Статус `keyword_search_transport_ready=true` подтверждает **только** локальную auth/read доступность DTK; он **НЕ** подтверждает реальные TikTok результаты, лицензию, пагинацию, upstream archive setting или финансовый budget.

## Настройка

DTK ставится отдельно, с проверенной pinned версией/коммитом. См. `docs/DTK_CONNECTOR.md`. Заполнить gitignored локальный `.env` ContentMachine:

```dotenv
CM_DTK_ENABLED=1
CM_DTK_BASE_URL=http://127.0.0.1:8000
CM_DTK_API_KEY=local_read_scope_key
```

**Никогда не сохранять реальный ключ в репозитории, чате, логах или браузере.** Минимальный scope: `tiktok:read`. Проверить и, если хранение не разрешено, отключить upstream `archive.enabled` **до** первого ручного поиска; DTK может архивировать данные сам по себе независимо от отсутствия импорта ContentMachine. Наличие Apache-2.0 лицензии на программу DTK **не разрешает** коммерческий TikTok scraping/data storage.

Запустить локальное приложение и открыть `Discovery Engine → TikTok Tool → Проверить подключение`. Это делает auth/me, не search. Пользовательский submit любой из 12 операций — отдельное явное действие, возможно с ограничениями аккаунта TikTok (guest-пагинация не гарантирована).

## QA

```bash
python -m unittest discover -s tests -p 'test_dtk_connector.py' -v
python -m unittest discover -s tests -p 'test_video_collector_dtk_stage7.py' -v
python -m unittest discover -s tests -v
node --check site/dtk-tool.js
python -m compileall -q intelligence/connectors/ intelligence/collector/ app.py
```

Тесты не используют реальные TikTok/DTK/OpenRouter credentials и не делают живые TikTok запросы. Для production smoke необходимо отдельное локальное развертывание DTK и проверка разрешений.

## Следующий этап

8 — специализированный Collector Worker. Только после отдельной проверки publisher Query Validation, source policy, расходов, idempotent external submit-intent и ручного согласия. Нельзя вызывать ручной `POST /api/tools/dtk/run` из backend worker.
