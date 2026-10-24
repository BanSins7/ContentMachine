# Project Source Intelligence — Stage 01 foundation

**Scope:** phase I / stage 01 of the 24 Oct 2026 source-intelligence plan. Contract and schema only; **no** fetchers, new endpoints, processing orchestration or changes to existing project/RTB behavior. Branch: `feature/project-source-intelligence-v1`.

## Audit and compatibility

- Baseline `main`: `8d9bee17402019a26bb4eaa27df040beb06153b9`. Existing migrations through `016_query_validation.sql`. Source migration intentionally named `030_project_source_intelligence_foundation.sql` to leave room for in-flight work; migrations are ordered lexicographically by filename, **not** by numeric sequence IDs.
- Old authoritative intake remains `projects.py` local `project.json` and `research/`; optional PostgreSQL mirror in `project_registry`, `project_profile_versions`. The existing `profile_hash` is **not recomputed** or replaced. New snapshots pin both `profile_revision` and its historical hash.
- No modifications to `app.py`, `site/projects.js`, Discovery, existing platform downloaders, ASR, or RTB prompts/cost ledger. Legacy 9-step onboarding, approvals, and published taxonomies continue unchanged.
- The global `sources` table describes provider permissions for Discovery. `project_sources` describes user-supplied per-project material and **does not imply** Discovery collection or performance evidence.

## Boundaries and future API contracts (not implemented yet)

| Component | Input | Output | Authority / stage |
| --- | --- | --- | --- |
| Source Registry | project_id + raw URL/field/link, rights | `ProjectSource` with stable UUID | stage 02; registration never fetches |
| File Library | uploaded user bytes | `MediaAsset` + `SourceVersion` + immutable bytes/hash | stage 03; no parsing on save |
| Source Processing | explicitly authorized version & budget | `ExtractionJob`, `source_processing_outputs` | stages 04–09; never inside HTTP request |
| Evidence Store | prepared source fragments | `ExtractedSegment`, `EvidenceRecord` + source locator | stage 10; claim is not an approved business fact |
| Context Snapshot | pinned profile, source versions and evidence decisions | `ProjectContextSnapshot` + relational pins + approval | stage 11; immutable, reproducible |
| Fact Extraction / RTB | explicitly approved contextual evidence | verified proposals -> human review -> approved facts | stage 12; old text-only input still supported |

Future compatible endpoints (not active in stage 01): `GET/POST /api/projects/{project_id}/sources`, source-specific PATCH/archive, version and extraction job status, context preview/compare/approve/publish. New handlers must use existing localhost host/origin/mutation protection and cannot accept arbitrary `project_id` references from request bodies without URL/project ownership checks.

## Canonical contracts and provenance

`intelligence/project_sources/contracts.py` defines strict Pydantic v2 models and machine-generated JSON Schema: `ProjectSource`, `SourceVersion`, `MediaAsset`, `ExtractionJob`, `ExtractedSegment`, `EvidenceRecord`, `VideoAnalysis`, `ProjectContextSnapshot`. Every DTO has an exact `/v1` discriminator. Nested types include `RightsPolicy`, `Locator`, `VideoSpan`, `SourcePin`. `json_schemas()` returns JSON Schema for downstream generators; no hand-maintained duplicate model definitions.

Locator formats: questionnaire field + exact char interval; PDF page; DOCX paragraph/cell; spreadsheet sheet + cell; web URL + selector/range; video measured time range/frame reference; raw text character range. Locators alone do **not** prove a quote: stage 10 must re-read immutable source content and independently verify bytes/quotes/timestamps. Video coverage is explicitly bounded; missing/unavailable is distinct from zero/false.

Evidence classification is mandatory: `user_claim`, `external_claim`, `observed_media`, `model_inference`; none is automatically an authoritative product fact. AI inferences require `needs_review`. Approval decisions use append-only review/approval tables rather than editing the evidence row.

## Relational graph and security

```text
project_registry ──< project_sources ──< project_source_versions
                         |                        |
                         |                        +──< project_media_assets
                         |                        +──< source_extraction_jobs → jobs (optional)
                         |                        +──< source_processing_outputs
                         |                        +──< project_evidence ──< project_evidence_reviews
                         |                                       |
project_profile_versions ──< project_context_snapshots ──────< project_context_source_pins
                                                    ├─────────< project_context_evidence_pins
                                                    └─────────< project_context_approvals
```

All project-child references are composite `(project_id, source_id/version_id/evidence_id)` where relevant, preventing cross-project relinking. `project_sources.active_version_id` is additionally scoped to the same source. Database triggers forbid changing a source’s project, stable ID, type, origin and original input even before its first version. Job identity and idempotency are also fixed after creation. Do **not** rely on UUID secrecy for authorization. Version/media/output/evidence/review/snapshot/pin/approval rows reject UPDATE and DELETE with immutable triggers; source registry is mutable only for metadata, rights, status and tombstones. No automatic cascading deletes of historic sources. Application service methods (later stages) must enforce actual permissions before reading private bytes or dispatching jobs.

`project_sources` grant flags `can_fetch`, `can_store`, `can_analyze`, `can_send_to_llm` default **false**. An explicit consent record and rights snapshot are required before paid/external work. Priced extraction jobs require a nonempty `consent_ref`, but **this is a contract/constraint, not an implemented financial authorization workflow**. Every executor must also check the applicable budget policy at dispatch. Permission revocations should block future use even if old versions remain for audit.

Source registration defaults to `registered` (no pending job). Processing statuses: `queued`, `processing`, `completed`, `partial`, `unavailable`, `failed`, `cancelled`, `needs_review`; `quality_status` additionally supports `unknown`. `initial_extraction_status` on an immutable source version records creation-time status; live processing must be derived from job/output records. Error codes are bounded; absent metrics and uncertain paid charges must never be interpreted as zero. Checkpointed stage outputs are append-only. A failed Vision step must not remove a successful transcript.

## Durable queue decision

Reuse `intelligence/jobs.py` only as a **candidate transport**, not by enqueueing automatically in stage 01. Existing `jobs.lease_next` is global (does not filter by job_type or project), `jobs.fail` retries automatically, and existing job schema has no source-specific consent/charge uncertainty gates. `source_extraction_jobs` introduces project-scoped durable records, a link to optional `jobs.id`, bounded attempts, idempotency and cost/consent metadata. Stage 06 must implement an explicitly registered dispatcher/worker that rejects unauthorized/revoked/uncertain paid tasks **before** invoking a provider, with leases, crash recovery and no automatic retry of ambiguous charges. A queue table alone is not a running worker.

## Migration policy and QA

Apply migration via `python -m intelligence.migrate`. The application checks SHA-256 of each migration; **never edit `030_...sql` after deploying it**. Forward changes must use additive new numbered migrations. Rollback in production means disabling new handlers (none activated yet) and applying a reviewed compensating forward migration: automatically dropping these tables could destroy pinned source history. A dry-run on an isolated database and restoration from backup are mandatory before production rollout.

Tests: `python -m unittest tests.test_project_sources_contracts -v` (offline); `CM_TEST_PG_DSN=... python -m unittest tests.test_project_sources_db -v` after migrations (PostgreSQL 17). Existing CI `python -m unittest discover -s tests -v` and Windows smoke remain authoritative for regression. Test results must be reported per exact SHA. Do not equate schema presence with working document/web/video extraction.

### E2E intended architecture (not implemented)

```text
Raw Project Intake ──> Project Source Registry [Deny by default]
                            │ explicit rights/consent + source version
                            ▼
                   Durable Source Processing
                     ├─ documents → normalized page/cell fragments
                     ├─ web → safe HTTPS segments
                     └─ video → existing downloader → ASR/OCR/Vision → timeline
                            ▼
              Evidence (kind + immutable locator + source version)
                            ▼
               human review / conflict verification
                            ▼
             immutable Project Context + manifest + approval
                            ▼
              optional evidence-aware RTB (old mode preserved)
```

**Known stage 01 limitations:** no CRUD API, source importer, file replacement, extraction engine, verified quote reconciliation, context builder, or project-worker. JSON schema validates shape, not verified truth; SQL pins ensure referential identity, not that `context_sha256` matches the JSON bytes. The stage 11 builder must compute canonical hashes and assemble/pin manifest and evidence atomically.
