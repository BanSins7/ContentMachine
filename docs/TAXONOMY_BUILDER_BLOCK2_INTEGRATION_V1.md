> **Актуализация 22.10.2026:** этот документ фиксирует состояние отдельного этапа разработки, а не текущий статус ветки. PR #7 (Taxonomy Builder) уже **merged** в `main` 21.10.2026, PR #8 добавил workspace UI, PR #9 — иерархический двухпроходный режим. Утверждения ниже о draft PR #7 и отсутствии последующих функций — **исторические**. Актуальное состояние: [CURRENT_STATE_2026-10-22.md](CURRENT_STATE_2026-10-22.md).

---

# Taxonomy Builder — Block 2: Questionnaire confirmation and handoff

**Status:** implemented in draft PR #7, no LLM calls or cost estimate.
**Parent design:** `docs/TAXONOMY_BUILDER_BLOCK1_ARCHITECTURE_V1.md`.
**Reuses:** `projects.py`, `site/projects.js`, `project_registry`, `project_profile_versions`; downloader and Discovery D1 remain unchanged.

## Actual user flow

1. Create or edit an existing project with the nine-step wizard (unchanged). Saving still works without PostgreSQL.
2. Project automatically opens **«Проверь данные проекта»**, which rereads the server-authoritative taxonomy-input snapshot. Existing projects use the action **«Продолжить к Taxonomy Builder»** from project details.
3. The confirmation screen shows an explicit list of missing Health & Fitness, product type, audience problems and product functions; `no_product` exempts product functions.
4. If PostgreSQL is connected and all constraints pass, pressing **«Подтвердить анкету»** sends both revision and SHA-256; it cannot approve a stale local version or the wrong project.
5. Confirmation is stored in `tb_intake_confirmations`, attached by a composite FK to `project_profile_versions`. Double clicks are idempotent, SQL update cannot silently rewrite approval.
6. A confirmed current version opens the separate `projectPipeline` screen. **Only the preview exists**: model settings and approval arrive in Blocks 3–4. Refreshing/revisiting requires server reread of the active version.
7. Editing profile increments revision, invalidates current approval (old revision remains recorded); the review must be repeated before the Studio preview unlocks.
8. If database is disabled, down, unsynchronized, or migration missing, the review screen shows a concrete blocker and does not allow approval. Saving the source questionnaire always remains possible.

## Endpoints

- `GET /api/projects/{project_id}/taxonomy-builder/intake`:
  `project_id,project_name,profile_revision,profile_hash,profile,research_documents,profile_db_sync,blockers,database_blocker,confirmed,can_confirm,next_step,pipeline_studio_available,pipeline_configuration_available`.
  `pipeline_configuration_available` is **always false in Block 2**, never fake.
- `POST /api/projects/{project_id}/taxonomy-builder/intake/confirm`:
  `expected_revision` integer, `expected_hash` lowercase 64-character hex, localhost mutation header. Returns the same authoritative view.
  Status **404** unknown project, **422** incomplete Builder intake, **409** stale/mismatched profile or DB revision conflict, **503** unavailable PostgreSQL, **200** success/idempotent.

## Storage

- Additive migration `004_tb_intake_confirmations.sql` includes a unique key on `project_profile_versions(project_id,revision,profile_hash)` and `tb_intake_confirmations` with exact snapshot composite FK, immutable UPDATE trigger and timestamp.
- We intentionally **do not** create separate taxonomy, model or cost tables in Block 2. Those belong to future blocks and must reconcile with separate SRE PR #6.
- DB approval is scoped by `project_id` and the application only binds on `127.0.0.1` with Host/Origin checks and a custom POST header. If remote/multi-user access is later introduced, authenticate and authorize ownership before reusing this endpoint.

## Test coverage

`tests/test_taxonomy_builder_intake.py` tests:
- Saved incomplete projects remain valid drafts and cannot be confirmed.
- `no_product` special case; unsupported niche blocked.
- DB off: fail closed, no false confirmed state.
- Invalid project IDs, localhost HTTP routes and missing mutation headers.
- PostgreSQL integration: confirmation/idempotency, preserving old version history,
  edit invalidation, stale 409, isolation across projects, successful handoff.
- Existing project/download/Discovery tests remain in the shared test suite.

Test commands:
```bash
python -m unittest discover -s tests -v
node --check site/projects.js
python -m compileall -q app.py projects.py intelligence/
```

## Not part of Block 2

No actual model registry, reasoning/temperature settings UI, LLM calls, estimation, approval of model configuration, payment consent, taxonomy generation or pipeline runner. The Pipeline screen is **deliberately a preview, not an inert fake action**. Progression to these functions requires Blocks 3–6.

**Stop after Block 2.** Wait for user approval before implementing Block 3.
