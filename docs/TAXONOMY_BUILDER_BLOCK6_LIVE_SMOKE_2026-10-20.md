# Taxonomy Builder — one real OpenRouter smoke test (2026-10-20)

**Evidence:** GitHub Actions [run 39402262474](https://github.com/BanSins7/ContentMachine/actions/runs/39402262474) — completed with `success`. The sanitized Actions artifact is `one-time-taxonomy-real-usage`.

- User-authorized *aggregate* live-test ceiling: **$0.60 USD**.
- Provider: OpenRouter, authenticated by configured secret `OPENROUTERKEY` (secret contents not logged).
- Selected real model: **`deepseek/deepseek-v4.1-flash`** from the approved model candidates after checking public OpenRouter capabilities and price.
- Actual stage: **Fact Extraction** with a *synthetic* health/fitness questionnaire.
- Source-quote and JSON fact validation: **passed**.
- `usage.prompt_tokens`: **317**.
- `usage.completion_tokens`: **256**.
- Reported reasoning tokens: **256**.
- `usage.cost`: **$0.000048472**.
- Reserved preflight estimate: **$0.00025634**.
- Stricter single-request preflight allowance: **$0.015**.
- Calculated user-test allowance remaining if no unrelated spending occurred: **$0.599951528**.

Only **one** real provider completion was sent; no automatic retry. This result verifies real provider connectivity, cost/usage reporting and synthetically grounded extraction, not the full real-world user-run lifecycle or taxonomy quality.

For this test we committed **only** a small one-time dispatcher to the main branch; it checked out the pinned Block 6 PR commit. After the provider call succeeded, the dispatcher was disabled in a second main-only commit. The rest of the application remains in draft PR #7, not main.

**The first-call reservation is an application-level estimate and cannot contractually limit a provider's eventual bill.** Before any further live tests, inspect actual OpenRouter Activity and agree a separate one-shot plan. Block 7 owns comprehensive E2E, model-quality comparisons, cross-platform interface QA and consolidation with the separate SRE PR #6.
