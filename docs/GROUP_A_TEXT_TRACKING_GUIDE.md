# GA-MOD-13 — Screen Text Tracking, Sparse Temporal Evidence

**Ветка:** `feature/group-a-full-module`; **Implementation:** COMPLETE.
**Release:** NOT READY. **QA:** GitHub code+scope static audit 19/19 PASS,
new Python/PG17 Actions QUEUED / NOT RUN; real video manual QC NOT VERIFIED.

## 1. Архитектура и точная граница доказательств

`intelligence/group_a/extractors/text_tracking.py` реализует чистые
`TrackConfig`, `track_screen_text(ocr, frame_manifest, config=...,
verify_current=...)`, `TextTrackingError`. Публичный офлайн-вызов:
`GroupAService.track_local_screen_text()`. Никакой фоновой очереди,
импорта приватного PSI, API-провайдера или прямого SQL writer.

Вход — именно `ga-frame-ocr/v1` GA12 с оригинальным GA07
`group-a-frame-manifest/v1`, привязанный к source video SHA,
project ID, run UUID, frame manifest SHA и каждому
`frame_ref`/`frame_sha256`/`sample_index`/`observed_sec`/
width/height. Обе цепочки проверяют canonical SHA-256.
Дополнительно хост обязан предоставить `verify_current(context)`,
который заново авторизует проект, запуск и медиа; совпадение SHA
не является разрешением доступа. Без literal `True` блокировка.

Выход `ga-screen-text-tracking/v1` — immutable, SHA-hashed.
**Один OCR кадр подтверждает только наблюдение в этот момент.**
Результат НЕ говорит, что текст присутствовал в каждом промежуточном
кадре. Всегда `sampling_truth=sparse_instants_only`,
`temporal_coverage=unknown`,
`full_video_text_absence=unknown`,
`continuous_intervals_verified=false`,
`first_text_true_onset_sec=null`,
`video_wide_screen_word_count=null`.

## 2. Нормализация, сопоставление и дедупликация

- Unicode NFKC/casefold и словная токенизация поддерживают
  RU/EN/смешанный текст. Пунктуационные вариации не формируют
  лишние появления; original OCR `text` и источники остаются.
- Слова собираются в строки по реальному Tesseract `line_ref`,
  порядок чтения внутри строки определяется X/геометрией,
  bbox объединяется в координатах исходного PNG. Confidence —
  исходная усреднённая OCR оценка, не калиброванная вероятность.
- Дубли одинаковой нормализованной строки в **одном** изображении
  с почти совпадающими bbox подавляются по IoU (default 0.70);
  разные одновременно расположенные строки остаются разными.
- Между соседними *наблюдёнными* кадрами текст сопоставляется
  по SequenceMatcher similarity (default >=0.80) и
  нормализованным координатам bbox, center distance <=0.22,
  дополнительному пространственному IoU; точные RU/EN слова
  и некоторая редакторская ошибка OCR допускают объединение.
  Жадное детерминированное global one-to-one сопоставление
  исключает присоединение двух одновременно существующих
  надписей к одному треку.
- Короткие отличающиеся слова не сопоставляются неточным fuzzy
  правилом (default минимум 5 символов). Движущийся overlay
  может остаться одним треком при достаточном spatial overlap.
- Наблюдённый `no_text_detected` закрывает трек;
  восстановление в следующем кадре создаёт **новое**
  появление. `failed`/`unavailable`/`skipped_budget`
  тоже разрывает связь, но `closure_reason=ocr_gap_unknown`,
  а не доказанное отсутствие текста. Частичная языковая
  доступность сохраняется как `unverified_language_gap`.
  Превышение `max_gap_sec` (default 1.5) закрывает трек
  как `sample_gap_exceeded`, даже если надпись похожа.

Каждый трек содержит `track_id`, исходный/canonical текст,
наблюдения с временем, bbox и raw confidence, их число,
`first_observed_sec`, `last_observed_sec`,
`sampled_span_sec`, `closure_reason`. Ограничение:
48 sampled frames, до 4000 строк/появлений; default модуль
FAIL CLOSED при превышении бюджета.

## 3. Различие чисел и настоящих временных границ

`sampled_screen_word_count` — число lexical tokens в
**наблюдённых уникальных появлениях/треках**, а не сумма одного и того же
текста по нескольким OCR-кадрам. Повторное появление после наблюдённой
тишины изображения считается новым occurrence. При полном
отсутствии распознанного текста оно `null`, не ложный ноль
полного видео.

`first_text_observed_sec` — первый **заснятый** момент текста,
не заявляемый как настоящий timestamp появления. Если текст
появился на 0.100 s и повторился на 0.900 s, известна
только пара наблюдений и `sampled_span_sec=0.800`;
не доказано, что текст существовал все 800 ms.
Однофреймовый FLASH даёт `sampled_span_sec=0.000`
и не превращается в фиктивный ненулевой интервал.

## 4. A003 — важное ограничение реальной схемы

Реальная `intelligence/migrations/003_video_features_a.sql`
проверена в `main`:
`video_feature_a_screen_text` требует
`start_sec >= 0`, `end_sec > start_sec`,
`screen_text`, `method_name`, необязательный
`confidence_pct`. Представление `video_feature_a_summary`
выводит `first_text_sec = min(start_sec)` только из
проверенных interval rows при `screen_text_status` partial/complete.

Редкий OCR кадр **НЕ ДАЁТ** подтверждённых start/end.
Поэтому GA13 предоставляет безопасную проекцию
`a003_screen_text_projection=[]` и
`a003_publication_state=blocked_unverified_continuity`.
Это сознательное защитное решение, а не скрытое заполнение
таблицы фиктивными кадрами/интервалами. GA-MOD-18 владеет
атомарным A003 writer и сможет публиковать только отдельно
доказанные temporal bounds после GA13/14 evidence validation.
Пока `first_text_sec` в A003 должен оставаться NULL.

## 5. QA и тесты

`tests/test_group_a_text_tracking.py`:
одна строка на трёх кадрах → одно появление/не тройной count;
однокадровая вспышка; повтор после blank;
failed/unavailable/skipped OCR gap; sparse-gap split;
две одновременные одинаковые надписи в разных местах;
внутрикадровый OCR duplicate; moving overlay/fade,
небольшая edit-distance OCR ошибка; короткие похожие слова;
русский/английский/Unicode/punctuation и X reading order;
zero vs unknown; SHA и frame-PNG mismatch; source/frame
timestamp подмена; revoked host rights; deterministic hash;
strict JSON Schema positive/negative; GA12→GA13 end-to-end
через реальный формат frame manifest и fake OCR engine.

**19 из 19 статических проверок GitHub** (isolated branch,
manifest/GA12 SHA, one-to-one matching, language/geometry,
coverage unknown, budget, host auth, strict Schema, no A003
fabrication, service exports/tests) прошли. Actual Python suite,
PG17 and ручной real-video temporal QC всё ещё **QUEUED/NOT RUN**.
No PASS claim for them.

Использование:
```python
from intelligence.group_a import GroupAService, TrackConfig
result = GroupAService().track_local_screen_text(
    ga12_ocr_observation,
    ga07_frame_manifest,
    config=TrackConfig(max_gap_sec="1.500"),
    verify_current=authoritative_project_run_media_rights_check,
)
assert result["a003_screen_text_projection"] == []
```

**Next:** GA-MOD-14 — Subtitle detection: confirmed presence vs unknown,
без ложного `false` на sparse-only OCR.
