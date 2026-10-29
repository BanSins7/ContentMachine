-- GC-MOD-29: additive measured-performance candidates for PostgreSQL 17.
-- Indexes are targeted to exact GC09 latest-source, GC28 newest-metric,
-- GC21 worker claim/recovery and source metadata query shapes.
-- Not CONCURRENTLY: migration runner is transactional; deploy under a
-- reviewed maintenance/lock-budget window, never rewrite migrations 003-063.
-- Actual planner selection and throughput require real EXPLAIN ANALYZE.
CREATE INDEX idx_gc29_raw_source_video_latest
    ON metric_snapshots(source_id, video_id, observed_at DESC, id DESC);
CREATE INDEX idx_gc29_observation_latest
    ON metric_snapshots(video_id, observed_at DESC, id DESC)
    WHERE source_metric_definition='platform_post_views';
CREATE INDEX idx_gc29_metadata_source_latest
    ON source_video_metadata(video_id, source_id, fetched_at DESC, id DESC);
CREATE INDEX idx_gc29_worker_project_ready
    ON group_c_jobs(project_id, available_at, id)
    WHERE state='queued' AND job_kind='score';
CREATE INDEX idx_gc29_worker_project_lease
    ON group_c_jobs(project_id, lease_until, id)
    WHERE state='running';
CREATE INDEX idx_gc29_outbox_project_ready
    ON group_c_outbox(project_id, created_at, id)
    WHERE state IN ('pending','failed');
