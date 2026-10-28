-- M29 additive 063: immutable audit of unknown, verified-generation-reconciled
-- outcomes. No provider POST, refund guess, retry-enable or worker unfreeze.
-- M26 one-per-run uniqueness remains, so no multi-request paid dispatch.
CREATE UNIQUE INDEX sre_m29_intent_owner
  ON sre_live_opus_dispatch_intents(project_id,run_id,item_id,intent_id);
CREATE UNIQUE INDEX sre_m29_receipt_owner
  ON sre_live_opus_receipts(project_id,receipt_id);

CREATE TABLE sre_live_opus_reconciliation_events (
  event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  item_id BIGINT NOT NULL,
  intent_id UUID NOT NULL,
  event_kind TEXT NOT NULL CHECK (event_kind IN (
    'unknown_no_generation_id','generation_lookup_unverified',
    'generation_accounted'
  )),
  generation_id TEXT CHECK (
    generation_id IS NULL OR (
      length(generation_id) BETWEEN 4 AND 200
      AND generation_id ~ '^[A-Za-z0-9_.:-]+$'
    )
  ),
  receipt_id UUID,
  actor_id TEXT NOT NULL CHECK(actor_id ~ '^[A-Za-z0-9:_@.-]{1,128}$'),
  reason_code TEXT NOT NULL CHECK(reason_code IN (
    'no_generation_id','generation_not_confirmed','generation_receipt_confirmed'
  )),
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  FOREIGN KEY (project_id,run_id,item_id,intent_id)
    REFERENCES sre_live_opus_dispatch_intents(project_id,run_id,item_id,intent_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (project_id,receipt_id)
    REFERENCES sre_live_opus_receipts(project_id,receipt_id)
    ON DELETE RESTRICT,
  CHECK (
    (event_kind='generation_accounted' AND
      generation_id IS NOT NULL AND receipt_id IS NOT NULL
      AND reason_code='generation_receipt_confirmed')
    OR
    (event_kind='unknown_no_generation_id' AND
      generation_id IS NULL AND receipt_id IS NULL
      AND reason_code='no_generation_id')
    OR
    (event_kind='generation_lookup_unverified' AND
      generation_id IS NOT NULL AND receipt_id IS NULL
      AND reason_code='generation_not_confirmed')
  )
);
CREATE UNIQUE INDEX sre_m29_one_event_per_reason
  ON sre_live_opus_reconciliation_events
     (project_id,intent_id,event_kind,COALESCE(generation_id,''));
CREATE UNIQUE INDEX sre_m29_one_confirmed_receipt
  ON sre_live_opus_reconciliation_events(project_id,intent_id)
  WHERE event_kind='generation_accounted';
CREATE INDEX sre_m29_project_reconcile_time
  ON sre_live_opus_reconciliation_events(project_id,run_id,recorded_at,event_id);

CREATE FUNCTION sre_m29_guard_reconciliation()
RETURNS trigger LANGUAGE plpgsql AS $m29$
DECLARE
  state_info RECORD;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'M29 reconciliation events are immutable and cannot be removed';
  END IF;
  PERFORM 1 FROM sre_workspaces WHERE project_id=NEW.project_id FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'M29 project workspace unavailable'; END IF;
  SELECT a.state AS attempt_state,rs.state AS reserve_state,
         rs.settlement_receipt_id, i.request_sha256,
         t.receipt_id AS real_receipt,t.generation_id AS real_generation,
         t.request_sha256 AS real_request,t.generation_authenticated
    INTO state_info
    FROM sre_live_opus_dispatch_intents i
    JOIN sre_live_attempt_fences a
      ON a.project_id=i.project_id AND a.attempt_id=i.attempt_id
    JOIN sre_live_budget_reservations rs
      ON rs.project_id=i.project_id AND rs.reservation_id=i.reservation_id
    LEFT JOIN sre_live_opus_receipts t
      ON t.project_id=i.project_id AND t.intent_id=i.intent_id
    WHERE i.project_id=NEW.project_id AND i.run_id=NEW.run_id
      AND i.item_id=NEW.item_id AND i.intent_id=NEW.intent_id
    FOR SHARE OF i,a,rs,t;
  IF NOT FOUND OR state_info.attempt_state NOT IN (
       'dispatch_intent','unknown','completed','failed_known'
     ) THEN RAISE EXCEPTION 'M29 cannot reconcile without durable M26 possible paid intent'; END IF;
  IF NEW.event_kind='generation_accounted' THEN
    IF state_info.reserve_state<>'settled'
       OR state_info.real_receipt IS DISTINCT FROM NEW.receipt_id
       OR state_info.settlement_receipt_id IS DISTINCT FROM NEW.receipt_id
       OR state_info.real_generation IS DISTINCT FROM NEW.generation_id
       OR state_info.real_request IS DISTINCT FROM state_info.request_sha256
       OR state_info.generation_authenticated IS NOT TRUE
    THEN RAISE EXCEPTION 'M29 confirmed outcome requires exact durable authenticated M28 paid receipt'; END IF;
  ELSE
    IF state_info.real_receipt IS NOT NULL
       OR EXISTS (
          SELECT 1 FROM sre_live_opus_reconciliation_events prior
          WHERE prior.project_id=NEW.project_id
            AND prior.intent_id=NEW.intent_id
            AND prior.event_kind='generation_accounted'
       )
    THEN RAISE EXCEPTION 'M29 already confirmed; no uncertain downgrade'; END IF;
    IF state_info.reserve_state<>'reserved' THEN
      RAISE EXCEPTION 'M29 unknown outcome must retain M25 reserved funds';
    END IF;
  END IF;
  RETURN NEW;
END;
$m29$;
CREATE TRIGGER sre_m29_reconcile_insert
  BEFORE INSERT ON sre_live_opus_reconciliation_events
  FOR EACH ROW EXECUTE FUNCTION sre_m29_guard_reconciliation();
CREATE TRIGGER sre_m29_reconcile_immutable
  BEFORE UPDATE OR DELETE ON sre_live_opus_reconciliation_events
  FOR EACH ROW EXECUTE FUNCTION sre_m29_guard_reconciliation();
REVOKE ALL ON sre_live_opus_reconciliation_events FROM PUBLIC;
