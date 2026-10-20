-- Read-time D1 rights gate. A stored transcript cannot be used after
-- source/policy revocation, media expiry, or a content retention deadline.
CREATE OR REPLACE VIEW sre_readable_content_versions AS
SELECT c.*
FROM sre_content_versions c
JOIN sources s ON s.id=c.source_id
JOIN source_policy_versions p ON p.id=c.source_policy_version_id
                           AND p.source_id=s.id
LEFT JOIN media_assets m ON m.id=c.media_asset_id
WHERE s.status='enabled' AND s.can_store_metadata AND p.is_active
  AND (c.expires_at IS NULL OR c.expires_at>now())
  AND (s.retention_days IS NULL OR c.created_at+
       s.retention_days*interval '1 day' > now())
  AND ((NOT (c.content ? 'transcript' OR c.content ? 'scenes'))
       OR (s.can_analyze_media AND m.id IS NOT NULL))
  AND (m.id IS NULL OR (m.permission <> 'metadata_only'
       AND (m.expires_at IS NULL OR m.expires_at>now())));

-- The future worker will record each stage's exact provider/model/prompt,
-- input/output digests and resource usage. No model is called by this migration.
CREATE TABLE IF NOT EXISTS sre_stage_executions (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id UUID NOT NULL,
  project_id TEXT NOT NULL,
  video_id BIGINT NOT NULL,
  stage_id TEXT NOT NULL CHECK(length(btrim(stage_id))>0),
  attempt_no INTEGER NOT NULL DEFAULT 1 CHECK(attempt_no>0),
  model_ref TEXT NOT NULL,
  provider TEXT NOT NULL,
  model_id TEXT NOT NULL,
  model_version TEXT NOT NULL,
  prompt_version TEXT NOT NULL,
  input_sha256 TEXT NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
  output_sha256 TEXT CHECK(output_sha256 IS NULL OR output_sha256 ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL CHECK(status IN ('completed','skipped','provider_error','invalid_output')),
  error_code TEXT,
  latency_ms BIGINT CHECK(latency_ms IS NULL OR latency_ms>=0),
  input_tokens BIGINT CHECK(input_tokens IS NULL OR input_tokens>=0),
  output_tokens BIGINT CHECK(output_tokens IS NULL OR output_tokens>=0),
  cost_usd NUMERIC(14,6) CHECK(cost_usd IS NULL OR cost_usd>=0),
  started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  FOREIGN KEY(run_id,project_id) REFERENCES sre_classification_runs(id,project_id) ON DELETE CASCADE,
  FOREIGN KEY(project_id,video_id) REFERENCES sre_project_videos(project_id,video_id),
  UNIQUE(run_id,video_id,stage_id,attempt_no),
  CHECK(finished_at IS NULL OR finished_at>=started_at)
);
CREATE INDEX IF NOT EXISTS idx_sre_stage_runs
  ON sre_stage_executions(project_id,run_id,video_id);
