# RTB-FIX-10 / FIX-11 — автономная передача на локальную проверку

**Дата:** 2026-10-27. **Ветка:** `feature/rtb-module-v2`. **PR:** #32 (Draft).
**Решение владельца:** не ждать GitHub Actions / online QA; реализацию FIX-10 и FIX-11 закрыть по исходному коду, а реальные тесты и объединение выполнять позднее на пользовательском ПК.

## Что завершено по реализации

- **FIX-10 CODE DONE:** реестр T01–T36 и AST-проверка существования тестовых методов; негативные и позитивные кейсы; legacy Flat/Deep V1 regression suites; защищённые no-live наборы offline/Node/PG17; аудит миграций 054–059 (SHA, таблицы, trigger→table); индивидуальные PG17 группы CI уже описаны, но CI запускать/ожидать не требуется.
- **FIX-11 CODE DONE:** границы собственных файлов RTB, сохранение legacy hooks, документированные V1/V2 export contracts, проверка exact-head CI evidence (остаётся опциональным артефактом, не считается критерием закрытия кода); локальный `local_release_handoff.py` для автономного исходного handoff, SHA-256 manifest, сводного статуса и маленьких offline tests.
- **Не подменять понятия:** "реализация закрыта" != "QA пройдена" != "разрешён merge". Точные результаты PostgreSQL17, браузерного E2E, budget/reconciliation и поведения потребителя Search Planner V2 не получены. Тесты не могут стать `PASS` только от появления файлов.

## Запуск на машине владельца (WSL/Linux/macOS из корня проекта)

```bash
git checkout feature/rtb-module-v2
git fetch origin main
git status --short
python -m intelligence.taxonomy_builder.local_release_handoff \
  --json-out reports/rtb_fix10_fix11_local_handoff.json
```

Этот default-режим **не вызывает GitHub Actions, OpenRouter, DB и веб API**. Он проверяет реальный Git diff против `origin/main`, legacy-preserving shared-file hooks, T01–T36 source registry и source SQL, затем пишет список SHA-256 файлов. При проблемах возвращает ненулевой exit status. Незакоммиченные изменения исходников и посторонние untracked файлы блокируют успешный handoff (сгенерированные файлы `reports/` разрешены). Поле `code_handoff_valid=true` означает только успешные *проверки исходников* для передачи на ПК.

Опционально запустить самые простые тесты без PostgreSQL и внешних API:

```bash
python -m intelligence.taxonomy_builder.local_release_handoff --run-basic \
  --json-out reports/rtb_fix10_fix11_local_handoff.json
```

Исполняемые offline тесты: T01–T36 registry, standalone boundary, CI-evidence fail-closed logic, SQL source guards, сам local handoff. Тесты не запускаются автоматически, пока не указан `--run-basic`. Отдельный `--without-git` допускает только проверку source registry/миграций, но не утверждает сохранение legacy и не формирует положительный `code_handoff_valid`.

## Следующий этап на ПК: ручная полная проверка (сейчас сознательно НЕ запущена)

1. На временной **одноразовой PostgreSQL 17** выставить `CM_TEST_PG_DSN` и `CM_DB_DSN` (только на тестовую БД), установить pinned requirements, выполнить `python -m intelligence.migrate`, затем `python -m intelligence.taxonomy_builder.migration_audit --postgres`. Проверить SHA 054–059, **13** RTB таблиц и **27** защитных trigger→table bindings. Не использовать production DB.
2. `python -m intelligence.taxonomy_builder.qa_matrix --run-offline --json-out reports/rtb_local_offline.json` — только offline Python/Node mock suites. Затем **только в тестовой PG17** `python -m intelligence.taxonomy_builder.qa_matrix --run-postgres --json-out reports/rtb_local_postgres.json`. Последняя команда также запускает offline suite, но не отправляет real provider calls. Проверить точные exit codes.
3. Проверить clean schema bootstrap и обновление копии исторических Flat/Deep данных, source SHA/migrations, H1/H2, две отдельно разрешённые expansions и блокировку третьей, retry/unknown-spend, atomic SRE/V1 projection, экспорт 14/11 полей. Все paid-provider вызовы — только моки.
4. Локально вручную пройти UX с несколькими проектами и авторизованным single-operator reviewer, включая refresh, ошибки и отзыв согласия. Настройка `CM_RTB_REVIEWER_ID` — **не** публичная multi-user authentication; не выставлять localhost сервис наружу.
5. **T28/T29 остаются внешним договором с отдельной веткой Search Planner:** RTB предоставляет verifiable V2 export, но реальная генерация query на основе search_signals — ответственность SP. Не менять `intelligence/search_planner/**` в RTB.
6. Повторить `git fetch origin main` и local handoff перед последующим merge; зафиксировать SHA и сравнение ветки. При любом отсутствующем PASS, изменении `main`, security blocker или реальном budget ambiguity не называть RTB production-ready.

## Семантика отчёта

`reports/rtb_fix10_fix11_local_handoff.json`:

- `source_hashes_sha256`: точные отпечатки версий исходников для повторяемости, **не подпись релизного QA**;
- `source_isolation`: проверка Git diff против доступного `origin/main`, `head_sha` и `base_sha`;
- `t01_t36`: **34 mapped**, **T28/T29 external** (не 34 runtime PASS);
- `migration_source_valid`: статическая корректность RTB SQL, а не применённая PostgreSQL-схема;
- `github_ci=NOT_REQUIRED_FOR_CODE_HANDOFF`: CI не является условием закрытия **кода** по решению пользователя;
- `local_pg17_qa`, `local_browser_qa`: `DEFERRED_TO_OWNER_PC`;
- `merge_authorized=false` и `release_ready=false`: намеренно, независимо от успешного source check.

**Состояние после FIX-10/11:** код передан для автономной верификации на ПК; FIX-09-C4 (особенно полноценное multi-user auth/unknown-spend reconciliation для публичной эксплуатации) и downstream SP T28/T29 не выдаются за готовые. `main` и чужие модули не изменяются. PR #32 сохраняется Draft до отдельного решения о merge.
