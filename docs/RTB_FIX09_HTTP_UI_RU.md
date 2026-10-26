# RTB FIX-09 — отдельный B9 Deep V2 HTTP/UX без вмешательства в Flat/Deep V1

**Статус 2026-10-26:** 09-A (localhost HTTP) и 09-B (отдельный базовый полный UI-маршрут) **РЕАЛИЗОВАНЫ ПО КОДУ**; весь FIX-09 **НЕ ЗАВЕРШЁН** до полноценного UI hardening / PG/Node QA. **CI: QUEUED/NOT EXECUTED**. Ветка `feature/rtb-module-v2`, [Draft PR #32](https://github.com/BanSins7/ContentMachine/pull/32), `main` не изменён.

## Важная граница авторизации

Текущий `app.py` — однопользовательский локальный Dashboard на `127.0.0.1:8765`, **без полноценной пользовательской сессии**. Недопустимо принимать из браузера произвольный `reviewer_id` как удостоверенную личность. Поэтому новые Deep V2 маршруты:

- Закрыты **по умолчанию**, пока серверный оператор не настроит `CM_RTB_REVIEWER_ID` из уже существующей таблицы `knowledge_fact_review_authorizations`. Например, при запуске локального сервера из PowerShell: `$env:CM_RTB_REVIEWER_ID = "existing-b9-authorized-reviewer"`; это **пример имени**, реальную существующую авторизацию надо обеспечить в B9 для каждого проекта.
- Каждый вызов RTB repository независимо исполняет `_auth(conn, project_id, server_actor)`, поэтому поддельный project ID и reviewer другого проекта отклоняются даже при локальном доступе.
- Используют существующий `Handler.authorized` (только localhost host/origin + `X-ContentMachine: 1`) и **дополнительную строгую проверку Origin для каждого POST**. Браузер не передаёт `reviewer_id`, `actor`, `api_key`, `sender` или контроль provider.
- Платные `advance-pass1`, `advance-pass2` и `advance-expansion` требуют строго `{"authorize_paid_call": true}`, которое UI посылает только после отдельного `window.confirm`. Базовая `authorize_usd` привязана к exact `quote_sha256`; расширения — отдельная quote + consent. GET никогда не вызывает LLM.
- **Это безопасная локальная модель одного оператора, а НЕ поддержка удалённого/multi-user входа.** Не открывать приложение публично через прокси, пока не появится полноценная серверная session identity + CSRF. Ни один paid/provider API не включает автоматический retry.

## Реализованные источники

- `intelligence/taxonomy_builder/deep_v2_http.py` — только RTB-owned маршруты `/api/projects/{project_id}/taxonomy-builder/deep-v2/**`:
  - POST `bindings/pin`, `quotes`, `consents`, `consents/revoke`, `runs/start`;
  - GET `bindings/{binding_id}`, `runs/{run_id}/status`, `pass2-status`, `review1`, `review2-preview`, `review2`, `publication`, `exports/{publication_id}?version=rtb-publication-export/v1|v2`;
  - POST `runs/{run_id}/advance-pass1`, `review1`, `advance-pass2`, `review2`, `publish`, `expansion-quote`, `expansion-consent`, `expansion-revoke`, `advance-expansion`.
- `app.py` — **только два узких GET/POST hook** и регистрация одного статического `/taxonomy-deep-v2.js`. Старые `/taxonomy-builder/runs/.../deep/` GET/POST обработчики сохранены. Обработчик B9 отказа не выдаёт сырые детали авторизации; ошибки маршрута 422/403/404/503.
- `site/index.html`: отдельная панель Deep V2 на **странице проекта**, а не в legacy Pipeline Studio, поэтому не требуется подтверждение старой анкеты. `site/projects.js`: одна простая точка расширения `cm-taxonomy-deep-v2-project-open` при открытии проекта. Состояния проектов не меняются.
- `site/taxonomy-deep-v2.js` (отдельный контроллер): pin B9 snapshot → zero-cost quote → явный total USD consent → unpaid idempotent run start → только отдельный paid Pass1 → Human Review1 → отдельно paid Pass2 → при недостатках отдельная expansion quote, дополнительное USD consent и один paid expansion (второй только после следующей quote/consent) → Human Review2 → explicit publish → read-only V1/V2 export. Source data и все 14 полей показываются через `textContent`, никогда `innerHTML`. Review решения `approve/reject/edit/merge`; `edit` управляет 13 отдельными полями + immutable ID, ручное `add` пока вводится через полный 14-field JSON — каждый вариант повторно валидирует сервер.
- `site/taxonomy-workspace.css` — стили только внутри `.tb-v2-*`. Остальные интерфейсы не переписывались.

## Сценарии безопасности и регрессии

- `tests/test_taxonomy_deep_v2_http.py`: **14 офлайн** кейсов HTTP. Missing/malformed server actor, forged reviewer/API key/provider, strict POST same-origin, explicit paid Boolean, GET no provider call, full-route namespace separation от V1, export version negotiation, no-cost separate expansion consent, malicious payload and size limits.
- `tests/test_taxonomy_deep_v2_ui.cjs`: браузерный контроллер в `vm` с моками, маршрут со страницы проекта, проверка отсутствия оплаты при open/quote/consent/start/GET, paid Pass1 только после confirm, Human Review1 не оплачивается, paid Pass2 отдельно, Human Review2, explicit publish и V2 export. Проверка того, что браузер не посылает `reviewer_id`, а source HTML обрабатывается как текст.
- `.github/workflows/rtb-module-contracts.yml`: добавлены Python-контракты Linux/Windows, Node 22 UI mock, RTB пути в trigger; PG17 тесты из прошлых этапов остаются в workflow. **Все CI пока QUEUED; успешного прогона нет**.

## Незакрытые работы FIX-09-C/D

- [x] ~~UI: 14-field Human Review редактор — неизменяемый ID и 13 отдельных полей, строгие JSON массивы ссылок B9/A8, локальная защита self-parent/merge target. Встроенный UI mock проверяет фактическое изменение имени и отказ при неправильном массиве B9 facts до POST.~~ **КОД ГОТОВ, изолированный V8 mock PASS, Node CI ещё QUEUED**.
- [x] ~~UI/Read API: exact max-two Expansion — две разные quote UUID, SHA и consent UUID; persisted `expansion_count=0→1→2` и UI block даже при stale `expand_pending` после второй попытки.~~ **КОД ГОТОВ, изолированный V8 mock PASS, PG17 CI ещё QUEUED**.
- [ ] UI: более удобное reparent/merge дерево, preview SHA/fact/observation в редактировании, восстановление/обработка сетевых ошибок и `uncertain`/revocation/cancel, настоящие Node/PG HTTP E2E.
- [ ] HTTP: реальная PostgreSQL17 mock E2E через `app.py`, авторизация B9 на исходных фактах, контроль окружения/запуска, идемпотентность ответов, UX reconciliation состояния `uncertain`.
- [ ] Для **удалённой/многопользовательской** работы нужна настоящая session authentication: локальная `CM_RTB_REVIEWER_ID` не является общей архитектурой user-login.
- [ ] FIX-10/11: T01–T36, реальные PG17 миграции 054–059 и полный анализ конфликтов/ветки/регрессии.

**Никакого live Opus usage и merge в main при этой работе.** Backend/UI code отмечены готовыми по реализации, но до реальных тестов нельзя считать релиз готовым.

## Продолжение FIX-09-C (2026-10-26)

- **Исправлена недостаточность первоначального теста:** ранее оба Expansion в браузерном моке возвращали одинаковые quote/consent ID. Теперь реальные условия эмулируются разными `expansionQuoteIDs[0/1]`, `expansionConsentIDs[0/1]` и `expansionHashes[0/1]`. Мок отклоняет использование старого согласия второй попыткой, тест требует только 2 paid advance. Отдельно проверена запись исходных Human Review1 edits всех 14 полей.
- **Новые поля form:** `V2_EDIT_FIELDS` 13 полей, `id` неизменен; B9 `source_fact_ids`, A8 `source_observation_ids`, `examples/search_signals/validation_focus` валидируются как JSON массивы строк, предупреждения до отправки при self-parent / пустой merge target. `site/index.html` обновлён. Авторитет остаётся у B9/H1 backend, UI не может утвердить новый product fact.
- **Fail closed при stale state:** `RTBDeepV2Pass1Repository.inspect_pass1` теперь отдаёт persisted `expansion_count`. В UI `expand_pending` без корректного integer счётчика либо с `count≥2` не предоставляет ни quote, ни consent, ни paid button. Предупреждение закреплено в строке состояния и не исчезает при successful GET. PG17 тест `test_two_paid_expansions_and_never_a_third` расширен read-status assertions `0→1→2` и проверкой уникальных SHA/consent.
- **Фактически выполнен только изолированный JavaScript V8 mock сценарий**, возвращено `RTB Deep V2 B9 UI mock: H1/H2 + 2 separate expansions OK`; реальная командная версия `node tests/test_taxonomy_deep_v2_ui.cjs` на GitHub runner и PostgreSQL17 всё ещё не подтверждены. Поэтому QA/релиз остаются незавершёнными.
