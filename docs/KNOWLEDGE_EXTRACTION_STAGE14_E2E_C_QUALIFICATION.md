# Content Machine — Stage14: offline end-to-end B1→B9 qualification and independent C execution

**Scope:** real PostgreSQL17 historical A approvals/B1 bundle/B2 packs/B3 mocked MiMo delivery/B4 normalize/B5 quote ground/B6 duplicate/conflict/B7 Quality Gate/B8 explicit human decisions/B9 immutable KnowledgeSnapshot and **NEW** C taxonomy-generation quote → separate consent → manual start → one-shot provider call → independent human topic review. CI runs synthetic mock provider responses ONLY.

No production auth/HTTP adapter for B8/C is enabled and no real paid MiMo/Opus/Gemini call is performed by tests. Existing RTB `legacy_questionnaire_v1` and `approved_project_context_v1` path is unchanged. The Stage14 C path is a distinct internal API and persists its invoices/runs in distinct tables; it intentionally does **not** silently call legacy `tb_runs` or `tb_taxonomy_publications`.

## Architectural boundary

- B8 consent to approve *factual knowledge* is required before B9 publication. B9 immutable fact publication is a separate explicit method (`KnowledgeSnapshotRepository.publish`).
- Only an immutable, reverified B9 plus a verified nonpaid C handoff can be quoted. Only C-compatible **own-product function/product-type or product/audience-research problem/audience** facts are allowed into legacy C's taxonomy prompt. Other/competitor facts remain in B9 but are not used to claim this project's product features.
- `KnowledgeCExecutionRepository.quote(project_id, session_id, authenticated_reviewer_id, model_id, max_output_tokens=4096)` obtains the **current** verified OpenRouter C model/catalog/pricing, constructs the exact taxonomy-v2 prompt using approved B9 facts, and reserves a conservative upper-bound amount for one request. The complete inputs/request/model catalog SHA are pinned. Quote expires in 30 minutes; costs remain indicative, not an OpenRouter guarantee.
- `authorize(..., quote_id, authorized_usd)` records an **independent C spending consent** (not MiMo B3 or legacy C). It validates current provider catalog + user grant + pinned A/B9 and enforces a budget covering the quote, at most USD 10,000. A revoked consent cannot start a C run. One consent authorizes only one C run.
- `start(..., quote_id, consent_id, idempotency_key)` creates a durable `generation_pending` run but performs **zero** provider calls. An idempotency-key replay must pin the same quote and consent.
- Only an explicit `advance(..., sender=... or api_key=...)` dispatches **at most one** taxonomy generation provider request. It serializes and persists a reservation/attempt before network transmission, checks original consent, pinned exact prompt, model/pricing freshness and budget, and blocks all automatic retries. Interrupted/missing-cost responses become `uncertain` with held reservation; known billed malformed responses record actual spend and `failed`; over-reservation records `paused_budget`. Exact source quote/subject classification cannot be changed by the returned model output.
- Valid taxonomy candidates are normalized through the **existing** `reviewable_topic_batch` and remain `topic_review`. `submit_topics(...,choices)` requires another separate explicit human decision on every topic; its signed JSON and no-delete/no-update trigger are immutable. It does not auto-publish to the old Taxonomy/ SRE tables. Downstream publication requires separate integration and review. New running mode does **not** call `fact_extraction` or the old RTB's questionnaire pipeline.

**Authorization caveat:** the Python API is an internal trusted-server boundary. The supplied `reviewer_id` is NOT an authentication credential; a production HTTP caller MUST obtain that identity via authenticated session and enforce project role/CSRF/permissions first. Explicit per-project `knowledge_fact_review_authorizations` grants are checked for each operation; no unauthenticated REST API is installed.

## New PostgreSQL migration 053

- `knowledge_c_generation_quotes`: immutable, exact B9 handoff/model/pricing/transport request SHA, quote expiration/cost.
- `knowledge_c_generation_consents`: immutable one-quote, exact authorized limit and human reviewer.
- `knowledge_c_generation_revocations`: append-only explicit human revocation.
- `knowledge_c_generation_runs`: project-scoped, one per consent, idempotency-protected state machine, authorized/spent/reserved USD.
- `knowledge_c_generation_attempts`: single, non-retriable cost reservation/provider outcome record per run. Unknown billing retains a reservation. SQL blocks updates after finalization.
- `knowledge_c_topic_reviews`: immutable, explicitly human-reviewed taxonomy decision, not yet a published RTB/SRE taxonomy.

Tables use project-scoped composite FKs and separate budget consent from B3 and legacy RTB. No scheduled workers or automatic charge paths.

## Offline E2E / QA matrix

`tests/test_ke_stage14_c_qualified.py` exercises real A approval/B1 routing/B3 MiMo mock/B4-B9 approval/real C catalog refresh/full C cost→consent→start→mock taxonomy→topic review. Tests also verify:
- full A/B9 quote citation role provenance, secure own-product role, no unknown/competitor fact promotion into C;
- no calls or rows in old `tb_runs`, no real OpenRouter dispatch in QA;
- no generation without a C quote/consent, wrong project or ungranted reviewer, insufficient budget or expired/stale model pricing;
- revocation before spend, non-retry on unknown provider usage, durable held reservation;
- one immutably logged C attempt, SQL quote/consent immutability;
- earlier B4/B5/B6/B7/B8/B9 regression suite, 49 PostgreSQL migrations expected including 053 (migration count depends on repository baseline).

Run with PostgreSQL17 Linux + Windows:

    python -m unittest discover -s tests -p 'test_ke_stage14_c_qualified.py' -v
    python -m unittest discover -s tests -v
    python -m scripts.psi_release_gate --strict-ci

## Release limitations and next scope

- Full end-to-end **offline technical** qualification, not a live paid provider/LLM quality benchmark. Before any production use, an operator must supply actual OpenRouter catalog/model compatibility, network credentials and independently authorize a C quote and one paid attempt.
- Authenticated B8 reviewer UI and actual product-facing API route are **not** delivered by this stage; internal service requires a verified, already-authenticated host. Do not claim production reviewer access.
- C currently quotes/dispatches one taxonomy_generation stage with native v2 schema; the legacy optional critic, hierarchical Deep Opus pass and SRE taxonomy publication remain separately gated, not automatically imported by this new mode. Stage14 does **not** claim that all paid C features are wired.
- The last Stage13 HEAD `e5fad176` passed Linux with 1524 tests and PostgreSQL17 48 migrations; Windows was cancelled by GitHub Actions, **not passed**. Stage14 CI must verify this exact Stage14 code and all B8/B9 regression on Linux and Windows before any technical release conclusion.
- PR #30 remains **Draft** against A branch, and main remains unchanged. No automatic merge.
