-- GC-MOD-03: standalone Group C performance runtime, additive to main <=053.
-- 054/055 belong to sibling Group A/B feature branches and are NOT copied here.
-- Existing C fact/score contracts in 003/004 are immutable: no ALTER to them.
-- This migration only allocates durable orchestration objects. It does NOT
-- authorize QDP intake, assert source rights, approve cohorts or calculate C7.
-- PostgreSQL 17; applied transactionally under intelligence.migrate checksum lock.

CREATE TABLE group_c_inbox (
  event_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  qdp_run_id UUID NOT NULL,
  analysis_group TEXT NOT NULL DEFAULT 'C' CHECK (analysis_group='C'),
  producer_schema TEXT NOT NULL CHECK (producer_schema='qdp-handoff/v1'),
  input_sha256 TEXT NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
  payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  state TEXT NOT NULL DEFAULT 'received'
    CHECK (state IN ('received','accepted','quarantined')),
  reason_code TEXT,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  accepted_at TIMESTAMPTZ,
  UNIQUE(project_id,event_id,video_id,input_sha256),
  CHECK (state <> 'accepted' OR accepted_at IS NOT NULL),
  CHECK (state = 'accepted' OR accepted_at IS NULL)
);
CREATE INDEX idx_group_c_inbox_project_recent
  ON group_c_inbox(project_id,received_at DESC,event_id);
CREATE INDEX idx_group_c_inbox_unaccepted
  ON group_c_inbox(project_id,received_at) WHERE state='received';

-- Any same-event/different-digest replay is an immutable investigation record.
-- The original inbox identity/payload is never replaced by a later delivery.
CREATE TABLE group_c_inbox_conflicts (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event_id UUID NOT NULL REFERENCES group_c_inbox(event_id) ON DELETE CASCADE,
  observed_payload_sha256 TEXT NOT NULL
    CHECK (observed_payload_sha256 ~ '^[0-9a-f]{64}$'),
  observed_input_sha256 TEXT NOT NULL
    CHECK (observed_input_sha256 ~ '^[0-9a-f]{64}$'),
  reason_code TEXT NOT NULL
    CHECK (reason_code IN ('payload_conflict','identity_conflict')),
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(event_id,observed_payload_sha256,observed_input_sha256)
);
CREATE INDEX idx_group_c_inbox_conflicts_event
  ON group_c_inbox_conflicts(event_id,recorded_at DESC);

-- A run references one exact QDP preparation plus the canonical D1 post/source.
-- Snapshot/config/metadata pointers start NULL until verified by later stages.
CREATE TABLE group_c_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  inbox_event_id UUID NOT NULL,
  qdp_input_sha256 TEXT NOT NULL CHECK (qdp_input_sha256 ~ '^[0-9a-f]{64}$'),
  source_id TEXT NOT NULL REFERENCES sources(id),
  idempotency_sha256 TEXT NOT NULL CHECK (idempotency_sha256 ~ '^[0-9a-f]{64}$'),
  snapshot_id BIGINT,
  previous_snapshot_id BIGINT,
  published_metadata_id BIGINT,
  calculation_config_id BIGINT REFERENCES metric_calculation_configs(id),
  creator_cohort_id BIGINT REFERENCES comparison_cohorts(id),
  niche_cohort_id BIGINT REFERENCES comparison_cohorts(id),
  prior_cohort_id BIGINT REFERENCES comparison_cohorts(id),
  source_policy_version_id BIGINT REFERENCES source_policy_versions(id),
  calculated_metric_id BIGINT REFERENCES calculated_metrics(id) ON DELETE CASCADE,
  state TEXT NOT NULL DEFAULT 'queued'
    CHECK (state IN ('queued','running','blocked','partial','completed',
                    'failed','cancelled','invalidated')),
  reason_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  UNIQUE(project_id,id),
  UNIQUE(project_id,id,video_id),
  UNIQUE(project_id,idempotency_sha256),
  FOREIGN KEY(project_id,inbox_event_id,video_id,qdp_input_sha256)
    REFERENCES group_c_inbox(project_id,event_id,video_id,input_sha256) ON DELETE CASCADE,
  FOREIGN KEY(snapshot_id,video_id)
    REFERENCES metric_snapshots(id,video_id) ON DELETE CASCADE,
  FOREIGN KEY(previous_snapshot_id,video_id)
    REFERENCES metric_snapshots(id,video_id) ON DELETE CASCADE,
  FOREIGN KEY(published_metadata_id,video_id)
    REFERENCES source_video_metadata(id,video_id) ON DELETE CASCADE,
  CHECK(snapshot_id IS NULL OR previous_snapshot_id IS NULL OR snapshot_id <> previous_snapshot_id),
  CHECK(state <> 'completed' OR
        (snapshot_id IS NOT NULL AND previous_snapshot_id IS NOT NULL
         AND published_metadata_id IS NOT NULL AND calculation_config_id IS NOT NULL
         AND creator_cohort_id IS NOT NULL AND prior_cohort_id IS NOT NULL
         AND calculated_metric_id IS NOT NULL AND completed_at IS NOT NULL)),
  CHECK(state='completed' OR completed_at IS NULL)
);
CREATE INDEX idx_group_c_runs_project_state
  ON group_c_runs(project_id,state,created_at DESC);
CREATE INDEX idx_group_c_runs_video_latest
  ON group_c_runs(project_id,video_id,created_at DESC);
CREATE INDEX idx_group_c_runs_input
  ON group_c_runs(project_id,inbox_event_id,created_at DESC);

-- Durable worker claims are separate from runs, with fencing generation.
CREATE TABLE group_c_jobs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  video_id BIGINT NOT NULL,
  job_kind TEXT NOT NULL CHECK (job_kind IN ('score','recompute','invalidate','verify_source')),
  idempotency_sha256 TEXT NOT NULL CHECK (idempotency_sha256 ~ '^[0-9a-f]{64}$'),
  state TEXT NOT NULL DEFAULT 'queued'
    CHECK (state IN ('queued','running','completed','failed','cancelled')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 5 CHECK (max_attempts BETWEEN 1 AND 100),
  lease_generation BIGINT NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
  lease_token UUID,
  lease_until TIMESTAMPTZ,
  worker_id TEXT,
  heartbeat_at TIMESTAMPTZ,
  available_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  error_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id,idempotency_sha256),
  FOREIGN KEY(project_id,run_id,video_id)
    REFERENCES group_c_runs(project_id,id,video_id) ON DELETE CASCADE,
  CHECK(attempts <= max_attempts),
  CHECK((state='running') = (lease_token IS NOT NULL)),
  CHECK((state='running') = (lease_until IS NOT NULL)),
  CHECK((state='running') = (worker_id IS NOT NULL)),
  CHECK(state <> 'running' OR (attempts > 0 AND lease_generation > 0))
);
CREATE INDEX idx_group_c_jobs_claim ON group_c_jobs(state,available_at,id)
  WHERE state='queued';
CREATE INDEX idx_group_c_jobs_lease ON group_c_jobs(lease_until)
  WHERE state='running';
CREATE INDEX idx_group_c_jobs_run ON group_c_jobs(project_id,run_id,created_at DESC);

-- Append-only revisions allow incremental recompute without replacing C7 history.
CREATE TABLE group_c_revisions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  video_id BIGINT NOT NULL,
  revision INTEGER NOT NULL CHECK (revision >= 1),
  revision_kind TEXT NOT NULL
    CHECK (revision_kind IN ('new_snapshot','publication_correction',
           'config_changed','cohort_changed','source_revoked','manual_review')),
  evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  evidence JSONB NOT NULL CHECK (jsonb_typeof(evidence)='object'),
  previous_calculated_metric_id BIGINT REFERENCES calculated_metrics(id) ON DELETE CASCADE,
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id,run_id,revision),
  FOREIGN KEY(project_id,run_id,video_id)
    REFERENCES group_c_runs(project_id,id,video_id) ON DELETE CASCADE
);
CREATE INDEX idx_group_c_revisions_run
  ON group_c_revisions(project_id,run_id,revision DESC);

-- Outbox carries a verified persisted calculated_metrics ID, never a C7 guess.
-- Delivery status is mutable; the pinned payload/lineage is not.
CREATE TABLE group_c_outbox (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  video_id BIGINT NOT NULL,
  calculated_metric_id BIGINT NOT NULL REFERENCES calculated_metrics(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL DEFAULT 'group_c.result_ready/v1'
    CHECK(event_type='group_c.result_ready/v1'),
  event_key TEXT NOT NULL CHECK (length(btrim(event_key)) BETWEEN 12 AND 200),
  payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
  state TEXT NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending','leased','delivered','failed')),
  delivery_attempts INTEGER NOT NULL DEFAULT 0 CHECK(delivery_attempts >= 0),
  lease_token UUID,
  lease_until TIMESTAMPTZ,
  delivered_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id,event_key),
  UNIQUE(project_id,run_id,calculated_metric_id,event_type),
  FOREIGN KEY(project_id,run_id,video_id)
    REFERENCES group_c_runs(project_id,id,video_id) ON DELETE CASCADE,
  CHECK((state='leased') = (lease_token IS NOT NULL)),
  CHECK((state='leased') = (lease_until IS NOT NULL)),
  CHECK((state='delivered') = (delivered_at IS NOT NULL))
);
CREATE INDEX idx_group_c_outbox_pending
  ON group_c_outbox(state,created_at,id) WHERE state IN ('pending','failed');
CREATE INDEX idx_group_c_outbox_lease
  ON group_c_outbox(lease_until) WHERE state='leased';

-- Protect the identity of replay/delivery records. Policy-compliant deletes
-- remain possible via future retention logic; updates to provenance do not.
CREATE FUNCTION group_c_inbox_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (OLD.event_id,OLD.project_id,OLD.video_id,OLD.qdp_run_id,
      OLD.analysis_group,OLD.producer_schema,OLD.input_sha256,
      OLD.payload_sha256,OLD.received_at)
     IS DISTINCT FROM
     (NEW.event_id,NEW.project_id,NEW.video_id,NEW.qdp_run_id,
      NEW.analysis_group,NEW.producer_schema,NEW.input_sha256,
      NEW.payload_sha256,NEW.received_at) THEN
    RAISE EXCEPTION 'Group C inbox identity immutable' USING ERRCODE='23514';
  END IF;
  IF OLD.state IN ('accepted','quarantined') AND NEW.state IS DISTINCT FROM OLD.state THEN
    RAISE EXCEPTION 'Group C terminal receipt cannot reopen' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_group_c_inbox_immutable BEFORE UPDATE ON group_c_inbox
  FOR EACH ROW EXECUTE FUNCTION group_c_inbox_identity_guard();

CREATE FUNCTION group_c_run_lineage_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE score RECORD;
BEGIN
  IF TG_OP='UPDATE' THEN
    IF (OLD.id,OLD.project_id,OLD.video_id,OLD.inbox_event_id,
      OLD.qdp_input_sha256,OLD.source_id,OLD.idempotency_sha256,OLD.created_at)
      IS DISTINCT FROM
     (NEW.id,NEW.project_id,NEW.video_id,NEW.inbox_event_id,
      NEW.qdp_input_sha256,NEW.source_id,NEW.idempotency_sha256,NEW.created_at) THEN
      RAISE EXCEPTION 'Group C run identity immutable' USING ERRCODE='23514';
    END IF;
  END IF;
  IF NOT EXISTS (SELECT 1 FROM group_c_inbox i WHERE i.event_id=NEW.inbox_event_id
                  AND i.project_id=NEW.project_id AND i.video_id=NEW.video_id
                  AND i.state='accepted') THEN
    RAISE EXCEPTION 'Group C run requires durably accepted inbox'
      USING ERRCODE='23514';
  END IF;
  IF NEW.state='completed' THEN
    SELECT post_id,snapshot_id,previous_snapshot_id,published_metadata_id,
           calculation_config_id,creator_cohort_id,niche_cohort_id,prior_cohort_id,
           score_status INTO score
      FROM calculated_metrics WHERE id=NEW.calculated_metric_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Group C complete run missing persisted metric' USING ERRCODE='23514';
    END IF;
    IF score.post_id IS DISTINCT FROM NEW.video_id
       OR score.snapshot_id IS DISTINCT FROM NEW.snapshot_id
       OR score.previous_snapshot_id IS DISTINCT FROM NEW.previous_snapshot_id
       OR score.published_metadata_id IS DISTINCT FROM NEW.published_metadata_id
       OR score.calculation_config_id IS DISTINCT FROM NEW.calculation_config_id
       OR score.creator_cohort_id IS DISTINCT FROM NEW.creator_cohort_id
       OR score.niche_cohort_id IS DISTINCT FROM NEW.niche_cohort_id
       OR score.prior_cohort_id IS DISTINCT FROM NEW.prior_cohort_id
       OR score.score_status IS DISTINCT FROM 'complete' THEN
      RAISE EXCEPTION 'Group C complete run must reference matching complete metric'
        USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_group_c_run_lineage BEFORE INSERT OR UPDATE ON group_c_runs
  FOR EACH ROW EXECUTE FUNCTION group_c_run_lineage_guard();

CREATE FUNCTION group_c_job_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (OLD.project_id,OLD.run_id,OLD.video_id,OLD.job_kind,
      OLD.idempotency_sha256,OLD.created_at)
     IS DISTINCT FROM
     (NEW.project_id,NEW.run_id,NEW.video_id,NEW.job_kind,
      NEW.idempotency_sha256,NEW.created_at) THEN
    RAISE EXCEPTION 'Group C job identity immutable' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_group_c_job_identity BEFORE UPDATE ON group_c_jobs
  FOR EACH ROW EXECUTE FUNCTION group_c_job_identity_guard();

CREATE FUNCTION group_c_revision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Group C revision is append-only' USING ERRCODE='23514';
END $$;
CREATE TRIGGER trg_group_c_revision_immutable BEFORE UPDATE ON group_c_revisions
  FOR EACH ROW EXECUTE FUNCTION group_c_revision_guard();
CREATE TRIGGER trg_group_c_inbox_conflict_immutable BEFORE UPDATE ON group_c_inbox_conflicts
  FOR EACH ROW EXECUTE FUNCTION group_c_revision_guard();

CREATE FUNCTION group_c_outbox_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE score RECORD;
BEGIN
  IF TG_OP='UPDATE' THEN
    IF (OLD.id,OLD.project_id,OLD.run_id,OLD.video_id,
      OLD.calculated_metric_id,OLD.event_type,OLD.event_key,
      OLD.payload_sha256,OLD.payload,OLD.created_at)
     IS DISTINCT FROM
     (NEW.id,NEW.project_id,NEW.run_id,NEW.video_id,
      NEW.calculated_metric_id,NEW.event_type,NEW.event_key,
      NEW.payload_sha256,NEW.payload,NEW.created_at) THEN
      RAISE EXCEPTION 'Group C outbox immutable payload' USING ERRCODE='23514';
    END IF;
  END IF;
  SELECT post_id,score_status INTO score FROM calculated_metrics
   WHERE id=NEW.calculated_metric_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Group C outbox score not found' USING ERRCODE='23514';
  END IF;
  IF score.post_id IS DISTINCT FROM NEW.video_id
     OR score.score_status IS DISTINCT FROM 'complete' OR NOT EXISTS(
       SELECT 1 FROM group_c_runs r WHERE r.id=NEW.run_id
        AND r.project_id=NEW.project_id AND r.video_id=NEW.video_id
        AND r.state='completed' AND r.calculated_metric_id=NEW.calculated_metric_id
     ) THEN
    RAISE EXCEPTION 'Group C outbox requires committed matching complete C score'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_group_c_outbox_guard BEFORE INSERT OR UPDATE ON group_c_outbox
  FOR EACH ROW EXECUTE FUNCTION group_c_outbox_guard();
