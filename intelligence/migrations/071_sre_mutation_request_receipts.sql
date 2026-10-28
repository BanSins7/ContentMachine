-- M39: crash-safe mutation request replay ledger, private SRE-owned.
-- A pending claim never expires automatically: after a crash between a
-- successful domain commit and response recording, retrying might re-grant
-- spending consent or duplicate human review. Operator reconciliation is
-- required before a fresh key; never treat "in doubt" as proof of failure.
CREATE TABLE sre_m39_mutation_receipts (
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 128),
 operation TEXT NOT NULL CHECK (operation IN
  ('attach','create_run','issue_quote','grant_consent','revoke_consent',
   'cancel_run','human_review','reconcile_unknown')),
 request_key TEXT NOT NULL CHECK (request_key ~ '^[A-Za-z0-9._-]{12,120}$'),
 request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
 state TEXT NOT NULL DEFAULT 'pending' CHECK (state IN ('pending','committed')),
 result JSONB,
 claimed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 committed_at TIMESTAMPTZ,
 PRIMARY KEY (project_id,actor_id,operation,request_key),
 CHECK ((state='pending' AND result IS NULL AND committed_at IS NULL)
   OR (state='committed' AND jsonb_typeof(result)='object'
       AND octet_length(result::text)<=4096 AND committed_at IS NOT NULL))
);
CREATE INDEX sre_m39_mutation_receipts_project_claimed
 ON sre_m39_mutation_receipts(project_id,claimed_at);

CREATE FUNCTION sre_m39_mutation_receipt_guard() RETURNS trigger
LANGUAGE plpgsql AS $sre_m39_receipt_guard$
BEGIN
 IF TG_OP='UPDATE' THEN
  IF OLD.state<>'pending' OR NEW.state<>'committed'
    OR NEW.project_id IS DISTINCT FROM OLD.project_id
    OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
    OR NEW.operation IS DISTINCT FROM OLD.operation
    OR NEW.request_key IS DISTINCT FROM OLD.request_key
    OR NEW.request_sha256 IS DISTINCT FROM OLD.request_sha256
    OR NEW.claimed_at IS DISTINCT FROM OLD.claimed_at
    OR NEW.committed_at IS NULL OR NEW.result IS NULL THEN
   RAISE EXCEPTION 'SRE immutable mutation receipt violation';
  END IF;
 END IF;
 RETURN NEW;
END;
$sre_m39_receipt_guard$;
CREATE TRIGGER sre_m39_mutation_receipt_immutable
 BEFORE UPDATE ON sre_m39_mutation_receipts
 FOR EACH ROW EXECUTE FUNCTION sre_m39_mutation_receipt_guard();
REVOKE ALL ON sre_m39_mutation_receipts FROM PUBLIC;
