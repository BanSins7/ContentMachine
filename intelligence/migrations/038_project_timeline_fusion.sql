-- PSI Stage 09: deterministic timeline fusion. Append-only output; no paid work.
-- Preserves 030..037 checksums and never writes Group A/B classifications.
ALTER TABLE project_video_acquisition_runs
 ADD CONSTRAINT psi_fusion_acq_exact_output UNIQUE(project_id,job_id,result_output_id);
ALTER TABLE project_transcript_runs
 ADD CONSTRAINT psi_fusion_transcript_exact_input UNIQUE(project_id,job_id,source_id,video_source_version_id);
ALTER TABLE project_transcript_runs
 ADD CONSTRAINT psi_fusion_transcript_exact_output UNIQUE(project_id,job_id,result_output_id);
ALTER TABLE project_vision_runs
 ADD CONSTRAINT psi_fusion_vision_exact_output UNIQUE(project_id,job_id,result_output_id);

CREATE TABLE project_video_fusion_runs (
 project_id TEXT NOT NULL,
 job_id UUID NOT NULL,
 source_id UUID NOT NULL,
 video_source_version_id UUID NOT NULL,
 acquisition_job_id UUID NOT NULL,
 acquisition_output_id UUID NOT NULL,
 transcript_job_id UUID,
 transcript_output_id UUID,
 prepare_job_id UUID,
 prepare_output_id UUID,
 vision_job_id UUID,
 vision_output_id UUID,
 config_sha256 TEXT NOT NULL CHECK(config_sha256 ~ '^[a-f0-9]{64}$'),
 engine_version TEXT NOT NULL CHECK(length(engine_version) BETWEEN 1 AND 100),
 result_output_id UUID,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 finished_at TIMESTAMPTZ,
 PRIMARY KEY(project_id,job_id),
 UNIQUE(project_id,source_id,video_source_version_id,config_sha256),
 FOREIGN KEY(project_id,source_id,video_source_version_id,job_id)
   REFERENCES source_extraction_jobs(project_id,source_id,source_version_id,id),
 FOREIGN KEY(project_id,acquisition_job_id,source_id,video_source_version_id)
   REFERENCES project_video_acquisition_runs(project_id,job_id,source_id,result_source_version_id),
 FOREIGN KEY(project_id,acquisition_job_id,acquisition_output_id)
   REFERENCES project_video_acquisition_runs(project_id,job_id,result_output_id),
 FOREIGN KEY(project_id,transcript_job_id,source_id,video_source_version_id)
   REFERENCES project_transcript_runs(project_id,job_id,source_id,video_source_version_id),
 FOREIGN KEY(project_id,transcript_job_id,transcript_output_id)
   REFERENCES project_transcript_runs(project_id,job_id,result_output_id),
 FOREIGN KEY(project_id,prepare_job_id,source_id,video_source_version_id)
   REFERENCES project_vision_runs(project_id,job_id,source_id,video_source_version_id),
 FOREIGN KEY(project_id,prepare_job_id,prepare_output_id)
   REFERENCES project_vision_runs(project_id,job_id,result_output_id),
 FOREIGN KEY(project_id,vision_job_id,source_id,video_source_version_id)
   REFERENCES project_vision_runs(project_id,job_id,source_id,video_source_version_id),
 FOREIGN KEY(project_id,vision_job_id,vision_output_id)
   REFERENCES project_vision_runs(project_id,job_id,result_output_id),
 FOREIGN KEY(project_id,source_id,video_source_version_id,result_output_id)
   REFERENCES source_processing_outputs(project_id,source_id,source_version_id,id),
 CHECK((transcript_job_id IS NULL)=(transcript_output_id IS NULL)),
 CHECK((prepare_job_id IS NULL)=(prepare_output_id IS NULL)),
 CHECK((vision_job_id IS NULL)=(vision_output_id IS NULL)),
 CHECK(transcript_job_id IS NOT NULL OR prepare_job_id IS NOT NULL),
 CHECK(vision_job_id IS NULL OR prepare_job_id IS NOT NULL)
);
CREATE INDEX psi_fusion_runs_source ON project_video_fusion_runs(project_id,source_id,created_at DESC);
CREATE UNIQUE INDEX psi_fusion_source_active ON source_extraction_jobs(project_id,source_id)
 WHERE stage='timeline_fusion' AND state IN ('queued','processing');
CREATE FUNCTION psi_guard_fusion_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.project_id,NEW.job_id,NEW.source_id,NEW.video_source_version_id,
        NEW.acquisition_job_id,NEW.acquisition_output_id,
        NEW.transcript_job_id,NEW.transcript_output_id,NEW.prepare_job_id,NEW.prepare_output_id,
        NEW.vision_job_id,NEW.vision_output_id,NEW.config_sha256,NEW.engine_version,NEW.created_at)
    IS DISTINCT FROM
    ROW(OLD.project_id,OLD.job_id,OLD.source_id,OLD.video_source_version_id,
        OLD.acquisition_job_id,OLD.acquisition_output_id,
        OLD.transcript_job_id,OLD.transcript_output_id,OLD.prepare_job_id,OLD.prepare_output_id,
        OLD.vision_job_id,OLD.vision_output_id,OLD.config_sha256,OLD.engine_version,OLD.created_at)
 THEN RAISE EXCEPTION 'Fusion run input identity is immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER psi_fusion_identity BEFORE UPDATE ON project_video_fusion_runs
 FOR EACH ROW EXECUTE FUNCTION psi_guard_fusion_identity();
