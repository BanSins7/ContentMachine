-- UA-MOD-11: additive, read-only *candidate IDs* for current-rights checks.
-- Retain 080/081/082 checksums unchanged. PostgreSQL 17.
-- This view deliberately exposes NO payload, score, readiness, or snapshot
-- content. Real read/HTTP/PATTERNS handlers MUST also execute the UAE
-- SourcePolicyCurrentReader + GuardedUAEReads against exact pinned metadata,
-- every metric observation and every media asset, inside a current transaction.
-- Candidate IDs ARE NOT authorization, qualified data, or a safe public API.
-- In particular, the 080 snapshot schema has no metadata/media IDs; the
-- Python pin-aware reader must still check those assets.
CREATE VIEW uae_snapshot_current_rights_candidates
WITH (security_barrier=true) AS
SELECT sn.project_id, sn.id AS snapshot_id, sn.video_id
FROM uae_unified_snapshots sn
JOIN sre_project_videos membership
  ON membership.project_id=sn.project_id
 AND membership.video_id=sn.video_id
JOIN sources src ON src.id=sn.source_id
JOIN source_policy_versions pol
  ON pol.id=sn.source_policy_version_id
 AND pol.source_id=sn.source_id
JOIN metric_snapshots ms
  ON ms.id=sn.metric_snapshot_id
 AND ms.video_id=sn.video_id
 AND ms.source_id=sn.source_id
 AND ms.source_policy_version_id=sn.source_policy_version_id
WHERE sn.status='qualified'
  AND src.status='enabled'
  AND src.can_store_metadata IS TRUE
  AND src.can_analyze_media IS TRUE
  AND src.can_store_metrics IS TRUE
  AND src.can_derive_scores IS TRUE
  AND pol.is_active IS TRUE
  AND pol.reviewed_at IS NOT NULL AND pol.reviewed_at<=now()
  AND src.updated_at<=now()
  AND pol.permissions->>'can_store_metadata'='true'
  AND pol.permissions->>'can_analyze_media'='true'
  AND pol.permissions->>'can_store_metrics'='true'
  AND pol.permissions->>'can_derive_scores'='true'
  AND NOT EXISTS (
    SELECT 1 FROM source_policy_versions other
    WHERE other.source_id=src.id AND other.is_active IS TRUE
      AND other.id<>pol.id
  )
  AND src.retention_days IS NOT NULL AND src.retention_days>0
  AND (pol.retention_policy->>'derived_retention_days')
      ~ '^[1-9][0-9]{0,2}$'
  AND CASE WHEN (pol.retention_policy->>'derived_retention_days')
       ~ '^[1-9][0-9]{0,2}$'
    THEN (pol.retention_policy->>'derived_retention_days')::integer BETWEEN 1 AND 365
    ELSE FALSE END
  AND ms.data_quality_status='ok' AND ms.precision='exact'
  AND ms.observation_time_assumed IS FALSE
  AND ms.source_metric_definition='platform_post_views'
  AND ms.source_metric_version IS NOT NULL
  AND ms.observed_at<=now() AND ms.collected_at<=now()
  AND (ms.expires_at IS NULL OR ms.expires_at>now())
  AND ms.observed_at + make_interval(days=>LEAST(
        src.retention_days,
        CASE WHEN (pol.retention_policy->>'derived_retention_days')
          ~ '^[1-9][0-9]{0,2}$'
          THEN (pol.retention_policy->>'derived_retention_days')::integer
          ELSE 1 END
      ))>now()
  AND NOT EXISTS (
    SELECT 1 FROM uae_snapshot_invalidations inv
    WHERE inv.project_id=sn.project_id AND inv.snapshot_id=sn.id
  )
  AND (SELECT count(*) FROM uae_snapshot_evidence e
       WHERE e.project_id=sn.project_id AND e.snapshot_id=sn.id
         AND e.state='complete' AND e.checked IS TRUE
         AND e.is_current IS TRUE)=7;

COMMENT ON VIEW uae_snapshot_current_rights_candidates IS
  'Read-side candidate IDs only, NOT publishable UAE facts: metadata/media, live SRE/QDP/A/B/C and full revocation checks required per request.';

-- Do not GRANT public SELECT on this or on base UAE snapshots.
-- No trigger rewrites history after source revocation: rights are read-live.
