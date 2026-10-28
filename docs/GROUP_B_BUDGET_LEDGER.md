# GB-MOD-10 — Cost Manager, quote/consent/reservation and immutable ledger

**Branch:** `feature/group-b-full-module`. **Status:** implementation committed, actual CI/PG17 results must be observed. **Schema:** additive `intelligence/migrations/057_group_b_budget_ledger.sql`; existing `005`, `055`, `056` remain unmodified. **Code:** `intelligence/group_b/{budget,ledger}.py`. No real OpenRouter dispatch is enabled; stage09 `OpenRouterProvider.invoke_approved`, `quote`, `OpenRouterTransport.post_json` **all remain blocked**.

## 1. Quotes: immutable, pinned, conservative

`quote_context(packet,catalog,model,request_sha256,output_tokens,prompt_tokens_ceiling,created_at,...)` accepts only a hash-verified `group-b-context/v1` packet, exact verified catalog model slug and catalog SHA256; `prompt_tokens_ceiling` is a *separate conservative full-request* bound at least 2048 above stage08's estimate (provider headers/schema/system instructions must fit), plus an explicitly bounded maximum completion. Unknown model capabilities, stale/tampered context hash, missing output context, malformed/negative/nonfinite prices, weak token bound, cap overflow, or unexpected schema => typed `budget_denied`. Quote TTL must be positive and no longer than **15 minutes**. If a quote has expired in the PostgreSQL clock, it cannot be approved or newly reserved.

The USD ceiling uses finite `Decimal` and rounds **up** to `NUMERIC(20,12)` (never float):

```
reserved ceiling = prompt_tokens_ceiling * prompt USD/token
                 + max_output_tokens * completion USD/token
                 + request USD
```

This supports **text-only, fixed-verified pricing**. OpenRouter image/request/cache units and per-endpoint visual token billing are **not** guessed. **Any positive image reference in a context packet is unquotable** until a verified endpoint/vision pricing scheme is implemented. The fact an OpenRouter catalog reports an image number is not adequate to infer per-image billing. The quote stores catalog/packet/evidence hashes, full limits, price snapshot, creation/expiry and a deterministic `quote_sha256`. It is not a signed provider offer. `quote_context` is read-only and creates **no charge or authorization**.

## 2. Explicit default-deny policy

The new migration creates **separate** project- and run-level `group_b_budget_project_limits` / `group_b_budget_run_limits` rows (`enabled DEFAULT FALSE`), each containing a lifetime USD ceiling. Neither row is implicitly created/enabled by any budget method. The project operator/host must provision limits through an authenticated administrative workflow, not an untrusted payload. `GroupBConfig.enable_paid_execution` is false by default, with zero max attempt USD. A pure quote may be prepared in disabled mode, but **no reservation** is possible unless an explicitly configured runtime permits spending, both DB policies are enabled, an accepted Group B QDP inbox and active pinned run exist, a nonexpired exact quote is saved, and a trusted host has recorded affirmative, project-scoped approval.

`BudgetLedger(connection_factory)` owns a **real psycopg transaction** for each operation; lower-level helpers are for internal already-transacted use. Every quote/approval/attempt is scoped by `project_id,run_id`, with existing composite FKs. `save_quote` writes immutable `group_b_model_quotes` only for accepted active B run and matching pinned media manifest. `approve_quote` requires a nonempty externally authenticated actor ID, `explicit_confirmation=True` from the **trusted host** (an arbitrary HTTP-provided boolean is NOT sufficient proof of consent), matching full quote amount, enabled project/run limits and current quote expiration. Existing `group_b_model_approvals` rows and unique per-quote approval enforce double-click idempotency; changed actor/amount is rejected.

No new B attempt is ever submitted from the budget interface. An approved quote/ledger reservation is a **liability hold**, not provider credit-card authorization or execution grant. Stage11 must implement the actual fresh GB07 media+rights gate, quote/consent verification, provider-specific request idempotency, and separate worker authorization before any HTTP POST.

## 3. Concurrent reservation / spend cap

`reserve_attempt` locks the **project limit first, then the run limit** with PostgreSQL `FOR UPDATE` in one transaction. It counts every attempt as either: settled actual cost, verified failed/no-charge zero, or **full reserved amount** for `reserved`, `submitted` and `uncertain`. It then enforces:

`already_accounted_project + current_quote <= project_lifetime_ceiling` and `already_accounted_run + current_quote <= run_lifetime_ceiling`, plus `GroupBConfig.max_paid_attempt_usd`.

A unique `(project_id,quote_id)` attempt index prevents a quote funding multiple attempts; `(project_id,run_id,request_key)` prevents double-clicks. The same key may return the **same still-reserved attempt ID**, never trigger another request. A replay when the state has left `reserved` is refused, not an instruction to resend. Changed hash/approval/quote under the same key fails. `submitted` or `uncertain` attempts **block all new paid attempts in that run**, even if a different quote/key is proposed.

## 4. Charge lifecycle and reconciliation

Existing mutable `group_b_model_attempts` stores current lifecycle:
`reserved -> submitted -> settled | failed | uncertain`, `uncertain -> settled | failed`. The additive migration installs a trigger rejecting identity mutation, repeated/no-op lifecycle rewrites, illegal transitions, unverified usage on pending states, mismatched provider ID changes, over-reservation settlement and fake positive `failed` charges. Provider request ID uniqueness is enforced per project.

The new **append-only** `group_b_budget_events` records one immutable event per state transition, with project/run/attempt lineage, ordered `event_no`, USD amount, content hash and optional provider request ID. Its UPDATE/DELETE trigger rejects history rewriting.

- `mark_submission(...,provider_request_id)` only persists the supplied trusted worker request ID and a state event; it **does not** call OpenRouter, and does **not** establish that a remote request arrived.
- `mark_ambiguous(...)` preserves the **entire reserved liability** and blocks new attempts. Timeouts/429/network errors must never be interpreted as zero charge.
- `reconcile_attempt(...,authenticated_receipt,receipt_verifier)` requires an explicitly injected trusted verifier, exactly matching immutable local request SHA/provider request ID, provider usage with token counts bounded by the original quote and a `Decimal` charged amount no higher than the reserved ceiling. Without a verifier, it returns `provider_receipt_unverifiable` and keeps the old state. A verified `state='no_charge'` **must have zero charge and zero tokens** before converting to `failed` and freeing the hold; a verified settled receipt records actual cost. Over-budget or inconsistent usage remains unresolved until audited.
- **No production receipt verifier/automated OpenRouter billing integration is provided in GB-MOD-10**; CI uses a mock verifier and fake provider IDs only. Never expose a raw `authenticated_receipt` mapping and a caller-supplied `verify=True` callback as a public HTTP API.

A failed SQL statement / exception inside `BudgetLedger` rolls back state and event changes together. All these actions are **accounting APIs**; they do not change the Stage09 HTTP POST denial.

## 5. Deployment, limits, QA

Migration 057 adds three `group_b_budget_*` tables, two scoped unique indexes on attempts, and row/event lifecycle guards. Deploy it **after** 055 and 056 on disposable staging first. Pre-existing conflicting attempts under the same quote can cause a safe migration failure, requiring manual audit and repair; do not silently delete financial history.

Runtime budget limits are lifetime ceilings; monthly resets, exchange-rate conversion, tax and actual provider-specific vision/caching prices are out of scope. Project administrators must carefully manage updates to `enabled` and policy caps. For an actual billable call, source policy and QDP/GA/video/media must be checked again **at point of use**, provider request replay protection enforced, and the verified post-call receipt reconciled. These are GB11 operational gates.

Tests:
```bash
python -m unittest discover -s tests -p 'test_group_b_budget.py' -v
CM_TEST_PG_DSN=postgresql://.../cm_group_b_test python -m unittest discover -s tests -p 'test_group_b_budget_db.py' -v
```
The first suite is pure Decimal/cap/expiry/image-cost denial/migration static checks. The second uses **real disposable PostgreSQL 17**, concurrent separate connections, double-click/replayed quote, expired quote, missing approval, project/run limit, uncertain submitted charge, verified no-charge, quote/usage exceeding reserve, append-only events and illegal mutability. There is no real HTTP POST, provider expense or completed end-to-end QDP+Group A sample in CI.

**QA truth:** Tests added to Windows/Linux + PG17 pipeline; distinguish **IMPLEMENTED** from **PASS**. Live OpenRouter settlement, cross-branch A/QDP, actor-auth service and real paid worker remain **NOT RUN/BLOCKED**.

**Next:** GB-MOD-11 — actual orchestrator/worker lifecycle, idempotent submission and execution with explicit authorization; do not treat reserved attempts as permission to send.
