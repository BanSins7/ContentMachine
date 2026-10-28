-- SP-Collector durable bridge, applied after SP060 and Collector054-057.
-- Preserve legacy Collector QV-manifest FK semantics and V1 migration history.
ALTER TABLE collector_query_sets
  ALTER COLUMN publication_id DROP NOT NULL,
  ADD COLUMN rtb_v2_publication_id UUID;
ALTER TABLE collector_query_sets
  ADD CONSTRAINT collector_source_exactly_one CHECK (
    (publication_id IS NOT NULL AND rtb_v2_publication_id IS NULL) OR
    (publication_id IS NULL AND rtb_v2_publication_id IS NOT NULL)
  );
ALTER TABLE collector_query_sets
  ADD CONSTRAINT collector_v2_publication_fk
  FOREIGN KEY (project_id,rtb_v2_publication_id)
  REFERENCES tb_deep_v2_publications(project_id,id);

CREATE TABLE collector_sp_intake_receipts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id),
  sp_event_id UUID NOT NULL,
  chunk_index INTEGER NOT NULL CHECK (chunk_index BETWEEN 0 AND 479),
  idempotency_key TEXT NOT NULL CHECK (length(idempotency_key) BETWEEN 12 AND 128),
  chunk_sha256 TEXT NOT NULL CHECK (chunk_sha256 ~ '^[0-9a-f]{64}$'),
  sp_query_set_sha256 TEXT NOT NULL CHECK (sp_query_set_sha256 ~ '^[0-9a-f]{64}$'),
  collector_query_set_id UUID NOT NULL,
  receipt JSONB NOT NULL CHECK (jsonb_typeof(receipt)='object'),
  receipt_sha256 TEXT NOT NULL CHECK (receipt_sha256 ~ '^[0-9a-f]{64}$'),
  accepted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  FOREIGN KEY (sp_event_id,project_id) REFERENCES sp_handoff_events(id,project_id),
  FOREIGN KEY (sp_event_id,chunk_index) REFERENCES sp_handoff_chunks(event_id,chunk_index),
  FOREIGN KEY (collector_query_set_id,project_id)
    REFERENCES collector_query_sets(id,project_id),
  UNIQUE (project_id,idempotency_key),
  UNIQUE (sp_event_id,chunk_index),
  UNIQUE (collector_query_set_id)
);
CREATE INDEX collector_sp_receipts_project ON collector_sp_intake_receipts(project_id,accepted_at DESC);
CREATE TRIGGER collector_sp_intake_immutable
  BEFORE UPDATE OR DELETE ON collector_sp_intake_receipts
  FOR EACH ROW EXECUTE FUNCTION collector_immutable_record();
