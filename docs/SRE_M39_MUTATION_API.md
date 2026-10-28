# SRE M39 — localhost write API, owner/CSRF/consent gates and crash-safe replay

**2026-10-28, branch `feature/sre-production-v1-20261023`, Draft PR #22. SOURCE CODE [x]; exact-head Linux/Windows/PG17 QA [ ] UNVERIFIED; operator-deployed browser/host integration [ ] BLOCKED.** Only SRE branch changed. No live paid Opus POST, no worker automatic start, no main/QDP/Collector edits.

## Surface, nonnegotiable host security

`intelligence/relevance/http_mutation_api.py` adds `LocalSREMutationAPI`. **Default `enabled=False`**, no listener or provider calls on import. Only explicit `create_mutation_loopback_server(app, port)` may bind **127.0.0.1**, returning a stopped stdlib WSGI server. M38 GET/HEAD routes optionally delegate to an independently enabled `LocalSREReadAPI`; no reads execute writes. Writes are **POST-only**, and only on `/api/sre/v1/projects/{32-lowerhex-project-id}/mutations/{operation}`.

No public / zero-trust HTTP auth is provided by this module. A trusted host MUST supply and actively enforce all of:

1. `actor_resolver(environ)` — already verified local OS/session actor, not user-provided `HTTP_X_ACTOR_ID`, query parameter or arbitrary unauthenticated cookie. Use safe session middleware, not the example placeholder callback.
2. `actor_authorizer(pid,actor,action)` — independent exact-project permission for `api_mutate_sre_<operation>`. Underlying M24 and M32 recheck their own more specific permissions.
3. `csrf_verifier(environ,actor,origin,operation,token)` — verifies a **fresh session-bound anti-CSRF token** delivered via `X-CSRF-Token`; a matching text token alone is insufficient. Exact `Origin: http://<Host>`, allowed local Host and loopback peer, optional `Sec-Fetch-Site: same-origin`. Cross-origin, missing Origin, proxy forwarding, opaque/file origins fail closed.
4. `rate_limiter(pid,actor,operation)` — shared/persistent project+actor action-quota, not a process-local counter reset by restart; missing backend or false→HTTP 429 before JSON or DB. Enforce hard per-user/tenant limits and protect `mutations/attach`, `mutations/human_review` and quote rate with stricter quotas.
5. `connect()` — operator-provisioned idle autocommit=True trusted PostgreSQL connection, **never** one from user-supplied URL.
6. `human_presence_verifier(pid,actor,body)` for **both** `grant_consent` and `human_review`: fresh human interaction independently authenticated by host; the echoed confirmation payload and CSRF token alone are not human attestation.
7. `catalog_resolver(pid,actor,operation)` for quotes/consent — **host-only** exact original Opus endpoint/verified catalog snapshot/operator USD cap and expected SHA. No client-supplied key, catalog or model.

```python
from intelligence.relevance.http_mutation_api import (
    LocalSREMutationAPI, create_mutation_loopback_server,
)
from intelligence.relevance.mutation_service import TrustedSREMutationService

# The names below are HOST IMPLEMENTATIONS (not supplied by this module).
service = TrustedSREMutationService(
    connect=trusted_autocommit_connection_factory,
    actor_authorizer=verified_project_permission,
    catalog_resolver=trusted_exact_opus_catalog_and_usd_cap,
    human_presence_verifier=verify_interactive_owner_or_human_reviewer,
)
app = LocalSREMutationAPI(
    service=service,
    actor_resolver=host_verified_session_actor,
    csrf_verifier=host_session_bound_csrf_check,
    rate_limiter=host_shared_project_actor_action_quota,
    enabled=False,  # leave OFF until fresh PG17/auth QA and operator approval
)
# With explicit host/operator opt-in, separately construct enabled=True.
# server = create_mutation_loopback_server(app, 8765)
# server.serve_forever()  # explicitly operated, never implicit
```

Transport requirements: `Content-Type: application/json` (or charset UTF-8), positive `Content-Length <= 32768`, strict UTF-8 JSON **object** rejecting duplicate keys, NaN/Infinity and unexpected fields, exact `Idempotency-Key` length 12..120 ASCII alphanumeric/period/underscore/hyphen, plus verified X-CSRF-Token. No JSON `api_key`, `generation_id`, `provider_response`, `model_id`, `dispatch_paid` or `reserve_budget` accepted.

## Mutation operations

| Operation suffix | Strict POST JSON body | Actual authoritative internal service | Safety |
|---|---|---|---|
| `attach` | `{"discovery_ids":[1,2]}` (1..50 sorted unique real D1 BIGINTs) | M07 `attach_discovery` inside outer atomic batch txn | Operator-selected only; source rights rechecked; never global scan; no partial batch commit |
| `create_run` | `{"publication_id":"UUID","content_version_ids":[1],"pipeline_config_id":1,"idempotency_key":"same-HTTP-Idempotency-Key"}` | M11 `create_live_run` | Genuine approved C9 + D1 source, unapproved `pending_consent` plan only, no scheduling or billing |
| `issue_quote` | `{"run_id":"UUID","item_id":1}` | M23 `issue_live_opus_quote` with host catalog/account cap | Original exact M20 preflight and immutable quote only, no consent/reserve |
| `grant_consent` | `{"run_id":"UUID","item_id":1,"quote_sha256":"64hex","explicit_confirmation":{...}}` | M24 `grant_live_sre_opus_consent` with host human-presence and strict quote SHA/USD/stage echo | Dedicated original quote, live catalog/price/expiry/current rights checked; never authorizes paid send by itself |
| `revoke_consent` | `{"run_id":"UUID","item_id":1,"quote_sha256":"64hex","reason":"user_revoked"}` | M24 `revoke_live_sre_opus_consent` | One-way, irreversible, no rescue of revoked consent |
| `cancel_run` | `{"run_id":"UUID"}` | M15 `cancel_live_run` | Fail-stop UNKNOWN after potential paid dispatch, no automatic retries/refund |
| `human_review` | `{"run_id":"UUID","item_id":1,"category_key":"UUID","human_decision":"match","reason_code":"verified_model_decision","expected_prior_review_id":null,"explicit_confirmation":{...}}` | M32 `record_live_sre_human_review` with human-presence check | Original M30 receipt and source rights, optimistic predecessor, immutable review, exact explicit action/quote/C9 decision echo |
| `reconcile_unknown` | `{"run_id":"UUID","item_id":1,"intent_id":"UUID"}` | M29 `reconcile_live_sre_opus_outcome` with **generation_id=None, api_key=None, provider_response=None** | **UNKNOWN audit only**, no payment provider GET/POST, no retry, no fabricated zero charge |

Domain functions retain their project lock/transaction, state machine, permissions and source-proof logic. No migration or route can authorize money on its own. The request key is NOT a spending consent, session secret or signature.

## Durable idempotency, crash behavior

New **additive 071** `intelligence/migrations/071_sre_mutation_request_receipts.sql` creates `sre_m39_mutation_receipts`: key `(project_id,actor_id,operation,request_key)`, canonical body SHA, `pending` or immutable `committed`, minimal bound receipt only, SQL transition guard, no PUBLIC access. This is a **separate** M39 ledger and is not the M30 provider receipt or quote ledger.

The trusted service executes:

1. Authenticate tenant/action, verify strict payload and human/catalog requirements; compute canonical project+actor+operation+body SHA.
2. In the **first committed PostgreSQL transaction**, INSERT `pending`. An existing matching `committed` key gives a minimal *historical* receipt, labelled `previously_committed_not_current_authorization` (never claims current consent/rights). Same key with modified body gives 409 `idempotency_conflict`; matching pending gives 409 `mutation_in_doubt`.
3. Execute exactly one owned service operation (selected batch inside one nested transaction). Existing source services use their own PostgreSQL transactions on an idle autocommit connection.
4. Persist a bounded 4096-byte, metadata-only `committed` result in a second committed transaction; return sanitized operation receipt.

**Crash gap is intentional fail-stop**: if actual service commit happened but the final M39 ACK record did not, the original ledger remains `pending`. Retrying that key must NEVER re-execute, re-consent, repeat a human decision, repost or infer no charge. The operator must independently inspect original C9/D1/SRE domain facts, repair/reconcile the request using a separately audited manual procedure. There is **no automatic lease expiration or automatic replay of pending mutations**. A new key represents a new explicit action and still passes all underlying domain constraints. Future M41 may provide an operator-only recovery report; this stage does not implement unsafe auto-recovery.

**Important distinction:** this is duplicate-effect prevention with explicit uncertain outcomes, not a claim of automatic exactly-once completion across a failed response boundary. It prevents automatic re-execution without inventing a success.

## QA, integration and blockers

Authored SRE-owned tests:
- `test_sre_mutation_service.py`: existing immutable source ports, idempotency SHA, collision, future retry after crash, no paid switches, current actor/tenant checks, strict D1 IDs, host human/catalog presence, atomic attach batch.
- `test_sre_http_mutation_api.py`: forged actor/project header, cross-origin, CSRF, rejected forwarded IP/remote host, rate flood 429, body length/strict JSON duplicate keys, GET/HEAD delegated to read-only M38, POST-only, exception/DSN redaction.
- `test_sre_mutation_schema.py`: forward-only 071, required migration guard, immutable pending/committed transition, next 072.
- `migration_guard.py` + M05/M36 expected-next tests updated to 071 required / next `072`; Linux/Windows SRE workflow extended to M39.

**Tests authored, exact-head results not verified as PASS.** Disposable PG17 comprehensive negative/positive security and actual host middleware/browser test is required to accept M39. The pre-existing corrupted SQL `062_sre_live_opus_receipts.sql` **blocks clean PG17 installation**; do not silently rewrite historical applied migration hashes. Other remaining source/production blockers: M16–M18 real ONNX, M25/M26 authenticated paid multi-leaf run, M34 retention/physical purge, M35 actual Collector Stage10, M36/M37 QDP event/storage mismatch, M42–M47 merge/release. All paid POST calls issued by M39: **0**.

**Global code-implementation progress:** M01–M39 out of M47 = **39/47 ≈ 83%** code milestones, **NOT** fully-tested production readiness. **Next source phase M40 — standalone Studio frontend**; the authenticated host and PG17 installation blockers remain.
