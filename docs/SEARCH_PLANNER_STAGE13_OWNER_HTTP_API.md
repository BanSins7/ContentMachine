# Content Machine · SP-13: authenticated local-owner Search Planner HTTP API

**Branch:** \`feature/search-planner-full-integration\`  
**Date:** 2026-10-26  
**Code status:** implemented on this branch. **QA:** offline tests committed; exact-HEAD GitHub focused/Windows/Linux/real PostgreSQL17 runs still not confirmed.  
**Release:** Draft PR #34 only. Nothing merged into \`main\` or Collector.

## 1. Integration, trust boundaries and independent modules

\`app.py\` adds only guarded lazy \`handle_get\`/\`handle_post\` mounts to \`intelligence/search_planner/http_api.py\`. Existing \`Handler.authorized()\` Host/Origin/\`X-ContentMachine\` behavior remains unchanged for unrelated modules. The SP router repeats loopback-IP and same-origin authorization checks and requires an additional secret for **every SP POST**.

- **Read-only GET**: no owner token necessary for locally authorized requests. The project must exist in the local \`projects.get_project\` store and backend project-scoped SQL readers require the same project identity. No GET method runs an LLM, posts to Collector, writes consent or starts workers.
- **Any SP POST**: requires \`CM_SP_OWNER_TOKEN\` in the **local server process** (32–256 chars of securely generated random secret) and identical \`X-CM-SP-Owner-Token\` request header. Constant-time comparison, no token in response, DB, log or UI. Also require correct Host/Origin and existing \`X-ContentMachine: 1\`; peer IP must be loopback. Missing/mismatched secret → 403 before parsing the request or invoking an SP service. Never accept \`approved:true\`, browser-supplied callbacks, \`accepted\` query sets, source registry or provider keys from JSON as authorization.
- **OwnerActionGuard**: server-assembled closure bound to the authenticated project; only enumerated actions are permitted. It is injected into \`GenerationWorkflow\`, \`GenerationExecutionController\`, \`TaskPlanner\`, and optional Collector dispatcher. Inner financial and immutable PostgreSQL checks **still apply**.
- **Default Collector**: \`EmptyCollectorAdapter\`, no Collector import, no network, \`awaiting_collector\` for durable SP inbox. The HTTP API intentionally does not allow untrusted user JSON to install a Collector port.
- **Production QV**: explicit \`CM_SP_EMBEDDING_MODEL_PATH\` must point to preinstalled manifest-pinned multilingual SentenceTransformer files; no network download or test hashing fixture fallback. Missing model → 503, not artificial semantic acceptance.
- **Paid POST**: \`/generation/{id}/execute-one\` disabled by default. Operator must explicitly set \`CM_SP_PAID_HTTP_ENABLED=1\`, present valid owner secret, supply a unique idempotency key and have a durable generation, priced quote, spending consent and remaining SQL budget. HTTP permits **one** paid worker invocation at a time inside this ThreadingHTTPServer process; unrelated local requests are served by other threads. It invokes only one batch and never auto-retries an uncertain provider POST.
- **Remote deployment is unsupported**. HTTP loopback, Host/Origin and a process-local owner token are a deliberate **single-local-owner** security design, not multiuser OAuth/remote authentication. Do not bind the app to public interfaces or reuse this token across shared users.

## 2. Route contract

All URLs start with \`/api/projects/{project_id}/search-planner\` (project ID is 32 lowercase hex chars). UUID path variables are canonical lowercase UUIDs. Successful results follow:

\`\`\`json
{"ok":true,"schema_version":"search-planner-http-response/v1","result":{"schema_version":"..."}}
\`\`\`

Errors follow \`{"ok":false,"schema_version":"search-planner-http-response/v1","error":"..."}\`, with 400, 403, 404, 409, 411, 413, 414, 415, 422, 503 as appropriate. Unknown route → 404. Unexpected backend exceptions are redacted as a generic 503.

| HTTP method and suffix | Purpose and body |
| --- | --- |
| \`GET /status\` | Module health/status; zero spending |
| \`GET /publications?limit=30\` | Only fully approved Deep C9 publications with both Human Reviews and exact final tree; limit 1–50 |
| \`GET /capabilities\` | Read-only available SP09 catalog (no Collector ready by default) |
| \`POST /capabilities/match\` | \`{"query":{"query_mode":"keyword","text":"...","language":"ru","market":"BY"}}\`; informative only, never authorization |
| \`POST /contexts/preview\` | \`{"publication_id":"...","search_policy":{...}}\`; immutable SP1/SP2 context preview |
| \`POST /generation/configs\` | \`{"configuration":{...}}\`; exact validated config, project-locked identical SHA registration now idempotently reuses existing ID |
| \`POST /generation/plan\` | \`{"publication_id":"...","config_id":"...","idempotency_key":"..."}\`; durable generation/batch planning, no paid call |
| \`GET /generation/{id}\` | Zero-cost run status |
| \`GET /generation/{id}/candidates\` | Immutable generation bundle and coverage |
| \`POST /generation/{id}/quote\` | Empty \`{}\` body; price estimate, no consent |
| \`GET /generation/{id}/budget\` | Financial ledger inspection, zero spending |
| \`POST /generation/{id}/consent\` | \`{"quote_id":"...","authorized_usd":"0.25000000"}\`; server owner guard + backend quote/budget checks |
| \`POST /consent/{id}/revoke\` | Empty body; owner-gated consent revocation |
| \`GET /generation/{id}/execution\` | Ledger/batch/attempt states |
| \`GET /generation/{id}/preflight\` | Metadata eligibility; no LLM POST |
| \`POST /generation/{id}/execute-one\` | \`{"idempotency_key":"..."}\`, operator-enabled paid gate, durable HTTP one-shot record, one SQG worker batch |
| \`POST /generation/{id}/recover\` | Optional \`older_than_seconds\`; mark uncertain, no blind retry |
| \`GET /generation/{id}/stale-reservations\` | Optional bounded \`older_than_seconds\`, read-only |
| \`POST /generation/{id}/reservation/{attempt_id}\` | Optional bounded \`older_than_seconds\`; owner-only release of **unsubmitted** reservations |
| \`POST /generation/{id}/cancel\` | Empty body; owner-only cancel |
| \`POST /qv/validate\` | \`{"source_outbox_id":"...","policy":{...}}\`; preinstalled pinned LOCAL model required, test fixture prohibited |
| \`GET /qv/{id}/validated\` | Production QV selected/decs/coverage |
| \`GET /qv/{id}/links\` | Immutable selected↔alias/hypothesis/source fact links |
| \`GET /tasks/{qv_id}/preview\` | Default held-only plan, no Collector IO |
| \`POST /tasks/{qv_id}/preview\` | Optional complete \`limits\` object for held preview |
| \`POST /tasks/{qv_id}/commit\` | \`{"idempotency_key":"...","limits":{...}}\`; server-only owner guard, persists **HELD**, not Collector delivery |
| \`GET /handoff/{qv_id}/preview?chunk_size=50\` | SP11 verified deterministic chunks; 117→50+50+17 |
| \`POST /handoff/{qv_id}/preview\` | Optional \`chunk_size\`, 1–50; no ACK |
| \`POST /handoff/{qv_id}/ingest\` | Empty body, project-scoped QV→SP atomic durable event+chunks+ACK |
| \`POST /handoff/drain\` | Optional \`limit\` 1–50; bounded explicit local QV inbox processing |
| \`GET /inbox/{event_id}/status\` | True stored inbox status and separate receipts; zero provider calls |
| \`POST /inbox/{event_id}/deliver\` | Empty body; owner-held approval and compatible server-provisioned Collector port required |
| \`POST /inbox/{event_id}/reconcile\` | \`{"chunk_index":0}\`; only independent receipt re-read, NEVER second accept/POST |

There is deliberately **no arbitrary source search, test_fixture validation, user-provided Collector port, background service start, or automatic provider loop** in the public API.

## 3. Safe operator setup and example

Generate a high-entropy password using OS secret-generation facilities (for example \`python -c "import secrets;print(secrets.token_urlsafe(48))"\`) and set \`CM_SP_OWNER_TOKEN\` **before launching** the local server. Do **not** commit the token to \`.env\` in Git, put it into public JS, paste it into reports, or log its value.

\`\`\`bash
# Local owner process configuration, token value intentionally not shown:
export CM_SP_OWNER_TOKEN='<generate_random_secret_of_at_least_32_chars>'
# No provider work is permitted unless explicitly enabled:
export CM_SP_PAID_HTTP_ENABLED=0
# Provision when local semantic QV is actually installed:
# export CM_SP_EMBEDDING_MODEL_PATH='/private/pinned/multilingual/model'
python app.py
\`\`\`

Then the local UI/test client sends \`Host: 127.0.0.1:8765\`, \`Origin: http://127.0.0.1:8765\`, \`X-ContentMachine: 1\`, \`X-CM-SP-Owner-Token: <secret>\` and \`Content-Type: application/json\` on SP POST requests. All route bodies are strict JSON objects, 2–32,768 bytes, no duplicate keys, no NaN/Infinity, no chunked/streaming uploads. Idempotency keys contain 12–128 ASCII alphanumeric, underscore, dot, colon or hyphen characters. GET query keys are explicit, size-bounded and may appear only once. Unsupported/forged fields are rejected, not silently ignored.

## 4. Durable HTTP paid idempotency

New **additive** migration \`intelligence/migrations/056_sp_http_paid_idempotency.sql\` creates \`sp_http_paid_actions\`, unique by \`(project_id,idempotency_key)\`, FK to authentic SQG generation, immutable action/generation/digest and state \`in_progress → completed|uncertain\`.

Before any permitted paid HTTP call, the server project-locks and transactionally inserts \`in_progress\` reservation **and commits it**. With the same key, a second browser POST or restarted server reads the existing row:
- \`completed\` returns the exact stored result, **worker called 0 times** for replay;
- \`in_progress\` or \`uncertain\` returns \`needs_attention\` and never invokes another paid worker; reconciliation is through existing SQG financial execution status/unknown dispatch investigations;
- another generation with the same key returns **409**;
- an exception or ambiguous result from the worker leaves state \`uncertain\`; paid budget is **not** released automatically.
- A **new** idempotency key is a **new explicit owner request** and can select another eligible batch; treat it as a new authorization event, never as an automatic retry.

This HTTP ledger **does not replace the SQG financial ledger**. The latter separately records signed quote, authorizations, irrevocable \`dispatch_intent\`, billed versus uncertain attempts and spending ceilings. HTTP payload cannot grant these rights.

## 5. Files and focused QA

- \`intelligence/search_planner/http_api.py\`: strict bounded routing, local owner verification and project lookup, no credential echo, separate GET/POST dispatch, one-paid-worker semaphore.
- \`intelligence/search_planner/http_paid.py\` and \`intelligence/migrations/056_sp_http_paid_idempotency.sql\`: durable HTTP idempotency and uncertain response safety, project-scoped immutable SQL.
- \`intelligence/search_planner/input_loader.py\`: C9 publication picker joins approved deep state/both human reviews/final approved_nodes and calls exact trusted reader on same snapshot.
- \`intelligence/search_planner/generation_engine/run_manager.py\`: project-locked config SHA de-duplication prevents a browser's repeated POST from adding new configuration revisions.
- \`intelligence/search_planner/service.py\` and \`api_contracts.py\`: safe method exports; no new mandatory Collector dependency.
- \`app.py\`: tiny lazy mount only for SP paths, preserving all pre-existing routes.
- \`tests/test_search_planner_http_api.py\`, \`tests/test_search_planner_stage13_publications.py\`, \`tests/test_search_planner_stage13_paid_http.py\`, \`tests/test_search_planner_stage13_config_idempotency.py\`: isolated local HTTP, project ACL, malformed body and SQL idempotency regression.
- \`.github/workflows/sp-stage01-fast.yml\`: focused SP01..SP13 suite.

\`\`\`bash
python -m compileall -q intelligence/search_planner tests/test_search_planner_http_api.py tests/test_search_planner_stage13_publications.py tests/test_search_planner_stage13_paid_http.py tests/test_search_planner_stage13_config_idempotency.py
python -m unittest discover -s tests -p 'test_search_planner_http_api.py' -v
python -m unittest discover -s tests -p 'test_search_planner_stage13_publications.py' -v
python -m unittest discover -s tests -p 'test_search_planner_stage13_paid_http.py' -v
python -m unittest discover -s tests -p 'test_search_planner_stage13_config_idempotency.py' -v
\`\`\`

**Not verified as PASS:** exact commit SHA GitHub Actions focused/full Linux+Windows, full local app+browser HTTP integration, fresh/upgrade PostgreSQL17 migrations 054/055/056, two concurrent process idempotency/crash/WAL, true installed multilingual weights, real Collector authenticated intake. Existing runners are still queued. These are SP15/SP16 integration/release gates. Do not merge Draft PR #34 without owner approval. SP14 will add the user interface and a secure local operator token acquisition flow, without storing the owner secret in local storage or embedding it in JavaScript bundles.
