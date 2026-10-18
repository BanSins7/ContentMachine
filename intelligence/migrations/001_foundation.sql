-- ContentMachine Intelligence V1. Every metric stays attached to a provider and observation time.
-- Only authorized data may be stored, analyzed or retained. Rights default to deny.
CREATE TABLE IF NOT EXISTS sources (
  id TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  source_type TEXT NOT NULL CHECK (source_type IN ('official','commercial','manual','owned')),
  status TEXT NOT NULL DEFAULT 'unconfigured',
  can_store_metrics BOOLEAN NOT NULL DEFAULT FALSE,
  can_derive_scores BOOLEAN NOT NULL DEFAULT FALSE,
  can_analyze_media BOOLEAN NOT NULL DEFAULT FALSE,
  retention_days INTEGER CHECK (retention_days IS NULL OR retention_days > 0),
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS topics (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  language TEXT,
  region TEXT,
  query_config JSONB NOT NULL DEFAULT '{}'::jsonb,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS creators (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  platform TEXT NOT NULL CHECK (platform IN ('youtube','tiktok','instagram')),
  platform_creator_id TEXT NOT NULL,
  username TEXT,
  canonical_url TEXT,
  topic_id BIGINT REFERENCES topics(id) ON DELETE SET NULL,
  language TEXT,
  region TEXT,
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(platform, platform_creator_id)
);
CREATE TABLE IF NOT EXISTS videos (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  platform TEXT NOT NULL CHECK (platform IN ('youtube','tiktok','instagram')),
  platform_video_id TEXT NOT NULL,
  creator_id BIGINT REFERENCES creators(id) ON DELETE SET NULL,
  canonical_url TEXT NOT NULL,
  published_at TIMESTAMPTZ,
  duration_seconds NUMERIC(10,3) CHECK (duration_seconds IS NULL OR duration_seconds >= 0),
  title TEXT,
  description TEXT,
  language TEXT,
  content_kind TEXT,
  paid_status TEXT NOT NULL DEFAULT 'unknown' CHECK (paid_status IN ('unknown','organic','paid')),
  first_seen_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(platform, platform_video_id)
);
CREATE INDEX IF NOT EXISTS idx_videos_platform_published ON videos(platform, published_at DESC);
CREATE INDEX IF NOT EXISTS idx_videos_creator_published ON videos(creator_id, published_at DESC);
CREATE TABLE IF NOT EXISTS search_queries (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  topic_id BIGINT REFERENCES topics(id) ON DELETE SET NULL,
  source_id TEXT REFERENCES sources(id) ON DELETE SET NULL,
  query_text TEXT NOT NULL,
  method TEXT NOT NULL,
  parent_query_id BIGINT REFERENCES search_queries(id) ON DELETE SET NULL,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS discoveries (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id),
  search_query_id BIGINT REFERENCES search_queries(id) ON DELETE SET NULL,
  method TEXT NOT NULL,
  source_rank INTEGER CHECK (source_rank IS NULL OR source_rank >= 0),
  provider_request_id TEXT,
  found_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_discoveries_video_source ON discoveries(video_id, source_id, found_at DESC);
CREATE TABLE IF NOT EXISTS metric_snapshots (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id),
  observed_at TIMESTAMPTZ NOT NULL,
  views BIGINT CHECK (views IS NULL OR views >= 0),
  likes BIGINT CHECK (likes IS NULL OR likes >= 0),
  comments BIGINT CHECK (comments IS NULL OR comments >= 0),
  shares BIGINT CHECK (shares IS NULL OR shares >= 0),
  saves BIGINT CHECK (saves IS NULL OR saves >= 0),
  followers BIGINT CHECK (followers IS NULL OR followers >= 0),
  precision TEXT NOT NULL DEFAULT 'exact' CHECK (precision IN ('exact','rounded','estimated','unknown')),
  expires_at TIMESTAMPTZ,
  collected_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(video_id, source_id, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_snapshots_video_time ON metric_snapshots(video_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_snapshots_expiry ON metric_snapshots(expires_at) WHERE expires_at IS NOT NULL;
CREATE TABLE IF NOT EXISTS jobs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  job_type TEXT NOT NULL,
  idempotency_key TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL DEFAULT 'queued'
     CHECK (state IN ('queued','running','completed','failed','cancelled')),
  priority INTEGER NOT NULL DEFAULT 0,
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts > 0),
  run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
  lease_until TIMESTAMPTZ,
  locked_by TEXT,
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  result JSONB,
  last_error TEXT,
  cost_usd NUMERIC(12,6) NOT NULL DEFAULT 0 CHECK (cost_usd >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_jobs_poll ON jobs(state, run_after, priority DESC);
CREATE INDEX IF NOT EXISTS idx_jobs_lease ON jobs(lease_until) WHERE state = 'running';
CREATE TABLE IF NOT EXISTS analytics_scores (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id),
  algorithm TEXT NOT NULL,
  algorithm_version TEXT NOT NULL,
  computed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  components JSONB NOT NULL,
  peer_sample_count INTEGER CHECK (peer_sample_count IS NULL OR peer_sample_count >= 0),
  data_confidence NUMERIC(5,2),
  CHECK (data_confidence IS NULL OR (data_confidence >= 0 AND data_confidence <= 100))
);
CREATE INDEX IF NOT EXISTS idx_scores_video_time ON analytics_scores(video_id, computed_at DESC);
CREATE TABLE IF NOT EXISTS batches (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  topic_id BIGINT REFERENCES topics(id) ON DELETE SET NULL,
  kind TEXT NOT NULL CHECK (kind IN ('breakouts','emerging','proven','established','control','explore')),
  version TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS batch_videos (
  batch_id BIGINT NOT NULL REFERENCES batches(id) ON DELETE CASCADE,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  reason JSONB NOT NULL DEFAULT '{}'::jsonb,
  PRIMARY KEY(batch_id, video_id)
);
CREATE TABLE IF NOT EXISTS media_assets (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id),
  media_kind TEXT NOT NULL,
  local_path TEXT NOT NULL,
  sha256 TEXT,
  permission TEXT NOT NULL DEFAULT 'metadata_only',
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS video_analyses (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  media_asset_id BIGINT REFERENCES media_assets(id) ON DELETE SET NULL,
  model_name TEXT NOT NULL,
  model_version TEXT,
  prompt_version TEXT,
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb,
  hypotheses JSONB NOT NULL DEFAULT '[]'::jsonb,
  result JSONB NOT NULL DEFAULT '{}'::jsonb,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS audit_events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  action TEXT NOT NULL,
  source_id TEXT REFERENCES sources(id) ON DELETE SET NULL,
  object_type TEXT,
  object_id TEXT,
  details JSONB NOT NULL DEFAULT '{}'::jsonb,
  happened_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
