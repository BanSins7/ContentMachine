# Phase I / Stage 10 — Evidence Normalization & Source Grounding

**Status:** implementation with exact source pinning, offline-only. No automatic LLM calls or RTB promotion.

## Contract

One `project_evidence.id` per **source-version + output + exact locator + exact quote**. Rows are append-only and project-scoped. A second independent source with identical text has a **different evidence_id** and citation. `project_evidence_exact_groups` groups exact Unicode NFC/whitespace/case-normalized duplicates without erasing separate citations or asserting semantic equivalence. `project_evidence_relationships` records potential lexical similarity and different versions of the *same questionnaire field* as reviewable candidates; it does not silently resolve contradictions. Semantic equivalence requires later human/LLM review and is never claimed here.

`project_evidence_grounding` records `input_output_id`, SHA-256 of the entire input JSON, quote SHA-256, locator SHA-256, source version and scope (`operational_product` / `competitor_research` / `creative_reference` / `general_research`), trust label, strictly typed value if present, and a stable internal citation. SQL composite FKs enforce matching project, source and pinned version. Immutable triggers prevent edits after publication. Results in `source_processing_outputs` include an immutable `evidence-normalization/v1` manifest.

Proof is **relative to the pinned locally stored extraction output**. For web results, Sonnet/OpenRouter's reported `page_text` is the only available comparison body; a matching quote does **not** establish that the actual remote site served the string. Model interpretation stays `model_reported_unverified` / `needs_review`. External/competitor claims and video aesthetics do not become first-party product functions or performance metrics.

## Supported inputs

- **Questionnaire:** explicit import of immutable `project_profile_versions(profile, revision, profile_hash)` primitive leaf values. Creates stable `questionnaire_field` sources per field path, with a new immutable source version per profile revision. Never changes `project.json` or user claims. Known secret-field names are skipped. Applies resource limits.
- **Documents:** exact text of `document-segments/v1` segment, with page/paragraph/cell/character locator and parser provenance. Invalid offsets are excluded with warnings.
- **Web:** only verbatim candidate quote substrings of persisted `web-source-extract/v1` model-returned `page_text`; strict saved offsets/hash. Model summaries and ungrounded claim descriptions are *not* evidence.
- **Transcript:** timed `transcript/v1` SRT/ASR segments; no fabricated audio timestamps or `no_speech` as proof of absent content.
- **Local OCR:** text from sampled frames in `vision-prepare/v1`, at the original frame sample's interval (<= 40 ms). No extension to entire scene.
- **Vision AI:** `visual-observations/v1` only after checking scene/frame references against its real immutable preparation output, including MP4 SHA-256, source version and exact selected frame; model observations need review.
- **Fusion:** `video-intelligence/v1` ledger quotes cross-checked against its embedded transcript/OCR/vision entries. This is a derived index, not independent evidence of a scene.

Typed extraction recognizes **only explicitly present standalone** percentages, dates and measured values with units. Unknown or omitted numbers remain `null`; **zero is never inferred from absence**.

## HTTP (localhost, same-origin protections from app.py)

| Method | Endpoint | Purpose |
|---|---|---|
| POST | `/api/projects/{project_id}/evidence/profile/import` | Explicit profile leaf import; `{}` or `{"dry_run":true}` |
| POST | `/api/projects/{project_id}/sources/{source_id}/evidence/enqueue` | Queue stored output normalization; `{"output_id":"uuid"}` optional, `allow_historical` optional, default false |
| GET | `/api/projects/{project_id}/sources/{source_id}/evidence/jobs` | Job history |
| GET | `/api/projects/{project_id}/sources/{source_id}/evidence/items?offset=0&limit=50` | Paginated fragments |
| GET | `/api/projects/{project_id}/evidence/items?offset=0&limit=50` | All project evidence |
| GET | `/api/projects/{project_id}/evidence/{evidence_id}/verify` | Fresh hash + exact quote check against pinned stored input |
| POST | `/api/projects/{project_id}/evidence/{evidence_id}/review` | Immutable human decision `{ "decision": "approved" | "rejected", "reason":"..." }` |
| POST | `/api/projects/{project_id}/sources/{source_id}/evidence/jobs/{job_id}/retry` | Explicit free retry after failure, limited to 3 attempts |

All access is scoped to project. A stale source version is rejected for new queue by default; explicit historical input may be normalized but **cannot be newly approved** after a newer active version. A human decision is not an RTB fact/publication and cannot promote third-party aesthetics to verified product claims. Partial/unavailable source outputs are not described as complete. No copyright permission gate is added.

## Execution

```bash
python -m intelligence.migrate
python -m intelligence.project_sources.evidence_jobs --poll 2
```

`--once` processes one job. No paid calls, internet access, download or media decoding. The UI mounts a source-level Evidence panel and a project-level profile import action. The dashboard can be refreshed while workers run. If no extracted output exists, Stage 10 explains that an earlier-stage extraction must be completed first.

## Follow-up boundaries

Stage 11 creates immutable approved Project Context snapshots **from pinned evidence**. Stage 12 adapts RTB fact validation using evidence IDs. Neither is triggered at Stage 10. Existing Group A/B schemas and legacy RTB are not mutated.

## QA

Unit tests: document offsets, web forged quotes, sampled frame timing, strict Vision references, source classification, typed values, normalized/exact duplicate semantics, partial/unknown. PostgreSQL integration: independent citations, retries and idempotency, immutable review, old profile revisions, possible conflict flags, wrong-project access, explicit historical pin; no external paid calls. Both Linux and Windows CI run migration `039` against PostgreSQL 17.
