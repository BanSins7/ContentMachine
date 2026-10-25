# Knowledge Extraction B — Stage 02: read-only A Context Intake & Integrity

**Status:** Implemented B1 read-only resolver, *not* B1 KnowledgeInputBundle
publication (Stage03), not B2 packs, and not production E2E. Based on A Stage11
and the Stage01 frozen envelope `ke-a-context-envelope/1`.

## New entry point

`intelligence.knowledge_extraction.intake.PostgresAResolver`.

- `resolve_context(project_id, context_id)` returns **only** an already
  approved real Stage11 Context envelope with A's immutable original payload.
- `resolve_output(project_id, context_id, source_id, source_version_id, output_id, expected_sha256)`
  retrieves exactly one Stage11-pinned original `source_processing_outputs.output`.
- `resolve_evidence(project_id, context_id, evidence_id, expected_quote_sha256, expected_locator_sha256)`
  retrieves the pinned exact quote, locator and trust label.
- `resolve_video(project_id, context_id, VideoAnalysisPin)` retrieves the
  pinned **unaltered** A `video-intelligence/v1` JSON (the caller must keep it
  away from MiMo; B3 will be text-only).
- `inspect(project_id, context_id)` returns `knowledge-intake-report/v1`
  readiness with source/analysis/evidence counts, explicit excluded sources,
  video-ref IDs, proof flags and warning codes, **no source text**.

All operations are read-only and have **zero provider calls**. These Python
APIs have no HTTP handler and cannot accidentally start a paid pipeline.

## Transaction and schema guarantees

1. Project ID and Context UUID are validated before database access.
2. PostgreSQL connection begins with `SET TRANSACTION ISOLATION LEVEL
   REPEATABLE READ READ ONLY` before any `SELECT`; no A `_pid`, `get_project`
   or `sync_profile_to_db`, which might write or synchronize a profile.
3. A `context_service._snapshot` verifies Context SHA, manifest and pinned
   source/output/Evidence rows; Stage01 `validate_context_envelope` checks
   canonical hash, input fingerprint, approval record, project/profile identity,
   graph and coverage.
4. For new B intake (default `require_current=True`), reconstruct the
   current A Context inside the **same** repeatable-read transaction and reject
   stale hashes, source version changes, archive changes, and profile revisions.
   Explicit `require_current=False` supports historical replay from pinned
   A records; **do not permit it when creating a fresh B Knowledge Run**.
5. Every pinned source is rechecked against the actual immutable version
   metadata, active source type and content SHA/byte-size/media type.
   All output JSONs are hash-verified and schema-version-verified.
   Fusion must match source/project/version ID and source video SHA.
6. Evidence quote and locator are independently re-hashed; grounding input
   output must be the same pinned output/version with original SHA. For
   document/web output schemas, source quote must occur in stored source
   text. Model-reported web text remains **unverified external content**.
   Profile-field Evidence is verified against its pinned original field/revision
   and exact field value hash. These checks do NOT prove entailment or truth.
7. Registered research-file versions are joined through
   `project_research_file_versions` and `project_media_assets`, with
   source/version/content SHA checks and A's existing secure
   `file_library._project_folder/_safe_path`. File bytes are read in bounded
   1-MiB chunks and SHA-verified. Missing file, mutated bytes, invalid
   metadata or symlink/path-escape fails closed.
8. Per-method output/evidence/video reads reverify the **whole** A snapshot
   before allowing a pin-specific selection. An attacker cannot switch to
   another source/version/ID by changing call arguments.

## Readiness and limitations

A decision `approved_partial` is valid only with the A-approved complete
list of excluded source IDs. The readiness report **includes those exclusions**
and is labelled `approved_partial`, never `ready`. New B Stage03 must decide
whether excluded text is mandatory; Stage04/05 must enumerate all mandatory
text fragments. No claim of 100% semantic coverage is made.

Video Fusion **JSON SHA and SQL provenance** are verified. Local MP4 byte
availability is **not yet independently checked** by this B resolver:
`verified.video_media_bytes=false` is explicit. Video is never passed to
MiMo by Stage02. Stage03/B9 can require an A media re-verification for
downstream video consumption; don't advertise local video storage completeness.

A's Context manifest can omit full text, and its Evidence normalizer is
bounded. Thus this Stage02 gate does not imply that every document sentence
has been packed. Text-fragment coverage and pinned KnowledgeInputBundle
persistence are reserved for Stage03.

No new copyright permission enforcement is introduced; the prior
requirements conflict remains described in
`docs/ADR_KNOWLEDGE_INPUT_PROVENANCE.md`. Cross-project isolation,
provenance/hash, safe filesystem access, and explicit future provider-cost
consent remain mandatory.

## QA

```bash
python -m unittest discover -s tests -p 'test_ke_stage02_intake_db.py' -v
python -m unittest discover -s tests -v
python -m scripts.psi_release_gate --strict-ci
```

PostgreSQL17 fixture tests cover approved exact-context reads, missing
approval, foreign project, unknown/unpinned Evidence/output, invalid UUID/SHA,
profile and source version staleness with historical replay, approved_partial
readiness, video bypass/pin integrity, registered local research-file
modification/deletion, no SQL writes, and no provider activity.

The test fixture cannot replace a paid production OpenRouter experiment or
video-platform ingest. No schema migrations, existing A code edits, or
legacy RTB behavior changes are introduced.

## Stage03 follow-up (do not begin without a new user command)

- Create the frozen `KnowledgeInputBundle` and persisted text fragment
  inventory from pinned profile fields, document segments and website page
  text rather than only bounded A Evidence; resolve source_role / subject_id
  with conservative routing and explicit unknown states.
- Add B1 source-level required/optional coverage and stable bundle hashes,
  avoiding silent omissions and duplicate B run state.
- Preserve `VideoAnalysisPin` for B9 separately. Later B3 uses exclusively
  permitted text fragments, never video/ASR/Vision.
