-- Content Machine B Stage11: deterministic, immutable readiness assessment.
-- Handoff is for Human Review ONLY; never a fact approval or publishing gate.
CREATE TABLE knowledge_fact_quality_gates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  dedup_id UUID NOT NULL,
  grounding_id UUID NOT NULL,
  normalization_id UUID NOT NULL,
  run_id UUID NOT NULL,
  batch_id UUID NOT NULL,
  schema_version TEXT NOT NULL DEFAULT 'knowledge-quality-gate/v1'
    CHECK(schema_version='knowledge-quality-gate/v1'),
  gate_version TEXT NOT NULL DEFAULT 'b7-deterministic/1.0.0'
    CHECK(gate_version='b7-deterministic/1.0.0'),
  decision TEXT NOT NULL CHECK(decision IN
    ('blocked','ready_with_warnings','ready_for_human_review')),
  handoff_allowed BOOLEAN NOT NULL,
  input_sha256 TEXT NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
  report_sha256 TEXT NOT NULL CHECK(report_sha256 ~ '^[0-9a-f]{64}$'),
  finding_count INTEGER NOT NULL CHECK(finding_count BETWEEN 0 AND 256),
  blocker_count INTEGER NOT NULL CHECK(blocker_count BETWEEN 0 AND 256),
  warning_count INTEGER NOT NULL CHECK(warning_count BETWEEN 0 AND 256),
  report JSONB NOT NULL CHECK(jsonb_typeof(report)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,dedup_id),
  UNIQUE(project_id,run_id),
  UNIQUE(project_id,id,dedup_id,grounding_id,normalization_id,run_id,batch_id),
  FOREIGN KEY(project_id,dedup_id,grounding_id,normalization_id,run_id,batch_id)
    REFERENCES knowledge_fact_dedup_batches(
      project_id,id,grounding_id,normalization_id,run_id,batch_id),
  CHECK(handoff_allowed = (decision <> 'blocked')),
  CHECK(blocker_count = 0 OR decision = 'blocked'),
  CHECK(warning_count = 0 OR decision <> 'ready_for_human_review'),
  CHECK(finding_count >= blocker_count + warning_count),
  CHECK(report->>'schema_version' = schema_version),
  CHECK(report->>'gate_version' = gate_version),
  CHECK(report->>'decision' = decision),
  CHECK(report->>'input_sha256' = input_sha256),
  CHECK((report->>'handoff_allowed')::boolean = handoff_allowed)
);
CREATE INDEX ke_quality_gate_project_run ON knowledge_fact_quality_gates(project_id,run_id);
CREATE TRIGGER ke_quality_gates_immutable BEFORE UPDATE OR DELETE ON knowledge_fact_quality_gates
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
