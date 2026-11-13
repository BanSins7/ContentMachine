# Instagram Reels Top-100 — отдельный сервис Content Machine

## Запуск

Windows: START_INSTAGRAM_TOP100.bat из корня репозитория.
Адрес: http://127.0.0.1:8767/
React + TypeScript: instagram-top100-ui/
Backend: intelligence/instagram_top100/
CLI: python -m scripts.run_instagram_top100 --accounts "@user1,@user2" --scan-limit 500 --confirm-paid --confirm-rights

## Pipeline

1. Пользователь вставляет публичные usernames/URL. Поддерживается большой список (до 5000 в одном запуске).
2. По явно подтверждённой кнопке Bright Data Instagram Reels Discovery (dataset gd_lyclm20il4r5helnj) выполняет поиск с 1 ноября текущего года по сегодня, до заданного лимита 100–5000 публикаций на аккаунт. Используется type=discover_new, discover_by=url, num_of_posts, start_date/end_date.
3. Фильтрация требует подтверждённый Reel/video, автора или соавтора, дату, публичное количество просмотров; отсутствие данных не подменяется нулём.
4. Выбирается до 100 видео с наибольшим views на каждый профиль; не обещаем полноту истории аккаунта.
5. Для выбранных роликов вызывается существующий worker/local_worker.py::exact_download_job (подтверждение shortcode) с engine=off, без Whisper и платных LLM. Повторные файлы проверяются по source URL, shortcode и SHA-256.
6. Каждый шаг и snapshot_id сохраняются локально. При сбое повтор платного trigger при неизвестном результате запрещён.

## Папки

MP4: %USERPROFILE%\Downloads\InstagramArchive\<username>\<shortcode>.mp4
Метаданные: рядом с видео, shortcode.json
Состояния: %USERPROFILE%\Downloads\ContentMachineInstagramTop100\runs\<run_id>.json

## Настройка

Использует существующие настройки Bright Data из Content Machine: CM_BRIGHTDATA_API_TOKEN, CM_BRIGHTDATA_METRICS_ENABLED=1 и CM_BRIGHTDATA_METRICS_RIGHTS_VERIFIED=1 (или подтверждения Studio). Токен не передаётся в React. Нужны явные чекбоксы согласия с платным сбором и на хранение медиа. Предполагаемый верхний объём платных записей отображается до старта.

## Важные ограничения

- Источник может вернуть неполный список. Если публикаций с 1 ноября меньше 100, скачиваются только доступные и проверенные. Популярные Reels вне лимита scan_limit могут не попасть в результаты.
- Нет обхода приватных аккаунтов, авторизации, CAPTCHA, блокировок, rate limits.
- Несколько тысяч аккаунтов обрабатываются последовательно, а не одновременно.
- Возможен ручной resume по уже сохранённому snapshot_id, без повторного платного POST.
- Live Bright Data и Instagram MP4 в этой ветке не проверены. Обязательный тест на пользовательском ПК: 1 аккаунт + 1 MP4, затем тест 10 и 100.
- Нельзя считать отсутствие результатов подтверждением отсутствия публикаций.
- UI: cd instagram-top100-ui; npm install; npm run build. Dev UI: npm run dev (Vite 5174, backend 8767).
