-- M11 SRE LIVE core storage. ADDITIVE ONLY; no historical SRE migrations edited.
-- This does NOT create finance, provider dispatch, model results or worker.
-- Future LIVE executor MUST still authenticate owner, recheck S2/M08 rights,
-- and perform transactional M12+ planning and M16+ lease/ledger fencing.
-- Use the next prefix after the merged-main 053 baseline.

-- Composite immutable authorities: the RTB publication must be exactly
-- linked to the SRE taxonomy version in the same workspace.
CREATE UNIQUE INDEX sre_m11_c9_project_publication_version
  ON tb_sre_taxonomy_links(project_id,builder_publication_id,sre_taxonomy_version_id);

-- A content row is referenced by its full version, project/video, source,
-- reviewed policy and exact immutable content digest. Legacy content with
-- NULL sha cannot satisfy the LIVE child FK; it is not silently upgraded.
CREATE UNIQUE INDEX sre_m11_content_origin_sha
  ON sre_content_versions
    (id,project_id,video_id,source_id,source_policy_version_id,content_sha256);

CREATE TABLE sre_live_runs (
  run_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES sre_workspaces(project_id),
  publication_id UUID NOT NULL,
  taxonomy_version_id BIGINT NOT NULL,
  taxonomy_sha256 TEXT NOT NULL CHECK(taxonomy_sha256 ~ '^[0-9a-f]{64}$'),
  pipeline_config_id BIGINT NOT NULL,
  pipeline_sha256 TEXT NOT NULL CHECK(pipeline_sha256 ~ '^[0-9a-f]{64}$'),
  request_sha256 TEXT NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
  manifest JSONB NOT NULL CHECK (jsonb_typeof(manifest)='object'),
  manifest_sha256 TEXT NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
  idempotency_key TEXT NOT NULL CHECK (
    length(idempotency_key) BETWEEN 1 AND 120
    AND idempotency_key ~ '^[A-Za-z0-9._-]+$'),
  execution_mode TEXT NOT NULL DEFAULT 'live' CHECK(execution_mode='live'),
  state TEXT NOT NULL DEFAULT 'planned' CHECK(state IN (
    'planned','pending_consent','queued','running','waiting_source',
    'paused_budget','paused_rights','unknown','partial','completed','failed','cancelled')),
  expected_items INTEGER NOT NULL CHECK(expected_items BETWEEN 1 AND 10000),
  finished_items INTEGER NOT NULL DEFAULT 0 CHECK(finished_items>=0),
  blocked_items INTEGER NOT NULL DEFAULT 0 CHECK(blocked_items>=0),
  cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
  lease_generation BIGINT NOT NULL DEFAULT 0 CHECK(lease_generation>=0),
  worker_token UUID,
  lease_until TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  UNIQUE(run_id,project_id),
  UNIQUE(project_id,idempotency_key),
  FOREIGN KEY(project_id,publication_id,taxonomy_version_id)
    REFERENCES tb_sre_taxonomy_links(
      project_id,builder_publication_id,sre_taxonomy_version_id),
  FOREIGN KEY(pipeline_config_id,project_id)
    REFERENCES sre_pipeline_configs(id,project_id),
  CHECK(finished_items+blocked_items<=expected_items),
  CHECK(state<>'completed' OR
    (finished_items=expected_items AND blocked_items=0)),
  CHECK(state<>'partial' OR
    (finished_items>0 AND finished_items<expected_items)),
  CHECK((worker_token IS NULL AND lease_until IS NULL) OR
        (worker_token IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE INDEX sre_m11_live_run_queue ON sre_live_runs(project_id,state,created_at,run_id);
CREATE INDEX sre_m11_live_run_lease ON sre_live_runs(state,lease_until)
  WHERE worker_token IS NOT NULL;

CREATE TABLE sre_live_items (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id UUID NOT NULL,
  project_id TEXT NOT NULL,
  video_id BIGINT NOT NULL,
  content_version_id BIGINT NOT NULL,
  content_sha256 TEXT NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
  source_id TEXT NOT NULL,
  source_policy_version_id BIGINT NOT NULL,
  discovery_id BIGINT NOT NULL,
  metadata_observation_id BIGINT,
  content_revision INTEGER NOT NULL CHECK(content_revision>0),
  origin JSONB NOT NULL CHECK(jsonb_typeof(origin)='object'),
  expected_hypothesis_keys JSONB NOT NULL CHECK (
    jsonb_typeof(expected_hypothesis_keys)='array'
    AND jsonb_array_length(expected_hypothesis_keys) BETWEEN 1 AND 48),
  contexts_sha256 TEXT NOT NULL CHECK(contexts_sha256 ~ '^[0-9a-f]{64}$'),
  manifest_sha256 TEXT NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
  state TEXT NOT NULL DEFAULT 'not_evaluated' CHECK(state IN (
    'not_evaluated','queued','running','waiting_source','paused_budget',
    'paused_rights','unknown','partial','completed','failed','cancelled')),
  lease_generation BIGINT NOT NULL DEFAULT 0 CHECK(lease_generation>=0),
  lease_until TIMESTAMPTZ,
  worker_token UUID,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 100),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  UNIQUE(run_id,video_id),
  UNIQUE(id,run_id,project_id),
  FOREIGN KEY(run_id,project_id)
    REFERENCES sre_live_runs(run_id,project_id),
  FOREIGN KEY(content_version_id,project_id,video_id,source_id,
              source_policy_version_id,content_sha256)
    REFERENCES sre_content_versions(id,project_id,video_id,source_id,
                                    source_policy_version_id,content_sha256),
  FOREIGN KEY(project_id,discovery_id)
    REFERENCES sre_project_discoveries(project_id,discovery_id),
  FOREIGN KEY(metadata_observation_id)
    REFERENCES source_video_metadata(id),
  CHECK(state<>'running' OR
        (lease_until IS NOT NULL AND worker_token IS NOT NULL)),
  CHECK((worker_token IS NULL AND lease_until IS NULL) OR
        (worker_token IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE INDEX sre_m11_live_item_queue ON sre_live_items(project_id,state,id);
CREATE INDEX sre_m11_live_item_lease ON sre_live_items(state,lease_until)
  WHERE worker_token IS NOT NULL;
CREATE INDEX sre_m11_live_item_by_run ON sre_live_items(project_id,run_id,state,video_id);

CREATE TABLE sre_live_batches (
  batch_id UUID PRIMARY KEY,
  run_id UUID NOT NULL,
  project_id TEXT NOT NULL,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  manifest JSONB NOT NULL CHECK(jsonb_typeof(manifest)='object'),
  manifest_sha256 TEXT NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
  input_sha256 TEXT NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
  state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN (
    'queued','running','waiting_source','paused_budget','paused_rights',
    'unknown','partial','completed','failed','cancelled')),
  lease_generation BIGINT NOT NULL DEFAULT 0 CHECK(lease_generation>=0),
  worker_token UUID,
  lease_until TIMESTAMPTZ,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 100),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  UNIQUE(run_id,ordinal),
  UNIQUE(batch_id,run_id,project_id),
  FOREIGN KEY(run_id,project_id)
    REFERENCES sre_live_runs(run_id,project_id),
  CHECK(state<>'running' OR
        (lease_until IS NOT NULL AND worker_token IS NOT NULL)),
  CHECK((worker_token IS NULL AND lease_until IS NULL) OR
        (worker_token IS NOT NULL AND lease_until IS NOT NULL))
);
CREATE INDEX sre_m11_live_batch_queue ON sre_live_batches(project_id,state,ordinal);
CREATE INDEX sre_m11_live_batch_lease ON sre_live_batches(state,lease_until)
  WHERE worker_token IS NOT NULL;

-- A source/observation/reference checksum is DB-pinned to a real immutable
-- S3 snapshot. JSON sent by a caller cannot attest its own origin.
CREATE FUNCTION sre_m11_check_content_origin() RETURNS trigger
LANGUAGE plpgsql AS $sre_m11_origin$
DECLARE c RECORD;
BEGIN
  SELECT revision,provenance,metadata_observation_id,intake_fingerprint
    INTO c FROM sre_content_versions
   WHERE id=NEW.content_version_id AND project_id=NEW.project_id
     AND video_id=NEW.video_id AND source_id=NEW.source_id
     AND source_policy_version_id=NEW.source_policy_version_id
     AND content_sha256=NEW.content_sha256;
  IF NOT FOUND OR c.intake_fingerprint IS NULL OR
     c.revision IS DISTINCT FROM NEW.content_revision OR
     c.metadata_observation_id IS DISTINCT FROM NEW.metadata_observation_id OR
     (c.provenance->>'discovery_id') IS DISTINCT FROM NEW.discovery_id::text
  THEN
    RAISE EXCEPTION 'M11 LIVE item requires exact versioned D1 origin';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM sre_project_discoveries d JOIN discoveries x
      ON x.id=d.discovery_id
    WHERE d.project_id=NEW.project_id AND d.video_id=NEW.video_id
      AND d.discovery_id=NEW.discovery_id
      AND x.video_id=NEW.video_id AND x.source_id=NEW.source_id
  ) THEN
    RAISE EXCEPTION 'M11 LIVE item discovery/source/project mismatch';
  END IF;
  RETURN NEW;
END; $sre_m11_origin$;
CREATE TRIGGER sre_m11_item_origin BEFORE INSERT ON sre_live_items
  FOR EACH ROW EXECUTE FUNCTION sre_m11_check_content_origin();

CREATE FUNCTION sre_m11_guard_run() RETURNS trigger
LANGUAGE plpgsql AS $sre_m11_run$
BEGIN
  IF (OLD.run_id,OLD.project_id,OLD.publication_id,OLD.taxonomy_version_id,
      OLD.taxonomy_sha256,OLD.pipeline_config_id,OLD.pipeline_sha256,
      OLD.request_sha256,OLD.manifest,OLD.manifest_sha256,
      OLD.idempotency_key,OLD.execution_mode,OLD.expected_items,OLD.created_at)
     IS DISTINCT FROM
     (NEW.run_id,NEW.project_id,NEW.publication_id,NEW.taxonomy_version_id,
      NEW.taxonomy_sha256,NEW.pipeline_config_id,NEW.pipeline_sha256,
      NEW.request_sha256,NEW.manifest,NEW.manifest_sha256,
      NEW.idempotency_key,NEW.execution_mode,NEW.expected_items,NEW.created_at)
  THEN RAISE EXCEPTION 'M11 LIVE run immutable identity'; END IF;
  IF NEW.lease_generation<OLD.lease_generation OR
     NEW.lease_generation>OLD.lease_generation+1 OR
     (NEW.worker_token IS DISTINCT FROM OLD.worker_token
       AND NEW.worker_token IS NOT NULL
       AND NEW.lease_generation<>OLD.lease_generation+1)
  THEN RAISE EXCEPTION 'M11 LIVE run stale or unfenced lease'; END IF;
  RETURN NEW;
END; $sre_m11_run$;
CREATE TRIGGER sre_m11_run_guard BEFORE UPDATE ON sre_live_runs
  FOR EACH ROW EXECUTE FUNCTION sre_m11_guard_run();

CREATE FUNCTION sre_m11_guard_item() RETURNS trigger
LANGUAGE plpgsql AS $sre_m11_item$
BEGIN
  IF (OLD.run_id,OLD.project_id,OLD.video_id,OLD.content_version_id,
      OLD.content_sha256,OLD.source_id,OLD.source_policy_version_id,
      OLD.discovery_id,OLD.metadata_observation_id,OLD.content_revision,
      OLD.origin,OLD.expected_hypothesis_keys,OLD.contexts_sha256,
      OLD.manifest_sha256,OLD.created_at)
     IS DISTINCT FROM
     (NEW.run_id,NEW.project_id,NEW.video_id,NEW.content_version_id,
      NEW.content_sha256,NEW.source_id,NEW.source_policy_version_id,
      NEW.discovery_id,NEW.metadata_observation_id,NEW.content_revision,
      NEW.origin,NEW.expected_hypothesis_keys,NEW.contexts_sha256,
      NEW.manifest_sha256,NEW.created_at)
  THEN RAISE EXCEPTION 'M11 LIVE item immutable input'; END IF;
  IF NEW.lease_generation<OLD.lease_generation OR
     NEW.lease_generation>OLD.lease_generation+1 OR
     (NEW.worker_token IS DISTINCT FROM OLD.worker_token
       AND NEW.worker_token IS NOT NULL
       AND NEW.lease_generation<>OLD.lease_generation+1)
  THEN RAISE EXCEPTION 'M11 LIVE item stale or unfenced lease'; END IF;
  RETURN NEW;
END; $sre_m11_item$;
CREATE TRIGGER sre_m11_item_guard BEFORE UPDATE ON sre_live_items
  FOR EACH ROW EXECUTE FUNCTION sre_m11_guard_item();

CREATE FUNCTION sre_m11_guard_batch() RETURNS trigger
LANGUAGE plpgsql AS $sre_m11_batch$
BEGIN
  IF (OLD.batch_id,OLD.run_id,OLD.project_id,OLD.ordinal,OLD.manifest,
      OLD.manifest_sha256,OLD.input_sha256,OLD.created_at)
     IS DISTINCT FROM
     (NEW.batch_id,NEW.run_id,NEW.project_id,NEW.ordinal,NEW.manifest,
      NEW.manifest_sha256,NEW.input_sha256,NEW.created_at)
  THEN RAISE EXCEPTION 'M11 LIVE batch immutable manifest'; END IF;
  IF NEW.lease_generation<OLD.lease_generation OR
     NEW.lease_generation>OLD.lease_generation+1 OR
     (NEW.worker_token IS DISTINCT FROM OLD.worker_token
       AND NEW.worker_token IS NOT NULL
       AND NEW.lease_generation<>OLD.lease_generation+1)
  THEN RAISE EXCEPTION 'M11 LIVE batch stale or unfenced lease'; END IF;
  RETURN NEW;
END; $sre_m11_batch$;
CREATE TRIGGER sre_m11_batch_guard BEFORE UPDATE ON sre_live_batches
  FOR EACH ROW EXECUTE FUNCTION sre_m11_guard_batch();

-- Accidental hard-deletes must not destroy immutable audit lineage. A
-- privileged database administrator could disable triggers, as with other
-- SRE tables; this is NOT row-level security against the DB owner.
CREATE FUNCTION sre_m11_block_delete() RETURNS trigger
LANGUAGE plpgsql AS $sre_m11_delete$
BEGIN RAISE EXCEPTION 'M11 LIVE records are append-only (no hard delete)'; END;
$sre_m11_delete$;
CREATE TRIGGER sre_m11_no_run_delete BEFORE DELETE ON sre_live_runs
  FOR EACH ROW EXECUTE FUNCTION sre_m11_block_delete();
CREATE TRIGGER sre_m11_no_item_delete BEFORE DELETE ON sre_live_items
  FOR EACH ROW EXECUTE FUNCTION sre_m11_block_delete();
CREATE TRIGGER sre_m11_no_batch_delete BEFORE DELETE ON sre_live_batches
  FOR EACH ROW EXECUTE FUNCTION sre_m11_block_delete();

-- Postgres grants no PUBLIC table privileges by default. Make the separation
-- explicit; callers must use a trusted role with service-side owner checks.
REVOKE ALL ON sre_live_runs,sre_live_items,sre_live_batches FROM PUBLIC;
REVOKE ALL ON SEQUENCE sre_live_items_id_seq FROM PUBLIC;
