-- UA-MOD-19 | UAE read-side candidate visibility (PostgreSQL 17).
-- Additive, read-only, project scoped, no public grant or HTTP handler.
-- The view returns ONLY internal IDs and is NOT a qualified publication API.
-- Full per-asset rights plus SRE/QDP/A/B/C current approval must be re-read
-- by UAE's Python VisibleSnapshotReader. Peer feature schemas are NOT in main.
-- 085 currently forbids qualified inserts; 084 payloads are unapproved only.
-- Consequently this view safely yields NO positive production rows until
-- a separately reviewed future effective-approval migration exists.

CREATE INDEX uae_revision_visibility_scope_idx
 ON uae_revision_inbox(
    project_id,video_id,source_id,source_policy_version_id,observed_at DESC
 );

CREATE VIEW uae_readside_visibility_candidates
 WITH (security_barrier=true, security_invoker=true) AS
SELECT sn.project_id,sn.id AS snapshot_id,sn.video_id
FROM uae_unified_snapshots sn
JOIN sre_project_videos membership
  ON membership.project_id=sn.project_id
 AND membership.video_id=sn.video_id
JOIN uae_qualification_requests req
  ON req.project_id=sn.project_id AND req.id=sn.request_id
 AND req.video_id=sn.video_id AND req.source_id=sn.source_id
 AND req.source_policy_version_id=sn.source_policy_version_id
 AND req.qdp_run_id=sn.qdp_run_id
 AND req.qdp_input_sha256=sn.qdp_input_sha256
JOIN uae_snapshot_payloads payload
  ON payload.project_id=sn.project_id AND payload.snapshot_id=sn.id
 AND payload.decision_sha256=sn.decision_sha256
JOIN sources src ON src.id=sn.source_id
JOIN source_policy_versions pol
  ON pol.id=sn.source_policy_version_id AND pol.source_id=src.id
JOIN metric_snapshots metric
  ON metric.id=sn.metric_snapshot_id
 AND metric.video_id=sn.video_id AND metric.source_id=sn.source_id
 AND metric.source_policy_version_id=sn.source_policy_version_id
JOIN sre_workspaces workspace ON workspace.project_id=sn.project_id
WHERE sn.status='qualified'
  AND sn.verified_as_of<=statement_timestamp()
  AND req.state='resolved'
  AND src.status='enabled'
  AND src.updated_at<=statement_timestamp()
  AND src.can_store_metadata IS TRUE
  AND src.can_analyze_media IS TRUE
  AND src.can_store_metrics IS TRUE
  AND src.can_derive_scores IS TRUE
  AND pol.is_active IS TRUE AND pol.reviewed_at IS NOT NULL
  AND pol.reviewed_at<=statement_timestamp()
  AND pol.permissions->>'can_store_metadata'='true'
  AND pol.permissions->>'can_analyze_media'='true'
  AND pol.permissions->>'can_store_metrics'='true'
  AND pol.permissions->>'can_derive_scores'='true'
  AND NOT EXISTS (
    SELECT 1 FROM source_policy_versions other
    WHERE other.source_id=src.id AND other.is_active IS TRUE
      AND other.id<>pol.id
  )
  AND src.retention_days>0
  AND CASE
    WHEN pol.retention_policy->>'derived_retention_days'
           ~ '^[1-9][0-9]{0,2}$'
    THEN (pol.retention_policy->>'derived_retention_days')::integer
         BETWEEN 1 AND 365
    ELSE FALSE
  END
  AND metric.data_quality_status='ok'
  AND metric.precision='exact'
  AND metric.observation_time_assumed IS FALSE
  AND metric.source_metric_definition='platform_post_views'
  AND metric.source_metric_version IS NOT NULL
  AND metric.observed_at<=statement_timestamp()
  AND metric.collected_at<=statement_timestamp()
  AND (metric.expires_at IS NULL
       OR metric.expires_at>statement_timestamp())
  AND metric.observed_at+make_interval(
     days=>LEAST(src.retention_days,CASE
        WHEN pol.retention_policy->>'derived_retention_days'
               ~ '^[1-9][0-9]{0,2}$'
        THEN (pol.retention_policy->>'derived_retention_days')::integer
        ELSE 1 END))>statement_timestamp()
  AND payload.assembly_schema='uae-snapshot-assembly/v1'
  AND payload.payload->>'status'='qualified'
  AND payload.payload->>'scope_sha256'=sn.scope_sha256
  AND payload.payload->>'input_sha256'=sn.inputs_sha256
  AND payload.payload->>'decision_sha256'=sn.decision_sha256
  AND payload.payload->>'project_id'=sn.project_id
  AND payload.payload->>'video_id'=sn.video_id::text
  AND payload.payload->>'source_id'=sn.source_id
  AND payload.payload->>'source_policy_version_id'
      =sn.source_policy_version_id::text
  AND payload.payload->'versions'->>'calculated_metric_id'
      =sn.calculated_metric_id::text
  AND payload.payload->'versions'->>'group_a_analysis_id'
      =sn.group_a_analysis_id::text
  AND payload.payload->'versions'->>'group_b_analysis_id'
      =sn.group_b_analysis_id::text
  AND workspace.active_taxonomy_id IS NOT NULL
  AND payload.payload->'versions'->>'sre_taxonomy_version_id'
      =workspace.active_taxonomy_id::text
  AND NOT EXISTS (
    SELECT 1 FROM uae_snapshot_invalidations inv
    WHERE inv.project_id=sn.project_id AND inv.snapshot_id=sn.id
  )
  AND (SELECT count(*) FROM uae_snapshot_evidence ev
       WHERE ev.project_id=sn.project_id AND ev.snapshot_id=sn.id
         AND ev.state='complete' AND ev.checked IS TRUE
         AND ev.is_current IS TRUE)=7
  -- Three actual UA07 receipts must belong to THIS exact QDP run/video.
  -- They do not substitute for LIVE QDP/SRE/A/B/C peer verification.
  AND (SELECT count(DISTINCT h.analysis_group)
       FROM uae_qdp_handoff_inbox h
       WHERE h.project_id=sn.project_id
         AND h.video_id=sn.video_id AND h.qdp_run_id=sn.qdp_run_id
         AND h.analysis_group IN ('A','B','C'))=3
  -- Conservatively suppress any later source/QDP/SRE/A/B/C revision,
  -- including rejected Group C human reviews, even when event-specific
  -- invalidation was not yet processed for a historical snapshot.
  AND NOT EXISTS (
    SELECT 1 FROM uae_revision_inbox revision
    WHERE revision.project_id=sn.project_id
      AND revision.video_id=sn.video_id
      AND revision.source_id=sn.source_id
      AND revision.source_policy_version_id=sn.source_policy_version_id
      AND (revision.observed_at>=sn.verified_as_of
           OR revision.event_kind='source_policy_revoked')
  );

COMMENT ON VIEW uae_readside_visibility_candidates IS
 'Internal candidate identifiers ONLY; no payload or score and no SELECT grant. Current per-media evidence, LIVE SRE/QDP/Group A/B/C approvals, snapshot/payload checksums and user authorization remain mandatory in Python.';

-- SECURITY_INVOKER means no privilege elevation. We issue no GRANT,
-- including no GRANT on qualified views, UAE base tables, metrics or scores.
-- No materialized cache: revocation, expiry and membership are rechecked
-- on every fresh statement; old snapshot rows are NEVER rewritten.
