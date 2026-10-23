-- Analytics durable local queue and read-time lineage hardening.
-- No external API calls or implicit recurring jobs.
CREATE TABLE analytics_qualification_jobs (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
 video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
 source_id TEXT NOT NULL REFERENCES sources(id),
 request_key TEXT NOT NULL CHECK (length(request_key) BETWEEN 8 AND 180),
 state TEXT NOT NULL DEFAULT 'queued'
   CHECK (state IN ('queued','leased','qualified','partial','blocked','failed')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 5),
 max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 5),
 lease_until TIMESTAMPTZ,
 worker_id TEXT,
 run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
 last_error_code TEXT,
 snapshot_id BIGINT REFERENCES analytics_unified_snapshots(id) ON DELETE SET NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,request_key),
 CHECK (state <> 'leased' OR (lease_until IS NOT NULL AND worker_id IS NOT NULL))
);
CREATE INDEX idx_aql_worker ON analytics_qualification_jobs(state,run_after,id)
 WHERE state IN ('queued','leased');
CREATE INDEX idx_aql_project ON analytics_qualification_jobs(project_id,created_at DESC);

-- Ingest guard must reject fabricated complete joins even from direct SQL writes.
CREATE FUNCTION cm_analytics_insert_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE p RECORD;
BEGIN
 IF NOT EXISTS (SELECT 1 FROM sre_project_videos
                WHERE project_id=NEW.project_id AND video_id=NEW.video_id) THEN
    RAISE EXCEPTION 'Analytics video must be linked to this project';
 END IF;
 SELECT s.status,s.can_store_metadata,s.can_analyze_media,
        s.can_store_metrics,s.can_derive_scores,
        v.is_active,v.reviewed_at,v.permissions,v.source_id
 INTO p FROM sources s JOIN source_policy_versions v ON v.source_id=s.id
 WHERE s.id=NEW.source_id AND v.id=NEW.source_policy_version_id;
 IF NOT FOUND OR p.status<>'enabled' OR NOT p.is_active OR p.reviewed_at IS NULL
    OR NOT p.can_store_metadata OR NOT p.can_analyze_media
    OR NOT p.can_store_metrics OR NOT p.can_derive_scores
    OR p.permissions->>'can_store_metadata'<>'true'
    OR p.permissions->>'can_analyze_media'<>'true'
    OR p.permissions->>'can_store_metrics'<>'true'
    OR p.permissions->>'can_derive_scores'<>'true' THEN
      RAISE EXCEPTION 'Analytics source rights missing or expired';
 END IF;
 IF NEW.video_a_analysis_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM video_feature_a_analyses a
    WHERE a.id=NEW.video_a_analysis_id AND a.video_id=NEW.video_id
      AND a.source_id=NEW.source_id AND a.source_policy_version_id=NEW.source_policy_version_id
      AND a.state='completed') THEN
    RAISE EXCEPTION 'Analytics video A lineage mismatch';
 END IF;
 IF NEW.carousel_a_analysis_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM carousel_feature_a_analyses a
    WHERE a.id=NEW.carousel_a_analysis_id AND a.video_id=NEW.video_id
      AND a.source_id=NEW.source_id AND a.source_policy_version_id=NEW.source_policy_version_id
      AND a.state='completed') THEN
    RAISE EXCEPTION 'Analytics carousel A lineage mismatch';
 END IF;
 IF NEW.b_analysis_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM content_b_analyses b
    WHERE b.id=NEW.b_analysis_id AND b.video_id=NEW.video_id
      AND b.source_id=NEW.source_id AND b.source_policy_version_id=NEW.source_policy_version_id
      AND b.state='completed' AND NOT b.needs_review
      AND b.video_a_analysis_id IS NOT DISTINCT FROM NEW.video_a_analysis_id
      AND b.carousel_a_analysis_id IS NOT DISTINCT FROM NEW.carousel_a_analysis_id) THEN
    RAISE EXCEPTION 'Analytics B lineage mismatch';
 END IF;
 IF NEW.metric_snapshot_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM metric_snapshots m
    WHERE m.id=NEW.metric_snapshot_id AND m.video_id=NEW.video_id
      AND m.source_id=NEW.source_id AND m.source_policy_version_id=NEW.source_policy_version_id
      AND m.data_quality_status='ok'
      AND (m.expires_at IS NULL OR m.expires_at>now())) THEN
    RAISE EXCEPTION 'Analytics C metric missing, invalid or expired';
 END IF;
 IF NEW.calculated_metric_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM calculated_metrics c
    WHERE c.id=NEW.calculated_metric_id AND c.post_id=NEW.video_id
      AND c.snapshot_id=NEW.metric_snapshot_id AND c.score_status='complete') THEN
    RAISE EXCEPTION 'Analytics C calculation lineage mismatch';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER trg_cm_analytics_insert_guard
 BEFORE INSERT ON analytics_unified_snapshots
 FOR EACH ROW EXECUTE FUNCTION cm_analytics_insert_guard();

-- Authoritative read must recheck *all* evidence, including past growth
-- observation and publication date evidence, even if cached C stays complete.
CREATE OR REPLACE VIEW analytics_visible_snapshots AS
SELECT u.*
FROM analytics_unified_snapshots u
JOIN sources s ON s.id=u.source_id
JOIN source_policy_versions p ON p.id=u.source_policy_version_id
JOIN sre_project_videos pv ON pv.project_id=u.project_id AND pv.video_id=u.video_id
WHERE s.status='enabled' AND s.can_store_metadata
  AND s.can_analyze_media AND s.can_store_metrics AND s.can_derive_scores
  AND p.is_active AND p.reviewed_at IS NOT NULL AND p.source_id=s.id
  AND p.permissions->>'can_store_metadata'='true'
  AND p.permissions->>'can_analyze_media'='true'
  AND p.permissions->>'can_store_metrics'='true'
  AND p.permissions->>'can_derive_scores'='true'
  AND (u.metric_snapshot_id IS NULL OR EXISTS (
   SELECT 1 FROM metric_snapshots m WHERE m.id=u.metric_snapshot_id
     AND m.video_id=u.video_id AND m.source_id=u.source_id
     AND m.source_policy_version_id=u.source_policy_version_id
     AND m.data_quality_status='ok'
     AND (m.expires_at IS NULL OR m.expires_at>now())))
  AND (u.video_a_analysis_id IS NULL OR EXISTS (
   SELECT 1 FROM video_feature_a_analyses a WHERE a.id=u.video_a_analysis_id
     AND a.video_id=u.video_id AND a.source_id=u.source_id
     AND a.source_policy_version_id=u.source_policy_version_id AND a.state='completed'))
  AND (u.carousel_a_analysis_id IS NULL OR EXISTS (
   SELECT 1 FROM carousel_feature_a_analyses a WHERE a.id=u.carousel_a_analysis_id
     AND a.video_id=u.video_id AND a.source_id=u.source_id
     AND a.source_policy_version_id=u.source_policy_version_id AND a.state='completed'))
  AND (u.b_analysis_id IS NULL OR EXISTS (
   SELECT 1 FROM content_b_analyses b WHERE b.id=u.b_analysis_id
     AND b.video_id=u.video_id AND b.source_id=u.source_id
     AND b.source_policy_version_id=u.source_policy_version_id
     AND b.state='completed' AND NOT b.needs_review))
  AND (u.calculated_metric_id IS NULL OR EXISTS (
   SELECT 1 FROM calculated_metrics c
   JOIN metric_snapshots previous ON previous.id=c.previous_snapshot_id
   JOIN source_video_metadata pub ON pub.id=c.published_metadata_id
   JOIN metric_calculation_configs conf ON conf.id=c.calculation_config_id
   JOIN comparison_cohorts creator ON creator.id=c.creator_cohort_id
   JOIN comparison_cohorts prior ON prior.id=c.prior_cohort_id
   WHERE c.id=u.calculated_metric_id AND c.post_id=u.video_id
     AND c.snapshot_id=u.metric_snapshot_id AND c.score_status='complete'
     AND previous.video_id=u.video_id AND previous.source_id=u.source_id
     AND previous.source_policy_version_id=u.source_policy_version_id
     AND previous.data_quality_status='ok'
     AND (previous.expires_at IS NULL OR previous.expires_at>now())
     AND pub.video_id=u.video_id AND pub.source_id=u.source_id
     AND pub.source_policy_version_id=u.source_policy_version_id
     AND pub.deleted_at IS NULL
     AND (pub.expires_at IS NULL OR pub.expires_at>now())
     AND conf.normalization_enabled=FALSE
     AND creator.is_frozen AND prior.is_frozen
     AND (c.niche_cohort_id IS NULL OR EXISTS (
        SELECT 1 FROM comparison_cohorts n WHERE n.id=c.niche_cohort_id AND n.is_frozen))))
  AND (u.relevance_label_id IS NULL OR EXISTS (
   SELECT 1 FROM sre_human_labels h
   JOIN sre_taxonomy_versions t ON t.id=h.taxonomy_version_id AND t.project_id=h.project_id
   WHERE h.id=u.relevance_label_id AND h.project_id=u.project_id AND h.video_id=u.video_id
     AND h.label='match' AND h.superseded_at IS NULL AND t.status='approved'))
  AND (u.relevance_result_id IS NULL OR EXISTS (
   SELECT 1 FROM sre_classification_results r
   JOIN sre_classification_runs cr ON cr.id=r.run_id AND cr.project_id=r.project_id
   JOIN sre_taxonomy_versions t ON t.id=r.taxonomy_version_id AND t.project_id=r.project_id
   WHERE r.id=u.relevance_result_id AND r.project_id=u.project_id
     AND r.video_id=u.video_id AND r.decision='match'
     AND cr.status='completed' AND t.status='approved'
     AND NOT EXISTS (SELECT 1 FROM sre_human_labels h
       WHERE h.project_id=r.project_id AND h.video_id=r.video_id
        AND h.taxonomy_version_id=r.taxonomy_version_id AND h.category_key=r.category_key
        AND h.superseded_at IS NULL AND h.label<>'match')));
