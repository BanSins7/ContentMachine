-- PSI Stage 08: scene/frame preparation + explicitly consented paid visual analysis.
-- Keep migration 030..036 checksums unchanged. Append-only output metadata; no rights gates.
CREATE TABLE project_vision_runs (
 project_id TEXT NOT NULL,
 job_id UUID NOT NULL,
 source_id UUID NOT NULL,
 video_source_version_id UUID NOT NULL,
 acquisition_job_id UUID NOT NULL,
 mode TEXT NOT NULL CHECK(mode IN ('prepare','analyze')),
 config JSONB NOT NULL CHECK(jsonb_typeof(config)='object'),
 config_sha256 TEXT NOT NULL CHECK(config_sha256 ~ '^[0-9a-f]{64}$'),
 parent_prepare_job_id UUID,
 quote_id UUID,
 result_output_id UUID,
 provider_request_id TEXT CHECK(provider_request_id IS NULL OR length(provider_request_id)<=120),
 started_at TIMESTAMPTZ,
 finished_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(project_id,job_id),
 UNIQUE(project_id,job_id,source_id,video_source_version_id),
 FOREIGN KEY(project_id,source_id,video_source_version_id,job_id)
  REFERENCES source_extraction_jobs(project_id,source_id,source_version_id,id),
 FOREIGN KEY(project_id,acquisition_job_id,source_id,video_source_version_id)
  REFERENCES project_video_acquisition_runs(project_id,job_id,source_id,result_source_version_id),
 FOREIGN KEY(project_id,parent_prepare_job_id,source_id,video_source_version_id)
  REFERENCES project_vision_runs(project_id,job_id,source_id,video_source_version_id),
 FOREIGN KEY(project_id,source_id,video_source_version_id,result_output_id)
  REFERENCES source_processing_outputs(project_id,source_id,source_version_id,id),
 CHECK((mode='prepare' AND parent_prepare_job_id IS NULL AND quote_id IS NULL)
    OR (mode='analyze' AND parent_prepare_job_id IS NOT NULL AND quote_id IS NOT NULL))
);
CREATE INDEX psi_vision_runs_source ON project_vision_runs(project_id,source_id,created_at DESC);
CREATE UNIQUE INDEX psi_vision_prepare_idempotency ON project_vision_runs(project_id,source_id,video_source_version_id,config_sha256)
 WHERE mode='prepare';
CREATE TABLE project_vision_quotes (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 source_id UUID NOT NULL,
 video_source_version_id UUID NOT NULL,
 prepare_job_id UUID NOT NULL,
 model TEXT NOT NULL CHECK(model='anthropic/claude-sonnet-4.6'),
 estimate_usd NUMERIC(18,8) NOT NULL CHECK(estimate_usd>0),
 frame_count INTEGER NOT NULL CHECK(frame_count BETWEEN 1 AND 8),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 expires_at TIMESTAMPTZ NOT NULL,
 UNIQUE(project_id,id),
 UNIQUE(project_id,source_id,id),
 FOREIGN KEY(project_id,prepare_job_id,source_id,video_source_version_id)
  REFERENCES project_vision_runs(project_id,job_id,source_id,video_source_version_id),
 CHECK(expires_at>created_at)
);
ALTER TABLE project_vision_runs ADD CONSTRAINT psi_vision_quote_same_source
 FOREIGN KEY(project_id,source_id,quote_id)
 REFERENCES project_vision_quotes(project_id,source_id,id);
CREATE UNIQUE INDEX psi_vision_paid_once_quote ON project_vision_runs(project_id,quote_id) WHERE quote_id IS NOT NULL;
CREATE UNIQUE INDEX psi_vision_source_active ON source_extraction_jobs(project_id,source_id)
 WHERE stage='vision' AND state IN ('queued','processing');
CREATE FUNCTION psi_guard_vision_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.project_id,NEW.job_id,NEW.source_id,NEW.video_source_version_id,
        NEW.acquisition_job_id,NEW.mode,NEW.config,NEW.config_sha256,
        NEW.parent_prepare_job_id,NEW.quote_id,NEW.created_at)
   IS DISTINCT FROM
    ROW(OLD.project_id,OLD.job_id,OLD.source_id,OLD.video_source_version_id,
        OLD.acquisition_job_id,OLD.mode,OLD.config,OLD.config_sha256,
        OLD.parent_prepare_job_id,OLD.quote_id,OLD.created_at)
 THEN RAISE EXCEPTION 'Vision job identity is immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER psi_vision_identity BEFORE UPDATE ON project_vision_runs
 FOR EACH ROW EXECUTE FUNCTION psi_guard_vision_identity();
CREATE TRIGGER psi_vision_quote_immutable BEFORE UPDATE OR DELETE ON project_vision_quotes
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
