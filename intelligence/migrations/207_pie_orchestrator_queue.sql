-- PIE-MOD-33: an additive orchestrator around migration200 pie_jobs/outbox.
-- Never allow paid-model retries or auto-claim for a paid attempt.
CREATE TABLE pie_job_execution_specs (
  project_id TEXT NOT NULL,
  job_id UUID NOT NULL,
  task_code TEXT NOT NULL CHECK (task_code IN (
    'dataset_snapshot','pattern_discovery','pattern_validation',
    'insight_deterministic','insight_verification','review_internal','source_invalidation',
    'paid_model_manual_handoff')),
  execution_policy TEXT NOT NULL CHECK (execution_policy IN (
    'safe_deterministic','manual_paid_only')),
  input_sha256 TEXT NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
  spec_sha256 TEXT NOT NULL CHECK(spec_sha256 ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,job_id),
  FOREIGN KEY(project_id,job_id) REFERENCES pie_jobs(project_id,id) ON DELETE CASCADE,
  CHECK ((task_code='paid_model_manual_handoff' AND execution_policy='manual_paid_only')
    OR (task_code<>'paid_model_manual_handoff' AND execution_policy='safe_deterministic'))
);
CREATE TRIGGER pie_job_execution_spec_immutable
 BEFORE UPDATE ON pie_job_execution_specs FOR EACH ROW
 EXECUTE FUNCTION pie_reject_evidence_update();

-- All events and safe checkpoint payloads are metadata only.
CREATE TABLE pie_job_orchestration_events (
  project_id TEXT NOT NULL,
  job_id UUID NOT NULL,
  event_seq INTEGER NOT NULL CHECK(event_seq BETWEEN 1 AND 100000),
  prev_event_sha256 TEXT CHECK(prev_event_sha256 IS NULL OR prev_event_sha256 ~ '^[0-9a-f]{64}$'),
  event_sha256 TEXT NOT NULL CHECK(event_sha256 ~ '^[0-9a-f]{64}$'),
  event_code TEXT NOT NULL CHECK(event_code IN (
    'enqueued','blocked_paid_manual','claimed','heartbeat','checkpoint',
    'retry_scheduled','expired_requeued','completed','failed_terminal','cancelled')),
  fence_token BIGINT NOT NULL CHECK(fence_token>=0),
  payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,job_id,event_seq),
  UNIQUE(project_id,event_sha256),
  FOREIGN KEY(project_id,job_id) REFERENCES pie_job_execution_specs(project_id,job_id) ON DELETE CASCADE,
  CHECK ((event_seq=1 AND prev_event_sha256 IS NULL) OR
         (event_seq>1 AND prev_event_sha256 IS NOT NULL)),
  CHECK ((payload->>'schema_version') IS NOT DISTINCT FROM 'pie-job-event/v1'),
  CHECK ((payload->>'project_id') IS NOT DISTINCT FROM project_id),
  CHECK ((payload->>'job_id') IS NOT DISTINCT FROM job_id::text),
  CHECK ((payload->>'event_seq') IS NOT DISTINCT FROM event_seq::text),
  CHECK ((payload->>'prev_event_sha256') IS NOT DISTINCT FROM prev_event_sha256),
  CHECK ((payload->>'event_code') IS NOT DISTINCT FROM event_code),
  CHECK ((payload->>'fence_token') IS NOT DISTINCT FROM fence_token::text),
  CHECK ((payload->'can_publish') IS NOT DISTINCT FROM 'false'::jsonb),
  CHECK ((payload->'may_issue_paid_request') IS NOT DISTINCT FROM 'false'::jsonb)
);
CREATE INDEX pie_job_orchestration_history
 ON pie_job_orchestration_events(project_id,job_id,event_seq DESC);
CREATE TRIGGER pie_job_orchestration_event_immutable
 BEFORE UPDATE ON pie_job_orchestration_events FOR EACH ROW
 EXECUTE FUNCTION pie_reject_evidence_update();

CREATE TABLE pie_job_checkpoints (
  project_id TEXT NOT NULL,
  job_id UUID NOT NULL,
  fence_token BIGINT NOT NULL CHECK(fence_token>0),
  checkpoint_seq INTEGER NOT NULL CHECK(checkpoint_seq BETWEEN 1 AND 10000),
  checkpoint_sha256 TEXT NOT NULL CHECK(checkpoint_sha256 ~ '^[0-9a-f]{64}$'),
  state_digest_sha256 TEXT NOT NULL CHECK(state_digest_sha256 ~ '^[0-9a-f]{64}$'),
  safe_metadata JSONB NOT NULL CHECK(jsonb_typeof(safe_metadata)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,job_id,fence_token,checkpoint_seq),
  UNIQUE(project_id,job_id,checkpoint_sha256),
  FOREIGN KEY(project_id,job_id) REFERENCES pie_job_execution_specs(project_id,job_id) ON DELETE CASCADE,
  CHECK ((safe_metadata->>'schema_version') IS NOT DISTINCT FROM 'pie-job-checkpoint/v1'),
  CHECK ((safe_metadata->>'project_id') IS NOT DISTINCT FROM project_id),
  CHECK ((safe_metadata->>'job_id') IS NOT DISTINCT FROM job_id::text),
  CHECK ((safe_metadata->>'fence_token') IS NOT DISTINCT FROM fence_token::text),
  CHECK ((safe_metadata->>'checkpoint_seq') IS NOT DISTINCT FROM checkpoint_seq::text),
  CHECK ((safe_metadata->>'state_digest_sha256') IS NOT DISTINCT FROM state_digest_sha256),
  CHECK ((safe_metadata->'can_publish') IS NOT DISTINCT FROM 'false'::jsonb),
  CHECK ((safe_metadata->'may_issue_paid_request') IS NOT DISTINCT FROM 'false'::jsonb)
);
CREATE TRIGGER pie_job_checkpoint_immutable
 BEFORE UPDATE ON pie_job_checkpoints FOR EACH ROW
 EXECUTE FUNCTION pie_reject_evidence_update();

-- Additional integrity at the SQL boundary. The host connection role must
-- restrict direct writes: this trigger is not an auth substitute.
CREATE FUNCTION pie_job_execution_spec_guard()
RETURNS trigger LANGUAGE plpgsql AS $pie33spec$
DECLARE existing RECORD;
BEGIN
  SELECT kind,state,attempts,max_attempts INTO existing FROM pie_jobs
   WHERE project_id=NEW.project_id AND id=NEW.job_id FOR UPDATE;
  IF NOT FOUND OR existing.attempts<>0 THEN
    RAISE EXCEPTION 'PIE job spec must bind unstarted exact job';
  END IF;
  IF (NEW.execution_policy='manual_paid_only' AND
      (existing.kind<>'insight' OR existing.state<>'blocked'
       OR existing.max_attempts<>1)) OR
     (NEW.execution_policy='safe_deterministic' AND existing.state<>'queued') OR
     (existing.kind<>'dataset' AND NEW.task_code='dataset_snapshot') OR
     (existing.kind<>'discovery' AND NEW.task_code='pattern_discovery') OR
     (existing.kind<>'validation' AND NEW.task_code='pattern_validation') OR
     (existing.kind<>'insight' AND NEW.task_code IN
        ('insight_deterministic','insight_verification','paid_model_manual_handoff')) OR
     (existing.kind<>'review' AND NEW.task_code='review_internal') OR
     (existing.kind<>'invalidation' AND NEW.task_code='source_invalidation') THEN
    RAISE EXCEPTION 'PIE job kind/policy mismatches source queue state';
  END IF;
  RETURN NEW;
END $pie33spec$;
CREATE TRIGGER pie_job_spec_guard_insert
 BEFORE INSERT ON pie_job_execution_specs FOR EACH ROW
 EXECUTE FUNCTION pie_job_execution_spec_guard();

CREATE FUNCTION pie_job_event_chain_guard()
RETURNS trigger LANGUAGE plpgsql AS $pie33event$
DECLARE last_row RECORD;
BEGIN
  -- Lifecycle updater already owns FOR UPDATE on the parent job. A direct SQL
  -- writer is serialized on this same row before checking event lineage.
  PERFORM 1 FROM pie_jobs WHERE project_id=NEW.project_id
    AND id=NEW.job_id FOR UPDATE;
  SELECT event_seq,event_sha256 INTO last_row
    FROM pie_job_orchestration_events
   WHERE project_id=NEW.project_id AND job_id=NEW.job_id
   ORDER BY event_seq DESC LIMIT 1;
  IF FOUND THEN
    IF NEW.event_seq<>last_row.event_seq+1
       OR NEW.prev_event_sha256 IS DISTINCT FROM last_row.event_sha256 THEN
      RAISE EXCEPTION 'PIE job event parent/fence lineage mismatch';
    END IF;
  ELSIF NEW.event_seq<>1 OR NEW.prev_event_sha256 IS NOT NULL THEN
    RAISE EXCEPTION 'PIE job event starts at sequence one';
  END IF;
  RETURN NEW;
END $pie33event$;
CREATE TRIGGER pie_job_event_chain_guard_insert
 BEFORE INSERT ON pie_job_orchestration_events FOR EACH ROW
 EXECUTE FUNCTION pie_job_event_chain_guard();

CREATE FUNCTION pie_job_checkpoint_fence_guard()
RETURNS trigger LANGUAGE plpgsql AS $pie33checkpoint$
DECLARE live RECORD;
BEGIN
  SELECT state,fence_token,lease_until,leased_by INTO live FROM pie_jobs
   WHERE project_id=NEW.project_id AND id=NEW.job_id FOR UPDATE;
  IF NOT FOUND OR live.state<>'leased'
     OR live.fence_token<>NEW.fence_token OR live.leased_by IS NULL
     OR live.lease_until<=clock_timestamp() THEN
    RAISE EXCEPTION 'PIE checkpoint requires current fenced live worker lease';
  END IF;
  RETURN NEW;
END $pie33checkpoint$;
CREATE TRIGGER pie_job_checkpoint_fence_guard_insert
 BEFORE INSERT ON pie_job_checkpoints FOR EACH ROW
 EXECUTE FUNCTION pie_job_checkpoint_fence_guard();

-- Server-side invariant for direct updates as well as Python fenced WHERE.
CREATE FUNCTION pie_job_transition_guard()
RETURNS trigger LANGUAGE plpgsql AS $pie33transition$
DECLARE policy TEXT;
BEGIN
  SELECT execution_policy INTO policy FROM pie_job_execution_specs
   WHERE project_id=OLD.project_id AND job_id=OLD.id;
  IF NEW.project_id<>OLD.project_id OR NEW.id<>OLD.id OR NEW.run_id<>OLD.run_id
     OR NEW.idempotency_key<>OLD.idempotency_key OR NEW.kind<>OLD.kind
     OR NEW.max_attempts<>OLD.max_attempts OR NEW.created_at<>OLD.created_at
     OR policy IS NULL THEN
    RAISE EXCEPTION 'PIE job immutable identity or missing execution spec';
  END IF;
  IF policy='manual_paid_only' THEN
    IF OLD.state<>'blocked' OR NEW.state<>'cancelled'
       OR NEW.attempts<>0 OR NEW.fence_token<>0
       OR NEW.leased_by IS NOT NULL OR NEW.lease_until IS NOT NULL THEN
      RAISE EXCEPTION 'PIE paid task is manual, non-claimable and nonretryable';
    END IF;
    RETURN NEW;
  END IF;
  IF (OLD.state IN ('completed','failed','cancelled','blocked') OR
      NEW.attempts>OLD.max_attempts OR NEW.attempts<OLD.attempts OR
      NEW.fence_token<OLD.fence_token) THEN
    RAISE EXCEPTION 'PIE worker cannot alter terminal task or reduce fence';
  END IF;
  IF NEW.state='leased' AND
     ((OLD.state='queued' AND
       (NEW.attempts<>OLD.attempts+1 OR
        NEW.fence_token<>OLD.fence_token+1 OR
        NEW.lease_until IS NULL OR NEW.leased_by IS NULL))
      OR (OLD.state='leased' AND
       (NEW.attempts<>OLD.attempts OR NEW.fence_token<>OLD.fence_token OR
        NEW.leased_by<>OLD.leased_by OR NEW.lease_until<OLD.lease_until))) THEN
    RAISE EXCEPTION 'PIE lease fencing monotonicity violated';
  END IF;
  IF NEW.state='leased' AND OLD.state NOT IN ('queued','leased') THEN
    RAISE EXCEPTION 'PIE cannot lease a nonqueued job';
  END IF;
  IF NEW.state<>'leased' AND
     (NEW.lease_until IS NOT NULL OR NEW.leased_by IS NOT NULL) THEN
    RAISE EXCEPTION 'PIE terminal/nonleased job cannot retain worker';
  END IF;
  IF OLD.state='queued' AND NEW.state NOT IN ('leased','cancelled') THEN
    RAISE EXCEPTION 'PIE queued job transition invalid';
  END IF;
  IF OLD.state='leased' AND NEW.state NOT IN
      ('leased','queued','completed','failed','cancelled') THEN
    RAISE EXCEPTION 'PIE live job transition invalid';
  END IF;
  IF OLD.state='leased' AND NEW.state<>'leased' AND
     (NEW.attempts<>OLD.attempts OR NEW.fence_token<>OLD.fence_token) THEN
    RAISE EXCEPTION 'PIE terminal transition cannot change lease token';
  END IF;
  RETURN NEW;
END $pie33transition$;
CREATE TRIGGER pie_job_transition_guard_update
 BEFORE UPDATE ON pie_jobs FOR EACH ROW
 EXECUTE FUNCTION pie_job_transition_guard();

-- Every object uses existing project/owner FK constraints and durable outbox.
-- A model credential, source row, video or prompt must never enter metadata.
