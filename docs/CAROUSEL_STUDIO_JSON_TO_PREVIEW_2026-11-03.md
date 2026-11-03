# Carousel Studio · две вкладки: ChatGPT → готовый MP4

Дата: 2026-11-03. Ветка: feature/ai-avatar-carousel-studio-20261102.

Генерация новых фотографий **не показывается** в интерфейсе (старый /avatar-studio.html перенаправляет на /carousel-studio.html). Python API генерации оставлен в коде для возможного возвращения.

## Что реально делает студия

**Вкладка 01 «Подготовка»:**
- Вставить исходный JSON со сценарием карусели или загрузить его из файла.
- Загрузить 1–100 фотографий PNG/JPG/WebP. Реестр формируется по именам файлов и тегам из имени: sad__morning__puffy.jpg.
- Показать список/миниатюры загруженных файлов; скачать JSON-пакет с исходной инструкцией и photo_manifest.
- «Скопировать задание для ChatGPT» — в буфер копируется подробный технический промпт + исходный JSON + список фото, который надо вставить в этот чат.
- **Фотографии сайт в ChatGPT автоматически НЕ отправляет.** Пользователь прикрепляет их к сообщению вручную. Копируемое задание не содержит бинарных изображений. Псевдоссылки на фотографии НЕ создаются.

**Вкладка 02 «Видеосборка»:**
- Принимает готовый JSON, созданный ассистентом вручную в чате.
- Использует фотографии из первой вкладки (пока не закрыта страница) либо позволяет загрузить их во второй вкладке. Ассеты связываются по реальному filename/photo_id, photo_tags и assets.photos.
- Не запускает LLM: тексты из финального JSON считаются утверждёнными, не перефразируются и не меняются.
- Рендерит кадры на локальном Canvas в формате 1080x1920 по умолчанию (можно 1080x1350), белый Arial normal 400, **без обводки и тени**, точные x/y, размер и ширина текстового блока из JSON.
- Позволяет просмотреть все слайды и скачать PNG/ZIP с manifest.
- «Создать MP4»: собранные PNG отправляются на локальный сервер, где FFmpeg кодирует H.264 / 30 FPS без звука, с длительностью каждого кадра из duration_seconds. Это настоящий MP4 (без генеративной модели, без оплачиваемых API).
- Выводит статус задачи, встроенный видеоплеер и кнопку «Скачать MP4».
- **Недостающие фотографии/CTA:** показывает явно помеченные заглушки в предпросмотре, но запрещает создание финального MP4 до добавления нужных ассетов.

**Ограничения текущего этапа:** фотографии хранятся в памяти текущей вкладки браузера, а не в постоянной библиотеке. Только прямые склейки между кадрами; озвучивание, музыка, движение персонажей, AI-генерация фото/видео и автопубликация пока отсутствуют. Отдельных batch-очередей для нескольких каруселей нет. Первоначальные изображения реальных людей не загружаются на внешние сервисы этим модулем. Итоговое видео сохраняется локально в каталоге задания.

## Запуск на Windows

    cd C:\ContentMachine
    git fetch origin
    git switch feature/ai-avatar-carousel-studio-20261102
    git pull --ff-only
    .\START_CONTENTMACHINE.bat

Откройте http://127.0.0.1:8765/carousel-studio.html#prepare .

В панели Content Machine > Инструменты есть две отдельные ссылки:
- «Карусели · Подготовка» — #prepare
- «Карусели · Видеосборка» — #render

**FFmpeg обязателен для MP4**. Установите FFmpeg с поддержкой libx264 и добавьте папку с ffmpeg.exe в PATH, либо задайте переменную окружения CM_FFMPEG_PATH с полным путём до ffmpeg.exe. Проверьте в PowerShell: ffmpeg -version. Перезапустите локальный сервер после установки FFmpeg, чтобы обновился PATH. Если FFmpeg отсутствует, предпросмотр/ZIP всё равно работают, но видео не создаётся; интерфейс сообщает об этом.

## Финальный JSON для вкладки 02

    {
      "carousel_id": "breakup_001",
      "format": "1080x1920",
      "language": "en",
      "avatar_id": "main_avatar",
      "assets": {
        "photos": [
          {"id": "sad_bedroom", "filename": "sad__bedroom.png", "tags": ["sad", "bedroom"]},
          {"id": "happy_cafe", "filename": "happy__cafe.jpg", "tags": ["happy", "cafe"]}
        ]
      },
      "slides": [
        {
          "id": "1",
          "role": "hook",
          "photo_id": "sad_bedroom",
          "photo_tags": ["sad", "bedroom"],
          "text_blocks": [
            {
              "type": "headline",
              "text": "POV: That breakup changed everything",
              "font_size": 64,
              "font_weight": 400,
              "color": "#FFFFFF",
              "x": 50,
              "y": 9,
              "width_percent": 88,
              "align": "center"
            }
          ],
          "duration_seconds": 3,
          "cta": {"enabled": false}
        },
        {
          "id": "2",
          "role": "cta",
          "photo_id": "happy_cafe",
          "text_blocks": [
            {"type": "headline", "text": "My daily routine starts here", "x": 50, "y": 8}
          ],
          "cta": {"enabled": true, "type": "app_screenshot_overlay",
                  "position": "bottom_center", "width_percent": 43},
          "duration_seconds": 4
        }
      ]
    }

Поле photo_id может быть ID из assets.photos или точным именем загруженного файла. Если photo_id задан, используется явный выбор этого файла (с учётом forbidden_tags). Если photo_id не задан, подбор по required_tags/optional_tags. CTA использует один загружаемый скриншот приложения. Для настройки текста также поддерживается slide.typography.{font_size_px,font_weight,color} и slide.layout.{x_percent,y_percent,max_width_percent,text_align,text_position}.

Шрифт всегда Arial/sans-serif в текущем рендерере, потому что мы фиксируем стандартный TikTok-стиль. Даже если входной JSON содержит неподдерживаемые свойства оформления, они не должны приводить к несанкционированной обводке/тени.

## API локального видеорендерера

- GET /api/carousel-video/status — ffmpeg_available.
- POST /api/carousel-video/render — JSON с carousel_id и slides: [{image: "data:image/png;base64,...", duration_seconds: 3.0}].
- GET /api/carousel-video/jobs/{uuid32} — статус queued/running/succeeded/failed и длительность.
- GET /api/carousel-video/files/{uuid32}/video — MP4 после успешного кодирования.

Максимум: 40 слайдов, 12 МБ/PNG, 100 МБ HTTP запрос, длительность 0.25–60 секунд на кадр, 600 секунд суммарно; 1 одновременный рендер. Нет автоматических платных повторов, потому что платных операций вообще нет. Кадры передаются только на локальный сервер Content Machine по localhost.

Хранилище: Windows %LOCALAPPDATA%\ContentMachineData\carousel-videos\<job-id>\video.mp4 (по умолчанию), альтернативно CM_CAROUSEL_VIDEO_DIR. FFmpeg-кадры после завершения удаляются.

## Проверки

    python -m py_compile app.py carousel_video.py carousel_studio.py
    python -m unittest discover -s tests -p "test_carousel_video.py" -v
    python -m unittest discover -s tests -p "test_carousel_studio.py" -v
    node --check site/carousel-studio.js
    node tests/test_carousel_studio_dom.cjs

Тест интерфейса использует поддельный видеосервер и Canvas; тест Python при наличии FFmpeg кодирует настоящее короткое MP4. Реальная проверка отображения пикселей в Windows Chrome требует запуска на машине пользователя. Ветка не объединена с main.
