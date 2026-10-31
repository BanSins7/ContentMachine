# Bright Data — первый тест на Windows, без платного запуска

Запускать только из C:\ContentMachine на собственной машине, в ветке
`feature/rtb-market-intelligence-20261030`. Тест **НЕ запускает**
`/trigger`, `/scrape`, jobs, LLM, медиа или миграции. Применяется
официальный безопасный read-only GET `/datasets/list`.

1. В PowerShell:
   ```powershell
   cd C:\ContentMachine
   git branch --show-current
   git pull --ff-only origin feature/rtb-market-intelligence-20261030
   ```
2. Если API-токен раньше был выложен в чат или публичный лог, отозвать
   его в настройках Bright Data и выпустить новый.
3. Рекомендуемый путь: запусти `SETUP_BRIGHTDATA_ONCE.bat` из папки проекта.
   Он один раз попросит токен скрытым вводом, сохранит его в `.env`
   и проверит API; при уже настроенном токене вводить ничего не нужно.

   Альтернативный ручной путь — отредактировать **локальный игнорируемый** `.env`:
   ```powershell
   notepad .env
   ```
   Добавить только на локальной машине:
   ```dotenv
   CM_BRIGHTDATA_API_TOKEN=LOCAL_NEW_TOKEN
   CM_BRIGHTDATA_METRICS_ENABLED=0
   CM_BRIGHTDATA_METRICS_RIGHTS_VERIFIED=0
   ```
   Токен не коммитить и не присылать в чат, логи или скриншоты.
   Выключенные флаги специально предотвращают запуск платных
   задач при открытии сайта после теста. Тест preflight проверяет
   ключ независимо от этих флагов.
4. Выполнить:
   ```powershell
   python -m intelligence.market_intelligence.brightdata_preflight
   ```
5. Интерпретация:
   - `status: authenticated` и HTTP 200: токен принят, каталог доступен.
   - `status: unauthorized` и HTTP 401: ключ неверен/отозван/нет доступа.
   - `status: check_failed`: сеть или структура ответа; см. `reason_code`.
   - `targets.platform.listed: true`: ID подключённого в коде скрапера
     присутствует в каталоге аккаунта. `false` означает, что нужно
     проверить ID/лицензию, но не доказательство отсутствия доступа.
   - `collection_started: false`: не выполнен ни один платный сбор.
6. Передать в чат **только выведенный JSON** без содержимого `.env`.
   Далее — отдельный согласованный live test на одном ролике, с учётом
   тарифа и прав провайдера.

Документация: https://docs.brightdata.com/api-reference/marketplace-dataset-api/get-dataset-list
