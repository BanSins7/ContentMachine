-- Block 3: Taxonomy Builder model catalog and versioned approved pipeline.
-- All metadata is verified via a read-only OpenRouter catalog endpoint.
-- No paid completion calls, quote, or execution in this migration.
CREATE TABLE tb_model_candidates (
  model_id TEXT PRIMARY KEY CHECK(length(btrim(model_id))>0),
  display_name TEXT NOT NULL CHECK(length(btrim(display_name))>0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
INSERT INTO tb_model_candidates(model_id,display_name) VALUES
('openai/gpt-6.1-sol','OpenAI: GPT-6.1 Sol'),
('openai/gpt-6-luna','OpenAI: GPT-6 Luna'),
('google/gemini-4-argon','Google: Gemini 4 Argon'),
('google/gemini-3.8-flash','Google: Gemini 3.8 Flash'),
('anthropic/claude-opus-5.5','Anthropic: Claude Opus 5.5'),
('anthropic/claude-sonnet-5.5','Anthropic: Claude Sonnet 5.5'),
('anthropic/claude-haiku-5.5','Anthropic: Claude Haiku 5.5'),
('deepseek/deepseek-v4.1-flash','DeepSeek: DeepSeek V4.1 Flash'),
('z-ai/glm-5.3-flash','Z.ai: GLM 5.3 Flash'),
('xiaomi/mimo-v2.6-pro','Xiaomi: MiMo-V2.6-Pro'),
('xiaomi/mimo-v2.6-flash','Xiaomi: MiMo-V2.6-Flash'),
('qwen/qwen3.8-27b','Qwen: Qwen3.8 27B')
ON CONFLICT(model_id) DO NOTHING;

CREATE TABLE tb_catalog_refreshes (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  checked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  models_present JSONB NOT NULL CHECK(jsonb_typeof(models_present)='array'),
  source_url TEXT NOT NULL DEFAULT 'https://openrouter.ai/api/v1/models'
);

CREATE TABLE tb_model_snapshots (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  model_id TEXT NOT NULL REFERENCES tb_model_candidates(model_id),
  provider TEXT NOT NULL DEFAULT 'openrouter' CHECK(provider='openrouter'),
  catalog_sha256 TEXT NOT NULL CHECK(catalog_sha256 ~ '^[0-9a-f]{64}$'),
  capabilities JSONB NOT NULL CHECK(jsonb_typeof(capabilities)='object'),
  pricing JSONB NOT NULL CHECK(jsonb_typeof(pricing)='object'),
  verified_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  source_url TEXT NOT NULL DEFAULT 'https://openrouter.ai/api/v1/models',
  UNIQUE(model_id,catalog_sha256),
  UNIQUE(id,model_id)
);
CREATE INDEX idx_tb_model_snapshots_latest ON tb_model_snapshots(model_id,id DESC);

ALTER TABLE tb_intake_confirmations ADD CONSTRAINT tb_confirmed_profile_composite_key
 UNIQUE(project_id,profile_revision,profile_hash);

CREATE TABLE tb_pipeline_drafts (
  project_id TEXT PRIMARY KEY REFERENCES project_registry(id) ON DELETE CASCADE,
  profile_revision INTEGER NOT NULL,
  profile_hash TEXT NOT NULL CHECK(profile_hash ~ '^[0-9a-f]{64}$'),
  draft_revision INTEGER NOT NULL DEFAULT 1 CHECK(draft_revision>0),
  config JSONB NOT NULL CHECK(jsonb_typeof(config)='object'),
  changed_by TEXT NOT NULL CHECK(length(btrim(changed_by))>0),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(project_id,profile_revision,profile_hash)
    REFERENCES tb_intake_confirmations(project_id,profile_revision,profile_hash)
);

CREATE TABLE tb_pipeline_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  profile_revision INTEGER NOT NULL,
  profile_hash TEXT NOT NULL,
  pipeline_version INTEGER NOT NULL CHECK(pipeline_version>0),
  source_draft_revision INTEGER NOT NULL CHECK(source_draft_revision>0),
  config JSONB NOT NULL CHECK(jsonb_typeof(config)='object'),
  model_snapshots JSONB NOT NULL CHECK(jsonb_typeof(model_snapshots)='object'),
  config_sha256 TEXT NOT NULL CHECK(config_sha256 ~ '^[0-9a-f]{64}$'),
  created_by TEXT NOT NULL CHECK(length(btrim(created_by))>0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(project_id,profile_revision,profile_hash)
    REFERENCES tb_intake_confirmations(project_id,profile_revision,profile_hash),
  UNIQUE(project_id,profile_revision,pipeline_version),
  UNIQUE(project_id,profile_revision,source_draft_revision),
  UNIQUE(id,project_id)
);
CREATE INDEX idx_tb_pipeline_versions_project ON tb_pipeline_versions(project_id,created_at DESC);

CREATE TABLE tb_pipeline_approvals (
  pipeline_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL,
  approved_by TEXT NOT NULL CHECK(length(btrim(approved_by))>0),
  approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(pipeline_id,project_id)
    REFERENCES tb_pipeline_versions(id,project_id)
);

CREATE OR REPLACE FUNCTION tb_immutable_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Immutable snapshot: create a new version instead';
END $$;
CREATE TRIGGER tb_snapshots_immutable BEFORE UPDATE OR DELETE ON tb_model_snapshots
 FOR EACH ROW EXECUTE FUNCTION tb_immutable_snapshot();
CREATE TRIGGER tb_pipelines_immutable BEFORE UPDATE OR DELETE ON tb_pipeline_versions
 FOR EACH ROW EXECUTE FUNCTION tb_immutable_snapshot();
CREATE TRIGGER tb_approvals_immutable BEFORE UPDATE OR DELETE ON tb_pipeline_approvals
 FOR EACH ROW EXECUTE FUNCTION tb_immutable_snapshot();