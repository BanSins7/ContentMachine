# UA-MOD-26 — Local project-scoped Unified Analytics HTTP API (v1)

**Branch:** `feature/unified-analytics-full-module` · **Main files:** `intelligence/unified_analytics/http_service.py`, `http_api.py`, two lazily loaded guarded hooks in existing `app.py`. **No migration, new public grant, network listener, model/paid API call, or relaxation of UA13/UA84/UA85.**

**Status:** IMPLEMENTED CORE / static inspection only. Python/HTTP integration and isolated PostgreSQL17 QA NOT RUN; effective live SRE/QDP/Group A/B/C qualification still BLOCKED. PR38 remains Draft.

## Authentication, deployment and risk controls

Legacy Content Machine dashboard listens on `127.0.0.1:8765`. Its `Handler.authorized(mutation=...)` rejects unexpected `Host`, cross-origin `Origin`, and mutations without `X-ContentMachine: 1`. This gate is reused **before** the UAE route import/dispatch. UAE adds a mandatory **independent per-project token** for *all* operations (GET as well as POST). A project ID, source ID or localhost access is never treated as account authorization.

The process operator must create cryptographically random **32-byte** project token, provision only its SHA-256 hexadecimal digest as the map value in environment `CM_UAE_HTTP_TOKENS_SHA256`, and keep the raw token privately on the authorized local client. Example setup (placeholder strings, **not working secrets**):

```sh
# SHA256 hex is generated from the 32-byte hex token by an offline operator.
# Each key is a real 32-lowerhex project ID, each value a real SHA256 hex.
export CM_UAE_HTTP_TOKENS_SHA256='{"<real_project_id>":"<sha256_of_private_32_byte_token>"}'
# The real client adds: X-UAE-Project-Token: <private_32_byte_token_as_64_lowercase_hex>
```

The environment map is loaded at request time for rotation. It must be valid JSON mapping a maximum of 100 project IDs to 64-hex digests. Bad, empty, absent, weak or unknown configuration => **403 `project_forbidden`** before opening a DB connection. Check uses `hmac.compare_digest` against SHA-256 of decoded 32 token bytes. These tokens are **not emitted in JSON, stored in UAE tables, placed in URLs, written in docs, printed or logged**. For multiple local users/accounts, a real identity/ACL service must replace this operator-provisioned project-token gate before network/public release. Frontend Studio must not embed tokens into static JS.

All routes additionally check canonical `project_registry.id`, returning **404 `project_not_found`** when the supplied, token-authorized ID does not exist. Every SQL lookup is parameterized and pinned to the requested project ID. There are no cross-project joins or list-all-project endpoint. GET never changes state; the only writes are to the existing project-aware `QualificationJobQueue` through its admission gate or its current-state cancellation method.

### Exact routes (prefix `/api/projects/{32-lowerhex-project-id}/unified-analytics`)

| Method | Route | Response and authorization |
|---|---|---|
| GET | `/preview?request_id={uuid}` | Reads **existing registered UAE request only**. Returns basic request state plus `qualification='not_verified'`; does **not** run a QDP/SRE/A/B/C qualification or create a snapshot. Unknown/foreign request: 404. |
| POST | `/qualify` | Body has only `request_id`, `request_sha256`, `idempotency_key`, `batch_key`. Calls existing `QualificationJobQueue.enqueue(QueueSpec)`: existing DB gate checks exact registered request, active source/policy and QDP handoff. Returns `job_id`, `created` or `replayed`, **`qualified:false`**, not an accepted SRE/Group C approval. |
| POST | `/qualify-batch` | `{"items":[<QueueSpec body>,...]}`, **1..100** distinct request/batch pairs. `enqueue_batch` transaction handles batch atomically. No invented qualified evidence. |
| GET | `/jobs/{uuid}` | One existing project-owned job's safe status/attempts/associated snapshot ID and timestamps. Omits lease token/worker internal details. Cross-project/unknown job: 404. |
| POST | `/jobs/{uuid}/cancel` | Body exactly `{}`. Calls existing project-scoped `QualificationJobQueue.cancel`; 200 on cancellation or idempotent already-cancelled replay; 409 if terminal job or concurrent state change. |
| GET | `/snapshots?before={positive_bigint}&limit={1..50}` | Descending stable keyset pagination over **project-owned `partial`/`blocked`** snapshots only. Output includes opaque IDs/version/status/time and `next_before`. Historical stored `qualified` is deliberately excluded unless UA19 live permission can be reconstructed and independently verified. No blockers/raw payload. |
| GET | `/snapshots/{positive-bigint}` | Minimal project-owned **`partial`/`blocked`** snapshot metadata only; qualified row omitted (404), no raw evidence, media, metric counters or C score. |
| GET | `/aggregates` | **409 `aggregate_requires_live_qualified_pins`**, after project-token and project-existence checks. There is currently no trusted HTTP project-specific `VisibilityPins` resolver that can reconstruct exact UA19 authorization. Do **not** query raw `calculated_metrics` or trust SQL historical `status='qualified'` to fill this endpoint. Future release requires independently verified source-backed pin resolver, project ACL, min-N and anti-differencing review. |

Content is JSON UTF-8; POST requires `Content-Type: application/json` or `application/json; charset=utf-8`, size 2..14,000 bytes, a valid JSON object, no duplicate object keys, and no `NaN`/`Infinity`. Only exact known route query/body keys accepted; unexpected query keys, repeated query parameters, invalid UUIDs, negative/zero/out-of-bound integers, malformed JSON and invalid methods do not reach queue admission. POST must include the pre-existing Host/Origin/CSRF checks and additional scoped token.

### Response contract

Successful calls have `{"ok":true,"schema_version":"uae-local-http/v1",...}`. Stable negative JSON shape is `{"ok":false,"code":"...","error":"UAE request rejected"}` with HTTP 400/403/404/409/413/415/503, without internal SQL/provider details. Cross-project token mismatch returns 403 before DB I/O; nonexistent resource under an authorized project gives 404. Internal DB/peer failures are 503, not success or partial evidence.

This is **intentionally not** a user-facing signed PIE research export. UAE24 individual ranked values, UA25 signed datasets, SRE/Group A/B/C internal raw features, and UA20/21/22/23 statistical queries remain private and are **not** reachable through these routes.

## QA matrix and unresolved live integration

- `tests/test_unified_analytics_http_api.py`: **42 authored** unit/mock tests for per-project auth 403, cross-project IDs/jobs 404, malformed body/query, legacy CSRF and Host denial, authorized QDP queue injection, replay/batch/cancel, keyset pagination excluding qualified historical rows, zero-writes on GET and 409 aggregate blocker.
- `tests/test_unified_analytics_http_wiring_static.py`: **12 authored** source guard tests for lazy two-hook `app.py` registration, Host/Origin/CSRF order, project-token hashing, parameterized scoped SQL, and no raw/PIE/C public exposure.
- `tests/test_unified_analytics_http_api_pg17.py`: **3 conditional** disposable PG17 tests for zero DB calls on unauthorized token, real persisted UAE15 partial diagnostics with no positive qualified aggregate, and missing/cross-project jobs 404.
- Source re-fetch/static audit may verify code shape but **cannot substitute for Python execution, real HTTP loopback tests or PG17**. Developer's own machine should run:

```bash
python -m compileall -q intelligence/unified_analytics app.py
python -m unittest discover -s tests -p "test_unified_analytics_http*.py" -v
# broader module regression:
python -m unittest discover -s tests -p "test_unified_analytics_*.py" -v
# optionally, disposable PostgreSQL17 only: export CM_TEST_PG_DSN
```

A genuine browser/HTTP socket smoke is required before release: compare GET/POST using same-origin vs wrong Host/Origin, missing/incorrect `X-ContentMachine`, incorrect project token, cross-project request/UUIDs, non-mutating GET, correct Content-Type, unique query fields and paging, plus exact QDP queue admissions. **Do not claim pass until executed.**

### Blockers / integration notes for UA-MOD-27

1. **UA13/UA84/UA85** forbid effective trusted production `qualified` snapshots; no qualified aggregate/public rank until true upstream authority and UA19 current pins.
2. The current local application has no per-user project ACL; a manually provisioned per-project token is an explicit strong local extra gate, **not** proof of multi-tenant web readiness. No remotely accessible listener should be configured by this stage.
3. UAE25→PIE trust bridge is still missing independently provisioned verifier/current rights/per-row provenance. RTB remains unbound; no bypass.
4. No provider calls, AI, paid billing, signed PIE datasets, raw media, score mutation, dynamic table name or programmatic job-worker launch are triggered by HTTP.
5. Existing main, other feature branches, legacy PR23 and UAE Draft PR38 untouched except the minimal UAE-only app route hooks.

**Next:** UA-MOD-27 — Unified Analytics Studio: explicit partial/blocked/unknown UI, source/provenance trace, no misleading `0–100 Breakout Score` indicator or unqualified promotion, and browser smoke tests after real API QA.
