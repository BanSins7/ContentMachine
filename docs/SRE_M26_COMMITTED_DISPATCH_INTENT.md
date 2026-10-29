# SRE M26 — Durable, consent-bound pre-HTTP Opus dispatch intent

**Branch:** `feature/sre-production-v1-20261023` · [Draft PR #22](https://github.com/BanSins7/ContentMachine/pull/22) · **2026-10-27**.

**Status:** source + forward-only 061 + unit/schema + PostgreSQL17 integration test **COMMITTED**. Full exact-head CI acceptance **NOT CONFIRMED**. No HTTP transport, billable tokens, receipt, settlement or model classification is performed or enabled by M26.

## Why M26 is independent of the provider adapter

A paid POST must never happen while the application's budget/attempt SQL transaction is open, and any crash between the committed intent and a receipt must be conservatively treated as a **possibly charged outcome**. M26 deliberately **commits the intent but does NOT POST**, keeping the paid adapter completely disabled until the separately tested M27–M29 stages.

The M25 project budget is installed only by explicit owner action. A valid quote does not mean approved spend. A valid owner consent does not reserve money. A valid reserve does not prove that provider pricing, exact tokens, account fees/taxes or live HTTP routing are verified.

## Trusted internal service boundary

`intelligence/relevance/opus_dispatch_intent.py`:

- `commit_live_sre_opus_dispatch_intent(conn, project, run_id, item_id, batch_id, reservation_id, quote_sha256, catalog_snapshot, *, request_ordinal, worker_token, lease_generation, actor_id, actor_authorizer, operator_expected_sha256, operator_endpoint_tag, operator_model_revision, operator_maximum_usd)`: **not an HTTP handler**; only a trusted host-owned authenticated operator may call it. Required strict `actor_authorizer(..., "commit_sre_opus_dispatch_intent") is True`, owner identity not accepted from an arbitrary request header. The operation requires a dedicated idle `psycopg` connection configured `autocommit=True`, rejecting all caller-held, nested or failed transactions (which would otherwise falsely claim that the intent was committed).
- It locks the SRE workspace exclusively to serialize against M15 cancellation/recovery, checks no previous possible paid M15 or M26 intent **anywhere in the LIVE run**, checks exact LIVE run+batch state and unfenced worker lease using the DB clock, rechecks actual S3/D1 external-send rights for exact video/content version/SHA, revalidates current M23 quote + M24 stage/actor-original consent against real approved C9/D1 and operator-pinned M22 endpoint/catalog, and demands a CURRENT `reserved` M25 exact `NUMERIC(24,9)` USD hold. No float, auto-downsizing, alternate model or fallback.
- It regenerates the complete M20 C9/D1 sealed request pack from the trusted database and confirms full batch SHA, exact M20 request ordinal and request SHA, quote revision/model `anthropic/claude-opus-5.5`, catalog SHA and full consent-bound reserved upper USD amount. The immutable M23 quote explicitly does **not** claim exact billable Opus tokens.
- In **ONE database transaction**, inserts a new M15 `reserved` technical witness with exact request SHA, changes it to `dispatch_intent` with DB timestamp, and inserts a cross-FK M26 immutable intent with exact `attempt_id`, worker token/lease generation, `quote_sha256`, `reservation_id`, `consent_id`, request ordinal/SHA, M20 batch SHA, model/catalog pins and reserved cap.
- The transaction **fully exits and commits** before the method returns; no paid/network code is imported or called. Response says `intent_committed=True`, `outcome=unknown_until_authenticated_provider_receipt`, `automatic_retry_allowed=False`, `dispatch_allowed=False`, `paid_calls=0`. Even if zero provider bytes were sent, losing the response or restarting does **NOT** authorize a second request.
- `read_committed_live_sre_opus_intent` queries the persisted witness on a new idle autocommit-enabled connection (same authorisation boundary as a host service). It returns redacted cost/hash metadata only, never the D1 raw source, Opus prompt, payment credentials or a send-authorization token.

**Important limitation:** The current M15 lease/recovery design freezes a whole LIVE run after any possible POST, so M26 intentionally creates **at most one request-level dispatch intent per run**. This is a safe single-request fencing milestone, *not* a fully executable all-leaf Opus batch. M27–M29 must add transactionally safe multi-request sequencing and authenticated receipt reconciliation, without relaxing unknown-paid fencing or silently skipping other planned M20 requests. No claim of end-to-end paid SRE completion is made.

## PostgreSQL migration 061

Forward-only `intelligence/migrations/061_sre_live_opus_dispatch_intents.sql` adds `sre_live_opus_dispatch_intents` and supporting composite uniqueness indexes on original M15/M25 authority rows. PostgreSQL FKs require the **exact** project/run/item, immutable M23 quote and M24 consent, reserved M25 funds, current M15 attempt token/generation/request and M13 batch membership. The INSERT trigger uses PostgreSQL real clock, checks worker/run cancellation+lease, M15 intent, consent revocation/expiry, quote/budget amount and a previous M26 intent; immutable UPDATE and DELETE are forbidden, and PUBLIC grants are revoked. A direct SQL write with plausible UUIDs alone cannot fake a complete current tuple.

Privacy removal while a possibly paid charge is outstanding **fails closed**. Future M29 audited reconciliation and retention policy must explicitly coordinate a truly safe purge. History 016/017 and all earlier SRE migrations are not rewritten at M26.

## QA evidence source

- `tests/test_sre_opus_dispatch_intent.py`: wrong/truthy operator authorization, bool/negative request ordinal, stale/cancelled worker, nested transaction and autocommit-off fail closed, no provider/HTTP imports.
- `tests/test_sre_opus_dispatch_intent_schema.py`: immutable composite M15/M24/M25 and all-hypothesis SHA boundaries, exact Decimal columns, one prior-intent fence, next migration prefix 062.
- `tests/test_sre_offline_pg.py::test_m26_pg_committed_single_request_intent_crash_no_retry`: real disposable PG17 two-human-approved C9 + D1, M12 LIVE, M13 batch membership, M14 claim, M23 quote, M24 consent, M25 reserve, single M15+M26 commit, **independent connection can read committed record**, direct SQL tamper denied, nested transaction and invalid ordinal denied with zero witness, replay fail closed, cancel => UNKNOWN and original reserve retained without provider or classification writes.
- All new/earlier unit and PG migration upgrade suites wired into `.github/workflows/sre-contracts.yml` Linux/Windows/Python3.12/PG17; 061 is required by M05 migration preflight. **No test is claimed successful merely for existing in the branch.** GitHub queued/cancelled/failed conclusions do not count as PASS.

**Next M27:** exact OpenRouter transport with default-off network, zero-cost injectable fake transport, fresh authenticated pre-send token/account checks and separate persistence/one-shot gate before the actual POST. M28/M29 must settle receipt and UNKNOWN outcomes. None is delivered by M26. Real M16–M18 ONNX acceptance remains BLOCKED; PR #22 stays Draft and `main`/other feature branches remain untouched.
