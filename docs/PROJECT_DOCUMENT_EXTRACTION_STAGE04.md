# PSI Phase I / Stage 04 — Document Extraction Engine

**Status:** Implemented in feature branch (not merged), no paid AI calls or web/video handling.

## Contract and provenance

`project_research_file_versions` from Stage 03 is authoritative for `file_id`, `source_version_id`, SHA256, MIME and immutable disk path. An explicit request enqueues an idempotent `document_parse` entry in the existing `source_extraction_jobs` table. A separate local worker leases with `FOR UPDATE SKIP LOCKED` and persists a `source_processing_outputs` record, tied to the exact project/source/version/job by database foreign keys. Existing file bytes, `project.json`, profile revision and old RTB runs are unchanged. Parser outputs are not facts, not approved evidence and not LLM results.

The output is JSON `document-segments/v1` with `parser_version`, `status`, ordered `segments`, `tables`, `coverage`, `warnings` and `extraction_engine`. Each segment contains exact extracted string and a locator. PDF: 1-based page + native text block + approximate reading order/bbox. DOCX: sequential paragraph index/heading section or table index + row/column. TXT/MD: line + file character offset. CSV/XLSX: sheet and cell. Per-segment `[start_char,end_char)` range refers to the original text of that identified block/paragraph/cell, *not* to an invented cross-document offset. OCR segments are explicitly flagged, their confidence is `null` when not measured. No implicit LLM summaries.

## Supported inputs

- PDF: PyMuPDF native text blocks/page, best-effort `find_tables`. Scanned or blank pages show partial coverage if no text is found. Optional `ocr=true` attempts **local** Tesseract on pages with insufficient text. Missing Tesseract or language packs → `partial`, never silently `completed`. Approximate block-based reading order is labeled.
- DOCX: `python-docx`, sequential paragraphs/headings/lists, table cell coordinates.
- TXT/MD/CSV: BOM-aware UTF-8 / UTF-16 / Windows-1251, exact lines and decoded character offsets; CSV preserves row/cell structure and delimiter.
- XLSX: `openpyxl` in `read_only` and no macro/links mode, formulae **never executed**. If cached value is unavailable, cell value is explicitly `неизвестно`, with original formula and `formula_executed=false`.
- Legacy `.doc`/`.xls`, PPT/PPTX, JPG/PNG: explicitly `unavailable` / `unsupported_format` (only the original is stored). No pretend success.

## Security/performance boundaries

- Same-file Stage03 MIME/signature/Office ZIP safety and 10 MiB maximum retained. Parser executed in a child process (`spawn` on Linux and Windows) with 60-second wall time and best-effort POSIX address-space limit; HTML/remote URLs are not supported.
- PDF up to 100 pages, XLSX 32 sheets / 2000 rows / 80 columns, DOCX max paragraphs/tables, 1800 normalized fragments, 3 MiB result payload. Limits result in `partial` with warning, not misleading completion.
- Child crash/timeout/corrupt source is `failed`, with error code. Files verified again with SHA256 before processing.
- Cache keyed by `(project_id, binary_sha256, parser_version, hash(config+file_extension))` so identical material with different parsing modes is not conflated. Cache immutable. Cross-project data is never served from a cache entry of another project.
- Worker attempts fenced; no paid provider calls; no auto background processing merely by creating a project. Retries only via explicit API request or reclaim after worker lease expiration.

## Runtime

Install: `python -m pip install -r intelligence/requirements.txt` (including PyMuPDF, python-docx, openpyxl, pytesseract). Optional for scanned PDF: install local Tesseract executable with `rus` and `eng` language packs; no OCR network service is invoked.

Run migrations: `python -m intelligence.migrate`.

Start worker in second terminal: `python -m intelligence.project_sources.document_jobs --poll 2`. For just one queued item: `python -m intelligence.project_sources.document_jobs --once`. The local web server alone only enqueues.

Project API (all under `/api/projects/{project_id}/research-files/{file_id}`):

- `POST /extract` with `{ "version": 1, "config": { "ocr": false } }` (optional version means active). HTTP 202; idempotent on source version/parser version/config.
- `GET /extractions` returns job list and lifecycle.
- `GET /extractions/{job_id}?offset=0&limit=60` returns bounded segments, coverage, warnings, source/version hashes.
- `POST /extractions/{job_id}/retry` with `{}` requeues failed/unavailable jobs only, subject to `max_attempts`.

File UI exposes enqueue + status/result preview; old 9-step questionnaire and downloads keep their current endpoints.

## QA / limitations

Offline fixtures: multi-page native PDF with empty page, Cyrillic text, DOCX table, XLSX with unknown formula cache, CSV encodings, long data, corrupt inputs, deterministic output, and child-process isolation. PostgreSQL 17 integration: idempotency, two document versions, same-project cache reuse, bytes integrity failure, no cross-project access. CI runs Python + Windows smoke + existing taxonomy/UI regressions. OCR accuracy is environment/language-package dependent, and reading order for complex multicolumn PDFs is approximate. Document processing has not been connected to RTB fact extraction (Stage 10–12).
