-- M26: one durable LIVE Opus dispatch intent per exact M20 request.
-- Forward-only 061. This is a technical pre-send fence, never HTTP transport.
-- No result, receipt, billable tokens or budget settlement created here.
CREATE UNIQUE INDEX sre_m26_reserved_owner
  ON sre_live_budget_reservations
     (project_id,run_id,item_id,quote_sha256,reservation_id);

CREATE UNIQUE INDEX sre_m26_attempt_owner
  ON sre_live_attempt_fences
     (project_id,run_id,item_id,attempt_id,request_sha256,
      worker_token,lease_generation);

CREATE TABLE sre_live_opus_dispatch_intents (
  intent_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  item_id BIGINT NOT NULL,
  batch_id UUID NOT NULL,
  reservation_id UUID NOT NULL,
  quote_sha256 TEXT NOT NULL CHECK (quote_sha256 ~ '^[0-9a-f]{64}$'),
  consent_id UUID NOT NULL,
  attempt_id UUID NOT NULL,
  worker_token UUID NOT NULL,
  lease_generation BIGINT NOT NULL CHECK (lease_generation>=1),
  request_ordinal INTEGER NOT NULL CHECK (request_ordinal>=0),
  request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  batch_manifest_sha256 TEXT NOT NULL CHECK (batch_manifest_sha256 ~ '^[0-9a-f]{64}$'),
  model_id TEXT NOT NULL CHECK (model_id='anthropic/claude-opus-5.5'),
  model_revision TEXT NOT NULL CHECK (length(model_revision) BETWEEN 6 AND 120),
  catalog_sha256 TEXT NOT NULL CHECK (catalog_sha256 ~ '^[0-9a-f]{64}$'),
  endpoint_tag TEXT NOT NULL CHECK (length(endpoint_tag) BETWEEN 1 AND 120),
  reserved_usd NUMERIC(24,9) NOT NULL CHECK (reserved_usd>0),
  operator_actor TEXT NOT NULL CHECK (operator_actor ~ '^[A-Za-z0-9:_@.-]{1,128}$'),
  intent_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  -- One attempt and one request identity can never be dispatched twice.
  UNIQUE (project_id,attempt_id),
  UNIQUE (project_id,run_id,item_id,request_sha256),
  UNIQUE (project_id,reservation_id),
  FOREIGN KEY (project_id,run_id,item_id,quote_sha256,reservation_id)
    REFERENCES sre_live_budget_reservations
      (project_id,run_id,item_id,quote_sha256,reservation_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (project_id,run_id,item_id,attempt_id,request_sha256,
               worker_token,lease_generation)
    REFERENCES sre_live_attempt_fences
      (project_id,run_id,item_id,attempt_id,request_sha256,
       worker_token,lease_generation)
    ON DELETE RESTRICT,
  FOREIGN KEY (batch_id,run_id,project_id,item_id)
    REFERENCES sre_live_batch_members(batch_id,run_id,project_id,item_id)
    ON DELETE RESTRICT,
  FOREIGN KEY (project_id,run_id,item_id,quote_sha256,consent_id)
    REFERENCES sre_live_opus_consents(project_id,run_id,item_id,quote_sha256,consent_id)
    ON DELETE RESTRICT
);
CREATE INDEX sre_m26_intent_run_time
  ON sre_live_opus_dispatch_intents(project_id,run_id,intent_at,intent_id);

-- A transaction cannot create an orphan/synthetic paid-intent record merely
-- by supplying valid UUIDs or bypassing Python. It must follow M15 fence
-- and M25 exact consent-bound reserved funds while the item lease is valid.
CREATE FUNCTION sre_m26_validate_intent() RETURNS trigger
LANGUAGE plpgsql AS $m26_guard$
DECLARE
  proof RECORD;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'M26 committed Opus intent is immutable and undeletable';
  END IF;
  PERFORM 1 FROM sre_workspaces WHERE project_id=NEW.project_id FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'M26 project workspace missing'; END IF;
  SELECT a.state AS attempt_state,a.intent_at AS attempt_time,
         i.state AS item_state,i.lease_until,i.worker_token AS current_token,
         i.lease_generation AS current_generation,
         r.execution_mode,r.state AS run_state,r.cancel_requested,
         b.state AS batch_state,rs.state AS budget_state,
         rs.amount_usd,rs.consent_id AS reserved_consent,
         q.batch_manifest_sha256 AS quote_batch,
         q.catalog_sha256 AS quote_catalog,
         q.max_usd AS quote_max,
         c.max_usd AS consent_max,c.revoked_at,c.expires_at AS consent_expires,
         q.expires_at AS quote_expires
    INTO proof
  FROM sre_live_attempt_fences a
  JOIN sre_live_items i
    ON (i.project_id,i.run_id,i.id)=(a.project_id,a.run_id,a.item_id)
  JOIN sre_live_runs r
    ON (r.project_id,r.run_id)=(i.project_id,i.run_id)
  JOIN sre_live_batch_members m
    ON (m.project_id,m.run_id,m.item_id,m.batch_id)=
       (a.project_id,a.run_id,a.item_id,a.batch_id)
  JOIN sre_live_batches b
    ON (b.project_id,b.run_id,b.batch_id)=
       (m.project_id,m.run_id,m.batch_id)
  JOIN sre_live_budget_reservations rs
    ON (rs.project_id,rs.run_id,rs.item_id,rs.quote_sha256,rs.reservation_id)=
       (NEW.project_id,NEW.run_id,NEW.item_id,NEW.quote_sha256,NEW.reservation_id)
  JOIN sre_live_opus_consents c
    ON (c.project_id,c.run_id,c.item_id,c.quote_sha256,c.consent_id)=
       (rs.project_id,rs.run_id,rs.item_id,rs.quote_sha256,rs.consent_id)
  JOIN sre_live_opus_quotes q
    ON (q.project_id,q.run_id,q.item_id,q.quote_sha256)=
       (c.project_id,c.run_id,c.item_id,c.quote_sha256)
  WHERE (a.project_id,a.run_id,a.item_id,a.batch_id,a.attempt_id,
         a.request_sha256,a.worker_token,a.lease_generation)=
        (NEW.project_id,NEW.run_id,NEW.item_id,NEW.batch_id,NEW.attempt_id,
         NEW.request_sha256,NEW.worker_token,NEW.lease_generation)
  FOR SHARE OF a,i,r,b,rs,c,q;
  IF NOT FOUND OR proof.attempt_state <> 'dispatch_intent'
     OR proof.attempt_time IS NULL OR proof.attempt_time > clock_timestamp()
     OR proof.execution_mode <> 'live'
     OR proof.run_state NOT IN ('queued','running') OR proof.cancel_requested
     OR proof.item_state <> 'running'
     OR proof.batch_state NOT IN ('queued','running')
     OR proof.current_token IS DISTINCT FROM NEW.worker_token
     OR proof.current_generation <> NEW.lease_generation
     OR proof.lease_until <= clock_timestamp()
     OR proof.budget_state <> 'reserved'
     OR proof.reserved_consent IS DISTINCT FROM NEW.consent_id
     OR proof.amount_usd <> NEW.reserved_usd
     OR proof.consent_max <> NEW.reserved_usd
     OR proof.quote_max <> NEW.reserved_usd
     OR proof.revoked_at IS NOT NULL OR proof.consent_expires <= clock_timestamp()
     OR proof.quote_expires <= clock_timestamp()
     OR proof.quote_batch <> NEW.batch_manifest_sha256
     OR proof.quote_catalog <> NEW.catalog_sha256 THEN
    RAISE EXCEPTION 'M26 active lease, approved quote, reserved cap and committed attempt required';
  END IF;
  IF NEW.intent_at < proof.attempt_time - INTERVAL '30 seconds'
     OR NEW.intent_at > clock_timestamp() + INTERVAL '30 seconds' THEN
    RAISE EXCEPTION 'M26 untrusted intent timestamp';
  END IF;
  -- A prior possible charge anywhere in this LIVE run prohibits ANY
  -- second M26 send intent, even on another item or request.
  PERFORM 1 FROM sre_live_opus_dispatch_intents
    WHERE project_id=NEW.project_id AND run_id=NEW.run_id;
  IF FOUND THEN
    RAISE EXCEPTION 'M26 outstanding or prior Opus send intent; reconciliation required';
  END IF;
  RETURN NEW;
END;
$m26_guard$;
CREATE TRIGGER sre_m26_intent_before_insert
  BEFORE INSERT ON sre_live_opus_dispatch_intents
  FOR EACH ROW EXECUTE FUNCTION sre_m26_validate_intent();
CREATE TRIGGER sre_m26_intent_no_mutation
  BEFORE UPDATE OR DELETE ON sre_live_opus_dispatch_intents
  FOR EACH ROW EXECUTE FUNCTION sre_m26_validate_intent();
REVOKE ALL ON sre_live_opus_dispatch_intents FROM PUBLIC;
