# SRE M44 — chaos/load, реализация без ожидания CI

Дата: 2026-10-29. **SOURCE PARTIAL**, настоящие chaos, concurrency, crash recovery, PG17 backup/restore и нагрузочные показатели **ещё не измерялись**. Вопрос с исторической 062 не скрывается, но не задерживает добавление независимого тестового генератора.

## Реализовано в SRE branch

- `intelligence/relevance/m44_chaos_plan.py`: строгий и детерминированный тестовый генератор для **100 / 1 000 / 10 000 видео** × **1–48 одобренных гипотез** (на текущем этапе ключи синтетические, не выдаются за публикацию C9). `iter_bounded_pairs` выделяет максимум `chunk_size × category_count` пар одновременно, где `1 <= chunk_size <= 1000`, и ничего не кеширует между yield. При верхней границе одного чанка максимум 48 000 пар, но не 480 000 пар для всего набора. Число ожидаемых пар: `N × K`.
- `plan_chaos`: детерминированный SHA-256 плана, полный count chunks/pairs и машинно-читаемые сценарии fault injection со статусом `not_executed`: dual claim M14, lease-death M14/M15, ambiguous paid intent M26/M29, revoke race M34, outbox ACK replay M36, restart after commit M15, backup/restore с проверкой fences/rights. Ни одна инварианта не маркируется PASS до реального исполнения.
- `scripts/sre_m44_disposable_plan.py`: автономный CLI, принимает `--videos`, `--leaves`, `--chunk` и опциональный `--seed`; строит repeatable UUID5 synthetic keys. По умолчанию только маленькая JSON metadata. Флаг `--enumerate` последовательно вычисляет SHA-256 всех синтетических пар, не подключая БД и не записывая источники.
- Ни импорта OpenRouter, ни подключения к PostgreSQL, ни SQL, ни модификации `main`, ни fake-result в LIVE таблицах. Этот генератор **не меняет** existing M13/M14/M29 production runtime и не создаёт платные задачи.

Примеры для ручного запуска позднее, если потребуется:

```bash
python -m scripts.sre_m44_disposable_plan --videos 100 --leaves 3
python -m scripts.sre_m44_disposable_plan --videos 1000 --leaves 20 --chunk 100
python -m scripts.sre_m44_disposable_plan --videos 10000 --leaves 48 --chunk 250 --enumerate
```

## Осталось до закрытия M44

- [x] Source-only repeatable bounded synthetic load/chaos scenario enumerator.
- [ ] Реальный disposable PostgreSQL 17 dual-worker race: один захват lease, никто не выполняет двойную оплату.
- [ ] Crash/restart после M26 intent с возможным списанием: `UNKNOWN` без автоматического повторного POST.
- [ ] Revocation между claim и commit; права блокируют storage/export, физическая очистка по M34 отдельно.
- [ ] Outbox publish/claim/ACK replay с реальным PG M36 и downstream contract.
- [ ] Backup/restore на реальной PG17 базе с проверкой generation/lease/rights.
- [ ] Настоящие 100/1k/10k planning измерения памяти/времени с реальным C9/D1, не только синтетическими ключами.
- [ ] Никаких чисел «масштабируемость доказана», пока нет raw measurements и exact SHA.

**Важно:** по просьбе владельца не ждать невыполненных CI-тестов, код M44 развивается дальше. Сам факт генерации плана НЕ означает, что гонки, платная бухгалтерия, retention или 10 000-видео throughput прошли. PR #22 остаётся Draft; `main` и другие feature ветки не изменялись.
