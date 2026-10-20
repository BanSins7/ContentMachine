# Discovery / Semantic Relevance Engine — implementation foundation (v0.1)

This document maps the 2026-10-19 Discovery specification to the **existing**
ContentMachine Python application. This branch is a **non-running foundation**:
it creates database schema, pure model contracts, a testable stage executor,
fact/taxonomy review persistence and benchmark primitives. No real LLM/API
requests or production classification jobs are started by these files.

## What already exists (unchanged)

- `projects.py`: local-only, nine-step JSON project wizard, UUID.hex IDs.
- `intelligence/migrations/001_foundation.sql` through `005_*.sql`:
  D0/D1 infrastructure, global video catalog and source provenance.
- `intelligence/discovery/`: offline/manual imports; existing D1 routes.
- `site/`: downloader and discovery UI. No SRE UI has been wired yet.
- `intelligence/migrate.py`: applies every numbered SQL file in order,
  checksum-guards installed migrations, and serializes using advisory lock.

## Architectural boundaries

```
Existing project wizard (local project.json, not yet synchronized to DB)
    |
    v
Project Intelligence Workspace / sre_workspaces (new project-scoped records)
    |
    +-- sre_profile_versions ---- sre_facts ------ human review
    |
    +-- sre_taxonomy_versions -- sre_taxonomy_categories -- approval / immutable vN
    |
    +-- sre_project_videos --> existing videos / D1 origin + provenance
    |                              |
    |                              +-- sre_content_versions (content + provenance)
    |
    +-- sre_pipeline_configs ---- pipeline runner / adapters
    |                                      |
    |                                      +-- sre_classification_runs/results
    |
    +-- sre_human_labels + sre_experiments + sre_experiment_runs
```

Global `videos` and `discoveries` remain canonical to avoid duplicating
data collection. **Project-specific decisions and versions are never global**.

## New files

- `intelligence/migrations/006_discovery_sre_workspace.sql`: additive schema.
- `intelligence/relevance/contracts.py`: validation, typed stage contracts.
- `intelligence/relevance/builder.py`: proposals with source-quote and approved-fact constraints.
- `intelligence/relevance/fact_review.py`: review/persist facts inside project scope.
- `intelligence/relevance/repository.py`: project workspace, taxonomy draft/review/approval,
  immutable pipeline configuration records, and project context.
- `intelligence/relevance/pipeline.py`: modular synchronous executor with injected
  adapters. It performs NO network/model calls itself.
- `intelligence/relevance/presets.py`: cascade and strong-only template configurations.
- `intelligence/relevance/experiments.py`: comparison against human ground truth.
- `tests/test_relevance_core.py` and `tests/test_relevance_postgres.py`.

## MVP product rules

- **Niche:** `health_fitness` only.
- **Goal:** fixed `maximize_views`; popularity is separate from semantic relevance.
- **Product type:** structured choice; final allowed options are NOT yet agreed.
- **Functions and audience problems:** free-form text, raw text preserved.
- **No mandatory separate result field. No CTA field in the Discovery scope.**
- Strong LLM generates *proposals*, not approvals. A fact proposal's original
  quote must exist verbatim in its corresponding input field.
- Approved facts precede topic proposals; human explicitly approves topics.
- A taxonomy category has a stable UUID, definition, include/exclude,
  positive/negative examples, type (direct/problem/adjacent), and fact links.
- Active approved taxonomy versions cannot be mutated. New versions are created
  as drafts. Database trigger also guards direct SQL updates of approved rules.
- `approved taxonomy` = human intent, NOT evidence of LLM classification accuracy.
  `sre_human_labels` = benchmark labels for accuracy measurements.
- Video verdicts: `match`, `no_match`, `uncertain`; pipeline also uses
  `not_evaluated` when a gate skipped a category. Skipping is NOT a negative.
- D1 origin platform (TikTok/YouTube/Instagram) and collection provider
  are separate; source policies and permitted retention still apply.

## Model providers / Pipeline Studio

Every stage is a replaceable `model_ref` with a caller-supplied adapter
`fn(stage, video, category_subset)`. Adapter raw results are **not uniform**:

- `embedding_retrieval`: `{category_key: cosine_similarity}` for all candidates.
- `llm_classifier`: `{category_key: {decision, evidence}}`.

The Pipeline Engine normalizes classification labels and stores stage traces.
It does not manufacture a Boolean label from similarity alone.

Embeddings are a **ranking-only stage by default**. Dropping candidates requires
explicit `allow_candidate_drop: true` and `calibrated: true`. The calibration
flag is developer-asserted, NOT proof of measured quality; it must be backed by
tests before enabling production. Dropped categories become `not_evaluated`.

Stage compatibility checks prevent embedding retrieval after classifiers or
`on_uncertain` routing without a prior classifier. Users can reorder compatible
stages, enable/disable them, choose model refs and save configs. The visual
Pipeline Studio editor is still to be built.

The presets `cascade()` and `strong_only()` include **placeholder model refs**:
they do not imply actual credentials or API integrations. Model registry rows
are unavailable by default. A real execution service must check the configured
model's capability, resolve exact provider/model/prompt versions, enforce
budget/permissions, log latency and costs, and only then call providers.

## Example for testing the executor (no external calls)

```python
from intelligence.relevance.presets import strong_only
from intelligence.relevance.pipeline import run_pipeline

category = {
    "category_key": "a49c1e4a-91bd-4a95-8c1d-120da90ae57b",
    "name": "Ошибки при похудении",
    "relation_type": "problem_related",
    "origin": "inferred",
    "independent": True,
    "definition": "Разбор конкретных ошибок, мешающих похудению",
    "rules": {
        "include": ["Объясняет ошибку"],
        "exclude": ["Просто личная история"],
        "positive_examples": ["Не учёл калории напитков"],
        "negative_examples": ["Мой дневник тренировок"]
    },
    "related_fact_ids": []
}

def mock_model(stage, video, cats):
    return {c["category_key"]: {"decision": "match", "reason": "title is a match",
      "evidence": [{"source":"title","quote":"Ошибки в рационе"}]}
            for c in cats}

result = run_pipeline(strong_only(), {"title": "Ошибки в рационе"}, [category],
                      {"strong_llm": mock_model})
assert result["labels"][category["category_key"]]["decision"] == "match"
```

## Running tests locally

```bash
python -m unittest tests.test_relevance_core -v
python -m unittest discover -s tests -v
# DB integration (only against isolated, disposable test database):
CM_TEST_PG_DSN=postgresql://... python -m unittest tests.test_relevance_postgres -v
```

The PR CI workflow applies SQL migration 006 to a disposable PostgreSQL service,
runs Python unit tests, and checks syntax automatically.

## Current development status / work still required

- [x] Additive PostgreSQL tables with cross-project foreign keys.
- [x] Fact/topic human review persistence, immutable approved taxonomy.
- [x] Validated configurable executor, cascade/strong-only definitions.
- [x] Repeatable label-based benchmark primitives, mocked offline tests.
- [ ] Link existing nine-step project wizard to the new minimal Health & Fitness
      fields; migrate prior local project.json records without losing uploads.
- [ ] Explicit, authenticated project access authorization before external HTTP
      endpoints (existing application is loopback-only). DB role/RLS as needed.
- [ ] Provider integration for strong taxonomy builder, embedding, fast & strong
      classifier; versioned prompts, token/cost accounting, real model registry.
- [ ] Implement Pipeline Studio UI and versioned configuration CRUD endpoints.
- [ ] Build storage-backed, queued SRE runner and append-only result persistence.
- [ ] Connect D1 collected transcripts/captions, media-rights and content revisions.
- [ ] Connect D2 search planning; still distinct from taxonomy and classification.
- [ ] Create benchmark of human-labeled real videos and calibrate thresholds.
- [ ] Optional pgvector indexing/versioning when embedding volumes justify it.
- [ ] Decide separate per-project DB vs shared database; this branch uses shared
      PostgreSQL with explicit project scoping, not a final infrastructure decision.

Do not merge as a claim that automatic Discovery and full SRE are operational.

## Structure hardening after SRE architecture audit (20 October 2026)

This PR remains a **foundation**, not a working LLM product. The following structural defects have been corrected:

1. `problem_related` is canonical (legacy SQL `problem` remains permitted for backward data compatibility); every category stores `origin`, `independent` and a versioned project context.
2. Topic-to-fact links now have real project-scoped PostgreSQL FKs and require previously approved facts, except explicitly independent topics.
3. Examples use `example_kind=illustrative|verified_video` with project-video FKs. LLM-created proposals cannot claim verified-video examples.
4. Classification `match` requires structured evidence `{source,quote,...}` verified against actual supplied video text/segments. It is a provenance check, **not semantic truth verification**.
5. Missing/invalid LLM outputs raise an explicit error. Provider failures are distinct from semantic `uncertain`.
6. Embeddings rank all categories even with filtering disabled. Rank-only is **not** a cost-saving filter; dropping candidates requires explicit benchmark metadata and records `not_evaluated`.
7. Workspace creation requires an existing local `projects.py` manifest; future HTTP endpoints still need authentication/authorization.
8. SRE content revisions require D1 source policy, permissions, and compatible media assets for transcripts/scenes; future readers must enforce expiry and cleanup.
9. Approved taxonomy versions, their fact links/examples and saved Pipeline configs are immutable. Results reference the same taxonomy version as their run.
10. `intelligence/relevance/storage.py` contains project-scoped operations for saving run snapshots, results and human correction history, without executing provider calls.

Additional migration files: `007_sre_structure_hardening.sql`, `008_sre_results_and_policy_integrity.sql`, `009_sre_reason.sql`.
Regression tests: `tests/test_relevance_core.py`, `tests/test_relevance_postgres.py`, `tests/test_relevance_storage.py`.

**Not yet built:** UI, real model adapters, API/authentication, D2 search, queue worker, embedding calibration, video ingestion, experiments with real prices/latencies. None of the 15 original end-to-end acceptance scenarios should be marked completed on this branch.
