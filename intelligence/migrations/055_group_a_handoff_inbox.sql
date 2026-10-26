-- GA-MOD-04: append-only conflict quarantine for qdp.preparation_ready/v1.
-- Depends on GA 054 and nothing in a QDP private schema.
-- Same event id with conflicting payload MUST NOT mutate a committed receipt.
CREATE TABLE IF NOT EXISTS ga_inbox_conflicts (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event_id UUID NOT NULL,
  recorded_payload_sha256 TEXT NOT NULL
    CHECK (recorded_payload_sha256 ~ '^[0-9a-f]{64}$'),
  observed_payload_sha256 TEXT NOT NULL
    CHECK (observed_payload_sha256 ~ '^[0-9a-f]{64}$'),
  observed_pointer_sha256 TEXT NOT NULL
    CHECK (observed_pointer_sha256 ~ '^[0-9a-f]{64}$'),
  reason_code TEXT NOT NULL DEFAULT 'event_payload_conflict'
    CHECK (reason_code IN ('event_payload_conflict','event_identity_conflict')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (event_id) REFERENCES ga_inbox(event_id) ON DELETE CASCADE,
  CHECK (recorded_payload_sha256 <> observed_payload_sha256
      OR reason_code='event_identity_conflict'),
  UNIQUE(event_id,observed_payload_sha256,observed_pointer_sha256)
);
CREATE INDEX IF NOT EXISTS idx_ga_inbox_conflicts_event
  ON ga_inbox_conflicts(event_id,created_at DESC);

-- Purely diagnostic view: a receipt proves durable intake, not completed A.
CREATE OR REPLACE VIEW ga_handoff_receipts AS
SELECT i.event_id,i.project_id,i.qdp_run_id,i.video_id,i.input_sha256,
       i.payload_sha256,i.run_id,i.receipt_state,i.created_at AS committed_at,
       r.state AS current_run_state
FROM ga_inbox i
JOIN ga_runs r ON r.id=i.run_id AND r.project_id=i.project_id
 AND r.video_id=i.video_id;

-- Allow future replay detection without modifying immutable inbox.
-- Conflict records are immutable and cannot be overwritten after quarantine.
CREATE OR REPLACE FUNCTION ga_inbox_conflict_guard()
RETURNS trigger LANGUAGE plpgsql AS $ga_conflict$
BEGIN
  IF TG_OP='UPDATE' THEN
    RAISE EXCEPTION 'GA handoff conflict audit is append-only'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END;
$ga_conflict$;
CREATE TRIGGER ga_inbox_conflict_immutable
 BEFORE UPDATE ON ga_inbox_conflicts
 FOR EACH ROW EXECUTE FUNCTION ga_inbox_conflict_guard();
