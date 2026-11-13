-- GC-MOD-23: immutable score-quality decision, independent of numeric C7.
-- Does not change historical 003/004/056-060 migration checksums.
-- No synthetic calculated_metrics rows with invented zeros or NULL scores.
CREATE TABLE group_c_quality_reports (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  video_id BIGINT NOT NULL,
  job_id BIGINT NOT NULL UNIQUE REFERENCES group_c_jobs(id) ON DELETE RESTRICT,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
  source_policy_version_id BIGINT,
  snapshot_id BIGINT,
  previous_snapshot_id BIGINT,
  published_metadata_id BIGINT,
  calculation_config_id BIGINT,
  reason_code TEXT NOT NULL
    CHECK(reason_code ~ '^[a-z][a-z0-9_]{0,79}$'),
  calculation_status TEXT NOT NULL CHECK(calculation_status IN
    ('insufficient_data','pending_configuration','invalid_timestamp',
     'incompatible_source','counter_correction','calculation_error')),
  score_status TEXT NOT NULL CHECK(score_status=calculation_status),
  severity TEXT NOT NULL CHECK(severity IN ('blocked','anomaly')),
  diagnostic_version TEXT NOT NULL DEFAULT 'group-c-quality-gate/v1'
    CHECK(diagnostic_version='group-c-quality-gate/v1'),
  fingerprint_sha256 TEXT NOT NULL CHECK(fingerprint_sha256 ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(project_id,run_id,video_id)
    REFERENCES group_c_runs(project_id,id,video_id) ON DELETE RESTRICT,
  FOREIGN KEY(snapshot_id,video_id)
    REFERENCES metric_snapshots(id,video_id) ON DELETE RESTRICT,
  FOREIGN KEY(previous_snapshot_id,video_id)
    REFERENCES metric_snapshots(id,video_id) ON DELETE RESTRICT,
  FOREIGN KEY(published_metadata_id,video_id)
    REFERENCES source_video_metadata(id,video_id) ON DELETE RESTRICT,
  FOREIGN KEY(calculation_config_id)
    REFERENCES metric_calculation_configs(id) ON DELETE RESTRICT,
  CHECK(snapshot_id IS DISTINCT FROM previous_snapshot_id),
  UNIQUE(project_id,run_id),
  UNIQUE(project_id,fingerprint_sha256)
);
CREATE INDEX idx_group_c_quality_reports_project
  ON group_c_quality_reports(project_id,created_at DESC);

-- A terminal quality report must describe a real fenced failed SCORE job,
-- never a completed job or a successfully persisted C7. This guard checks
-- report's project/run/video and QDP source/provenance, not only its hash.
CREATE FUNCTION group_c_quality_report_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  j RECORD;
  r RECORD;
BEGIN
  IF TG_OP<>'INSERT' THEN
    RAISE EXCEPTION 'Group C quality report is append-only'
      USING ERRCODE='23514';
  END IF;
  SELECT project_id,run_id,video_id,job_kind,state,error_code
    INTO j FROM group_c_jobs WHERE id=NEW.job_id;
  SELECT project_id,video_id,source_id,source_policy_version_id,
         snapshot_id,previous_snapshot_id,published_metadata_id,
         calculation_config_id,state,calculated_metric_id,reason_code
    INTO r FROM group_c_runs WHERE id=NEW.run_id AND project_id=NEW.project_id;
  IF NOT FOUND OR j.project_id IS DISTINCT FROM NEW.project_id
    OR j.run_id IS DISTINCT FROM NEW.run_id
    OR j.video_id IS DISTINCT FROM NEW.video_id
    OR j.job_kind IS DISTINCT FROM 'score'
    OR j.state IS DISTINCT FROM 'failed'
    OR j.error_code IS DISTINCT FROM NEW.reason_code
    OR r.project_id IS DISTINCT FROM NEW.project_id
    OR r.video_id IS DISTINCT FROM NEW.video_id
    OR r.source_id IS DISTINCT FROM NEW.source_id
    OR r.source_policy_version_id IS DISTINCT FROM NEW.source_policy_version_id
    OR r.snapshot_id IS DISTINCT FROM NEW.snapshot_id
    OR r.previous_snapshot_id IS DISTINCT FROM NEW.previous_snapshot_id
    OR r.published_metadata_id IS DISTINCT FROM NEW.published_metadata_id
    OR r.calculation_config_id IS DISTINCT FROM NEW.calculation_config_id
    OR r.state IS DISTINCT FROM 'blocked'
    OR r.calculated_metric_id IS NOT NULL
    OR r.reason_code IS DISTINCT FROM NEW.reason_code
  THEN
    RAISE EXCEPTION 'Quality report requires exact blocked run and failed score job'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_group_c_quality_report_guard
  BEFORE INSERT OR UPDATE OR DELETE ON group_c_quality_reports
  FOR EACH ROW EXECUTE FUNCTION group_c_quality_report_guard();
