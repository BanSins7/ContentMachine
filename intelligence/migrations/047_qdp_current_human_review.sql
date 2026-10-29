-- FIX-15 additive redefinition of current group view; immutable 042/043 untouched.
-- Historical snapshots remain stored; any new human label immediately vetoes current reads.
CREATE OR REPLACE VIEW qdp_group_current_preparations AS
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
   -- Immediate, authoritative SRE effective-human-review veto for each
   -- pinned category, without waiting for an asynchronous event/reconciliation.
   AND NOT EXISTS (
       SELECT 1 FROM qdp_item_lineage l
       JOIN qdp_runs bound ON bound.id=l.run_id AND bound.project_id=l.project_id
       LEFT JOIN sre_classification_results res
         ON res.id=l.sre_result_id AND res.run_id=bound.sre_run_id
        AND res.project_id=l.project_id AND res.video_id=l.video_id
        AND res.category_key=l.category_key
       LEFT JOIN LATERAL (
          SELECT h.id,h.label FROM sre_human_labels h
           WHERE h.project_id=l.project_id AND h.video_id=l.video_id
             AND h.taxonomy_version_id=bound.taxonomy_version_id
             AND h.category_key=l.category_key AND h.superseded_at IS NULL
           ORDER BY h.created_at DESC,h.id DESC LIMIT 1
       ) review ON TRUE
       WHERE l.run_id=p.run_id AND l.project_id=p.project_id
         AND l.video_id=p.video_id
         AND (res.id IS NULL OR
              res.decision IS DISTINCT FROM l.model_decision OR
              COALESCE(review.label,res.decision) IS DISTINCT FROM l.effective_decision OR
              review.id IS DISTINCT FROM l.human_label_id)
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
