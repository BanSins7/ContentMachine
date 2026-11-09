-- Native image carousels in standalone Marketing. Does not depend on QDP.
-- Immutable ordered SHA slide manifest, independent from MP4 asset kind.
-- Group C continues to consume canonical D1 metric_snapshots of the same post.
CREATE TABLE IF NOT EXISTS mi_carousel_analyses(
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  source_id UUID NOT NULL,
  source_version_id UUID NOT NULL,
  platform TEXT NOT NULL CHECK(platform IN ('tiktok','instagram')),
  native_id TEXT NOT NULL,
  canonical_post_url TEXT NOT NULL,
  content_kind TEXT NOT NULL CHECK(content_kind='photo_carousel'),
  method_version TEXT NOT NULL CHECK(method_version='mi-photo-carousel/v1'),
  source_manifest_sha256 TEXT NOT NULL CHECK(source_manifest_sha256 ~ '^[0-9a-f]{64}$'),
  local_storage_ref TEXT NOT NULL,
  manifest JSONB NOT NULL CHECK(jsonb_typeof(manifest)='object'),
  technical JSONB NOT NULL CHECK(jsonb_typeof(technical)='object'),
  creative JSONB NOT NULL CHECK(jsonb_typeof(creative)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id,video_id,source_manifest_sha256,method_version),
  FOREIGN KEY(project_id,source_id) REFERENCES project_sources(project_id,id)
);
CREATE INDEX IF NOT EXISTS mi_carousel_analyses_latest
  ON mi_carousel_analyses(project_id,video_id,created_at DESC,id DESC);
CREATE OR REPLACE FUNCTION mi_carousel_analysis_immutable()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Marketing source carousel evidence is immutable';
END $$;
CREATE TRIGGER trg_mi_carousel_analysis_immutable
  BEFORE UPDATE OR DELETE ON mi_carousel_analyses
  FOR EACH ROW EXECUTE FUNCTION mi_carousel_analysis_immutable();
