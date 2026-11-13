# Group C — GC-MOD-07: независимое pure Decimal ядро C1–C7

**Branch:** `feature/group-c-full-module`  
**Source:** `intelligence/group_c/pure_math.py`  
**Tests:** `tests/test_group_c_pure_math.py`  
**Версия вычислений:** `group-c-decimal/v1`  
**Статус:** код реализован; *не* является production-ready C7 worker.

## Что перенесено и почему

Сверена функция `compute_c_metrics(current, previous, publication, config, creator, prior, niche=None)` из `intelligence/analytics/metrics.py` ветки `feature/analytics-engine-qualified-pipeline-20261023` (reference HEAD `12921968ef3d5c6f5e706fd8e625c54733405f3b`), а также эталонный fixture из `tests/test_analytics_engine.py` той ветки.

Новая **самостоятельная** функция с той же сигнатурой размещена в `intelligence.group_c.pure_math`. Она не импортирует Analytics, QDP, PostgreSQL, provider SDK, не изменяет источники и не отправляет задания. Не экспортируется из корневого `intelligence.group_c` как готовый production scorer — так сохраняется ранее установленное условие, что модуль без approved config/cohort/rights не выдаёт готовую сервисную оценку.

**Важная граница:** арифметическая функция возвращает `calculation_status='complete'`, `score_status='complete'` *только как поле чистого вычисленного словаря*. Это **не** готовый `calculated_metrics` DB row, не доказательство source/peer permissions и **не** разрешение публиковать score. Только будущий stage20 writer после повторной транзакционной проверки всех proof сможет писать `complete` в БД.

## Фиксированный математический контракт и единицы

| Компонент | Формула | Единицы |
|---|---|---|
| C1 | `(V + alpha) / (creator_median_views + alpha)` | безразмерный |
| C2, необязательный | `(V + alpha) / (niche_median_views + alpha)` | безразмерный, **исключён** из C7 |
| C3 | `(V - previous_views) / elapsed_hours` | views/hour |
| C4 | `L/V, C/V, S/V`; raw `(L + 1.5*C + 3*S)/V`; проценты умножаются на 100 | доля / проценты |
| C5 | `Ls=(L+k*pL)/(V+k)`, `Cs=(C+k*pC)/(V+k)`, `Ss=(S+k*pS)/(V+k)`; `Ws=Ls+1.5*Cs+3*Ss` | доля |
| C6 | `100 * 2^(-elapsed_hours_since_published_at / 48)` | 0..100 |
| C7 | `0.45*C1 + 0.30*C3 + 0.15*C5 + 0.10*C6` | **raw composite** без нормализации/обрезки/предполагаемого максимума 100 |

Веса и 48-часовой half-life совпадают с frozen `contracts.py` и SQL 003/004. `alpha`, `bayesian_k` и `pL/pC/pS` требуются **явно**, строго положительные для alpha/k; коэффициенты priors — доли из диапазона 0..1. Нулевая/отсутствующая медиана допустима только при одобренном alpha>0; отсутствующая конфигурация не подменяется стандартной.

## Точность, совместимость и отрицательные контракты

- Все поля результата с числовым значением имеют **ровно 12 знаков** `Decimal` (`NUMERIC(30,12)`). `ROUND_HALF_UP` применяется к каждой величине; C7 сумма вычисляется из **округлённых** четырёх компонентов, как у reference Analytics. Overflow (`abs(output)>=10^18` после округления), NaN/Infinity и нечисловые значения запрещены.
- Вычисление использует локальный decimal context, precision 80; внешняя `getcontext().prec` и rounding mode не могут изменять результат. Ранее выявленное ложное переполнение от Decimal `abs()` предотвращается `copy_abs()`.
- `elapsed_hours` строится из точного `timedelta.days/seconds/microseconds` в Decimal, без `total_seconds()` с float. Для интервала в 1 микросекунду при росте на 1 view результат C3 = `3600000000.000000000000`.
- `float` не принимается для исходных metric facts/alpha/k/priors. Единственное исключение: фиксированные четыре веса `weights_json`, потому что стандартный psycopg JSON может декодироваться в float; каждый такой float должен численно **точно** соответствовать перечисленному canonical весу, другие значения отвергаются.
- Неизвестный, разный или пустой source ID, другое видео, duplicate snapshot ID, разные versions/definitions, `observation_time_assumed=True`, unverified/retracted/estimated observations, невозрастающее observed_at, дата публикации после observed_at, уменьшившиеся views, `V=0`, missing L/C/S и неутверждённые/не-frozen cohort fields приводят к typed `MetricError.code`, а не к фиктивному score.
- Состав из `C2 niche` необязателен. Наличие нишевого cohort не меняет C7, так как C2 не участвует в итоговой формуле.
- Pure scorer **не** проверяет сам по себе законность метрики `platform_post_views` (это C0 Stage06), фактическую SRE/QDP currentness (Stage04/05/21), signed retention (Stage24), source-specific publication lineage (Stage08) и реальность reviewer approvals (Stages09–12). Поля `is_frozen` в pure input не равны доверенной human approval из БД.

## Эталонная parity и тесты

Reference input: `V=200, Vprev=100, Δt=4h, L=20,C=4,S=2, alpha=5,k=50, creator median=100, niche median=250, pL=0.1,pC=0.02,pS=0.01, age=12h`. Полностью сохранённый эталон (все 27 возвращаемых полей):

- C1 = `1.952380952381`.
- C2 = `0.803921568627`.
- C3 = `25.000000000000`.
- C4 weighted raw = `0.160000000000`.
- C5 weighted smoothed = `0.160000000000`.
- C6 = `84.089641525371`.
- C7 = **`16.811535581108`**, сумма четырех округлённых компонентов.

В локальном изолированном Python 3.13: перенесённая реализация на reference fixture дала **точное совпадение всех 27 выходных полей** с независимо воспроизведённой reference реализацией Analytics. **14/14** локальных независимых тестов в отдельном mirror PASSED (включая полное gold, отсутствие niche, overflow, global precision, error codes и microsecond interval). Эти локальные тесты выполнялись на рабочей копии алгоритма. Код затем вручную перенесён в GitHub; это **не** доказательство выполнения полного GitHub suite на точном HEAD.

GitHub-версия `tests/test_group_c_pure_math.py` содержит **12** отдельных unit methods, повторяющих полный независимый gold vector и adversarial cases. Команда для выполнения:

```bash
python -m unittest discover -s tests -p "test_group_c_pure_math.py" -v
python -m unittest discover -s tests -p "test_group_c_*.py" -v
```

`.github/workflows/group-c-module.yml` уже запускает второй тестовый glob на Linux/Windows в `pure-contracts`; отдельная PG17 миграция и DB-факты для GC-MOD-07 **не требуются**. Последний GitHub CI был `queued`, поэтому **точный GitHub checkout QA: NOT VERIFIED**, хотя локальная pure arithmetic и reference parity проверены.

## Интеграция без двух production scorers

Ветка Analytics **не менялась**. Сейчас её historical `intelligence.analytics.metrics.compute_c_metrics` существует отдельно. После согласованного merge GC-MOD-25 обязан заменить внутренние импорты Analytics на тонкую делегацию в `intelligence.group_c.pure_math.compute_c_metrics` и сохранить parity-тест, затем убрать старую вычислительную копию. До этого нельзя заявлять единый production scorer на объединённом main.

Stage08: verified `published_at` из конкретной metadata записи D1, с source/policy/TTL и timezone, без подстановки wall-clock timestamp. Stages09–12: frozen compatible approved cohorts/config. Stages13–19: реальные этапы C1–C7 с их stage-level semantics/gates и reason statuses. Stage20: persisted score и transactional integrity.
