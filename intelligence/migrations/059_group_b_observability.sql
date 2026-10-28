-- GB-MOD-27 additive observability. No changes to SQL005, 055/056/057/058.
-- App owns project-scoped writes after trusted worker lease verification.
-- No prompt, raw media, URLs, API keys, reviewer text or provider bodies.
CREATE TABLE group_b_execution_metrics (
 id BIGSERIAL PRIMARY KEY,
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 job_id UUID NOT NULL,
 lease_generation BIGINT NOT NULL CHECK(lease_generation>=1),
 block TEXT NOT NULL CHECK(block IN ('B1','B2','B3','B4','B5','B6','B7','B8')),
 queue_kind TEXT NOT NULL CHECK(queue_kind IN ('local','paid')),
 outcome TEXT NOT NULL CHECK(outcome IN
   ('complete','partial','failed','blocked','lease_expired')),
 duration_ms BIGINT CHECK(duration_ms IS NULL OR duration_ms BETWEEN 0 AND 86400000),
 prompt_tokens INTEGER CHECK(prompt_tokens IS NULL OR prompt_tokens BETWEEN 0 AND 1000000),
 completion_tokens INTEGER CHECK(completion_tokens IS NULL OR completion_tokens BETWEEN 0 AND 1000000),
 image_count INTEGER CHECK(image_count IS NULL OR image_count BETWEEN 0 AND 10000),
 charged_usd NUMERIC(20,12)
   CHECK(charged_usd IS NULL OR charged_usd>=0),
 result_sha256 TEXT CHECK(result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'),
 failure_code TEXT CHECK(failure_code IS NULL OR failure_code ~ '^[a-z][a-z0-9_]{0,79}$'),
 trace_sha256 TEXT NOT NULL CHECK(trace_sha256 ~ '^[0-9a-f]{64}$'),
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,job_id,lease_generation),
 FOREIGN KEY(project_id,run_id) REFERENCES group_b_runs(project_id,id),
 FOREIGN KEY(project_id,job_id) REFERENCES group_b_jobs(project_id,id),
 CHECK(queue_kind<>'paid' OR outcome<>'complete' OR charged_usd IS NOT NULL),
 CHECK(outcome<>'complete' OR (result_sha256 IS NOT NULL AND failure_code IS NULL)),
 CHECK(outcome='complete' OR result_sha256 IS NULL),
 CHECK(outcome<>'lease_expired' OR duration_ms IS NULL)
);
CREATE INDEX group_b_metrics_scope_time ON group_b_execution_metrics(
 project_id,recorded_at DESC,id DESC);
CREATE INDEX group_b_metrics_run_outcome ON group_b_execution_metrics(
 project_id,run_id,queue_kind,outcome);
CREATE FUNCTION group_b_execution_metric_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Group B telemetry event is immutable' USING ERRCODE='23514';
END $$;
CREATE TRIGGER trg_group_b_execution_metrics_immutable
 BEFORE UPDATE OR DELETE ON group_b_execution_metrics
 FOR EACH ROW EXECUTE FUNCTION group_b_execution_metric_immutable();
CREATE FUNCTION group_b_execution_metric_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS(
  SELECT 1 FROM group_b_jobs j WHERE
   j.project_id=NEW.project_id AND j.id=NEW.job_id
   AND j.run_id=NEW.run_id AND j.block=NEW.block
   AND j.kind=NEW.queue_kind AND j.lease_generation=NEW.lease_generation
   AND ((NEW.outcome='lease_expired' AND j.state IN ('failed','blocked')) OR
        (NEW.outcome<>'lease_expired' AND j.state=NEW.outcome))
 ) THEN
  RAISE EXCEPTION 'Group B metric must reference exact terminal job generation'
    USING ERRCODE='23514';
 END IF;
 IF NEW.queue_kind='paid' AND
    (NEW.prompt_tokens IS NOT NULL OR NEW.completion_tokens IS NOT NULL OR
     NEW.image_count IS NOT NULL OR NEW.charged_usd IS NOT NULL) THEN
  RAISE EXCEPTION 'Paid metrics require separately verified ledger receipt'
    USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER trg_group_b_execution_metric_identity
 BEFORE INSERT ON group_b_execution_metrics
 FOR EACH ROW EXECUTE FUNCTION group_b_execution_metric_identity();
