# Content Machine — Knowledge Extraction B, architecture contract v1

Status: **B Stage01 / B-00 contract freeze**, 2026-10-25. This is NOT B1/B2/B3 execution or production readiness.

## Repository baseline, integration strategy

- Source of truth: A branch `feature/project-source-intelligence-v1`, commit `1970cd0620e826542c4cae70db4d4cf8f824a607`, draft PR #29, based on `main` `8d9bee17402019a26bb4eaa27df040beb06153b9`.
- New B branch `feature/knowledge-extraction-b-v1` begins at that A commit and is additive. Its draft PR must target the A feature branch until A merges to `main`. Never merge A/B automatically.
- A SQL migrations 030–041 are immutable. Next potential migration slot is **042**; Stage01 adds no SQL.
- Each follow-on stage is separately user-authorized. No paid OpenRouter calls, media ingestion, external fetch, or runtime feature flag in Stage01.

## Boundary and ownership

```text
A Stage02–10: profile/source versions, downloaded or uploaded assets, parsed documents,
              Sonnet-reported website text, ASR/OCR/Vision/Video Intelligence,
              source-grounded Evidence with immutable SQL provenance
    ↓
A Stage11: published and manually approved project-context/v1
    ↓  verified Stage11 _snapshot ENVELOPE (external approval record)
B1: intake, cross-project/hash/pin/revision checks and source/text inventory
B2: lossless Full Context / Multi-Pack with explicit coverage and token reservation
B3: OpenRouter MiMo, text source fact proposals ONLY; no video or video transcript
B4: Python schema/subject normalization
B5: quote-to-pinned-A-artifact grounding; never assert objective truth
B6: exact duplicate merge, unresolved candidate conflicts
B7: deterministic completeness / integrity Quality Gate
B8: separate explicit Human Knowledge Review, CAS, immutable decisions
B9: approved KnowledgeSnapshot with exact A source hash and pinned video refs
    ↓
C: NEW knowledge_snapshot_v1 input for existing RTB/deep Opus 5.5; explicitly
   authorized C cost quote/consent; no second MiMo extraction of B facts.
```

The legacy RTB modes remain `legacy_questionnaire_v1` and
`approved_project_context_v1`. Existing A12 integration in
`intelligence/taxonomy_builder/context_binding.py`,
`run_repository.py`, `run_contracts.py`, `provider.py`,
`cost_repository.py` and `deep_repository.py` is **not replaced** in Stage01.
`facts-context-v1` is not `facts-v3` and its approved facts are not automatically a B9 snapshot.
`research_goal` in current legacy RTB is fixed to health/fitness/maximize_views:
the A profile `goal` remains separate; B→C must not silently overwrite either.

## Approved A Context Envelope: the executable v1 boundary

Schema: `intelligence/knowledge_extraction/schemas/context_envelope_v1.json`.
Validator: `intelligence/knowledge_extraction/contracts.py:validate_context_envelope`.

The actual A `context_service._snapshot(conn, project_id, context_id)`
returns an **envelope** with `context_id`, `context_version`,
`context_sha256`, `profile_revision`, `profile_hash`,
`input_fingerprint`, `created_at`, `approval`, `approved`, and
`context`. The nested immutable `context` is produced by
`context_builder.build` with `schema_version="project-context/v1"`.
Its fields include `profile`, `source_manifest`, `analysis_refs`,
`evidence_manifest`, `evidence_ids`, `approved_evidence_ids`,
`unavailable_or_excluded_sources`, `reviewable_relationships`,
`coverage` and `warnings`.

**Critical:** the nested payload retains `approval_status="pending"`
even after external SQL `project_context_approvals` has approved the
context. Never mutate/re-hash a published payload to reflect approval.
`approved_partial` must have explicit exclusions. Approving an A source
collection **does not** approve extracted B facts.

An older theoretical `ProjectContextSnapshot` DTO in
`intelligence/project_sources/contracts.py` also says
`schema_version="project-context/v1"`, but embeds context ID/version,
uses different field names and includes optional `approved_fact_ids`.
It does not describe what A Stage11 actually persists or returns.
**B accepts only the real envelope**, rejecting a DTO-shaped guess. The
old DTO stays untouched to avoid an unrequested A compatibility change.
A follow-up explicit ADR/migration may deprecate it, but Stage01 does
not silently create a second payload schema.

Python `validate_context_envelope` first validates JSON Schema draft
2020-12 and then verifies canonical A SHA-256, input fingerprint,
project/profile scope, approval state, source/output/evidence version
relationships, all member IDs, per-source and aggregate counts,
approved Evidence IDs, missing-source manifest and relation IDs.
It is a pure *in-memory* gate and does **not** certify that SQL rows or
external media blobs currently exist. B1 Stage02 must use project-scoped
pinned SQL lookups to verify those objects BEFORE provider use.

## Resolver contract (not yet implemented)

`ImmutableAResolver` is a Python Protocol, with four read-only operations:

- `resolve_context(project_id, context_id)` ⇒ original A envelope;
- `resolve_output(project_id, context_id, source_id, source_version_id, output_id, expected_sha256)` ⇒ pinned, canonical output;
- `resolve_evidence(project_id, context_id, evidence_id, expected_quote_sha256, expected_locator_sha256)` ⇒ pinned original quote and locator;
- `resolve_video(project_id, context_id, VideoAnalysisPin)` ⇒ original A fusion output, from exact SHA pin.

For Stage02 these MUST bind project ID on every SQL join, re-run
`context_service._verify_pins`, recompute hashes of canonical
`source_processing_outputs.output`, quote and locator, validate source
version and status, verify stored asset hashes when applicable, and reject
foreign/missing/stale/unavailable refs. No remote URL, AI model, arbitrary
local path, or user-provided resource can act as an immutable resolver.

## Fragment provenance contract

A's Stage10 `evidence_normalizer` intentionally caps materialized Evidence
per run (at 450); Stage11 caps number of Evidence and Context payload bytes.
Consequently **A's `evidence_manifest` is NOT guaranteed to represent
every sentence in a multi-hundred-page PDF**.

B Stage03 will build a separate **technical text-fragment index** from
pinned, permitted `source_processing_outputs` for
`document-segments/v1`, `web-source-extract/v1` and pinned questionnaire
fields. Its `TextFragmentPin.fragment_id` is NOT an A `evidence_id`.
When an A Evidence exists its ID may also be attached as optional provenance.
Every fragment must preserve `source_id`, `source_version_id`,
`output_id` (when applicable), SHA, original locator, segment index,
source role and subject; Stage03 verifies the actual text/offset, refusing
fabricated matches. Unknown/unreadable mandatory output ⇒ an explicit
`uncovered` reason and fail-closed/approved partial policy, NOT a
fabricated Evidence ID or silent truncation.

MiMo B3 processes text from the pinned questionnaire/document/website
inventory ONLY. Even if A has transcript text, the source video and its
ASR/OCR/Vision/Fusion stay out of B3. `VideoAnalysisPin` stores only
`source_id/source_version_id/output_id/output_sha256/schema_version`
for A `video-intelligence/v1`. Do not use the different historical
DTO `video-analysis/v1` from A contracts.py as the fusion output format.

## Roles, subjects and factual trust

Allowed roles: `own_product`, `audience_research`, `competitor`,
`creative_reference`, `other`. Conservative defaults for known
website/account source kinds are in `contracts.py`; *unknown,
questionnaire_field, research_document, research_link* default to
`other`. B Stage03 must use verified questionnaire field paths and
owner-selected subject mapping to disambiguate facts and ensure distinct
`subject_id` for each competitor. Never assume a competitor claim is
an own-product capability.

`model_reported_unverified` for Sonnet-reported website text is preserved.
An exact quote match establishes occurrence in stored model-produced text,
not independently downloaded site bytes, semantic entailment, or objective truth.

## Safety, economics and rollout

Preserve technical project isolation, immutable provenance checks, source
classification, prompt-injection-as-data, local secret/path redaction and
explicit one-run cost/consent; user approval for A content is not blanket
approval for new paid B or C calls. The source requirements disagree about
copyright/rights gates; Stage01 records the conflict, does not introduce new
copyright/rights enforcement, and does not falsely claim revocation support.
See `docs/ADR_KNOWLEDGE_INPUT_PROVENANCE.md`.

Stage02–03 introduce resolver, inventory and SQL after separate authorization.
Stage04–07 introduce packs, MiMo and spending ledger. Stage08–12 introduce
B fact validation, quality and review. Stage13 publishes Knowledge and C bridge;
Stage14 does E2E release qualification. No new mode is active at Stage01.

## Acceptance for Stage01

1. `python -m unittest tests.test_ke_stage01_contracts -v` validates
   generated real A Stage11 payloads wrapped in real envelope format.
2. Wrong project, content hash, profile revision, incomplete approved/full
   state, false evidence provenance, wrong output ID, unexpected DTO shapes
   fail closed with stable reason codes.
3. Video refs are passed through without transcript/observations; no paid calls.
4. Run the **existing entire** Linux+Windows CI and A Stage14 release gate;
   add no migration and change no existing RTB behavior.
5. Open a separate Draft B PR against the A feature branch; do not merge.
