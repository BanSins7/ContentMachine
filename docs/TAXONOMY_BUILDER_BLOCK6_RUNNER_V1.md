# Taxonomy Builder — Block 6: live runner, human approval gates and budget accounting

**Status:** implementation in draft PR #7. Uses the same project, intake, model and quote snapshots introduced in Blocks 2–5. **Does not merge into main**. Real-money test remains manually opt-in; normal tests are fully mocked.

## Workflow and manual interventions

1. Confirm exact questionnaire profile; save and approve the per-stage Pipeline (Fact Extraction, Taxonomy Generation, optional Critic).
2. Estimate cost, explicitly consent to max USD, and separately press **Создать запуск**. Creating a run uses no external API.
3. Press **Выполнить следующий LLM-этап** to send exactly one Fact Extraction request. OpenRouter must return JSON and usage cost.
4. Review each extracted fact: accept, edit, reject, or add one new human-authored fact. Raw source quotes must occur exactly in the frozen profile; model-proposed facts are not silently accepted.
5. Press **Выполнить следующий LLM-этап** for Taxonomy Generation. The generator only receives approved facts, plus fixed Health & Fitness / maximize views objective, and must ground direct/problem topics in fact IDs.
6. If Critic was enabled, press the button once more to run its review-only request. Its recommendations do **not** directly modify the taxonomy.
7. Review proposals, edit names, reject/accept categories, then publish an immutable Research Taxonomy version.

The app never auto-chains paid requests. Users can leave and return to a run using the locally stored run ID. Cancelling stops future work; a currently running or uncertain request cannot safely be cancelled/retried.

## Provider and budget

- OpenRouter Chat Completions, one call per stage, JSON schema response format, **provider.require_parameters=true**, allow_fallbacks=false.
- Per-stage chosen model, effort, supported temperature and max output tokens are transported without silently dropping settings.
- Exact non-streaming usage.cost, prompt/completion/reasoning tokens and provider response ID are persisted. Usage missing or response network uncertainty leaves the funds reserved, sets status uncertain, and blocks paid retries. Do not treat a timeout as free.
- Each consent authorizes **exactly one run** (migration `009_tb_single_run_per_consent.sql`). Starting another run requires a new quote and separate human approval; idempotently reopening the same run is free.
- Each paid stage uses a transaction to reserve conservative upper model costs: UTF-8 payload byte count plus wrapper reserve and 120% billing margin, with model output bounded by chosen max tokens. The DB stores actual expense and reserved expense separately. This is a **software budget guard**, not an external billing guarantee; if the provider unexpectedly overcharges, account for it and halt.
- Before each request the catalog pricing hash/version is checked against the approved model snapshot. If unavailable/stale, stop. The quote must be current when the run starts; the later human review may take longer than the quote expiry, but the approved snapshots stay pinned and catalog is rechecked.
- No unattended paid retries, no background scheduler, and no automatic recovery of uncertain requests.
- The runner can read \`OPENROUTER_API_KEY\` / \`OPENROUTERKEY\` from the server environment or the pre-existing ContentMachine Settings key file. Secrets are never returned to browser or persisted to attempt rows.
- **Research documents are not sent or parsed**. User explicitly acknowledged their exclusion when approving budget in Block 5.

## Backend endpoints

- POST \`/api/projects/{id}/taxonomy-builder/runs\` with quote_id, consent_id, idempotency_key -> a run with zero paid calls.
- GET \`/api/projects/{id}/taxonomy-builder/runs/{run_id}\` -> current status, usage, expenses, fact/topic proposals and human reviews.
- POST \`.../runs/{run_id}/advance\` -> one paid call after reservation, if status allows and credentials are present.
- POST \`.../runs/{run_id}/facts/review\` -> all proposed facts reviewed, optionally human additions.
- POST \`.../runs/{run_id}/topics/publish\` -> reviewed category set becomes immutable taxonomy.
- POST \`.../runs/{run_id}/cancel\` -> stops new calls, preserves spent tokens.

Stage errors are explicit statuses, not fabricated "unknown topic" results. Attempt records are unique per run/role. The UI never permits a second call for a completed/failed/uncertain stage of the same run.

## Project storage

Migration \`007_tb_runs_and_reviews.sql\` adds version-scoped runs, attempts with reservation/usage/charge, immutable fact reviews, immutable published taxonomies, project-scoped FK and idempotency uniqueness.

**PR #6 caveat:** separate SRE taxonomy workspace is not in main. This Builder publication remains versioned and project-scoped; before merging both tracks, Block 7 must reconcile the Research Taxonomy storage into one authoritative schema, rather than operating two competing stores.

## Automated tests

- \`tests/test_taxonomy_builder_run.py\`: source-quote verification, human edits, relation/fact links, critique restrictions, request encoding, reservation costs, usage parsing.
- \`tests/test_taxonomy_builder_run_db.py\`: disposable PostgreSQL fixture, idempotent creation, two human gates, immutable taxonomy, cancellation, budget pause, uncertain call and billing once, HTTP origin guard.
- \`tests/test_taxonomy_run_ui.cjs\`: real editor-controller events with mock API; no automatic paid requests.
- Existing pipeline, estimator, UI and Windows smoke tests remain enabled.

## User-approved real test limit: $0.30 TOTAL

A dedicated workflow \`.github/workflows/taxonomy-live-smoke.yml\` with \`workflow_dispatch\` only uses the user's named secret **OPENROUTERKEY** and runs \`scripts/taxonomy_live_smoke.py\`. It is **not invoked by CI**. The script performs **at most one provider completion**, chooses the least-expensive verified JSON-capable candidate from the twelve approved model names, preflights a conservative $0.015 maximum reservation, sets max output to 256, does not retry on unknown results, and logs only reported cost, tokens and validation outcome.

**Important:** GitHub Actions manual workflow dispatch requires a workflow registered on the default branch. PR #7 is deliberately unmerged, so it may not be dispatchable directly in the GitHub Actions web UI until the workflow is available on main. We do not bypass that restriction by enabling automatic paid PR CI. If no dispatch is available, real paid test status is **NOT RUN**; the user's $0.30 remains unspent by this test. A user-triggered rerun counts as a **new expense** against the same aggregate cap. Check OpenRouter Activity for any unknown outcomes before authorizing another run.

## Known limitations, Block 7 follow-up

- No dedicated persistent background worker; the local HTTP call stays open for up to 55 seconds per stage. If the server dies in-flight, the pending attempt remains uncertain and must be manually reconciled rather than retried.
- No tokenizer-level exact input budget guarantee; conservative estimate and cost snapshots are guards, not contractual upper billing limits.
- No binary document extraction, and no full model-quality/latency benchmarking yet.
- Full end-to-end live testing of ALL stages/models is still needed; one smoke call is not equivalent.
- Single-user localhost origin/header protections apply; not designed for remote multi-user deployment.
- The currently separate PR #6 taxonomy schema must be reconciled before joint integration into main.

**Stop at Block 6, await owner instruction before Block 7.**

Catalog consistency follow-up: migration `008_tb_catalog_observed_snapshots.sql` records which exact immutable model capability/price snapshot was returned by each refresh. A model's older price returning again must not be treated as an obsolete version.
