-- RTB: mandatory immutable human topic review before sending paid Critic.
-- Existing runs in critic_pending must also obtain a review receipt.
-- No automatic retries or spending consent changes.
CREATE TABLE tb_topic_precritic_reviews (
  run_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL,
  approved_topics JSONB NOT NULL CHECK(jsonb_typeof(approved_topics)='array'),
  reviewed_by TEXT NOT NULL,
  reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(run_id,project_id) REFERENCES tb_runs(id,project_id)
);
CREATE TRIGGER tb_topic_precritic_review_immutable
  BEFORE UPDATE OR DELETE ON tb_topic_precritic_reviews
  FOR EACH ROW EXECUTE FUNCTION tb_block_review_mutations();
