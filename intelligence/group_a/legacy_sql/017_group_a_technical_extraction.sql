-- Group A executable stage A1: QDP-gated local video technical measurements.
-- Additive only; existing A003 and QDP migrations are not changed.
-- No downloads, provider calls, transcription or content interpretation.
CREATE TABLE IF NOT EXISTS group_a_tasks (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 qdp_run_id UUID NOT NULL,
 video_id BIGINT NOT NULL,
 media_asset_id BIGINT NOT NULL REFERENCES media_assets(id),
 source_id TEXT NOT NULL REFERENCES sources(id),
 source_policy_version_id BIGINT NOT NULL REFERENCES source_policy_versions(id),
 qdp_input_sha256 TEXT NOT NULL CHECK (qdp_input_sha256 ~ '^[0-9a-f]{64}$'),
 input_sha256 TEXT NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
 config JSONB NOT NULL CHECK(jsonb_typeof(config)='object'),
 job_id BIGINT UNIQUE REFERENCES jobs(id) ON DELETE SET NULL,
 analysis_id BIGINT UNIQUE REFERENCES video_feature_a_analyses(id) ON DELETE SET NULL,
 status TEXT NOT NULL DEFAULT 'queued'
   CHECK(status IN ('queued','running','completed','blocked','failed','cancelled')),
 error_code TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 finished_at TIMESTAMPTZ,
 FOREIGN KEY(qdp_run_id,project_id,video_id)
    REFERENCES qdp_items(run_id,project_id,video_id) ON DELETE CASCADE,
 UNIQUE(qdp_run_id,video_id,input_sha256),
 CHECK(finished_at IS NULL OR finished_at>=created_at)
);
CREATE INDEX IF NOT EXISTS idx_group_a_tasks_project ON group_a_tasks(project_id,created_at DESC);
CREATE INDEX IF NOT EXISTS idx_group_a_tasks_status ON group_a_tasks(status,created_at);
-- No historical mutation of the technical measurement or task identity.
CREATE OR REPLACE FUNCTION group_a_task_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (OLD.project_id,OLD.qdp_run_id,OLD.video_id,OLD.media_asset_id,
     OLD.source_id,OLD.source_policy_version_id,OLD.qdp_input_sha256,
     OLD.input_sha256,OLD.config)
    IS DISTINCT FROM
    (NEW.project_id,NEW.qdp_run_id,NEW.video_id,NEW.media_asset_id,
     NEW.source_id,NEW.source_policy_version_id,NEW.qdp_input_sha256,
     NEW.input_sha256,NEW.config) THEN
     RAISE EXCEPTION 'Group A task identity is immutable';
 END IF;
 IF OLD.job_id IS NOT NULL AND NEW.job_id IS DISTINCT FROM OLD.job_id THEN
    RAISE EXCEPTION 'Group A job identity cannot be replaced';
 END IF;
 IF OLD.status IN ('completed','blocked','cancelled','failed')
    AND NEW.status<>OLD.status THEN
    RAISE EXCEPTION 'Group A task is terminal';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER group_a_task_identity_immutable BEFORE UPDATE ON group_a_tasks
 FOR EACH ROW EXECUTE FUNCTION group_a_task_identity_guard();

-- Read-time rights gating applies to the QDP snapshot and the referenced
-- media file, not just to old INSERT checks in A003. Missing/expired data
-- cannot be exported as a valid completed result.
CREATE OR REPLACE VIEW group_a_readable_results AS
SELECT t.id AS task_id,t.project_id,t.qdp_run_id,t.video_id,t.status,
       a.id AS analysis_id,a.analysis_version,a.state AS analysis_state,
       a.scene_status,a.speech_status,a.screen_text_status,a.audio_status,
       a.measured_at,sm.duration_sec,sm.aspect_ratio,sm.scene_count,
       sm.first_text_sec,sm.first_speech_sec,sm.speech_wpm,
       sm.speech_coverage_pct,sm.has_subtitles,sm.cta_start_sec
FROM group_a_tasks t
JOIN qdp_readable_enrichments q ON q.run_id=t.qdp_run_id AND q.project_id=t.project_id
     AND q.video_id=t.video_id AND q.media_asset_id=t.media_asset_id
     AND q.source_id=t.source_id
     AND q.source_policy_version_id=t.source_policy_version_id
JOIN qdp_preparations p ON p.run_id=t.qdp_run_id AND p.project_id=t.project_id
     AND p.video_id=t.video_id AND p.analysis_group='A'
     AND p.state='ready' AND p.input_sha256=t.qdp_input_sha256
JOIN media_assets m ON m.id=t.media_asset_id
     AND m.video_id=t.video_id AND m.source_id=t.source_id
JOIN sources src ON src.id=t.source_id AND src.status='enabled' AND src.can_analyze_media
JOIN source_policy_versions sp ON sp.id=t.source_policy_version_id
     AND sp.source_id=t.source_id AND sp.is_active
     AND sp.permissions->>'can_analyze_media'='true'
JOIN video_feature_a_analyses a ON a.id=t.analysis_id
     AND a.video_id=t.video_id AND a.source_id=t.source_id
     AND a.media_asset_id=t.media_asset_id
JOIN video_feature_a_summary sm ON sm.analysis_id=a.id
WHERE t.status='completed' AND m.permission<>'metadata_only'
  AND (m.expires_at IS NULL OR m.expires_at>now());
