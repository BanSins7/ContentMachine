# Project Source Intelligence — Stage 11: Project Context Assembly & Immutable Snapshots

Implementation: `intelligence/project_sources/context_builder.py`, `context_service.py`, `context_http.py`, additive PostgreSQL migration `040_project_context_assembly.sql`, browser `site/project-context.js`.

## Purpose and boundary

Build a **versioned, deterministic Project Context v1** from the **already stored** project profile, reference-source versions, extraction/analysis outputs and Stage 10 grounded Evidence. Stage 11 never calls an LLM, downloads or modifies a document/video, publishes taxonomy, writes Group A/B, or changes `/taxonomy-input`, `/confirmations` or historical project.json. No copyright/rights processing gates are activated.

### Exact, immutable relationships

The Stage 01 tables `project_context_snapshots`, `project_context_source_pins`, `project_context_evidence_pins`, `project_context_approvals` remain canonical. Migration `040` extends them with:

- `project_context_analysis_pins`: exact source/version/output/SQL SHA-256 and output schema, with composite FKs to pinned sources and original extraction outputs;
- `project_context_grounded_evidence_pins`: exact source/version/evidence, quote and locator hashes, with FKs to original evidence and the Stage 10 grounding record;
- `project_context_build_inputs`: immutable input fingerprint and builder version.

All three additions have **immutable update/delete triggers**. No old migration checksums or Stage 10 records are rewritten. A Stage 11 build stores `context_sha256 = SHA256(canonical UTF-8 context JSON)`; this hash excludes the context UUID, snapshot creation time and later human approval. Canonical JSON uses sorted keys, compact separators and Unicode text. Re-reading a snapshot recomputes the context digest, verifies the manifest against stored JSON, checks **every persisted source/output/evidence pin**, and recomputes the output JSON and evidence quote/locator digests. A missing/mismatched pin refuses the read; an old snapshot cannot silently reference a new file or different ASR/Vision output.

### Context JSON v1

`context_payload` contains:

- `schema_version`, `builder_version`, project ID;
- pinned immutable profile revision/hash and allowlisted, safety-redacted onboarding fields (niche, goal, product etc.);
- `source_manifest`: source ID/type, safe URL, version ID/no, content hash plus **explicit hash provenance** (`source_byte_sha256` vs `model_reported_text_sha256` vs `not_captured`), MIME, bytes, recorded date, processing state, analysis refs, Evidence counts and missing/excluded reason;
- `analysis_refs`: exact output IDs, output SHA256, schema, engine/model and config hash, recorded date, status. There are no raw local filenames, storage refs or copied binary bytes;
- `evidence_manifest`: *all* pinned Evidence IDs and origin/version/output, knowledge scope, kind, trust, citation, quote/locator SHA256 and snapshot-time human review decision. Human approval is not automatic independent verification of external facts;
- `approved_evidence_ids`: those explicitly approved in Stage 10, still carrying their original trust and scope;
- `unavailable_or_excluded_sources`, typed coverage counts, warning list and reviewable relationships.

For an OpenRouter-fetched website, the SHA belongs to **model-returned text**, *not the original site's HTTP bytes*. Speech from ASR and isolated OCR/Vision frame descriptions are not elevated to video-wide or first-party product facts. Sources with no grounded Evidence are explicitly represented with `no_grounded_evidence`, not silently omitted. Archived sources are marked `source_archived` in the manifest, not erased. If a bound cannot be met (180 sources, 750 outputs, 3000 Evidence records, 5 MB full JSON), the build fails **without trimming provenance**.

## Workflow

1. User imports profile fields into Evidence and explicitly processes sources with Stages 03–10. Each output and review is append-only.
2. `GET /api/projects/{pid}/context/draft` reads one repeatable-read DB snapshot and calculates a **non-mutating** preview, excluded sources and `context_sha256`.
3. User reviews the preview and calls `POST /api/projects/{pid}/context/build` with `{"expected_context_sha256":"<hash from draft>"}`. Build locks the project record, rereads the inputs and rejects mismatching preview (`409 stale_preview`). If an identical content hash has already been snapshotted, it returns that existing context (idempotent); otherwise it atomically creates the next `context_version` and every immutable pin.
4. `GET /api/projects/{pid}/context/snapshots` lists historical snapshots, `.../{context_id}` returns the whole immutable JSON, `.../manifest` gives the source/output/evidence ID manifest. `GET /context/compare?left=<id>&right=<id>` provides added/removed/changed sources, outputs and evidence and a profile-change flag.
5. `POST /api/projects/{pid}/context/snapshots/{id}/decide` stores exactly one immutable human decision (`approved`, `approved_partial`, `rejected`). Body must contain `decision`, `expected_context_sha256`, `acknowledge_partial` boolean and **all exact** `excluded_source_ids`. Full approval is prohibited while any source is excluded. `approved_partial` requires at least one excluded source and explicit acknowledgement. Published approval is rejected if the profile, source version, output or review inputs have changed since build. The review record allocates an immutable `approval_id`, returned in API alongside the separately frozen context hash — not injected back into hashed context JSON. Rejected snapshots are retained.
6. `GET /context/approved` returns the latest approved/approved_partial snapshot for downstream use. Stage 12, not this stage, will bind it to Taxonomy Builder.

### Evidence retrieval and token budget

`GET /api/projects/{pid}/context/snapshots/{id}/selection?token_budget=1200` produces a compact view of **only pinned, unrejected** evidence and actual citation texts with `evidence_id` / trust/scope. Reviewed product evidence is ordered before unreviewed or external statements. The heuristic reserves `~3 characters/token`, bounded to 100–8000 requested tokens, not a guaranteed model tokenizer. It never modifies or discards IDs from the canonical full immutable snapshot. Subsequent semantic retrieval can add embeddings without invalidating old context hashes. **Do not use the selection output as a replacement for full provenance.**

### Runtime

No separate worker is required: preview, create, review and export are bounded PostgreSQL-only operations. Existing localhost/Origin and `X-ContentMachine` protections of `app.py` apply. Project IDs and UUIDs are validated and SQL access is always scoped to a project. No keys, authorization header values, source filesystem paths or binary assets are included in the export; nested unrecognized profile keys are omitted and token-like key/value pairs redacted.

### QA

`tests/test_context_builder.py`, `tests/test_context_http.py`, `tests/test_context_db.py` cover deterministic hashes, pinned analysis/evidence and byte-level manifest, rejected forged output digest, profile secret/path redaction, exact partial approval, explicit human confirmation, idempotency, reproducibility of historical versions after profile edits, refusal of stale preview/snapshot and cross-project denial. CI runs the complete project regression suite with ephemeral PostgreSQL 17 on Linux/Windows and local Windows D1 smoke. No real paid APIs or downloads.

### Known limits

- No automatic AI summary, vector similarity or semantic equivalence classifier in Stage 11. Selection currently uses a deterministic prioritization; future semantic retrieval must preserve the same immutable references.
- `approved` is approval of **the context selection and disclosed coverage**, not the truth of arbitrary third-party claims or the creative judgement of Vision AI.
- If a project contains more material than the hard bounds, increase the source selection/retrieval architecture before producing a full snapshot; **do not silently truncate**.
