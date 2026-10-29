# GA-MOD-21 — Project-scoped local HTTP API, Studio and observability

**Branch:** `feature/group-a-full-module`, Group A owned files only. **Implementation:** opt-in committed. **Python+PG17+browser E2E:** NOT VERIFIED until execution logs exist. No `app.py`, `main`, QDP, Group B or other-module code modified. This stage does not activate workers, install paid models or accept direct file uploads.

## Explicit host registration (required)

```python
from intelligence.group_a import GroupAService, mount_group_a_handler

ga=GroupAService()
facade=ga.make_local_http_facade(
    connect=owned_psycopg_connection_factory,
    authorize_project=host_authenticated_project_authority,
    verify_current=host_current_project_qdp_source_media_authority,
    verify_result=host_confirm_exact_GA_snapshot_origin_and_bytes,
    resolve_manifest=host_reopen_authoritative_GA05_manifest_for_GA04_run,
    verify_authority=host_authorize_cancel_project_run,
    allowed_hosts=("127.0.0.1:8765","localhost:8765"),
)

# Host integration point; deliberately not committed into app.py:
MountedHandler = mount_group_a_handler(app.Handler,facade)
server = ThreadingHTTPServer((app.HOST,app.PORT),MountedHandler)
```

**No implicit app startup modification**: real host must replace its server Handler binding as shown. Without the opt-in mount, versioned URLs and `/group-a` are not active. The old unmounted A1 `http_api.handle_get/post` still uses an explicit unavailable legacy handoff; new routes do not silently bypass that gate. `GroupAService` exports a constructor, not a running server.

`authorize_project(project_id,permission)` MUST return a *trusted host-authenticated* object `{"allowed":true,"actor_ref_sha256":"64 lowercase hex"}` only for the logged-in operator, exact project, and requested `read`/`write` scope. Never trust a client-supplied project ID alone. `verify_current(scope)` MUST authenticate the live current project, QDP preparation, source policy and original media bytes; `verify_result(immutable_result)` re-confirms A003/A004/GA result SHA and provenance. `resolve_manifest(project_id,run_id)` must look up an existing GA04 accepted run and GA05 verified media manifest; the browser never sends a forged raw manifest. `verify_authority(scope)` must authorize manual run cancellation or other destructive actions in the current project. A callable returning `True` unconditionally is suitable for isolated unit tests **only**, never production.

## Frozen endpoint matrix

All HTTP endpoints are under `/api/projects/{32-hex-project-id}/group-a/v1`, opt-in on trusted local Host `127.0.0.1:8765` or `localhost:8765`. `Origin` must be absent or one of the exact local HTTP origins. Mutation also requires `X-ContentMachine: 1` and JSON `Content-Type: application/json`; bodies are capped at **4,096 bytes**, responses at **65,536 bytes**. The host's `respond` adds CSP `script-src 'self'`, no-store, nosniff and referrer policy. All errors contain a stable code only, never provider exceptions, paths, credentials, SQL text, headers or captured model output.

| Method | Route | Truth / side effects |
|---|---|---|
| GET | `/capabilities` | Registered API capabilities; runtime explicitly opt-in, QDP external rights required |
| GET | `/runs?limit=20&cursor=...` | Project-scoped current QDP-authorized run listing, keyset cursor |
| GET | `/runs/{uuid}` | Run state, stage/outbox status counters |
| GET | `/runs/{uuid}/steps?limit=20&cursor=...` | Stage attempts, resource slots, duration & reserved slot-seconds |
| GET | `/runs/{uuid}/events?limit=20&cursor=...` | Immutable operator actions, not raw private traces |
| GET | `/runs/{uuid}/measurements?limit=20` | Only locally completed plus externally re-verified A003/A004 pinned result; NULL remains unknown; bounded scene/speech/OCR/music time windows and carousel slide indices |
| POST | `/runs/{uuid}/start` body `{}` | Install/replay GA20 DAG for **existing GA04 run**, no automatic worker or model start |
| POST | `/runs/{uuid}/pause` body `{}` | Pause new claims (in-progress leases remain cooperatively cancellable) |
| POST | `/runs/{uuid}/resume` body `{}` | Resume new claims; current rights reverified |
| POST | `/runs/{uuid}/cancel` body `{}` | Explicit host-authorized cancel; completed snapshot history preserved |
| POST | `/runs/{uuid}/retry` body `{"step_key":"..."}` | Only failed locally safe stage under GA20 attempt limit/backoff/lease constraints. **Never paid provider retry** |

All other methods, unrecognized paths, query fields and duplicate query values fail closed. Cross-project runs are rejected; writes use an outer psycopg transaction with audit action so a foreign run or audit failure rolls back the operation. The `ga_runs` and `ga21_local_stage_telemetry` references are the new GA 054/057 schema, not QDP shadow tables.

## Studio UI

Open `http://127.0.0.1:8765/group-a?project_id=<32hex>` after host registration. Static assets come from `intelligence/group_a/site/group-a.html`, `group-a.css` and `group-a.js`; no CDN, network fonts, analytics SDK, external origin, inline JS, template HTML insertion, local storage or eval. Dynamic strings render via DOM `textContent`, so malicious OCR/transcript text cannot become HTML. The page fetches only the authenticated project-scoped API; it never gets media filesystem paths or raw PCM.

The interface provides project and GA04 run selection, current run status, stage progress and CPU/GPU *logical reserved-slot telemetry*, explicit start/pause/resume/cancel and manual safe-stage retry, operator audit events, A003 video scene boundaries, first speech/on-screen observation state and local clock, per-video speech and OCR segment timestamps, music/SFX event windows and A004 ordered photo/video slides. Unmeasured/partial fields remain `—`, not zero/complete. The API intentionally does **not** transmit raw speech/OCR strings (only timing, observed statuses/counts), even if privately present in A003 tables.

The page does not claim new GA18 independent human gold, automatic dense mixed-carousel video OCR, Group B classified CTA, actual device GPU utilization or OS-enforced resource budgets. `reserved_cpu_slot_sec`/`reserved_gpu_slot_sec` are computed from GA20 attempt wall durations × reserved slots, NOT actual CPU energy/cost or money spent; the real costs are unknown unless a trusted host supplies separate telemetry. Media source SHA and QDP data must remain current at every read.

## Additional persistence

Forward-only Group A migration `058_group_a_api_observability.sql` adds `ga21_operator_events`: project/run FK, audit event type and SHA-256 pseudonymous operator ref, immutable insert-only, action digest unique per run. It stores no username, IP, request/response body, local file path or HTTP headers. `ga21_local_stage_telemetry` aggregates GA20 step attempts and resource slot reservations without claiming physical utilization; it does not change GA 054/057, existing views or any external tables.

## Test matrix and truthful status

```bash
python -m compileall -q intelligence/group_a tests/test_group_a_http_studio.py
python -m unittest discover -s tests -p 'test_group_a_http_studio.py' -v
python -m unittest discover -s tests -p 'test_group_a_*.py' -v
node --check intelligence/group_a/site/group-a.js

# Disposable PostgreSQL 17 only, never production:
CM_TEST_PG_DSN='postgresql://.../ga21_disposable' \
 python -m unittest discover -s tests -p 'test_group_a_http_studio.py' -v
```

Committed tests: valid project run/status pages, foreign project denied, Host and Origin rejection, missing `X-ContentMachine`, incorrect JSON MIME and oversized/missing bodies, duplicate/unknown query fields, unsupported method/path, malformed cursor/limit, host injected exceptions redacted, explicitly mounted unrelated host handler route continues functioning, HTML has external same-origin scripts and JS has no unsafe DOM HTML writes. Conditional PostgreSQL 17 tests reuse a disposable isolated GA04/GA20 fixture to verify source/run scoping, stage telemetry, pagination, immutable operator actions, no A003 measurement for incomplete run. Script syntax may be checked with `node --check`; source parser checks are not browser E2E.

**QA**: the source/JSON/JS static verification and written test source do not constitute successful Python/PG17 or browser tests. No live host registration, authenticated QDP provider, external Group B receiver or Windows/Linux E2E is claimed. **Next GA22** performance/gold accuracy; GA23 PG17 multi-module E2E and actual browser/Windows/Linux CI; GA24 integration/merge gate with independently observed logs.
