-- UAE-GC integration v1: one durable Group C -> UAE reception per immutable outbox event.
-- Dependencies: GC056..064 and UAE080..090; transactional PG17 migrations only.
-- Receipt ID is an intake ACK identifier (NOT an analytics_unified_snapshots ID).
-- No C7 calculation, qualification, public view, default GRANT, or background worker.
CREATE TABLE uae_group_c_receptions (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
  outbox_id UUID NOT NULL REFERENCES group_c_outbox(id) ON DELETE CASCADE,
  run_id UUID NOT NULL,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  calculated_metric_id BIGINT NOT NULL REFERENCES calculated_metrics(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id),
  source_policy_version_id BIGINT NOT NULL REFERENCES source_policy_versions(id),
  snapshot_id BIGINT NOT NULL REFERENCES metric_snapshots(id) ON DELETE CASCADE,
  previous_snapshot_id BIGINT NOT NULL REFERENCES metric_snapshots(id) ON DELETE CASCADE,
  published_metadata_id BIGINT NOT NULL REFERENCES source_video_metadata(id) ON DELETE CASCADE,
  calculation_config_id BIGINT NOT NULL REFERENCES metric_calculation_configs(id),
  evidence_sha256 TEXT NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$'),
  event_sha256 TEXT NOT NULL CHECK(event_sha256 ~ '^[0-9a-f]{64}$'),
  breakout_score NUMERIC(30,12) NOT NULL,
  state TEXT NOT NULL DEFAULT 'received_unqualified'
    CHECK(state='received_unqualified'),
  received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,outbox_id),
  UNIQUE(project_id,event_sha256),
  FOREIGN KEY(project_id,run_id,video_id)
    REFERENCES group_c_runs(project_id,id,video_id) ON DELETE CASCADE,
  CHECK(snapshot_id <> previous_snapshot_id)
);
CREATE INDEX uae_gc_receptions_project_video
  ON uae_group_c_receptions(project_id,video_id,received_at DESC,id DESC);
CREATE INDEX uae_gc_receptions_metric
  ON uae_group_c_receptions(project_id,calculated_metric_id);

CREATE FUNCTION uae_group_c_reception_immutable() RETURNS trigger
LANGUAGE plpgsql AS $uae_gc_immut$
BEGIN
  RAISE EXCEPTION 'Group C reception identity is immutable' USING ERRCODE='23514';
END
$uae_gc_immut$;
CREATE TRIGGER uae_group_c_reception_immutable_trg
 BEFORE UPDATE ON uae_group_c_receptions FOR EACH ROW
 EXECUTE FUNCTION uae_group_c_reception_immutable();

COMMENT ON TABLE uae_group_c_receptions IS
 'Original Group C C7 receipt. Immutable intake only, never qualifies UAE or permits stale score reads; '
 'each access must recheck current Group C source rights, QDP and provenance. '
 'Revoke/erasure must delete intake rows by the authorized lifecycle executor.';
-- No broad GRANT, RLS bypass, implicit UAE snapshot, or publication side effect.
