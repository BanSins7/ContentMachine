-- RTB FIX-06-B: deterministic signed Search Readiness and Human Review2 gate.
-- Only RTB-owned tables/functions; no edits to B9, A8, SRE or legacy Deep V1.
CREATE TABLE tb_deep_v2_readiness_reports (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  actor TEXT NOT NULL CHECK(length(btrim(actor)) BETWEEN 1 AND 160),
  candidate_stage TEXT NOT NULL CHECK(candidate_stage IN(
    'deep_second','deep_expand_1','deep_expand_2')),
  candidate_sha256 TEXT NOT NULL CHECK(candidate_sha256 ~ '^[0-9a-f]{64}$'),
  approved_sha256 TEXT NOT NULL CHECK(approved_sha256 ~ '^[0-9a-f]{64}$'),
  research_input_sha256 TEXT NOT NULL CHECK(research_input_sha256 ~ '^[0-9a-f]{64}$'),
  report_sha256 TEXT NOT NULL CHECK(report_sha256 ~ '^[0-9a-f]{64}$'),
  report JSONB NOT NULL CHECK(jsonb_typeof(report)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,run_id),
  UNIQUE(project_id,run_id,candidate_sha256,approved_sha256),
  FOREIGN KEY(project_id,run_id) REFERENCES tb_deep_v2_runs(project_id,id),
  FOREIGN KEY(project_id,actor)
    REFERENCES knowledge_fact_review_authorizations(project_id,reviewer_id),
  CHECK(report->>'schema_version'='rtb-search-readiness/v1'),
  CHECK(report->>'report_sha256'=report_sha256),
  CHECK(report->>'tree_sha256'=approved_sha256),
  CHECK(report->>'research_input_sha256'=research_input_sha256),
  CHECK(report->>'candidate_stage'='human_review2'),
  CHECK(report->>'passed'='true'),
  CHECK(report->'blocking_errors'='[]'::jsonb)
);
CREATE FUNCTION tb_deep_v2_readiness_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE saved_status TEXT;
BEGIN
 SELECT status INTO saved_status FROM tb_deep_v2_runs
 WHERE project_id=NEW.project_id AND id=NEW.run_id FOR UPDATE;
 IF NOT FOUND OR saved_status<>'review2'
    OR NOT EXISTS (
      SELECT 1 FROM knowledge_fact_review_authorizations a
       WHERE a.project_id=NEW.project_id AND a.reviewer_id=NEW.actor
         AND a.enabled=true)
    OR NOT EXISTS (
      SELECT 1 FROM tb_deep_v2_research_bindings b
      JOIN tb_deep_v2_runs r
        ON r.project_id=b.project_id AND r.binding_id=b.id
      WHERE r.project_id=NEW.project_id AND r.id=NEW.run_id
        AND b.research_input_sha256=NEW.research_input_sha256
    ) THEN
   RAISE EXCEPTION 'RTB Deep V2 readiness lacks authorized source-bound review2';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tb_deep_v2_readiness_insert_guard BEFORE INSERT
 ON tb_deep_v2_readiness_reports FOR EACH ROW
 EXECUTE FUNCTION tb_deep_v2_readiness_guard();
CREATE TRIGGER tb_deep_v2_readiness_immutable BEFORE UPDATE OR DELETE
 ON tb_deep_v2_readiness_reports FOR EACH ROW
 EXECUTE FUNCTION psi_block_immutable_write();

-- Human Review2 must be paired with a persisted, PASSING readiness report
-- in the same transaction and with the very same candidate and approved SHA.
CREATE OR REPLACE FUNCTION tb_deep_v2_review_guard() RETURNS trigger LANGUAGE plpgsql AS $$
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
 IF NEW.review_no=2 AND NOT EXISTS (
   SELECT 1 FROM tb_deep_v2_readiness_reports report
   WHERE report.project_id=NEW.project_id AND report.run_id=NEW.run_id
     AND report.candidate_sha256=NEW.candidate_sha256
     AND report.approved_sha256=NEW.approved_sha256
     AND report.report->>'passed'='true'
 ) THEN
   RAISE EXCEPTION 'RTB Deep V2 second review requires passing signed readiness report';
 END IF;
 RETURN NEW;
END $$;
