-- Forward-only repair of 074: restore the independent metadata-readability
-- contract established by 043 while retaining every modern SRE M36
-- current-rights/tombstone/ACK check introduced by 074.
-- An enrichment remains legally readable when its prepared item's state is
-- blocked, as long as source, policy, pinned metadata and SRE rights are live.
-- Per-group readiness is *still* guarded in qdp_group_current_preparations.
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


