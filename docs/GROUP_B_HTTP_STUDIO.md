# Group B — GB-MOD-26: HTTP API & Operator Studio

**Implementation branch:** `feature/group-b-full-module` only. **Status:** routes/Studio/source-scoped repository and tests implemented, NOT integration-verified. **No edits to `main`, SQL005 or other module branches.**

## Scope and deployment

The dashboard is the existing Python `http.server.ThreadingHTTPServer` in `app.py` bound to `127.0.0.1:8765`, **not Flask**. Its `Handler.authorized()` already checks the exact localhost Host and Origin and requires `X-ContentMachine: 1` on mutations. Stage26 adds minimal GET/POST dispatch to `intelligence/group_b/http_api.py` before other project handlers, and static assets `site/group-b.js`, `site/group-b.css` to the exact asset allowlist.

The UI mounts via `window.ContentMachineGroupB.mount(root,project.id)` inside the *existing per-project Details screen* in `site/projects.js`, not in other modules' screens. Safe DOM `textContent`/element constructors; no `innerHTML` of provider/text content, no localStorage-driven reviewer identity/paid permission. Layout supports run list pagination, selected run, B1–B8 block statuses/error codes, reference SHA evidence, review history revision, stale/invalidated status and operator forms. It reports `qualification: unverified` until an independently signed current source/quality/UAE gate has run.

## HTTP routes

All routes are restricted to `/api/projects/{project_id}/group-b`, with lowercase 32-hex project and canonical UUID run IDs. `GET` accepts exact `offset=0..100000`, `limit=1..50`, and `block=B1..B8` only for evidence. Unknown query keys, repeated parameters, malformed UUIDs, content types and oversized request bodies are rejected. JSON POST has explicit length limit 48 KB and disallows unrecognized fields. Responses always set the existing app's `Cache-Control: no-store`, CSP, referrer policy and X-Content-Type-Options. No CORS/OPTIONS endpoint added.

| Method | Route | Action |
|---|---|---|
| GET | `/runs?offset=0&limit=20` | Project-scoped paginated history (no source media/OCR body) |
| GET | `/runs/{id}?offset=0&limit=20` | Actual runtime states, B1–B8 statuses, bounded jobs, quote metadata, revision count, invalidation flag |
| GET | `/runs/{id}/evidence?block=B7&offset=0&limit=20` | Only authenticated original reference SHA metadata via independent fresh media-rights/evidence-access check; no raw media or model text by default |
| POST | `/runs` | Request new run from existing verified QDP inbox by UUID and idempotency key. Server must be connected to a trusted QDP host creating actual durable inbox/run/receipt; **browser cannot invent QDP** |
| POST | `/runs/{id}/quote` | Real `BudgetQuote` from trusted model catalog+evidence context, persisted by existing `save_quote`; **no model POST** |
| POST | `/runs/{id}/approve` | Exact USD amount, explicit consent, authenticated operator identity from server, existing `approve_quote`. Header or HTML `confirmation: true` **is not** proof of user identity |
| POST | `/runs/{id}/start` | Requires fresh independently verified `ReadinessReport`, current media source rights and per-block paid attempts; existing `enqueue_jobs` only (no provider dispatch from HTTP) |
| POST | `/runs/{id}/cancel` | Explicit confirmation and authenticated operator. Existing `cancel_run` with transactional lease fencing; **does not refund/zero uncertain paid reservations** |
| POST | `/runs/{id}/review` | Builds original-analysis-pinned `ReviewDecision`, requires trusted authenticated reviewer and independent per-ref evidence admission; commits immutable GB23 revision and GB24 invalidation atomically. Gold annotations distinct from actual corrections |

Implementation:
- `intelligence/group_b/studio_service.py`: scoped SQL and bounded responses; real existing `group_b_runs`, `group_b_jobs`, `group_b_block_states`, `group_b_review_events`, `group_b_model_quotes` and `group_b_outbox`. Never accesses other projects by bare analysis_id.
- `intelligence/group_b/http_api.py`: route and JSON parser; no DB or network side effects before same-origin check. Unexpected service/internal errors are sanitized rather than leaking DSN, API key or source content.
- `site/group-b.js`, `site/group-b.css`: project-isolated interactive operator panel and B1–B8 evidence.

## Deliberately fail-closed runtime capability

**The repo does not currently contain a credential-backed, production-grade authenticated reviewer/paid/QDP operator host.** The default HTTP service uses an explicitly local-only project profile permission for **project-scoped metadata reads only**. All mutating operations are denied as `trusted_*_not_connected` until a trusted server-side `GroupBStudioService` with a real `operator(project_id,action,conn)` plus an authenticated `host` is installed using `configure_operator_service(service)`. The public HTTP API **cannot configure or replace these hooks**, and incoming JSON never supplies a verified reviewer/user/receipt. This is deliberate to avoid manufacturing production approvals or fake successful analysis.

The externally installed host must provide independent credentials and at least these scoped capabilities:
- `create_verified_run(project_id,run_id,conn,inbox_id,idempotency_key)` verifies current original QDP accepted event and persists a real durable receipt/run; returns an actual bound run UUID;
- `quote_verified_context(project_id,run_id,conn,body,run)` prepares authenticated typed `BudgetQuote` (catalog, image unit prices, media, QDP/GA and permissions);
- `verified_approving_operator(...)` authenticates identity and explicit user consent separately from request JSON;
- `verified_job_readiness(...)` returns typed `ReadinessReport` and preapproved exact paid attempt IDs without any paid HTTP call;
- `verify_cancel_operator(...)` confirms authenticated project permission;
- `verified_reviewer_identity(...)` plus GB23 `verify_human_review_admission(...)` validate the actual reviewer/source/secondary approval and original source refs;
- `verify_evidence_read(...)` confirms current rights/expiry and operator permission before exposing references.

Missing host means **genuine 424 dependency unavailable**, never an automatically successful pretend operation. Programmatic callers can provide a verified host without touching peers, but the last missing identity and QDP/media/provider caps MUST be implemented by the relevant owners. Opening Studio does not start workers, bill models, import media or qualify Analytics.

## Tests

- `tests/test_group_b_http.py`: host/origin and mutation header, project ID and UUID, pagination, JSON type/body size, unrecognized routes, project isolation before query, missing trusted QDP/paid approver, safe error redaction.
- `tests/test_group_b_http_db.py`: optional disposable PG17 actual run/B1–B8/status/quote metadata, foreign-project block, real cancellation via fake fixture-only operator, missing trusted model approval/evidence callback.
- `tests/test_group_b_ui.cjs`: Node built-in test runner, fake DOM project mounting, B1–B8 UI rendering, nonqualified text, no `innerHTML`, static allowlist, invalid project refusal. `node --check` syntax command.
- Wired to Group B Linux/Windows/PG17 workflow; PostgreSQL tests skip without `CM_TEST_PG_DSN`.

```bash
python -m unittest discover -s tests -p "test_group_b_http.py" -v
node --check site/group-b.js
node --test tests/test_group_b_ui.cjs
CM_TEST_PG_DSN="postgresql://.../cm_group_b_test" python -m unittest discover -s tests -p "test_group_b_http_db.py" -v
```

**QA truth:** implementation and tests committed; actual local Python/Node/PG17 execution and green CI have not yet been observed. **Operational blockers:** real operator auth, producer QDP receipt/GA/media current proof, paid authorization, full B1–B8 completed source and reviewer identity, and actual interoperable UAE/Group C. UI is present and read-only by default, privileged actions deliberately unavailable until authenticated host capability is wired.
