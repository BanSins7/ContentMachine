-- UA-MOD-05 | Canonical project-to-video membership belongs to SRE/D1.
-- Forward-only additive integrity guard after 080_unified_analytics_core.sql.
-- SRE project's membership table exists in main 006_discovery_sre_workspace.sql.
-- No foreign keys to feature-only QDP, Group A/B/C tables and no SRE writes.
--
-- A project registry row and a globally existing videos.id do NOT, separately,
-- prove that the same project owns this video. Enforce the COMPOSITE identity.
-- Deleting membership cascades UAE request/snapshot history: a revoked project
-- cannot leave orphaned snapshots to accidentally appear in future read views.
-- Current rights, taxonomy, LIVE run and QDP membership still need read checks.

ALTER TABLE uae_qualification_requests
  ADD CONSTRAINT uae_request_project_video_membership_fk
  FOREIGN KEY (project_id, video_id)
  REFERENCES sre_project_videos(project_id, video_id)
  ON DELETE CASCADE;

COMMENT ON CONSTRAINT uae_request_project_video_membership_fk
  ON uae_qualification_requests IS
  'Exact current D1/SRE project+videos.id membership; not source or QDP qualification';

-- No source-policy or QDP proof is inferred merely from this composite FK.
-- 080 guards independently enforce exact source and policy version.
