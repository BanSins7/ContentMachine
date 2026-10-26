# SRE M01 — безопасная синхронизация main в автономную ветку

Дата: 2026-10-26. Применено только к ветке `feature/sre-production-v1-20261023`, PR #22. `main` не менялся.

## 1. Проверенная точка синхронизации

| Объект | SHA |
|---|---|
| main (проверен перед merge) | `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` |
| SRE до merge | `658f59b3e718b715544f5324a6dffa328bf03fb2` |
| Двухродительский merge-коммит | `09a8c7542abafa7c856e3555581a70ea52b87796` |
| Объединённое дерево | `7e68c83f12c358f6996bc4fd60f708634ef5383f` |

Родители merge: 1) SRE до merge, 2) main. Не использовались force push, замена ветки, cherry-pick/копирование чужих PR либо merge в main. Ветка сдвинута только fast-forward, с ожидаемым старым HEAD.

## 2. Проверка трёхстороннего конфликта

Общий предок: `4e51de5fc59f52d83e4fa71492d2d767689cd019`.

GitHub сравнение показало **32 специфичных изменения SRE**: новые SRE-файлы и миграции плюс только две правки относительно общего предка в `intelligence/relevance/storage.py` и `tests/test_relevance_storage.py`. Для обеих правок SHA в актуальном main **совпадал с общим предком** — отсутствие изменений upstream по этим двум файлам доказано.

Стратегия без перезаписи данных: взять полный tree main + ровно 32 проверенных SRE blob SHA. Ни одна SRE-добавка не совпала путём с файлом main. Это эквивалент разрешённого безконфликтного трёхстороннего merge по файлам. Создан обычный коммит с двумя родителями, не синтетический переписывающий историю reset.

После merge `compare main...SRE` вернул status=`ahead`, ahead=76, behind=0, отличия = те же 32 SRE-файла, **0 чужих изменённых файлов**. Ветку основного приложения не затрагивали. Upstream `app.py`, `site/index.html`, оба существовавших CI-workflow получили версии main (не старые варианты SRE).

## 3. Модули и миграции в объединённом дереве

- Исторические SQL SRE `016_sre_d1_intake_rights.sql` и `017_sre_offline_run_engine.sql` остались неизменными (Git blob SHA `d77350188169f4aeff9379d8075dc4d4ea6707de` и `21f80e816d3c6f87c4624461be07410136c21ad5`).
- Из main сохранён `016_query_validation.sql` и более новые PSI/Knowledge migrations `030–053`, включая `053_ke_c_qualified_execution.sql`.
- `intelligence/migrate.py` — исходный checksum-guarded лексический применитель миграций из main; не менялся.
- Пакет `intelligence/search_planner/query_validation` и интерфейсы A/B доступны в объединённой ветке.
- **Не импортировали** никакие незамерженные Collector, QDP, Analytics, Group A или C v2 файлы. Их совместимые вход/выход останутся работой M35–M37.
- Старые S0–S4 в SRE сохранены побайтово. Ни одна fixture, SRE runtime, SRE workflow или миграция SRE на merge не переписаны.

## 4. Реальные проверки GitHub Actions (отдельно фиксировать финальный статус)

Workflow CI запускаются GitHub автоматически после двухродительского merge.

| Проверка | GitHub Actions |
|---|---|
| Полный Linux/Python + PG | https://github.com/BanSins7/ContentMachine/actions/runs/40289331759 |
| Windows D1 smoke + integration | https://github.com/BanSins7/ContentMachine/actions/runs/40289331770 |
| Целевой SRE S1–S4 Linux+Windows+PostgreSQL17 | https://github.com/BanSins7/ContentMachine/actions/runs/40289331842 |
| Knowledge Extraction B Stage14 на объединённой базе | https://github.com/BanSins7/ContentMachine/actions/runs/40289331751 |

**Важно:** запись `QUEUED`/`IN_PROGRESS` не является PASS. Ниже M01 может быть помечен DONE только после наблюдаемого завершения всех обязательных Linux/Windows/PG регрессионных проверок на merge-коде. Исторические CI на старом SRE SHA к M01 не относятся.

## 5. Ограничения, которые не устраняет M01

- Синхронизация Git деревьев и тестовый PG17 **не подтверждают** схему настоящей пользовательской/production базы. Перед применением миграций на ней обязателен просмотр `schema_migrations`, согласование checksum и резервная копия.
- На данном этапе автономный SRE НЕ получил HTTP/SRE Studio, LIVE Opus, ledger, локальное ранжирование всех гипотез или внешние Collector/QDP потребители.
- PR22 по-прежнему Draft. Готовность к главному merge при M47 проверяется заново на **новом** main SHA (main может измениться).
- Нельзя считать `execution_mode=offline_fixture` семантическим LIVE, даже если S4 run.status='completed'. QDP всё ещё требует собственного LIVE gate.

## 6. Следующий этап

Только по запросу владельца: **M02** — самостоятельный versioned manifest SRE, capabilities и строгие inbound/outbound порты, которые импортируются без Collector/QDP/Analytics, с typed ID/schema negative tests.
