# Content Machine — B Stage08 · B4 deterministic Python normalization

Status: **offline normalization and immutable PostgreSQL snapshot**, NOT B5 evidence verification or Human Knowledge Review. Additive to A and B Stages01–07. No provider calls, new HTTP routes, background jobs, changes to legacy RTB tables or approved snapshots.

## Input boundary

Only a persisted Stage07 ready_for_normalization run is eligible. The run must have exactly one completed billed attempt per Stage06 Full/Multi pack; request SHA, pack SHA, usage, candidate count/80-limit and response metadata must agree. Failed, uncertain, paused, revoked, incomplete or still-reserved runs are blocked; do not claim partial success. B4 does not use an LLM or A video/ASR/OCR/Vision/Fusion observations.

Historical read-only replay of exact immutable A/B1/B2 packs uses the existing Stage06 materializer. The current profile may have changed since the paid run; B4 remains pinned to the original approved source/version, not the latest profile.

For every candidate:
1. Validate the six facts-v3 model-owned fields: kind, text, source_chunk_id, source_quote and zero-based Unicode quote_start/quote_end.
2. Verify original chunk membership and exact quoted text in the immutable stored Full/Multi pack. This is a **preliminary replay**, not B5 independent source-blob grounding.
3. Reconstruct expected server-owned source ID/version/output, fragment, locator, evidence IDs, role, subject and trust label from the pack. Candidate-supplied modifications never become authority.
4. Only trim/collapse whitespace in fact text; never spellcheck, change numbers, casefold, NFKC, rewrite meaning, infer product functions or create new facts. Preserve original_text, exact source_quote and offsets.
5. Generate SHA-256 stable IDs from normalizer version, run ID, pack/candidate index and original candidate digest. Exact duplicates stay separate until B6.
6. Store valid cards as pending_grounding with grounding_status=pending_b5, human_review_status=unreviewed. Invalid cards enter immutable quarantined status with a stable technical reason, without disappearing from counts.

Unknown scope is never promoted to own_product. Ambiguous cards expose requires_subject_review. Matching a stored quote does not establish semantic entailment, independent factual truth or human approval.

## Python API

- FactNormalizationRepository.normalize(project_id, completed_execution_run_id): persist-or-replay idempotently, with result counters; provider_calls=0.
- FactNormalizationRepository.materialize(project_id, completed_execution_run_id): historical report plus fully revalidated individual cards.
- normalize_drafts(run_id=...,batch_id=...,blueprint=...,source_packs=...,attempts=...): pure in-memory deterministic normalization with no SQL/network/model.

A transaction holds the execution run lock plus shared locks on completed attempts; simultaneous normalization cannot insert two snapshots. Historical replays detect changed Stage07 drafts or immutable output integrity failures.

## PostgreSQL schema

Migration 047_ke_fact_normalization.sql creates:

- knowledge_fact_normalization_batches: immutable one-row-per-run report, input/report SHA, exact counts, project-scoped composite FK.
- knowledge_fact_normalized_proposals: immutable per-proposal or quarantined record, unique pack/index and SHA-stable IDs, project-scoped references.

Both tables forbid UPDATE and DELETE. Quarantine stores the rejected candidate SHA and reason rather than another copy of bad source data. Normalized proposals contain only the exact source quote and lineage needed for Stage09/B8, never the entire source document, API secrets or OpenRouter prompt.

## Quality and security

A zero-fact B3 response creates zero B4 facts; it does NOT prove exhaustive semantic extraction. Every proposed candidate position has exactly one valid or quarantined row; report also captures per-pack counts, 80-candidate limits, quarantine reasons and zero video/provider calls. B7 still checks missing/invalid submissions. Source-data prompt injections stay inert data: this stage executes no tools or external instructions.

QA commands:

    python -m unittest discover -s tests -p 'test_ke_stage08_normalization.py' -v
    python -m unittest discover -s tests -v
    python -m scripts.psi_release_gate --strict-ci

Linux and Windows CI use PostgreSQL 17 and a synthetic provider sender only. Negative QA: fabricated IDs, text/quote offsets, competitor→own-product forging, altered model drafts, exact cited provenance, partial/uncertain attempts, replay after profile changes, immutable triggers, multi-pack, cross-project and idempotency.

Next only after user authorization: Stage09 B5 independent immutable A source artifact grounding (including independent source version, exact quote and locator hash checks). B4 output cannot be published directly to C.
