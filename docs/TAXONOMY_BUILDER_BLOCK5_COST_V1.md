> **Актуализация 22.10.2026:** этот документ фиксирует состояние отдельного этапа разработки, а не текущий статус ветки. PR #7 (Taxonomy Builder) уже **merged** в `main` 21.10.2026, PR #8 добавил workspace UI, PR #9 — иерархический двухпроходный режим. Утверждения ниже о draft PR #7 и отсутствии последующих функций — **исторические**. Актуальное состояние: [CURRENT_STATE_2026-10-22.md](CURRENT_STATE_2026-10-22.md).

---

# Taxonomy Builder — Block 5: cost forecast and explicit spending consent

Status: Block 5 implementation in draft PR #7, **no real LLM execution**. Parent: Block 1 architectural plan and Blocks 2–4 intake and configurable Pipeline backend/UI.

## User path

1. Confirm the Health & Fitness project questionnaire.
2. Select models and their independent reasoning/temperature/output settings in Taxonomy Pipeline Studio.
3. Save and **approve** this exact immutable Pipeline revision.
4. Cost screen automatically opens, displays **low, expected and high** scenarios and a detailed breakdown for every enabled stage including optional Critic.
5. Either choose **«Изменить Pipeline»**, which returns to the Studio; or explicitly set a maximum authorized dollar amount and acknowledge uncertainty and excluded research documents, then press **«Разрешить бюджет»**.
6. Budget consent is recorded, but **nothing is charged** and no Builder is started. Execution and actual spending enforcement belong to Block 6.
7. User can revoke authorization or explicitly regenerate a new estimate before granting different authorization.

No estimate, approval or consent secretly invokes completions or creates a running job. The launch API does not exist in this block.

## Pricing source, units and uncertainty

OpenRouter model listing returns \`pricing.prompt\` and \`pricing.completion\` in **USD per token** (not dollars per million). The \`pricing.request\` field is dollars per request. If a distinct nonzero \`pricing.internal_reasoning\` rate is present, the estimator adds it conservatively per projected billable output token (which may overestimate). Displayed values are converted to per-million for readability. The source for every pinned model is the public \`https://openrouter.ai/api/v1/models\`, and the quote preserves the model capability/pricing snapshot hashes.

Official reference: https://openrouter.ai/docs/api/api-reference/models/get-models

The estimator **does not use a model tokenizer or exact future prompts yet**; it uses:
- Actual UTF-8 byte size of the project's saved questionnaire.
- Scenario heuristic \`25% / 55% / 90%\` of the questionnaire bytes as estimated tokens, plus fixed prompt allowances of \`900 / 1600 / 1100\` input tokens for Fact Extraction / Taxonomy Generation / Critic.
- An expected downstream input allowance from the previous LLM's projected output.
- Output scenarios \`15% / 45% / 100%\` of each stage's configured \`max_output_tokens\`.
- No extra retries; no scraping; no external document parsing, embeddings or image inputs; no credit-purchase fees.
- Optional Critic is included only when enabled.

The three scenarios express uncertainty: **even the high scenario is NOT a mathematical upper bound**. The actual number of input tokens, reasoning tokens, future user-approved facts, provider routing/pricing changes and retries can differ. \`max_authorized_spend\` is a user-intent limit, not proof that a future worker enforces it. A trustworthy runner must reserve/check worst-case cost before each paid request and stop when it cannot enforce the limit (Block 6).

**Uploaded research documents are NOT included** in the Block 5 estimate or approved execution scope. Their count and bytes are displayed. An explicit acknowledgement is required when they exist. Supporting them later will require a fresh estimate and consent. Conditional/long-context \`pricing.overrides\`, unsupported provider-specific discounts and unknown base input/output rates fail closed: no made-up dollar estimate.

## Data & security guarantees

Additive PostgreSQL migration \`006_tb_cost_quotes_and_consents.sql\`:
- \`tb_cost_quotes\` immutably stores exact project, immutable approved Pipeline/config hash, confirmed profile revision/hash, input hash, pinned pricing hash, estimator version, source assumptions, USD totals, creation/expiry.
- \`tb_spending_consents\` is a separate immutable approval of one exact quote with explicit user-defined max USD (0 to 10000).
- \`tb_spending_revocations\` is an immutable audit event. A revoked consent cannot be silently reused; a new quote is needed.
- The previous quote is never applied to a newly edited/unapproved Pipeline. Any change of profile, config, model snapshot, pricing or expiry invalidates authorization.
- Quotes expire after 30 minutes. Catalog metadata must be fresh. Unknown model prices/tiered conditions prevent consent.

GET /api/projects/{id}/taxonomy-builder/cost
Returns current server-derived state, last quote (if any), current consent (if valid), expiry/stale reason and \`launch_available:false\`.

POST /api/projects/{id}/taxonomy-builder/cost/quote
Body: \`{"pipeline_id":"UUID","force_new":false}\`. Calculates/reuses a valid quote tied to the exact approved active Pipeline; \`force_new:true\` makes a new quote for revised consent. No LLM calls.

POST /api/projects/{id}/taxonomy-builder/cost/consent
Body: \`{"quote_id":"UUID","authorized_usd":"0.50","acknowledge_documents_excluded":true}\`. Server checks current approved Pipeline, source hash, fresh pricing, unexpired and most recent quote, budget >= estimated high scenario, and optional document acknowledgement. It does not execute any spend.

POST /api/projects/{id}/taxonomy-builder/cost/revoke
Body: \`{"consent_id":"UUID"}\`. Idempotently revokes future consent for the same project only.

Existing app security (localhost bound, origin/host checks and mutation header) is maintained; remote/multi-user authorization is not implemented. No API tokens reach the browser. No actual card/billing API is touched.

## Code

- \`intelligence/taxonomy_builder/cost_estimator.py\`: Decimal-only arithmetic, per-token source rates, fail-closed pricing and scenario assumptions.
- \`intelligence/taxonomy_builder/cost_repository.py\`: version and freshness checks, immutable PostgreSQL quotes/consents and revocations, stale quote rejection and project-scoped isolation.
- \`site/taxonomy-cost.js\`: accessible cost screen controller, scope disclosure, explicit budget selection, revoke/return interactions.
- \`site/taxonomy-studio.js\` and \`site/projects.js\`: approved Pipeline → cost handoff and return to editor.
- \`site/index.html\`, \`site/style.css\`, \`app.py\`: UI and HTTP routes.
- \`tests/test_taxonomy_builder_cost.py\`, \`tests/test_taxonomy_builder_cost_db.py\`, \`tests/test_taxonomy_cost_ui.cjs\`: unit, real PostgreSQL and browser-controller regression tests.
- \`.github/workflows/tests.yml\`: Node syntax/UI test added to the existing Python/PostgreSQL/Windows checks.

## Acceptance and remaining

- Valid current approved Pipeline → estimate per model and human-review-aware assumptions; can accept, return or refuse.
- Changing Pipeline/price/profile or expiry prevents stale authorization.
- Uploaded docs must be explicitly acknowledged as excluded; still not processed.
- Revoking consent blocks its reuse; no paid model request is possible in Block 5.
- Price unknown, conditional rate, provider unavailable or unsupported selected model cause an actionable error, not an invented estimate.

**Block 6** must deliver source document handling or explicit text-only scope, a queue/runner with spending reservations/accounting, actual OpenRouter calls, human fact/topic gates and publish. **Block 7** will run end-to-end tests.

Stop after Block 5; wait for user permission before further work.
