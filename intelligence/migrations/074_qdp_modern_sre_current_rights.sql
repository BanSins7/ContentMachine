-- Forward-only SRE LIVE QDP current-rights checks; unchanged 042/047 history.
-- Never treat a durable receipt as perpetual authority to access D1/media.
CREATE OR REPLACE VIEW qdp_current_base_enrichments AS
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
   )
   -- A modern SRE M36 source can vanish after its ACK. The source
   -- may not be visible via QDP even when the D1 metadata still exists.
   AND (r.sre_live_run_id IS NULL OR EXISTS (
       SELECT 1 FROM qdp_sre_live_events q
       JOIN sre_live_result_outbox outbox
         ON outbox.event_id=q.event_id
         AND outbox.project_id=q.project_id
       JOIN sre_live_items item
         ON item.run_id=q.sre_run_id AND item.project_id=q.project_id
         AND item.id=outbox.item_id AND item.video_id=e.video_id
       WHERE q.project_id=e.project_id
         AND q.sre_run_id=r.sre_live_run_id AND q.video_id=e.video_id
         AND q.kind='ready' AND outbox.state='acked'
         AND outbox.kind='ready'
         AND item.state='completed'
         AND item.content_sha256=(mf.manifest->'by_video'->(e.video_id::text)->>'content_sha256')
         AND NOT EXISTS (
           SELECT 1 FROM qdp_sre_live_tombstones t
           WHERE t.project_id=q.project_id
             AND t.prior_ready_event_id=q.event_id)
         AND EXISTS (
           SELECT 1 FROM sre_m36_live_only_export live
           WHERE live.event_id=q.event_id AND live.project_id=q.project_id
         )
   ));

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
   -- Legacy and M36 LIVE use separate, original source IDs.
   -- Modern M32 review supersession immediately hides stale Group A/B/C.
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
         AND (
           (bound.sre_live_run_id IS NULL AND (
              res.id IS NULL OR
              res.decision IS DISTINCT FROM l.model_decision OR
              COALESCE(review.label,res.decision) IS DISTINCT FROM l.effective_decision OR
              review.id IS DISTINCT FROM l.human_label_id
           ))
           OR
           (bound.sre_live_run_id IS NOT NULL AND NOT EXISTS (
              SELECT 1 FROM sre_m36_live_only_export current
              JOIN sre_live_items item
                ON item.id=current.item_id
                AND item.run_id=current.run_id
                AND item.project_id=current.project_id
                AND item.video_id=l.video_id
              JOIN sre_live_verified_pair_results original
                ON original.project_id=current.project_id
                AND original.run_id=current.run_id
                AND original.item_id=current.item_id
                AND original.category_key=current.category_key
              JOIN qdp_sre_live_events received
                ON received.event_id=current.event_id
                AND received.project_id=current.project_id
                AND received.kind='ready'
              JOIN sre_live_result_outbox origin
                ON origin.event_id=current.event_id
                AND origin.project_id=current.project_id
                AND origin.state='acked'
              WHERE current.project_id=l.project_id
                AND current.run_id=bound.sre_live_run_id
                AND current.category_key=l.category_key
                AND item.content_version_id=l.content_version_id
                AND item.content_sha256=l.content_sha256
                AND original.result_id=l.sre_live_result_id
                AND current.review_id=l.sre_live_review_id
                AND current.original_model_decision=l.model_decision
                AND current.human_decision=l.effective_decision
                AND NOT EXISTS (
                  SELECT 1 FROM qdp_sre_live_tombstones tomb
                  WHERE tomb.project_id=received.project_id
                    AND tomb.prior_ready_event_id=received.event_id
                )
           ))
         )
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
