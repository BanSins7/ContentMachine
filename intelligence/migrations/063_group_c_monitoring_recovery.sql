-- GC-MOD-28: append-only, human-authorized recovery provenance.
-- No rewriting existing 003/004/056..062; no automated physical erasure.
CREATE TABLE group_c_recovery_operations (
  id UUID PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE RESTRICT,
  action TEXT NOT NULL CHECK(action='recover_expired_jobs'),
  request_sha256 TEXT NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
  authority_sha256 TEXT NOT NULL CHECK(authority_sha256 ~ '^[0-9a-f]{64}$'),
  recovered_job_ids JSONB NOT NULL CHECK(jsonb_typeof(recovered_job_ids)='array'),
  recovered_count INTEGER NOT NULL CHECK(recovered_count>=0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (jsonb_array_length(recovered_job_ids)=recovered_count),
  UNIQUE(project_id,id)
);
CREATE INDEX idx_group_c_recovery_project_recent
 ON group_c_recovery_operations(project_id,created_at DESC,id DESC);
CREATE FUNCTION group_c_recovery_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Group C recovery evidence immutable' USING ERRCODE='23514';
END $$;
CREATE TRIGGER trg_gc_recovery_immutable
 BEFORE UPDATE OR DELETE ON group_c_recovery_operations
 FOR EACH ROW EXECUTE FUNCTION group_c_recovery_immutable();
