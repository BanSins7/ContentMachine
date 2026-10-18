# ContentMachine — повторный аудит качества D1.2–D1.7

**Дата:** 2026-10-18  
**Проверенный код:** `4b32d9c9a12bd24e35eb3ff5b99e295634f2ee61` (main, до этого отчёта).  
**GitHub Actions:** https://github.com/BanSins7/ContentMachine/actions/runs/39138160048  
**QA:** **PASS, 113 тестов / 128.659 s**, PostgreSQL 17, Python unit/integration, локальные HTTP API, JS syntax и Python compileall.  
**Нельзя путать:** CI на Linux runner и пользовательский Windows smoke — это разные доказательства. D2 реальный поиск не включён.

## Вердикт

**D1.2–D1.7: исправлен и прошёл автоматический QA.** Без локального Windows smoke **не подтверждён окончательно как работающий на ПК пользователя**. Внешний массовый Discovery не включён. Готов к проверке на ПК и затем к подготовке D2, с отдельным gate на хранение/удаление данных поставщика.

## Существенные находки аудита / изменения

| Код / зона | Обнаруженная проблема | Исправление и новая проверка |
|---|---|---|
| `service._ingest_one` | URL-only повторный импорт перекрывал заголовок/дату пустой source metadata | Не создаём пустую metadata при URL-only; непустые поля наследуют уже разрешённую прежнюю metadata этого же источника. `test_reimport_url_does_not_erase_title_or_publication` |
| `service.work_once` / `cancel_run` | Гонка: отмена могла произойти между проверкой cancel и SQL commit | `SELECT ... FOR UPDATE` на run, сериализация транзакций; после cancel нельзя записать новый пакет; сохранение `finished_at`. `test_cancelled_running_job_cannot_write_after_lease_recovery` |
| `service._claim` | Задание, истёкшее на последней попытке, могло навсегда оставить run running | Перевод окончательно истёкшего job в failed + пересчёт run. `test_exhausted_lease_marks_run_failed_instead_of_hanging` |
| `service.list_videos/video_trace/diagnostics/kpi` | При отзыве прав нельзя было гарантировать блокировку последующего чтения | Read gate теперь проверяет source.status, can_store_metadata, active policy; KPI скрыт при отзыве. Тесты revocation/status |
| `http_api / sources` | `GET /sources` всегда показывал источник активным | Источник выводится из фактической PostgreSQL policy, не константа. Тесты status |
| `importers/normalize` | CSV `NaN`/Infinity мог сломать весь импорт JSONB; пустая optional platform отклонялась | Валидируем duration отдельно по записи, ошибки получают reason; пустая platform выводится из URL. Тесты finite/NaN и blank optional platform |
| `data_quality_flags / discoveries` | Невозможно найти номер строки с ошибкой или входной позиции | `row_number` фиксируется в safe JSON и `source_rank`, UI раскрывает номер строки; без хранения raw URL в диагностике. Тест rejected row 2 |
| `run_detail / UI` | Ошибки worker скрыты за общим количеством, без кодов | Sanitized `job_errors` с кодами, отображение в деталях run |
| `site/discovery.js` | Периодический опрос стирал пагинацию каталога | Auto-refresh сохраняет страницу после «Показать ещё», но обновляет первые 50, если пагинация не расширена |
| `tests/` | HTTP-тест запускал daemon worker, мешающий последующим тестам; 10k cleanup мог затронуть слишком широкий ID range | Изолированный HTTP-worker и run-scoped cleanup с уникальным namespace; конкурентный test двух worker |
| `service` batch | Не было проверки, что ошибка внутри пакета откатывает все записанные данные | Transaction rollback + retry, проверка `test_mid_batch_db_failure_rolls_back_and_safe_retry` |
| `kpi` | Границы UTC календарных месяцев не были отдельно проверены | Новый тест 29 февраля високосного года и December→January |

## Инварианты после аудита
- `NULL != 0`; нет фиктивных views/shares/Shorts-confirmed.
- 10k synthetic URL → 10k новых identities; повторить те же 10k → 0 новых и 10k duplicate occurrences внутри своего run; сама история находок сохраняется.
- Повторное получение URL не скрывает валидные title/published_at.
- SQL constraints + PostgreSQL transaction обеспечивают atomic batch и уникальность ID.
- Задание в `jobs` не может быть успешным без commit; expired/retry и cancel имеют проверенные ветки.
- UI и API не запускают внешние/платные collector; ручной импорт не извлекает статистику из платформ.
- KPI `qualified_fresh` **равен нулю в D1**: дата/тематика из ручного CSV — не внешнее подтверждение. Есть отдельный `manual_labeled_fresh`.
- Право на сохранение статистики для manual-local выключено; read source blocked после revoke.

## QA доказательства
- Кодовый commit: `4b32d9c9a12bd24e35eb3ff5b99e295634f2ee61`.
- Workflow run: https://github.com/BanSins7/ContentMachine/actions/runs/39138160048 (success).
- Журнал тестов: `Ran 113 tests in 128.659s; OK`.
- `test_end_to_end_import_dedupe_restart_and_trace`: 10 000 synthetic + 10 000 replay, CI PASS.
- `test_live_local_http_import_trace_and_auth`: проверены локальные HTTP endpoints и запрет без спецзаголовка, CI PASS.
- Quality review: `tests/test_discovery_quality_review.py` — revocation, retries, concurrency, metadata, cancellation, bad CSV, bad rows.
- Нет утверждения, что на Windows прошло визуальное smoke QA: не проходило.

## Действительно открытые пункты

**Перед D2 — обязательные:**
1. **Windows smoke:** Fetch/Pull; `python -m intelligence.migrate`; `python -m intelligence.health`; открыть Discovery, импортировать CSV и ссылку, повторить, проверить статус/каталог/«История», отмену и продолжение после restart.
2. **Source-specific compliance:** для реальных внешних источников реализовать сроки retention, проверку разрешений полей, удаление/обновление истёкших данных и read/trace policy. D1 работает только с user-supplied manual source.
3. **Source API live:** mock-тесты не доказывают, что внешний TikTok/Instagram/YouTube API работает. Протокол live QA только в D2/D3 с пользовательским доступом и бюджетом.
4. **UI browser QA:** CI проверяет JS syntax, HTTP и структуру, но не внешний вид/поведение реального браузера на ПК.

**Допустимо отложить до D2–D5:** масштабный Search Planner, provider adapters, хранение metrics snapshots от платформ, advanced rankings, API budgets, отдельный worker supervisor/heartbeat, мониторинг по расписанию. Метрики по реальным платформам запрещены до source rights и проверки terms.

## Инструкция для следующего исполнителя

Взять этот файл, `docs/DISCOVERY_ENGINE_D0_MASTER_SPEC.md`, актуальный SHA main, ветку D1 и QA лог. **Не писать D2 до Windows smoke**; не включать платные API без явного user budget. В D2 повторно использовать нормализацию identity, но policy для внешних данных проектировать и тестировать отдельно. Если UI/worker сломан на Windows — вернуться к D1 и исправить до начала D2.

## Windows acceptance — коротко

```powershell
cd C:\ContentMachine
python -m intelligence.migrate
python -m intelligence.health
```

После GitHub Desktop Fetch/Pull открыть `http://127.0.0.1:8765` → Discovery Engine. Проверить импорт известной публичной HTTPS ссылки, её повтор, «История», метку релевантности и журнал ошибок. Прислать результат.

**Backup:** по явному решению пользователя не выполнялся.
