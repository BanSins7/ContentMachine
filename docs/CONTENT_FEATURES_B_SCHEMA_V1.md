# Структура B — PostgreSQL source-first v1 (B1–B8)

Статус: реализация хранения, миграция 005_content_features_b.sql.
Источники требований: восемь предоставленных TXT от 18.10.2026, а не старые таблицы ContentMachine.
Это контракт записи/валидации классификаций, а не автоматический мультимодальный анализатор.

## ID и версии

content_b_analyses ссылается на существующий videos.id, sources.id, опциональную
source_policy_versions и одну соответствующую запись Group A (video_feature_a_analyses
или carousel_feature_a_analyses). Каждый анализ имеет analysis_key, analysis_version,
taxonomy_version, метод, модель/версию промпта, статус, needs_review и причины недоступности.
Завершённый анализ не переписываем: для нового запуска новый analysis_key.

Приоритет B2: content_type ровно video/carousel, а не пять низкоуровневых типов A.
- A video, slideshow_video -> B video.
- A photo_carousel, mixed_carousel -> B carousel.
- A image_post НЕ имеет отдельного согласованного B2-контракта: не имитируем перелистываемую карусель.
- Неизвестный videos.content_kind может быть установлен позднее при фактическом осмотре медиа;
  импорт URL не даёт прав на анализ.

## Все 8 TXT блоков

B1 Hook: content_b_hooks + content_b_hook_parts — дословные spoken_text/screen_text,
visual_description, уникальный массив mechanisms из 10 значений, пять независимых nullable
булевых формулировки, массив delivery из 4 способов, упорядоченные смысловые части с
временем видео либо индексом слайда. НЕТ первичного типа хука.

B2 Format: content_b_formats — 12 video_format с primary + опциональным secondary;
8 carousel_format. Формат НЕ является темой, UGC, tutorial или сценарным паттерном.

B3 Story: content_b_story_sampling, content_b_story_frames, content_b_story_intervals,
content_b_story_slides, content_b_story_patterns (view) — ровно 26 ролей, по одной на
ВЫБРАННЫЙ кадр/слайд, before_after_phase before/after/both/null, сохранение повторов.
Первые 5 сек 2 изображения/сек, далее 1, ориентир 60 образцов; это частота выборки,
не возвращаемый в A параметр FPS исходного видео. B3 не равен границам монтажных сцен A.

B4 Visual: content_b_visual_content — ровно четыре поля ai_generated (true/false/null),
person_visible, product_visible, logo_visible. Происхождение AI не угадывается по виду.

B5 Editing: content_b_editing — single_take / slow_cuts / medium_cuts / fast_cuts;
ЧЕТЫРЕ числа cut_count, cuts_per_minute, avg_shot_duration, cuts_first_5_sec.
Только видео, без платной LLM. avg_shot_duration СОХРАНЁН потому, что это явное
требование исходного B5; avg_scene_sec в группе A не возвращался.

B6 Subtitles: content_b_subtitles — none/standard/dynamic и семь полей subtitle_style
(position, font_size, font_style, text_color, highlight_color, background_style,
display_mode). Только видео, никогда не подменять субтитры текстом слайда.

B7 CTA: content_b_cta_analyses, content_b_ctas, content_b_cta_summary (view) —
12 типов, ноль или больше ПОЯВЛЕНИЙ, дословный текст + spoken_text/screen_text,
один или два канала delivery, девять destination.type, service/url/handle/app_name/
keyword/other_description. Для видео секунда, для карусели индекс слайда.
none — отсутствие записей, НЕ тип. При partial/failed has_cta = NULL.

B8 Topic: content_b_categories, content_b_topics, content_b_subtopics и
content_b_topic_assignments — иерархия CATEGORY > TOPIC > SUBTOPIC (опциональная),
стабильные ID и проверенные связи родитель-ребёнок. Плоские topics Discovery
используются для поиска и НЕ заменяют B8.

## Служебные ограничения и открытые вопросы

- Процессы OCR/ASR/мультимодальной классификации сейчас не подключены;
  миграция хранит только будущие результаты. Блок B5 может вычисляться позже
  локально FFmpeg + PySceneDetect.
- Для unknown/needs_review нет ложных нулей или guessed URL.
- Семантическая дедупликация B8 требует отдельного поиска смысла; UNIQUE(slug)
  предотвращает только точные дубли внутри одного родителя.
- people описан в B2 как отдельный будущий параметр, однако его контракт не
  задан восемью файлами. Не выдумываем типы людей или ИИ-персонажей.
- slide_layout и text_density упомянуты для B2 каруселей, но схемы этих объектов
  ещё не согласованы. Не создаём произвольные категории.
- Порог вторичного video_format отсутствует в B2 — нужна проверка реальными данными.
- 2/1 кадров в секунду для B3 — стартовый ориентир, не доказанный оптимум.
- Строка B4 записывается только если проанализирован визуальный материал; если
  классификатор не запускали, строку НЕ создаём, а не ставим false.
- Действия требуют явных прав sources.can_analyze_media и надлежащей политики,
  также при чтении и удалении по retention, не только при INSERT.
- Исторические videos, topics, video_analyses, Group A не преобразуются и не
  удаляются. D1 URL-only источник остаётся metadata-only.

## Дальнейший порядок

1. Завершить и проверить новую схему B на Windows/Linux с PostgreSQL.
2. Связать локальные исходные медиа с canonical videos.id и разрешённым источником.
3. Добавить локальный B5 и затем общий ASR/OCR/мультимодальный проход B1–B8
   с проверкой словарей, затрат и ручных меток на реальных публикациях.
4. Утвердить отсутствующие спецификации people и layout отдельно.
