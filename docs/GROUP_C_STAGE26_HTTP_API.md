# GC-MOD-26 — Group C HTTP API (standalone WSGI boundary)

**Branch:** \`feature/group-c-full-module\`. **Status:** CODE IMPLEMENTED for standalone, injected-IAM HTTP interface and route-level Group C domain delegation. **Actual HTTP/PG17/CI execution NOT VERIFIED**. Deployment mounting / real IAM & CSRF key management, PG roles and production end-to-end must be wired outside this standalone feature branch; do not call the API live or production-ready. No app.py/main/peer edits.

## Scope and architectural rule

\`intelligence/group_c/http_api.py\` implements a callable **\`GroupCHTTPAPI\` WSGI endpoint**, not a new HTTP server. It performs no provider/LLM/HTTP requests, starts no background work, and never computes C7. Dependencies MUST be explicitly injected:
- \`connect\`: dedicated PostgreSQL \`autocommit=True\` connection factory (one per request); Group C services retain their own SERIALIZABLE/REPEATABLE READ transactions.
- \`access_verifier\`: **actual trusted external project-scoped IAM**, exposes \`authorize(bearer_token,project_id,action,method,path)->HTTPGrant|None\` and \`verify_csrf(bearer_token,project_id,action,csrf_token,origin)->literal True\`. This adapter must validate expiry/signature/revocation and project role itself, and not copy untrusted user-supplied X-User headers. Invalid/mismatching grant fails closed BEFORE opening DB.
- \`allowed_hosts\`: exact lowercase Host allowlist; \`allowed_origins\`: exact \`https://<allowed_host>\` origins (no wildcard). HTTPS by default and untrusted \`X-Forwarded-Host/Proto\` rejected. WSGI scheme must be set by a trusted TLS terminator; the client cannot select it.
- \`human_reviewer_verifier\` and \`config_activation_verifier\`: separately injected live signed human-review and global configuration activation verifiers. **Bearer access is not equivalent to human signature**. Without these, corresponding mutation endpoints return typed 503. Test-only synthetic verifiers must NEVER be mounted in production.

**Security model:** every route requires \`Authorization: Bearer <verified opaque token>\`; IAM checks exact canonical project, method, path and action \`group_c:read\`, \`group_c:review_create\`, \`group_c:review_decide\`, \`group_c:config_activate\` or \`group_c:run_cancel\`. Every POST additionally requires exact Origin equal to verified HTTPS Host, a current action-bound \`X-CSRF-Token\` verified by IAM, empty Cookie header and \`application/json\` (strict fields, unique keys, max 32 KiB). Extra/unknown query keys, method, path, identifiers, noncanonical UUID, bool-as-int, invalid pagination and invalid JSON denied with stable codes; never reflect SQL/internal IAM exception messages. Responses set \`Cache-Control: no-store\`, \`nosniff\`, \`DENY\` frame, \`default-src 'none'\`, \`no-referrer\` and stable schema version \`group-c-http/v1\`. Decimal values are **strings**, not JSON floats. Backend errors are redacted as \`internal_error\`.

## Routes

Prefix \`/api/group-c/v1/projects/{project_id}\` with canonical lowercase 32-hex project ID. \`GET\` list routes accept optional \`limit=1..50&offset=0..10000\` and include deterministic order, \`next_offset\`, project-scoped queries.

| Method | Route | Responsibility |
| --- | --- | --- |
| GET | \`/runs\`, \`/runs/{run_uuid}\` | Exact project-owned run state, source ID pointers, status/reason; no raw C7. |
| POST | \`/runs/{run_uuid}/cancel\` | Requires action-level IAM+CSRF. Calls GC21 \`PostgreSQLGroupCWorker.cancel\` fenced row lock; queued/running → cancelled, identical replay \`cancelled:false\`, completed/failed rejected. |
| GET | \`/reviews\`, \`/reviews/{review_uuid}\` | Project-owned signed review subject & decision status, no assertion/token material. |
| POST | \`/reviews\` | Creates a review only from original config+frozen creator/prior/(optional niche) IDs and **1..500** reviewer-specified minimum peers, using \`PostgreSQLHumanReview.create_request\` with live source/member rights, not automatic approval. |
| POST | \`/reviews/{review_uuid}/decisions\` | Project verifies review before passing explicit approved/rejected, meaningful note and signed assertion to \`PostgreSQLHumanReview.record_decision\` + separately trusted signed-human verifier. |
| GET | \`/configs\`, \`/configs/{id}\` | Project-visible signed \`group_c_config_activation_events\`, canonical config identity/version/current global activity, not unapproved global config enumeration. |
| POST | \`/configs/{id}/activate\` | Project verifies exact review/config; calls \`PostgreSQLConfigActivation.activate\` with independent real global activation IAM and signed assertion (never auto-approve). |
| GET | \`/cohorts\`, \`/cohorts/{freeze_id}\` | Project-owned immutable freeze purpose/member count/source metric version/evidence SHA; no private peer payload. |
| GET | \`/scores\`, \`/scores/{calculated_metric_id}\` | Only project-owned GC20 certified C7. Every score and list item passes GC24 **live** source/published/raw/ALL frozen peer retention+rights; invalid C7 returns \`unavailable\` with \`breakout_score:null\` and no evidence SHA. Detail reads original four SQL Decimal C7 components via GC25 \`load_persisted\` only after passing the same live gate. |
| GET | \`/videos/{video_id}/preview\` | Read-only **existing run/score preview** scoped to \`sre_project_videos\`; no unreviewed C7 computation. If no run or rights unavailable, null breakout and explicit reason; if complete and eligible, certified SQL C7 only. |

No unreviewed score-writing endpoint, no automatic human decisions, no background claims or provider request in HTTP layer.

### API examples (syntax only; no live request sent)

\`\`\`http
GET /api/group-c/v1/projects/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/scores/123 HTTP/1.1
Host: cm.example.com
Authorization: Bearer <externally verified JWT-or-opaque-token>
\`\`\`

\`\`\`json
{
  "schema_version": "group-c-http/v1",
  "data": {
    "calculated_metric_id": 123,
    "state": "unavailable",
    "reason_code": "metric_snapshot_expired",
    "breakout_score": null,
    "evidence_sha256": null
  }
}
\`\`\`

\`\`\`http
POST /api/group-c/v1/projects/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa/runs/123e4567-e89b-12d3-a456-426614174000/cancel HTTP/1.1
Host: cm.example.com
Origin: https://cm.example.com
Authorization: Bearer <verified-token>
X-CSRF-Token: <trusted-action-bound-token>
Content-Type: application/json
Content-Length: 2

{}
\`\`\`

## QA and remaining integration boundary

\`tests/test_group_c_http_api.py\` adds independent pure WSGI attack-case tests (unknown Host/forwarded headers, HTTP downgrade, missing Bearer, cross-project grant, CSRF/origin/cookie, invalid query/page, duplicate/unknown JSON, response headers/Decimal/UUID) and **optional disposable PG17** integrated routes using real Group C fixture tables. The PG17 tests cover project-scoped run read/cancel/replay, historical score live eligibility/expiry, same SQL four score contributions, source-safe score listing and preview, scoped review/config/cohort queries, signed human review decision, signed global config activation, and no verifier fail-closed. Synthetic IAM doubles are TEST ONLY. CI fresh+upgrade PG17 and Linux/Windows pure tests are authored, but **exact-head execution is not verified**.

Integration required in its own deployment/hosting owner: mount \`GroupCHTTPAPI\` at a real WSGI server, provide TLS-trusted scheme, production project-scoped bearer IAM and binding-specific CSRF source, signed reviewer + activation verifier, database privileges restricting unguarded \`calculated_metrics\` reads, request rate limits/observability, real source rights, and cross-service E2E. No other module/branch changed. **Next GC-MOD-27 — Group C Studio.**
