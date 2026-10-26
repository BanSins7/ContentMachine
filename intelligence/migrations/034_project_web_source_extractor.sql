-- PSI Stage 05: explicit-cost Sonnet 4.6 + OpenRouter server-side web_fetch only.
-- Raw destination HTML is NOT captured and provenance stays model-reported.
-- No copyright gate; existing 030/031/032/033 migrations stay unchanged.
CREATE TABLE project_web_fetch_quotes (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 source_id UUID NOT NULL,
 source_version_id UUID NOT NULL,
 url TEXT NOT NULL CHECK(length(url) BETWEEN 8 AND 2048),
 model TEXT NOT NULL CHECK(model='anthropic/claude-sonnet-4.6'),
 engine TEXT NOT NULL CHECK(engine='openrouter'),
 config_sha256 TEXT NOT NULL CHECK(config_sha256 ~ '^[0-9a-f]{64}$'),
 estimated_cost_usd NUMERIC(18,8) NOT NULL CHECK(estimated_cost_usd>0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 expires_at TIMESTAMPTZ NOT NULL,
 UNIQUE(project_id,id),
 UNIQUE(project_id,source_id,id),
 FOREIGN KEY(project_id,source_id,source_version_id)
  REFERENCES project_source_versions(project_id,source_id,id),
 CHECK(expires_at>created_at)
);
CREATE INDEX psi_web_quote_source ON project_web_fetch_quotes(project_id,source_id,created_at DESC);
CREATE TRIGGER psi_web_quote_immutable BEFORE UPDATE OR DELETE ON project_web_fetch_quotes
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();

CREATE TABLE project_web_fetch_runs (
 project_id TEXT NOT NULL,
 job_id UUID NOT NULL,
 source_id UUID NOT NULL,
 quote_id UUID NOT NULL,
 input_source_version_id UUID NOT NULL,
 requested_url TEXT NOT NULL CHECK(length(requested_url) BETWEEN 8 AND 2048),
 model TEXT NOT NULL CHECK(model='anthropic/claude-sonnet-4.6'),
 config_sha256 TEXT NOT NULL CHECK(config_sha256 ~ '^[0-9a-f]{64}$'),
 result_source_version_id UUID,
 result_output_id UUID,
 provider_request_id TEXT CHECK(provider_request_id IS NULL OR length(provider_request_id)<=120),
 started_at TIMESTAMPTZ,
 finished_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(project_id,job_id),
 UNIQUE(project_id,quote_id),
 FOREIGN KEY(project_id,source_id,input_source_version_id,job_id)
  REFERENCES source_extraction_jobs(project_id,source_id,source_version_id,id),
 FOREIGN KEY(project_id,source_id,quote_id)
  REFERENCES project_web_fetch_quotes(project_id,source_id,id),
 FOREIGN KEY(project_id,source_id,result_source_version_id)
  REFERENCES project_source_versions(project_id,source_id,id),
 FOREIGN KEY(project_id,source_id,result_source_version_id,result_output_id)
  REFERENCES source_processing_outputs(project_id,source_id,source_version_id,id),
 CHECK((result_output_id IS NULL)=(result_source_version_id IS NULL))
);
CREATE INDEX psi_web_run_source ON project_web_fetch_runs(project_id,source_id,created_at DESC);

-- At most one queued/in-flight paid web operation per project source.
CREATE UNIQUE INDEX psi_web_one_active_source ON source_extraction_jobs(project_id,source_id)
 WHERE stage='web_fetch' AND state IN ('queued','processing');
