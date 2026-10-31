# Как проверить Marketing Group A, B и C на Windows

Все команды ниже запускаются **из папки репозитория `ContentMachine`**, а не из `C:\Users\User`. Удобно открыть папку в проводнике Windows и написать `powershell` в адресной строке. Эти тесты ничего не загружают на OpenRouter/Bright Data, не требуют API-ключей и не запускают QDP.

## 1. Переключись на ветку с новой реализацией

```powershell
git fetch origin
git switch feature/rtb-market-intelligence-20261030
git pull --ff-only origin feature/rtb-market-intelligence-20261030
git status --short
```

Если локальной ветки ещё нет и `git switch` сообщает, что она не найдена:

```powershell
git switch --track origin/feature/rtb-market-intelligence-20261030
```

Не запускай тесты с `main`: новая Group A/B/C находится в **Draft PR #48**, не объединённом с main.

## 2. Одна установка зависимостей

```powershell
python --version
ffmpeg -version
ffprobe -version
node --version
docker --version
python -m pip install -r intelligence/requirements.txt
```

Python 3.11+ необходим. FFmpeg/FFprobe нужны для тестов реальных MP4. Node.js нужен для проверки JS. Docker Desktop должен работать только при запуске проверки базы данных. Если команда не распознаётся, установи соответствующую программу и **открой PowerShell заново**.

## 3. Проверить все A/B/C без базы данных и платных API

```powershell
.\RUN_MARKETING_TESTS.ps1
```

Если политика Windows блокирует выполнение скриптов, используй не меняющий системные настройки BAT:

```powershell
.\RUN_MARKETING_TESTS.bat
```

Или выполни однократную команду:

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\RUN_MARKETING_TESTS.ps1
```

Этот режим проверяет: контракты A, все B1–B8, C0–C7, 100 синтетических видео с 10 авторами и 2 платформами, source SHA/таймкоды, автоматический batch completion, Bright Data preflight без API-запросов, HTTP/UI contracts, FFmpeg smoke и безопасность несуществующих/отозванных/исправленных метрик. **Синтетические данные используются только в тестах**, не в рабочей базе.

## 4. Полная проверка с реальной PostgreSQL 17

Запусти Docker Desktop и выполни:

```powershell
.\RUN_MARKETING_TESTS.ps1 -Full
```

Скрипт сам создаст **временный контейнер `cm-mi-test-...`** с PostgreSQL 17 на случайном локальном порту. Он выполнит свежие миграции, повторный прогон миграций на идемпотентность и проверку таблиц `mi_video_analyses`, `mi_brightdata_numeric_fields`, `market_intelligence_snapshots`, ограничений версий v1–v4, FK и запрета на UPDATE/DELETE рыночных снимков. После проверки контейнер будет удалён.

**Твоя обычная база данных Content Machine и контейнер `contentmachine-postgres` не используются и не изменяются.**

Можно запустить только базу с основными тестами, без обязательной проверки наличия Node:

```powershell
.\RUN_MARKETING_TESTS.ps1 -WithDatabase
```

## 5. Проверить собственный реальный MP4

```powershell
.\RUN_MARKETING_TESTS.ps1 -Video "D:\Videos\sample.mp4"
```

Или вместе с базой:

```powershell
.\RUN_MARKETING_TESTS.ps1 -Full -Video "D:\Videos\sample.mp4"
```

Система локально вычислит SHA-256 файла, запустит оригинальные скопированные технические алгоритмы Group A, создаст реальные sampled frames, попытается локальную обработку audio/OCR с отключёнными платными внешними моделями, сформирует восемь **честных частичных** B-отчётов и проверит A→B SHA-контракт. Твой MP4 никому не отправляется. Если модель не была установлена — соответствующий этап покажет UNKNOWN/UNAVAILABLE, а не вымышленный результат. Group C на таком файле *невозможно* проверить без настоящих исторических D1 метрик: поддельная вовлечённость не создаётся.

## 6. Где смотреть результаты

Каждый запуск создаёт папку:

`marketing-test-reports\YYYYMMDD-HHMMSS-xxxxxxxx\`

В ней:

- `report.json` — итоговые статусы `PASS`, `FAIL`, `SKIP`, `BLOCKED` и точная версия Git;
- `A_video_audio__*.log` — FFmpeg/GA ошибки;
- `B_eight_blocks__*.log` — ошибки B1–B8 и доказательств;
- `C_performance__*.log` — C0–C7, формулы, 100 видео;
- `fresh_migrations_postgres17.log` — установка миграций и SQL;
- `real_video_report.json` — SHA и подробные статусы A/B для твоего MP4, без копирования самого видео.

Папка добавлена в `.gitignore`. **Отправь мне `report.json` и `.log` у шагов с FAIL**, и я смогу исправить конкретные ошибки в GitHub. Прежде чем пересылать файлы, проверь логи на наличие личных путей к твоим видео.

`PASS` подтверждает только соответствующий тест, **не** весь production pipeline. `SKIP` не считается пройденным, а `BLOCKED` означает, что не хватает среды. `--Full` без завершённых проверок Docker/FFmpeg/Node не выдаёт успешный вердикт.

## 7. Полный реальный прогон 100 роликов

Для проверки маркетинговой системы на реальных платформах в самом интерфейсе запусти пакет `Анализ 100 видео`, заранее подготовив источники с действительными правами и разрешённым доступом к Bright Data/OpenRouter. Тестовый скрипт **намеренно не создаёт платных заданий и не включает права за пользователя**. После реального запуска смотри статус пакета, количества A/B/C, причины `rights_required`, `awaiting_exact_metrics_or_media` и ID финального аналитического снимка.

**Не путай:** успешно прошедший стресс-тест на 100 синтетических публикаций + один локальный MP4 **не** подтверждают сбор и полный анализ 100 настоящих видео.
