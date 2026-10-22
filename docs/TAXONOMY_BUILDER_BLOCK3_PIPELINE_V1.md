> **Актуализация 22.10.2026:** этот документ фиксирует состояние отдельного этапа разработки, а не текущий статус ветки. PR #7 (Taxonomy Builder) уже **merged** в `main` 21.10.2026, PR #8 добавил workspace UI, PR #9 — иерархический двухпроходный режим. Утверждения ниже о draft PR #7 и отсутствии последующих функций — **исторические**. Актуальное состояние: [CURRENT_STATE_2026-10-22.md](CURRENT_STATE_2026-10-22.md).

---

# Taxonomy Builder — Block 3: Model Registry & versioned Pipeline backend

**Status:** implemented backend foundation in draft PR #7. **No visual editor, cost quote, consent, model generation or paid API requests.**
**Depends on:** Block 2 immutable profile confirmation (migration 004).
**Migration:** \`005_tb_pipeline_registry.sql\`. The existing \`project.json\` continues as authoritative source of product facts. This migration does not touch D1, downloaders, or the separate SRE PR #6.

## Twelve selectable candidates requested by the owner

The registry **always lists** all 12 candidates, exactly as submitted. Presence in the list does *not* claim availability or parameter support:

| Provider | OpenRouter model slug |
|---|---|
| OpenAI | \`openai/gpt-6.1-sol\`, \`openai/gpt-6-luna\` |
| Google | \`google/gemini-4-argon\`, \`google/gemini-3.8-flash\` |
| Anthropic | \`anthropic/claude-opus-5.5\`, \`anthropic/claude-sonnet-5.5\`, \`anthropic/claude-haiku-5.5\` |
| DeepSeek | \`deepseek/deepseek-v4.1-flash\` |
| Z.ai | \`z-ai/glm-5.3-flash\` |
| Xiaomi | \`xiaomi/mimo-v2.6-pro\`, \`xiaomi/mimo-v2.6-flash\` |
| Qwen | \`qwen/qwen3.8-27b\` |

A server-side **explicit metadata refresh** reads public \`https://openrouter.ai/api/v1/models\` (no API key or chat/completion request) and captures \`supported_parameters\`, \`reasoning.supported_efforts\`, context/output limits, text-output capability and raw pricing. The list of returned models may differ from the screenshots. **Only models verified in the latest fresh refresh and capable of text + JSON responses can be used in an approved new Pipeline.** Models not currently present show \`missing\`, \`stale\` or \`unverified\` instead of imagined features.

\`POST /api/taxonomy-builder/models/refresh\` performs a metadata-only provider call. No automatic/periodic background refresh, no charges. Catalog versions are append-only, linked to SHA-256 of their verified capabilities+pricing. Each refresh records the set of models observed; model status expires after 48h. No dynamic arbitrary URL is accepted. If the provider is unavailable, refresh fails closed; old historical Pipeline versions are not mutated.

**Limits of verification:** OpenRouter publishes a list of reasoning efforts for many models, but not always numeric reasoning token budgets nor **joint support of temperature and explicit reasoning**. We deliberately do not infer those combinations; explicit temperature with chosen effort is rejected unless an independently verified allowed-effort list exists. In default reasoning mode, temperature may be used only if the model advertises it **and** reasoning is not known to be mandatory/enabled by default, or the exact default-reasoning combination has independently verified support. No unsupported field is silently omitted.

Source schema references: OpenRouter GET /api/v1/models and official reasoning documentation:
- https://openrouter.ai/docs/api/api-reference/models/get-models
- https://openrouter.ai/docs/guides/best-practices/reasoning-tokens

## Pipeline stages

The user can independently select model, \`reasoning\`, \`temperature\`, \`max_output_tokens\` for:

1. \`fact_extraction\` — required; prompt \`facts-v1\`.
2. **Human Fact Review** — required, not configurable away.
3. \`taxonomy_generation\` — required; prompt \`taxonomy-v1\`.
4. \`taxonomy_critic\` — optional, model-selected or disabled; prompt \`critic-v1\`.
5. **Human Taxonomy Review** — required, not configurable away.

The config only contains LLM stages in their fixed dependency order: facts, taxonomy, optional critic. Deterministic validators and human gates are not toggleable config entries.

### Request schema for a saved draft

\`\`\`json
{
  "expected_draft_revision": 0,
  "config": {
    "schema_version": 1,
    "stages": [
      {
        "role": "fact_extraction",
        "model_id": "openai/gpt-6.1-sol",
        "catalog_version_id": 42,
        "reasoning": {"mode": "effort", "effort": "medium"},
        "temperature": null,
        "max_output_tokens": 6000,
        "prompt_version": "facts-v1"
      },
      {
        "role": "taxonomy_generation",
        "model_id": "anthropic/claude-sonnet-5.5",
        "catalog_version_id": 43,
        "reasoning": {"mode": "default"},
        "temperature": 0.5,
        "max_output_tokens": 8000,
        "prompt_version": "taxonomy-v1"
      },
      {"role": "taxonomy_critic", "enabled": false}
    ]
  }
}
\`\`\`

**Example IDs 42 and 43 are placeholders.** Read the actual \`catalog_version_id\` from the model-catalog endpoint; never assume this numeric ID.

Reasoning modes: \`{"mode":"default"}\`, \`{"mode":"effort","effort":"high"}\` (exact effort must appear in the catalog), and \`{"mode":"budget","budget_tokens":...}\` (only if a later provider-verified budget range is registered; currently unavailable for catalog-only models). Temperature: \`null\` or 0..2, only if both the selected model and reasoning setting permit it. Reasoning tokens can consume the max output and future billable output; do not promise generation quality based on a setting alone.

## HTTP endpoints added to the existing localhost-only app

- \`GET /api/projects/{id}/taxonomy-builder/models\`: 12 candidates with \`status\`, \`selectable\`, raw pricing and capabilities. Requires current confirmed project version.
- \`POST /api/taxonomy-builder/models/refresh\` body \`{}\`: download and persist public model metadata; no paid chat request. Requires existing same-origin / localhost \`X-ContentMachine: 1\` protections.
- \`GET /api/projects/{id}/taxonomy-builder/pipeline\`: return project current intake revision, draft, immutable approved history, state: \`pipeline_setup\`, \`pipeline_review\` or \`pipeline_approved\`. \`model_editor_available=false\` until Block 4 UI.
- \`POST /api/projects/{id}/taxonomy-builder/pipeline/draft\`: full config with \`expected_draft_revision\`, optimistic concurrency, strict verified feature matrix. Starts at 0 then increments 1, 2, ...
- \`POST /api/projects/{id}/taxonomy-builder/pipeline/approve\`: body \`{"expected_draft_revision":1}\`; atomic approval of immutable config + pinned pricing/capability/prompt snapshot. Repeated same-revision approve is idempotent; editing draft creates a new version requiring another explicit approval.

There is deliberately **no** \`POST /runs\`, \`/quote\`, \`/consent\` or chat API path in Block 3.

## Database safety & project integrity

- \`tb_model_candidates\`: static list of the 12 owner-approved names; they are not treated as confirmed provider capabilities.
- \`tb_catalog_refreshes\`: record of observed provider model IDs and freshness.
- \`tb_model_snapshots\`: immutable capabilities/pricing hashes.
- \`tb_pipeline_drafts\`: mutable only while current intake version is confirmed; optimistic draft revision to avoid overwritten tabs.
- \`tb_pipeline_versions\`: immutable project/intake/version-scoped approved snapshots with \`source_draft_revision\`, \`config_sha256\`.
- \`tb_pipeline_approvals\`: immutable human authorization of a *configuration*, **not** money or provider spend.

Every read/write checks project ID. Strict FK to \`tb_intake_confirmations\` binds each saved draft/version to one exact profile revision/hash. New intake revision forces a fresh draft and human approval. Database triggers protect historic config, capability snapshot and approval history. Browser-supplied stage capabilities are never trusted.

Security: currently restricted to ContentMachine's localhost dashboard with Host/Origin/custom POST header. This is **not multi-user authentication**, so do not expose on an external network before access control is added.

## Running and testing

\`\`\`bash
CM_DB_DSN=postgresql://... python -m intelligence.migrate
python -m unittest discover -s tests -v
node --check site/projects.js
\`\`\`

New tests: \`tests/test_taxonomy_builder_pipeline.py\` (pure per-model parameter validation) and \`tests/test_taxonomy_builder_pipeline_db.py\` (versioned PostgreSQL storage and isolation). OpenRouter catalog calls are **mocked** in CI, no paid requests or API keys.

## The next blocks

- **Block 4**: real ChatGPT-like Pipeline Studio UI; per-stage selectors, reasoning/temperature controls, explanation of disabled choices, draft saving/approval from the interface.
- **Block 5**: verified pricing snapshot, estimated low/typical/high cost and separate capped spending consent.
- **Block 6**: real LLM adapters, human fact/topic review, expense accounting and taxonomy publication.

**Stop after Block 3 for user approval.**
