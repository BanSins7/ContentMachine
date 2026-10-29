-- FIX-05: payload-free forensic audit of SRE event ID/hash collisions.
-- Additive forward-only migration; do not modify applied 040 or SRE-owned SQL.
-- This table does NOT accept or acknowledge events and stores NO raw payload.
CREATE TABLE qdp_sre_inbox_quarantine (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 event_id UUID NOT NULL,
 project_id TEXT,
 observed_sha256 TEXT NOT NULL CHECK (observed_sha256 ~ '^[0-9a-f]{64}$'),
 claimed_sha256 TEXT CHECK
   (claimed_sha256 IS NULL OR claimed_sha256 ~ '^[0-9a-f]{64}$'),
 previous_sha256 TEXT CHECK
   (previous_sha256 IS NULL OR previous_sha256 ~ '^[0-9a-f]{64}$'),
 reason_code TEXT NOT NULL CHECK(length(reason_code) BETWEEN 1 AND 80),
 observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(event_id,observed_sha256,reason_code)
);
CREATE INDEX idx_qdp_sre_quarantine_recent
  ON qdp_sre_inbox_quarantine(observed_at DESC,event_id);
CREATE INDEX idx_qdp_sre_quarantine_event
  ON qdp_sre_inbox_quarantine(event_id,reason_code);

CREATE TRIGGER qdp_inbox_quarantine_immutable
 BEFORE UPDATE ON qdp_sre_inbox_quarantine
 FOR EACH ROW EXECUTE FUNCTION qdp_guard_integration_fact();
