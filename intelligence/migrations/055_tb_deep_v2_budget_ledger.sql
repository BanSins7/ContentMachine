-- RTB-FIX-02-B2/C schema foundation: separate Deep V2 price/consent/run ledger.
-- No historical tb_runs or B9 single-pass Flat C tables are altered.
-- This migration DOES NOT authorize or call any provider.
CREATE TABLE tb_deep_v2_quotes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id),
  binding_id UUID NOT NULL,
  research_input_sha256 TEXT NOT NULL CHECK(research_input_sha256 ~ '^[0-9a-f]{64}$'),
  quote_sha256 TEXT NOT NULL CHECK(quote_sha256 ~ '^[0-9a-f]{64}$'),
  request_sha256 TEXT NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
  prompt_sha256 TEXT NOT NULL CHECK(prompt_sha256 ~ '^[0-9a-f]{64}$'),
  prompt_version TEXT NOT NULL CHECK(prompt_version='deep-v2/1'),
  model_id TEXT NOT NULL CHECK(model_id='anthropic/claude-opus-5.5'),
  catalog_version_id BIGINT NOT NULL REFERENCES tb_model_snapshots(id),
  catalog_sha256 TEXT NOT NULL CHECK(catalog_sha256 ~ '^[0-9a-f]{64}$'),
  model_snapshot JSONB NOT NULL CHECK(jsonb_typeof(model_snapshot)='object'),
  stage_config JSONB NOT NULL CHECK(jsonb_typeof(stage_config)='object'),
  quote JSONB NOT NULL CHECK(jsonb_typeof(quote)='object'),
  required_pass1_reservation_usd NUMERIC(20,12) NOT NULL
    CHECK(required_pass1_reservation_usd>0),
  required_pass2_reservation_usd NUMERIC(20,12) NOT NULL
    CHECK(required_pass2_reservation_usd>0),
  quoted_usd NUMERIC(20,12) NOT NULL
    CHECK(quoted_usd>=required_pass1_reservation_usd+required_pass2_reservation_usd),
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT (clock_timestamp()+interval '30 minutes'),
  UNIQUE(project_id,id),
  UNIQUE(project_id,id,binding_id),
  FOREIGN KEY(project_id,binding_id,research_input_sha256)
    REFERENCES tb_deep_v2_research_bindings(project_id,id,research_input_sha256),
  FOREIGN KEY(project_id,created_by)
    REFERENCES knowledge_fact_review_authorizations(project_id,reviewer_id),
  CHECK(length(btrim(created_by)) BETWEEN 1 AND 160),
  CHECK(quote->>'schema_version'='rtb-deep-v2-quote/v1'),
  CHECK(quote->>'research_input_sha256'=research_input_sha256),
  CHECK(quote->>'binding_id'=binding_id::text),
  CHECK(quote->>'quote_sha256'=quote_sha256),
  CHECK(quote->>'request_sha256'=request_sha256),
  CHECK(quote->>'prompt_sha256'=prompt_sha256),
  CHECK(quote->>'prompt_version'=prompt_version),
  CHECK(quote->>'model_id'=model_id),
  CHECK(quote->>'catalog_sha256'=catalog_sha256),
  CHECK(quote->>'quoted_usd'=quoted_usd::text),
  CHECK(model_snapshot->>'model_id'=model_id),
  CHECK(model_snapshot->>'catalog_sha256'=catalog_sha256),
  CHECK(stage_config->>'prompt_version'=prompt_version),
  CHECK(stage_config->>'model_id'=model_id)
);
CREATE INDEX tb_deep_v2_quotes_by_project
 ON tb_deep_v2_quotes(project_id,created_at DESC);

-- Quotes are admitted only with a verified, matching stored model snapshot
-- and an already-enabled reviewer. The future repository additionally checks
-- the current catalog freshness/price at authorization and dispatch.
CREATE FUNCTION tb_deep_v2_check_quote() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.expires_at<=NEW.created_at THEN
   RAISE EXCEPTION 'RTB Deep V2 quote expiry invalid';
 END IF;
 IF NOT EXISTS(
   SELECT 1 FROM tb_model_snapshots m
   WHERE m.id=NEW.catalog_version_id
     AND m.model_id=NEW.model_id
     AND m.catalog_sha256=NEW.catalog_sha256
     AND m.capabilities=NEW.model_snapshot->'capabilities'
     AND m.pricing=NEW.model_snapshot->'pricing'
 ) THEN
   RAISE EXCEPTION 'RTB Deep V2 catalog mismatch';
 END IF;
 IF NOT EXISTS(
   SELECT 1 FROM knowledge_fact_review_authorizations a
   WHERE a.project_id=NEW.project_id
     AND a.reviewer_id=NEW.created_by AND a.enabled=true
 ) THEN
   RAISE EXCEPTION 'RTB Deep V2 quote reviewer not authorized';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tb_deep_v2_quote_guard BEFORE INSERT ON tb_deep_v2_quotes
 FOR EACH ROW EXECUTE FUNCTION tb_deep_v2_check_quote();
CREATE TRIGGER tb_deep_v2_quote_immutable BEFORE UPDATE OR DELETE
 ON tb_deep_v2_quotes FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();

CREATE TABLE tb_deep_v2_consents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  quote_id UUID NOT NULL,
  authorized_usd NUMERIC(20,12) NOT NULL CHECK(authorized_usd>0),
  approved_by TEXT NOT NULL CHECK(length(btrim(approved_by)) BETWEEN 1 AND 160),
  acknowledged_quote_sha256 TEXT NOT NULL
    CHECK(acknowledged_quote_sha256 ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,quote_id),
  UNIQUE(project_id,id,quote_id),
  FOREIGN KEY(project_id,quote_id)
    REFERENCES tb_deep_v2_quotes(project_id,id),
  FOREIGN KEY(project_id,approved_by)
    REFERENCES knowledge_fact_review_authorizations(project_id,reviewer_id)
);
CREATE TABLE tb_deep_v2_revocations (
  project_id TEXT NOT NULL,
  consent_id UUID NOT NULL,
  revoked_by TEXT NOT NULL CHECK(length(btrim(revoked_by)) BETWEEN 1 AND 160),
  revoked_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,consent_id),
  FOREIGN KEY(project_id,consent_id)
    REFERENCES tb_deep_v2_consents(project_id,id)
);

CREATE FUNCTION tb_deep_v2_check_consent() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE q RECORD;
BEGIN
 SELECT quoted_usd,quote_sha256,expires_at INTO q
   FROM tb_deep_v2_quotes
  WHERE project_id=NEW.project_id AND id=NEW.quote_id FOR UPDATE;
 IF NOT FOUND OR clock_timestamp()>=q.expires_at
    OR NEW.acknowledged_quote_sha256<>q.quote_sha256
    OR NEW.authorized_usd<q.quoted_usd THEN
   RAISE EXCEPTION 'RTB Deep V2 quote expired or consent budget/hash mismatch';
 END IF;
 IF NOT EXISTS(
   SELECT 1 FROM knowledge_fact_review_authorizations a
    WHERE a.project_id=NEW.project_id
      AND a.reviewer_id=NEW.approved_by AND a.enabled=true
 ) THEN
   RAISE EXCEPTION 'RTB Deep V2 consent approver unauthorized';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tb_deep_v2_consent_guard BEFORE INSERT ON tb_deep_v2_consents
 FOR EACH ROW EXECUTE FUNCTION tb_deep_v2_check_consent();
CREATE TRIGGER tb_deep_v2_consent_immutable BEFORE UPDATE OR DELETE
 ON tb_deep_v2_consents FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER tb_deep_v2_revocation_immutable BEFORE UPDATE OR DELETE
 ON tb_deep_v2_revocations FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();

CREATE TABLE tb_deep_v2_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  binding_id UUID NOT NULL,
  quote_id UUID NOT NULL,
  consent_id UUID NOT NULL,
  idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 12 AND 128),
  start_request_sha256 TEXT NOT NULL CHECK(start_request_sha256 ~ '^[0-9a-f]{64}$'),
  status TEXT NOT NULL DEFAULT 'pass1_pending' CHECK(status IN(
    'pass1_pending','pass1_running','review1',
    'pass2_pending','pass2_running','expand_pending','expand_running','review2',
    'approved','failed','uncertain','blocked_quality','paused_budget',
    'paused_price','cancelled')),
  expansion_count SMALLINT NOT NULL DEFAULT 0 CHECK(expansion_count BETWEEN 0 AND 2),
  authorized_usd NUMERIC(20,12) NOT NULL CHECK(authorized_usd>0),
  reserved_usd NUMERIC(20,12) NOT NULL DEFAULT 0 CHECK(reserved_usd>=0),
  spent_usd NUMERIC(20,12) NOT NULL DEFAULT 0 CHECK(spent_usd>=0),
  quality_report JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,idempotency_key),
  UNIQUE(project_id,consent_id),
  FOREIGN KEY(project_id,quote_id,binding_id)
    REFERENCES tb_deep_v2_quotes(project_id,id,binding_id),
  FOREIGN KEY(project_id,consent_id,quote_id)
    REFERENCES tb_deep_v2_consents(project_id,id,quote_id),
  CHECK(spent_usd+reserved_usd<=authorized_usd OR status='paused_budget')
);
CREATE INDEX tb_deep_v2_runs_recent
 ON tb_deep_v2_runs(project_id,created_at DESC);
CREATE TABLE tb_deep_v2_attempts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  stage TEXT NOT NULL CHECK(stage IN
    ('deep_first','deep_second','deep_expand_1','deep_expand_2')),
  status TEXT NOT NULL CHECK(status IN('reserved','completed','error','uncertain')),
  request_sha256 TEXT NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
  prompt_sha256 TEXT NOT NULL CHECK(prompt_sha256 ~ '^[0-9a-f]{64}$'),
  reservation_usd NUMERIC(20,12) NOT NULL CHECK(reservation_usd>0),
  charged_usd NUMERIC(20,12) CHECK(charged_usd>=0),
  usage JSONB,
  response_id TEXT,
  output JSONB,
  error_code TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  finished_at TIMESTAMPTZ,
  UNIQUE(project_id,run_id,stage),
  FOREIGN KEY(project_id,run_id)
    REFERENCES tb_deep_v2_runs(project_id,id),
  CHECK((status='reserved' AND charged_usd IS NULL AND finished_at IS NULL)
     OR status IN('completed','error','uncertain'))
);
CREATE INDEX tb_deep_v2_attempts_recent
 ON tb_deep_v2_attempts(project_id,run_id,started_at);

-- Runs may change *only* lifecycle and budget, not source/consent identity.
CREATE FUNCTION tb_deep_v2_run_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (OLD.project_id,OLD.binding_id,OLD.quote_id,OLD.consent_id,
     OLD.idempotency_key,OLD.start_request_sha256,OLD.authorized_usd,OLD.created_at)
    IS DISTINCT FROM
    (NEW.project_id,NEW.binding_id,NEW.quote_id,NEW.consent_id,
     NEW.idempotency_key,NEW.start_request_sha256,NEW.authorized_usd,NEW.created_at)
    OR NEW.spent_usd<OLD.spent_usd THEN
   RAISE EXCEPTION 'RTB Deep V2 run identity/spend immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tb_deep_v2_run_update_guard BEFORE UPDATE
 ON tb_deep_v2_runs FOR EACH ROW EXECUTE FUNCTION tb_deep_v2_run_guard();
CREATE TRIGGER tb_deep_v2_run_no_delete BEFORE DELETE
 ON tb_deep_v2_runs FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();

CREATE FUNCTION tb_deep_v2_attempt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.status<>'reserved' OR NEW.status='reserved' OR
   (OLD.project_id,OLD.run_id,OLD.stage,OLD.request_sha256,OLD.prompt_sha256,
    OLD.reservation_usd,OLD.started_at) IS DISTINCT FROM
   (NEW.project_id,NEW.run_id,NEW.stage,NEW.request_sha256,NEW.prompt_sha256,
    NEW.reservation_usd,NEW.started_at) THEN
   RAISE EXCEPTION 'RTB Deep V2 attempt is final or changed';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tb_deep_v2_attempt_update_guard BEFORE UPDATE
 ON tb_deep_v2_attempts FOR EACH ROW EXECUTE FUNCTION tb_deep_v2_attempt_guard();
CREATE TRIGGER tb_deep_v2_attempt_no_delete BEFORE DELETE
 ON tb_deep_v2_attempts FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();

-- No automatic job triggers. Every paid stage will require an explicit POST,
-- an active consent, a current price check and one transactionally reserved
-- attempt. Until that repository exists these tables are deliberately dormant.
