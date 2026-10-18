-- ContentMachine D1.1 additive Discovery database migration.
-- Do not edit 001_foundation.sql. No destructive data operations.
-- New source capabilities and permissions remain disabled by default.

ALTER TABLE sources
    ADD COLUMN IF NOT EXISTS can_search BOOLEAN NOT NULL DEFAULT FALSE,
    ADD COLUMN IF NOT EXISTS can_store_metadata BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS source_policy_versions (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    version INTEGER NOT NULL CHECK (version > 0),
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    capabilities JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(capabilities) = 'object'),
    permissions JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(permissions) = 'object'),
    retention_policy JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(retention_policy) = 'object'),
    reference_urls JSONB NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(reference_urls) = 'array'),
    reviewed_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (source_id, version)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_source_policy_one_active
    ON source_policy_versions(source_id) WHERE is_active;

CREATE TABLE IF NOT EXISTS search_runs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    idempotency_key TEXT NOT NULL UNIQUE CHECK (length(idempotency_key) BETWEEN 1 AND 250),
    config JSONB NOT NULL CHECK (jsonb_typeof(config) = 'object'),
    config_version INTEGER NOT NULL DEFAULT 1 CHECK (config_version >= 1),
    planner_version TEXT NOT NULL,
    state TEXT NOT NULL DEFAULT 'created'
        CHECK (state IN ('created','queued','running','completed','partial','blocked','failed','cancelled')),
    cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
    error_code TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    CHECK (finished_at IS NULL OR finished_at >= created_at),
    CHECK (started_at IS NULL OR started_at >= created_at)
);
CREATE INDEX IF NOT EXISTS idx_search_runs_created
    ON search_runs(created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_search_runs_state
    ON search_runs(state, created_at DESC);

CREATE TABLE IF NOT EXISTS source_requests (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id BIGINT NOT NULL REFERENCES search_runs(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(id),
    job_id BIGINT REFERENCES jobs(id) ON DELETE SET NULL,
    query_id BIGINT REFERENCES search_queries(id) ON DELETE SET NULL,
    logical_request_key TEXT NOT NULL CHECK (length(logical_request_key) BETWEEN 1 AND 250),
    attempt_no INTEGER NOT NULL DEFAULT 1 CHECK (attempt_no >= 1),
    cursor_hash TEXT,
    state TEXT NOT NULL DEFAULT 'planned'
        CHECK (state IN ('planned','started','succeeded','failed','unknown')),
    http_status INTEGER CHECK (http_status IS NULL OR http_status BETWEEN 100 AND 599),
    provider_request_id TEXT,
    quota_units BIGINT NOT NULL DEFAULT 0 CHECK (quota_units >= 0),
    cost_usd NUMERIC(14,6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
    charge_state TEXT NOT NULL DEFAULT 'not_applicable'
        CHECK (charge_state IN ('not_applicable','reserved','charged','unknown','refunded')),
    error_code TEXT,
    started_at TIMESTAMPTZ,
    finished_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (logical_request_key, attempt_no),
    CHECK (finished_at IS NULL OR started_at IS NULL OR finished_at >= started_at)
);
CREATE INDEX IF NOT EXISTS idx_source_requests_run_started
    ON source_requests(run_id, started_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_source_requests_source_time
    ON source_requests(source_id, created_at DESC);

CREATE TABLE IF NOT EXISTS source_video_metadata (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(id),
    source_policy_version_id BIGINT REFERENCES source_policy_versions(id) ON DELETE SET NULL,
    fetched_at TIMESTAMPTZ NOT NULL,
    title TEXT,
    description TEXT,
    creator_name TEXT,
    published_at TIMESTAMPTZ,
    duration_seconds NUMERIC(10,3)
       CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
    is_current BOOLEAN NOT NULL DEFAULT TRUE,
    expires_at TIMESTAMPTZ,
    deleted_at TIMESTAMPTZ,
    UNIQUE (video_id, source_id, fetched_at),
    CHECK (expires_at IS NULL OR expires_at >= fetched_at),
    CHECK (deleted_at IS NULL OR NOT is_current)
);
CREATE INDEX IF NOT EXISTS idx_source_video_metadata_expiry
    ON source_video_metadata(expires_at) WHERE expires_at IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_source_video_metadata_video
    ON source_video_metadata(video_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS creator_topics (
    creator_id BIGINT NOT NULL REFERENCES creators(id) ON DELETE CASCADE,
    topic_id BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    relevance_state TEXT NOT NULL DEFAULT 'unknown'
       CHECK (relevance_state IN ('unknown','relevant','irrelevant','needs_review')),
    reason TEXT,
    labeled_at TIMESTAMPTZ,
    PRIMARY KEY (creator_id, topic_id)
);
CREATE INDEX IF NOT EXISTS idx_creator_topics_topic
    ON creator_topics(topic_id, relevance_state);

CREATE TABLE IF NOT EXISTS video_topics (
    video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    topic_id BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    relevance_state TEXT NOT NULL DEFAULT 'unknown'
       CHECK (relevance_state IN ('unknown','relevant','irrelevant','needs_review')),
    label_origin TEXT,
    reason TEXT,
    labeled_at TIMESTAMPTZ,
    PRIMARY KEY (video_id, topic_id)
);
CREATE INDEX IF NOT EXISTS idx_video_topics_topic
    ON video_topics(topic_id, relevance_state);

CREATE TABLE IF NOT EXISTS monitor_targets (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    target_type TEXT NOT NULL CHECK (target_type IN ('video','creator')),
    video_id BIGINT REFERENCES videos(id) ON DELETE CASCADE,
    creator_id BIGINT REFERENCES creators(id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES sources(id),
    priority INTEGER NOT NULL DEFAULT 0,
    enabled BOOLEAN NOT NULL DEFAULT FALSE,
    cadence_policy JSONB NOT NULL DEFAULT '{}'::jsonb
       CHECK (jsonb_typeof(cadence_policy) = 'object'),
    next_due_at TIMESTAMPTZ NOT NULL,
    last_checked_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (
        (target_type = 'video' AND video_id IS NOT NULL AND creator_id IS NULL)
        OR (target_type = 'creator' AND creator_id IS NOT NULL AND video_id IS NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_monitor_video_per_source
    ON monitor_targets(source_id, video_id) WHERE video_id IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS idx_monitor_creator_per_source
    ON monitor_targets(source_id, creator_id) WHERE creator_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_monitor_due
    ON monitor_targets(next_due_at, priority DESC) WHERE enabled;

CREATE TABLE IF NOT EXISTS source_quota_ledger (
    source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
    bucket_name TEXT NOT NULL CHECK (length(bucket_name) BETWEEN 1 AND 120),
    window_start_at TIMESTAMPTZ NOT NULL,
    window_end_at TIMESTAMPTZ NOT NULL,
    limit_units BIGINT CHECK (limit_units IS NULL OR limit_units >= 0),
    reserved_units BIGINT NOT NULL DEFAULT 0 CHECK (reserved_units >= 0),
    consumed_units BIGINT NOT NULL DEFAULT 0 CHECK (consumed_units >= 0),
    spend_limit_usd NUMERIC(14,6)
        CHECK (spend_limit_usd IS NULL OR spend_limit_usd >= 0),
    reserved_usd NUMERIC(14,6) NOT NULL DEFAULT 0 CHECK (reserved_usd >= 0),
    spent_usd NUMERIC(14,6) NOT NULL DEFAULT 0 CHECK (spent_usd >= 0),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, bucket_name, window_start_at),
    CHECK (window_end_at > window_start_at),
    CHECK (limit_units IS NULL OR reserved_units + consumed_units <= limit_units),
    CHECK (spend_limit_usd IS NULL OR reserved_usd + spent_usd <= spend_limit_usd)
);
CREATE INDEX IF NOT EXISTS idx_source_quota_window
    ON source_quota_ledger(source_id, window_end_at DESC);

CREATE TABLE IF NOT EXISTS data_quality_flags (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    video_id BIGINT REFERENCES videos(id) ON DELETE CASCADE,
    source_id TEXT REFERENCES sources(id) ON DELETE SET NULL,
    run_id BIGINT REFERENCES search_runs(id) ON DELETE CASCADE,
    code TEXT NOT NULL CHECK (length(code) BETWEEN 1 AND 100),
    details JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(details) = 'object'),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    resolved_at TIMESTAMPTZ,
    CHECK (video_id IS NOT NULL OR run_id IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_quality_flags_video_open
    ON data_quality_flags(video_id, created_at DESC) WHERE resolved_at IS NULL;

CREATE TABLE IF NOT EXISTS human_labels (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    topic_id BIGINT NOT NULL REFERENCES topics(id) ON DELETE CASCADE,
    label TEXT NOT NULL CHECK (label IN ('relevant','irrelevant','unsure')),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    superseded_at TIMESTAMPTZ
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_human_labels_current
    ON human_labels(video_id, topic_id) WHERE superseded_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_human_labels_topic
    ON human_labels(topic_id, created_at DESC);

-- Existing rows remain unchanged. Previous discoveries have NULL run and key.
ALTER TABLE discoveries
    ADD COLUMN IF NOT EXISTS run_id BIGINT REFERENCES search_runs(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS source_request_id BIGINT REFERENCES source_requests(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS discovery_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS idx_discoveries_logical_unique
    ON discoveries(discovery_key) WHERE discovery_key IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_discoveries_run_video
    ON discoveries(run_id, video_id) WHERE run_id IS NOT NULL;

-- Prerequisite only; queue fencing/heartbeats are not implemented by this DDL.
ALTER TABLE jobs
    ADD COLUMN IF NOT EXISTS lease_generation BIGINT NOT NULL DEFAULT 0
       CHECK (lease_generation >= 0);

ALTER TABLE metric_snapshots
    ADD COLUMN IF NOT EXISTS source_policy_version_id BIGINT
       REFERENCES source_policy_versions(id) ON DELETE SET NULL,
    ADD COLUMN IF NOT EXISTS observation_time_assumed BOOLEAN NOT NULL DEFAULT FALSE;
