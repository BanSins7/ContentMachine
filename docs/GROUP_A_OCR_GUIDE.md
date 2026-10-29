# GA-MOD-12 — Frame OCR Engine, RU/EN Tesseract & Geometry

**Ветка:** `feature/group-a-full-module`. **Implementation:** COMPLETE (Tesseract 5 CPU).
**QA:** GitHub static audit 18/18 PASS; direct Tesseract RU/EN/rotated CLI PASS.
Committed Python/PG17 Actions QUEUED/NOT RUN. PaddleOCR/GPU NOT IMPLEMENTED/NOT VERIFIED.

## Объём реализации

`extractors/ocr.py`: `OCRConfig`, `OCRBackend` port, `OCRCache`,
`analyze_frame_ocr()`. Каждый batch требует канонический GA07 frame
manifest, точные PNG bytes (проверка SHA/размера/геометрии/perceptual hash),
project_id 32-hex и run UUID. Сохраняются `frame_ref`, PNG SHA,
`sample_index`, **фактически наблюдённый** PTS `observed_sec`,
ширина/высота оригинала, слова, `line_ref`, `confidence_pct`
и pixel-`bbox{x,y,width,height}`.

`extractors/tesseract_ocr.py`: реальный offline Tesseract 5.x. Требует
доверенных абсолютных путей бинарника/каталога traineddata и ожидаемый
SHA fingerprint *самого бинарника + установленных используемых eng/rus/osd
моделей*. Хэш перепроверяется до OCR и перед возвратом cache identity.
Нет скачивания весов, интернета, платного API или импорта PSI.
Tesseract CPU-only, 1–4 OpenMP threads; cuda не подменяется CPU.
Опциональный PaddleOCR/GPU движок явно **не поставляется**.

Языки: `eng`, `rus`, оба вместе. Недоступный язык по умолчанию
`unavailable`, не отсутствие текста; только явная конфигурация
`missing_language=fallback_eng` разрешает обработать English
с `state=partial_language`. Фактически использованные пакеты
сохраняются в доказательстве.

Параметры: `frame_budget` 1–48 (default 12), `timeout_sec` 1–30
(default 15 на subprocess), `batch_timeout_sec` 15–900 (default 150),
`scale` 1/2, `preprocess` none/autocontrast/sharpen, `psm`
3/6/11/12, явные повороты 0/90/180/270. TSV разбирает только
OCR word-level (`level=5`), валидирует оценку 0..100 и координаты.
После масштабирования/вращения прямоугольники переводятся обратно в
пиксели исходного GA07 PNG; неоднозначная геометрия отклоняется.
Тессеракт confidence — сырая оценка движка, **не калиброванная точность**.

Frame state: `text_observed`, `no_text_detected`,
`partial_language`, `failed`, `unavailable`, `skipped_budget`.
Падение одного кадра не удаляет успешные наблюдения остальных.
Неизвестные/пропущенные кадры не маркируются «нет текста».
У batch ВСЕГДА `temporal_coverage=unknown`,
`screen_text_absence=unknown`, `unsampled_intervals_present=true`.
Даже `no_text_detected` означает лишь отсутствие распознанных слов
в одном обработанном кадре. Временное отслеживание — GA13,
проверка субтитров — GA14, запись A003 — GA18.

`OCRCache` индексирует project/run, SHA источника, GA07 manifest/PNG,
model fingerprint/engine/version/config. На каждом cache get, включая
cache miss, требуется authoritative `verify_current(context) is True`;
отзыв прав уничтожает значение. Результаты возвращаются защитными
JSON-копиями. Это не разрешение повторного использования между проектами.

Strict JSON Schema: `schemas/ga-frame-ocr-v1.json`.
Offline facade: `GroupAService.inspect_local_frame_text(...)`.
Фоновый worker, SQL writer, внешние provider calls выключены.

## Тесты и QA

`tests/test_group_a_ocr.py`: EN/RU слова, небольшая subtitle-like
строка, PNG с шумом/пустой кадр, повороты 90/180/270, исходные bbox,
битый TSV/PNG/SHA, неверные confidence/geometry, отсутствие русской
модели и явный English fallback, отказ прав/кэш и defensive copies,
timeout, frame-budget, частичные ошибки, strict JSON Schema,
опциональные реальные локальные Tesseract изображения.

**Independent CLI smoke PASS** (Tesseract 5.5.0 + eng/rus models,
синтетический DejaVuSans PNG): `HELLO WORLD` (96.48/96.47),
`ПРИВЕТ МИР` (96.21/96.93), `ROTATED TEXT` после 270°
обратного поворота (95.31). TSV дал реальные bounding boxes.
Это проверка CLI, **не выполнение committed Python tests**.
Реальная точность на шумных видео не измерялась.

При первой генерации шага CI появился invalid YAML:
GitHub показывал `failure` на самом файле workflow, **0 jobs**.
Исправлено в коммите `fd5b033`; GitHub вновь распознаёт
`Group A standalone (frame OCR and PG17)`, статус QUEUED.
Скрипт CI ставит tesseract-ocr, пакеты eng/rus, fonts-dejavu,
использует Pillow 11.1.0 и discover `test_group_a_*.py`.
Python/PG17 тесты **QUEUED/NOT RUN**, не PASS.

**Next:** GA-MOD-13 screen text tracking на редких наблюдениях,
без выдуманных непрерывных интервалов.
