-- B Stage09: independent quotation grounding against pinned A artifacts.
-- Literal source text match is NOT semantic entailment or independent website truth.
-- Append-only; no edits to PSI migrations, TB review or prior B stages.

CREATE TABLE knowledge_fact_grounding_batches (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  normalization_id UUID NOT NULL,
  run_id UUID NOT NULL,
  batch_id UUID NOT NULL,
  context_id UUID NOT NULL,
  schema_version TEXT NOT NULL DEFAULT 'knowledge-grounding/v1'
    CHECK (schema_version='knowledge-grounding/v1'),
  grounding_version TEXT NOT NULL DEFAULT 'b5-independent-a/1.0.0'
    CHECK (grounding_version='b5-independent-a/1.0.0'),
  input_sha256 TEXT NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
  report_sha256 TEXT NOT NULL CHECK(report_sha256 ~ '^[0-9a-f]{64}$'),
  total_count INTEGER NOT NULL CHECK(total_count BETWEEN 0 AND 20480),
  matched_count INTEGER NOT NULL CHECK(matched_count BETWEEN 0 AND 20480),
  rejected_count INTEGER NOT NULL CHECK(rejected_count BETWEEN 0 AND 20480),
  skipped_count INTEGER NOT NULL CHECK(skipped_count BETWEEN 0 AND 20480),
  report JSONB NOT NULL CHECK(jsonb_typeof(report)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,id,normalization_id,run_id,batch_id),
  UNIQUE(project_id,normalization_id),
  FOREIGN KEY(project_id,normalization_id,run_id,batch_id)
    REFERENCES knowledge_fact_normalization_batches(project_id,id,run_id,batch_id),
  FOREIGN KEY(project_id,context_id)
    REFERENCES project_context_snapshots(project_id,id),
  CHECK(total_count=matched_count+rejected_count+skipped_count),
  CHECK(report->>'schema_version'=schema_version),
  CHECK(report->>'input_sha256'=input_sha256),
  CHECK(report->>'grounding_version'=grounding_version)
);
CREATE INDEX ke_grounding_batches_by_run ON knowledge_fact_grounding_batches(project_id,run_id);

CREATE TABLE knowledge_fact_grounding_receipts (
  project_id TEXT NOT NULL,
  grounding_id UUID NOT NULL,
  normalization_id UUID NOT NULL,
  run_id UUID NOT NULL,
  batch_id UUID NOT NULL,
  pack_index INTEGER NOT NULL CHECK(pack_index BETWEEN 0 AND 255),
  candidate_index INTEGER NOT NULL CHECK(candidate_index BETWEEN 0 AND 79),
  proposal_id TEXT NOT NULL CHECK(proposal_id ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL CHECK(status IN ('source_quote_matched','rejected','skipped_b4_quarantine')),
  reason_code TEXT,
  receipt_sha256 TEXT NOT NULL CHECK(receipt_sha256 ~ '^[0-9a-f]{64}$'),
  receipt JSONB NOT NULL CHECK(jsonb_typeof(receipt)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,grounding_id,pack_index,candidate_index),
  UNIQUE(project_id,grounding_id,proposal_id),
  FOREIGN KEY(project_id,grounding_id,normalization_id,run_id,batch_id)
    REFERENCES knowledge_fact_grounding_batches(project_id,id,normalization_id,run_id,batch_id),
  FOREIGN KEY(project_id,normalization_id,pack_index,candidate_index)
    REFERENCES knowledge_fact_normalized_proposals(project_id,normalization_id,pack_index,candidate_index),
  CHECK(receipt->>'proposal_id'=proposal_id),
  CHECK(receipt->>'status'=status),
  CHECK((status='source_quote_matched' AND reason_code IS NULL)
        OR (status<>'source_quote_matched' AND reason_code IS NOT NULL))
);
CREATE INDEX ke_grounding_receipts_by_status
  ON knowledge_fact_grounding_receipts(project_id,run_id,status,pack_index,candidate_index);
CREATE TRIGGER ke_grounding_batch_immutable BEFORE UPDATE OR DELETE ON knowledge_fact_grounding_batches
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_grounding_receipt_immutable BEFORE UPDATE OR DELETE ON knowledge_fact_grounding_receipts
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
