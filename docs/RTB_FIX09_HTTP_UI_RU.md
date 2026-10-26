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
- `site/taxonomy-deep-v2.js` (отдельный контроллер): pin B9 snapshot → zero-cost quote → явный total USD consent → unpaid idempotent run start → только отдельный paid Pass1 → Human Review1 → отдельно paid Pass2 → при недостатках отдельная expansion quote, дополнительное USD consent и один paid expansion (второй только после следующей quote/consent) → Human Review2 → explicit publish → read-only V1/V2 export. Source data и все 14 полей показываются через `textContent`, никогда `innerHTML`. Review решения `approve/reject/edit/merge`, ручное `add` через полный 14-field JSON — каждый вариант полностью валидирует сервер.
- `site/taxonomy-workspace.css` — стили только внутри `.tb-v2-*`. Остальные интерфейсы не переписывались.

## Сценарии безопасности и регрессии

- `tests/test_taxonomy_deep_v2_http.py`: **14 офлайн** кейсов HTTP. Missing/malformed server actor, forged reviewer/API key/provider, strict POST same-origin, explicit paid Boolean, GET no provider call, full-route namespace separation от V1, export version negotiation, no-cost separate expansion consent, malicious payload and size limits.
- `tests/test_taxonomy_deep_v2_ui.cjs`: браузерный контроллер в `vm` с моками, маршрут со страницы проекта, проверка отсутствия оплаты при open/quote/consent/start/GET, paid Pass1 только после confirm, Human Review1 не оплачивается, paid Pass2 отдельно, Human Review2, explicit publish и V2 export. Проверка того, что браузер не посылает `reviewer_id`, а source HTML обрабатывается как текст.
- `.github/workflows/rtb-module-contracts.yml`: добавлены Python-контракты Linux/Windows, Node 22 UI mock, RTB пути в trigger; PG17 тесты из прошлых этапов остаются в workflow. **Все CI пока QUEUED; успешного прогона нет**.

## Незакрытые работы FIX-09-C/D

- [ ] UI: полноценный редактор узлов с отдельными полями для всех 14 свойств вместо ручного JSON, проверяемое reparent/merge UX, предпросмотр SHA/fact/observation при обзоре, UI тесты ошибок quality/кнопок/повторной загрузки/двух expansion/cancel.
- [ ] HTTP: реальная PostgreSQL17 mock E2E через `app.py`, авторизация B9 на исходных фактах, контроль окружения/запуска, идемпотентность ответов, UX reconciliation состояния `uncertain`.
- [ ] Для **удалённой/многопользовательской** работы нужна настоящая session authentication: локальная `CM_RTB_REVIEWER_ID` не является общей архитектурой user-login.
- [ ] FIX-10/11: T01–T36, реальные PG17 миграции 054–059 и полный анализ конфликтов/ветки/регрессии.

**Никакого live Opus usage и merge в main при этой работе.** Backend/UI code отмечены готовыми по реализации, но до реальных тестов нельзя считать релиз готовым.
