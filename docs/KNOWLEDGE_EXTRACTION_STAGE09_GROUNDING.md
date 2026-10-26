# Content Machine B — Stage09 / B5 independent original-source grounding

Status: **offline provenance and exact-quote verification**, no new provider call and no semantic-claim approval.

## Why B5 is NOT a repeat of B3/B4

B3 MiMo sees server-owned Full/Multi-pack text, and B4 verifies model citations against that same pack. B5 must independently reopen the **original approved A context/source version**. It must reject manipulated original output hashes or provenance even when a model-visible EvidencePack still passes its own SHA checks.

B5 uses a fresh PostgreSQL repeatable-read transaction to load the saved B1 KnowledgeInputBundle via Stage03/04 historical context verification, revalidate every pinned A source/profile version, SHA of processing-output JSON, immutable source-file bytes where enabled, A evidence pins and original-profile field locators, and then completely re-build B1 inventory + exact original text fragments from A. This independent original text is compared against **every** B2 Full/Multi row, even if MiMo cited zero facts from that row. Missing/mismatching chunks, source roles, versions, output IDs, field paths, locators, SHA or Unicode text trigger a hard fail. The entire Grounding snapshot is not written on a broken source lineage.

For each B4 normalized proposal, the exact MiMo-proposed quote is then checked again at its zero-based Unicode offsets into the **independently reconstructed original A fragment**. For Multi-Pack, the pack-local quote offsets are shifted by the chunk's verified original char_start before matching. B4-invalid candidates are accounted for as skipped without promotion; individual bad quotes, unknown chunk and changed claimed ownership become rejected records with stable reason codes. Each input candidate produces exactly one receipt. Exact duplicates remain separate for Stage10/B6.

## Outcomes and trust semantics

- source_quote_matched — an exact literal substring was found in the pinned stored A source version/output, with original fragment/locator/quote/asset/hash/revision lineage preserved. **It is NOT proof the claim accurately describes that quote, is objectively true or has been human-approved.**
- rejected — individual candidate citation, quote range or role/source lineage disagrees with A.
- skipped_b4_quarantine — B4 already quarantined the model proposal. B5 retains count/identity, never approves it.

Preserve source trust labels: model_reported_unverified for Sonnet website extracts; local_document_extraction for parsed documents; user_provided_profile for questionnaire. A webpage match only proves that the quote occurs in stored, model-reported website text — B5 does NOT fetch the live website, verify current truth, or establish website/claim authorship. B5 does NOT ingest video, video transcripts, ASR/OCR/Vision or fusion into MiMo. B5 does not make LLM requests, review human approval or change Stage07 spending.

## Python API

    from intelligence.knowledge_extraction.grounding import FactGroundingRepository
    repo=FactGroundingRepository()
    result=repo.ground(project_id, completed_and_normalized_run_id)
    old=repo.materialize(project_id, completed_and_normalized_run_id)

These are server-side Python calls; Stage09 does not enable any HTTP endpoint or automatic/background job. Idempotent grounding returns the same ID when the exact historical source and immutable receipts agree. Historical replay (including after profile/source revisions) re-checks the originally approved pinned A records, not the current project's latest inputs.

For deterministic fixture QA, ground_proposals(...) is the pure B5 algorithm. It requires independently supplied fragments and A original_texts, never treats a supplied source_pack as the source of truth.

## PostgreSQL

Migration 048_ke_independent_grounding.sql introduces immutable tables:
- knowledge_fact_grounding_batches: one per B4 normalization, composite FKs for project/normalization/run/batch and original A context; report/input SHA; strict accounting of matched/rejected/skipped proposal totals.
- knowledge_fact_grounding_receipts: one for every B4 candidate index with stable proposal ID, status/reason, SHA of receipt and server-owned original source/locator/version pins. Explicit immutability triggers and project-scoped FK prevent mixing knowledge between projects.

B5 stores only receipt metadata, hashes and source locators (no duplicate original PDF/site text, raw MiMo prompt, API key or media bytes). The valid B4 proposal still lives in the immutable B4 table for Human Review after B6/B7. No automatic publication into C.

## QA and limits

    python -m unittest discover -s tests -p 'test_ke_stage09_grounding.py' -v
    python -m unittest discover -s tests -v
    python -m scripts.psi_release_gate --strict-ci

Test with PostgreSQL17 on Linux and Windows: Full/Multi source reconstruction, Unicode quoted span, source-identity forgery, altered output bytes, stale profile historical replay, missing B4, unknown/duplicate proposals, individual quote rejection, immutable receipts and foreign-project rejection. Fake model sender only; zero paid calls.

**Stage10 (B6) is the next work item:** exact duplicate analysis and candidate conflict detection. Stage11 B7 then audits pack completeness, grounded quotes, B4 quarantines and readiness for independent Stage12 B8 Human Knowledge Review. Do not claim semantic truth or production release based on quote matching.
