-- GC-MOD-24: additive score invalidation and restricted erasure coordination.
-- Never modify historical scores or signed review/cohort material.
-- Canonical GC03/GC20 immutable evidence remains protected by FK RESTRICT.
CREATE TABLE group_c_score_invalidations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE RESTRICT,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE RESTRICT,
  calculated_metric_id BIGINT NOT NULL REFERENCES calculated_metrics(id) ON DELETE RESTRICT,
  completed_run_id UUID NOT NULL,
  reason_code TEXT NOT NULL CHECK (reason_code ~ '^[a-z][a-z0-9_]{0,79}$'),
  evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  invalidation_version TEXT NOT NULL DEFAULT 'group-c-retention/v1'
    CHECK (invalidation_version='group-c-retention/v1'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,calculated_metric_id),
  UNIQUE(project_id,evidence_sha256),
  FOREIGN KEY(project_id,completed_run_id,video_id)
    REFERENCES group_c_runs(project_id,id,video_id) ON DELETE RESTRICT
);
CREATE INDEX idx_gc_invalidations_project
  ON group_c_score_invalidations(project_id,created_at DESC);

-- This is a legal/operations request, NOT proof of physical erasure.
-- Hard delete of GC20 receipts/cohort members/review decisions is intentionally
-- NOT attempted: applied migrations make them immutable and RESTRICT-linked.
CREATE TABLE group_c_erasure_requests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE RESTRICT,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE RESTRICT,
  calculated_metric_id BIGINT NOT NULL REFERENCES calculated_metrics(id) ON DELETE RESTRICT,
  authorization_event_id UUID NOT NULL UNIQUE,
  authority_assertion_sha256 TEXT NOT NULL CHECK (authority_assertion_sha256 ~ '^[0-9a-f]{64}$'),
  reason_code TEXT NOT NULL CHECK (reason_code IN ('legal_erasure_required','retention_erase_required')),
  state TEXT NOT NULL DEFAULT 'external_erasure_execution_required'
    CHECK (state='external_erasure_execution_required'),
  request_version TEXT NOT NULL DEFAULT 'group-c-erasure-request/v1'
    CHECK (request_version='group-c-erasure-request/v1'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,calculated_metric_id)
);
CREATE INDEX idx_gc_erasure_project
  ON group_c_erasure_requests(project_id,created_at DESC);

CREATE FUNCTION group_c_retention_append_only_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  cert RECORD;
  score RECORD;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'Group C retention and erasure audit immutable'
      USING ERRCODE='23514';
  END IF;
  SELECT project_id,video_id,calculated_metric_id INTO cert
  FROM group_c_calculation_receipts
  WHERE calculated_metric_id=NEW.calculated_metric_id;
  SELECT post_id,score_status INTO score
  FROM calculated_metrics WHERE id=NEW.calculated_metric_id;
  IF NOT FOUND OR cert.project_id IS DISTINCT FROM NEW.project_id
     OR cert.video_id IS DISTINCT FROM NEW.video_id
     OR cert.calculated_metric_id IS DISTINCT FROM NEW.calculated_metric_id
     OR score.post_id IS DISTINCT FROM NEW.video_id
     OR score.score_status IS DISTINCT FROM 'complete'
  THEN
    RAISE EXCEPTION 'Group C retention audit must reference certified complete C7'
      USING ERRCODE='23514';
  END IF;
  IF TG_TABLE_NAME='group_c_score_invalidations' THEN
    IF NOT EXISTS(
      SELECT 1 FROM group_c_runs r
      WHERE r.project_id=NEW.project_id AND r.video_id=NEW.video_id
        AND r.id=NEW.completed_run_id AND r.state='completed'
        AND r.calculated_metric_id=NEW.calculated_metric_id
    ) THEN
      RAISE EXCEPTION 'Group C invalidation requires exact completed run'
        USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_gc_invalidation_append_only
  BEFORE INSERT OR UPDATE OR DELETE ON group_c_score_invalidations
  FOR EACH ROW EXECUTE FUNCTION group_c_retention_append_only_guard();
CREATE TRIGGER trg_gc_erasure_request_append_only
  BEFORE INSERT OR UPDATE OR DELETE ON group_c_erasure_requests
  FOR EACH ROW EXECUTE FUNCTION group_c_retention_append_only_guard();

-- No stale pending event may newly lease/deliver once an explicit source/
-- peer retention invalidation is recorded. GC25 transport must ALSO invoke
-- the live source/policy/peer read gate before any external delivery.
CREATE FUNCTION group_c_outbox_invalidation_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF (TG_OP='INSERT' OR
      (TG_OP='UPDATE' AND
       NEW.state IN ('leased','delivered') AND
       NEW.state IS DISTINCT FROM OLD.state)) AND EXISTS(
      SELECT 1 FROM group_c_score_invalidations i
      WHERE i.project_id=NEW.project_id
        AND i.calculated_metric_id=NEW.calculated_metric_id
  ) THEN
    RAISE EXCEPTION 'Group C invalidated C7 cannot enter Analytics delivery'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_gc_outbox_invalidation
  BEFORE INSERT OR UPDATE ON group_c_outbox
  FOR EACH ROW EXECUTE FUNCTION group_c_outbox_invalidation_guard();
