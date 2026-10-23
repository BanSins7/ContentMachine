-- Qualified Data Pipeline v1. New additive project-scoped pipeline, no platform/API calls.
-- SRE is authoritative for inclusion. Existing videos, metadata, snapshots and media are reused.
CREATE TABLE qdp_runs (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 sre_run_id UUID NOT NULL,
 taxonomy_version_id BIGINT NOT NULL,
 idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 12 AND 128),
 input_sha256 TEXT NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
 config JSONB NOT NULL CHECK(jsonb_typeof(config)='object'),
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN
   ('queued','running','completed','partial','failed','cancelled')),
 total_qualified INTEGER NOT NULL DEFAULT 0 CHECK(total_qualified>=0),
 total_review INTEGER NOT NULL DEFAULT 0 CHECK(total_review>=0),
 total_excluded INTEGER NOT NULL DEFAULT 0 CHECK(total_excluded>=0),
 cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 finished_at TIMESTAMPTZ,
 FOREIGN KEY (sre_run_id,project_id) REFERENCES sre_classification_runs(id,project_id),
 FOREIGN KEY (taxonomy_version_id,project_id) REFERENCES sre_taxonomy_versions(id,project_id),
 UNIQUE(project_id,idempotency_key), UNIQUE(id,project_id),
 CHECK (finished_at IS NULL OR finished_at>=created_at)
);
CREATE INDEX idx_qdp_runs_project ON qdp_runs(project_id,created_at DESC);
CREATE TABLE qdp_items (
 run_id UUID NOT NULL,
 project_id TEXT NOT NULL,
 video_id BIGINT NOT NULL,
 sre_category_keys JSONB NOT NULL CHECK(jsonb_typeof(sre_category_keys)='array'),
 sre_result_ids JSONB NOT NULL CHECK(jsonb_typeof(sre_result_ids)='array'),
 status TEXT NOT NULL DEFAULT 'queued'
    CHECK(status IN ('queued','ready','partial','blocked','cancelled')),
 reason_code TEXT,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(run_id,video_id),
 UNIQUE(run_id,project_id,video_id),
 FOREIGN KEY(run_id,project_id) REFERENCES qdp_runs(id,project_id) ON DELETE CASCADE,
 FOREIGN KEY(project_id,video_id) REFERENCES sre_project_videos(project_id,video_id)
);
CREATE INDEX idx_qdp_items_project ON qdp_items(project_id,status,video_id);
CREATE TABLE qdp_enrichments (
 run_id UUID NOT NULL,
 project_id TEXT NOT NULL,
 video_id BIGINT NOT NULL,
 source_id TEXT NOT NULL REFERENCES sources(id),
 source_policy_version_id BIGINT NOT NULL REFERENCES source_policy_versions(id),
 metadata_id BIGINT NOT NULL REFERENCES source_video_metadata(id),
 media_asset_id BIGINT REFERENCES media_assets(id),
 metadata JSONB NOT NULL CHECK(jsonb_typeof(metadata)='object'),
 metrics JSONB NOT NULL CHECK(jsonb_typeof(metrics)='object'),
 metric_snapshot_ids JSONB NOT NULL CHECK(jsonb_typeof(metric_snapshot_ids)='array'),
 enriched_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 expires_at TIMESTAMPTZ,
 PRIMARY KEY(run_id,video_id),
 FOREIGN KEY(run_id,project_id,video_id)
   REFERENCES qdp_items(run_id,project_id,video_id) ON DELETE CASCADE,
 CHECK(expires_at IS NULL OR expires_at>enriched_at)
);
CREATE INDEX idx_qdp_enrichment_expiry ON qdp_enrichments(expires_at)
 WHERE expires_at IS NOT NULL;
CREATE TABLE qdp_preparations (
 run_id UUID NOT NULL,
 project_id TEXT NOT NULL,
 video_id BIGINT NOT NULL,
 analysis_group TEXT NOT NULL CHECK(analysis_group IN ('A','B','C')),
 state TEXT NOT NULL CHECK(state IN ('ready','blocked')),
 reason_code TEXT,
 inputs JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(inputs)='object'),
 input_sha256 TEXT NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(run_id,video_id,analysis_group),
 FOREIGN KEY(run_id,project_id,video_id)
   REFERENCES qdp_items(run_id,project_id,video_id) ON DELETE CASCADE,
 CHECK((state='ready' AND reason_code IS NULL) OR (state='blocked' AND reason_code IS NOT NULL))
);
CREATE INDEX idx_qdp_preparations_ready ON qdp_preparations(project_id,analysis_group,state);
CREATE TABLE qdp_events (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 run_id UUID NOT NULL REFERENCES qdp_runs(id) ON DELETE CASCADE,
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 video_id BIGINT,
 event_code TEXT NOT NULL CHECK(length(event_code) BETWEEN 1 AND 80),
 details JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(details)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(run_id,project_id) REFERENCES qdp_runs(id,project_id)
);
CREATE INDEX idx_qdp_events_run ON qdp_events(run_id,created_at DESC);
-- Snapshots are immutable. Expired or revoked data is purged, never silently edited.
CREATE FUNCTION qdp_immutable_snapshot() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'QDP snapshot is immutable; create a new run';
END $$;
CREATE TRIGGER qdp_enrichments_immutable BEFORE UPDATE ON qdp_enrichments
 FOR EACH ROW EXECUTE FUNCTION qdp_immutable_snapshot();
CREATE TRIGGER qdp_preparations_immutable BEFORE UPDATE ON qdp_preparations
 FOR EACH ROW EXECUTE FUNCTION qdp_immutable_snapshot();
-- Reads must revalidate current policy. A revoked/expired source is not exposed.
CREATE VIEW qdp_readable_enrichments AS
SELECT e.* FROM qdp_enrichments e
JOIN sources s ON s.id=e.source_id
JOIN source_policy_versions p ON p.id=e.source_policy_version_id AND p.source_id=s.id
JOIN source_video_metadata m ON m.id=e.metadata_id
WHERE s.status='enabled' AND s.can_store_metadata AND p.is_active
  AND p.permissions->>'can_store_metadata'='true'
  AND (e.expires_at IS NULL OR e.expires_at>now())
  AND (m.deleted_at IS NULL) AND (m.expires_at IS NULL OR m.expires_at>now())
  AND (s.retention_days IS NULL OR m.fetched_at+s.retention_days*interval '1 day'>now())
  AND (jsonb_array_length(e.metric_snapshot_ids)=0 OR
       (s.can_store_metrics AND p.permissions->>'can_store_metrics'='true'))
  AND (e.media_asset_id IS NULL OR
       (s.can_analyze_media AND p.permissions->>'can_analyze_media'='true'
        AND EXISTS(SELECT 1 FROM media_assets a WHERE a.id=e.media_asset_id
                     AND a.video_id=e.video_id AND a.source_id=e.source_id
                     AND a.permission<>'metadata_only'
                     AND (a.expires_at IS NULL OR a.expires_at>now()))))
  AND NOT EXISTS (
      SELECT 1 FROM jsonb_array_elements_text(e.metric_snapshot_ids) AS mid(value)
      WHERE NOT EXISTS (
          SELECT 1 FROM metric_snapshots ms
          WHERE ms.id=mid.value::bigint AND ms.video_id=e.video_id
            AND ms.source_id=e.source_id
            AND ms.source_policy_version_id=e.source_policy_version_id
            AND ms.data_quality_status='ok'
            AND (ms.expires_at IS NULL OR ms.expires_at>now())
      )
  );
