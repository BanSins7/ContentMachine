-- PIE-MOD-29 additive; retain immutable 200/201/202 migration checksums.
-- Claims are durable BEFORE HTTP dispatch: one irreversible attempt => one send.
CREATE TABLE pie_model_dispatch_claims (
  project_id TEXT NOT NULL,
  attempt_id UUID NOT NULL,
  authorization_id UUID NOT NULL,
  request_sha256 TEXT NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
  quote_sha256 TEXT NOT NULL CHECK(quote_sha256 ~ '^[0-9a-f]{64}$'),
  prompt_sha256 TEXT NOT NULL CHECK(prompt_sha256 ~ '^[0-9a-f]{64}$'),
  catalog_sha256 TEXT NOT NULL CHECK(catalog_sha256 ~ '^[0-9a-f]{64}$'),
  request_payload_sha256 TEXT NOT NULL CHECK(request_payload_sha256 ~ '^[0-9a-f]{64}$'),
  model_slug TEXT NOT NULL CHECK(length(model_slug) BETWEEN 3 AND 120),
  max_output_tokens INTEGER NOT NULL CHECK(max_output_tokens BETWEEN 1 AND 4096),
  claimed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,attempt_id),
  UNIQUE(project_id,request_sha256),
  FOREIGN KEY(project_id,attempt_id) REFERENCES pie_model_attempts(project_id,id) ON DELETE CASCADE,
  FOREIGN KEY(project_id,authorization_id) REFERENCES pie_spend_authorizations(project_id,id) ON DELETE CASCADE
);

-- The outcome is observed/provider-reported only; not proof that a credit
-- card or provider settlement is final. Null actual cost => quarantine.
CREATE TABLE pie_model_dispatch_outcomes (
  project_id TEXT NOT NULL,
  attempt_id UUID NOT NULL,
  result_sha256 TEXT NOT NULL CHECK(result_sha256 ~ '^[0-9a-f]{64}$'),
  outcome TEXT NOT NULL CHECK(outcome IN ('reported_charged','uncertain_charge')),
  reported_cost_usd NUMERIC(20,12),
  provider_generation_id TEXT CHECK(provider_generation_id IS NULL OR length(provider_generation_id) BETWEEN 1 AND 160),
  response_sha256 TEXT CHECK(response_sha256 IS NULL OR response_sha256 ~ '^[0-9a-f]{64}$'),
  reported_prompt_tokens INTEGER CHECK(reported_prompt_tokens IS NULL OR reported_prompt_tokens>=0),
  reported_completion_tokens INTEGER CHECK(reported_completion_tokens IS NULL OR reported_completion_tokens>=0),
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,attempt_id),
  FOREIGN KEY(project_id,attempt_id) REFERENCES pie_model_dispatch_claims(project_id,attempt_id) ON DELETE CASCADE,
  CHECK ((outcome='uncertain_charge' AND reported_cost_usd IS NULL AND response_sha256 IS NULL)
      OR (outcome='reported_charged' AND reported_cost_usd IS NOT NULL
          AND reported_cost_usd>=0 AND response_sha256 IS NOT NULL
          AND reported_prompt_tokens IS NOT NULL AND reported_completion_tokens IS NOT NULL))
);
-- A direct SQL writer cannot rebind a claim to a different approval or
-- request digest than the original immutable budget reservation.
CREATE FUNCTION pie_model_dispatch_claim_guard() RETURNS trigger LANGUAGE plpgsql AS $pie29$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pie_model_attempts a
    WHERE a.project_id=NEW.project_id AND a.id=NEW.attempt_id
      AND a.authorization_id=NEW.authorization_id
      AND a.request_sha256=NEW.request_sha256
  ) THEN
    RAISE EXCEPTION 'PIE dispatch claim must match original budget reservation';
  END IF;
  RETURN NEW;
END $pie29$;
CREATE TRIGGER pie_model_dispatch_claim_guard_insert
 BEFORE INSERT ON pie_model_dispatch_claims FOR EACH ROW
 EXECUTE FUNCTION pie_model_dispatch_claim_guard();

-- A provider-reported cost greater than its held reservation is not a
-- valid charged outcome, even for a direct SQL writer bypassing Python.
CREATE FUNCTION pie_model_dispatch_reported_cost_guard() RETURNS trigger LANGUAGE plpgsql AS $pie29$
DECLARE held NUMERIC(20,12);
BEGIN
  SELECT a.reserved_usd INTO held FROM pie_model_attempts a
    WHERE a.project_id=NEW.project_id AND a.id=NEW.attempt_id;
  IF NOT FOUND OR (NEW.outcome='reported_charged' AND NEW.reported_cost_usd>held) THEN
    RAISE EXCEPTION 'PIE provider-reported usage exceeds or lacks reservation';
  END IF;
  RETURN NEW;
END $pie29$;
CREATE TRIGGER pie_model_dispatch_cost_guard_insert
 BEFORE INSERT ON pie_model_dispatch_outcomes FOR EACH ROW
 EXECUTE FUNCTION pie_model_dispatch_reported_cost_guard();

CREATE INDEX pie_model_dispatch_claims_project ON pie_model_dispatch_claims(project_id,claimed_at DESC);

CREATE TRIGGER pie_model_dispatch_claims_immutable
 BEFORE UPDATE ON pie_model_dispatch_claims FOR EACH ROW
 EXECUTE FUNCTION pie_reject_evidence_update();
CREATE TRIGGER pie_model_dispatch_outcomes_immutable
 BEFORE UPDATE ON pie_model_dispatch_outcomes FOR EACH ROW
 EXECUTE FUNCTION pie_reject_evidence_update();

-- No automatic retry, reassignment or reservation release on uncertain billing.
-- An administrator must reconcile externally against provider billing records.
