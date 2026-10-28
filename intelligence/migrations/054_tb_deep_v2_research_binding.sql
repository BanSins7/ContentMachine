-- RTB-FIX-02-B: read-only provenance freeze for an independently payable Deep V2.
-- No quote, consent, paid execution, legacy tb_runs or B Stage14 flat run
-- is created by this migration.
CREATE TABLE tb_deep_v2_research_bindings (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id),
  knowledge_snapshot_id UUID NOT NULL,
  knowledge_snapshot_sha256 TEXT NOT NULL
    CHECK (knowledge_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
  handoff_id UUID NOT NULL,
  handoff_sha256 TEXT NOT NULL
    CHECK (handoff_sha256 ~ '^[0-9a-f]{64}$'),
  context_id UUID NOT NULL,
  context_sha256 TEXT NOT NULL
    CHECK (context_sha256 ~ '^[0-9a-f]{64}$'),
  research_input_sha256 TEXT NOT NULL
    CHECK (research_input_sha256 ~ '^[0-9a-f]{64}$'),
  research_input JSONB NOT NULL CHECK (jsonb_typeof(research_input)='object'),
  created_by TEXT NOT NULL CHECK(length(btrim(created_by)) BETWEEN 1 AND 160),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,knowledge_snapshot_id),
  UNIQUE(project_id,id,research_input_sha256),
  FOREIGN KEY(project_id,knowledge_snapshot_id,knowledge_snapshot_sha256)
    REFERENCES knowledge_approved_snapshots(project_id,id,snapshot_sha256),
  FOREIGN KEY(project_id,handoff_id)
    REFERENCES knowledge_c_handoffs(project_id,id),
  FOREIGN KEY(project_id,context_id)
    REFERENCES project_context_snapshots(project_id,id),
  FOREIGN KEY(project_id,created_by)
    REFERENCES knowledge_fact_review_authorizations(project_id,reviewer_id),
  CHECK(research_input->>'schema_version'='rtb-research-input/v2'),
  CHECK(research_input->>'project_id'=project_id),
  CHECK(research_input->>'input_mode'='knowledge_snapshot_v1'),
  CHECK(research_input->>'b9_snapshot_id'=knowledge_snapshot_id::text),
  CHECK(research_input->>'b9_snapshot_sha256'=knowledge_snapshot_sha256),
  CHECK(research_input->>'b9_handoff_id'=handoff_id::text),
  CHECK(research_input->>'b9_handoff_sha256'=handoff_sha256),
  CHECK(research_input->>'a_context_id'=context_id::text),
  CHECK(research_input->>'a_context_sha256'=context_sha256),
  CHECK(research_input->>'research_input_sha256'=research_input_sha256),
  CHECK(research_input->>'paid_execution_authorized'='false'),
  CHECK(research_input->>'provider_calls'='0')
);
CREATE INDEX tb_deep_v2_binding_by_project
  ON tb_deep_v2_research_bindings(project_id,created_at DESC);

-- Ensure the pinned B9 handoff really belongs to this B9 snapshot and the
-- approved A context. Independent FKs to B9 + handoff are not sufficient.
CREATE FUNCTION tb_deep_v2_verify_binding() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM knowledge_approved_snapshots s
    JOIN knowledge_c_handoffs h
      ON h.project_id=s.project_id AND h.snapshot_id=s.id
    JOIN project_context_snapshots a
      ON a.project_id=s.project_id AND a.id=s.context_id
    WHERE s.project_id=NEW.project_id
      AND s.id=NEW.knowledge_snapshot_id
      AND s.snapshot_sha256=NEW.knowledge_snapshot_sha256
      AND s.context_id=NEW.context_id
      AND s.context_sha256=NEW.context_sha256
      AND a.context_sha256=NEW.context_sha256
      AND h.id=NEW.handoff_id
      AND h.handoff_sha256=NEW.handoff_sha256
      AND h.snapshot_sha256=NEW.knowledge_snapshot_sha256
      AND h.handoff->>'snapshot_id'=NEW.knowledge_snapshot_id::text
      AND h.handoff->>'context_sha256'=NEW.context_sha256
  ) THEN
    RAISE EXCEPTION 'RTB Deep V2 research binding source identities do not match';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM knowledge_fact_review_authorizations auth
    WHERE auth.project_id=NEW.project_id
      AND auth.reviewer_id=NEW.created_by
      AND auth.enabled=true
  ) THEN
    RAISE EXCEPTION 'RTB Deep V2 research binding requires authorized reviewer';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tb_deep_v2_binding_source_guard BEFORE INSERT
  ON tb_deep_v2_research_bindings FOR EACH ROW
  EXECUTE FUNCTION tb_deep_v2_verify_binding();

-- Frozen Research Input is append-only. A later C-run receives its exact ID and
-- SHA; any source change requires an entirely new human-approved B9 snapshot.
CREATE TRIGGER tb_deep_v2_binding_immutable BEFORE UPDATE OR DELETE
  ON tb_deep_v2_research_bindings FOR EACH ROW
  EXECUTE FUNCTION psi_block_immutable_write();
