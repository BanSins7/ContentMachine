-- Market Intelligence Stage 1. Additive, never modify legacy RTB/Collector/Discovery.
-- Sources stay in project_sources; videos/metrics stay in canonical D1/Group C.
-- This table contains frozen reports, NOT alternative metric or media truths.
CREATE TABLE IF NOT EXISTS market_intelligence_snapshots (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
 payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
 payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
 status TEXT NOT NULL CHECK (status IN ('partial','no_discovery_data')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id, id),
 UNIQUE(project_id, payload_sha256),
 CHECK (payload->>'project_id' = project_id),
 CHECK (payload->>'schema_version' = 'market-intelligence-snapshot/v1'),
 CHECK (payload->>'status' = status),
 CHECK (payload->'market_hypotheses_verified' = 'false'::jsonb)
);
CREATE INDEX IF NOT EXISTS idx_mi_project_created
 ON market_intelligence_snapshots(project_id, created_at DESC, id DESC);

CREATE OR REPLACE FUNCTION cm_mi_immutable() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Market Intelligence snapshots are immutable';
END $$;
CREATE TRIGGER trg_mi_immutable BEFORE UPDATE OR DELETE ON market_intelligence_snapshots
 FOR EACH ROW EXECUTE FUNCTION cm_mi_immutable();
