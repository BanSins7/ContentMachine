# UA-MOD-27 — Unified Analytics Studio (project-local, fail-closed)

**Branch:** `feature/unified-analytics-full-module`  
**Project UI:** `site/unified-analytics-studio.js`, `site/unified-analytics-studio.css`  
**Minimal host integration:** `site/index.html`, `site/projects.js`, the exact named-asset map in root `app.py`.  
**Status:** IMPLEMENTED CORE; real DOM/Chromium screenshot/browser request testing and PostgreSQL17 E2E **NOT RUN** in this session.

## What the Studio actually does

The Studio mounts inside **existing project details** beneath Project Context. It does **not** create another app, new React bundle, auth subsystem, database migration, worker, paid job scheduler or SQL data endpoint. The CSS is scoped under `.uae-studio`, responsive at 850/500 px, with keyboard focus outlines, live status announcements, labelled fields, simple semantic lists and reduced-motion support. Existing site routes and pages remain unchanged except two explicit static assets and one project-detail mount hook.

The Studio uses exclusively UA-MOD-26's existing versioned **local project-scoped HTTP** surface:
`/api/projects/{pid}/unified-analytics/... `. An operator must **first** configure `CM_UAE_HTTP_TOKENS_SHA256` on the local process and supply the 64-lowercase-hex project token to the Studio. Token is a **password input only**, never stored in `localStorage`, `sessionStorage`, URL/query parameters, cookies, logs, or static JS. Every UAE fetch includes `X-UAE-Project-Token` and **`X-UAE-Api-Version: uae-local-http/v1`**; only explicit user actions cause network requests. Mutating requests additionally include `X-ContentMachine: 1`, and the server still validates Host/Origin/mutation token and project authorization. The browser credentials mode is same-origin. The token is cleared via the UI and when the API returns project-forbidden. Changing a token invalidates the entire visible project-specific readside (timeline, inspected job, request and snapshot details, cancellation control) immediately; in-flight responses are discarded when the token changes. A project-forbidden response also clears the entire readside.

### Panels and supported actions

- **Connection + status:** clear not-configured/invalid/expired token/DB/unknown version states, with a safe, stable error mapping and no internal server error details.
- **Snapshot timeline:** explicit user-initiated GET `snapshots?limit=10` with `next_before` keyset pagination. List entries show persisted snapshot ID, video ID, snapshot version, creation time and *partial* or *blocked* status. **Historical `qualified` rows are not displayed** by UA26 and aren't included in page counts. The counts displayed are about the last fetched page only, NOT total rows in the project. Empty and backend-error states have separate language.
- **Snapshot inspector:** GET `snapshots/{id}` exposes only UA26's fixed safe fields: IDs, version, partial/blocked state, gate/input schema, creation date. It never queries raw blockers, detailed peer scores or media. Placeholder labels describe unavailable A/B/C current evidence, unavailable private blocker details, and unknown Breakout Score, not invented values.
- **Job inspection and cancellation:** manual UUID-based GET `jobs/{uuid}` shows queue status, attempts, timestamps, error code (server-sanitized), optional snapshot ID and that a `succeeded` queue state does **not** mean current `qualified`. Explicit confirmation before POST `jobs/{uuid}/cancel`; only pending/retry/leased jobs show the cancellation control.
- **Request preview and manual admission:** manual UUID GET `preview?request_id={uuid}` reads registered request without mutation. An explicit advanced disclosure allows manual enqueue of a previously registered request only after entering its *exact* request SHA-256, idempotency key and batch key, passing client-side structure checks and a confirmation dialog. The UA17 queue also rechecks source policy and QDP handoff and may return 409 `qualification_not_ready` / `idempotency_conflict`. This UI does not fabricate a request digest, authorize a peer, trigger model execution or run a worker. No automatic retries.
- **A/B/C evidence:** a structured seven-part section shows source/rights, QDP, SRE, A, B, C and aggregates. These are **explicitly unavailable**, not fake green badges: UA26 intentionally does not expose the independently checked joined evidence or raw A/B/C features. Future real API integration can replace the placeholders only after an independently verified UAE19 permission/pins resolver and source policy/privacy approval.
- **Breakout Score + components:** no numeric value, progress dial or **0–100** interpretation. The display says `Значения и компоненты не опубликованы`; missing metrics are **not zero**. No confidence intervals, p-values, significance or causality claims.
- **Aggregate dimensions:** all eight UA21 dimensions (content kind, hook mechanism, video format, story role, editing, subtitle, CTA, topic) are listed **unavailable**. Explicit availability probe calls GET `aggregates`, interprets the current 409 `aggregate_requires_live_qualified_pins` as expected withheld access, never a successful/empty dataset. Min sample N≥5 is an underlying privacy gate, not population representativeness.

**Important:** the visual A/B/C evidence, component and dimension sections are **honest placeholders**, not complete live analytics. Completing them without source-backed current authorization requires further work on the trusted backend; do not misrepresent the stage as full production analytics.

## UI safety / lifecycle

All dynamic API strings enter the page via `textContent`, never `innerHTML`/HTML interpolation. No untrusted script/media URLs, no `eval`, no token in query parameters. All dynamic list rendering verifies partial/blocked status and safe positive IDs before painting. The asynchronous UI uses a single busy gate, project-DOM `isConnected`, serial epoch checks for pagination/refresh, and token-revocation checks after `await fetch` to avoid repainting with a stale project authorization. Buttons are not silently executed on mount; every queue mutation is explicitly confirmed.

This is a private local Studio, NOT multi-user authentication. A genuine account/role ACL, rate limits, audit logging, anti-differencing/privacy review, user identity token binding, secure key delivery, and independently authenticated producer ports are necessary before enabling remote hosting/public analytics.

## Test inventory (authored, not executed)

- `tests/test_unified_analytics_studio_static.py` — **22 authored** static Python checks for exact host CSS/script registration, project mount, token absence of storage/logging, explicit version/auth/CSRF, XSS-safe text, expected withheld score/A/B/C status, pagination/race guards, focus/responsive design.
- `tests/node/test_unified_analytics_studio.cjs` — **14 authored**, dependency-free Node 18+/22 `node:test` DOM smoke scenarios (mount doesn't fetch, bad token with zero calls, successful versioned GET, snapshot inspector ID, gated aggregate 409, revoke old token, queue job/explicit cancellation/CSRF, manual enqueue, no HTML injection or browser storage).
- `tests/node/test_unified_analytics_studio_browser.cjs` — **2 authored** optional real Playwright Chromium smoke tests: fake same-origin loopback fetch, snapshots/blocked and 409 availability with optional screenshot, plus 390px mobile layout. If Playwright is not installed, tests are **SKIPPED**, NOT passed. Set `CM_UAE_SCREENSHOT_DIR` to a writable local directory to capture `ua27-studio-partial-blocked.png` in the optional Chromium test. No real API/backend socket required for this mocked visual smoke.
- Existing UA26 unit/mock, Host/Origin/CSRF, isolated PostgreSQL 17 and full module tests are **required regressions**. Studio does not require a new database migration or its own PG fixture. Real DB + actual localhost browser HTTP integration must separately check 403/404/409 and the API's project token provisioning, since fake browser responses cannot independently prove server authorization.

On the developer machine with repository checkout:

```bash
python -m compileall -q app.py intelligence/unified_analytics
python -m unittest discover -s tests -p "test_unified_analytics_studio*.py" -v
node --test tests/node/test_unified_analytics_studio.cjs
# optional: install Playwright Chromium using the approved local dev setup
CM_UAE_SCREENSHOT_DIR=/tmp/uae-visuals node --test tests/node/test_unified_analytics_studio_browser.cjs
python -m unittest discover -s tests -p "test_unified_analytics_http*.py" -v
# optional isolated PG17: CM_TEST_PG_DSN
```

**QA in this environment:** GitHub source re-read/static invariant inspection plus **7/7 directly executed isolated V8 Studio smoke scenarios** (mocked DOM+API: absent token/zero fetch, exact snapshot inspection, withheld aggregate 409, explicit queue job/cancel/CSRF, manual admission, token-change revocation and disconnect) PASS. These are neither the authored Node test suite nor a real browser.  The repo is not mounted/checked out and github.com DNS is unavailable from the local runtime. No executed Node, browser, screenshot, Python or PG17 results may be claimed. In particular, the optional browser screenshot is **not** present until the test actually runs.

## Integration blockers and next steps

UA13/UA84/UA85 continue to deny effective positive `qualified` peer snapshots; UA26 aggregates are intentionally 409. An independent trusted **server-side UAE19 current-pin resolver** plus role-checked projection of A/B/C features, C component evidence, blocker **safe codes only**, audited query access, group thresholding and anti-differencing protection would be needed to activate real analytics panels. PIE signed handoff and RTB producer binding also remain separate integration gates. **Do not edit other branches or main as part of this UI stage.**

**Next:** UA-MOD-28 — Observability and operational CLI. Include bounded, privacy-preserving counters and trace IDs; no payloads/tokens/media in logs and no automatic global data scans.
