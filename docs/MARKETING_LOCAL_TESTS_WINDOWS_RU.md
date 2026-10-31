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

## 7. Проверка уже собранного реального проекта — только чтение

Если у тебя уже создан проект в Content Machine и работают локальная база с реальными скачанными видео/метриками, можно проверить фактические A/B/C-результаты **без повторного выполнения дорогостоящего pipeline**:

```powershell
python scripts/check_market_live_project.py --project-id ВАШ_ID_ПРОЕКТА --expect 100 --output marketing-test-reports/real-project.json
```

`ВАШ_ID_ПРОЕКТА` — внутренний ID проекта (32 шестнадцатеричных символа), не его название. Этот режим читает существующий PostgreSQL через обычные локальные настройки Content Machine. Он проверяет SHA отчётов A/B/C по каждому видео, наличие точного C0, наличие C2/C5/C7, итоговый ID снимка и состояние всех выбранных видео. **Записи в базу, сбор через Bright Data, скачивание или LLM-вызовы он не выполняет.** Если какой-то этап не готов, команда заканчивается с кодом 2 и выводит отсутствие покрытия, а не изображает успешный полный прогон.

## 8. Полный реальный прогон 100 роликов

Для проверки маркетинговой системы на реальных платформах в самом интерфейсе запусти пакет `Анализ 100 видео`, заранее подготовив источники с действительными правами и разрешённым доступом к Bright Data/OpenRouter. Тестовый скрипт **намеренно не создаёт платных заданий и не включает права за пользователя**. После реального запуска смотри статус пакета, количества A/B/C, причины `rights_required`, `awaiting_exact_metrics_or_media` и ID финального аналитического снимка.

**Не путай:** успешно прошедший стресс-тест на 100 синтетических публикаций + один локальный MP4 **не** подтверждают сбор и полный анализ 100 настоящих видео.


## 9. Установка настоящих локальных моделей Group A на Windows (отдельный opt-in)

`probe_market_video.py` по умолчанию **намеренно отключает** ASR, VAD,
OCR и внешнюю мультимодальную модель. Для теста реальных локальных моделей
используй `--local-models`: он по-прежнему никогда не отправляет MP4
во внешний API и не создаёт платных запросов.

Из корня `C:\ContentMachine`:

```powershell
winget install -e --id UB-Mannheim.TesseractOCR
python -m pip install faster-whisper huggingface-hub torch
python -m scripts.setup_market_group_a_models --ocr
python -m scripts.setup_market_group_a_models --vad
python -m scripts.setup_market_group_a_models --asr
python -m scripts.probe_market_video --local-models --video ".\marketing-qa\videos\01.mp4" --output ".\marketing-qa\reports\01-local-models.json"
```

Tesseract установи заранее с необходимыми **официальными языковыми
пакетами** (в первую очередь `eng`, для русских надписей `rus`). При
уже установленном Tesseract пропусти winget-команду. Скрипт обнаруживает
установленный 5.x, проверяет реальные `eng/rus/osd` traineddata и закрепляет
хэш самого бинарника и выбранных пакетов. Обновление бинарника или словарей
меняет fingerprint: понадобится повторить `--ocr`.

`--vad` скачивает оригинальный Silero JIT из неизменяемого git-коммита,
проверяет локальный SHA и запуск CPU-инференса; `--asr` получает
многоязычный `Systran/faster-whisper-small` с неизменяемого Git revision,
копирует локальные файлы без symlinks, проверяет полный SHA-инвентарь и
загрузку модели CPU. Установка требует Интернет **только на этапе
явного provisioning**. Во время самого анализа модель работает
локально, без загрузки весов и API-ключей.

Параметры сохраняются только в существующем приватном `.env` — другие
настройки остаются неизменными. Скачанные файлы и отчёты под
`marketing-qa/` исключены из Git. При запуске настоящего медиа-worker
перезапусти сервер, чтобы он перечитал конфигурацию.

Audio Events/GA15 не включается автоматически: требуются отдельно
разрешённая/лицензированная конкретная модель ONNX, совместимые layout
и label map, подтверждённые SHA и разрешения. До настройки этот
этап честно остаётся `unavailable`. Не объявляй весь Group A
сертифицированным, пока этот компонент не проверен.
