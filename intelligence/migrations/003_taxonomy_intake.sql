-- ContentMachine taxonomy intake: additive project identity and immutable profile revisions.
-- Projects continue to exist as local JSON; this is a mirror for future Taxonomy Builder.
CREATE TABLE IF NOT EXISTS project_registry (
    id TEXT PRIMARY KEY CHECK (id ~ '^[0-9a-f]{32}$'),
    name TEXT NOT NULL,
    profile_revision INTEGER NOT NULL CHECK (profile_revision >= 1),
    profile_hash TEXT NOT NULL CHECK (length(profile_hash) = 64),
    created_at TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL,
    CHECK (updated_at >= created_at)
);

CREATE TABLE IF NOT EXISTS project_profile_versions (
    project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
    revision INTEGER NOT NULL CHECK (revision >= 1),
    schema_version INTEGER NOT NULL CHECK (schema_version >= 1),
    profile_hash TEXT NOT NULL CHECK (length(profile_hash) = 64),
    profile JSONB NOT NULL CHECK (jsonb_typeof(profile) = 'object'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (project_id, revision)
);
CREATE INDEX IF NOT EXISTS idx_profile_versions_recorded
    ON project_profile_versions(project_id, recorded_at DESC);
