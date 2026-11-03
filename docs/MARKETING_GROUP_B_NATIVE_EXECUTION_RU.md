# Marketing Group B: локальный B1–B8 без QDP

## Архитектура

Marketing использует свою зарегистрированную публикацию, exact-ID/rights-approved MP4, Group A `mi-group-a-media-report/v1` и локально скопированные taxonomy/kernels в `intelligence/market_intelligence/_copied/group_b`. В Marketing **не исполняются Group B QDP adapters, Group B external worker или QDP-owned DB**. Сохранение — только `mi_video_analyses.creative.group_b`, метод `mi-abc-b8-native-media/v4`, миграция `084_mi_group_b_native.sql`.

`replica_b_timeline.py` отправляет настоящие SHA-проверенные GA07 кадры в настройками источника разрешённую мультимодальную модель, последовательно, окнами по 8 кадрам (не более 48 кадров на видео). Модель видит только actual byte proof + GA11-синхронизированный ASR или SRT, а не фиктивный QDP context. Структурный JSON возвращается с провайдерским ID и ссылками на кадры, неизвестные метки остаются UNKNOWN. При отсутствии `can_send_to_llm` анализ проводится без внешнего inference.

`group_b_report.py` выполняет независимый read-only audit: проверяет SHA Group A, заново пересчитывает хеш исходного frame manifest/GA OCR, исключает frame refs вне исходного видео, проверяет временные интервалы B3/B7 и copied-B taxonomy B1/B2/B3/B4/B7, запрещает непроверенные отрицательные утверждения и URLs. `native_evidence.make_receipt()` сверяет **весь** payload B1–B8 с report SHA, включая текст, стили, ID темы. Дальше в отчёте сохраняются A + B SHA и lineage конкретного видео, версии метода и источника. Никаких внешних branches/runtime сервисов.

## B1–B8

| Блок | Подключённые источники/алгоритмы | Граница достоверности |
|---|---|---|
| B1 Hook | Скопированная taxonomy B1; строго первые 5 секунд, типы хуков, sampled frame proof; `group_b_text_evidence` ищет **verbatim** текст в настоящих GA12 OCR и GA11-синхронизированной речи; flags только по реально обнаруженному тексту | Не подставляем поздний кадр вместо отсутствующего раннего; мнение модели ≠ аттестация дословной речи |
| B2 Format | Оригинальная taxonomy видеоформатов B2; голосование по окнам и вторичный формат только при достаточном числе наблюдений | Противоречивые окна не дают универсальной «истины» обо всём ролике |
| B3 Story | Оригинальная taxonomy ролей B3, временные интервалы и refs по всем успешно рассмотренным окнам | Это наблюдённые сегменты, **не гарантированные смысловые границы** |
| B4 Visual | Оригинальные 3 признака B4, `present` только с точным frame-ref каждого признака; AI origin остаётся null | Ни `absent` на ненаблюдавшихся кадрах, ни определение AI-generated «на глаз» |
| B5 Editing | GA06 реальная FFmpeg обработка видео + copied B5 thresholds скорости монтажа (пороговый proxy) | **Не полный verified shot-boundary catalog**; нулевая реакция фильтра не означает `single_take` |
| B6 Subtitles | GA14 embedded subtitle packet audit + измеренные actual OCR размеры/положение/время с refs; тот же B4/B8 multimodal запрос предлагает `position/font_size/font_style/colors/background_style/display_mode` с кадрами и copied-B6 validation | OCR надписи ≠ визуально доказанные субтитры, нет сертификации dynamic/standard/none без полного инспектора |
| B7 CTA | Оригинальная taxonomy CTA/destination/service; все наблюдённые окна, time-bound occurrences, отдельный GA verbatim text match | Модель не генерирует вымышленные URL, аккаунты или отрицательный «CTA не существует» |
| B8 Topic | Мультимодальные category/topic/subtopic *labels*; оригинальный `TopicRegistry` использует локальный **собственный** SHA-pinned каталог Marketing, проверяет иерархию, parent и merge; новое имя => `observed_unmapped` | Без утверждённого каталога невозможно честно возвращать утверждённые category/topic/subtopic ID |

## Каталог Marketing B8

Серверная настройка (один раз, не в пользовательском интерфейсе) задаёт `CM_MARKET_B_TAXONOMY_JSON` — компактный JSON следующей формы, и `CM_MARKET_B_TAXONOMY_SHA256` — SHA-256 **точных UTF-8 байт строки JSON**:

```json
{
  "schema_version": "mi-b8-taxonomy/v1",
  "categories": [
    {"id": 1, "slug": "wellness", "name": "Wellness", "parent_id": null, "merged_into_id": null}
  ],
  "topics": [
    {"id": 2, "slug": "face_yoga", "name": "Face Yoga", "parent_id": 1, "merged_into_id": null}
  ],
  "subtopics": [
    {"id": 3, "slug": "depuff", "name": "Depuff", "parent_id": 2, "merged_into_id": null}
  ]
}
```

Это иллюстративная структура, не зарегистрированные ID конкретного проекта. Реальный каталог требует собственного утверждения и pin. Отсутствие pin → `observed_unmapped`; **никогда** выдуманный ID. Ключ OpenRouter и право отправки медиа проверяются сервером, повторного пользовательского согласия в UI нет.

## Доказательства и приёмка

- `tests/test_market_group_b_native.py`: B1–B8, ссылки на реальные кадры, time windows, B4 feature refs, B6 pixel OCR, B8 taxonomy, tamper/checksum, ASR/CTA/hook proof.
- `tests/test_market_group_b_real_mp4.py`: **настоящий FFmpeg MP4** → скопированный Group A → B1–B8 → Marketing native A+B receipts.
- `tests/test_market_replica_execution.py`: fake model return и ошибка подменённого frame ref.
- `tests/test_market_native_evidence.py`: source version, SHA, temporal proofs.
- CI `.github/workflows/rtb-market-intelligence.yml`: Python 3.12 + PostgreSQL 17 migration contract + JS syntax.
- UI показывает per-video B1–B8 по нажатию, не 100 развернутых отчётов одновременно.

## Что **не** объявлено production complete

Оригинальные контракты Group B требуют подтверждения полноты *непрерывной* инспекции исходного видео и точного каталога монтажных склеек, а также юридически разрешённых источников и целиком проверенного B8 topic registry. Выборка до 48 кадров и классификатор OpenRouter физически не могут заверить отсутствие чего-либо между выбранными кадрами. Принудительно ставить `full_group_b_certified=true` здесь было бы ложной реализацией. Поэтому `group_b.native_report.original_full_group_b_certified=false` до настоящего непрерывного видеоанализа с подтверждёнными алгоритмами B4/B5/B6/B7. При отсутствии модели всегда `unavailable`, без фиктивной числовой оценки. Перед main нужен real-world прогон на 1, 5 и 100 видео с зафиксированными SHA, расходами и реальными model responses.
