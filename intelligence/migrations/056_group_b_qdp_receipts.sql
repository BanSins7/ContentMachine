-- GB-MOD-04: consumer-owned QDP delivery receipt and append-only replay quarantine.
-- 055 and main-owned 005 are immutable. No dependency on peer QDP tables.
CREATE TABLE group_b_inbox_receipts (
 project_id TEXT NOT NULL,
 inbox_id UUID NOT NULL,
 run_id UUID NOT NULL,
 event_id UUID NOT NULL,
 event_sha256 TEXT NOT NULL CHECK(event_sha256 ~ '^[0-9a-f]{64}$'),
 preparation_sha256 TEXT NOT NULL CHECK(preparation_sha256 ~ '^[0-9a-f]{64}$'),
 receipt_key TEXT NOT NULL CHECK(length(receipt_key) BETWEEN 8 AND 160),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(project_id,inbox_id),
 UNIQUE(project_id,event_id),
 UNIQUE(project_id,receipt_key),
 FOREIGN KEY(project_id,inbox_id) REFERENCES group_b_inbox(project_id,id),
 FOREIGN KEY(project_id,run_id,inbox_id) REFERENCES group_b_runs(project_id,id,inbox_id)
);
CREATE INDEX group_b_receipts_run ON group_b_inbox_receipts(project_id,run_id);

CREATE TABLE group_b_inbox_conflicts (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 project_id TEXT NOT NULL,
 inbox_id UUID NOT NULL,
 event_key TEXT NOT NULL CHECK(length(event_key) BETWEEN 1 AND 200),
 expected_sha256 TEXT NOT NULL CHECK(expected_sha256 ~ '^[0-9a-f]{64}$'),
 received_sha256 TEXT NOT NULL CHECK(received_sha256 ~ '^[0-9a-f]{64}$'),
 reason_code TEXT NOT NULL CHECK(reason_code IN
    ('event_hash_conflict','preparation_hash_conflict','identity_conflict')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(project_id,inbox_id) REFERENCES group_b_inbox(project_id,id),
 UNIQUE(project_id,event_key,received_sha256,reason_code),
 CHECK(expected_sha256<>received_sha256 OR reason_code IN ('identity_conflict','preparation_hash_conflict'))
);
CREATE INDEX group_b_conflict_project_time ON group_b_inbox_conflicts(project_id,created_at DESC);
CREATE TRIGGER trg_group_b_receipts_immutable BEFORE UPDATE OR DELETE
 ON group_b_inbox_receipts FOR EACH ROW EXECUTE FUNCTION group_b_forbid_change();
CREATE TRIGGER trg_group_b_conflicts_immutable BEFORE UPDATE OR DELETE
 ON group_b_inbox_conflicts FOR EACH ROW EXECUTE FUNCTION group_b_forbid_change();
