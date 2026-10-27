-- M28: forward-only confirmed-generation accounting. NEVER enables POST.
-- The external source of financial truth must be a trusted authenticated
-- OpenRouter HTTPS GET /generation completed by the host-owned M28 port.
-- Direct SQL is trusted owner only; an untrusted JSON blob never authenticates.
CREATE UNIQUE INDEX sre_m28_intent_settlement_owner
  ON sre_live_opus_dispatch_intents
     (project_id,run_id,item_id,intent_id,reservation_id,request_sha256);

CREATE TABLE sre_live_opus_receipts (
  receipt_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  item_id BIGINT NOT NULL,
  intent_id UUID NOT NULL,
  reservation_id UUID NOT NULL,
  request_sha256 TEXT NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
  generation_id TEXT NOT NULL
    CHECK(length(generation_id) BETWEEN 4 AND 200
          AND generation_id ~ '^[A-Za-z0-9_.:-]+$'),
  model_id TEXT NOT NULL CHECK(model_id='anthropic/claude-opus-5.5'),
  provider_name TEXT NOT NULL CHECK(provider_name='Anthropic'),
  response_sha256 TEXT NOT NULL CHECK(response_sha256 ~ '^[0-9a-f]{64}$'),
  generation_metadata_sha256 TEXT NOT NULL
    CHECK(generation_metadata_sha256 ~ '^[0-9a-f]{64}$'),
  evidence_sha256 TEXT NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$'),
  input_tokens BIGINT NOT NULL CHECK(input_tokens>=0),
  output_tokens BIGINT NOT NULL CHECK(output_tokens>=0),
  held_usd NUMERIC(24,9) NOT NULL CHECK(held_usd>0),
  charged_usd NUMERIC(24,9) NOT NULL CHECK(charged_usd>=0
                                              AND charged_usd<=held_usd),
  settled_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  generation_authenticated BOOLEAN NOT NULL CHECK(generation_authenticated IS TRUE),
  -- A single M26 intent can have exactly one provider generation and receipt.
  UNIQUE(project_id,intent_id),
  UNIQUE(project_id,generation_id),
  UNIQUE(project_id,reservation_id),
  FOREIGN KEY(project_id,run_id,item_id,intent_id,reservation_id,request_sha256)
    REFERENCES sre_live_opus_dispatch_intents
      (project_id,run_id,item_id,intent_id,reservation_id,request_sha256)
    ON DELETE RESTRICT
);
CREATE INDEX sre_m28_receipts_project_recorded
  ON sre_live_opus_receipts(project_id,run_id,settled_at,receipt_id);

ALTER TABLE sre_live_budget_reservations
  ADD COLUMN settled_at TIMESTAMPTZ,
  ADD COLUMN settlement_receipt_id UUID;
ALTER TABLE sre_live_budget_reservations
  DROP CONSTRAINT IF EXISTS sre_live_budget_reservations_state_check;
ALTER TABLE sre_live_budget_reservations ADD CONSTRAINT sre_m28_reservation_states
  CHECK(state IN ('reserved','released','settled'));
ALTER TABLE sre_live_budget_reservations
  ADD CONSTRAINT sre_m28_reserved_or_final CHECK(
    (state='reserved' AND released_at IS NULL AND release_actor IS NULL
      AND release_reason IS NULL AND settled_at IS NULL
      AND settlement_receipt_id IS NULL) OR
    (state='released' AND released_at IS NOT NULL AND release_actor IS NOT NULL
      AND release_reason IS NOT NULL AND settled_at IS NULL
      AND settlement_receipt_id IS NULL) OR
    (state='settled' AND released_at IS NULL AND release_actor IS NULL
      AND release_reason IS NULL AND settled_at IS NOT NULL
      AND settlement_receipt_id IS NOT NULL));
-- The prior M25 state/metadata CHECK allowed released and reserved only:
-- state=settled is now covered by sre_m28_reserved_or_final. Replace it.
ALTER TABLE sre_live_budget_reservations
  DROP CONSTRAINT IF EXISTS sre_live_budget_reservations_check;
ALTER TABLE sre_live_budget_reservations
  DROP CONSTRAINT IF EXISTS sre_live_budget_reservations_check1;

ALTER TABLE sre_live_budget_events
  DROP CONSTRAINT IF EXISTS sre_live_budget_events_event_kind_check;
ALTER TABLE sre_live_budget_events
  ADD CONSTRAINT sre_m28_event_kinds CHECK (
    event_kind IN ('reserved','released','retention_released','settled'));
ALTER TABLE sre_live_budget_events
  ADD COLUMN receipt_id UUID,
  ADD CONSTRAINT sre_m28_settlement_event_receipt CHECK (
    (event_kind='settled' AND receipt_id IS NOT NULL) OR
    (event_kind<>'settled' AND receipt_id IS NULL));

-- The M25 account trigger still prohibits arbitrary increases in spent USD.
-- Allow the one exact journaled movement during an AFTER reservation settlement
-- trigger. Depth alone is NOT authorization: matching receipt and final
-- reservation MUST be present; project lock serializes finance operations.
CREATE OR REPLACE FUNCTION sre_m25_budget_account_guard() RETURNS trigger
LANGUAGE plpgsql AS $m28_account$
BEGIN
  IF NEW.project_id IS DISTINCT FROM OLD.project_id
     OR NEW.cap_usd IS DISTINCT FROM OLD.cap_usd
     OR NEW.configured_actor IS DISTINCT FROM OLD.configured_actor
     OR NEW.created_at IS DISTINCT FROM OLD.created_at THEN
    RAISE EXCEPTION 'M28 fixed budget identity/cap immutable';
  END IF;
  IF NEW.spent_usd IS DISTINCT FROM OLD.spent_usd THEN
    IF pg_trigger_depth()<2 OR NOT EXISTS (
      SELECT 1 FROM sre_live_opus_receipts t
      JOIN sre_live_budget_reservations x
        ON x.project_id=t.project_id AND x.reservation_id=t.reservation_id
      WHERE t.project_id=OLD.project_id AND x.state='settled'
        AND x.settlement_receipt_id=t.receipt_id
        AND NEW.spent_usd=OLD.spent_usd+t.charged_usd
        AND NEW.reserved_usd=OLD.reserved_usd-x.amount_usd
        AND NEW.released_usd=OLD.released_usd+
                               x.amount_usd-t.charged_usd
        AND t.settled_at>clock_timestamp()-INTERVAL '1 minute'
    ) THEN
      RAISE EXCEPTION 'M28 unaudited spent counter mutation';
    END IF;
  END IF;
  RETURN NEW;
END;
$m28_account$;

-- Preserve the original M25 INSERT/expiry/consent gates, but allow exactly
-- ONE final reserved->settled transition and no mutation after settlement.
CREATE OR REPLACE FUNCTION sre_m25_budget_reservation_guard() RETURNS trigger
LANGUAGE plpgsql AS $m28_reservation$
DECLARE
  pin RECORD;
BEGIN
  IF TG_OP='INSERT' THEN
    IF NEW.state<>'reserved' OR NEW.released_at IS NOT NULL
       OR NEW.settled_at IS NOT NULL OR NEW.settlement_receipt_id IS NOT NULL
    THEN RAISE EXCEPTION 'M28 reserve must start unspent'; END IF;
    PERFORM 1 FROM sre_workspaces WHERE project_id=NEW.project_id FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION 'M28 workspace absent'; END IF;
    SELECT c.max_usd,c.expires_at,c.revoked_at,q.max_usd AS quote_usd,
           q.expires_at AS quote_expires INTO pin
      FROM sre_live_opus_consents c JOIN sre_live_opus_quotes q
      ON (q.project_id,q.run_id,q.item_id,q.quote_sha256)=
         (c.project_id,c.run_id,c.item_id,c.quote_sha256)
      WHERE c.consent_id=NEW.consent_id AND c.project_id=NEW.project_id
        AND c.run_id=NEW.run_id AND c.item_id=NEW.item_id
        AND c.quote_sha256=NEW.quote_sha256
      FOR SHARE OF c,q;
    IF NOT FOUND OR pin.revoked_at IS NOT NULL
       OR pin.expires_at<=clock_timestamp()
       OR pin.quote_expires<=clock_timestamp()
       OR pin.max_usd<>NEW.amount_usd OR pin.quote_usd<>NEW.amount_usd
    THEN RAISE EXCEPTION 'M28 active exact consent amount required'; END IF;
    PERFORM 1 FROM sre_live_attempt_fences
      WHERE project_id=NEW.project_id AND run_id=NEW.run_id
      AND item_id=NEW.item_id AND state IN
          ('dispatch_intent','unknown','completed','failed_known');
    IF FOUND THEN RAISE EXCEPTION 'M28 paid/unknown attempt cannot re-reserve'; END IF;
    RETURN NEW;
  END IF;
  IF OLD.state<>'reserved' OR NEW.state NOT IN ('released','settled')
     OR (to_jsonb(NEW)-'state'-'released_at'-'release_actor'-'release_reason'-
         'settled_at'-'settlement_receipt_id') IS DISTINCT FROM
        (to_jsonb(OLD)-'state'-'released_at'-'release_actor'-'release_reason'-
         'settled_at'-'settlement_receipt_id') THEN
    RAISE EXCEPTION 'M28 reservation is immutable except one-time finalization';
  END IF;
  PERFORM 1 FROM sre_workspaces WHERE project_id=NEW.project_id FOR SHARE;
  IF NEW.state='released' THEN
    IF NEW.released_at IS NULL OR NEW.release_actor IS NULL
       OR NEW.release_reason IS NULL OR NEW.settled_at IS NOT NULL
       OR NEW.settlement_receipt_id IS NOT NULL
       OR NEW.released_at<OLD.reserved_at
       OR NEW.released_at>clock_timestamp()+INTERVAL '30 seconds' THEN
      RAISE EXCEPTION 'M28 invalid pre-dispatch release'; END IF;
    PERFORM 1 FROM sre_live_attempt_fences
      WHERE project_id=NEW.project_id AND run_id=NEW.run_id
      AND item_id=NEW.item_id AND state IN
          ('dispatch_intent','unknown','completed','failed_known');
    IF FOUND THEN RAISE EXCEPTION 'M28 possible paid charge retains reserve'; END IF;
  ELSE
    IF NEW.released_at IS NOT NULL OR NEW.release_actor IS NOT NULL
       OR NEW.release_reason IS NOT NULL OR NEW.settled_at IS NULL
       OR NEW.settlement_receipt_id IS NULL
       OR NEW.settled_at<OLD.reserved_at
       OR NEW.settled_at>clock_timestamp()+INTERVAL '30 seconds'
       OR NOT EXISTS (
        SELECT 1 FROM sre_live_opus_receipts t
        WHERE t.project_id=NEW.project_id
          AND t.reservation_id=NEW.reservation_id
          AND t.receipt_id=NEW.settlement_receipt_id
          AND t.held_usd=NEW.amount_usd
      ) THEN RAISE EXCEPTION 'M28 exact authenticated receipt required'; END IF;
  END IF;
  RETURN NEW;
END;
$m28_reservation$;

CREATE OR REPLACE FUNCTION sre_m25_budget_post_account() RETURNS trigger
LANGUAGE plpgsql AS $m28_post$
DECLARE charged NUMERIC(24,9);
BEGIN
  IF TG_OP='INSERT' THEN
    UPDATE sre_live_budget_accounts
    SET reserved_usd=reserved_usd+NEW.amount_usd,updated_at=clock_timestamp()
    WHERE project_id=NEW.project_id
      AND cap_usd>=spent_usd+reserved_usd+NEW.amount_usd;
    IF NOT FOUND THEN RAISE EXCEPTION 'M28 project cap exceeded'; END IF;
    INSERT INTO sre_live_budget_events
      (project_id,reservation_id,event_kind,amount_usd)
    VALUES (NEW.project_id,NEW.reservation_id,'reserved',NEW.amount_usd);
  ELSIF TG_OP='UPDATE' AND NEW.state='released' THEN
    UPDATE sre_live_budget_accounts
    SET reserved_usd=reserved_usd-OLD.amount_usd,
        released_usd=released_usd+OLD.amount_usd,
        updated_at=clock_timestamp()
    WHERE project_id=NEW.project_id AND reserved_usd>=OLD.amount_usd;
    IF NOT FOUND THEN RAISE EXCEPTION 'M28 released reserve underflow'; END IF;
    INSERT INTO sre_live_budget_events
      (project_id,reservation_id,event_kind,amount_usd)
    VALUES (NEW.project_id,NEW.reservation_id,'released',NEW.amount_usd);
  ELSIF TG_OP='UPDATE' AND NEW.state='settled' THEN
    SELECT t.charged_usd INTO charged FROM sre_live_opus_receipts t
    WHERE t.project_id=NEW.project_id AND t.reservation_id=NEW.reservation_id
      AND t.receipt_id=NEW.settlement_receipt_id;
    IF NOT FOUND OR charged>OLD.amount_usd OR charged<0 THEN
      RAISE EXCEPTION 'M28 invalid charge'; END IF;
    UPDATE sre_live_budget_accounts
    SET reserved_usd=reserved_usd-OLD.amount_usd,
        spent_usd=spent_usd+charged,
        released_usd=released_usd+OLD.amount_usd-charged,
        updated_at=clock_timestamp()
    WHERE project_id=NEW.project_id AND reserved_usd>=OLD.amount_usd
      AND cap_usd>=spent_usd+charged;
    IF NOT FOUND THEN RAISE EXCEPTION 'M28 insufficient reserve'; END IF;
    INSERT INTO sre_live_budget_events
      (project_id,reservation_id,event_kind,amount_usd,receipt_id)
    VALUES (NEW.project_id,NEW.reservation_id,'settled',
            CASE WHEN charged=0 THEN OLD.amount_usd ELSE charged END,
            NEW.settlement_receipt_id);
  ELSIF TG_OP='DELETE' THEN
    -- A paid/unknown M15 witness must never be erased by project retention.
    PERFORM 1 FROM sre_live_attempt_fences WHERE project_id=OLD.project_id
      AND run_id=OLD.run_id AND item_id=OLD.item_id
      AND state IN ('dispatch_intent','unknown','completed','failed_known');
    IF FOUND THEN RAISE EXCEPTION 'M28 paid/unknown reserve cannot be purged'; END IF;
    IF OLD.state='reserved' THEN
      UPDATE sre_live_budget_accounts
        SET reserved_usd=reserved_usd-OLD.amount_usd,
            released_usd=released_usd+OLD.amount_usd,
            updated_at=clock_timestamp()
      WHERE project_id=OLD.project_id AND reserved_usd>=OLD.amount_usd;
      IF FOUND THEN
        INSERT INTO sre_live_budget_events
          (project_id,reservation_id,event_kind,amount_usd)
        VALUES (OLD.project_id,OLD.reservation_id,'retention_released',OLD.amount_usd);
      END IF;
    END IF;
  END IF;
  RETURN NULL;
END;
$m28_post$;

CREATE FUNCTION sre_m28_receipt_guard() RETURNS trigger LANGUAGE plpgsql AS $m28_receipt$
DECLARE pin RECORD;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'M28 receipt immutable and cannot be purged';
  END IF;
  PERFORM 1 FROM sre_workspaces WHERE project_id=NEW.project_id FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'M28 workspace missing'; END IF;
  SELECT a.state AS attempt_state, r.state AS reserve_state,
         r.amount_usd AS hold, i.reserved_usd AS intent_hold
    INTO pin
  FROM sre_live_opus_dispatch_intents i
  JOIN sre_live_attempt_fences a ON
     a.project_id=i.project_id AND a.attempt_id=i.attempt_id
  JOIN sre_live_budget_reservations r ON
     r.project_id=i.project_id AND r.reservation_id=i.reservation_id
  WHERE i.project_id=NEW.project_id AND i.run_id=NEW.run_id
    AND i.item_id=NEW.item_id AND i.intent_id=NEW.intent_id
    AND i.reservation_id=NEW.reservation_id
    AND i.request_sha256=NEW.request_sha256
    AND i.model_id=NEW.model_id
  FOR SHARE OF a,r,i;
  IF NOT FOUND OR pin.attempt_state NOT IN ('dispatch_intent','unknown')
     OR pin.reserve_state<>'reserved' OR pin.hold<>NEW.held_usd
     OR pin.intent_hold<>NEW.held_usd
     OR NEW.settled_at>clock_timestamp()+INTERVAL '30 seconds'
     OR NEW.settled_at<clock_timestamp()-INTERVAL '1 minute' THEN
    RAISE EXCEPTION 'M28 authenticated provider receipt must match held M26 intent';
  END IF;
  RETURN NEW;
END;
$m28_receipt$;
CREATE TRIGGER sre_m28_receipt_insert_guard
  BEFORE INSERT ON sre_live_opus_receipts
  FOR EACH ROW EXECUTE FUNCTION sre_m28_receipt_guard();
CREATE FUNCTION sre_m28_receipt_settle() RETURNS trigger LANGUAGE plpgsql AS $m28_settle$
BEGIN
  UPDATE sre_live_budget_reservations
  SET state='settled',settled_at=clock_timestamp(),
      settlement_receipt_id=NEW.receipt_id
  WHERE project_id=NEW.project_id AND reservation_id=NEW.reservation_id
    AND state='reserved' AND amount_usd=NEW.held_usd;
  IF NOT FOUND THEN RAISE EXCEPTION 'M28 receipt settlement race'; END IF;
  RETURN NULL;
END;
$m28_settle$;
CREATE TRIGGER sre_m28_receipt_after_insert
  AFTER INSERT ON sre_live_opus_receipts
  FOR EACH ROW EXECUTE FUNCTION sre_m28_receipt_settle();
CREATE TRIGGER sre_m28_receipt_no_mutation
  BEFORE UPDATE OR DELETE ON sre_live_opus_receipts
  FOR EACH ROW EXECUTE FUNCTION sre_m28_receipt_guard();
REVOKE ALL ON sre_live_opus_receipts FROM PUBLIC;
