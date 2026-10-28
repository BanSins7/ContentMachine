# PIE-MOD-30 — Supervised Candidate Insight Generation Engine v1

**Branch:** feature/patterns-insights-full-module; no edits to main, UAE/QDP/A/B/C/SRE.  
**Wire:** pie-candidate-insight-generation/v1.  
**Files:** research/insight_generation.py, tests/test_pie_insight_generation.py, migrations/204_pie_candidate_insight_generations.sql, package public exports.  
**State:** Candidate-only, no live source connector, reviewer, actual model send or publication.

## Scope and scientific eligibility

PIE-MOD-30 ties stage25 immutable candidate Pattern Registry revision to stage26 **fresh current authenticated** rights/project/row validation, stage27 source tombstone guard, stage28 strictly evidence-bound Russian research narrative and optional stage29 previously completed model attempt provenance. The engine outputs a fully traceable, **unverified candidate**, not a supported/public Insight. An absent pattern, mismatched hypothesis/owner/revision or unknown/invalidated source yields no Insight artifact and no public exposure.

The generated candidate contains a precise pattern series ID, positive revision, hypothesis SHA, source prompt/facts SHA, two logical evidence IDs (pattern revision + hypothesis), stage28 draft SHA and canonical JSON, generation mode, source model attempt/response SHA (if any) and explicit review_state=unverified_candidate. Draft sections preserve feature, units, discovery matched-pair cohort, original rational descriptive difference, missing calibrated confidence/p/q, causal limitations, independence/holdout gaps, counterexample uncertainty and proposed preregistered tests (ideas only). It cannot claim guaranteed video growth, publication rights, independently validated causality, or legitimate statistical significance.

## Default zero-cost local generation

generate_candidate_insight(connection, query, series_id, authorizer, reader, gate, signer, ...) defaults to **local_deterministic** and does not invoke or reserve a model. It prepares a fresh stage28 prompt by querying stage26 with exact project+authenticated owner and all original row/current source-policy rights. It then invokes stage27 invalidation preflight for the same pattern revision, checks the absolute **latest** revision from immutable registry in PostgreSQL, and builds stage28 canonical model-selection codes internally (no free-form LLM content). It materializes only safe deterministic Russian explanations using compile_candidate_insight_draft. The human-facing text is a bounded research draft, not a published Insight. No model provider secrets, media/source row IDs, transcripts or provenance objects are stored or returned.

Current project-scoped registry advisory transaction lock is held while reading, validating, and inserting the artifact. Stage25 revision writes and stage27 source revocations use that same lock; a revocation or reanalysis cannot race the generation commit and leave a newly generated artifact stale *at creation*. Later rights changes can make any existing generated candidate ineligible; **there is no public read API** in this step, and stage31+ must recheck before showing it.

The use of a caller-supplied signed cursor and owner filter is intentional: a requested pattern must be on the actual authorized/current stage26 result page. No historical fallback, global cross-project search or direct privileged snapshot bypass.

## Optional previously dispatched model result

A separate optional mode **provider_reported** is available ONLY when all of model_quote, dispatch_receipt and model_response_json are supplied. The model has already been dispatched by stage29 under explicit, independently validated spend approval and a non-retryable pre-send claim. This stage **does not invoke any model or attempt charges/retries**.

The engine requires: typed stage29 quote and receipt, reported_charged rather than uncertain_charge, correct project, exact prompt SHA and quote identity, original model JSON bytes matching the receipt's response SHA, one database row for the claim/attempt/outcome in the exact project, observed provider generation ID/catalog/model/request SHA, and the same reported USD cost not above the held reservation. If any proof is missing or contradictory, no artifact is persisted. The response JSON must also pass stage28's strict exact two evidence citations/registered selection codes. Provider-reported charge is **not** final bank settlement or proof of truthful research. The original bytes are hashed, but only deterministic redacted candidate draft content is stored.

No arbitrary LLM prose enters this stage; it can neither invent citations nor numerical evidence. Future stage31 will own independent semantic contradiction/audit logic.

## PostgreSQL 17 additive artifact ledger

Migration **204_pie_candidate_insight_generations.sql** creates pie_candidate_insight_generations:

- Composite project and generation SHA primary key; composite project, pattern series ID and revision foreign key to actual immutable stage25 registry.
- Project-scoped nullable stage29 model attempt foreign key, unique per project/model attempt; only provider_reported may attach a model attempt and response fingerprint, local_deterministic may not.
- Hypothesis SHA and prompt/source facts/draft SHA, exact JSONB evidence IDs and safe canonical candidate artifact; CHECK prevents can_publish=true, approved_live=true, causal_claim=true, independent_holdout_confirmed=true or promoted review status.
- BEFORE INSERT guard independently verifies exact source hypothesis/version and, for paid mode, the recorded reported_charged response SHA and source prompt SHA. It also checks all nine stage27 original-input tombstone kinds. This prevents a direct SQL insertion of a foreign/revoked pattern or a quarantined/uncertain provider response. NOTE: source-ownership/authenticated human rights MUST still be provided by Python host; SQL cannot prove external source rights.
- Immutable UPDATE trigger and source-index for later stage31 audit. Same exact artifact is idempotently returned; conflicting provenance cannot overwrite the historical result. Application writes the artifact inside the same transaction used to recheck source currency and invalidate safely.

Migration **203** had a previously undetected syntax bug in its two PL/pgSQL dollar-quoted trigger functions (single dollar signs). This stage repaired both to **$pie29$** tagged delimiters in PIE branch (commit e9a21e0fcb8d0e1850644b4c322d619f5fe75c25). **This changes the committed checksum for migration 203.** Since the invalid syntax could not be applied in PostgreSQL as written, a standard fresh database should apply the corrected version. If a real environment reports migration203 checksum mismatch, DO NOT rewrite the history table or force SQL; inspect and reconcile the actual applied SQL/checksum manually first. Migrations 200–202 were not changed.

## Reproducibility, adversarial and data isolation tests

tests/test_pie_insight_generation.py has **30 authored unittest methods**, including:

- Positive zero-cost deterministic source-pinned candidate with exact stage28 evidence IDs, cohort N=4, illustrative manually calculated 5/2 raw-unit effect and noncausal uncertainty/caveats.
- Latest revision/owner/hypothesis mismatch, absent pattern, revoked source or unavailable rights, malformed project/series, mutated output and bit-exact SHA replay failure.
- Repeat exact source/draft idempotency; no duplicate stored artifact, no background/provider call.
- All-or-none optional model quote/receipt/raw bytes; reported outcome must be present and match source prompt/catalog/model provider generation ID, cost/reservation and SHA; fake/missing, uncertain, cross-project, hallucinated links, changed quote/cost/response cause refusal without write.
- Negative privacy tests ensure media/video/row SHA/creator/credentials never appear in candidate JSON.
- Static SQL tests verify scoped foreign keys, immutable trigger, stage27 tombstone guard and stage29 reported-only link.
- Optional disposable PostgreSQL17 checks for fresh/upgrade schema, invalid cross-project/missing-pattern insertion refusal, and corrected stage29 migration quote syntax. These skip unless CM_TEST_PG_DSN explicitly points to a disposable PostgreSQL database containing the word test in its name.

Positive stage28 rights and stage27 tombstone checks and model/DB fakes are explicitly **MOCKED**. No real UAE producer, model, confirmed reviewer, rights/licensing, bill reconciliation or actual paid call is exercised.

Commands to execute on a full source checkout:

    python -m unittest discover -s tests -p 'test_pie_insight_generation.py' -q
    python -m unittest discover -s tests -p 'test_pie_*.py' -q
    python -m compileall -q intelligence/patterns_insights tests/test_pie_insight_generation.py

Use a disposable PostgreSQL17 CM_TEST_PG_DSN for opt-in SQL tests. Local full unittest, compileall, actual PostgreSQL fresh/upgrade, real EUA/UAE source rights, independent reviewer/holdout, live OpenRouter billing and GitHub green CI were **NOT observed** when committing these files. Implementation is code-complete candidate mode only, not integration/production ready.

## Next

**PIE-MOD-31 — Insight verification, contradictions and hallucination guard.** Must independently revalidate every numeric fact/unit/source scope, pattern/revision currency and counterevidence before any export; cannot use a stage30 artifact as authorization or make unsupported causality claims.
