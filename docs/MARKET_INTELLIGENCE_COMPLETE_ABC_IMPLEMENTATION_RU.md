# Market Intelligence — полный цикл данных → выбранное медиа → A/B/C
Дата: 2026-10-31. Ветка: feature/rtb-market-intelligence-20261030.
Это план завершения, НЕ заявление о готовом production E2E.

## Почему прежний вариант неполный
Раннее независимое MI использовало только Group A технический FFmpeg + до
12 отобранных кадров и опциональные ASR/OCR; Group B кодовые B1/B2/B3/B7
категории применялись через единственную модель не более чем к 8 кадрам.
Group C использовалась только для creator ratio / views/hour; B1–B8 DAG,
QDP/GA exact snapshot/paid quotas/receipts, другие C cohort/gates и видео
производство не исполнялись в MI. Простое копирование исходников не
разворачивает их зависимости и не делает полноценный видеоанализ.

## Что реализовано сейчас
- Bright Data преднастроенный read-only ключ, результаты TikTok Posts,
  Instagram Reels, YouTube Videos posts после разрешения внешнего сбора.
- D1 пять основных публичных счётчиков (views, likes, comments count,
  shares, saves), одинаковое определение/платформа/источник.
- Миграция 081: каждое успешно импортированное D1 наблюдение хранит также
  все встреченные числовые поля Bright Data (плоские + вложенные
  объекты ограниченной глубины), с исходными именами в
  mi_brightdata_numeric_fields. Нет текстов комментариев, длинных raw
  payload, персональных числовых идентификаторов. ID и даты — provenance,
  не performance-метрики.
- Никакого автоматического извлечения retention из видео или
  моделирования CTR. Нет live paid provider и mp4 smoke по клиентскому
  аккаунту; CI не доказывает наличие retention-полей.

## Полный pipeline / критерии приёмки
1. **Search & register**: пользовательские аккаунты / ролики / поисковые
   запросы. Реальный авторский crawl с пагинацией, отметкой покрытия,
   дедупликацией (не текущий 100 posts/profile).
2. **Bright Data enrichment**: dataset schema discovery per platform,
   source-specific typing и unit descriptors для каждого полученного
   numeric поля; запись в canonical D1 + immutable extended numeric store;
   null/not_public/not_permitted различать; тексты комментариев не собирать.
   Для сохранения и обработки проверить коммерческие права и retention.
3. **Retention source**: watch time, average view duration,
   completion rate/retention curves получать ИСКЛЮЧИТЕЛЬНО если реальные
   значения доступны в Bright Data или через авторизованную аналитику
   собственных аккаунтов (YouTube Analytics, TikTok/Meta insights).
   У чужих аккаунтов эти метрики зачастую не публичны.
4. **Selection**: выбрать явные video IDs по критериям качества и
   балансу успех/контроль, записать immutable versioned batch. Отмечать
   недоступные файлы — не считать завершёнными.
5. **Acquisition**: каждый выбранный источник с подтверждёнными
   can_fetch/can_store/can_analyze через существующий
   intelligence.project_sources.video_jobs (source/version/ID, checksum,
   anti-repeat, local media roots). Дозированная очередь, невозможность
   бесконтрольной загрузки и повторов, коды inaccessible/revoked.
   TikTok photo carousel требует отдельного пути набора оригинальных слайдов.
   Это ЕЩЁ не автоматизировано в MI.
6. **Full Group A**: оригинальные A intake, validated media manifest,
   аудио/видео extraction, временная шкала всех сегментов, OCR/ASR,
   output SHA/receipts, exact QDP handoff. Повторно проверять права.
   Текущий MI анализирует лишь неполное подмножество.
7. **Full Group B B1–B8**: не просто 8 статичных кадров. Реальный QDP/GA
   exact snapshot, блоки B1 hook, B2 format, B3 story, B4 visual,
   B5 editing, B6 subtitles, B7 CTA, B8 topic, typed evidence timestamps,
   model provider/usage ledger, retry reconciliation, independent
   validators, statuses для неописанных моментов.
   Только после полного B1–B8 receipt отмечать «полный».
8. **Full Group C**: сравнимые creator/niche cohorts с публикационной
   поправкой, engagement smoothing, breakout_score, quality gates,
   historical growth, current source policies и pinned config. Не считать
   C complete при одних creator_ratio и views/hour.
9. **Market analysis → RTB**: связь каждого формата с исходными
   видео/авторами/достоверными цифрами, семантическая кластеризация
   схожих механик, межплатформенное сравнение, независимые авторы,
   трендовый временной ряд, проверенные входы Opus.
10. **E2E acceptance**: на ПК пользователя сначала 1 выбранное видео →
    MP4/слайды → A1–A8 → B1–B8 → C0–C* → snapshot/Opus; потом
    5/100/1500 с измеренными расходами, правами, логами ошибок и
    контрольными обычными видео. CI mock/PG17 не заменяет live тесты.

## Текущие ограничения и безопасность
- Часть значений не публикуется платформами и физически не добывается
  парсингом чужих страниц. Ни один агрегатор не может гарантировать
  100% watch-time чужого видео.
- До проверки тарифа provider trigger остаётся выключенным.
- Доступ по API Bright Data подтверждён только авторизацией + каталогом,
  не live-загрузкой и не полными dataset fields.
- Автоматическое получение каждого выбранного ролика и полная B/C
  интеграция — НЕ ЗАВЕРШЕНЫ. Не показывать их как PASS.
