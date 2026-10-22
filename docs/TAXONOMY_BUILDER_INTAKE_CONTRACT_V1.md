> **Актуализация 22.10.2026:** Это исторический контракт отдельного intake-этапа. Полный Taxonomy Builder с ручными LLM-этапами и публикацией теперь реализован в `main`. Фразы «future» относятся к моменту проектирования. Подробности: [CURRENT_STATE_2026-10-22.md](CURRENT_STATE_2026-10-22.md).

---

# ContentMachine — Taxonomy Builder intake contract v1

**Scope:** preparation only. No automatic LLM calls, taxonomy generation or media analysis. This does not modify the existing 9-step wizard, downloaders or manual Discovery importer.

## Sources of truth

- Original user inputs and research files: local `project.json` plus `research/` (unchanged).
- PostgreSQL mirror: `project_registry` and append-only `project_profile_versions` from migration `003_taxonomy_intake.sql`. It is written only when `CM_DB_ENABLED` is enabled and migrations are applied.
- On unavailable PostgreSQL, editing/saving locally still works and `profile_db_sync` is `unavailable` or `migration_required`. The Builder **must not** assume successful DB synchronization.
- Historical projects without new fields are supported. `profile_revision` defaults to 1; new structured fields default to empty strings. On first taxonomy-input fetch, the current local revision is mirrored when PostgreSQL is ready.
- Binary research attachments are not copied into PostgreSQL. The Builder can read them via the existing local download endpoint, after checking their format and permission. Extracting their content is the Builder's task.

## Structured intake fields

The existing wizard retains all nine steps. Step 3 adds these optional fields alongside `product_description`:

- `product_type`: `mobile_app`, `digital_service`, `physical_product`, `service`, `course`, `no_product`, `other`, or empty. This is the initial enum for implementation; the full product-type taxonomy is not claimed to have been researched or finalized.
- `product_functions`: user's raw statement of actual product capabilities; don't invent other capabilities.
- `audience_problems`: user's raw description of audience pains, distinct from product functions.

All existing fields (`niche`, `goal`, `product_description`, `product_url`, competitors, social URLs, promoters, video references) remain available. Existing projects keep their untouched raw inputs. `goal` is retained for backwards compatibility; the Builder must not require it or treat it as the Discovery ranking objective.

## HTTP contract (localhost, same site)

- `POST /api/projects`: create, returns `profile_revision=1` and `profile_db_sync`.
- `GET /api/projects/{project_id}`: read complete editable raw profile.
- `POST /api/projects/{project_id}/update`: partial edit of allowed text fields and name. JSON must contain integer `expected_revision` from the last GET; stale writes return HTTP 409. Successful changes increment `profile_revision`. Research attachments are not edited by this endpoint.
- `GET /api/projects/{project_id}/taxonomy-input`: stable ready-to-consume payload:
  - `schema_version: 1`, `project_id` (original 32-char lowercase hex UUID), `profile_revision`, `profile_updated_at`, `profile_hash` (SHA256 of canonical contract data).
  - `profile`: all raw string fields listed above, including the three structured fields.
  - `research_documents`: `index`, `name`, `size`, `download_url`; never internal disk paths.
  - `missing_structured_fields`: optional fields still blank (information, not inferred facts).
  - `has_source_material`: true when useful intake text or documents exist; false means Builder should request information, not hallucinate.
  - `profile_db_sync`: `synced`, `disabled`, `migration_required`, `revision_conflict`, or `unavailable`.

## Builder integration

1. GET taxonomy-input and check `schema_version`, `project_id`, `profile_revision`, `profile_hash`, `has_source_material` and DB sync state.
2. Extract function/problem **proposals** with source-span evidence from user's words and allowed research documents. Preserve raw input. Never promote inferred product capabilities as verified facts.
3. Keep the resulting draft and human approvals in separate future Taxonomy Builder tables, keyed by `project_id` and frozen `profile_revision/profile_hash`. Do not overwrite `project_profile_versions`.
4. Publish immutable approved taxonomy snapshots only after user review. Any profile edit may make an old draft stale; prompt user to review changes.
5. Discovery's existing `topics` / `video_topics` are **global and not project-scoped**; don't use them as the new approved project taxonomy. The taxonomy-to-video relations require a separate future migration and execution pipeline.

## Setup / smoke check

Run `python -m intelligence.migrate` once on the existing local PostgreSQL. If PostgreSQL is disabled, projects still save locally and the Builder can read the HTTP contract, but PG-backed taxonomy cannot start.

Create a project or update one, then GET `/api/projects/{id}/taxonomy-input`: verify the three structured fields, revision, hash and sync status. Two revisions should appear in `project_profile_versions` after one real edit if PostgreSQL is enabled. Existing downloader and manual Discovery routes are unaffected.
