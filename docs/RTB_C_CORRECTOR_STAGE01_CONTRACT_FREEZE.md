# RTB C Corrector — C-FIX-00: Contract Freeze

Baseline: main commit 218f5f3163ccc7179a4809ba4386a1d1b6ff80f5 after A PR #29 and B PR #30. Development branch: feature/rtb-c-corrector-v2.

Status: Stage 01 **initial implementation only**. No C-run lifecycle, paid generation, database migration, Deep V2 validation, or publication has been changed yet. This is a grounded freeze of the actual A/B/C interfaces, not a replacement of working RTB V1.

## 1. Actual source contracts

| Owner | Contract in merged main | Future C behavior |
| --- | --- | --- |
| A | project_context_snapshots.id (UUID), project-context/v1, context_sha256 | Use the existing context_id as the exact A8 project_source_snapshot_id |
| A video | video-intelligence/v1, immutable output_sha256, evidence dictionary with evidence_id keys | Hydrate through the verified existing A/B reader; preserve evidence content, trust and timing |
| B9 | knowledge_approved_snapshots.id (UUID), knowledge-snapshot/v1, snapshot_sha256 | Pin exact snapshot by ID and SHA |
| B facts | facts[].fact_id = SHA-256, reviewed_text, source_role, subject_scope, citations | Preserve the original B9 data. Convert only in explicit RTB/SRE adapter view |
| B C handoff | knowledge-c-handoff/v1, input_mode knowledge_snapshot_v1 | Verify against upstream build_c_handoff rather than trusting client JSON |
| RTB V1 | Deep tree 11 fields, two Opus passes, two reviews, quality gate | Preserve exact V1 behavior and historic approved publications |
| Search Planner | HypothesisContext/v1, 11 strict fields, legacy Fact Review join | Add v2 reader and B9-aware input path without changing V1 |
| SRE | sre_facts.id UUID and atomic RTB bridge | Reuse identical UUID facts; detect conflicting reuse |

Important: the B9 snapshot JSON has a facts array and SHA-based fact_id. It does NOT use the hypothetical approved_facts[].id UUID fields in the old C Corrector draft. Its immutable A anchor is context_id, not a newly invented project_source_snapshot_id property.

## 2. Fact identity and original video observations

C's stable RTB/SRE adapter UUID uses UUID5 with NAMESPACE_URL and the namespaced string contentmachine/rtb/b9-fact/v1/{project_id}/{fact_sha256}. Project-scoping prevents two projects from accidentally sharing the same SRE primary key. Identical B9 facts within the same project across repeated C-runs yield identical UUIDs. Never generate random fact UUIDs for the same SHA. Keep source SHA IDs and citations in B9 and the frozen manifest.

In A, original observations are stored under the evidence dictionary of a video-intelligence/v1 output, not a guaranteed flat observations[] array. Existing PostgresAResolver.resolve_video(project_id, context_id, VideoAnalysisPin) verifies the approved context, source version, output ID and SHA. A evidence identifiers include entries such as speech:job:seg00001 and model_vision:job:sc001:f001, with their original locators, timestamps and trust status.

Use a stable C locator output_id::original_evidence_id; this is an **index key**, not a rewrite of the original observation. Carry the exact original evidence object and pinned output SHA. Do not infer product functionality, video performance, fully observed creative mechanisms, or valid time ranges from these references. Empty video refs are supported; missing or tampered pinned outputs must block C.

## 3. RTB and downstream version policy

Deep V1 retains its exact 11 node fields: id, parent_id, kind, relation, source_fact_ids, name, rationale, definition, include, exclude, examples.

Deep V2 will require 14 node fields: V1 plus source_observation_ids, search_signals, validation_focus. Every direction/branch must have three empty extension arrays. Every hypothesis must have 1-6 search signals, 1-6 validation focuses, and optionally 0-12 source observation refs verified against frozen A. Strict Deep V2 validation belongs to Stage 04.

B Stage14 already created a single-pass flat C pipeline using knowledge_c_generation_runs and taxonomy-v2, with independent quote, consent and one manual topic review. This is NOT the two-pass Deep RTB lifecycle. It must remain available without being confused with the Deep V2 path. Deep V2 should extend existing tb_runs/tb_deep_runs with an explicit B9-bound mode and a real immutable research-input binding, without fabricated tb_fact_review_submissions.

Existing Deep V1 code reads _reviewed() from tb_fact_review_submissions and pins deep-v1. New mode must choose a version-specific reader and prompt from persisted run config. Pass 1/2/expansion must use the same frozen B9/A input, never latest data. Keep Claude Opus 5.5, two human approvals, separate paid dispatch and maximum two expansion passes.

SRE and Search Planner V1 must receive a deterministic 11-field core projection, never new V2 keys in strict legacy schemas. Search Planner V2 alone consumes source_observation_ids, search_signals and validation_focus and forwards them to AI Query/Analysis Preparation without treating search signals as final queries or validation focus as fact verification.

## 4. Stage 01 executable artifacts

- intelligence/taxonomy_builder/research_contracts_v2.py: B9 handoff identity check, stable B9 SHA-to-UUID, A verified-original observation index and non-mutating 14-to-11 field projection.
- tests/test_taxonomy_c_corrector_contracts.py: offline two-approved-fact and two-A-evidence fixtures, three-level V1-compatible tree, stable ID checks, changed SHA/project failure cases and no-LLM projection.
- This document records the exact source-of-truth boundaries and version choices.

Run offline unit tests with:

    python -m unittest discover -s tests -p 'test_taxonomy_c_corrector_contracts.py' -v

## 5. Work remaining

C-FIX-01: actual frozen Research Input service using PostgresAResolver. C-FIX-02: B9-bound multi-pass C-run, quote and consent, PostgreSQL changes. C-FIX-03: strict Deep V2 JSON Schema and Pass 1. C-FIX-04/05: review and Pass 2. C-FIX-06: Python Search Readiness gate. C-FIX-07: immutable V2 publication, SRE facts reuse, V1 projection. C-FIX-08: Search Planner V2. C-FIX-09: T01-T36 and full CI.

Do not reserve the next migration number until checking integrated A/B migrations 030-053 and competing unmerged SRE/Collector/Analytics branches with overlapping lower migration prefixes. Do not mutate historical V1 publications, add a third LLM, enable auto-paid retries, or quietly drop input evidence.
