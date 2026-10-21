# Video Collector v0 — безопасный офлайн-планировщик

**Статус: отдельный технический шаг, не включённый Live Apify Collector.**
Этот модуль НИКОГДА не делает вызовы Apify/TikTok/OpenRouter, не списывает
деньги и не сохраняет найденные материалы в базу.

## Для чего

`intelligence.collector.planner.build_preview` получает опубликованное
дерево **с двумя завершёнными Human Review** и создаёт
`searchQueries`, `resultsPerPage`, `searchSection="/video"`
для публичного Actor `clockworks/tiktok-scraper`.
Поисковые строки берутся только из названий УТВЕРЖДЁННЫХ
исследовательских гипотез и их корневых направлений,
поэтому каждый запрос связан с `node_id`, `direction_id`,
`source_fact_ids` и номером таксономии. За один предварительный
план — не более **20** запросов, не более **25** результатов
на запрос; никаких скрытых дорогих date/sorting filters.

**Важно:** `publication_verified=True` может задавать только серверный
код, который запросил `tb_deep_publications` по
`project_id` и `run_id` и сверил проект, версию и immutable
статус. Нельзя доверять флагу из браузера. Поэтому сетевой
endpoint пока не открыт: сначала нужно закончить RTB PR #9
и добавить серверную проверку публикации.

`intelligence.collector.apify_preview.inspect_items` обрабатывает
синтетические или добровольно предоставленные результаты
из публичного формата Clockworks. Важные поля:
`webVideoUrl`, `id`, `text`, `createTimeISO`,
`isSlideshow`. Записи с ошибками, неверным видео-ID,
внешними хостами, slideshow или повторным ID отклоняются.
Видео/медиа, личные данные автора и метрики вроде
`playCount/diggCount` здесь **специально не сохраняются**,
потому что предварительный просмотр не является лицензией
на их коммерческую обработку. Результат `persisted=0`,
`paid_calls=0`.

## Бесплатный пример в Python

```python
from intelligence.collector.planner import build_preview

# Не использовать с draft: это должен быть именно tree из immutable
# публикации RTB, полученный сервером после проверки прав и project scope.
plan = build_preview(
    project_id="a"*32,
    taxonomy_version=1,
    published_nodes=approved_tree_from_database,
    publication_verified=True,
    max_queries=4,
    results_per_query=5,
)
print(plan["actor_input"])
# Никакой POST/оплаты не происходит.
```

## Прежде чем делать реальный Apify integration

1. Проверить доступ, условия коммерческого хранения metadata/metrics,
   актуальную pricing model и scope пользовательского аккаунта.
2. Создать серверные `collector_plan` + `collector_attempt` записи с
   привязкой к immutable taxonomy и отдельным денежным разрешением;
   raw run/dataset IDs и идемпотентность.
3. Продумать возможные расходы **до** POST: верхний финансовый
   лимит провайдера; если невозможно гарантировать, не делать POST.
4. Отдельный явный Submit: Actor запуск строго 1 раз,
   HTTP ошибки/таймауты — `uncertain`, только read/poll/reconcile
   по сохранённому Actor run ID, никакого автоматического POST.
5. Когда provider policy разрешает, передавать канонические видео
   в существующий Discovery D1 с **достоверным external-source provenance**.
   Нельзя выдавать внешние видео за `manual_local_d1`.
6. Потом — SRE `unmatched/uncertain`, аналитика и при необходимости
   переутверждение RTB Taxonomy v2.

Проверка: `python -m unittest discover -s tests -p
"test_video_collector_*py" -v` — полностью бесплатная.

Официальные источники:

- https://apify.com/clockworks/tiktok-scraper/input-schema
- https://apify.com/clockworks/tiktok-scraper/output-schema
- https://docs.apify.com/api/v2
- https://developers.tiktok.com/docs/en/research-api-faq

Стоимость поиска на сайте может измениться; текущий Actor
объявляет минимальную цену `from $1.70/1000 results`, но это
не полный тариф и не обещание фактической стоимости одного
квалифицированного, уникального видео.
