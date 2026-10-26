-- Stage10 B6: immutable exact-group memberships and *candidate* contradictions.
-- A B5 literal quotation match is NOT a statement of objective truth.
-- Never auto-resolve conflicting claims or auto-approve any knowledge.

CREATE TABLE knowledge_fact_dedup_batches (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  grounding_id UUID NOT NULL,
  normalization_id UUID NOT NULL,
  run_id UUID NOT NULL,
  batch_id UUID NOT NULL,
  schema_version TEXT NOT NULL DEFAULT 'knowledge-dedup/v1'
    CHECK(schema_version='knowledge-dedup/v1'),
  engine_version TEXT NOT NULL DEFAULT 'b6-deterministic/1.0.0'
    CHECK(engine_version='b6-deterministic/1.0.0'),
  input_sha256 TEXT NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
  report_sha256 TEXT NOT NULL CHECK(report_sha256 ~ '^[0-9a-f]{64}$'),
  proposal_count INTEGER NOT NULL CHECK(proposal_count BETWEEN 0 AND 20480),
  eligible_count INTEGER NOT NULL CHECK(eligible_count BETWEEN 0 AND 20480),
  blocked_count INTEGER NOT NULL CHECK(blocked_count BETWEEN 0 AND 20480),
  group_count INTEGER NOT NULL CHECK(group_count BETWEEN 0 AND 20480),
  duplicate_count INTEGER NOT NULL CHECK(duplicate_count BETWEEN 0 AND 20480),
  conflict_candidate_count INTEGER NOT NULL CHECK(conflict_candidate_count BETWEEN 0 AND 50000),
  report JSONB NOT NULL CHECK(jsonb_typeof(report)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,id,grounding_id,normalization_id,run_id,batch_id),
  UNIQUE(project_id,grounding_id),
  FOREIGN KEY(project_id,grounding_id,normalization_id,run_id,batch_id)
    REFERENCES knowledge_fact_grounding_batches(project_id,id,normalization_id,run_id,batch_id),
  CHECK(proposal_count=eligible_count+blocked_count),
  CHECK(eligible_count=group_count+duplicate_count),
  CHECK(report->>'schema_version'=schema_version),
  CHECK(report->>'engine_version'=engine_version),
  CHECK(report->>'input_sha256'=input_sha256)
);
CREATE INDEX ke_dedup_by_run ON knowledge_fact_dedup_batches(project_id,run_id);

CREATE TABLE knowledge_fact_exact_groups (
  project_id TEXT NOT NULL,
  dedup_id UUID NOT NULL,
  group_id TEXT NOT NULL CHECK(group_id ~ '^[0-9a-f]{64}$'),
  group_key_sha256 TEXT NOT NULL CHECK(group_key_sha256 ~ '^[0-9a-f]{64}$'),
  representative_id TEXT NOT NULL CHECK(representative_id ~ '^[0-9a-f]{64}$'),
  fact_kind TEXT NOT NULL,
  source_role TEXT NOT NULL,
  subject_scope TEXT NOT NULL,
  subject_id TEXT,
  original_text TEXT NOT NULL CHECK(length(original_text) BETWEEN 1 AND 800),
  member_count INTEGER NOT NULL CHECK(member_count BETWEEN 1 AND 20480),
  review_status TEXT NOT NULL DEFAULT 'unreviewed' CHECK(review_status='unreviewed'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,dedup_id,group_id),
  UNIQUE(project_id,dedup_id,group_key_sha256),
  FOREIGN KEY(project_id,dedup_id) REFERENCES knowledge_fact_dedup_batches(project_id,id)
);

CREATE TABLE knowledge_fact_dedup_members (
  project_id TEXT NOT NULL,
  dedup_id UUID NOT NULL,
  grounding_id UUID NOT NULL,
  normalization_id UUID NOT NULL,
  run_id UUID NOT NULL,
  batch_id UUID NOT NULL,
  pack_index INTEGER NOT NULL CHECK(pack_index BETWEEN 0 AND 255),
  candidate_index INTEGER NOT NULL CHECK(candidate_index BETWEEN 0 AND 79),
  proposal_id TEXT NOT NULL CHECK(proposal_id ~ '^[0-9a-f]{64}$'),
  group_id TEXT,
  status TEXT NOT NULL CHECK(status IN ('grouped','blocked_b5','skipped_b4')),
  reason_code TEXT,
  b5_receipt_sha256 TEXT NOT NULL CHECK(b5_receipt_sha256 ~ '^[0-9a-f]{64}$'),
  provenance JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,dedup_id,pack_index,candidate_index),
  UNIQUE(project_id,dedup_id,proposal_id),
  FOREIGN KEY(project_id,dedup_id,grounding_id,normalization_id,run_id,batch_id)
    REFERENCES knowledge_fact_dedup_batches(project_id,id,grounding_id,normalization_id,run_id,batch_id),
  FOREIGN KEY(project_id,grounding_id,pack_index,candidate_index)
    REFERENCES knowledge_fact_grounding_receipts(project_id,grounding_id,pack_index,candidate_index),
  FOREIGN KEY(project_id,normalization_id,pack_index,candidate_index)
    REFERENCES knowledge_fact_normalized_proposals(project_id,normalization_id,pack_index,candidate_index),
  FOREIGN KEY(project_id,dedup_id,group_id)
    REFERENCES knowledge_fact_exact_groups(project_id,dedup_id,group_id),
  CHECK(
    (status='grouped' AND group_id IS NOT NULL AND reason_code IS NULL
        AND provenance IS NOT NULL AND jsonb_typeof(provenance)='object')
    OR
    (status<>'grouped' AND group_id IS NULL AND reason_code IS NOT NULL AND provenance IS NULL)
  )
);
CREATE INDEX ke_dedup_members_by_group ON knowledge_fact_dedup_members(project_id,dedup_id,group_id);

CREATE TABLE knowledge_fact_possible_conflicts (
  project_id TEXT NOT NULL,
  dedup_id UUID NOT NULL,
  conflict_id TEXT NOT NULL CHECK(conflict_id ~ '^[0-9a-f]{64}$'),
  group_a TEXT NOT NULL,
  group_b TEXT NOT NULL,
  rule TEXT NOT NULL CHECK(rule IN ('opposite_negation','different_number_same_phrase')),
  review_state TEXT NOT NULL DEFAULT 'needs_review' CHECK(review_state='needs_review'),
  reason TEXT NOT NULL CHECK(length(reason) BETWEEN 5 AND 250),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,dedup_id,conflict_id),
  UNIQUE(project_id,dedup_id,group_a,group_b,rule),
  FOREIGN KEY(project_id,dedup_id,group_a)
    REFERENCES knowledge_fact_exact_groups(project_id,dedup_id,group_id),
  FOREIGN KEY(project_id,dedup_id,group_b)
    REFERENCES knowledge_fact_exact_groups(project_id,dedup_id,group_id),
  CHECK(group_a < group_b)
);
CREATE INDEX ke_possible_conflicts_by_group ON knowledge_fact_possible_conflicts(project_id,dedup_id,group_a,group_b);

CREATE TRIGGER ke_dedup_batches_immutable BEFORE UPDATE OR DELETE ON knowledge_fact_dedup_batches
  FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_dedup_groups_immutable BEFORE UPDATE OR DELETE ON knowledge_fact_exact_groups
  FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_dedup_members_immutable BEFORE UPDATE OR DELETE ON knowledge_fact_dedup_members
  FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_possible_conflicts_immutable BEFORE UPDATE OR DELETE ON knowledge_fact_possible_conflicts
  FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
