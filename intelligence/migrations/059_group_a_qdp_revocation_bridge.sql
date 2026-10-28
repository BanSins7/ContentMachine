-- GA-MOD-24: additive, Group-A-owned QDP FIX-15 revocation inbox.
-- Distinct migration filename from SRE/Group B/Group C and deterministic
-- revision even for an invalidation delivered before an A preparation.
CREATE TABLE IF NOT EXISTS ga_qdp_revocation_receipts (
    event_id UUID PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE
       CHECK (project_id ~ '^[a-f0-9]{32}$'),
    qdp_run_id UUID NOT NULL,
    video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    input_sha256 TEXT NOT NULL CHECK (input_sha256 ~ '^[a-f0-9]{64}$'),
    event_payload_sha256 TEXT NOT NULL CHECK (event_payload_sha256 ~ '^[a-f0-9]{64}$'),
    provenance_sha256 TEXT NOT NULL CHECK (provenance_sha256 ~ '^[a-f0-9]{64}$'),
    invalidation_generation INTEGER NOT NULL CHECK (invalidation_generation > 0),
    origin TEXT NOT NULL CHECK (origin IN
       ('sre','human_review','d1_source','retention','configuration','operator')),
    affected_runs INTEGER NOT NULL CHECK (affected_runs >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_ga_qdp_revocation_scope
    ON ga_qdp_revocation_receipts(project_id,qdp_run_id,video_id,
                                  invalidation_generation DESC);

CREATE OR REPLACE FUNCTION ga24_revocation_immutable() RETURNS trigger
LANGUAGE plpgsql AS $ga24_guard$
BEGIN
    IF TG_OP='DELETE' AND (
        NOT EXISTS (SELECT 1 FROM project_registry p
                    WHERE p.id=OLD.project_id)
        OR NOT EXISTS (SELECT 1 FROM videos v WHERE v.id=OLD.video_id)
    ) THEN
        -- An owning project/video retention cascade may remove its receipt.
        -- Direct deletion while both parents still exist is always blocked.
        RETURN OLD;
    END IF;
    RAISE EXCEPTION 'GA QDP revocation receipt is immutable'
       USING ERRCODE='23514';
END;
$ga24_guard$;
CREATE TRIGGER ga24_qdp_revocation_receipt_immutable
    BEFORE UPDATE OR DELETE ON ga_qdp_revocation_receipts
    FOR EACH ROW EXECUTE FUNCTION ga24_revocation_immutable();
