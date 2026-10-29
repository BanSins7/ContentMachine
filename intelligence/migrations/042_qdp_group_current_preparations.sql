-- QDP FIX-10: separate historical prepared facts from CURRENT rights.
-- Additive. No applied 016/040/041 migration or peer-owned table is rewritten.
-- Original qdp_readable_enrichments is intentionally NOT reused: its C/media
-- global gates cause unrelated A/B/C groups to disappear on a policy change.
CREATE VIEW qdp_current_base_enrichments AS
SELECT e.*
  FROM qdp_enrichments e
  JOIN qdp_runs r ON r.id=e.run_id AND r.project_id=e.project_id
  JOIN qdp_run_manifests mf ON mf.run_id=r.id AND mf.project_id=r.project_id
  JOIN qdp_items i ON i.run_id=e.run_id AND i.project_id=e.project_id
                  AND i.video_id=e.video_id
  JOIN source_video_metadata m ON m.id=e.metadata_id AND m.video_id=e.video_id
                  AND m.source_id=e.source_id
                  AND m.source_policy_version_id=e.source_policy_version_id
  JOIN sources s ON s.id=e.source_id
  JOIN source_policy_versions sp ON sp.id=e.source_policy_version_id
                  AND sp.source_id=e.source_id
 WHERE NOT r.cancel_requested
   AND i.status IN ('ready','partial')
   AND s.status='enabled' AND s.can_store_metadata
   AND sp.is_active AND sp.permissions->>'can_store_metadata'='true'
   AND m.is_current AND m.deleted_at IS NULL
   AND (e.expires_at IS NULL OR e.expires_at>now())
   AND (m.expires_at IS NULL OR m.expires_at>now())
   AND (s.retention_days IS NULL OR
        m.fetched_at+s.retention_days*interval '1 day'>now())
   AND (sp.retention_policy->>'retention_days' IS NULL OR
        m.fetched_at+(sp.retention_policy->>'retention_days')::integer
          *interval '1 day'>now())
   AND NOT EXISTS (
       SELECT 1 FROM qdp_invalidations x
        WHERE x.run_id=e.run_id AND x.project_id=e.project_id
          AND (x.video_id IS NULL OR x.video_id=e.video_id)
          AND x.analysis_group IS NULL
   );

-- A/B checks NEVER depend on C score permissions/metric snapshots.
-- C checks NEVER depend on media permissions or media assets.
-- Immutable p.state and p.inputs are historical; current eligibility is
-- a separate view that can disappear after rights revocation.
CREATE VIEW qdp_group_current_preparations AS
SELECT p.run_id,p.project_id,p.video_id,p.analysis_group,
       p.state,p.reason_code,p.inputs,p.input_sha256,p.created_at,
       e.source_id,e.source_policy_version_id,e.metadata_id,e.media_asset_id
  FROM qdp_preparations p
  JOIN qdp_current_base_enrichments e
    ON e.run_id=p.run_id AND e.project_id=p.project_id
   AND e.video_id=p.video_id
  JOIN sources s ON s.id=e.source_id
  JOIN source_policy_versions sp
    ON sp.id=e.source_policy_version_id AND sp.source_id=e.source_id
  JOIN videos v ON v.id=e.video_id
 WHERE p.state='ready'
   AND NOT EXISTS (
       SELECT 1 FROM qdp_invalidations x
        WHERE x.run_id=p.run_id AND x.project_id=p.project_id
          AND (x.video_id IS NULL OR x.video_id=p.video_id)
          AND (x.analysis_group IS NULL OR x.analysis_group=p.analysis_group)
   )
   AND (
      (p.analysis_group IN ('A','B')
       AND s.can_analyze_media
       AND sp.permissions->>'can_analyze_media'='true'
       AND v.content_kind IN ('video','slideshow_video')
       AND p.inputs->>'media_kind'='video'
       AND EXISTS (
         SELECT 1 FROM media_assets a
          WHERE a.id=e.media_asset_id
            AND a.id=(p.inputs->>'media_asset_id')::bigint
            AND a.video_id=e.video_id AND a.source_id=e.source_id
            AND a.media_kind='video'
            AND a.permission NOT IN ('','metadata_only')
            AND a.sha256=p.inputs->>'media_sha256'
            AND a.sha256 ~ '^[0-9a-f]{64}$'
            AND (a.expires_at IS NULL OR a.expires_at>now())
       ))
      OR
      (p.analysis_group='C'
       AND s.can_store_metrics AND s.can_derive_scores
       AND sp.permissions->>'can_store_metrics'='true'
       AND sp.permissions->>'can_derive_scores'='true'
       AND jsonb_typeof(p.inputs->'metric_snapshot_ids')='array'
       AND jsonb_array_length(p.inputs->'metric_snapshot_ids')=2
       AND p.inputs->>'score_config_id' IS NOT NULL
       AND EXISTS (
         SELECT 1 FROM metric_calculation_configs conf
          WHERE conf.id=(p.inputs->>'score_config_id')::bigint
            AND conf.is_active AND conf.alpha IS NOT NULL
            AND conf.bayesian_k IS NOT NULL
       )
       AND (
         SELECT count(*) FROM (
           SELECT ms.id
             FROM metric_snapshots ms
            WHERE ms.video_id=e.video_id AND ms.source_id=e.source_id
              AND ms.source_policy_version_id=e.source_policy_version_id
              AND ms.id IN (
                  SELECT value::bigint FROM jsonb_array_elements_text(
                    p.inputs->'metric_snapshot_ids') AS mids(value))
              AND ms.data_quality_status='ok'
              AND ms.precision='exact' AND NOT ms.observation_time_assumed
              AND ms.views IS NOT NULL AND ms.views>=0
              AND (ms.expires_at IS NULL OR ms.expires_at>now())
         ) eligible
       )=2)
   );
CREATE INDEX idx_qdp_current_group_read
  ON qdp_preparations(project_id,run_id,analysis_group,video_id)
  WHERE state='ready';
