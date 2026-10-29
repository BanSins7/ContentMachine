-- QDP FIX-15: additive generation fencing and unique append-only revocation.
-- Do NOT edit already applied SQL 016/040..045 or consumer-owned schemas.
ALTER TABLE qdp_runs
 ADD COLUMN invalidation_generation BIGINT NOT NULL DEFAULT 0
 CHECK (invalidation_generation>=0);
ALTER TABLE qdp_outbox_deliveries
 ADD COLUMN claimed_invalidation_generation BIGINT NOT NULL DEFAULT 0
 CHECK (claimed_invalidation_generation>=0);

-- Null-scoped revocations (whole run, video, or specific A/B/C) must replay
-- idempotently; PostgreSQL 17 supports NULLS NOT DISTINCT.
CREATE UNIQUE INDEX uq_qdp_invalidation_provenance_scope
 ON qdp_invalidations(project_id,run_id,video_id,analysis_group,origin,
                      provenance_sha256) NULLS NOT DISTINCT;
CREATE INDEX idx_qdp_revoked_events
 ON qdp_outbox(project_id,run_id,event_type,video_id,analysis_group)
 WHERE event_type='preparation_invalidated';
