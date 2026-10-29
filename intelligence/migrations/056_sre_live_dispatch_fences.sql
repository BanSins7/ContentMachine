-- M15 additive fail-stop LIVE dispatch-intent fence and crash audit.
-- Corrected unreleased SRE-only 056 syntax after PG17 CI found an invalid
-- truncated check and duplicate DDL. Historical 016/017 are unchanged.
-- This is NOT provider dispatch, consent, money reservation or settlement.
-- An intent may have reached a paid provider; never retry it blindly.

CREATE TABLE sre_live_attempt_fences (
  attempt_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  item_id BIGINT NOT NULL,
  batch_id UUID NOT NULL,
  lease_generation BIGINT NOT NULL CHECK (lease_generation>=1),
  worker_token UUID NOT NULL,
  request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  -- A lease generation may have at most one intent. A known abandoned
  -- pre-dispatch attempt requires a NEW fenced claim and generation.
  UNIQUE (item_id,lease_generation),
  state TEXT NOT NULL DEFAULT 'reserved' CHECK (state IN (
    'reserved','dispatch_intent','unknown','completed','failed_known',
    'abandoned_before_dispatch'
  )),
  intent_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  FOREIGN KEY (batch_id,run_id,project_id,item_id)
    REFERENCES sre_live_batch_members(batch_id,run_id,project_id,item_id),
  CHECK ((state IN ('reserved','abandoned_before_dispatch') AND intent_at IS NULL)
         OR (state IN ('dispatch_intent','unknown','completed','failed_known')
             AND intent_at IS NOT NULL))
);
CREATE INDEX sre_m15_attempts_by_run
  ON sre_live_attempt_fences(project_id,run_id,state,item_id);
CREATE INDEX sre_m15_attempts_by_item
  ON sre_live_attempt_fences(project_id,item_id,lease_generation);

-- Protect the evidence that decides whether safe replay is possible.
-- This is a technical witness table; actual paid execution remains forbidden
-- until budget + approval + provider journal are implemented later.
CREATE FUNCTION sre_m15_guard_attempt() RETURNS trigger LANGUAGE plpgsql AS $m15_attempt$
DECLARE current_lease RECORD;
BEGIN
  -- Coordinate even a direct attempt INSERT/UPDATE with M15 cancellation:
  -- claim/attempt writers hold workspace FOR SHARE; cancel/stop takes
  -- workspace FOR UPDATE first. This prevents a new dispatch_intent from
  -- sneaking into a transaction after cancellation inspected the journal.
  PERFORM 1 FROM sre_workspaces
    WHERE project_id=NEW.project_id FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'M15 attempt workspace does not exist';
  END IF;
  IF TG_OP='INSERT' THEN
    IF NEW.state <> 'reserved' OR NEW.intent_at IS NOT NULL THEN
      RAISE EXCEPTION 'M15 attempt must begin reserved before dispatch';
    END IF;
    SELECT i.worker_token,i.lease_generation,i.lease_until,i.state,
           r.state AS run_state,r.cancel_requested
      INTO current_lease
    FROM sre_live_items i JOIN sre_live_runs r
      ON r.run_id=i.run_id AND r.project_id=i.project_id
    WHERE i.project_id=NEW.project_id AND i.id=NEW.item_id
      AND i.run_id=NEW.run_id;
    IF NOT FOUND OR current_lease.worker_token IS DISTINCT FROM NEW.worker_token
       OR current_lease.lease_generation <> NEW.lease_generation
       OR current_lease.lease_until <= clock_timestamp()
       OR current_lease.state <> 'running'
       OR current_lease.run_state NOT IN ('queued','running')
       OR current_lease.cancel_requested THEN
      RAISE EXCEPTION 'M15 attempt requires current fenced LIVE owner';
    END IF;
  ELSE
    IF (OLD.attempt_id,OLD.project_id,OLD.run_id,OLD.item_id,OLD.batch_id,
        OLD.lease_generation,OLD.worker_token,OLD.request_sha256,OLD.created_at)
       IS DISTINCT FROM
       (NEW.attempt_id,NEW.project_id,NEW.run_id,NEW.item_id,NEW.batch_id,
        NEW.lease_generation,NEW.worker_token,NEW.request_sha256,NEW.created_at)
    THEN RAISE EXCEPTION 'M15 immutable dispatch witness'; END IF;
    IF OLD.state IS DISTINCT FROM NEW.state AND NOT (
       (OLD.state='reserved' AND
        NEW.state IN ('dispatch_intent','abandoned_before_dispatch'))
       OR (OLD.state='dispatch_intent' AND NEW.state='unknown')
    ) THEN
      RAISE EXCEPTION 'M15 forbidden attempt recovery without audited reconciliation';
    END IF;
    IF OLD.intent_at IS NOT NULL AND NEW.intent_at IS DISTINCT FROM OLD.intent_at
    THEN RAISE EXCEPTION 'M15 intent timestamp may never be changed'; END IF;
    IF OLD.state='reserved' AND NEW.state='dispatch_intent' THEN
      SELECT i.worker_token,i.lease_generation,i.lease_until,i.state,
             r.state AS run_state,r.cancel_requested
        INTO current_lease
      FROM sre_live_items i JOIN sre_live_runs r
        ON r.run_id=i.run_id AND r.project_id=i.project_id
      WHERE i.project_id=NEW.project_id AND i.id=NEW.item_id
        AND i.run_id=NEW.run_id;
      IF NOT FOUND OR current_lease.worker_token IS DISTINCT FROM NEW.worker_token
         OR current_lease.lease_generation <> NEW.lease_generation
         OR current_lease.lease_until <= clock_timestamp()
         OR current_lease.state <> 'running'
         OR current_lease.run_state NOT IN ('queued','running')
         OR current_lease.cancel_requested
         OR NEW.intent_at IS NULL
      THEN RAISE EXCEPTION 'M15 cannot dispatch with stale or invalid lease'; END IF;
    END IF;
    IF OLD.state='reserved' AND NEW.state='abandoned_before_dispatch'
       AND NEW.intent_at IS NOT NULL THEN
      RAISE EXCEPTION 'M15 pre-dispatch abandon cannot claim intent'; END IF;
  END IF;
  NEW.updated_at:=clock_timestamp();
  RETURN NEW;
END;
$m15_attempt$;
CREATE TRIGGER sre_m15_attempt_guard
  BEFORE INSERT OR UPDATE ON sre_live_attempt_fences
  FOR EACH ROW EXECUTE FUNCTION sre_m15_guard_attempt();

CREATE FUNCTION sre_m15_no_delete() RETURNS trigger LANGUAGE plpgsql AS $m15_nodelete$
BEGIN
  RAISE EXCEPTION 'M15 dispatch-intent witnesses are append only';
END;
$m15_nodelete$;
CREATE TRIGGER sre_m15_attempt_no_delete
  BEFORE DELETE ON sre_live_attempt_fences
  FOR EACH ROW EXECUTE FUNCTION sre_m15_no_delete();

REVOKE ALL ON sre_live_attempt_fences FROM PUBLIC;
