# PIE-MOD-08 — Comparable Strata & Source Definitions

**Ветка-владелец:** `feature/patterns-insights-full-module`. **Контракт:** `pie-comparable-strata/v1`. **Статус:** самостоятельное реализованное *кандидатное* детерминированное разбиение; статистические выводы и производственная авторизация ещё не реализованы.

## Цель и API

`build_candidate_strata(snapshot: CandidateResearchSnapshot, policy: StrataPolicy) -> CandidateStrata` работает **только** с ранее собранным и воспроизведённым `pie-research-candidate/v1`; вызывает `replay_candidate_snapshot()` и сравнивает все поля, а не доверяет произвольно сконструированному объекту. Не делает сеть, SQL, LLM, Group C arithmetic или вызовы UAE. **Не обновляет права источников.** Для будущего продакшен-чтения требуется отдельная повторная проверка текущего права доступа.

Каждая включённая публикация назначается **ровно одной** группе. Сохранены исходные `videos.id`, `uae_snapshot_id`, `row_sha256`, точный DECIMAL-текст `analysis_weight`, исходные исключённые ID и причины, размер всей исследованной совокупности, SHA исходного исследования и SHA версионированной политики. Отсутствующие публикации не отбрасываются незаметно; аудит `sum(strata.sample_size) + excluded_count == scanned_count` обязателен.

## Измерения сопоставимости

Ключ каждой группы содержит **всё перечисленное ниже**:

| Измерение | Источник | Правило |
|---|---|---|
| Платформа и тип публикации | UAE exact row `platform`, `content_kind` | Не объединять video/carousel или разные платформы |
| Провайдер и юридическая версия | UAE pointer `source_id`, `source_policy_version_id` | Только одинаковые pinned scope |
| Определение и версия метрики | UAE pointer `source_metric_definition`, `source_metric_version` | Запрет объединять raw views с другими определениями/версиями |
| Group C | UAE pointer `calculation_config_id` | Никакой нормализации и повторного вычисления C7 |
| A/B/SRE и B-таксономия | UAE frozen version fields | Разные версии не смешивать |
| Creator/earlier/cohort/niche revisions | UAE frozen creator/prior/niche cohort versions | Null — не выдуманное значение |
| Creator grouping | Явная политика `individual` либо `cohort` | Индивидуальный ключ автора не смешивается с другим; режим cohort использует frozen creator cohort version, но **не доказывает независимость публикаций** |
| Ниша | Явно выбранный `features_b` ключ либо отсутствие | Скалярный string/int — одна категория; отсутствующая ниша помечается `missing` при `isolate` или блокируется при `reject`; многозначный список отбрасывать/размножать **нельзя** |
| Возраст публикации | `observed_at - published_at` | Half-open версии интервалов `[min,max)`, явные непрерывные интервалы начиная с нуля до бесконечности |
| Давность метрики | `as_of - observed_at` | Аналогичные явные независимые интервалы |

Возрасты вычисляются целым числом микросекунд и сравниваются с целыми границами секунд. UTC-смещения из ISO timestamps соблюдаются; точное значение на верхней границе попадает в следующий интервал. Никаких автоматически придуманных порогов или единой «свежей» группы нет: `StrataPolicy` обязательно передаёт оба массива `AgeBucket` и их версию. Ограничение до 64 интервалов каждого вида; проверяются полнота покрытия, непрерывность, уникальные labels и корректная типизация.

`require_one_stratum(partition, video_ids)` проверяет, что все канонические, уникальные отсортированные `videos.id` существуют и принадлежат **одному и тому же** immutable ключу. При разных группах — `PIEBoundaryError('incompatible_strata')`. Это структурный предохранитель, **не статистическое сравнение**: нет P-values, проверки мощности/перекрытия, независимости creator или процедуры FDR. Этим будут заниматься PIE-MOD-09…24. Ни `require_one_stratum`, ни позитивные mock-тесты не дают разрешения на инференцию.

## Контроль целостности и пересборка

`CandidateStrata` — frozen dataclass с defensive `as_dict()`, canonical JSON и `artifact_sha256`. `replay_candidate_strata(raw, expected_sha256, original_snapshot)` проверяет UTF-8, уникальность JSON-ключей, размер, неприемлемые nonfinite числа, байтовый хеш, exact study policy, полностью повторно строит разбиение из оригинального `CandidateResearchSnapshot` и проверяет **полное совпадение**; просто пересчитать хеш подменённого JSON недостаточно. Изменение platform/kind/source metric/C version/creator policy/age bucket/feature меняет идентичность артефакта. Нет разрешения на историческую авторизацию после offline replay.

Результат всегда имеет `evidence_state='unverified'`, `verified_live=False`, `ready_for_analysis=False`. Политика `niche_missing='isolate'` означает «категория отсутствия свидетельства», **не** утверждение сопоставимости ненаблюдавшихся ниш; будущий метод должен блокировать некорректное сравнение даже внутри этой категории, если дизайн того требует.

## QA и реальные ограничения

- Новые файлы: `intelligence/patterns_insights/research/strata.py`, `tests/test_pie_strata.py`, экспорт в `research/__init__.py` и package `__init__.py`.
- Локально: автономные smoke-тесты нового алгоритма с **mock-контрактом** (не полный репозиторий). Отдельный GitHub Actions workflow `PIE standalone module` после коммита должен выполнить `python -m unittest discover -s tests -p 'test_pie_*.py' -v` для реального интегрированного CI; до подтверждённого `success` **не** записывать полный PASS.
- Тестируются platform/kind split, explicit creator grouping, unknown/multilabel niche, source+metric+config+taxonomy version changes, age boundary, exact included/excluded counts, no duplicate counts, adversarial resealed JSON, offline replay, immutable key, no evidence readiness.
- PostgreSQL 17, реальные права доступа и UAE `uae-to-pie/v1` пока не проверены. Никакие статистические и причинные утверждения или дорогие модельные вызовы этим этапом не разрешены.

**Следующий этап:** PIE-MOD-09 — Outcome and comparison policy: явный выбор измеряемого исхода, pre-registered contrast и окна; не использовать текущие кандидаты как сертифицированную производственную выборку.
