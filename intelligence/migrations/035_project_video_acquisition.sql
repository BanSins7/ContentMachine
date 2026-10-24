-- PSI Stage 06: project-scoped durable wrappers for existing platform downloaders.
-- No modification to D1 source/video identity and no network activity in migrations.
CREATE TABLE project_video_acquisition_runs (
 project_id TEXT NOT NULL,
 job_id UUID NOT NULL,
 source_id UUID NOT NULL,
 input_source_version_id UUID NOT NULL,
 platform TEXT NOT NULL CHECK(platform IN ('tiktok','youtube','instagram')),
 native_id TEXT NOT NULL CHECK(length(native_id) BETWEEN 5 AND 100),
 canonical_url TEXT NOT NULL CHECK(length(canonical_url) BETWEEN 12 AND 2048),
 result_source_version_id UUID,
 result_output_id UUID,
 video_asset_id UUID,
 video_sha256 TEXT CHECK(video_sha256 IS NULL OR video_sha256 ~ '^[0-9a-f]{64}$'),
 cache_reused BOOLEAN NOT NULL DEFAULT FALSE,
 progress NUMERIC(5,2) CHECK(progress IS NULL OR progress BETWEEN 0 AND 100),
 phase TEXT NOT NULL DEFAULT 'queued' CHECK(length(phase) BETWEEN 1 AND 40),
 cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
 unavailable_reason TEXT CHECK(unavailable_reason IS NULL OR length(unavailable_reason)<=400),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 started_at TIMESTAMPTZ,
 finished_at TIMESTAMPTZ,
 PRIMARY KEY(project_id,job_id),
 FOREIGN KEY(project_id,source_id,input_source_version_id,job_id)
  REFERENCES source_extraction_jobs(project_id,source_id,source_version_id,id),
 FOREIGN KEY(project_id,source_id,result_source_version_id)
  REFERENCES project_source_versions(project_id,source_id,id),
 FOREIGN KEY(project_id,source_id,result_source_version_id,result_output_id)
  REFERENCES source_processing_outputs(project_id,source_id,source_version_id,id),
 FOREIGN KEY(project_id,source_id,result_source_version_id,video_asset_id)
  REFERENCES project_media_assets(project_id,source_id,source_version_id,id),
 CHECK((result_source_version_id IS NULL)=(result_output_id IS NULL)),
 CHECK((video_asset_id IS NULL)=(result_output_id IS NULL)),
 CHECK((video_sha256 IS NULL)=(result_output_id IS NULL))
);
CREATE INDEX psi_video_runs_source ON project_video_acquisition_runs(project_id,source_id,created_at DESC);
CREATE INDEX psi_video_runs_native ON project_video_acquisition_runs(platform,native_id,created_at DESC);
-- A single source cannot be queued/downloaded twice at the same time.
CREATE UNIQUE INDEX psi_video_one_active_source ON source_extraction_jobs(project_id,source_id)
 WHERE stage='video_download' AND state IN ('queued','processing');
