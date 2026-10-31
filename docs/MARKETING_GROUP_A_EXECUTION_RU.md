# Group A внутри Marketing — исполняемая автономная интеграция

В Marketing реализован **собственный** Group A для выбранных видео, без QDP и без вызова worker'ов Group A из других веток/сервисов. Исходные алгоритмы размещены локально в `intelligence/market_intelligence/_copied/group_a/`.

## Исполнитель

`media_worker.py` проверяет право источника и SHA-256 скачанного MP4, вызывает `replica_media.analyze_media()`. Тот запускает:

| Этап | Технология | Вывод |
|---|---|---|
| A technical | скопированный GA06 FFprobe/FFmpeg | длительность, соотношение сторон, сцены, локальная временная ось |
| A frames | GA07 scene-aware frame sampling | до 48 байтово проверенных PNG с **реальным PTS**, SHA и manifest |
| A OCR | GA12 + локальный закреплённый Tesseract | слова/координаты/времена только в наблюдённых кадрах |
| A text tracking | GA13 | совпадения текста между кадрами, без изобретённых непрерывных интервалов |
| A audio decode | GA09 | одно однократное декодирование PCM, проверенный hash, ноль/тишина/недоступность |
| A VAD | GA09 Silero local JIT | кандидаты речевой активности, интервалы и источник модели |
| A ASR | GA10 local faster-whisper | фактически распознанная речь/язык и сегменты |
| A speech metrics | GA11 | слов, WPM, длительность и доля речи, временное смещение A/V (если доказуемо) |
| A acoustic events | GA15 локальный ONNX YAMNet/PANNs | интервалы и присутствие музыки/SFX/прочего; отсутствие классификатора = UNKNOWN |
| A subtitle probe | GA14 FFprobe embedded subtitle stream packets | наличие *встроенных* дорожек и пакетов |
| A subtitle detection | GA14 tri-state, OCR + ASR + embedded streams | TRUE только с доказательствами, FALSE только при проверенной плотной негативной инспекции |
| A downloader transcription sidecar | GA10 SRT parser | дополнительные текстовые подсказки, **НЕ доказательство видимых субтитров или произнесённой речи** |

`group_a_audio.py` декодирует аудиопоток один раз и запускает VAD, ASR, speech metrics и acoustic events независимо. Ошибка в одном классификаторе не уничтожает наблюдения остальных.

`group_a_report.py` собирает `mi-group-a-media-report/v1`: состояния этапов, исходные SHA, хронологическую карту проверенных сцен/кадров/текстовых наблюдений/звуковых событий и reasons для UNKNOWN. Неисправные временные интервалы отклоняются; ASR переносится на video clock **только после GA11-подтверждения**. В `mi_video_analyses` сохраняется `creative.group_a`. Идентификатор метода: `mi-abc-audio-expanded-media/v3`; новая версия добавлена миграцией 083, исторические v1/v2 записи не переписываются.

## Конфигурация моделей на сервере

Все модели только локальные и заранее установленные (без download при обработке отдельных видео, без ключей или галочек в пользовательском интерфейсе). Если путь/проверенный hash отсутствует, соответствующий этап возвращает `unavailable`. При наличии полного backend pin-манифеста модель запускается автоматически, если соответствующий `*_ENABLED` не равен `0`.

- **ASR:** `CM_MARKET_ASR_MODEL_DIR`, `CM_MARKET_ASR_MODEL_SHA256`, `CM_MARKET_ASR_MODEL_VERSION`, `CM_MARKET_ASR_ENGINE_VERSION`. Нужен локальный faster-whisper; `CM_MARKET_ASR_ENABLED=0` отключает.
- **VAD:** `CM_MARKET_VAD_MODEL_PATH`, `CM_MARKET_VAD_MODEL_SHA256`, `CM_MARKET_VAD_MODEL_VERSION` для локального Silero JIT; `CM_MARKET_VAD_ENABLED=0` отключает.
- **Акустические события:** `CM_MARKET_AUDIO_EVENTS_MODEL_PATH`, `CM_MARKET_AUDIO_EVENTS_MODEL_SHA256`, `CM_MARKET_AUDIO_EVENTS_MODEL_VERSION`, `CM_MARKET_AUDIO_EVENTS_ENGINE` (`yamnet_onnx`/`panns_onnx`), `CM_MARKET_AUDIO_EVENTS_LABELMAP_JSON`, `CM_MARKET_AUDIO_EVENTS_LABELMAP_SHA256`, `CM_MARKET_AUDIO_EVENTS_INPUT_LAYOUT`; необходима явная серверная `CM_MARKET_AUDIO_EVENTS_LICENSE_AUTHORIZED=1`; `CM_MARKET_AUDIO_EVENTS_ENABLED=0` отключает.
- **OCR:** `CM_MARKET_TESSERACT_PIN`, `CM_MARKET_TESSERACT_BINARY`, `CM_MARKET_TESSERACT_DATA`.
- Для FFmpeg нужны `ffmpeg` и `ffprobe` в PATH; `CM_MARKET_MEDIA_ROOTS` ограничивает доступные каталоги медиа.

## Приёмка

Offline-тесты: `python -m unittest discover -s tests -p "test_market_group_a_complete.py" -v` и `python -m unittest discover -s tests -p "test_market_replica_execution.py" -v`; CI дополнительно компилирует все скопированные модули и проверяет PostgreSQL 17 миграции. Тестовый MP4 генерируется настоящим FFmpeg; тесты не имитируют наличие внешней модели.

**Важная граница:** наличие исполняемых алгоритмов Group A и отчёта не равно гарантии, что на хосте установлены все модели и что видеоряд обследован кадр за кадром. Взятые GA07 кадры остаются sparse observations, поэтому `original_full_group_a_certified=false` до отдельного подтверждения полного покрытия. Нельзя выдавать отсутствие объекта на ненаблюдённых кадрах за доказанный факт. E2E с 100 настоящими MP4 ещё необходим.
