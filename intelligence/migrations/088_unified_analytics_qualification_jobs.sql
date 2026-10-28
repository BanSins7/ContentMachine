-- UA-MOD-17 | Forward-only durable qualification queue (PostgreSQL 17).
-- UAE-owned only. No peer writes, public grants, scheduler or score publication.
-- A queued or succeeded job never proves SRE/Group A/B/C qualification.

CREATE TABLE uae_qualification_jobs (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 request_id UUID NOT NULL,
 idempotency_key TEXT NOT NULL CHECK
   (length(idempotency_key) BETWEEN 8 AND 180 AND idempotency_key ~ '^[A-Za-z0-9_.:-]+$'),
 batch_key TEXT NOT NULL CHECK
   (length(batch_key) BETWEEN 1 AND 120 AND batch_key ~ '^[A-Za-z0-9_.:-]+$'),
 request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
 job_sha256 TEXT NOT NULL CHECK (job_sha256 ~ '^[0-9a-f]{64}$'),
 priority SMALLINT NOT NULL DEFAULT 0 CHECK (priority BETWEEN -10 AND 10),
 max_attempts SMALLINT NOT NULL DEFAULT 4 CHECK (max_attempts BETWEEN 1 AND 8),
 attempts SMALLINT NOT NULL DEFAULT 0 CHECK (attempts >= 0 AND attempts <= max_attempts),
 lease_generation INTEGER NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
 state TEXT NOT NULL DEFAULT 'pending' CHECK
   (state IN ('pending','leased','retry','succeeded','dead','cancelled')),
 available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 lease_token UUID,
 lease_owner TEXT CHECK (lease_owner IS NULL OR
   (length(lease_owner) BETWEEN 3 AND 120 AND lease_owner ~ '^[A-Za-z0-9_.:-]+$')),
 leased_until TIMESTAMPTZ,
 snapshot_id BIGINT,
 last_error_code TEXT CHECK (last_error_code IS NULL OR
   (length(last_error_code) BETWEEN 2 AND 80 AND last_error_code ~ '^[a-z][a-z0-9_]+$')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 finished_at TIMESTAMPTZ,
 UNIQUE(project_id,id),
 UNIQUE(project_id,idempotency_key),
 UNIQUE(project_id,request_id,batch_key),
 FOREIGN KEY(project_id,request_id)
   REFERENCES uae_qualification_requests(project_id,id) ON DELETE CASCADE,
 FOREIGN KEY(project_id,snapshot_id)
   REFERENCES uae_unified_snapshots(project_id,id) ON DELETE CASCADE,
 CHECK (
   (state='leased' AND lease_token IS NOT NULL
     AND lease_owner IS NOT NULL AND leased_until IS NOT NULL)
   OR (state<>'leased' AND lease_token IS NULL
     AND lease_owner IS NULL AND leased_until IS NULL)),
 CHECK ((state='succeeded') = (snapshot_id IS NOT NULL)),
 CHECK ((state IN ('succeeded','dead','cancelled')) = (finished_at IS NOT NULL)),
 CHECK (state<>'dead' OR attempts=max_attempts),
 CHECK (state NOT IN ('leased','retry','succeeded','dead') OR attempts>0),
 CHECK (updated_at >= created_at)
);
CREATE INDEX uae_jobs_claim_idx
 ON uae_qualification_jobs(project_id, priority DESC, available_at,created_at,id)
 WHERE state IN ('pending','retry');
CREATE INDEX uae_jobs_expired_idx
 ON uae_qualification_jobs(project_id,leased_until,id) WHERE state='leased';
CREATE INDEX uae_jobs_dlq_idx
 ON uae_qualification_jobs(project_id,finished_at DESC,id) WHERE state='dead';

-- Readiness means only an ACTIVE canonical source and the three verified
-- QDP handoff receipts on the exact request/run/video. It does NOT attest
-- effective live SRE, approved B review or approved Group C score.
-- This is re-evaluated on enqueue, claim, heartbeat and success.
CREATE FUNCTION uae_queue_request_ready(p_project TEXT,p_request UUID)
RETURNS BOOLEAN LANGUAGE sql STABLE AS $uae_queue_ready$
 SELECT EXISTS (
  SELECT 1 FROM uae_qualification_requests r
  JOIN sources s ON s.id=r.source_id
  JOIN source_policy_versions p ON p.id=r.source_policy_version_id
     AND p.source_id=s.id
  JOIN sre_project_videos pv ON pv.project_id=r.project_id
     AND pv.video_id=r.video_id
  WHERE r.project_id=p_project AND r.id=p_request
    AND r.state IN ('registered','processing')
    AND s.status='enabled' AND p.is_active IS TRUE
    AND p.reviewed_at IS NOT NULL AND p.reviewed_at <= clock_timestamp()
    AND s.updated_at <= clock_timestamp()
    AND NOT EXISTS (
      SELECT 1 FROM source_policy_versions newer
      WHERE newer.source_id=s.id AND newer.id<>p.id AND newer.is_active
    )
    AND s.can_store_metadata IS TRUE
    AND s.can_analyze_media IS TRUE
    AND s.can_store_metrics IS TRUE
    AND s.can_derive_scores IS TRUE
    AND p.permissions->>'can_store_metadata'='true'
    AND p.permissions->>'can_analyze_media'='true'
    AND p.permissions->>'can_store_metrics'='true'
    AND p.permissions->>'can_derive_scores'='true'
    AND s.retention_days > 0
    AND (CASE WHEN p.retention_policy->>'derived_retention_days'
                    ~ '^[0-9]{1,3}$'
            THEN (p.retention_policy->>'derived_retention_days')::integer
            ELSE 0 END) BETWEEN 1 AND 365
    AND (SELECT count(DISTINCT h.analysis_group)
         FROM uae_qdp_handoff_inbox h
         WHERE h.project_id=r.project_id
           AND h.video_id=r.video_id AND h.qdp_run_id=r.qdp_run_id
           AND h.analysis_group IN ('A','B','C'))=3
 )
$uae_queue_ready$;

-- Only status/lease fields are mutable; enforce state graph, monotonic
-- epoch and attempt counts even if someone bypasses the Python facade.
CREATE FUNCTION uae_job_lifecycle_guard() RETURNS trigger
LANGUAGE plpgsql AS $uae_job_guard$
BEGIN
 IF TG_OP='UPDATE' THEN
  IF (to_jsonb(OLD)-'state'-'attempts'-'lease_generation'
      -'available_at'-'lease_token'-'lease_owner'-'leased_until'
      -'snapshot_id'-'last_error_code'-'updated_at'-'finished_at')
     IS DISTINCT FROM
     (to_jsonb(NEW)-'state'-'attempts'-'lease_generation'
      -'available_at'-'lease_token'-'lease_owner'-'leased_until'
      -'snapshot_id'-'last_error_code'-'updated_at'-'finished_at')
    OR OLD.state IN ('succeeded','dead','cancelled')
    OR (OLD.state IN ('pending','retry') AND NEW.state NOT IN
        ('leased','cancelled'))
    OR (OLD.state='leased' AND NEW.state NOT IN
        ('leased','retry','succeeded','dead','cancelled'))
    OR (NEW.state='leased' AND OLD.state IN ('pending','retry') AND
        (NEW.attempts<>OLD.attempts+1
         OR NEW.lease_generation<>OLD.lease_generation+1))
    OR (NEW.state='leased' AND OLD.state='leased' AND
        (NEW.attempts<>OLD.attempts OR
         NEW.lease_generation<>OLD.lease_generation OR
         NEW.lease_token IS DISTINCT FROM OLD.lease_token OR
         NEW.lease_owner IS DISTINCT FROM OLD.lease_owner OR
         NEW.leased_until <= OLD.leased_until))
    OR (NEW.state<>'leased' AND
        (NEW.attempts<>OLD.attempts OR
         NEW.lease_generation<>OLD.lease_generation))
    OR (NEW.state='dead' AND NEW.attempts<>NEW.max_attempts)
    OR (NEW.state='succeeded' AND
        (OLD.state<>'leased' OR NEW.snapshot_id IS NULL
         OR OLD.leased_until<=clock_timestamp()))
    OR (NEW.state='retry' AND
        (OLD.state<>'leased' OR NEW.attempts>=NEW.max_attempts))
    OR NEW.updated_at<OLD.updated_at
  THEN
   RAISE EXCEPTION 'UAE qualification job lifecycle violation'
     USING ERRCODE='23514';
  END IF;
 END IF;
 RETURN NEW;
END
$uae_job_guard$;
CREATE TRIGGER uae_job_lifecycle_trg
 BEFORE UPDATE ON uae_qualification_jobs
 FOR EACH ROW EXECUTE FUNCTION uae_job_lifecycle_guard();

-- Append-only transitions (heartbeats do not create new audit events).
CREATE TABLE uae_qualification_job_events (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 project_id TEXT NOT NULL,
 job_id UUID NOT NULL,
 lease_generation INTEGER NOT NULL CHECK(lease_generation>=0),
 from_state TEXT,
 to_state TEXT NOT NULL CHECK(to_state IN
   ('pending','leased','retry','succeeded','dead','cancelled')),
 error_code TEXT,
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(project_id,job_id)
   REFERENCES uae_qualification_jobs(project_id,id) ON DELETE CASCADE
);
CREATE INDEX uae_job_event_history_idx
 ON uae_qualification_job_events(project_id,job_id,id);
CREATE FUNCTION uae_job_audit_transition() RETURNS trigger
LANGUAGE plpgsql AS $uae_job_audit$
BEGIN
 IF TG_OP='INSERT' THEN
  INSERT INTO uae_qualification_job_events(
   project_id,job_id,lease_generation,from_state,to_state,error_code)
  VALUES(NEW.project_id,NEW.id,NEW.lease_generation,
         NULL,NEW.state,NEW.last_error_code);
 ELSIF OLD.state IS DISTINCT FROM NEW.state THEN
  INSERT INTO uae_qualification_job_events(
   project_id,job_id,lease_generation,from_state,to_state,error_code)
  VALUES(NEW.project_id,NEW.id,NEW.lease_generation,
         OLD.state,NEW.state,NEW.last_error_code);
 END IF;
 RETURN NEW;
END
$uae_job_audit$;
CREATE TRIGGER uae_job_audit_trg
 AFTER INSERT OR UPDATE ON uae_qualification_jobs
 FOR EACH ROW EXECUTE FUNCTION uae_job_audit_transition();
CREATE FUNCTION uae_job_event_immutable() RETURNS trigger
LANGUAGE plpgsql AS $uae_job_event_immutable$
BEGIN
 RAISE EXCEPTION 'UAE job transition history cannot be updated'
  USING ERRCODE='23514';
END
$uae_job_event_immutable$;
CREATE TRIGGER uae_job_event_update_guard
 BEFORE UPDATE ON uae_qualification_job_events
 FOR EACH ROW EXECUTE FUNCTION uae_job_event_immutable();

-- No GRANT, publisher, cron, network provider, AI inference or automatic worker.
