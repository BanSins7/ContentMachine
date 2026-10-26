# Content Machine B — Stage10 / B6 Deduplication & Conflicts

**Status:** Deterministic, offline, append-only *exact* claim grouping and lexical **potential-conflict** detection. This stage does NOT use semantic embeddings, fast LLMs, MiMo, Sonnet, video, or paid OpenRouter calls; no knowledge approval, B8 reviewer action, B9 publication or C invocation.

## Input gate — B5 and all prior history are mandatory

FactDeduplicationRepository.analyze(project_id,completed_run_id) requires a full, already persisted Stage09 B5 grounding. The repository calls FactGroundingRepository.materialize() to independently recheck *historical pinned A originals* against B1/B2 sources, Stage07 MiMo proposal attempts, the Stage08 immutable normalization and Stage09 immutable citations. A run with no B5 snapshot cannot create B6 rows.

Every normalized B4 proposal position must have exactly one distinct, canonical-SHA-verified B5 receipt. Reordered, swapped, forged, omitted, duplicated, changed-source, or silently discarded receipts are rejected. Explicit B4 quarantine and B5 invalid citations are represented as blocked members; they are **never** grouped as valid claims.

## Exact group algorithm

Group candidates ONLY when:
- The B5 verdict is `source_quote_matched`.
- B4 kind, *exact whitespace-normalized B4 fact text*, source_role, subject_scope and subject_id match. No casefold, NFKC, paraphrase or meaning inference is used in the exact group key.
- If scope is ambiguous (not own_product without a pinned subject_id), source/fragment identity is included in group key, so unrelated competitors and anonymous claims cannot be implicitly merged.

SHA-256 stable `group_id` uses engine version, immutable B3 execution run and canonical key. Representative is the lexicographically smallest proposal_id for deterministic replay; it is **not an elected true fact**. Each original model proposal keeps an individual, immutable membership row with pack/candidate index, proposal_id, source version, output, original locator SHA, original quote SHA, trust label and individual B5 receipt SHA. All source references survive deduplication (including two identical claims originating from different sources).

A zero-fact B5 run yields zero groups; model-inferred completeness is never implied.

## Potential conflict rules (conservative)

Potential contradictions are flagged ONLY for a comparable own_product claim or explicitly identified competitor. Conflict pairing is between distinct exact groups in the same claim kind, source role and subject scope/identity.

- `opposite_negation`: same lexical tokens except a single standalone "не" or "not", excluding common compound cases ("не менее", "не более", "not only"). No semantic entailment, grammar interpretation or business arbitration is claimed.
- `different_number_same_phrase`: one number in both otherwise identical lowercased phrases, but differing numerical values. This may be a **false positive** due to timeframe/units/definitions; it is only a candidate for human review.

Pairs are sorted by stable group ID, receive their own SHA and immutable `review_state=needs_review`. No winner is chosen; no source text is overwritten, no group is deleted or promoted. A strict 50,000-pair cap **fails closed** rather than silently truncating the review workload. Nonmatching lexical claims can conflict semantically; B6 does NOT claim exhaustive conflict detection. B7 must consider coverage and B8 must review contradictions.

## PostgreSQL v17

Migration `049_ke_fact_dedup_conflicts.sql` creates 4 additive tables:
1. `knowledge_fact_dedup_batches` — one immutable input/report SHA / counts snapshot per B5 grounding ID, composite project-scoped FK back to B5 normalization and original B3 run.
2. `knowledge_fact_exact_groups` — immutable representative/kind/scope/subject and member-count group.
3. `knowledge_fact_dedup_members` — immutable one-row-per-original proposal including all distinct B5 quotation provenance or blocked reason, FKs back to both B4/B5.
4. `knowledge_fact_possible_conflicts` — immutable potential relations between original groups, separate from approved Knowledge.

All tables have UPDATE/DELETE guards. Same project/run snapshot is idempotent and historical replay checks every saved group, every member and candidate relation. B6 does not expose a public paid job, HTTP endpoint or automatic worker.

## Public Python contracts

    from intelligence.knowledge_extraction.deduplication import FactDeduplicationRepository
    repo = FactDeduplicationRepository()
    summary = repo.analyze(project_id, completed_and_grounded_run_id)
    historical = repo.materialize(project_id, completed_and_grounded_run_id)

For pure unit QA, use `analyze_grounding(run_id=..., batch_id=..., normalized_rows=..., grounding_receipts=..., b4_report_sha256=..., b5_report_sha256=...)`. The B5 origin receipts must be actual independently checked production inputs, not user-provided claims. Server must authenticate the project caller at its future transport boundary.

## Security / operational limits

- No model or website truth is certified. Stage09 B5 matched quotation proves literal appearance in pinned A output, not factual objective correctness or semantic entailment.
- Sonnet-extracted website trust label stays `model_reported_unverified`. Do not reinterpret it as independent live website verification.
- B4 and B5 quarantines remain visible with exact counts; no accidental automatic own-product promotion.
- Explicit unknown/ambiguous subject source identities remain separate; false negatives for duplicates are preferred over merging unrelated claims.
- No LLM/embedding is invoked; lexical conflicts are incomplete hypotheses, not authoritative contradictions.
- Stage11 B7 Quality Gate will assess blocked counts, detected conflicts, pack completeness and readiness for Stage12 Human Review.

## QA

    python -m unittest discover -s tests -p 'test_ke_stage10_deduplication.py' -v
    python -m unittest discover -s tests -v
    python -m scripts.psi_release_gate --strict-ci

Linux and Windows GitHub CI use PostgreSQL 17 and fake Stage07 OpenRouter senders exclusively. Test exact duplicate preservation, citation-count conservation, numeric/polarity candidate flags, unknown competitor separation, partial B4 quarantine, B5 rejection, foreign project access, SQL immutable triggers, repeated historical replay, full/multi pack and stale profile revisions.

**Next Stage11 B7 — Quality Gate** only on explicit new instruction. Do not merge into main or claim knowledge publishing from B6.
