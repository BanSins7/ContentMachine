# PIE-MOD-29 — OpenRouter LLM Adapter and Spend Governance v1

**Branch:** feature/patterns-insights-full-module; never edit main/UAE/QDP/Group A/B/C/SRE.  
**Status:** candidate-only model/financial control implementation. No live paid request or authenticated host approval has occurred.  
**Module:** intelligence/patterns_insights/research/model_governance.py  
**Migration:** intelligence/migrations/203_pie_model_dispatch_claims.sql (additive). Original migrations 200/201/202 remain byte-identical.

## Approved request boundary

Stage28 produces **non-dispatchable, candidate-only**, independently replay-sealed structured prompt messages. Stage29 can prepare a *candidate* price quote without sending data to a model. A separate host-supplied SpendAuthority must independently check **current** project membership/approver identity, explicit consent and the relevant authorization_id. Inserting an approval_id in Python is **not** consent. None of the test fake authorities is a real app authorization provider.

The only network transport is an explicitly configured OpenRouterHTTPS object; no network, env var read or paid operation occurs during module import, quote computation or test fixture generation. The only runtime credential lookup is OPENROUTER_API_KEY (or an explicitly injected key) at the exact HTTP operation. The fixed HTTPS host is openrouter.ai, with certificates verified, redirects disabled and no automatic retries; timeout is bounded to 1–60 seconds. An OpenRouterTransport Protocol can be replaced by a fake in tests. No model fallback list is supplied and provider.allow_fallbacks=false; provider.require_parameters=true and provider.data_collection=deny. Streaming, tools and web-search plugins are not enabled. Schema-constrained JSON is required with strict=true. Model selection is configurable by explicit exact author/slug, not a pinned default or openrouter/auto.

Official API references verified for 2026:
- https://openrouter.ai/docs/api/api-reference/models/get-model (GET /api/v1/model/:author/:slug)
- https://openrouter.ai/docs/api/api-reference/chat/send-chat-completion-request
- https://openrouter.ai/docs/api/api-reference/models/get-models
- https://openrouter.ai/blog/insights/model-routing/ (require_parameters, provider fallbacks and data_collection)
- https://openrouter.ai/support (usage.include=true, billing cost)
The provider catalog and model prices are fetched afresh by the caller; this code does **not** fabricate a current named model price or assert real remote verification based on synthetic test metadata.

## Catalog verification and conservative quote

verify_model_catalog reads an expected single-model catalog data object, requires exact model ID and canonical slug, text input/output modalities, a context ceiling, a top-provider maximum completion token count, support for response_format AND max_tokens, and finite nonnegative input, output and per-request USD rates. Unknown paid images/audio/web_search/internal_reasoning rates block this text-only stage. OpenRouterModelSnapshot identifies the exact capabilities/prices via canonical SHA and records the observation timestamp separately. Different observation times of *identical prices/parameters* produce the same catalog fingerprint; price or capability changes produce a new fingerprint.

model_quote requires a **sealed stage28 prompt**, a catalog entry observed within 2 minutes, a bounded completion token cap and enough model context. The input estimate is a conservative UTF-8 byte upper bound for role-separated messages plus JSON schema + framing. The reserve estimate is the ceiling-rounded Decimal sum:

    reserve_usd = ceil_12(
       assumed_prompt_tokens * catalog.prompt_price_per_token +
       requested_output_tokens * catalog.completion_price_per_token +
       catalog.request_price_usd
    )

This is a *conservative planning estimate*, **not a cryptographic proof of final provider billing**: provider tokenizers, routing prices and any unanticipated ancillary services may differ. Actual reported cost exceeding reservation is treated as uncertain; it is NEVER silently accepted as within budget. Both max prompt/output counts and source SHA/model/catalog identity are carried in a canonical quote SHA and request SHA. Output is bounded and the quote must be positive and below $50 per attempt. All monetary calculations use Decimal, not floating-point accounting. Recomputing a quote with the same model/prices and source yields the same estimated spend; changed prices fail before dispatch.

## Explicit reservation and immutable pre-send claim

The existing migration200 tables are reused:

- pie_spend_authorizations: explicit approved_by, project scope, USD budget and expiration. Host SpendAuthority must additionally verify user and current consent.
- pie_model_attempts: immutable project authorization reference, unique project+request SHA, reserved_usd. Its existing database INSERT trigger locks authorization and rejects expired/over-budget attempts, including concurrent reservations.
- pie_model_attempt_events: immutable reserved/charged/uncertain sequence; event receipts never store keys or prompt text.

reserve_model_attempt checks fresh SpendAuthority, inserts one immutable reservation and one reserved ledger event in a single PostgreSQL transaction. Same project + request SHA cannot create multiple attempts. No automatic release of reservation occurs on error or uncertain charge.

New additive migration203 introduces:
- pie_model_dispatch_claims: exactly one immutable row per project+attempt, matching existing authorization, request, catalog/prompt/quote fingerprint and hash of request JSON. It is inserted and COMMITTED **before** any potentially billable HTTP POST; a duplicate attempt cannot be re-sent.
- pie_model_dispatch_outcomes: exactly one immutable outcome row per attempted POST. Only reported_charged (with reported USD, tokens, result SHA) or uncertain_charge (no verified cost/result) are permitted. Direct UPDATE is rejected by the standard PIE immutable evidence trigger.

The dispatch flow refreshes the live model catalog read-only; rejects price/capability changes; calls stage26 for fresh current project/source/row rights and stage27 for exact source revocation; independently recalculates the approved quote; asks SpendAuthority for a **second fresh authorization**. In the same database transaction as a new pre-send claim, it obtains the PIE project advisory lock and checks the stage27 tombstone again, preventing a source revocation race up to claim commit. It checks reservation ownership and value against the database before creating the irreversible claim. Upon commit, it executes **at most one** HTTP request for the attempt.

If an HTTP timeout, transport exception, partial/invalid response, unexpected model, excess tokens/cost, missing usage.cost, unsupported citations or invalid stage28 strict JSON output occurs, the attempt enters an immutable uncertain_charge outcome. Even when a response is returned with cost, reported_charged is **provider-reported**, not independently settled final payment. The full reservation remains held until an independent billing reconciliation; no blind retry is possible. If persisting the outcome fails after the network call, the already committed claim blocks repeat sending and the caller receives model_outcome_persistence_unknown for manual audit. No secrets, private provider errors or source media are included in receipts.

This is not automatic budget replenishment, subscription management, manual billing reconciliation, actual payment initiation, approval by the user, worker leasing or a fully authenticated product API. Real host-proven approver and fresh UAE/project/rights connectors remain missing, so the code must not be enabled in production by passing a test stub that returns True.

## Deterministic synthetic and cost oracles

tests/test_pie_model_governance.py contains **27 authored unittest methods** (synthetic fakes + opt-in disposable PostgreSQL17):

- exact author/slug, text-only model, response_format and max_tokens; reject openrouter/auto, aliases, missing capability, float/negative/NaN rates or unknown paid auxiliary modalities;
- stable model capability SHA across identical re-fetches and modified SHA after any price change;
- independent Decimal hand calculation from exact input bound, two per-token rates and per-request fee, rounded up to 12 decimal places;
- prompt/catalog expiry, context limit, output token ceiling and provider routing require_parameters/allow_fallbacks controls;
- mandatory independent SpendAuthority, no SQL before authorization, duplicate immutable reservation;
- one successful fake response with exact source JSON and cost (USD 0.00012 in test), two immutable events; reported_charged is explicitly not bank settlement;
- fake timeout, missing usage or incomplete result, forged source citations, different model, quoted cost overrun => uncertain charge, no request replay;
- new current model pricing and expired/altered authorization or changed prompt => no HTTP;
- mocked fixed-host HTTPS GET URL, no real provider request and no hardcoded key;
- optional PG17 migration/upgrade, real DB budget guard and duplicate request, project-scoped foreign-key isolation and direct UPDATE immutability of claims and outcomes (requires explicit disposable CM_TEST_PG_DSN).

All affirmative source rights, current spend authorization, stage27 preflight and OpenRouter catalog/response calls are **MOCKED**. Test-only model_slug openai/gpt-test is synthetic and MUST NOT be represented as a real available model; no model was selected and no paid usage occurred.

## QA, user controls and next stage

Run in a full local checkout:

    python -m unittest discover -s tests -p 'test_pie_model_governance.py' -q
    python -m unittest discover -s tests -p 'test_pie_*.py' -q
    python -m compileall -q intelligence/patterns_insights tests/test_pie_model_governance.py

The optional real DB cases SKIP unless CM_TEST_PG_DSN references an explicitly disposable PostgreSQL17 database whose name includes test. No production database, paid OpenRouter request or API key is needed for QA. These files are committed via GitHub connector; **local unit test execution, compileall, disposable PG17 fresh/upgrade, concurrency stress, real UAE/rights, authenticated approval and passing GitHub CI have NOT BEEN OBSERVED**. Mark stage29 IMPLEMENTED with testing/integration NOT VERIFIED, never green or production ready.

NEXT PIE-MOD-30: a fully supervised Insight Generation Engine must reconcile claimed usage/billing and integrate strict stage28 drafts without treating any candidate as supported/publishable.
