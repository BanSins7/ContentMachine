-- Block 6: sequential, auditable, budget-gated Taxonomy Builder runs.
-- Immutable model/prompt/input snapshots; no automatic background completion.
CREATE TABLE tb_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id),
  pipeline_id UUID NOT NULL,
  quote_id UUID NOT NULL,
  consent_id UUID NOT NULL,
  profile_revision INTEGER NOT NULL,
  profile_hash TEXT NOT NULL,
  idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 12 AND 128),
  status TEXT NOT NULL DEFAULT 'extraction_pending' CHECK(status IN (
    'extraction_pending','extraction_running','fact_review',
    'generation_pending','generation_running','critic_pending','critic_running',
    'topic_review','approved','cancelled','failed','uncertain','paused_budget','paused_price'
  )),
  profile_snapshot JSONB NOT NULL CHECK(jsonb_typeof(profile_snapshot)='object'),
  config_snapshot JSONB NOT NULL CHECK(jsonb_typeof(config_snapshot)='object'),
  model_snapshots JSONB NOT NULL CHECK(jsonb_typeof(model_snapshots)='object'),
  input_sha256 TEXT NOT NULL,
  authorized_usd NUMERIC(18,8) NOT NULL CHECK(authorized_usd>0),
  spent_usd NUMERIC(18,8) NOT NULL DEFAULT 0 CHECK(spent_usd>=0),
  reserved_usd NUMERIC(18,8) NOT NULL DEFAULT 0 CHECK(reserved_usd>=0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(pipeline_id,project_id,profile_revision,profile_hash)
    REFERENCES tb_pipeline_versions(id,project_id,profile_revision,profile_hash),
  FOREIGN KEY(quote_id,project_id,pipeline_id)
    REFERENCES tb_cost_quotes(id,project_id,pipeline_id),
  FOREIGN KEY(consent_id,project_id) REFERENCES tb_spending_consents(id,project_id),
  UNIQUE(project_id,idempotency_key),
  UNIQUE(id,project_id)
  -- Runtime checks reservations against authorized budget before dispatch.
  -- Do not prohibit recording an unexpected real provider overcharge.
);
CREATE INDEX idx_tb_runs_recent ON tb_runs(project_id,created_at DESC);

CREATE TABLE tb_stage_attempts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('fact_extraction','taxonomy_generation','taxonomy_critic')),
  status TEXT NOT NULL CHECK(status IN ('reserved','completed','error','uncertain')),
  reservation_usd NUMERIC(18,8) NOT NULL CHECK(reservation_usd>0),
  charged_usd NUMERIC(18,8) CHECK(charged_usd>=0),
  model_id TEXT NOT NULL,
  reasoning JSONB NOT NULL,
  temperature NUMERIC,
  prompt_version TEXT NOT NULL,
  prompt_sha256 TEXT NOT NULL,
  request_sha256 TEXT NOT NULL,
  provider_response_id TEXT,
  usage JSONB,
  output JSONB,
  error_message TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  ended_at TIMESTAMPTZ,
  FOREIGN KEY(run_id,project_id) REFERENCES tb_runs(id,project_id),
  UNIQUE(run_id,role)
);
CREATE INDEX idx_tb_attempts_by_run ON tb_stage_attempts(run_id,started_at);

CREATE TABLE tb_fact_review_submissions (
  run_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL,
  approved_facts JSONB NOT NULL CHECK(jsonb_typeof(approved_facts)='array'),
  reviewed_by TEXT NOT NULL,
  reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(run_id,project_id) REFERENCES tb_runs(id,project_id)
);
CREATE TABLE tb_taxonomy_publications (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL UNIQUE,
  taxonomy_version INTEGER NOT NULL,
  approved_topics JSONB NOT NULL CHECK(jsonb_typeof(approved_topics)='array'),
  approved_by TEXT NOT NULL,
  approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(run_id,project_id) REFERENCES tb_runs(id,project_id),
  UNIQUE(project_id,taxonomy_version)
);
CREATE INDEX idx_tb_taxonomy_by_project ON tb_taxonomy_publications(project_id,taxonomy_version DESC);
CREATE FUNCTION tb_block_review_mutations() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Human reviews and published taxonomies are immutable'; END $$;
CREATE TRIGGER tb_facts_review_immutable BEFORE UPDATE OR DELETE ON tb_fact_review_submissions
  FOR EACH ROW EXECUTE FUNCTION tb_block_review_mutations();
CREATE TRIGGER tb_taxonomy_publication_immutable BEFORE UPDATE OR DELETE ON tb_taxonomy_publications
  FOR EACH ROW EXECUTE FUNCTION tb_block_review_mutations();
