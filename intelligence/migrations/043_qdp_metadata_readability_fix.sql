-- FIX-10 follow-up: distinguish metadata readability from item preparation.
-- An item blocked for freshness / missing group inputs still has existing
-- legally readable metadata; its group inputs must remain separately gated.
-- 042 was already executed in disposable CI: use a forward migration, never
-- edit the checksum of an already applied SQL file.
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
   );

