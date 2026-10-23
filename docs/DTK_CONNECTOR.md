# ContentMachine ↔ Evil0ctal DTK v5 (TikTok tool)

**Статус:** интеграция с *отдельным* self-hosted DTK v5 по локальному REST API.
Исходник DTK: https://github.com/Evil0ctal/Douyin_TikTok_Download_API
Лицензия upstream: Apache-2.0. Мы не копируем код или БД upstream в ContentMachine.

## Зачем

Tool Registry получает `dtk_tiktok` для будущего Search Planner. Уже сейчас в
**Discovery Engine → TikTok Tool** можно вручную отправлять задания поиска,
чтения видео/авторов, комментариев, хэштегов и звуков; результат читается по
`task_id`. Адаптер не запускает сбор фоном, не импортирует данные в Discovery,
не скачивает медиа, не обходит запреты платформы и не записывает чужие метрики.

Временно не подключены: автоматическое создание заданий из RTB, SRE,
monitoring, пагинация без ручного cursor, отдельное хранилище данных провайдера.
Сами URL и результаты могут храниться **внутри DTK**, если в upstream включён
архив; см. блок о правах.

## Установка

1. Разверни независимый DTK v5 по его [официальной инструкции](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/documents/en/02-installation.md).
   Используй проверенную версию или SHA, а не бесконтрольный `latest`.
   По официальной схеме:

   ~~~bash
   git clone https://github.com/Evil0ctal/Douyin_TikTok_Download_API.git
   cd Douyin_TikTok_Download_API
   # заполни .env согласно upstream docs, в т. ч. DTK_SECRET_KEY
   docker compose -p dtk -f docker/compose.yml up -d
   ~~~

2. Создай администратора DTK через его мастер первоначальной настройки.
   В DTK создай **отдельный API key с минимальным scope `tiktok:read`**;
   полный `admin` key в ContentMachine не нужен.
3. В **корневой локальный `.env` ContentMachine** (gitignored) добавь:

   ~~~dotenv
   CM_DTK_ENABLED=1
   CM_DTK_BASE_URL=http://127.0.0.1:8000
   CM_DTK_API_KEY=dtk_ВАШ_КЛЮЧ
   ~~~

   Важна именно IPv4-адресация `127.0.0.1`. Этот коннектор намеренно не
   принимает адреса в LAN, интернете, имена хостов или HTTPS-URL:
   защита локального приложения от SSRF. Не передавай ключ в чат, браузер,
   GitHub, логи или файлы диагностики. Защити доступ к локальному `.env`.
4. Перезапусти ContentMachine (изменения `.env` читаются на запросах; повторный
   запуск гарантирует, что процесс увидит текущие настройки).
5. Открой `http://127.0.0.1:8765` → **Discovery Engine** → **TikTok Tool**.
   Нажми «Проверить подключение». Если есть `tiktok:read`, будут доступны
   поиск и чтение. Если DTK не запущен, видишь `unavailable`, не ложный успех.

## Операции

| В UI | action | Параметры |
| --- | --- | --- |
| Поиск видео | `search_posts` | `keyword`, optional `cursor` |
| Поиск аккаунтов | `search_users` | `keyword`, optional `cursor` |
| Видео | `video` | `aweme_id` |
| Похожие видео | `related_posts` | `aweme_id` |
| Комментарии | `comments` | `aweme_id`, optional `cursor` |
| Ответы | `comment_replies` | `aweme_id`, `comment_id`, optional `cursor` |
| Профиль | `user` | `sec_user_id` |
| Видео автора | `user_posts` | `sec_user_id`, optional `cursor` |
| Хэштег | `hashtag` | `name` |
| Видео по хэштегу | `hashtag_posts` | `hashtag_id`, optional `cursor` |
| Звук | `music` | `music_id` |
| Видео со звуком | `music_posts` | `music_id`, optional `cursor` |

Внешний DTK использует модель async tasks:
- `POST /api/tools/dtk/run` c `{ "action":"search_posts", "params":{"keyword":"home workout"} }`
  отправляет **один** запрос в локальный DTK и возвращает HTTP 202 + `task_id`.
- `GET /api/tools/dtk/tasks/<uuid>` читает его состояние и данные, если `done`.
- `GET /api/tools/dtk/status` возвращает доступность, capabilities, scope
  и **никогда** не раскрывает ключ.

С точки зрения браузера POST требует `X-ContentMachine: 1`, локальный Host и
проверку Origin, как и прочие изменения ContentMachine. Внешние запросы к DTK
выполняются без следования HTTP-редиректам, ограничены по времени и размеру.
Запросы не повторяются автоматически при таймауте: открой DTK task list/log,
прежде чем отправить аналогичный запрос второй раз.

## Ограничения реального TikTok-поиска

По [upstream API guide](https://github.com/Evil0ctal/Douyin_TikTok_Download_API/blob/main/documents/en/11-api.md),
поиск видео гостем может давать очень мало результатов и не поддерживать
полноценные следующие страницы. DTK явно предупреждает о необходимости
авторизованной identity для дальнейшей пагинации. **Полученный пустой
результат не доказывает отсутствие роликов.** Состояние авторизации аккаунтов,
доступность TikTok, условия платформы и реальную стабильность нужно проверить
в работающем DTK на своём компьютере.

## Хранение, права, безопасность

**Важный пункт:** в стандартной конфигурации DTK v5
`archive.enabled=True`: сторонний сервис может архивировать данные **до**
получения ответа нашим адаптером. Если право хранения чужих публикаций
не подтверждено, в настройках DTK отключи `archive.enabled` до первого
поиска и согласуй его собственные retention/cache/log параметры. Мы не имеем
права называть результаты внешнего API `manual_local_d1` или импортировать
их в PostgreSQL без отдельной source policy/licensing. Работа с авторизованной
TikTok identity и коммерческий сбор регулируются правилами платформы.

Для безопасной автоматизации следующий этап должен отдельно внедрить
лицензионный source registry, persistent `collector_attempts`, дедупликацию,
права на метаданные и ingest в существующую Discovery D1.

## Тесты и границы готовности

Без TikTok и без DTK:
~~~bash
python -m unittest discover -s tests -p "test_dtk_connector.py" -v
node --check site/dtk-tool.js
~~~

`tests/test_dtk_connector.py` проверяет whitelist, локальный URL, отсутствие
утечки ключей, async `task_id`, ответы ошибок и same-origin POST.
Установка/конфигурация и **живой end-to-end TikTok search** этими тестами
не подтверждаются — они требуют работающей отдельной инсталляции DTK.
