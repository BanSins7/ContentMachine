-- PIE-MOD-25: additive, project-scoped, append-only candidate registry.
-- Stage24 supported publication is impossible. This is NOT a live UAE rights gate.
-- Migration 200 is intentionally left untouched for checksum compatibility.
CREATE TABLE pie_pattern_registry_series (
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
  series_id TEXT NOT NULL CHECK (series_id ~ '^[0-9a-f]{64}$'),
  hypothesis_sha256 TEXT NOT NULL CHECK (hypothesis_sha256 ~ '^[0-9a-f]{64}$'),
  factor_id TEXT NOT NULL CHECK (factor_id ~ '^[a-z][a-z0-9_]{0,79}$'),
  owner_id TEXT NOT NULL CHECK (length(btrim(owner_id)) BETWEEN 1 AND 160),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (project_id,series_id),
  UNIQUE (project_id,hypothesis_sha256,owner_id),
  UNIQUE (project_id,series_id,hypothesis_sha256)
);

CREATE TABLE pie_pattern_registry_revisions (
  revision_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  series_id TEXT NOT NULL,
  hypothesis_sha256 TEXT NOT NULL CHECK(hypothesis_sha256 ~ '^[0-9a-f]{64}$'),
  revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 2147483647),
  evidence_sha256 TEXT NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$'),
  payload_sha256 TEXT NOT NULL CHECK(payload_sha256 ~ '^[0-9a-f]{64}$'),
  source_snapshot_sha256 TEXT NOT NULL CHECK(source_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
  source_quality_sha256 TEXT NOT NULL CHECK(source_quality_sha256 ~ '^[0-9a-f]{64}$'),
  stratum_sha256 TEXT NOT NULL CHECK(stratum_sha256 ~ '^[0-9a-f]{64}$'),
  source_rows_sha256 TEXT NOT NULL CHECK(source_rows_sha256 ~ '^[0-9a-f]{64}$'),
  validation_id TEXT NOT NULL CHECK(validation_id ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL CHECK(status IN ('exploratory','rejected','needs_review')),
  payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object'),
  -- Even direct SQL writes must be candidate-only and match the stored identity.
  -- IS NOT DISTINCT FROM also rejects absent/null JSON properties (CHECK NULL pitfall).
  CHECK ((payload->>'schema_version') IS NOT DISTINCT FROM
         'pie-immutable-pattern-registry-entry-candidate/v1'),
  CHECK ((payload->>'analysis_revision_status') IS NOT DISTINCT FROM status),
  CHECK ((payload->>'project_id') IS NOT DISTINCT FROM project_id),
  CHECK ((payload->>'candidate_id') IS NOT DISTINCT FROM series_id),
  CHECK ((payload->>'hypothesis_sha256') IS NOT DISTINCT FROM hypothesis_sha256),
  CHECK ((payload->>'evidence_sha256') IS NOT DISTINCT FROM evidence_sha256),
  CHECK ((payload->>'source_pattern_quality_sha256') IS NOT DISTINCT FROM source_quality_sha256),
  CHECK ((payload->>'validation_id') IS NOT DISTINCT FROM validation_id),
  CHECK ((payload->>'source_rows_sha256') IS NOT DISTINCT FROM source_rows_sha256),
  CHECK ((payload->'approved_live') IS NOT DISTINCT FROM 'false'::jsonb),
  CHECK ((payload->'may_publish') IS NOT DISTINCT FROM 'false'::jsonb),
  CHECK ((payload->'can_release_to_registry_as_supported') IS NOT DISTINCT FROM 'false'::jsonb),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  FOREIGN KEY(project_id,series_id,hypothesis_sha256)
    REFERENCES pie_pattern_registry_series(project_id,series_id,hypothesis_sha256)
    ON DELETE CASCADE,
  UNIQUE(project_id,series_id,revision),
  UNIQUE(project_id,series_id,evidence_sha256),
  UNIQUE(project_id,revision_id)
);
CREATE INDEX pie_pattern_registry_revisions_scope
  ON pie_pattern_registry_revisions(project_id,series_id,revision DESC);
CREATE INDEX pie_pattern_registry_revisions_quality
  ON pie_pattern_registry_revisions(project_id,status,created_at DESC);
CREATE INDEX pie_pattern_registry_revisions_origin
  ON pie_pattern_registry_revisions(project_id,source_snapshot_sha256,source_quality_sha256);

-- Direct modification is forbidden; reanalysis creates a NEW revision.
-- A project's explicit cascade-delete/retention purge remains possible.
CREATE TRIGGER pie_registry_series_immutable
  BEFORE UPDATE ON pie_pattern_registry_series
  FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();
CREATE TRIGGER pie_registry_revision_immutable
  BEFORE UPDATE ON pie_pattern_registry_revisions
  FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();

-- No SQL principal can manufacture a supported revision through this table:
-- the status constraint excludes it, independently of application code.
