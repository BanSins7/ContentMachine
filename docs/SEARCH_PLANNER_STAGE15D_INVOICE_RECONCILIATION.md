# SP-15D — trusted provider invoice reconciliation boundary (code)

**2026-10-27. Implementation committed to \`feature/search-planner-full-integration\`, NOT to main.** The local code and contract tests are present; current exact-HEAD GitHub Actions and real PostgreSQL17 billing integration are **not yet verified**.

## Financial safety requirement

An SP paid HTTP dispatch writes \`dispatch_intent\` before the request. When the response/worker crashes, it becomes \`uncertain\`. It is **incorrect** to assume that the bill is USD 0, that the reservation is free for retry, or that the missing LLM answer was successfully processed.

This change implements an **opt-in operator decision gate** and a strictly typed **trusted provider invoice verification port**. No OpenRouter billing-account adapter is available by default, so **no production uncertain reserve is reconciled automatically**. A screenshot, manual input, source hash, request digest, estimate, or arbitrary injected HTTP JSON alone is *not* sufficient proof.

## Added module and migration

- \`intelligence/migrations/058_sp_invoice_reconciliations.sql\`: additive and immutable audit log \`sp_invoice_reconciliations\`. Foreign key to the project/generation, unique attempt and unique project+invoice line; fixed provider \`openrouter\`, fixed owner action and decision, pinned request hash/provider response ID, invoice line hash/account fingerprint/verified timestamp/exact charged USD. The existing SQG ledger remains authoritative; migration never changes an old SQG table or refunds funds.
- \`intelligence/search_planner/invoice_reconciliation.py\`:
  - \`list_uncertain_invoices\`: read-only project-scoped operator worklist with reserved USD and **presence** of response ID (not raw response ID or keys). \`invoice_verified=False\` unless independently proven.
  - \`read_uncertain_attempt\`: project+generation scoped, read-only attempt identity. A missing durable provider response ID **fails closed**; the SHA of a request is not an OpenRouter invoice lookup key.
  - \`InvoiceReconciliationController.reconcile\`: explicit server-side verified owner action \`search_planner.generation.reconcile_invoice\`. With no injected \`VerifiedInvoicePort\`, returns 503 without mutating PostgreSQL. No public invoice-submission HTTP route is exposed.
  - \`validate_verified_invoice\`: strict receipt mapping to exact project, generation, attempt, request SHA, persisted provider response ID, billed invoice line, verified source/time and Decimal USD <= held reservation. Incomplete/partial evidence or overbilling => manual investigation, no silent clamping.
  - \`apply_verified_invoice\`: one transaction using SQG generation->consent->batch->attempt lock order, re-check frozen identity and remaining consent, immutable evidence INSERT, mark ambiguous attempt \`reconciled_failed\`, update reserved/spent Decimal ledger and SQG audit event. Duplicate exact settlement is read-only idempotent; different evidence fails. Never creates \`sqg_candidates\`, \`sqg_outbox\`, search tasks or a fake successful model output.
- \`SearchPlannerService\` exposes \`uncertain_invoice_worklist\` and \`reconcile_verified_invoice\`, with the latter default-denying all callers until explicitly assembled with **all three** trusted owner guard, real provider-verification adapter, and preconfigured billing account hash.
- \`tests/test_search_planner_stage15_invoice.py\`: 20 offline regression tests for immutability, owner gate, missing adapter, wrong project/request/response, amount precision, zero evidence rule, cancellation races/ledger insufficiency via mocks, transaction write shape, no false output and exact idempotency.
- Focused GitHub Actions Linux/Windows include compile+unit suites.

## Invariants for real provider adapter (NOT yet implemented)

A production OpenRouter adapter must independently authenticate to the **same billing account** and read an authoritative *final per-request* invoice/usage line. The trusted port must establish:

1. Exact match to the SP persisted \`provider_response_id\`, request identity, project/generation/attempt lineage, and a **separately preconfigured trusted billing account SHA-256**; a fingerprint supplied by the same invoice payload is not enough.
2. Charge is posted/final and belongs uniquely to one invoice line. The invoice-line ID cannot pay off more than one attempt; PostgreSQL enforces the uniqueness.
3. \`charge_usd=0\` is acceptable **only** with an explicit final per-request zero-charge record from the provider. A missing invoice, request mismatch, delayed statement, incomplete export, timeout or ambiguous response **must remain uncertain**.
4. No inferred charge from token counts, a quote, log message, screenshot or guessed fee.
5. Any bill exceeding the held reservation or consent balance must be manually escalated with full amount preserved externally. The adapter must never round, clamp or quietly authorise more spend.
6. The real owner must confirm the settlement action independently of the original paid run consent. The caller must not control the injected owner guard or verifier.
7. The production verifier's own authentication/provenance must be independently audited; a SHA-256 hash alone is a fingerprint, **not** cryptographic proof of OpenRouter origin.

If OpenRouter does not expose a trustworthy per-request final invoice API or the process crash occurs before a provider response ID can be stored, **this module intentionally cannot settle the attempt**. It stays uncertain for verified manual dispute handling, not automatic refund or retry. A provider-side idempotency key/correlation protocol would have to be proven separately, not invented here.

## Important accounting semantics

An unknown or paid HTTP result with missing output is **not reconstructed** from the invoice. Upon a successful, positively evidenced resolution, status is \`reconciled_failed\`, and the batch becomes \`failed_known\`, even if provider billing suggests work completed remotely. There is no publication or QV output. Multiple unresolved attempts keep generation \`uncertain\`; otherwise the generation fails (or remains cancelled if owner cancellation was requested). This is intentionally conservative.

Real PostgreSQL 17 concurrency, WAL recovery, invoice database constraints and deployed provider adapter live tests remain SP-16 checks. No real OpenRouter billing calls were made or proven here.

## What remains of SP-15D

- Real, independently audited and credential-safe provider billing verifier, if OpenRouter exposes the necessary unique final per-request records. Until then it is unavailable by design.
- Real Collector capabilities source/policy/price/quota/credential health publisher and independently attested delivery receipts, from the dedicated Collector module. No SP-created fake readiness or accepted receipt.
- Operator service manager / alert integration and Windows/Linux PG17 live process crash/race tests.
- Full project-scoped authenticated HTTP API audits and release gates. **Do not merge PR #34 merely because offline tests were added.**
