# Полные автономные копии Group A / Group B / Group C в Market Intelligence

Ветка: `feature/rtb-market-intelligence-20261030`. Только тестовый контур,
не включать автоматически платные вызовы моделей или провайдеров.

## Источники
- Group A: `feature/group-a-full-module@88befd74b102e1ff09c46ba674406c9ba31b476d`;
  Git-tree `3f8575a2fd94f888330a303de94c190f3fcf1864`.
- Group B: `feature/group-b-full-module@ba660a8dd4b42711d73b1f0028aa6a7f4d5f5fec`;
  Git-tree `0d56e27adbad1d9acb47c651cd6500b02d71d214`.
- Group C: `feature/group-c-full-module@521ff74c9eda0279fc1318be7d187de3b44ffcf0`;
  Git-tree `5d55235f53f778238cc9d08151903ffa7e04a538`.

## Структура
- `intelligence/market_intelligence/_copied/group_a/` — все оригинальные 85 файлов A
- `intelligence/market_intelligence/_copied/group_b/` — все оригинальные 75 файлов B
- `intelligence/market_intelligence/_copied/group_c/` — все оригинальные 32 файла C

Копирование сделано ссылками Git tree в одном репозитории: **файлы
побайтно идентичны первоисточнику**. Исходные ветки и текущие
`intelligence/group_a`, `intelligence/market_intelligence/technical.py`,
`creative.py`, `performance.py` не меняются.

## Принцип интеграции
- Новая копия находится в независимом namespace
  `intelligence.market_intelligence._copied`, исходные `intelligence.group_a/b/c`
  не требуются для импорта и не должны использоваться в тестовом контуре.
- Отдельный `replica_probe.py` проводит offline smoke и
  проверяет отсутствие побочного API-действия.
- Факт копирования исходников **не означает**, что все блоки B1–B8
  автоматически получают исходные медиа, модели, права и исполняют полный
  pipeline. Полному B нужны подготовленные медиа, привязанные источники,
  авторизованный поставщик модели и корректные результаты.
- Кластеризации видео **между авторами** в A/B/C нет — это отдельный слой MI.
- Обратная совместимость внешних абсолютных импортов/DDL и местами
  тяжёлых внешних пакетов должна быть отдельно проверена CI; не
  подменяем копирование ложным заявлением о полном E2E.

## Как проверить
```
python -m compileall -q intelligence/market_intelligence/_copied
python -m unittest discover -s tests -p 'test_market_replica_smoke.py' -v
python -m intelligence.market_intelligence.replica_probe
```
Документирование результатов offline-тестов: Github Actions Workflow.
