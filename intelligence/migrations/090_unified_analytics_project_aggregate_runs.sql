-- UA-MOD-24 | Internal immutable project aggregation RUN manifest (PG17).
-- No qualified/public release, GRANT, trigger-based score elevation or API.
-- A run is a digest pointer, not a cache of persisted Group C scores.
-- Revalidate every selected UA19 snapshot at EVERY use; old manifests alone
-- are NOT authorization. No RTB claim: current UAE candidate lacks RTB port.
-- Migration file must be immutable after initial deploy.
CREATE TABLE uae_project_aggregate_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
  request_key TEXT NOT NULL
    CHECK (length(request_key) BETWEEN 8 AND 120 AND
           request_key ~ '^[A-Za-z0-9_.:-]+$'),
  run_revision INTEGER NOT NULL CHECK (run_revision>0),
  schema_version TEXT NOT NULL DEFAULT 'uae-project-ranked-run/v1'
    CHECK (schema_version='uae-project-ranked-run/v1'),
  sort_mode TEXT NOT NULL
    CHECK (sort_mode IN ('breakout_desc','views_desc','video_id_asc')),
  sample_size INTEGER NOT NULL CHECK (sample_size BETWEEN 5 AND 50),
  compatibility_sha256 TEXT NOT NULL CHECK (
    compatibility_sha256 ~ '^[0-9a-f]{64}$'),
  lineage_sha256 TEXT NOT NULL CHECK (
    lineage_sha256 ~ '^[0-9a-f]{64}$'),
  stats_sha256 TEXT NOT NULL CHECK (
    stats_sha256 ~ '^[0-9a-f]{64}$'),
  ranked_sha256 TEXT NOT NULL CHECK (
    ranked_sha256 ~ '^[0-9a-f]{64}$'),
  rtb_evidence_state TEXT NOT NULL DEFAULT 'unavailable_not_bound'
    CHECK (rtb_evidence_state='unavailable_not_bound'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (project_id,id),
  UNIQUE (project_id,request_key,run_revision)
);
CREATE INDEX uae_project_aggregate_runs_project_version_idx
 ON uae_project_aggregate_runs(project_id,created_at DESC,id DESC);

CREATE FUNCTION uae_project_aggregate_run_immutable() RETURNS trigger
LANGUAGE plpgsql AS $uae_run_guard$
BEGIN
 RAISE EXCEPTION 'UAE aggregate run manifest is immutable'
   USING ERRCODE='23514';
END
$uae_run_guard$;
-- Logical immutability forbids modifying a run in place. Do NOT block
-- project-level ON DELETE CASCADE retention/erasure; deletion remains owner-
-- controlled because no general DELETE permission is granted.
CREATE TRIGGER uae_project_aggregate_runs_immutable_trg
 BEFORE UPDATE ON uae_project_aggregate_runs
 FOR EACH ROW EXECUTE FUNCTION uae_project_aggregate_run_immutable();

COMMENT ON TABLE uae_project_aggregate_runs IS
 'UA24 internal immutable-by-version digest manifest, never sufficient for read rights; '
 'no metrics, media or raw Group C values. RTB capability not bound.';

-- No SELECT/INSERT GRANT, public view, HTTP route, background job or score cache.
