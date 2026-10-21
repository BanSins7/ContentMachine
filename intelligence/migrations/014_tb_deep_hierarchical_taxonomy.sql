-- Optional two-pass hierarchical Research Taxonomy extension.
-- Existing flat Taxonomy Builder runs remain unchanged. Deep mode can start
-- only from an approved-facts, generation_pending run; no automatic LLM calls.
CREATE TABLE tb_deep_runs (
 run_id UUID PRIMARY KEY,
 project_id TEXT NOT NULL,
 status TEXT NOT NULL CHECK(status IN (
  'pass1_pending','pass1_running','review1','pass2_pending','pass2_running',
  'expand_pending','expand_running','review2','approved','failed','uncertain',
  'paused_budget','paused_price','blocked_quality')),
 expansion_count SMALLINT NOT NULL DEFAULT 0 CHECK(expansion_count BETWEEN 0 AND 2),
 quality_report JSONB,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(run_id,project_id) REFERENCES tb_runs(id,project_id)
);
CREATE INDEX idx_tb_deep_recent ON tb_deep_runs(project_id,created_at DESC);

CREATE TABLE tb_deep_attempts (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 stage TEXT NOT NULL CHECK(stage IN ('deep_first','deep_second','deep_expand_1','deep_expand_2')),
 status TEXT NOT NULL CHECK(status IN ('reserved','completed','error','uncertain')),
 reservation_usd NUMERIC(18,8) NOT NULL CHECK(reservation_usd>0),
 charged_usd NUMERIC(18,8) CHECK(charged_usd>=0),
 model_id TEXT NOT NULL,
 request_sha256 TEXT NOT NULL,
 prompt_sha256 TEXT NOT NULL,
 response_id TEXT,
 usage JSONB,
 output JSONB,
 error_message TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 completed_at TIMESTAMPTZ,
 FOREIGN KEY(run_id,project_id) REFERENCES tb_deep_runs(run_id,project_id),
 UNIQUE(run_id,stage)
);
CREATE INDEX idx_tb_deep_attempts ON tb_deep_attempts(run_id,created_at);

CREATE TABLE tb_deep_reviews (
 run_id UUID NOT NULL,
 project_id TEXT NOT NULL,
 review_no SMALLINT NOT NULL CHECK(review_no IN (1,2)),
 approved_nodes JSONB NOT NULL CHECK(jsonb_typeof(approved_nodes)='array'),
 reviewed_by TEXT NOT NULL DEFAULT 'local_owner',
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(run_id,review_no),
 FOREIGN KEY(run_id,project_id) REFERENCES tb_deep_runs(run_id,project_id)
);
CREATE TABLE tb_deep_publications (
 run_id UUID PRIMARY KEY,
 project_id TEXT NOT NULL,
 builder_publication_id UUID NOT NULL UNIQUE REFERENCES tb_taxonomy_publications(id),
 tree JSONB NOT NULL CHECK(jsonb_typeof(tree)='array'),
 quality_report JSONB NOT NULL CHECK(jsonb_typeof(quality_report)='object'),
 published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(run_id,project_id) REFERENCES tb_deep_runs(run_id,project_id)
);
-- Reviews/tree publication are immutable. A revision always needs a new run.
CREATE TRIGGER tb_deep_reviews_immutable BEFORE UPDATE OR DELETE ON tb_deep_reviews
 FOR EACH ROW EXECUTE FUNCTION tb_block_review_mutations();
CREATE TRIGGER tb_deep_publication_immutable BEFORE UPDATE OR DELETE ON tb_deep_publications
 FOR EACH ROW EXECUTE FUNCTION tb_block_review_mutations();
