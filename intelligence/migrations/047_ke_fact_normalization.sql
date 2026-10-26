-- B Stage08: deterministic Python normalization of *completed* Stage07 drafts.
-- These tables do NOT certify independent grounding or human approval.
-- Do not modify historical migrations or legacy TB fact/review tables.

CREATE TABLE knowledge_fact_normalization_batches (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  batch_id UUID NOT NULL,
  schema_version TEXT NOT NULL DEFAULT 'knowledge-normalization/v1'
    CHECK (schema_version = 'knowledge-normalization/v1'),
  normalizer_version TEXT NOT NULL CHECK (normalizer_version = 'b4-python/1.0.0'),
  input_sha256 TEXT NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
  report_sha256 TEXT NOT NULL CHECK (report_sha256 ~ '^[0-9a-f]{64}$'),
  proposal_count INTEGER NOT NULL CHECK (proposal_count BETWEEN 0 AND 20480),
  normalized_count INTEGER NOT NULL CHECK (normalized_count BETWEEN 0 AND 20480),
  quarantined_count INTEGER NOT NULL CHECK (quarantined_count BETWEEN 0 AND 20480),
  report JSONB NOT NULL CHECK (jsonb_typeof(report) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE (project_id,id),
  UNIQUE (project_id,id,run_id,batch_id),
  UNIQUE (project_id,run_id),
  CHECK (proposal_count = normalized_count + quarantined_count),
  CHECK (report->>'schema_version' = schema_version),
  CHECK (report->>'input_sha256' = input_sha256),
  CHECK (report->>'normalizer_version' = normalizer_version),
  FOREIGN KEY (project_id,run_id,batch_id)
    REFERENCES knowledge_fact_execution_runs(project_id,id,batch_id)
);
CREATE INDEX ke_fact_normalization_by_batch ON knowledge_fact_normalization_batches(project_id,batch_id);

CREATE TABLE knowledge_fact_normalized_proposals (
  project_id TEXT NOT NULL,
  normalization_id UUID NOT NULL,
  run_id UUID NOT NULL,
  batch_id UUID NOT NULL,
  pack_index INTEGER NOT NULL CHECK (pack_index BETWEEN 0 AND 255),
  candidate_index INTEGER NOT NULL CHECK (candidate_index BETWEEN 0 AND 79),
  proposal_id TEXT NOT NULL CHECK (proposal_id ~ '^[0-9a-f]{64}$'),
  raw_candidate_sha256 TEXT NOT NULL CHECK (raw_candidate_sha256 ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL CHECK (status IN ('pending_grounding','quarantined')),
  reason_code TEXT,
  normalized JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (project_id,normalization_id,pack_index,candidate_index),
  UNIQUE (project_id,run_id,proposal_id),
  FOREIGN KEY (project_id,normalization_id,run_id,batch_id)
    REFERENCES knowledge_fact_normalization_batches(project_id,id,run_id,batch_id),
  FOREIGN KEY (project_id,run_id,batch_id)
    REFERENCES knowledge_fact_execution_runs(project_id,id,batch_id),
  FOREIGN KEY (project_id,batch_id,pack_index)
    REFERENCES knowledge_fact_request_parts(project_id,batch_id,pack_index),
  CHECK ((status='pending_grounding' AND normalized IS NOT NULL AND reason_code IS NULL)
      OR (status='quarantined' AND normalized IS NULL AND reason_code IS NOT NULL)),
  CHECK (normalized IS NULL OR jsonb_typeof(normalized)='object'),
  CHECK (normalized IS NULL OR normalized->>'proposal_id'=proposal_id),
  CHECK (normalized IS NULL OR normalized->>'grounding_status'='pending_b5'),
  CHECK (normalized IS NULL OR normalized->>'human_review_status'='unreviewed')
);
CREATE INDEX ke_fact_normalized_by_run_status
  ON knowledge_fact_normalized_proposals(project_id,run_id,status,pack_index,candidate_index);

CREATE TRIGGER ke_fact_normalization_batch_immutable
 BEFORE UPDATE OR DELETE ON knowledge_fact_normalization_batches
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_fact_normalized_proposal_immutable
 BEFORE UPDATE OR DELETE ON knowledge_fact_normalized_proposals
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
