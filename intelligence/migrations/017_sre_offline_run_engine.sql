-- SRE S4: durable ZERO-COST offline-only execution. No provider calls.
-- Separate companion tables prevent fabricated fake runs being mistaken for
-- real LLM/quote/consent operations. Prior migrations remain unchanged.

ALTER TABLE sre_classification_runs
  DROP CONSTRAINT IF EXISTS sre_classification_runs_status_check;
ALTER TABLE sre_classification_runs
  ADD CONSTRAINT sre_classification_runs_status_check
  CHECK (status IN ('queued','running','completed','partial','failed','cancelled'));

CREATE TABLE sre_offline_runs (
  run_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL,
  publication_id UUID NOT NULL,
  taxonomy_sha256 TEXT NOT NULL CHECK (taxonomy_sha256 ~ '^[0-9a-f]{64}$'),
  pipeline_sha256 TEXT NOT NULL CHECK (pipeline_sha256 ~ '^[0-9a-f]{64}$'),
  request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  idempotency_key TEXT NOT NULL CHECK (
    length(idempotency_key) BETWEEN 1 AND 120
    AND idempotency_key ~ '^[a-zA-Z0-9._-]+$'),
  status TEXT NOT NULL DEFAULT 'queued'
    CHECK(status IN ('queued','running','completed','partial','failed','cancelled')),
  cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  UNIQUE(project_id,idempotency_key),
  UNIQUE(run_id,project_id),
  FOREIGN KEY (run_id,project_id)
    REFERENCES sre_classification_runs(id,project_id) ON DELETE CASCADE
);
CREATE INDEX sre_offline_runs_status_idx ON sre_offline_runs(status,created_at);

CREATE TABLE sre_offline_items (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id UUID NOT NULL,
  project_id TEXT NOT NULL,
  video_id BIGINT NOT NULL,
  content_version_id BIGINT NOT NULL,
  content_sha256 TEXT NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
  contexts JSONB NOT NULL CHECK(jsonb_typeof(contexts)='array'
                             AND jsonb_array_length(contexts)>0),
  contexts_sha256 TEXT NOT NULL CHECK(contexts_sha256 ~ '^[0-9a-f]{64}$'),
  state TEXT NOT NULL DEFAULT 'queued'
    CHECK(state IN ('queued','running','completed','blocked_rights','failed','cancelled')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
  max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 10),
  lease_generation BIGINT NOT NULL DEFAULT 0 CHECK(lease_generation>=0),
  lease_until TIMESTAMPTZ,
  worker_token UUID,
  error_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  UNIQUE(run_id,video_id),
  FOREIGN KEY(run_id,project_id) REFERENCES sre_offline_runs(run_id,project_id)
    ON DELETE CASCADE,
  FOREIGN KEY(content_version_id,project_id,video_id)
    REFERENCES sre_content_versions(id,project_id,video_id),
  FOREIGN KEY(project_id,video_id)
    REFERENCES sre_project_videos(project_id,video_id),
  CHECK(state <> 'running' OR (lease_until IS NOT NULL AND worker_token IS NOT NULL))
);
CREATE INDEX sre_offline_claim ON sre_offline_items(state,lease_until,created_at,id)
  WHERE state IN ('queued','running');
CREATE INDEX sre_offline_run_items ON sre_offline_items(project_id,run_id,state);

CREATE OR REPLACE FUNCTION sre_s4_guard_offline_run() RETURNS trigger
LANGUAGE plpgsql AS $sre_s4_run$
BEGIN
  IF NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.project_id IS DISTINCT FROM OLD.project_id
     OR NEW.publication_id IS DISTINCT FROM OLD.publication_id
     OR NEW.taxonomy_sha256 IS DISTINCT FROM OLD.taxonomy_sha256
     OR NEW.pipeline_sha256 IS DISTINCT FROM OLD.pipeline_sha256
     OR NEW.request_sha256 IS DISTINCT FROM OLD.request_sha256
     OR NEW.idempotency_key IS DISTINCT FROM OLD.idempotency_key
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'S4 offline run lineage is immutable';
  END IF;
  RETURN NEW;
END $sre_s4_run$;
CREATE TRIGGER sre_s4_immutable_offline_runs
 BEFORE UPDATE ON sre_offline_runs
 FOR EACH ROW EXECUTE FUNCTION sre_s4_guard_offline_run();

CREATE OR REPLACE FUNCTION sre_s4_guard_offline_item() RETURNS trigger
LANGUAGE plpgsql AS $sre_s4_item$
BEGIN
  IF NEW.run_id IS DISTINCT FROM OLD.run_id
     OR NEW.project_id IS DISTINCT FROM OLD.project_id
     OR NEW.video_id IS DISTINCT FROM OLD.video_id
     OR NEW.content_version_id IS DISTINCT FROM OLD.content_version_id
     OR NEW.content_sha256 IS DISTINCT FROM OLD.content_sha256
     OR NEW.contexts IS DISTINCT FROM OLD.contexts
     OR NEW.contexts_sha256 IS DISTINCT FROM OLD.contexts_sha256
     OR NEW.max_attempts IS DISTINCT FROM OLD.max_attempts
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'S4 offline item inputs are immutable';
  END IF;
  RETURN NEW;
END $sre_s4_item$;
CREATE TRIGGER sre_s4_immutable_offline_items
 BEFORE UPDATE ON sre_offline_items
 FOR EACH ROW EXECUTE FUNCTION sre_s4_guard_offline_item();

-- Only internal S4 workers can finalize a fake result. The documented owner
-- boundary is in the Python service; never publish as real model evidence.
