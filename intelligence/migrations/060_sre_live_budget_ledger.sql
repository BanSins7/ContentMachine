-- M25 SRE-only immutable project USD budget and atomic consent-bound reservation.
-- Forward-only 060. No provider transport, receipt settlement, paid attempts.
-- All amounts are NUMERIC(24,9). Owners MUST explicitly install a budget.
CREATE TABLE sre_live_budget_accounts (
  project_id TEXT PRIMARY KEY REFERENCES sre_workspaces(project_id) ON DELETE CASCADE,
  cap_usd NUMERIC(24,9) NOT NULL CHECK (cap_usd>0),
  reserved_usd NUMERIC(24,9) NOT NULL DEFAULT 0 CHECK (reserved_usd>=0),
  spent_usd NUMERIC(24,9) NOT NULL DEFAULT 0 CHECK (spent_usd>=0),
  released_usd NUMERIC(24,9) NOT NULL DEFAULT 0 CHECK (released_usd>=0),
  configured_actor TEXT NOT NULL CHECK (configured_actor ~ '^[A-Za-z0-9:_@.-]{1,128}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CHECK (reserved_usd+spent_usd<=cap_usd)
);
-- Composite quote/consent/tenant identity, not just the primary consent UUID.
CREATE UNIQUE INDEX sre_m25_consent_ledger_owner
  ON sre_live_opus_consents(project_id,run_id,item_id,quote_sha256,consent_id);

CREATE TABLE sre_live_budget_reservations (
  reservation_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES sre_live_budget_accounts(project_id) ON DELETE CASCADE,
  run_id UUID NOT NULL,
  item_id BIGINT NOT NULL,
  quote_sha256 TEXT NOT NULL CHECK (quote_sha256 ~ '^[0-9a-f]{64}$'),
  consent_id UUID NOT NULL,
  amount_usd NUMERIC(24,9) NOT NULL CHECK (amount_usd>0),
  state TEXT NOT NULL DEFAULT 'reserved' CHECK (state IN ('reserved','released')),
  reserved_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  released_at TIMESTAMPTZ,
  release_actor TEXT CHECK (release_actor ~ '^[A-Za-z0-9:_@.-]{1,128}$'),
  release_reason TEXT CHECK (release_reason IN (
    'user_cancelled','operator_cancelled','consent_revoked',
    'rights_revoked','expired_pre_dispatch')),
  FOREIGN KEY(project_id,run_id,item_id,quote_sha256,consent_id)
    REFERENCES sre_live_opus_consents(project_id,run_id,item_id,quote_sha256,consent_id)
    ON DELETE CASCADE,
  UNIQUE(project_id,consent_id),
  CHECK ((state='reserved' AND released_at IS NULL AND release_actor IS NULL
          AND release_reason IS NULL) OR
         (state='released' AND released_at IS NOT NULL AND
          release_actor IS NOT NULL AND release_reason IS NOT NULL
          AND released_at>=reserved_at))
);
-- A fresh quote cannot double-reserve the same LIVE item while its earlier
-- quote remains reserved. A released consent itself cannot be reused.
CREATE UNIQUE INDEX sre_m25_one_active_item
  ON sre_live_budget_reservations(project_id,run_id,item_id)
  WHERE state='reserved';

CREATE TABLE sre_live_budget_events (
  event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES sre_live_budget_accounts(project_id)
    ON DELETE CASCADE,
  reservation_id UUID NOT NULL,
  event_kind TEXT NOT NULL CHECK (event_kind IN ('reserved','released','retention_released')),
  amount_usd NUMERIC(24,9) NOT NULL CHECK (amount_usd>0),
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(reservation_id,event_kind)
);
CREATE INDEX sre_m25_budget_events_project_time
  ON sre_live_budget_events(project_id,recorded_at,event_id);

-- SQL-level invariants protect against accidental callers bypassing Python.
-- AFTER INSERT accounting is vital: ON CONFLICT DO NOTHING must not charge.
-- The configured project ceiling and authenticated creator are immutable.
-- Counters are updated by reservation triggers; all table owners are trusted.
CREATE FUNCTION sre_m25_budget_account_guard() RETURNS trigger
LANGUAGE plpgsql AS $m25_cap$
BEGIN
  IF NEW.project_id IS DISTINCT FROM OLD.project_id
     OR NEW.cap_usd IS DISTINCT FROM OLD.cap_usd
     OR NEW.configured_actor IS DISTINCT FROM OLD.configured_actor
     OR NEW.created_at IS DISTINCT FROM OLD.created_at
     OR NEW.spent_usd IS DISTINCT FROM OLD.spent_usd THEN
    RAISE EXCEPTION 'M25 fixed cap, owner and spent column are immutable';
  END IF;
  RETURN NEW;
END;
$m25_cap$;
CREATE TRIGGER sre_m25_budget_account_guard
  BEFORE UPDATE ON sre_live_budget_accounts
  FOR EACH ROW EXECUTE FUNCTION sre_m25_budget_account_guard();

CREATE FUNCTION sre_m25_budget_reservation_guard() RETURNS trigger
LANGUAGE plpgsql AS $m25_guard$
DECLARE
  pin RECORD;
  charged BIGINT;
BEGIN
  IF TG_OP='INSERT' THEN
    IF NEW.state<>'reserved' OR NEW.released_at IS NOT NULL THEN
      RAISE EXCEPTION 'M25 budget reservation must start reserved';
    END IF;
    -- Serialize with M15 cancellation, source revocation and attempt inserts.
    PERFORM 1 FROM sre_workspaces WHERE project_id=NEW.project_id FOR SHARE;
    IF NOT FOUND THEN RAISE EXCEPTION 'M25 SRE workspace absent'; END IF;
    SELECT c.max_usd,c.expires_at,c.revoked_at,q.max_usd AS quote_usd,
           q.expires_at AS quote_expires
      INTO pin
      FROM sre_live_opus_consents c
      JOIN sre_live_opus_quotes q
        ON (q.project_id,q.run_id,q.item_id,q.quote_sha256)=
           (c.project_id,c.run_id,c.item_id,c.quote_sha256)
      WHERE c.consent_id=NEW.consent_id AND c.project_id=NEW.project_id
        AND c.run_id=NEW.run_id AND c.item_id=NEW.item_id
        AND c.quote_sha256=NEW.quote_sha256
      FOR SHARE OF c,q;
    IF NOT FOUND OR pin.revoked_at IS NOT NULL
       OR pin.expires_at<=clock_timestamp()
       OR pin.quote_expires<=clock_timestamp()
       OR pin.max_usd<>NEW.amount_usd OR pin.quote_usd<>NEW.amount_usd THEN
      RAISE EXCEPTION 'M25 exact active quote consent/amount required';
    END IF;
    PERFORM 1 FROM sre_live_attempt_fences
      WHERE project_id=NEW.project_id AND run_id=NEW.run_id
        AND item_id=NEW.item_id AND state IN (
          'dispatch_intent','unknown','completed','failed_known');
    IF FOUND THEN RAISE EXCEPTION 'M25 unknown/paid attempt must not re-reserve'; END IF;
    RETURN NEW;
  ELSIF TG_OP='UPDATE' THEN
    IF OLD.state<>'reserved' OR NEW.state<>'released'
       OR (to_jsonb(NEW)-'state'-'released_at'-'release_actor'-'release_reason')
           IS DISTINCT FROM
          (to_jsonb(OLD)-'state'-'released_at'-'release_actor'-'release_reason')
       OR NEW.released_at IS NULL OR NEW.release_actor IS NULL
       OR NEW.release_reason IS NULL
       OR NEW.released_at<OLD.reserved_at
       OR NEW.released_at>clock_timestamp()+INTERVAL '30 seconds' THEN
      RAISE EXCEPTION 'M25 reservation is immutable except one-way release';
    END IF;
    PERFORM 1 FROM sre_workspaces WHERE project_id=NEW.project_id FOR SHARE;
    PERFORM 1 FROM sre_live_attempt_fences
      WHERE project_id=NEW.project_id AND run_id=NEW.run_id
        AND item_id=NEW.item_id AND state IN (
          'dispatch_intent','unknown','completed','failed_known');
    IF FOUND THEN RAISE EXCEPTION 'M25 possible paid dispatch: retain reserve'; END IF;
    RETURN NEW;
  END IF;
  RETURN NEW;
END;
$m25_guard$;
CREATE TRIGGER sre_m25_reservation_before_insert
  BEFORE INSERT ON sre_live_budget_reservations
  FOR EACH ROW EXECUTE FUNCTION sre_m25_budget_reservation_guard();
CREATE TRIGGER sre_m25_reservation_before_update
  BEFORE UPDATE ON sre_live_budget_reservations
  FOR EACH ROW EXECUTE FUNCTION sre_m25_budget_reservation_guard();

CREATE FUNCTION sre_m25_budget_post_account() RETURNS trigger
LANGUAGE plpgsql AS $m25_account$
BEGIN
  IF TG_OP='INSERT' THEN
    UPDATE sre_live_budget_accounts
       SET reserved_usd=reserved_usd+NEW.amount_usd,updated_at=clock_timestamp()
     WHERE project_id=NEW.project_id
       AND cap_usd>=spent_usd+reserved_usd+NEW.amount_usd;
    IF NOT FOUND THEN RAISE EXCEPTION 'M25 project USD cap exhausted'; END IF;
    INSERT INTO sre_live_budget_events(project_id,reservation_id,event_kind,amount_usd)
      VALUES (NEW.project_id,NEW.reservation_id,'reserved',NEW.amount_usd);
  ELSIF TG_OP='UPDATE' THEN
    UPDATE sre_live_budget_accounts
       SET reserved_usd=reserved_usd-OLD.amount_usd,
           released_usd=released_usd+OLD.amount_usd,
           updated_at=clock_timestamp()
     WHERE project_id=NEW.project_id AND reserved_usd>=OLD.amount_usd;
    IF NOT FOUND THEN RAISE EXCEPTION 'M25 reserve underflow'; END IF;
    INSERT INTO sre_live_budget_events(project_id,reservation_id,event_kind,amount_usd)
      VALUES (NEW.project_id,NEW.reservation_id,'released',NEW.amount_usd);
  ELSIF TG_OP='DELETE' THEN
    -- Privacy cascade: budget may itself already be deleting. Never erase a
    -- possible dispatch witness, even through upstream quote retention purge.
    PERFORM 1 FROM sre_live_attempt_fences
      WHERE project_id=OLD.project_id AND run_id=OLD.run_id
        AND item_id=OLD.item_id AND state IN (
          'dispatch_intent','unknown','completed','failed_known');
    IF FOUND THEN RAISE EXCEPTION 'M25 paid/unknown reserve cannot be purged'; END IF;
    IF OLD.state='reserved' THEN
      UPDATE sre_live_budget_accounts SET
        reserved_usd=reserved_usd-OLD.amount_usd,
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
$m25_account$;
CREATE TRIGGER sre_m25_reservation_after_insert
  AFTER INSERT ON sre_live_budget_reservations
  FOR EACH ROW EXECUTE FUNCTION sre_m25_budget_post_account();
CREATE TRIGGER sre_m25_reservation_after_update
  AFTER UPDATE ON sre_live_budget_reservations
  FOR EACH ROW EXECUTE FUNCTION sre_m25_budget_post_account();
CREATE TRIGGER sre_m25_reservation_after_delete
  AFTER DELETE ON sre_live_budget_reservations
  FOR EACH ROW EXECUTE FUNCTION sre_m25_budget_post_account();

CREATE FUNCTION sre_m25_event_no_mutation() RETURNS trigger
LANGUAGE plpgsql AS $m25_events$
BEGIN
  RAISE EXCEPTION 'M25 financial journal is append-only';
END;
$m25_events$;
-- Audit UPDATE is forbidden. DELETE remains permitted for project-wide
-- privacy retention and parent CASCADE; only trusted table owner can do it.
CREATE TRIGGER sre_m25_budget_events_immutable
  BEFORE UPDATE ON sre_live_budget_events
  FOR EACH ROW EXECUTE FUNCTION sre_m25_event_no_mutation();
-- Table owner privileges are trusted internal services only; PUBLIC never
-- receives finance writes and host ACLs remain mandatory.
REVOKE ALL ON sre_live_budget_accounts FROM PUBLIC;
REVOKE ALL ON sre_live_budget_reservations FROM PUBLIC;
REVOKE ALL ON sre_live_budget_events FROM PUBLIC;
