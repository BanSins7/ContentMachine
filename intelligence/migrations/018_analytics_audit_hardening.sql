-- 018: audited Analytics P1/P2 safety + governance. Additive; existing derived rows
-- with NULL expiry remain unreadable until requalified. No implicit approvals.
ALTER TABLE analytics_unified_snapshots
  ADD COLUMN derived_expires_at TIMESTAMPTZ;
CREATE INDEX idx_analytics_unified_expiry ON analytics_unified_snapshots(derived_expires_at);

-- Freshness depends on publication metadata. It MUST be part of C identity.
ALTER TABLE calculated_metrics DROP CONSTRAINT uq_c_calculation_lineage;
ALTER TABLE calculated_metrics ADD CONSTRAINT uq_c_calculation_lineage
  UNIQUE NULLS NOT DISTINCT (snapshot_id,calculation_config_id,previous_snapshot_id,
                             creator_cohort_id,niche_cohort_id,prior_cohort_id,published_metadata_id);

-- A prior observation is evidence, never a retention blocker. Delete derived scores
-- when that evidence is deleted; analytics_unified_snapshots cascades via C id.
ALTER TABLE calculated_metrics
  DROP CONSTRAINT calculated_metrics_previous_snapshot_id_post_id_fkey;
ALTER TABLE calculated_metrics
  ADD CONSTRAINT calculated_metrics_previous_snapshot_id_post_id_fkey
  FOREIGN KEY(previous_snapshot_id,post_id)
  REFERENCES metric_snapshots(id,video_id) ON DELETE CASCADE;

-- Distinguish an illegal direct edit of a frozen cohort from the *required*
-- FK cascade when deleting its now-absent raw observation.
CREATE OR REPLACE FUNCTION cm_c_cohort_member_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE cohort BIGINT;
BEGIN
  cohort := CASE WHEN TG_OP='DELETE' THEN OLD.cohort_id ELSE NEW.cohort_id END;
  IF TG_OP='DELETE' AND NOT EXISTS (
      SELECT 1 FROM metric_snapshots WHERE id=OLD.comparison_snapshot_id
  ) THEN RETURN OLD; END IF;
  IF EXISTS (SELECT 1 FROM comparison_cohorts WHERE id=cohort AND is_frozen) THEN
    RAISE EXCEPTION 'Members of a frozen cohort cannot change';
  END IF;
  RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;

-- A frozen cohort/config is NOT a human approval. Approvals are explicit,
-- project/source/version scoped and can be revoked without mutating old scores.
CREATE TABLE analytics_config_approvals (
 project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
 source_id TEXT NOT NULL REFERENCES sources(id),
 config_id BIGINT NOT NULL REFERENCES metric_calculation_configs(id) ON DELETE CASCADE,
 approved_by TEXT NOT NULL CHECK (length(btrim(approved_by))>0),
 approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 is_active BOOLEAN NOT NULL DEFAULT TRUE,
 min_creator_posts INTEGER NOT NULL CHECK (min_creator_posts BETWEEN 1 AND 1000000),
 min_prior_posts INTEGER NOT NULL CHECK (min_prior_posts BETWEEN 1 AND 1000000),
 min_niche_posts INTEGER NOT NULL CHECK (min_niche_posts BETWEEN 1 AND 1000000),
 approval_reason TEXT NOT NULL CHECK(length(btrim(approval_reason))>0),
 PRIMARY KEY(project_id,source_id,config_id)
);
CREATE TABLE analytics_cohort_approvals (
 project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
 source_id TEXT NOT NULL REFERENCES sources(id),
 cohort_id BIGINT NOT NULL REFERENCES comparison_cohorts(id) ON DELETE CASCADE,
 source_metric_version TEXT NOT NULL CHECK(length(source_metric_version)>0),
 definition_version TEXT NOT NULL CHECK(length(definition_version)>0),
 min_post_count INTEGER NOT NULL CHECK(min_post_count BETWEEN 1 AND 1000000),
 approved_by TEXT NOT NULL CHECK(length(btrim(approved_by))>0),
 approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 is_active BOOLEAN NOT NULL DEFAULT TRUE,
 approval_reason TEXT NOT NULL CHECK(length(btrim(approval_reason))>0),
 PRIMARY KEY(project_id,source_id,cohort_id)
);
CREATE INDEX idx_analytics_approved_cohorts ON analytics_cohort_approvals(cohort_id,is_active);

-- A frozen membership is readable only while EVERY member's raw snapshot still
-- exists, has live policy rights, matches source+definition+version and is in
-- the frozen observation window. Undefined age/time filters are rejected in v1.
CREATE FUNCTION analytics_cohort_healthy(
  proj TEXT,src TEXT,cohort BIGINT,definition TEXT,metric_version TEXT,
  required_posts INTEGER,observed TIMESTAMPTZ
) RETURNS BOOLEAN LANGUAGE SQL STABLE AS $$
 SELECT EXISTS (
  SELECT 1 FROM comparison_cohorts c
  JOIN analytics_cohort_approvals ap ON ap.cohort_id=c.id
    AND ap.project_id=proj AND ap.source_id=src AND ap.is_active
  WHERE c.id=cohort AND c.is_frozen AND c.source_id=src
    AND c.source_metric_definition=definition
    AND c.evaluated_as_of<=observed AND c.definition_version=ap.definition_version
    AND ap.source_metric_version=metric_version
    AND c.post_count>=required_posts AND c.post_count>=ap.min_post_count
    AND c.age_window='{}'::jsonb AND c.time_window='{}'::jsonb
    AND (SELECT count(DISTINCT m.comparison_post_id)
         FROM comparison_cohort_members m WHERE m.cohort_id=c.id) = c.post_count
    AND NOT EXISTS (
     SELECT 1 FROM comparison_cohort_members member
     LEFT JOIN metric_snapshots snap ON snap.id=member.comparison_snapshot_id
     LEFT JOIN source_policy_versions pol ON pol.id=snap.source_policy_version_id
     LEFT JOIN sources s ON s.id=snap.source_id
     WHERE member.cohort_id=c.id AND (
       snap.id IS NULL OR snap.video_id<>member.comparison_post_id
       OR snap.source_id IS DISTINCT FROM src
       OR snap.source_metric_definition IS DISTINCT FROM definition
       OR snap.source_metric_version IS DISTINCT FROM metric_version
       OR snap.data_quality_status IS DISTINCT FROM 'ok'
       OR snap.observation_time_assumed IS DISTINCT FROM FALSE
       OR snap.observed_at>c.evaluated_as_of
       OR (snap.expires_at IS NOT NULL AND snap.expires_at<=now())
       OR pol.id IS NULL OR pol.source_id IS DISTINCT FROM src
       OR pol.is_active IS DISTINCT FROM TRUE OR pol.reviewed_at IS NULL
       OR pol.permissions->>'can_store_metrics' IS DISTINCT FROM 'true'
       OR pol.permissions->>'can_derive_scores' IS DISTINCT FROM 'true'
       OR s.status IS DISTINCT FROM 'enabled'
       OR s.can_store_metrics IS DISTINCT FROM TRUE OR s.can_derive_scores IS DISTINCT FROM TRUE))
 )
$$;

-- Read check is authoritative. This replaces the v017 view (same output columns)
-- without editing/checksumming existing migrations.
CREATE OR REPLACE VIEW analytics_visible_snapshots AS
SELECT u.*
FROM analytics_unified_snapshots u
JOIN sources s ON s.id=u.source_id
JOIN source_policy_versions p ON p.id=u.source_policy_version_id
JOIN sre_project_videos pv ON pv.project_id=u.project_id AND pv.video_id=u.video_id
WHERE s.status='enabled' AND s.can_store_metadata AND s.can_analyze_media
  AND s.can_store_metrics AND s.can_derive_scores AND
  p.is_active AND p.reviewed_at IS NOT NULL AND p.source_id=s.id
  AND p.permissions->>'can_store_metadata'='true'
  AND p.permissions->>'can_analyze_media'='true'
  AND p.permissions->>'can_store_metrics'='true'
  AND p.permissions->>'can_derive_scores'='true'
  AND u.derived_expires_at>now()
  AND (s.retention_days IS NULL
       OR u.created_at + s.retention_days * INTERVAL '1 day'>now())
  AND CASE WHEN (p.retention_policy->>'derived_retention_days') ~ '^[0-9]{1,3}$'
      THEN u.created_at +
           (p.retention_policy->>'derived_retention_days')::int * INTERVAL '1 day'>now()
      ELSE FALSE END
  AND (u.metric_snapshot_id IS NULL OR EXISTS (
    SELECT 1 FROM metric_snapshots m WHERE m.id=u.metric_snapshot_id
     AND m.video_id=u.video_id AND m.source_id=u.source_id
     AND m.source_policy_version_id=u.source_policy_version_id
     AND m.data_quality_status='ok' AND
     (m.expires_at IS NULL OR m.expires_at>now())))
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
  AND (u.status<>'qualified' OR (
      (u.video_a_analysis_id IS NOT NULL AND EXISTS (
         SELECT 1 FROM video_feature_a_analyses a
         JOIN media_assets media ON media.id=a.media_asset_id
         WHERE a.id=u.video_a_analysis_id AND media.video_id=u.video_id
           AND media.source_id=u.source_id AND media.permission<>'metadata_only'
           AND media.sha256~'^[0-9a-f]{64}$'
           AND (media.expires_at IS NULL OR media.expires_at>now()))
       OR u.carousel_a_analysis_id IS NOT NULL AND EXISTS (
         SELECT 1 FROM carousel_feature_a_items i
         WHERE i.analysis_id=u.carousel_a_analysis_id)
       AND NOT EXISTS (
         SELECT 1 FROM carousel_feature_a_items i
         LEFT JOIN media_assets media ON media.id=i.media_asset_id
         WHERE i.analysis_id=u.carousel_a_analysis_id AND (
           media.id IS NULL OR media.video_id<>u.video_id
           OR media.source_id<>u.source_id OR media.permission='metadata_only'
           OR media.sha256 IS NULL OR media.sha256!~'^[0-9a-f]{64}$'
           OR (media.expires_at IS NOT NULL AND media.expires_at<=now()))))
     AND NOT EXISTS (
       SELECT 1 FROM content_b_ctas c WHERE c.analysis_id=u.b_analysis_id
         AND c.extraction_status<>'detected')
  ))
  AND (u.calculated_metric_id IS NULL OR EXISTS (
   SELECT 1 FROM calculated_metrics c
   JOIN metric_snapshots previous ON previous.id=c.previous_snapshot_id
   JOIN metric_snapshots current ON current.id=c.snapshot_id
   JOIN source_video_metadata pub ON pub.id=c.published_metadata_id
   JOIN metric_calculation_configs conf ON conf.id=c.calculation_config_id
   JOIN comparison_cohorts creator ON creator.id=c.creator_cohort_id
   JOIN comparison_cohorts prior ON prior.id=c.prior_cohort_id
   JOIN analytics_config_approvals approval
       ON approval.config_id=c.calculation_config_id
          AND approval.project_id=u.project_id AND approval.source_id=u.source_id
          AND approval.is_active
   WHERE c.id=u.calculated_metric_id AND c.post_id=u.video_id
     AND c.snapshot_id=u.metric_snapshot_id AND c.score_status='complete'
     AND current.source_metric_version=previous.source_metric_version
     AND previous.observation_time_assumed=FALSE
     AND current.observation_time_assumed=FALSE
     AND previous.video_id=u.video_id AND previous.source_id=u.source_id
     AND previous.source_policy_version_id=u.source_policy_version_id
     AND previous.data_quality_status='ok'
     AND (previous.expires_at IS NULL OR previous.expires_at>now())
     AND pub.video_id=u.video_id AND pub.source_id=u.source_id
     AND pub.source_policy_version_id=u.source_policy_version_id AND pub.deleted_at IS NULL
     AND (pub.expires_at IS NULL OR pub.expires_at>now())
     AND conf.normalization_enabled=FALSE AND creator.is_frozen AND prior.is_frozen
     AND creator.post_count>=approval.min_creator_posts
     AND prior.post_count>=approval.min_prior_posts
     AND analytics_cohort_healthy(u.project_id,u.source_id,creator.id,
         current.source_metric_definition,current.source_metric_version,
         approval.min_creator_posts,current.observed_at)
     AND analytics_cohort_healthy(u.project_id,u.source_id,prior.id,
         current.source_metric_definition,current.source_metric_version,
         approval.min_prior_posts,current.observed_at)
     AND (c.niche_cohort_id IS NULL OR
         analytics_cohort_healthy(u.project_id,u.source_id,c.niche_cohort_id,
         current.source_metric_definition,current.source_metric_version,
         approval.min_niche_posts,current.observed_at))
  ))
  AND (u.relevance_label_id IS NULL OR EXISTS (
   SELECT 1 FROM sre_human_labels h
   JOIN sre_taxonomy_versions t ON t.id=h.taxonomy_version_id AND t.project_id=h.project_id
   JOIN sre_workspaces w ON w.project_id=h.project_id AND w.active_taxonomy_id=h.taxonomy_version_id
   WHERE h.id=u.relevance_label_id AND h.project_id=u.project_id AND h.video_id=u.video_id
     AND h.label='match' AND h.superseded_at IS NULL AND t.status='approved'))
  AND (u.relevance_result_id IS NULL OR EXISTS (
   SELECT 1 FROM sre_classification_results r
   JOIN sre_classification_runs cr ON cr.id=r.run_id AND cr.project_id=r.project_id
   JOIN sre_taxonomy_versions t ON t.id=r.taxonomy_version_id AND t.project_id=r.project_id
   JOIN sre_workspaces w ON w.project_id=r.project_id AND w.active_taxonomy_id=r.taxonomy_version_id
   WHERE r.id=u.relevance_result_id AND r.project_id=u.project_id
     AND r.video_id=u.video_id AND r.decision='match'
     AND cr.status='completed' AND t.status='approved'
     AND NOT EXISTS (SELECT 1 FROM sre_human_labels h
       WHERE h.project_id=r.project_id AND h.video_id=r.video_id
        AND h.taxonomy_version_id=r.taxonomy_version_id AND h.category_key=r.category_key
        AND h.superseded_at IS NULL AND h.label<>'match')));
