-- MI extra numeric fields from Bright Data snapshots.
-- Does not alter canonical D1 snapshots or claim proprietary watch analytics.
-- Each record is linked to the exact accepted D1 metric snapshot.
-- No raw post JSON, comment bodies, user identifiers, tokens or media.
CREATE TABLE IF NOT EXISTS mi_brightdata_numeric_fields (
    metric_snapshot_id BIGINT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
    video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    platform TEXT NOT NULL CHECK (platform IN ('tiktok','instagram','youtube')),
    source_id TEXT NOT NULL REFERENCES sources(id),
    fields JSONB NOT NULL CHECK (jsonb_typeof(fields) = 'object'),
    extraction_version TEXT NOT NULL DEFAULT 'mi-brightdata-numeric/v1',
    observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at TIMESTAMPTZ NOT NULL,
    FOREIGN KEY (metric_snapshot_id, video_id)
      REFERENCES metric_snapshots(id,video_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS mi_bd_numeric_project_video
  ON mi_brightdata_numeric_fields(project_id,video_id,observed_at DESC);
CREATE INDEX IF NOT EXISTS mi_bd_numeric_expiry
  ON mi_brightdata_numeric_fields(expires_at);
-- The normal retention job can delete expired values without retaining data
-- beyond the provider metrics' lifespan.
CREATE OR REPLACE FUNCTION mi_brightdata_numeric_immutable()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Market provider numeric evidence is immutable';
END $$;
CREATE TRIGGER trg_mi_brightdata_numeric_immutable
  BEFORE UPDATE ON mi_brightdata_numeric_fields
  FOR EACH ROW EXECUTE FUNCTION mi_brightdata_numeric_immutable();
