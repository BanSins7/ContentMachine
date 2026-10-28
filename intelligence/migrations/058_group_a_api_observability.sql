-- GA-MOD-21: isolated operator audit and telemetry; no external module SQL.
-- Raw paths, transcripts, IPs, browser headers, tokens and media bytes
-- MUST NOT appear in operator audit event bodies.
CREATE TABLE ga21_operator_events (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id UUID NOT NULL,
    project_id TEXT NOT NULL,
    event_kind TEXT NOT NULL CHECK (event_kind IN (
      'dag_scheduled','run_paused','run_resumed','run_cancelled',
      'local_step_retried','run_invalidated'
    )),
    step_id BIGINT REFERENCES ga_steps(id) ON DELETE SET NULL,
    actor_ref_sha256 TEXT NOT NULL CHECK (actor_ref_sha256 ~ '^[a-f0-9]{64}$'),
    event_sha256 TEXT NOT NULL CHECK (event_sha256 ~ '^[a-f0-9]{64}$'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (run_id,project_id)
      REFERENCES ga_runs(id,project_id) ON DELETE CASCADE,
    UNIQUE(run_id,event_sha256)
);
CREATE INDEX idx_ga21_events_project_cursor
  ON ga21_operator_events(project_id,run_id,id DESC);
CREATE OR REPLACE FUNCTION ga21_events_immutable()
RETURNS trigger LANGUAGE plpgsql AS $ga21_event$
BEGIN
    IF TG_OP='DELETE' AND NOT EXISTS(
        SELECT 1 FROM ga_runs WHERE id=OLD.run_id
    ) THEN
        -- Legitimate parent project/run retention cascade only.
        RETURN OLD;
    END IF;
    IF TG_OP='UPDATE' AND OLD.step_id IS NOT NULL
       AND NEW.step_id IS NULL
       AND to_jsonb(NEW)-'step_id'=to_jsonb(OLD)-'step_id'
    THEN
        -- FK ON DELETE SET NULL from pruned step keeps the original event.
        RETURN NEW;
    END IF;
    RAISE EXCEPTION 'GA21 operator event is immutable'
      USING ERRCODE='23514';
END;
$ga21_event$;
CREATE TRIGGER ga21_operator_events_immutable
  BEFORE UPDATE OR DELETE ON ga21_operator_events
  FOR EACH ROW EXECUTE FUNCTION ga21_events_immutable();

-- Aggregate *reserved slot time*, not actual CPU usage/energy/provider cost.
-- This view never implies that physical GPU isolation or paid budget telemetry
-- exists. Outbox attempts count as delivery attempts, not billed model calls.
CREATE OR REPLACE VIEW ga21_local_stage_telemetry AS
SELECT r.project_id,s.run_id,s.id AS step_id,s.step_key,s.step_kind,
       s.state,s.attempts,
       spec.cpu_slots,spec.gpu_slots,spec.max_runtime_sec,
       coalesce(sum(greatest(0,
         extract(epoch from (coalesce(a.finished_at,now())-a.started_at)))),
         0)::numeric(14,2) AS elapsed_attempt_sec,
       coalesce(sum(greatest(0,
         extract(epoch from (coalesce(a.finished_at,now())-a.started_at)))
         *spec.cpu_slots),0)::numeric(14,2) AS reserved_cpu_slot_sec,
       coalesce(sum(greatest(0,
         extract(epoch from (coalesce(a.finished_at,now())-a.started_at)))
         *spec.gpu_slots),0)::numeric(14,2) AS reserved_gpu_slot_sec
  FROM ga_steps s JOIN ga_runs r ON r.id=s.run_id
  JOIN ga_step_resource_specs spec ON spec.step_id=s.id
  LEFT JOIN ga_step_attempts a ON a.step_id=s.id
 GROUP BY r.project_id,s.run_id,s.id,s.step_key,s.step_kind,
          s.state,s.attempts,spec.cpu_slots,spec.gpu_slots,
          spec.max_runtime_sec;
