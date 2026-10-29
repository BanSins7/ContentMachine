# M38 — SRE local read-only HTTP API (code completed, QA not accepted)

**Branch:** `feature/sre-production-v1-20261023`, Draft PR #22. **2026-10-28.** Source-code-only milestone [x]. Exact-head Linux/Windows/PG17 PASS **not verified**. Full M42–M47 release gates, M34/M35/M36/M37 ecosystem blockers unchanged. No paid Opus POST, QDP/Collector activity or main/peer branch edits.

## Entry point

`intelligence/relevance/http_read_api.py` exposes a dependency-free, WSGI-compliant `LocalSREReadAPI` and `create_loopback_server(app,port)`. Both are **disabled by default**. Importing these modules does NOT open a socket, PostgreSQL, spend money, load a model, run a worker or call a provider. The host must explicitly opt in, authenticate the actor and bind **only 127.0.0.1**. `create_loopback_server` only returns a server object; it does not call `serve_forever()`.

Example with **host-provided**, independently verified session credentials (not a standalone insecure sample auth implementation):

```python
from intelligence.relevance.http_read_api import (
    LocalSREReadAPI, create_loopback_server
)

# Host supplies these functions from its own trusted project authentication.
# connect() must yield an idle, autocommit=True, dedicated PostgreSQL connection.
# session_actor(environ) MUST verify host/OS session or session credential;
# do not trust HTTP_X_ACTOR_ID / HTTP_X_PROJECT_ID / arbitrary browser headers.
# project_authorizer(pid,actor,permission) MUST validate exact project scope.
app = LocalSREReadAPI(
    connect=connect,
    actor_resolver=session_actor,
    actor_authorizer=project_authorizer,
    enabled=False,  # initially disabled; opt-in after QA and auth review
)
# After operator approval, construct with enabled=True and explicitly start:
# server = create_loopback_server(app,8765)
# server.serve_forever()
```

**No client-provided identity becomes an authenticated host actor.** Trusted host owns session verification; actor string must match the trusted host authorizer. `manifest` requires `read_sre_api_manifest` for a verified actor, `pid=None` for project-independent metadata. Every project route requires a separate project-scoped permission `read_sre_api_<route>`, validated before any DB connection. Other users/projects get generic access-denied errors without SQL/raw data. Local loopback `REMOTE_ADDR` and safe `Host` are required; forwarded proxy headers are rejected. WSGI cannot independently detect an unauthenticated local proxy that hides its forwarding, so **never deploy through a non-loopback proxy**.

## Supported GET / HEAD paths

All routes are under `/api/sre/v1`. Project ID must be 32 lowercase hexadecimal, and all other IDs must be exact canonical UUID or positive PostgreSQL BIGINT. `HEAD` performs the same auth+read but returns no body.

| Path | Data | Rights/safety |
|---|---|---|
| `/manifest` | Explicit route manifest, local/read-only capabilities | Verified host identity/manifest permission; no secrets |
| `/projects/{pid}/health` | Schema presence and availability only | Workspace+project permission; never implies actual LIVE/paid readiness |
| `/projects/{pid}/taxonomy/{publication_id}` | Approved C9 revision, category UUID/key hierarchy preview | Real approved H1/H2 C9 publication checked against RTB and SRE projection; bounded nodes; no raw quotes/facts |
| `/projects/{pid}/content/{content_version_id}/preview` | Rights-checked title/description metadata excerpts, D1 version/source SHA | Current S3/D1 read gate; no transcript, media, or full raw source text; no stale/tombstoned source |
| `/projects/{pid}/runs` | LIVE-only run status list | Project-scoped; no source body; keyset paging |
| `/projects/{pid}/runs/{run_id}` | Original LIVE state, completion counters and C9 pin | Never declares synthetic/legacy run LIVE |
| `/projects/{pid}/runs/{run_id}/results` | Genuine M30 receipt-backed per-category model result and M32 latest human overlay | One project/live run, current D1 source rights checked for EVERY returned row including lookahead, no provider text or body |
| `/projects/{pid}/runs/{run_id}/history` | Minimal immutable human-review history metadata | Source-rights recheck on each row, no review notes or reviewer personal data |
| `/projects/{pid}/runs/{run_id}/review-queue` | Not-yet-reviewed authenticated M30 pair results | Bound by true SQL predicate `h.review_id IS NULL` BEFORE limit, D1 rights rechecked |
| `/projects/{pid}/runs/{run_id}/quotes` | Active unexpired M23 quote **metadata only** (hash, conditional max USD, issue/expiry), NOT quote body | No cancelled/UNKNOWN/completed or revoked/expired quotes; requires approved C9, current D1 rights and absence of matching revoked consent; does not grant spending approval |

For collection endpoints use `?limit=1..100&after=<opaque-or-typed-cursor>`; defaults to 50, returns `rows,has_more,next_cursor`; invalid/duplicate query names and unbounded inputs denied. The API uses stable deterministic keyset paging, not cross-project offsets. Unpaged health/run/content may not carry a cursor. `read_api.py` contains all SQL projections and minimal serializable response dictionaries. Quotes return fixed decimal strings and are clearly labelled as conditional maximums without exact token count or consent. The source response for results/history/review-queue is aborted if even a *lookahead* D1 record loses rights, instead of leaking which revoked rows existed through pagination.

**All actual data calls run inside one PostgreSQL `SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY` transaction** on an idle autocommit=True connection; no SQL INSERT/UPDATE/DELETE, no other module's DB writes, no provider/network calls. Unknown routes return 404, POST/PUT/PATCH/DELETE/OPTIONS return 405 with `Allow: GET, HEAD`. HTTP replies always use no-store, JSON, no-referrer, nosniff, CSP and sanitized generic errors; driver exceptions/credentials are not echoed. *A trusted WSGI host must protect its auth middleware and not override REMOTE_ADDR from headers.*

## Tests and acceptance boundaries

SRE-owned tests authored:

- `tests/test_sre_http_read_api.py`: disabled-by-default, authenticated manifest, project role and local address enforcement, header/forwarded spoofing, noncanonical IDs, all read routes in read-only DB transaction, GET/HEAD, forbidding mutations, no leaking SQL/DSN exceptions, pagination query validation.
- `tests/test_sre_read_api.py`: source-matched C9 and D1 previews, bounded response and keyset cursor, revoked source abort including lookahead, only genuine M30 source+M32 human overlay for results, unreviewed SQL queue, original human history, no raw quote/consent and revocation+expiry quote exclusion, proof that schema-presence is not LIVE QA readiness.
- `.github/workflows/sre-contracts.yml` extended to M38 on Linux/Windows. Tests are **written; exact-head run outcome must be inspected before marking QA PASS**.

**Known pre-existing blocker:** broken SQL `062_sre_live_opus_receipts.sql` prevents a clean PostgreSQL17 migration chain; do not silently change its historical applied SHA. See `docs/SRE_M36_062_MIGRATION_BLOCKER_FORENSICS.md`. This M38 code does not solve M16–M18 ONNX, M25/M26 authenticated/paid multi-leaf evidence, M34 physical retention, M35 real Collector Stage10, M36/M37 QDP mismatch or M42+ acceptance.

**Progress accounting:** SRE **M01–M38 / 47 = 80.85% source-code implementation**; not an overall production-readiness percentage. M38 code [x], exact-head QA [ ] not verified, real authenticated browser E2E [ ] BLOCKED. Next source stage **M39 (mutations + origin/CSRF/owner/consent protections)**, with no paid automatic starts or other-branch edits.
