# Project Source Intelligence — Phase I, Stage 03: Research File Library

Scope: A4 Research File Library + A5 metadata/safe-file checks + A6 binary storage. **No parser, OCR, web fetch, Sonnet/OpenRouter, video downloads, processing-job dispatch, copyright checks or automated profile edits.** Stage 04 owns document extraction. Stage 05 owns Sonnet 4.6/OpenRouter Web Fetch; Stage 03 never sends a URL or document to a model.

## Storage and records

- Legacy `/projects/<project_id>/project.json`, `research/<stored_name>` and `/api/projects/<id>/files/<index>` remain unchanged. The explicit `import-legacy` operation records existing files under a deterministic UUIDv5 derived from project ID and stored filename. It does **not** change the questionnaire, its hash, revision or files on disk. Existing legacy download URLs stay valid.
- New uploads/replacements use `research_library/<2 hex>/<file UUID>/vNNNNN_<SHA256>.bin` under the existing project directory (outside Git). The path is entirely generated and is independent of user-controlled filenames.
- `project_research_files` owns stable UUID identity, project, source and current-version pointer, and archive state. `project_research_file_versions` appends immutable version rows, exact SHA-256, original name, MIME, size, uploader, eligibility, local asset path, source version, media-asset FK and timestamps. Migration `032_project_research_file_library.sql` is additive. Previous migrations are never edited.
- Each version creates a `project_source_versions` row and `project_media_assets` row. The version is not marked `document_parse: completed`; parser stage is a separate operation. Source and content provenance remain stable for future `project_context_source_pins`. Replace never overwrites an old asset, preserving context pins and historic downloads.
- Files are stored locally, outside PostgreSQL; PG keeps metadata, pointers and unique constraints. PostgreSQL must be enabled for management endpoints. Errors return unavailable rather than pretending an uploaded document was registered.

## API (existing localhost/Host/Origin/X-ContentMachine protection)

- `GET /api/projects/{id}/research-files?include_archived=0|1` — list metadata, active versions, integrity status `ready|missing|corrupt`, limits.
- `GET /api/projects/{id}/research-files/{file_id}/versions` — immutable history.
- `GET /api/projects/{id}/research-files/{file_id}/download[?version=1]` — original bytes with SHA-256 verification and `nosniff`/attachment headers. Archiving does not disable historical version retrieval.
- `POST /api/projects/{id}/research-files` — upload `{name,data_base64,uploaded_by}` (201).
- `POST /api/projects/{id}/research-files/{file_id}/replace` — same plus mandatory `expected_updated_at`, creates version N+1 (201).
- `POST /api/projects/{id}/research-files/{file_id}/archive` / `/restore` — `{expected_updated_at}`, soft state change (200).
- `POST /api/projects/{id}/research-files/import-legacy` — optional `{dry_run:true}`; idempotent explicit import, no bytes rewritten.

All paths are project scoped, and file UUIDs cannot be used across projects. A stale timestamp yields HTTP 409; invalid signature/filename 422; unavailable PG 503. API is local: no paid network side effects. UI sits in the existing project detail view; nine-step intake is unchanged.

## Safety, limits and failure model

- Maximum **10 MiB per file**, **50 active files per project**, **100 MiB on-disk project document bytes, including historic versions and original legacy research**, **100 versions/file**, **14 MiB POST JSON**, plus **100 MiB required free disk reserve**. Limits apply to actual uploaded content and do not increase the old wizard's limits (10 files, 24 MiB total for initial upload).
- Strict extension allowlist; no path separators, `..`, device/control characters, or symlinks. PDF/PNG/JPEG/OLE Office magic and DOCX/XLSX/PPTX archive structure are checked. Archives reject path traversal, symlink entries, encryption, extreme compression and excessive uncompressed bytes. Text rejects obvious binary data. Extension is never trusted alone. These are **technical file-safety checks, not rights/license gates**.
- `uploaded_by` is an audit label of the local uploader, not authenticated multi-user identity; the app remains a local single-user application.
- Upload path: validate bytes -> acquire project-row lock -> check quotas -> write private temp file -> fsync -> atomic rename to unique immutable final name -> commit source/asset/version rows in one PG transaction. On SQL failure, roll back and best-effort remove newly written bytes; **no successful response** is returned. After a process crash between rename and commit, orphan bytes are possible, but no committed DB row points to non-durable data.
- For orphan audit: `python -m scripts.research_files_repair <project-id>`; to quarantine unreferenced bytes older than 10 minutes: add `--quarantine`. Default scan is read-only. Quarantine moves orphan bytes, never deletes referenced versions or legacy files. Missing/corrupted registered bytes remain visible as `missing|corrupt` for operator repair; they are not silently ignored.
- PostgreSQL-backed source versions and uploaded bytes are append-only; tombstoning a file only changes the active registry record. This stage does **not** alter frozen project-context snapshots or RTB profile revision/hash.

## QA

Tests: `test_research_file_library_unit.py`, `test_research_file_http.py` (offline), `test_research_file_library_db.py` (PostgreSQL 17 integration). Assertions cover file signature validation, malformed archives, unsafe names, symlinks, legacy import, add/replace/archived/download history, optimistic conflicts, immutable version rows, failing SQL rollback, and project isolation. CI runs Linux PostgreSQL and Windows D1 smoke without remote providers. All success claims must be tied to actual CI runs.
