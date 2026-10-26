-- B Stage13 / B9: immutable approved KnowledgeSnapshot with A source/video pins.
-- Does NOT grant paid C execution, change old Taxonomy Builder modes or auto-publish.
CREATE TABLE knowledge_approved_snapshots (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id),
  session_id UUID NOT NULL,
  submission_id UUID NOT NULL,
  gate_id UUID NOT NULL,
  context_id UUID NOT NULL,
  run_id UUID NOT NULL,
  schema_version TEXT NOT NULL DEFAULT 'knowledge-snapshot/v1'
    CHECK (schema_version='knowledge-snapshot/v1'),
  builder_version TEXT NOT NULL DEFAULT 'b9-immutable/1.0.0'
    CHECK (builder_version='b9-immutable/1.0.0'),
  submission_sha256 TEXT NOT NULL CHECK (submission_sha256 ~ '^[0-9a-f]{64}$'),
  context_sha256 TEXT NOT NULL CHECK (context_sha256 ~ '^[0-9a-f]{64}$'),
  snapshot_sha256 TEXT NOT NULL CHECK (snapshot_sha256 ~ '^[0-9a-f]{64}$'),
  approved_count INTEGER NOT NULL CHECK (approved_count BETWEEN 1 AND 20480),
  video_ref_count INTEGER NOT NULL CHECK (video_ref_count BETWEEN 0 AND 1024),
  snapshot JSONB NOT NULL CHECK (jsonb_typeof(snapshot)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,session_id),
  UNIQUE(project_id,submission_id),
  UNIQUE(project_id,run_id),
  UNIQUE(project_id,id,snapshot_sha256),
  FOREIGN KEY(project_id,session_id)
    REFERENCES knowledge_fact_review_sessions(project_id,id),
  FOREIGN KEY(project_id,submission_id)
    REFERENCES knowledge_fact_review_submissions(project_id,id),
  FOREIGN KEY(project_id,gate_id)
    REFERENCES knowledge_fact_quality_gates(project_id,id),
  FOREIGN KEY(project_id,context_id)
    REFERENCES project_context_snapshots(project_id,id),
  CHECK (snapshot->>'schema_version'='knowledge-snapshot/v1'),
  CHECK (snapshot->>'builder_version'=builder_version),
  CHECK (snapshot->>'project_id'=project_id),
  CHECK (snapshot->>'session_id'=session_id::text),
  CHECK (snapshot->>'submission_id'=submission_id::text),
  CHECK (snapshot->>'context_id'=context_id::text),
  CHECK (snapshot->>'run_id'=run_id::text),
  CHECK ((snapshot->>'approved_count')::integer=approved_count),
  CHECK (jsonb_array_length(snapshot->'video_analysis_refs')=video_ref_count)
);
CREATE INDEX ke_snapshots_by_project ON knowledge_approved_snapshots(project_id,created_at DESC);

-- Persist optional immutable C payload preparation WITHOUT starting a C run.
CREATE TABLE knowledge_c_handoffs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  snapshot_id UUID NOT NULL,
  snapshot_sha256 TEXT NOT NULL CHECK(snapshot_sha256 ~ '^[0-9a-f]{64}$'),
  handoff_sha256 TEXT NOT NULL CHECK(handoff_sha256 ~ '^[0-9a-f]{64}$'),
  handoff JSONB NOT NULL CHECK(jsonb_typeof(handoff)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,snapshot_id),
  FOREIGN KEY(project_id,snapshot_id,snapshot_sha256)
    REFERENCES knowledge_approved_snapshots(project_id,id,snapshot_sha256),
  CHECK(handoff->>'input_mode'='knowledge_snapshot_v1'),
  CHECK(handoff->>'snapshot_id'=snapshot_id::text),
  CHECK(handoff->>'snapshot_sha256'=snapshot_sha256),
  CHECK(handoff->>'execution_status'='not_started')
);
CREATE TRIGGER ke_knowledge_snapshot_immutable BEFORE UPDATE OR DELETE
  ON knowledge_approved_snapshots FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_c_handoff_immutable BEFORE UPDATE OR DELETE
  ON knowledge_c_handoffs FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
