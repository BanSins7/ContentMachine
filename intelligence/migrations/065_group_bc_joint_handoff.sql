-- Group B + Group C certified fan-in. PostgreSQL 17, additive migration.
-- Ordered after 055-059 Group B and 056-064 Group C migrations; main may
-- already carry other 05x/06x migrations. Filename, not numeric prefix, is
-- the immutable checksum key in intelligence.migrate.
-- This is NOT a replacement for the independent B and C outboxes/consumers.

CREATE TABLE group_bc_joint_receipts (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 video_id BIGINT NOT NULL REFERENCES videos(id),
 source_id TEXT NOT NULL REFERENCES sources(id),
 qdp_run_id UUID NOT NULL,
 b_run_id UUID NOT NULL,
 c_run_id UUID NOT NULL,
 b_analysis_id BIGINT NOT NULL REFERENCES content_b_analyses(id),
 c_metric_id BIGINT NOT NULL REFERENCES calculated_metrics(id),
 source_policy_version_id BIGINT NOT NULL REFERENCES source_policy_versions(id),
 b_preparation_sha256 TEXT NOT NULL CHECK (b_preparation_sha256 ~ '^[0-9a-f]{64}$'),
 c_preparation_sha256 TEXT NOT NULL CHECK (c_preparation_sha256 ~ '^[0-9a-f]{64}$'),
 b_handoff_sha256 TEXT NOT NULL CHECK (b_handoff_sha256 ~ '^[0-9a-f]{64}$'),
 c_evidence_sha256 TEXT NOT NULL CHECK (c_evidence_sha256 ~ '^[0-9a-f]{64}$'),
 payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
 payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'
       AND payload->>'schema_version'='group-bc-qualified-join/v1'),
 state TEXT NOT NULL DEFAULT 'queued' CHECK (state IN ('queued','delivered')),
 delivered_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,id),
 UNIQUE(project_id,b_run_id,c_run_id),
 FOREIGN KEY(project_id,b_run_id) REFERENCES group_b_runs(project_id,id),
 FOREIGN KEY(project_id,c_run_id) REFERENCES group_c_runs(project_id,id),
 CHECK ((state='delivered') = (delivered_at IS NOT NULL))
);
CREATE INDEX idx_group_bc_pending
 ON group_bc_joint_receipts(project_id,created_at,id) WHERE state='queued';
CREATE INDEX idx_group_bc_project_video
 ON group_bc_joint_receipts(project_id,video_id,created_at DESC);

-- A direct SQL insert cannot turn an unrelated/old pair into a certified
-- joint event. Locks make pair state and lineage stable for this transaction.
-- Cryptographic B/C currentness is checked again in GroupBCPipeline.
CREATE FUNCTION group_bc_pair_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS (
  SELECT 1 FROM group_b_runs br
   JOIN group_b_inbox bi
    ON bi.project_id=br.project_id AND bi.id=br.inbox_id
   JOIN group_b_inbox_receipts bir
    ON bir.project_id=br.project_id AND bir.run_id=br.id
       AND bir.inbox_id=bi.id
   JOIN group_c_runs cr
    ON cr.project_id=br.project_id AND cr.video_id=br.video_id
       AND cr.source_id=br.source_id
       AND cr.source_policy_version_id=br.source_policy_version_id
   JOIN group_c_inbox ci
    ON ci.project_id=cr.project_id AND ci.event_id=cr.inbox_event_id
       AND ci.video_id=cr.video_id
       AND ci.input_sha256=cr.qdp_input_sha256
       AND ci.qdp_run_id=bi.qdp_run_id
   JOIN group_c_calculation_receipts ccr
    ON ccr.project_id=cr.project_id AND ccr.video_id=cr.video_id
       AND ccr.calculated_metric_id=cr.calculated_metric_id
   WHERE br.project_id=NEW.project_id
     AND br.id=NEW.b_run_id AND cr.id=NEW.c_run_id
     AND br.video_id=NEW.video_id AND br.source_id=NEW.source_id
     AND bi.video_id=NEW.video_id AND bi.source_id=NEW.source_id
     AND br.state='completed' AND cr.state='completed'
     AND bi.status='accepted' AND ci.state='accepted'
     AND bi.qdp_run_id=NEW.qdp_run_id
     AND br.result_analysis_id=NEW.b_analysis_id
     AND cr.calculated_metric_id=NEW.c_metric_id
     AND br.source_policy_version_id=NEW.source_policy_version_id
     AND bi.preparation_sha256=NEW.b_preparation_sha256
     AND ci.input_sha256=NEW.c_preparation_sha256
   FOR KEY SHARE OF br,bi,cr,ci
 ) THEN
  RAISE EXCEPTION 'group_bc_exact_lineage_mismatch'
    USING ERRCODE='23514';
 END IF;
 -- Immutable digest covers exact identity and the two certified pointers.
 -- Duplicate or corrected payloads for the same pair are rejected by service.
 IF NEW.payload->>'project_id' IS DISTINCT FROM NEW.project_id
   OR NEW.payload->>'source_id' IS DISTINCT FROM NEW.source_id
   OR NEW.payload->>'qdp_run_id' IS DISTINCT FROM NEW.qdp_run_id::text
   OR NEW.payload->>'video_id' IS DISTINCT FROM NEW.video_id::text
   OR NEW.payload->>'source_policy_version_id'
          IS DISTINCT FROM NEW.source_policy_version_id::text
   OR NEW.payload#>>'{group_b,run_id}' IS DISTINCT FROM NEW.b_run_id::text
   OR NEW.payload#>>'{group_c,run_id}' IS DISTINCT FROM NEW.c_run_id::text
   OR NEW.payload#>>'{group_b,analysis_id}' IS DISTINCT FROM NEW.b_analysis_id::text
   OR NEW.payload#>>'{group_c,calculated_metric_id}' IS DISTINCT FROM NEW.c_metric_id::text
   OR NEW.payload#>>'{group_b,handoff_sha256}' IS DISTINCT FROM NEW.b_handoff_sha256
   OR NEW.payload#>>'{group_c,evidence_sha256}' IS DISTINCT FROM NEW.c_evidence_sha256
   OR NEW.payload#>>'{group_b,preparation_sha256}' IS DISTINCT FROM NEW.b_preparation_sha256
   OR NEW.payload#>>'{group_c,preparation_sha256}' IS DISTINCT FROM NEW.c_preparation_sha256
 THEN
  RAISE EXCEPTION 'group_bc_payload_lineage_mismatch'
    USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END
$$;
CREATE TRIGGER trg_group_bc_guard
 BEFORE INSERT ON group_bc_joint_receipts
 FOR EACH ROW EXECUTE FUNCTION group_bc_pair_guard();

-- The body, IDs, source history and creation time NEVER change.
-- Delivery is a one-way transition, and only after same-transaction ACK.
CREATE FUNCTION group_bc_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
   RAISE EXCEPTION 'group_bc_append_only' USING ERRCODE='23514';
 END IF;
 IF (to_jsonb(NEW) - 'state' - 'delivered_at') IS DISTINCT FROM
    (to_jsonb(OLD) - 'state' - 'delivered_at')
    OR OLD.state <> 'queued' OR NEW.state <> 'delivered'
    OR NEW.delivered_at IS NULL THEN
   RAISE EXCEPTION 'group_bc_immutable_receipt' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END
$$;
CREATE TRIGGER trg_group_bc_immutable
 BEFORE UPDATE OR DELETE ON group_bc_joint_receipts
 FOR EACH ROW EXECUTE FUNCTION group_bc_immutable();
