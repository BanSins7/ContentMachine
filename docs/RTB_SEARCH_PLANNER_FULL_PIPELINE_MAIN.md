# RTB Deep V2 → Search Planner → QV → Tasks (main integration)

Implementation: additive SQL migration `060_sp_rtb_deep_v2_pipeline.sql`.
The old, approved Deep C9 publication/plan/quote/consent path remains unchanged.

## How to run locally
1. Apply all repository migrations in filename order to PostgreSQL 17, on
   a disposable copy first. Never edit an already-applied SQL migration.
2. Set `CM_RTB_REVIEWER_ID` to a server-configured reviewer that is
   genuinely authorized for the project in
   `knowledge_fact_review_authorizations`; do not accept reviewer identities
   from browsers. For SP mutating requests set high-entropy
   `CM_SP_OWNER_TOKEN`. Paid execution remains OFF unless explicitly enabled.
3. Publish the RTB Deep V2 run after BOTH human reviews, verified B9 and
   original A readiness gates. The V2 export requires publication
   `published`, current SQL ACL, source replay and matching SHA.
4. In Search Planner choose `RTB Deep V2` under `01 · C9`. Generate
   a read-only Context preview. All fourteen node fields, full approved B9
   fact metadata and referenced original A observations are retained.
5. Register a pinned OpenRouter config, then `Create Generation $0`.
   PostgreSQL stores `rtb_v2_publication_id`, V2 export SHA and immutable
   V2 contexts/batches. No legacy fake C9 publication is inserted.
6. The existing quote, human owner consent, exact pinned provider request,
   ledger, uncertain-attempt reconciliation and QV mechanisms are reused.
   They are NOT bypassed or automatically enabled. Configure real model
   snapshots and embedding models before expecting execution.
7. For Tasks the verified QV reader independently re-opens the current RTB
   V2 export, compares the pinned SHA and all saved context hashes. The
   Collector adapter still requires its real durable inbox receipt.

## Fail-closed rules
- No implicit 14→11 production conversion, no project/source ACL bypass.
- A V2 generation has NULL legacy `publication_id` and `rtb_run_id` and
  real non-NULL `rtb_v2_publication_id`/export checksum. The exact inverse
  applies to legacy V1. PostgreSQL triggers independently check V2 publication
  status, both reviews, quote and consent identities.
- No paid provider call is implied by planning, quotation or this integration.
- Scope of change: SP consumer and shared SQL entrypoint only. RTB upstream
  publication/reviewer logic is left immutable.

## Release evidence still required
- Run Python unit/integration tests and JS checks on your local machine.
- PostgreSQL 17 fresh-install and previous-main upgrade with migrations;
  run real V1/V2 project isolation, human-review rejection, idempotent
  generation, quote/consent, paid preflight and stop-on-revocation tests.
- Verify a genuine published V2 → paid SQG → completed QV → held tasks →
  Collector ACK using real source and reviewer permissions.
- No claim of passing paid E2E or external source readiness is made here.
