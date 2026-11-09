-- Market Intelligence: user-triggered profile listing, metadata-only.
-- The canonical video is videos.id; discovered publications only preserve
-- project/profile provenance and never imply metrics or media permissions.
CREATE TABLE IF NOT EXISTS mi_author_publications (
 project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
 profile_url TEXT NOT NULL CHECK (length(profile_url) BETWEEN 10 AND 2048),
 platform TEXT NOT NULL CHECK (platform IN ('tiktok','instagram','youtube')),
 platform_video_id TEXT NOT NULL,
 video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
 discovery_job_id BIGINT NOT NULL REFERENCES jobs(id) ON DELETE RESTRICT,
 discovered_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(project_id,profile_url,platform,platform_video_id),
 UNIQUE(project_id,profile_url,video_id)
);
CREATE INDEX IF NOT EXISTS mi_author_publications_video
 ON mi_author_publications(project_id,video_id);
CREATE INDEX IF NOT EXISTS mi_author_publications_job
 ON mi_author_publications(discovery_job_id);

-- Registered as user-triggered PUBLIC PROFILE URL listing, not a provider
-- verified source of rights for video analysis or performance snapshots.
-- All actual metrics remain NULL/unavailable until independently obtained
-- under a source policy that permits them.
