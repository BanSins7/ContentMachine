-- MI private media analysis store. Uses D1 video identity but never A/B/C tables.
-- This additive migration is safe for legacy RTB and preserves media provenance.
CREATE TABLE IF NOT EXISTS mi_video_analyses (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
 video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
 source_id UUID NOT NULL,
 source_version_id UUID NOT NULL,
 media_asset_id UUID NOT NULL,
 media_sha256 TEXT NOT NULL CHECK (media_sha256 ~ '^[0-9a-f]{64}$'),
 method_version TEXT NOT NULL CHECK (method_version='mi-independent-media/v1'),
 technical JSONB NOT NULL CHECK (jsonb_typeof(technical)='object'),
 creative JSONB NOT NULL CHECK (jsonb_typeof(creative)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,video_id,media_sha256,method_version),
 FOREIGN KEY (project_id,source_id,source_version_id,media_asset_id)
   REFERENCES project_media_assets(project_id,source_id,source_version_id,id)
);
CREATE INDEX IF NOT EXISTS mi_video_analysis_lookup
 ON mi_video_analyses(project_id,video_id,created_at DESC,id DESC);

CREATE OR REPLACE FUNCTION mi_media_analysis_immutable() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'MI evidence is immutable';
END $$;
CREATE TRIGGER trg_mi_media_analysis_immutable
 BEFORE UPDATE OR DELETE ON mi_video_analyses
 FOR EACH ROW EXECUTE FUNCTION mi_media_analysis_immutable();
