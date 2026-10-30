# Group C — GC-MOD-01: аудит и заморозка контрактов C0–C7

**Статус:** IMPLEMENTED as audit/contract specification, **не** реализация scorer / runtime / QDP-consumer. Зафиксировано 26.10.2026. Источник истины: `intelligence/group_c/contract_audit.py`. Любые новые ограничения БД вводить **только отдельными additive migrations** в соответствующих следующих этапах, не редактировать 003/004.

## 1. Сверенные конкретные ревизии

| Источник | Ревизия / путь | Доверенная функция |
|---|---|---|
| GitHub main | `218f5f3163ccc7179a4809ba4386a1d1b6ff80f5` | база независимой ветки |
| D1 facts | `001_foundation.sql`, `002_discovery.sql` | canonical `videos`, `metric_snapshots`, `source_video_metadata` |
| C DB v1 | `003_performance_c.sql` blob `2dac1d9d700df60caf7f7d7ce69abdf6d41b723e` | посты, когорты, конфигурация, результаты |
| C DB integrity | `004_performance_c_integrity.sql` blob `38fa62ed26583bf510b2a067cfd79d7011e258e8` | lineage, frozen, защита завершённого результата |
| C первичная спецификация | `docs/PERFORMANCE_C_DATABASE_V1.md`, Group C full MD/TXT | решения C0–C7 и открытые параметры |
| Analytics PR23 | `feature/analytics-engine-qualified-pipeline-20261023`, `intelligence/analytics/metrics.py`, blob `6bfe7aaebf609ff72cf9a470e429decdb8de786d`; head `12921968ef3d5c6f5e706fd8e625c54733405f3b` | **reference**, не production source Group C |
| QDP PR24 | `feature/qualified-data-pipeline-v1-20261023`, head `1221a6197e350c7fcdb80e629b34d57bd7a65c09` | v1 pointer / preparation semantics; совместимость в будущем требует re-probe |

**Примечание:** исходный пользовательский `ContentMachine_C_Performance_Breakout_DB_Spec_v1.txt` прямо в репозитории не обнаружен; его решения подтверждаются существующим `PERFORMANCE_C_DATABASE_V1.md`, MD/TXT планом и применёнными SQL. Если текст спецификации отличается, это **contract change / review**, а не скрытое изменение формулы.

## 2. Матрица C0–C7 — точные входы, выходы и единицы

| Этап | Необходимые исходные поля и свидетельства | Значения в БД | Вычисление и единицы | NULL / запрет |
|---|---|---|---|---|
| C0 raw | `metric_snapshots.id,video_id,source_id,observed_at,collected_at,views,likes,comments,shares,saves,followers,source_metric_definition,source_metric_version,observation_time_assumed,data_quality_status,precision,expires_at` + source-policy version | Сами поля `metric_snapshots`, без отдельной копии | Целые исходные counters, observed_at — время **наблюдения**, collected_at — время извлечения | 0 ≠ NULL; `unverified`/`partial`/`assumed`/`retracted` не дают полный C7 |
| C1 creator ratio | C0 views, config alpha>0, *approved frozen* creator cohort, медиана по сверенным peers того же автора | `creator_ratio,creator_median_views,alpha_used,creator_cohort_id` | `(V+alpha)/(Mcreator+alpha)`, безразмерная доля | Нет alpha, peers или median → NULL и статус неполноты, без дефолта |
| C2 niche outlier | C0 views, alpha, optional approved frozen niche cohort и median | `niche_outlier,niche_median_views,niche_cohort_id` | `(V+alpha)/(Mniche+alpha)`, безразмерная доля | **Опционально**; отсутствие C2 не мешает C7, никакого вклада C2 в Score |
| C3 growth | current + previous `metric_snapshots.id,video_id,source_id,definition,version,views,observed_at`, strictly increasing | `views_growth_per_hour,previous_snapshot_id` | `(V2-V1)/((t2-t1) seconds/3600)`, **views/hour** | Смена source/definition/version, равное время или отрицательная коррекция запрещают complete; не заменять на 0 |
| C4 raw engagement | verified current `views>0,likes,comments,shares` | `like_rate_pct,comment_rate_pct,share_rate_pct,engagement_rate_pct,weighted_engagement_raw` | `ER=(L+C+S)/V`; `WR=(L+1.5*C+3*S)/V`; поля `*_pct=100*доля` | При V=0 rates неопределены; saves в этих формулах не участвуют |
| C5 Bayesian | C4 counters, `bayesian_k>0`, одобренная prior cohort с `pL,pC,pS` | `like_rate_smoothed,comment_rate_smoothed,share_rate_smoothed,weighted_engagement_smoothed,k_used,p_l_used,p_c_used,p_s_used,prior_cohort_id` | `Ls=(L+k*pL)/(V+k)`, `Cs=(C+k*pC)/(V+k)`, `Ss=(S+k*pS)/(V+k)`, `Ws=Ls+1.5*Cs+3*Ss`; доли | Любая отсутствующая prior, config или approval → pending; никаких придуманных коэффициентов |
| C6 freshness | verified `source_video_metadata.id,published_at` на тот же post/source, current `observed_at` | `freshness_score,published_metadata_id` | `100*2^(-hours_since_publication/48)`; 0/48/96h = 100/50/25 | `now()` не заменяет observed_at, naive/future/NULL published_at блокирует |
| C7 breakout | `C1,C3,C5,C6` целиком + approved config/peers/lineage | `score_creator_component,score_growth_component,score_engagement_component,score_freshness_component,breakout_score,score_status,calculation_status` | `0.45*C1+0.30*C3+0.15*C5+0.10*C6`, компоненты как raw magnitudes | **НЕ** min-max / z-score / probability / 0–100. Отсутствует хотя бы одна обязательная составляющая → breakout_score=NULL |

### Decimal и storage

Расчёты в `Decimal` (ref PR23: контекст точности 60, ROUND_HALF_UP), каждое persisting numeric значение округляется до 12 знаков для `NUMERIC(30,12)`. Компоненты C7 после округления C1/C3/C5/C6 вычисляются как `ROUND(weight * rounded_value, 12)`, итог **сумма 4 сохранённых компонентов**. Допустимый диапазон `freshness_score` 0..100, но **breakout_score не имеет этого диапазона**. Не применять float-агрегацию.

### Identity, lineage и version boundary

- `videos.id` = канонический BIGINT поста; `source_id` = TEXT D1 source (не platform), `project_id` = 32 lower hex, `qdp_run_id` = UUID; не преобразовывать один домен ID в другой.
- Все snapshots для C3: один `video_id`, `source_id`, `source_metric_definition` **и** `source_metric_version`. `observed_at` фактический; `observation_time_assumed = FALSE`; качество `ok`, действующий срок наблюдений.
- `source_video_metadata.id` + `video_id` + `source_id` связывает время публикации; `videos.published_at` и `found_at` не служат безусловной заменой.
- Сравнимые peers должны совпадать по platform, creator/topic назначению, content-type, source/definition/version и утверждённым age/time windows; target post исключён; frozen означает immutable membership + snapshot IDs + median/prior evidence.
- Calculation uniqueness в 003: `snapshot_id,calculation_config_id,previous_snapshot_id,creator_cohort_id,niche_cohort_id,prior_cohort_id`, `NULLS NOT DISTINCT`; версионность вычислений не путать с готовностью входного QDP.
- Завершённые `calculated_metrics` immutable; новый источник/версия/peer/config вызывает **новый** calculation, старый complete не править.

## 3. Статусы и причины

Допустимые **в обоих** `calculation_status` и `score_status` 003: `complete`, `insufficient_data`, `pending_configuration`, `invalid_timestamp`, `incompatible_source`, `counter_correction`, `calculation_error`. Новые детальные причины хранятся отдельно (future runtime/review), не записываются произвольно в эту SQL CHECK колонку.

| Причина (не обязательно DB enum) | DB status | Значение |
|---|---|---|
| missing snapshot/counter, zero-views rate, insufficient prior history/peers/metadata | `insufficient_data` | Значение отсутствует, score NULL |
| alpha/k/priors отсутствуют; config/cohort не одобрены | `pending_configuration` | Не делать вывод о значении параметра, score NULL |
| source/metric definition/version/platform/type несовместимы, observation assumed/unverified, source invalid | `incompatible_source` либо `insufficient_data` для unverified/assumed | Несопоставимые данные запрещено пересчитывать как будто они valid |
| publication date после observation, naive, zero/negative interval | `invalid_timestamp` | Не выдумывать timestamps |
| current views меньше предыдущего | `counter_correction` | Не clamp-to-zero, не полноценный Growth |
| Decimal overflow / result cannot fit NUMERIC(30,12) | `calculation_error` | Не писать partial как complete |

Функция `status_for_blocker` в `contract_audit.py` отображает **строго известные** детальные причины в имеющийся DB enum; неизвестный код → `ContractError(unmapped_c_blocker)`. Она намеренно **не** выставляет `complete`: завершённость может поставить лишь настоящий будущий scorer после положительной проверки ВСЕХ gates, не отсутствие ошибок в одном маппинге. Отсутствующий **только C2** — не blocker.

## 4. Различия SQL и текущего reference scorer — НЕ замалчивать

| ID | Факт, подтверждённый аудитом | Риск | Этап устранения |
|---|---|---|---|
| GAP-01 | `004.cm_c_result_integrity` проверяет `source_metric_definition`, но не сравнивает `source_metric_version` двух snapshots | Возможно invalid complete при смене provider-version | GC-MOD-05/15, additive migration/worker guard |
| GAP-02 | `004` для complete не проверяет `observation_time_assumed`, `data_quality_status`, `precision`, `expires_at` обеих точек | Возможно scoring estimated/retracted/stale observations | GC-MOD-06/23/24 |
| GAP-03 | `003/004` не гарантируют для C7 совместимость frozen cohort по actual creator/platform/kind/source/definition+version и исключение target из peers; `comparison_cohorts` пока без version метрики | Сравнение несопоставимых posts / leakage | GC-MOD-09/10/20 |
| GAP-04 | `is_frozen` не равен утверждению человека; текущая схема не требует `prior_cohort_id` для complete и не проверяет provenance чисел `pL/pC/pS` | Произвольные priors дают формально complete | GC-MOD-10/11/12/20 |
| GAP-05 | `metric_calculation_configs.is_active` допускает `alpha=NULL`, `k=NULL`; активация не является review / approved | Ошибочное принятие конфигурации | GC-MOD-11/12 |
| GAP-06 | `source_video_metadata.published_at` с valid ID есть; complete trigger сверяет дату, но не проверяет `source_id` metadata vs snapshots | Подмена publication time другого source на том же video | GC-MOD-08/20 |
| GAP-07 | `calculation_status` и `score_status` SQL CHECK независимо валидны; взаимное соответствие для complete не навязано DB | Противоречивый persisted result | GC-MOD-20 |
| GAP-08 | В `004` не проверяется current source policy при **чтении**; отсутствие source/peer ретракции в позднее время не инвалидирует старый readable complete | Stale / revoked выводы | GC-MOD-23/24/25 |
| GAP-09 | Reference Analytics scorer проверяет `source_metric_version`, assumed FALSE, quality ok, но не доказывает same project, approved humans, cross-peer eligibility, published metadata ID или retention в самой pure функции | Pure arithmetic ≠ pipeline readiness | GC-MOD-02/04/05/08/11 |
| GAP-10 | QDP `QDPHandoffPointer`: `event_id,project_id,qdp_run_id,video_id,analysis_group,input_sha256,schema_version=qdp-handoff/v1`; Group C internal `GroupCInputPointer`: `project_id,qdp_run_id,video_id,source_id,content_kind,metric_snapshot_id,preparation_sha256,metric_source_definition,metric_source_version` | НЕЛЬЗЯ deserialize qdp event напрямую как C normalized pointer; нужны verified joins/projection | GC-MOD-02/04 |
| GAP-11 | QDP `ready` означает подготовлен вход, НЕ C0–C7 complete | Ошибка статусов между системами | GC-MOD-04/25 |
| GAP-12 | 003/004 не требуют `views>0` или обязательные L/C/S при complete; математическая функция reference запрещает V=0 | Деление на ноль и фиктивные results | GC-MOD-06/16/20 |

**Не считать проверки SQL уже обеспеченными, когда реально их должен добавить новый C-owned runtime и миграции.** Расхождения занесены как явно открытые пункты, а не скрыто исправлены без тестирования.

## 5. QDP / Analytics handshakes

**QDP → C**: принимать только версионированный `qdp-handoff/v1` (QDP event ID UUID + explicit group `C` + SHA). После durable re-read QDP preparation, самого D1 post, **обоих** актуальных snapshots, metadata и frozen/approved C config C создаёт собственный job/receipt, ACK **после DB commit**. Внутренний normalized pointer Group C расширен source/definition/version ID, но **не** является wire-format QDP. Не объявлять существующую реализацию consumer на GC-MOD-01.

**C → Analytics**: посылать reference на `calculated_metrics.id`, `post_id`, `snapshot_id`, `previous_snapshot_id`, `published_metadata_id`, config/cohort IDs, `score_status`, four persisted components и currentness. Analytics PR23 имеет самостоятельный `compute_c_metrics`; для main-интеграции нужен единственный production calculator из Group C и adapter, а не два конкурирующих вычислителя. Ветка Analytics сейчас **не модифицирована**.

## 6. QA и запреты

- Контрактный executable inventory: `intelligence/group_c/contract_audit.py`; pure regressions: `tests/test_group_c_contract_audit.py`.
- Команда: `python -m unittest discover -s tests -p 'test_group_c_*.py' -v`. Проверки структуры SQL **не** заменяют PostgreSQL-17 fresh+upgrade и concurrency E2E.
- Не требовать / не генерировать `alpha`, `bayesian_k`, prior rates или fake cohorts.
- Не запускать LLM/network/provider внутри Group C, не править старые миграции 003/004, QDP/Analytics/Group A/B ветки и main.
- Any formula/status/lineage change = новая версия contract + согласование и тесты; не менять v1 in-place.
