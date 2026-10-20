-- Taxonomy Builder Block 5: immutable quotes and explicit spending authorization.
-- No provider completions, runs, bank debits, or external charges are possible here.
ALTER TABLE tb_pipeline_versions
  ADD CONSTRAINT tb_quote_pipeline_snapshot_key
  UNIQUE(id,project_id,profile_revision,profile_hash);

CREATE TABLE tb_cost_quotes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  pipeline_id UUID NOT NULL,
  profile_revision INTEGER NOT NULL,
  profile_hash TEXT NOT NULL CHECK(profile_hash ~ '^[0-9a-f]{64}$'),
  config_sha256 TEXT NOT NULL CHECK(config_sha256 ~ '^[0-9a-f]{64}$'),
  input_sha256 TEXT NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
  pricing_sha256 TEXT NOT NULL CHECK(pricing_sha256 ~ '^[0-9a-f]{64}$'),
  estimator_version TEXT NOT NULL CHECK(length(btrim(estimator_version)) > 0),
  quote_details JSONB NOT NULL CHECK(jsonb_typeof(quote_details)='object'),
  low_usd NUMERIC(18,6) NOT NULL CHECK(low_usd>=0),
  expected_usd NUMERIC(18,6) NOT NULL CHECK(expected_usd>=low_usd),
  high_usd NUMERIC(18,6) NOT NULL CHECK(high_usd>=expected_usd),
  currency TEXT NOT NULL DEFAULT 'USD' CHECK(currency='USD'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ NOT NULL,
  FOREIGN KEY(pipeline_id,project_id,profile_revision,profile_hash)
    REFERENCES tb_pipeline_versions(id,project_id,profile_revision,profile_hash),
  UNIQUE(id,project_id,pipeline_id),
  CHECK(expires_at>created_at)
);
CREATE INDEX idx_tb_quotes_current ON tb_cost_quotes(project_id,pipeline_id,created_at DESC);

CREATE TABLE tb_spending_consents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  pipeline_id UUID NOT NULL,
  quote_id UUID NOT NULL UNIQUE,
  authorized_usd NUMERIC(18,6) NOT NULL CHECK(authorized_usd>0 AND authorized_usd<=10000),
  authorized_by TEXT NOT NULL CHECK(length(btrim(authorized_by))>0),
  authorized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(quote_id,project_id,pipeline_id)
    REFERENCES tb_cost_quotes(id,project_id,pipeline_id),
  UNIQUE(id,project_id)
);
CREATE INDEX idx_tb_consents_project ON tb_spending_consents(project_id,authorized_at DESC);

CREATE TABLE tb_spending_revocations (
  consent_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL,
  revoked_by TEXT NOT NULL CHECK(length(btrim(revoked_by))>0),
  revoked_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(consent_id,project_id) REFERENCES tb_spending_consents(id,project_id)
);

CREATE TRIGGER tb_quotes_immutable BEFORE UPDATE OR DELETE ON tb_cost_quotes
 FOR EACH ROW EXECUTE FUNCTION tb_immutable_snapshot();
CREATE TRIGGER tb_consents_immutable BEFORE UPDATE OR DELETE ON tb_spending_consents
 FOR EACH ROW EXECUTE FUNCTION tb_immutable_snapshot();
CREATE TRIGGER tb_revocations_immutable BEFORE UPDATE OR DELETE ON tb_spending_revocations
 FOR EACH ROW EXECUTE FUNCTION tb_immutable_snapshot();