-- PIE-MOD-27: append-only exact-source invalidation ledger and recompute signals.
-- Additive schema after 201; never edit prior immutable migrations/evidence.
-- A late revocation is an OR-set tombstone: it is not discarded by event time.
CREATE TABLE pie_pattern_registry_invalidations (
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
  event_id TEXT NOT NULL CHECK (event_id ~ '^[0-9a-f]{64}$'),
  target_kind TEXT NOT NULL CHECK (target_kind IN (
    'uae_manifest','dataset_snapshot','matched_rows','quality_report',
    'holdout','numeric_report','robustness_report','study_design','stratum'
  )),
  target_sha256 TEXT NOT NULL CHECK (target_sha256 ~ '^[0-9a-f]{64}$'),
  replacement_sha256 TEXT CHECK (replacement_sha256 IS NULL OR replacement_sha256 ~ '^[0-9a-f]{64}$'),
  producer TEXT NOT NULL CHECK (producer IN (
    'uae','qdp','group_a','group_b','group_c','sre',
    'source_policy','retention','research_config'
  )),
  reason_code TEXT NOT NULL CHECK (reason_code IN (
    'upstream_revision','source_rights_revoked','source_retention_expired',
    'configuration_changed','new_source_snapshot','source_evidence_invalidated'
  )),
  event_effective_at TIMESTAMPTZ NOT NULL,
  source_event_id TEXT NOT NULL CHECK (length(btrim(source_event_id)) BETWEEN 1 AND 160),
  notice JSONB NOT NULL CHECK (jsonb_typeof(notice)='object'),
  received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (project_id,event_id),
  CHECK (replacement_sha256 IS NULL OR replacement_sha256 <> target_sha256),
  CHECK ((notice->>'schema_version') IS NOT DISTINCT FROM 'pie-source-invalidation-notice/v1'),
  CHECK ((notice->>'project_id') IS NOT DISTINCT FROM project_id),
  CHECK ((notice->>'target_kind') IS NOT DISTINCT FROM target_kind),
  CHECK ((notice->>'target_sha256') IS NOT DISTINCT FROM target_sha256),
  CHECK ((notice->>'producer') IS NOT DISTINCT FROM producer),
  CHECK ((notice->>'reason_code') IS NOT DISTINCT FROM reason_code),
  CHECK ((notice->>'source_event_id') IS NOT DISTINCT FROM source_event_id)
);
CREATE INDEX pie_pattern_invalidations_target_lookup
  ON pie_pattern_registry_invalidations(project_id,target_kind,target_sha256);

-- A request is a durable signal only, never a paid execution, authorization,
-- queue lease or guarantee that a trusted UAE source can now be recomputed.
CREATE TABLE pie_pattern_registry_recompute_requests (
  project_id TEXT NOT NULL,
  event_id TEXT NOT NULL,
  series_id TEXT NOT NULL,
  source_revision_id UUID NOT NULL,
  request_key TEXT NOT NULL CHECK (request_key ~ '^[0-9a-f]{64}$'),
  requested_state TEXT NOT NULL DEFAULT 'awaiting_trusted_source_and_review'
    CHECK (requested_state='awaiting_trusted_source_and_review'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (project_id,event_id,series_id,source_revision_id),
  UNIQUE (project_id,request_key),
  FOREIGN KEY (project_id,event_id) REFERENCES
    pie_pattern_registry_invalidations(project_id,event_id) ON DELETE CASCADE,
  FOREIGN KEY (project_id,series_id) REFERENCES
    pie_pattern_registry_series(project_id,series_id) ON DELETE CASCADE,
  FOREIGN KEY (project_id,source_revision_id) REFERENCES
    pie_pattern_registry_revisions(project_id,revision_id) ON DELETE CASCADE
);
CREATE INDEX pie_pattern_recompute_project_series
  ON pie_pattern_registry_recompute_requests(project_id,series_id,created_at DESC);

CREATE TRIGGER pie_registry_invalidations_immutable
  BEFORE UPDATE ON pie_pattern_registry_invalidations
  FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();
CREATE TRIGGER pie_registry_recompute_immutable
  BEFORE UPDATE ON pie_pattern_registry_recompute_requests
  FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();

-- Intentional: no "latest-event wins" field and no UPDATE/DELETE revocation.
-- Out-of-order source events always remain effective for their exact inputs.
