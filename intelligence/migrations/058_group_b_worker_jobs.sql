-- GB-MOD-11: additive per-block, durable, fenced worker jobs. No paid HTTP.
-- Retain 005/055/056/057 unchanged. Join B project+run on every operation.
CREATE TABLE group_b_jobs (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 block TEXT NOT NULL CHECK (block IN ('B1','B2','B3','B4','B5','B6','B7','B8')),
 kind TEXT NOT NULL CHECK (kind IN ('local','paid')),
 depends_on TEXT[] NOT NULL DEFAULT '{}',
 attempt_id UUID,
 evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
 state TEXT NOT NULL DEFAULT 'ready'
   CHECK (state IN ('ready','running','complete','partial','failed','blocked','cancelled')),
 lease_token UUID,
 lease_generation BIGINT NOT NULL DEFAULT 0 CHECK (lease_generation>=0),
 lease_until TIMESTAMPTZ,
 attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts>=0),
 max_attempts INTEGER NOT NULL DEFAULT 1 CHECK (max_attempts BETWEEN 1 AND 10),
 result_sha256 TEXT CHECK (result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'),
 last_error_code TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,id),
 UNIQUE(project_id,run_id,block),
 FOREIGN KEY(project_id,run_id) REFERENCES group_b_runs(project_id,id),
 FOREIGN KEY(project_id,run_id,block)
   REFERENCES group_b_block_states(project_id,run_id,block),
 FOREIGN KEY(project_id,attempt_id)
   REFERENCES group_b_model_attempts(project_id,id),
 CHECK ((kind='local' AND attempt_id IS NULL) OR
        (kind='paid' AND attempt_id IS NOT NULL)),
 CHECK ((state='running' AND lease_token IS NOT NULL AND lease_until IS NOT NULL) OR
        (state<>'running' AND lease_token IS NULL AND lease_until IS NULL)),
 CHECK (state <> 'complete' OR result_sha256 IS NOT NULL),
 CHECK (state = 'complete' OR result_sha256 IS NULL),
 CHECK (array_position(depends_on,block) IS NULL),
 CHECK (array_length(depends_on,1) IS NULL OR array_length(depends_on,1)<=7)
);
CREATE INDEX group_b_jobs_claim_index
 ON group_b_jobs(project_id,created_at) WHERE state IN ('ready','running');
CREATE INDEX group_b_jobs_lease_index
 ON group_b_jobs(lease_until) WHERE state='running';

CREATE TABLE group_b_job_events (
 id BIGSERIAL PRIMARY KEY,
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 job_id UUID NOT NULL,
 lease_generation BIGINT NOT NULL CHECK(lease_generation>=0),
 event_type TEXT NOT NULL
  CHECK(event_type IN ('enqueued','claimed','heartbeat','reclaimed','completed',
                       'partial','failed','blocked','cancelled','lease_expired')),
 evidence_sha256 TEXT NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$'),
 result_sha256 TEXT CHECK(result_sha256 IS NULL OR result_sha256 ~ '^[0-9a-f]{64}$'),
 reason_code TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(project_id,job_id) REFERENCES group_b_jobs(project_id,id),
 FOREIGN KEY(project_id,run_id) REFERENCES group_b_runs(project_id,id)
);
CREATE INDEX group_b_job_events_job
 ON group_b_job_events(project_id,job_id,created_at,id);

CREATE FUNCTION group_b_job_event_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Group B job event is immutable' USING ERRCODE='23514';
END $$;
CREATE TRIGGER trg_group_b_job_events_immutable
 BEFORE UPDATE OR DELETE ON group_b_job_events
 FOR EACH ROW EXECUTE FUNCTION group_b_job_event_immutable();

CREATE FUNCTION group_b_job_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' AND
    (OLD.id,OLD.project_id,OLD.run_id,OLD.block,OLD.kind,OLD.depends_on,
     OLD.attempt_id,OLD.evidence_sha256,OLD.max_attempts,OLD.created_at)
    IS DISTINCT FROM
    (NEW.id,NEW.project_id,NEW.run_id,NEW.block,NEW.kind,NEW.depends_on,
     NEW.attempt_id,NEW.evidence_sha256,NEW.max_attempts,NEW.created_at)
 THEN RAISE EXCEPTION 'Group B job identity immutable' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' AND NEW.lease_generation<OLD.lease_generation
 THEN RAISE EXCEPTION 'Group B fencing generation is monotonic' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' AND NEW.attempts<OLD.attempts
 THEN RAISE EXCEPTION 'Group B job attempts monotonic' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' AND OLD.state IN ('complete','failed','cancelled','blocked')
 THEN RAISE EXCEPTION 'Terminal Group B job immutable' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER trg_group_b_job_identity_guard
 BEFORE UPDATE ON group_b_jobs
 FOR EACH ROW EXECUTE FUNCTION group_b_job_identity_guard();

-- A worker claim is not the same thing as an authorized provider dispatch.
-- Paid job claim is intentionally deferred until executor authorization is
-- implemented; a provider call cannot be claimed from this schema alone.
