-- PSI Stage 07: immutable transcript normalization requests pinned to a verified
-- Stage 06 video output. Existing 030..035 checksums are preserved.
-- No background uploads, pay-per-request ASR, or source file mutation.
ALTER TABLE project_video_acquisition_runs
  ADD CONSTRAINT psi_video_acquisition_exact_transcript_parent
  UNIQUE (project_id,job_id,source_id,result_source_version_id);

CREATE TABLE project_transcript_runs (
 project_id TEXT NOT NULL,
 job_id UUID NOT NULL,
 source_id UUID NOT NULL,
 video_source_version_id UUID NOT NULL,
 acquisition_job_id UUID NOT NULL,
 mode TEXT NOT NULL CHECK (mode IN ('reuse_sidecar','local_asr')),
 normalizer_version TEXT NOT NULL CHECK(length(normalizer_version) BETWEEN 1 AND 80),
 settings JSONB NOT NULL CHECK(jsonb_typeof(settings)='object'),
 config_sha256 TEXT NOT NULL CHECK(config_sha256 ~ '^[0-9a-f]{64}$'),
 result_output_id UUID,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 finished_at TIMESTAMPTZ,
 PRIMARY KEY(project_id,job_id),
 UNIQUE(project_id,source_id,video_source_version_id,mode,config_sha256),
 FOREIGN KEY (project_id,source_id,video_source_version_id,job_id)
  REFERENCES source_extraction_jobs(project_id,source_id,source_version_id,id),
 FOREIGN KEY (project_id,acquisition_job_id,source_id,video_source_version_id)
  REFERENCES project_video_acquisition_runs(project_id,job_id,source_id,result_source_version_id),
 FOREIGN KEY(project_id,source_id,video_source_version_id,result_output_id)
  REFERENCES source_processing_outputs(project_id,source_id,source_version_id,id)
);
CREATE INDEX psi_transcripts_file_recent
  ON project_transcript_runs(project_id,source_id,created_at DESC);
CREATE FUNCTION psi_guard_transcript_run_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.project_id,NEW.job_id,NEW.source_id,NEW.video_source_version_id,NEW.acquisition_job_id,
        NEW.mode,NEW.normalizer_version,NEW.settings,NEW.config_sha256,NEW.created_at)
   IS DISTINCT FROM
    ROW(OLD.project_id,OLD.job_id,OLD.source_id,OLD.video_source_version_id,OLD.acquisition_job_id,
        OLD.mode,OLD.normalizer_version,OLD.settings,OLD.config_sha256,OLD.created_at)
 THEN RAISE EXCEPTION 'Transcript run identity is immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER psi_transcript_runs_identity BEFORE UPDATE ON project_transcript_runs
  FOR EACH ROW EXECUTE FUNCTION psi_guard_transcript_run_identity();
-- No concurrent ASR requests for one project source, including racing retries.
CREATE UNIQUE INDEX psi_asr_one_active_source ON source_extraction_jobs(project_id,source_id)
 WHERE stage='asr' AND state IN ('queued','processing');
