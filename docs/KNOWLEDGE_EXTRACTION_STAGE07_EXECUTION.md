# Content Machine B — Stage 07 · B3 MiMo Execution & Cost Ledger

Status: additive Stage07 worker API (Python only); NOT an automatic background job or production E2E release. New PostgreSQL migration 046. Legacy TB spending consent, RTB runs, source-context, videos and already published taxonomies remain untouched.

## Pipeline boundary

Approved A context -> B1 immutable KnowledgeInputBundle -> B2 Full/Multi pack -> Stage06 facts-v3 **unsent** request batch -> **Stage07 manually authorized paid attempt(s)** -> **Stage08 B4 Python normalization** -> Stage09 B5 independent source grounding -> Stage10 B6 dedup/conflict -> Stage11 B7 gate -> Stage12 B8 human review -> Stage13 B9 immutable Knowledge Snapshot -> C.

No automatic Stage08+ processing or knowledge publishing occurs in Stage07. Only verified TEXT packs are transported to MiMo; no A video analysis or video ASR content is placed in a request.

## Exact manual sequence

The server caller must explicitly select project, immutable Stage06 batch and provide independent spending consent:

1. FactExecutionRepository.quote(project_id, batch_id) re-materializes the exact Stage06 packs, checks current A approval/profile and model-catalog freshness, and creates a **30-minute indicative reserve quote**; no provider call.
2. FactExecutionRepository.authorize(project_id, quote_id, authorized_usd, approved_by=...) requires a conscious budget approval covering the quote (maximum USD 10,000). A source/context approval or old Taxonomy Builder consent is never accepted as this authorization.
3. FactExecutionRepository.start(project_id, quote_id, consent_id, idempotency_key) binds consent exactly once to one run. Idempotent replay with identical key/inputs returns the original run and does not dispatch.
4. An **explicit** FactExecutionRepository.advance(project_id, run_id, sender=...) or advance(project_id, run_id) call re-verifies immutable pack SHA, pinned provider request SHA, current A context, current model catalog and remaining budget. It commits a **reserved** attempt in PostgreSQL BEFORE any network I/O, then sends exactly one non-streaming request with strict JSON Schema and fallbacks disabled.
5. On fully costed, valid response, charged USD and usage are recorded; candidates remain pending independent source grounding and human review. For multi-pack a subsequent explicit advance handles the next pack. After the final successful pack the status is **ready_for_normalization**, not approved.
6. Missing usage, HTTP failures, timeouts or worker crashes: **uncertain** or left **running + reserved**. The hold cannot be released by an automatic retry. A billed malformed/truncated response records its actual charge and stops in **failed**. Provider overcharge records actual spend and pauses further execution.

Methods are intended for use by an authenticated server-side caller in a future reviewed HTTP/job layer. Stage07 deliberately adds no route, auto-worker, endpoint, cron job or startup side effect: there is **no** automatic paid execution.

## SQL ledger

- knowledge_fact_cost_quotes (immutable)
- knowledge_fact_spending_consents (immutable, quote UNIQUE)
- knowledge_fact_consent_revocations (immutable)
- knowledge_fact_execution_runs (mutable spent/reserved/status; one run per consent)
- knowledge_fact_execution_attempts (one durable row per run/pack; **unique** run+pack)

Stored provider results are restricted to allowlisted usage, an opaque provider response ID and server-validated B3 proposals with their exact text citations. No raw full model prompt, API key, headers, HTTP error payload or source-document blob is stored in the ledger.

The retained Stage06 request blueprints are immutable and store only SHA fingerprints, not original source contents. Exception messages from untrusted HTTP providers are deliberately reduced to non-sensitive error codes.

## State machine

pending -> running (committed reservation before external call) -> pending (next Multi pack) or ready_for_normalization (all paid packs validated)

running -> uncertain (billing unknown, reservation held; no paid retry)

running -> failed (known billed invalid response/model/finish_reason; no retry)

running -> paused_budget (provider actual spend exceeds reserved amount)

pending -> revoked (consent revoked before dispatch)

pending -> paused_budget (insufficient authorized remainder)

No method in this stage automatically transitions a terminal state back to pending or retries an uncertain attempt.

## Caveats & next stage

Reservation is a cautious byte-based upper estimate with 20% buffer, **not** a contractual provider spending cap or exact tokenizer. Provider billing may exceed the reserve; the ledger retains the real charge rather than falsifying a capped cost. Reconcile uncertain bills manually against the provider before any new consent. Model catalog refresh is external and never invoked automatically by the Stage07 executor.

Extraction candidates are **not** B4-normalized nor B5 source-verified by this stage. A quote appearing in MiMo-readable text is not proof of semantic entailment or objective truth. The 80-candidate ceiling flags downstream extraction coverage concern.

If process termination occurs after committed reservation but before recording usage, the attempt remains 'reserved' with the run 'running'. This deliberately blocks new paid requests until a future **manual** reconciliation feature exists; never infer a zero-dollar bill.

## Offline QA / Release

- python -m unittest tests.test_ke_stage07_execution -v
- python -m unittest discover -s tests -v (Linux CI and Windows CI)
- python -m scripts.psi_release_gate --strict-ci
- POSTGRES17 migrations executed once, immutable checks run on migrated schema
- No real paid OpenRouter request in automated CI; only injected fake sender

Next implementation stage: B4 Python proposal normalization. Do not advance to Stage08, merge PR #30 or run live paid API calls without separate request.
