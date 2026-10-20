-- Taxonomy Builder / Block 2: exact-profile human intake confirmation.
-- Additive to existing project_registry and project_profile_versions (003).
-- No paid calls, models, or taxonomy generation are enabled by this migration.
ALTER TABLE project_profile_versions
  ADD CONSTRAINT tb_intake_profile_key UNIQUE(project_id,revision,profile_hash);

CREATE TABLE tb_intake_confirmations (
  project_id TEXT NOT NULL,
  profile_revision INTEGER NOT NULL CHECK (profile_revision>0),
  profile_hash TEXT NOT NULL CHECK (profile_hash ~ '^[0-9a-f]{64}$'),
  confirmed_by TEXT NOT NULL CHECK (length(btrim(confirmed_by))>0),
  confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(project_id,profile_revision),
  FOREIGN KEY(project_id,profile_revision,profile_hash)
    REFERENCES project_profile_versions(project_id,revision,profile_hash) ON DELETE CASCADE
);
CREATE INDEX idx_tb_confirmations_lookup
  ON tb_intake_confirmations(project_id,confirmed_at DESC);

-- Existing confirmations cannot be changed in-place to approve another revision.
CREATE FUNCTION tb_block_confirmation_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Confirmed intake is immutable; confirm a new profile revision';
END $$;
CREATE TRIGGER tb_confirmations_immutable
  BEFORE UPDATE ON tb_intake_confirmations
  FOR EACH ROW EXECUTE FUNCTION tb_block_confirmation_update();
