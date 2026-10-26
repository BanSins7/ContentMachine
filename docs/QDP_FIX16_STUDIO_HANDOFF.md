# QDP-FIX-16 — Studio API and operator UI

**Status: CODE_IMPLEMENTED.** Full tests [40367164789](https://github.com/BanSins7/ContentMachine/actions/runs/40367164789) passed **245/245** on QDP code `cc46bfa6`, PostgreSQL 17, Python/JS syntax, and actual Group A/C peer intake CI. After that, `6c84baa3` restored the legacy 100-row bare run GET (exact SHA CI pending at authoring).

## Local authorization and safety

Application `app.py` already enforces localhost Host/Origin and `X-ContentMachine: 1` for mutations; QDP reuses those original gates without remote auth bypass. Every GET/POST is scoped to 32-char lower-case project ID and validated canonical UUID. Query parameter allowlists, non-duplicated scalar inputs, 1–100/200 bounded page sizes and 2–4096 byte JSON bodies fail closed. No arbitrary public origin/source `revoke` endpoint exists. GET never contacts OpenRouter, DTK, TikTok, downloads videos, launches workers, or writes Group results.

## Routes

| Method | Suffix under `/api/projects/:project/qualified-data/` | Contract |
|---|---|---|
| GET | `eligible-sre-runs?limit=25&cursor=<SRE run UUID>` | Completed LIVE SRE, approved taxonomy, checked model resolution; failure if SRE LIVE schema absent; keyset next cursor. Selection is not full manifest preview. |
| GET | `preview?sre_run_id=<UUID>` | Read-only full SRE selection/manifest evidence coverage and qualified/review/excluded counts. |
| POST | `runs` | Exact `{sre_run_id,idempotency_key,config}`; durable free local QDP jobs, not Group tasks. |
| GET | `runs?limit=30&cursor=<QDP run UUID>` | Project-scoped run list by `(created_at,id)` keyset. |
| GET | `runs/:run_id?limit=50&after_video_id=<ID>` | Current-rights-gated video/group page by video ID. Bare `GET runs/:run_id` preserves legacy 100-video default. |
| GET | `runs/:run_id/manifest` | SHA-256 pinned taxonomy/selection/manifest and expected counts; not raw forbidden content. |
| GET | `runs/:run_id/diagnostics` | Group historical/current readiness counts, consumer deliveries and invalidation watermark. No fake completion. |
| GET | `runs/:run_id/events?limit=40&cursor=<QDP event UUID>` | Project-specific, immutable event metadata and delivery status; no full event payload. |
| GET | `runs/:run_id/lineage?video_id=<positive D1 ID>` | Exact evidence hashes/IDs only when CURRENT view authorizes that video; independent A/B media and C metric redaction. |
| POST | `runs/:run_id/refresh` | Exact `{idempotency_key,config}`; new independent QDP run ID. Previous revocations, immutable history and receipts remain. |
| POST | `runs/:run_id/cancel` | Existing `{}` bounded local cancellation; never cancels peer-owned already accepted jobs. |

All GET responses are read-only, JSON `ok=true`, and cross-project/invalid cursors fail closed. No browsing query creates provider paid work. No new broad database privilege is required.

## Full QDP Studio

`site/index.html` now contains an explicitly selected LIVE SRE run **dropdown** instead of accepting arbitrary run UUID strings, plus Preview→Create, expiry limits, mandatory published timestamp toggle, run history, run-specific Refresh/Cancel, manifest digests, independent A/B/C diagnostics, delivery ACK state, signed event list, paginated video items, and selected video provenance. `site/qualified-data.js` renders untrusted strings by `textContent`, not `innerHTML`, and resets screens on project change. Pinned SRE identity/list alone is not authorization: full preview, job worker and downstream must recheck current SRE/D1 rights.

Pagination is bounded: up to 25 LIVE SRE rows, 25 history rows, 50 video items, 40 events at a time (keys supplied by server). No unbounded browser loops or full 10k item fetch.

The UI distinguishes `ready` prepared facts, `acknowledged` consumer inbox receipts, and **completed Group analysis** (not claimed or fabricated by QDP). A human or policy veto makes old evidence inaccessible; the Refresh button explicitly requests a **new** run instead of silently overwriting historical content.

## QA and external gates

`tests/test_qdp_studio.py` tests strict GET/POST allowlists, too many/oversized query fields, malformed body, local parent CSRF order, bad project paths, cursor routing, refresh and safe DOM. Disposable PG17 `tests/test_qualified_data_db.py` covers only verified LIVE picker, run/video/event keyset pagination, immutable manifest summary, group delivery diagnostics, cross-project isolation and revocation redaction, A/B media vs C metrics visibility. Group A/C independently pinned actual intake CI remains green. **Manual browser session on the user's machine is NOT verified**; UX visual polish needs real browser QA. Group B remains incompatible with current QDP v2, UAE completed A/B/C readers and real Group revoke ports remain absent/blocked. Do not conflate those blockers with QDP local Studio tests.

No writes to `main` or peer Group branches by FIX-16.
