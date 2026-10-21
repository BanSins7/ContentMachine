-- Fix: current OpenRouter catalog observation must reference exact observed snapshot.
-- Existing immutable snapshot history stays intact. This is additive, not a rewrite.
CREATE TABLE tb_catalog_refresh_models (
  refresh_id BIGINT NOT NULL REFERENCES tb_catalog_refreshes(id),
  model_id TEXT NOT NULL REFERENCES tb_model_candidates(model_id),
  snapshot_id BIGINT NOT NULL,
  PRIMARY KEY(refresh_id,model_id),
  FOREIGN KEY(snapshot_id,model_id)
    REFERENCES tb_model_snapshots(id,model_id)
);
CREATE INDEX idx_tb_catalog_refresh_snapshots
  ON tb_catalog_refresh_models(model_id,refresh_id DESC);
