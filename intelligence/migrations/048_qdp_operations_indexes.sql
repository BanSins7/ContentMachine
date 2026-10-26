-- QDP FIX-17: FORWARD-ONLY operational indexes; no changes to applied 016/040..047.
-- These indexes support bounded keyset reads and per-consumer delivery scans.
CREATE INDEX IF NOT EXISTS idx_qdp_17_project_run_video
 ON qdp_items(project_id,run_id,video_id);
CREATE INDEX IF NOT EXISTS idx_qdp_17_run_events_page
 ON qdp_outbox(project_id,run_id,created_at DESC,event_id DESC);
CREATE INDEX IF NOT EXISTS idx_qdp_17_delivery_consumer_due
 ON qdp_outbox_deliveries(consumer,state,run_after,event_id)
 WHERE state IN ('pending','leased');
CREATE INDEX IF NOT EXISTS idx_qdp_17_outbox_group_readiness
 ON qdp_preparations(project_id,run_id,analysis_group,video_id)
 WHERE state='ready';
CREATE INDEX IF NOT EXISTS idx_qdp_17_invalidation_run
 ON qdp_invalidations(project_id,run_id,created_at DESC,id DESC);
