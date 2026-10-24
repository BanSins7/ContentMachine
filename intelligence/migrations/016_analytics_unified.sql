-- Additive, append-only Unified Analytics evidence / qualification snapshots.
-- This does not invent measured A/B signals or formula C parameters.
CREATE TABLE analytics_unified_snapshots (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
 video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
 source_id TEXT NOT NULL REFERENCES sources(id),
 source_policy_version_id BIGINT NOT NULL REFERENCES source_policy_versions(id),
 request_key TEXT NOT NULL CHECK (length(request_key) BETWEEN 8 AND 180),
 engine_version TEXT NOT NULL CHECK (engine_version <> ''),
 content_kind TEXT CHECK (content_kind IS NULL OR content_kind IN
     ('video','slideshow_video','photo_carousel','mixed_carousel','image_post')),
 status TEXT NOT NULL CHECK (status IN ('qualified','partial','blocked')),
 video_a_analysis_id BIGINT REFERENCES video_feature_a_analyses(id) ON DELETE CASCADE,
 carousel_a_analysis_id BIGINT REFERENCES carousel_feature_a_analyses(id) ON DELETE CASCADE,
 b_analysis_id BIGINT REFERENCES content_b_analyses(id) ON DELETE CASCADE,
 metric_snapshot_id BIGINT REFERENCES metric_snapshots(id) ON DELETE CASCADE,
 calculated_metric_id BIGINT REFERENCES calculated_metrics(id) ON DELETE CASCADE,
 relevance_label_id BIGINT REFERENCES sre_human_labels(id) ON DELETE CASCADE,
 relevance_result_id BIGINT REFERENCES sre_classification_results(id) ON DELETE CASCADE,
 readiness JSONB NOT NULL CHECK (jsonb_typeof(readiness) = 'object'),
 blockers JSONB NOT NULL CHECK (jsonb_typeof(blockers) = 'array'),
 features_a JSONB NOT NULL CHECK (jsonb_typeof(features_a) = 'object'),
 features_b JSONB NOT NULL CHECK (jsonb_typeof(features_b) = 'object'),
 metrics_c JSONB NOT NULL CHECK (jsonb_typeof(metrics_c) = 'object'),
 lineage JSONB NOT NULL CHECK (jsonb_typeof(lineage) = 'object'),
 fingerprint TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE (project_id,request_key),
 UNIQUE (project_id,fingerprint),
 CHECK ((video_a_analysis_id IS NULL OR carousel_a_analysis_id IS NULL)),
 CHECK (status <> 'qualified' OR (
   b_analysis_id IS NOT NULL AND calculated_metric_id IS NOT NULL
   AND metric_snapshot_id IS NOT NULL
   AND (video_a_analysis_id IS NOT NULL OR carousel_a_analysis_id IS NOT NULL)
   AND (relevance_label_id IS NOT NULL OR relevance_result_id IS NOT NULL)
   AND blockers = '[]'::jsonb)),
 CHECK (status <> 'blocked' OR blockers <> '[]'::jsonb)
);
CREATE INDEX idx_analytics_unified_project_status
 ON analytics_unified_snapshots(project_id,status,created_at DESC);
CREATE INDEX idx_analytics_unified_video
 ON analytics_unified_snapshots(video_id,created_at DESC);
CREATE INDEX idx_analytics_unified_c_score
 ON analytics_unified_snapshots(calculated_metric_id) WHERE calculated_metric_id IS NOT NULL;

-- No UPDATE to old analytical conclusions. Revocation / retention may DELETE.
CREATE FUNCTION cm_analytics_no_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Unified snapshot is immutable; write a new request_key';
END $$;
CREATE TRIGGER trg_cm_analytics_no_update
 BEFORE UPDATE ON analytics_unified_snapshots
 FOR EACH ROW EXECUTE FUNCTION cm_analytics_no_update();

-- Read-side rights rechecked every time, not only at snapshot creation.
-- Source policy opt-ins are intersections with sources flags; manual D1 remains denied.
CREATE VIEW analytics_visible_snapshots AS
SELECT u.*
FROM analytics_unified_snapshots u
JOIN sources s ON s.id=u.source_id
JOIN source_policy_versions p ON p.id=u.source_policy_version_id
WHERE s.status='enabled' AND s.can_store_metadata
  AND s.can_analyze_media AND s.can_store_metrics AND s.can_derive_scores
  AND p.is_active AND p.reviewed_at IS NOT NULL AND p.source_id=s.id
  AND p.permissions->>'can_store_metadata'='true'
  AND p.permissions->>'can_analyze_media'='true'
  AND p.permissions->>'can_store_metrics'='true'
  AND p.permissions->>'can_derive_scores'='true'
  AND (u.metric_snapshot_id IS NULL OR EXISTS (
   SELECT 1 FROM metric_snapshots m
   WHERE m.id=u.metric_snapshot_id AND m.video_id=u.video_id
     AND m.source_id=u.source_id
     AND m.data_quality_status='ok'
     AND (m.expires_at IS NULL OR m.expires_at>now())))
  AND (u.video_a_analysis_id IS NULL OR EXISTS (
   SELECT 1 FROM video_feature_a_analyses a
   WHERE a.id=u.video_a_analysis_id AND a.video_id=u.video_id
     AND a.source_id=u.source_id AND a.state='completed'))
  AND (u.carousel_a_analysis_id IS NULL OR EXISTS (
   SELECT 1 FROM carousel_feature_a_analyses a
   WHERE a.id=u.carousel_a_analysis_id AND a.video_id=u.video_id
     AND a.source_id=u.source_id AND a.state='completed'))
  AND (u.b_analysis_id IS NULL OR EXISTS (
   SELECT 1 FROM content_b_analyses b WHERE b.id=u.b_analysis_id
    AND b.video_id=u.video_id AND b.source_id=u.source_id
    AND b.state='completed' AND NOT b.needs_review))
  AND (u.calculated_metric_id IS NULL OR EXISTS (
   SELECT 1 FROM calculated_metrics c WHERE c.id=u.calculated_metric_id
    AND c.post_id=u.video_id AND c.snapshot_id=u.metric_snapshot_id
    AND c.score_status='complete'));
