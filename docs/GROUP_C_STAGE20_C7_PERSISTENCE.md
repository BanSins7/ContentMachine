# GC-MOD-20 — Атомарное сохранение полного C7 и неизменяемое доказательство

**Ветка:** \`feature/group-c-full-module\`. **Статус:** CODE IMPLEMENTED; синтетические pure/PG17 тесты написаны, полный exact-head PG17/CI **NOT VERIFIED**. Реальный IAM/QDP/Source и end-to-end worker/Analytics delivery **BLOCKED_EXTERNAL**. **Нельзя направлять в production, пока обязательные IAM и QA не подключены/не пройдены.** Main, другие feature-ветки и существующие SQL 003/004/056–059 не изменялись.

## Хранимые таблицы и схема

Сохраняем завершённый и стабильный \`calculated_metrics\` из миграций 003/004. \`intelligence/migrations/060_group_c_calculation_receipts.sql\` — строго **добавочная** миграция с таблицей \`group_c_calculation_receipts\`, которая связывает:

- \`calculated_metric_id\` (PK, FK), \`project_id\`, \`video_id\`, current/previous snapshot ID, \`published_metadata_id\`, \`config_id\`, \`creator_freeze_id\`, \`prior_freeze_id\` и nullable niche freeze;
- точные UUID human review request, approved decision, global activation event;
- \`review_subject_sha256\`, \`evidence_sha256\` и \`calculation_version\`, \`receipt_version=group-c-persisted-c7/v1\`.

\`UNIQUE(project_id,evidence_sha256)\` вместе с авторитетным старым ключом \`uq_c_calculation_lineage UNIQUE NULLS NOT DISTINCT (snapshot_id,config_id,previous_id,creator_cohort_id,niche_cohort_id,prior_cohort_id)\` предотвращает неоднозначное сохранение версии. PostgreSQL trigger требует именно \`score_status=complete\`, \`calculation_status=complete\`, совпадения всех PK/FK/metadata/когорт с \`calculated_metrics\`, оригинального approved human review и currently active global config/activation. UPDATE/DELETE сертификата блокируются; завершённый C7 уже неизменяем по триггеру 004. Никакого изменения hash старых миграций.

**Ограничение защиты:** если у приложения есть прямые права писать в \`calculated_metrics\`, существующая 003/004 схема технически может допустить другие легаси-пути. Production database roles должны запретить обход записи Group C мимо авторизованного writer; вводить любые изменения DB role нужно отдельно в deployment/integration, не объявляя SQL миграцию 060 полноценной IAM/role security. Проверка реальных DB grants остаётся обязательной.

## Строгий атомарный writer

\`PostgreSQLC7Persistence.persist\` из \`intelligence/group_c/c7_persistence.py\` требует независимое autocommit PG17 connection и внешне внедрённый \`C7PersistenceApprovalVerifier.verify_c7_persistence\`. Нет дефолтной permissive IAM имплементации. Возвращаемое от IAM значение должно быть literal \`True\`; \`False\`, integer \`1\`, \`None\`, отсутствие verifier и подложные обзоры блокируют любой score-write.

В **ОДНОЙ** \`SERIALIZABLE\` write-транзакции writer:

1. Устанавливает исходный canonical project/video, source/source_policy, точные \`previous/current snapshot_id\` + publication metadata и проверяет source rights, expiry, quality, non-assumed observed timestamps и корректность авторитетной view definition/version. Выбирает последние **два без фильтрации** raw-снимка, включая отменённые/исправленные/сменившие view-version; не использует \`last good\`.
2. Проверяет \`published_at\` только из source_video_metadata (GC08), строгую цепочку \`published_at <= previous.observed_at < current.observed_at\`, срок хранения и source-policy/version.
3. Заново читает \`approved\` human review, **единственную active глобальную** конфигурацию, полные оригинальные frozen creator/prior/optional-niche cohort evidence, пересоздаёт весь signed \`subject_sha256\` и сравнивает \`review_payload\`/minima/source/metric/alpha/k/priors/weights. Требует точный \`activated\` event той же review decision. Проверяет текущую доступность каждого peer source/metadata через policy.
4. По каждому peer повторно проверяет original legacy membership, project/platform/content kind, creator или niche topic, оригинальные raw snapshot IDs и views; для engagement prior пересчитывает pooled L/C/S из всех оригинальных raw counters и сверяет точные утверждённые frozen+legacy доли. Несоответствие блокирует финал.
5. Перед любой записью вызывает trusted external IAM-verifier, который обязан подтверждать действительность именно **текущего подписанного** человеческого решения и отдельное глобальное действие активации, полномочия project score-write и отсутствие revocation. Тестовый verifier существует только внутри disposable тестов.
6. Пересчитывает **с нуля** C1 Creator Ratio, C3 Views Growth, C4 raw engagement (+ display percent), C5 Bayesian k/priors, C6 48-hour Freshness, optional C2 niche diagnostic, C7 \`0.45*C1+0.30*C3+0.15*C5+0.10*C6\`. Только чистая Decimal математика, \`NUMERIC(30,12)\`, точная \`ROUND_HALF_UP\` компонента и затем C7; C2 НЕ входит в формулу, нет нормализации и предела 100.
7. Формирует fingerprint по исходным снимкам и counters, publication SHA, immutable frozen cohort SHA, reviewed subject/decision/activation IDs, source/version/политике и **всем** сохраняемым полям результата. Делает \`INSERT ... ON CONFLICT ON CONSTRAINT uq_c_calculation_lineage DO NOTHING\`. Если конфликт, уже сохранённая строка и неизменяемый сертификат должны **строго совпасть** по всем persisted полям + fingerprint; тогда возвращается существующий id/inserted=False; любое изменение lineage/summary/fingerprint вызывает \`c7_lineage_conflict\`. Новые complete row + certificate вставляются **внутри того же commit** (ошибка → полный rollback).

Ошибки SERIALIZABLE конфликтов \`40001\` намеренно не преобразуются в фиктивную успешную запись; вызывающий GC-MOD-21 worker должен безопасно повторить **всю** транзакцию после нового реального IAM/source reread. Внешнее IAM действие само по себе должно быть проверяемым/не вызывать побочных SQL-операций, нарушающих atomic score commit.

Возвращаемый frozen \`PersistedC7Receipt\`: \`calculated_metric_id\`, snapshot/metadata/source policy/config/freeze/review/activation IDs, C7 и evidence SHA, \`inserted\`. \`score_status="complete"\`, \`status="persisted_complete_not_published"\`: **факт DB commit**, не факт доставки/приёма Unified Analytics. Публикация outbox и ACK QDP принадлежат следующим этапам; никаких provider calls, автоматического worker или LLM.

## Тесты, known blockers

\`tests/test_group_c_c7_persistence.py\` включает чистые Stage07 gold-parity вектора для **всех** сохраняемых полей с math-эквивалентом, обязательные поля SQL и C1–C7, no-invented alpha/k/counters, versioned deterministic SHA и immutable receipt. Disposable PG17 сценарии включают approvals/activation, полноценную atomic запись C7 + audit, строгую идемпотентность, no-IAM/no-approval/source revocation, очередное raw retraction, чужие IDs/source/metric, невозможность UPDATE complete/certificate, конфликт после изменения публикации и контроль sha256 migration 060.

CI \`test_group_c_*.py\` на Linux/Windows и отдельные fresh+upgrade PostgreSQL17 workflow steps с \`GC_TEST_DISPOSABLE_DB=YES\`; **на текущем этапе не заявлено фактическое прохождение exact-head PG17/CI**. Реальная production IAM/grants и QDP/Analytics delivery остаются BLOCKED_EXTERNAL. Тестовые verifiers нельзя использовать в production.

**Следующий этап GC-MOD-21 — Durable Worker:** claim, SKIP LOCKED, fencing, heartbeat, cancellation, retries SERIALIZABLE/recovery и ограничения конкурентности. Никогда не повторно использовать старый result как актуальное разрешение.
