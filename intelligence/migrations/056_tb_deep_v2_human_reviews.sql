-- RTB-FIX-04 A: human-reviewed and immutable Deep V2 trees.
-- Separate from Deep V1 tb_deep_reviews; no edits to A8/B9 or legacy runs.
CREATE TABLE tb_deep_v2_reviews (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  review_no SMALLINT NOT NULL CHECK(review_no IN (1,2)),
  actor TEXT NOT NULL CHECK(length(btrim(actor)) BETWEEN 1 AND 160),
  candidate_sha256 TEXT NOT NULL CHECK(candidate_sha256 ~ '^[0-9a-f]{64}$'),
  approved_sha256 TEXT NOT NULL CHECK(approved_sha256 ~ '^[0-9a-f]{64}$'),
  decisions JSONB NOT NULL CHECK(jsonb_typeof(decisions)='array'),
  approved_nodes JSONB NOT NULL CHECK(jsonb_typeof(approved_nodes)='array'),
  reviewed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,run_id,review_no),
  UNIQUE(project_id,id),
  FOREIGN KEY(project_id,run_id) REFERENCES tb_deep_v2_runs(project_id,id),
  FOREIGN KEY(project_id,actor)
    REFERENCES knowledge_fact_review_authorizations(project_id,reviewer_id)
);

CREATE FUNCTION tb_deep_v2_review_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE current_status TEXT;
BEGIN
  SELECT status INTO current_status
  FROM tb_deep_v2_runs
  WHERE project_id=NEW.project_id AND id=NEW.run_id FOR UPDATE;
  IF (NEW.review_no=1 AND current_status<>'review1')
     OR (NEW.review_no=2 AND current_status<>'review2') THEN
    RAISE EXCEPTION 'RTB Deep V2 human review state mismatch';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM knowledge_fact_review_authorizations a
     WHERE a.project_id=NEW.project_id AND a.reviewer_id=NEW.actor
       AND a.enabled=true
  ) THEN
    RAISE EXCEPTION 'RTB Deep V2 human reviewer unauthorized';
  END IF;
  IF NEW.review_no=1 AND NOT EXISTS (
    SELECT 1 FROM tb_deep_v2_attempts a
     WHERE a.project_id=NEW.project_id AND a.run_id=NEW.run_id
       AND a.stage='deep_first' AND a.status='completed'
       AND jsonb_typeof(a.output->'nodes')='array'
  ) THEN
    RAISE EXCEPTION 'RTB Deep V2 first review requires completed Pass1';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tb_deep_v2_review_insert_guard BEFORE INSERT
  ON tb_deep_v2_reviews FOR EACH ROW
  EXECUTE FUNCTION tb_deep_v2_review_guard();
CREATE TRIGGER tb_deep_v2_review_immutable BEFORE UPDATE OR DELETE
  ON tb_deep_v2_reviews FOR EACH ROW
  EXECUTE FUNCTION psi_block_immutable_write();

-- Read-only H1 is the sole source for second pass. No review update in place,
-- no new paid request from INSERT triggers and no fake legacy Fact Review rows.
