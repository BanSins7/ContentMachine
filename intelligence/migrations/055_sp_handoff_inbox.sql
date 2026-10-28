-- SP-12: standalone, durable QV -> SP inbox and separately attested Collector delivery.
-- Migration is ADDITIVE. Never alter historical 015 SQG / 016 QV.
-- The QV ACK is written by dispatcher in the SAME DB transaction as inbox/chunks.
-- This SQL creates no provider jobs or external source authorization.

CREATE TABLE sp_handoff_events (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 qv_run_id UUID NOT NULL,
 qv_outbox_id UUID NOT NULL UNIQUE REFERENCES qv_outbox(id),
 projection_sha256 TEXT NOT NULL CHECK (projection_sha256 ~ '^[0-9a-f]{64}$'),
 projection JSONB NOT NULL CHECK (
  jsonb_typeof(projection)='object' AND
  projection->>'schema_version'='search-planner-handoff-projection/v1' AND
  projection->>'projection_sha256'=projection_sha256 AND
  projection->>'external_dispatch_authorized'='false' AND
  projection->>'qv_outbox_acknowledged_by_this_projection'='false'
 ),
 selected_count INTEGER NOT NULL CHECK (selected_count BETWEEN 0 AND 480),
 chunk_count INTEGER NOT NULL CHECK (chunk_count BETWEEN 0 AND 480),
 status TEXT NOT NULL CHECK (status IN (
  'pending_planning','awaiting_owner_commit','committed_held',
  'awaiting_collector','delivering','collector_accepted',
  'incompatible','needs_attention','no_work','cancelled'
 )),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(qv_run_id,project_id) REFERENCES qv_runs(id,project_id),
 UNIQUE(project_id,qv_run_id),
 UNIQUE(id,project_id),
 CHECK((selected_count=0 AND chunk_count=0 AND status='no_work') OR
       (selected_count>0 AND chunk_count>0 AND status<>'no_work'))
);
CREATE INDEX sp_handoff_events_project_status
 ON sp_handoff_events(project_id,status,created_at);
CREATE INDEX sp_handoff_events_qv ON sp_handoff_events(qv_run_id);

CREATE TABLE sp_handoff_chunks (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 event_id UUID NOT NULL REFERENCES sp_handoff_events(id),
 chunk_index INTEGER NOT NULL CHECK (chunk_index BETWEEN 0 AND 479),
 chunk_sha256 TEXT NOT NULL CHECK (chunk_sha256 ~ '^[0-9a-f]{64}$'),
 selected_count INTEGER NOT NULL CHECK (selected_count BETWEEN 1 AND 50),
 chunk JSONB NOT NULL CHECK (
  jsonb_typeof(chunk)='object' AND
  chunk->>'schema_version'='search-planner-handoff-chunk/v1' AND
  chunk->>'chunk_sha256'=chunk_sha256
 ),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(event_id,chunk_index), UNIQUE(id,event_id)
);

CREATE TABLE sp_handoff_query_links (
 event_id UUID NOT NULL REFERENCES sp_handoff_events(id),
 chunk_index INTEGER NOT NULL,
 candidate_id UUID NOT NULL,
 qv_hypothesis_id TEXT NOT NULL,
 provenance JSONB NOT NULL CHECK(jsonb_typeof(provenance)='object'),
 provenance_sha256 TEXT NOT NULL CHECK(provenance_sha256 ~ '^[0-9a-f]{64}$'),
 PRIMARY KEY(event_id,candidate_id),
 FOREIGN KEY(event_id,chunk_index)
    REFERENCES sp_handoff_chunks(event_id,chunk_index)
);
CREATE INDEX sp_handoff_links_chunk ON sp_handoff_query_links(event_id,chunk_index);

-- One logical delivery reservation per chunk. Initially 'uncertain' BEFORE
-- an external adapter is invoked. Unknown outcomes must NOT auto retry.
CREATE TABLE sp_delivery_attempts (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 event_id UUID NOT NULL,
 chunk_index INTEGER NOT NULL,
 idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 12 AND 128),
 collector_contract TEXT NOT NULL DEFAULT 'validated_query_set/v1'
   CHECK(collector_contract='validated_query_set/v1'),
 chunk_sha256 TEXT NOT NULL CHECK(chunk_sha256 ~ '^[0-9a-f]{64}$'),
 status TEXT NOT NULL DEFAULT 'uncertain'
   CHECK(status IN ('uncertain','accepted','incompatible','needs_attention')),
 attempted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 resolved_at TIMESTAMPTZ,
 FOREIGN KEY(event_id,chunk_index)
  REFERENCES sp_handoff_chunks(event_id,chunk_index),
 UNIQUE(event_id,chunk_index),
 UNIQUE(idempotency_key)
);
CREATE INDEX sp_delivery_attempts_pending
 ON sp_delivery_attempts(status,attempted_at);

-- Receipt is an externally provided durable object, never an SP inbox ACK.
CREATE TABLE sp_delivery_receipts (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 attempt_id UUID NOT NULL UNIQUE REFERENCES sp_delivery_attempts(id),
 event_id UUID NOT NULL,
 chunk_index INTEGER NOT NULL,
 external_query_set_id TEXT NOT NULL CHECK(length(external_query_set_id) BETWEEN 1 AND 240),
 external_plan_id TEXT CHECK(external_plan_id IS NULL OR length(external_plan_id) BETWEEN 1 AND 240),
 collector_contract TEXT NOT NULL CHECK(collector_contract='validated_query_set/v1'),
 collector_receipt_sha256 TEXT NOT NULL CHECK(collector_receipt_sha256 ~ '^[0-9a-f]{64}$'),
 receipt JSONB NOT NULL CHECK(jsonb_typeof(receipt)='object'),
 accepted_at TIMESTAMPTZ NOT NULL,
 stored_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(event_id,chunk_index)
  REFERENCES sp_handoff_chunks(event_id,chunk_index),
 UNIQUE(event_id,chunk_index)
);

CREATE FUNCTION sp_handoff_reject_event_payload_mutation() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.id, NEW.project_id, NEW.qv_run_id, NEW.qv_outbox_id,
        NEW.projection_sha256, NEW.projection, NEW.selected_count,
        NEW.chunk_count, NEW.created_at) IS DISTINCT FROM
    ROW(OLD.id, OLD.project_id, OLD.qv_run_id, OLD.qv_outbox_id,
        OLD.projection_sha256, OLD.projection, OLD.selected_count,
        OLD.chunk_count, OLD.created_at) THEN
    RAISE EXCEPTION 'SP handoff inbox source is immutable';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER sp_handoff_events_immutable_source
 BEFORE UPDATE ON sp_handoff_events
 FOR EACH ROW EXECUTE FUNCTION sp_handoff_reject_event_payload_mutation();
CREATE TRIGGER sp_handoff_events_no_delete
 BEFORE DELETE ON sp_handoff_events
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();
CREATE TRIGGER sp_handoff_chunks_immutable
 BEFORE UPDATE OR DELETE ON sp_handoff_chunks
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();
CREATE TRIGGER sp_handoff_links_immutable
 BEFORE UPDATE OR DELETE ON sp_handoff_query_links
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();
CREATE TRIGGER sp_delivery_receipts_immutable
 BEFORE UPDATE OR DELETE ON sp_delivery_receipts
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();

CREATE FUNCTION sp_delivery_attempt_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.id, NEW.event_id, NEW.chunk_index, NEW.idempotency_key,
        NEW.collector_contract, NEW.chunk_sha256, NEW.attempted_at) IS DISTINCT FROM
    ROW(OLD.id, OLD.event_id, OLD.chunk_index, OLD.idempotency_key,
        OLD.collector_contract, OLD.chunk_sha256, OLD.attempted_at) OR
    OLD.status <> 'uncertain' OR
    NEW.status NOT IN ('accepted','incompatible','needs_attention') OR
    NEW.resolved_at IS NULL THEN
    RAISE EXCEPTION 'Illegal durable delivery attempt transition';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER sp_delivery_attempts_transition
 BEFORE UPDATE ON sp_delivery_attempts
 FOR EACH ROW EXECUTE FUNCTION sp_delivery_attempt_guard();
CREATE TRIGGER sp_delivery_attempts_no_delete
 BEFORE DELETE ON sp_delivery_attempts
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();
