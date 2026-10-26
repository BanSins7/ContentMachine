-- RTB FIX-05-C: opt-in, separately priced Deep V2 expansions (maximum two).
-- Extends only RTB-owned append-only ledger; leaves B9, A8 and Deep V1 intact.

CREATE TABLE tb_deep_v2_expansion_quotes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  expansion_no SMALLINT NOT NULL CHECK(expansion_no BETWEEN 1 AND 2),
  research_input_sha256 TEXT NOT NULL CHECK(research_input_sha256 ~ '^[0-9a-f]{64}$'),
  previous_tree_sha256 TEXT NOT NULL CHECK(previous_tree_sha256 ~ '^[0-9a-f]{64}$'),
  quality_sha256 TEXT NOT NULL CHECK(quality_sha256 ~ '^[0-9a-f]{64}$'),
  quote_sha256 TEXT NOT NULL CHECK(quote_sha256 ~ '^[0-9a-f]{64}$'),
  request_sha256 TEXT NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
  prompt_sha256 TEXT NOT NULL CHECK(prompt_sha256 ~ '^[0-9a-f]{64}$'),
  model_id TEXT NOT NULL CHECK(model_id='anthropic/claude-opus-5.5'),
  catalog_version_id BIGINT NOT NULL REFERENCES tb_model_snapshots(id),
  catalog_sha256 TEXT NOT NULL CHECK(catalog_sha256 ~ '^[0-9a-f]{64}$'),
  max_output_tokens INTEGER NOT NULL CHECK(max_output_tokens BETWEEN 4096 AND 16000),
  reservation_usd NUMERIC(20,12) NOT NULL CHECK(reservation_usd>0),
  quote JSONB NOT NULL CHECK(jsonb_typeof(quote)='object'),
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT (clock_timestamp()+interval '30 minutes'),
  UNIQUE(project_id,id),
  UNIQUE(project_id,id,run_id,expansion_no),
  FOREIGN KEY(project_id,run_id) REFERENCES tb_deep_v2_runs(project_id,id),
  FOREIGN KEY(project_id,created_by)
    REFERENCES knowledge_fact_review_authorizations(project_id,reviewer_id),
  CHECK(quote->>'schema_version'='rtb-deep-v2-expansion-quote/v1'),
  CHECK(quote->>'quote_sha256'=quote_sha256),
  CHECK(quote->>'research_input_sha256'=research_input_sha256),
  CHECK(quote->>'request_sha256'=request_sha256),
  CHECK(quote->>'prompt_sha256'=prompt_sha256),
  CHECK(quote->>'previous_tree_sha256'=previous_tree_sha256),
  CHECK(quote->>'quality_sha256'=quality_sha256),
  CHECK(quote->>'run_id'=run_id::text),
  CHECK((quote->>'expansion_no')::smallint=expansion_no),
  CHECK((quote->>'reservation_usd')::numeric=reservation_usd),
  CHECK(quote->>'catalog_sha256'=catalog_sha256)
);

CREATE FUNCTION tb_deep_v2_expansion_quote_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r RECORD;
BEGIN
  SELECT status,expansion_count INTO r FROM tb_deep_v2_runs
    WHERE project_id=NEW.project_id AND id=NEW.run_id FOR UPDATE;
  IF NOT FOUND OR r.status<>'expand_pending'
     OR NEW.expansion_no<>r.expansion_count+1
     OR NEW.expires_at<=NEW.created_at
     OR EXISTS (SELECT 1 FROM tb_deep_v2_expansion_consents c
                WHERE c.project_id=NEW.project_id AND c.run_id=NEW.run_id
                  AND c.expansion_no=NEW.expansion_no)
     OR NOT EXISTS (
       SELECT 1 FROM knowledge_fact_review_authorizations a
       WHERE a.project_id=NEW.project_id AND a.reviewer_id=NEW.created_by
         AND a.enabled=true)
     OR NOT EXISTS (
       SELECT 1 FROM tb_model_snapshots m
       WHERE m.id=NEW.catalog_version_id AND m.model_id=NEW.model_id
         AND m.catalog_sha256=NEW.catalog_sha256) THEN
    RAISE EXCEPTION 'RTB V2 expansion quote state, actor or catalog mismatch';
  END IF;
  RETURN NEW;
END $$;
-- Quote trigger is added after creating consent table so PL/pgSQL sees it.

CREATE TABLE tb_deep_v2_expansion_consents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  quote_id UUID NOT NULL,
  run_id UUID NOT NULL,
  expansion_no SMALLINT NOT NULL CHECK(expansion_no BETWEEN 1 AND 2),
  approved_by TEXT NOT NULL,
  authorized_usd NUMERIC(20,12) NOT NULL CHECK(authorized_usd>0),
  acknowledged_quote_sha256 TEXT NOT NULL CHECK(acknowledged_quote_sha256 ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,run_id,expansion_no),
  FOREIGN KEY(project_id,quote_id,run_id,expansion_no)
    REFERENCES tb_deep_v2_expansion_quotes(project_id,id,run_id,expansion_no),
  FOREIGN KEY(project_id,approved_by)
    REFERENCES knowledge_fact_review_authorizations(project_id,reviewer_id)
);

CREATE TABLE tb_deep_v2_expansion_revocations (
  project_id TEXT NOT NULL,
  consent_id UUID NOT NULL,
  revoked_by TEXT NOT NULL,
  revoked_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,consent_id),
  FOREIGN KEY(project_id,consent_id)
    REFERENCES tb_deep_v2_expansion_consents(project_id,id)
);

CREATE TRIGGER tb_deep_v2_expansion_quote_check BEFORE INSERT
 ON tb_deep_v2_expansion_quotes FOR EACH ROW
 EXECUTE FUNCTION tb_deep_v2_expansion_quote_guard();

CREATE FUNCTION tb_deep_v2_expansion_consent_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE q RECORD; r RECORD;
BEGIN
  SELECT quote_sha256,reservation_usd,expires_at INTO q
    FROM tb_deep_v2_expansion_quotes
   WHERE project_id=NEW.project_id AND id=NEW.quote_id FOR UPDATE;
  SELECT status,expansion_count,consent_id INTO r
    FROM tb_deep_v2_runs
   WHERE project_id=NEW.project_id AND id=NEW.run_id FOR UPDATE;
  IF NOT FOUND OR r.status<>'expand_pending'
     OR NEW.expansion_no<>r.expansion_count+1
     OR NEW.acknowledged_quote_sha256<>q.quote_sha256
     OR NEW.authorized_usd<q.reservation_usd
     OR NEW.authorized_usd-q.reservation_usd>=0.000001
     OR clock_timestamp()>=q.expires_at
     OR EXISTS (SELECT 1 FROM tb_deep_v2_revocations b
       WHERE b.project_id=NEW.project_id AND b.consent_id=r.consent_id)
     OR NOT EXISTS (
       SELECT 1 FROM knowledge_fact_review_authorizations a
        WHERE a.project_id=NEW.project_id AND a.reviewer_id=NEW.approved_by
          AND a.enabled=true) THEN
    RAISE EXCEPTION 'RTB V2 expansion consent invalid, stale or revoked';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tb_deep_v2_expansion_consent_check BEFORE INSERT
 ON tb_deep_v2_expansion_consents FOR EACH ROW
 EXECUTE FUNCTION tb_deep_v2_expansion_consent_guard();

-- Replacing the existing trigger function is an additive RTB-only migration.
-- Authorized increases must be exactly auditable signed Expansion consents.
CREATE OR REPLACE FUNCTION tb_deep_v2_run_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE total_authorized NUMERIC(20,12);
BEGIN
  SELECT base.authorized_usd + COALESCE((
    SELECT SUM(extra.authorized_usd) FROM tb_deep_v2_expansion_consents extra
      WHERE extra.project_id=NEW.project_id AND extra.run_id=NEW.id
  ), 0) INTO total_authorized
  FROM tb_deep_v2_consents base
  WHERE base.project_id=NEW.project_id AND base.id=NEW.consent_id;
  IF (OLD.project_id,OLD.binding_id,OLD.quote_id,OLD.consent_id,
      OLD.idempotency_key,OLD.start_request_sha256,OLD.created_at)
     IS DISTINCT FROM
     (NEW.project_id,NEW.binding_id,NEW.quote_id,NEW.consent_id,
      NEW.idempotency_key,NEW.start_request_sha256,NEW.created_at)
     OR NEW.authorized_usd IS DISTINCT FROM total_authorized
     OR NEW.authorized_usd<OLD.authorized_usd
     OR NEW.spent_usd<OLD.spent_usd THEN
    RAISE EXCEPTION 'RTB Deep V2 run identity/budget provenance immutable';
  END IF;
  RETURN NEW;
END $$;

CREATE FUNCTION tb_deep_v2_expansion_grant() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  -- This AFTER INSERT is the sole permitted path to increase run budget.
  -- Same transaction: failure rolls back both consent and budget amendment.
  UPDATE tb_deep_v2_runs
     SET authorized_usd=authorized_usd+NEW.authorized_usd,
         updated_at=clock_timestamp()
   WHERE project_id=NEW.project_id AND id=NEW.run_id
     AND status='expand_pending' AND expansion_count=NEW.expansion_no-1;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'RTB Deep V2 expansion grant lost its pending state';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER tb_deep_v2_expansion_grant_after_insert AFTER INSERT
  ON tb_deep_v2_expansion_consents FOR EACH ROW
  EXECUTE FUNCTION tb_deep_v2_expansion_grant();

-- Replace stage insert guard to require stage-specific NEW paid consent,
-- rather than inheriting approval for only the original Pass1/Pass2.
CREATE OR REPLACE FUNCTION tb_deep_v2_check_attempt_insert() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r RECORD; expansion_no SMALLINT;
BEGIN
  SELECT status,reserved_usd,spent_usd,authorized_usd,consent_id,expansion_count
    INTO r FROM tb_deep_v2_runs
   WHERE project_id=NEW.project_id AND id=NEW.run_id FOR UPDATE;
  expansion_no := CASE NEW.stage
    WHEN 'deep_expand_1' THEN 1 WHEN 'deep_expand_2' THEN 2 ELSE 0 END;
  IF NOT FOUND OR NEW.status<>'reserved'
     OR r.reserved_usd<NEW.reservation_usd
     OR r.spent_usd+r.reserved_usd>r.authorized_usd
     OR NOT (
       (r.status='pass1_running' AND NEW.stage='deep_first') OR
       (r.status='pass2_running' AND NEW.stage='deep_second') OR
       (r.status='expand_running' AND expansion_no=r.expansion_count+1 AND expansion_no>0)
     )
     OR EXISTS (SELECT 1 FROM tb_deep_v2_revocations revoked
       WHERE revoked.project_id=NEW.project_id AND revoked.consent_id=r.consent_id)
     OR EXISTS (SELECT 1 FROM tb_deep_v2_attempts prior
       WHERE prior.project_id=NEW.project_id AND prior.run_id=NEW.run_id
         AND prior.status='reserved')
     OR (expansion_no>0 AND NOT EXISTS (
       SELECT 1 FROM tb_deep_v2_expansion_consents ec
       JOIN tb_deep_v2_expansion_quotes eq
         ON eq.project_id=ec.project_id AND eq.id=ec.quote_id
       WHERE ec.project_id=NEW.project_id AND ec.run_id=NEW.run_id
         AND ec.expansion_no=expansion_no
         AND eq.request_sha256=NEW.request_sha256
         AND eq.prompt_sha256=NEW.prompt_sha256
         AND eq.reservation_usd>=NEW.reservation_usd
         AND NOT EXISTS(SELECT 1 FROM tb_deep_v2_expansion_revocations rev
           WHERE rev.project_id=ec.project_id AND rev.consent_id=ec.id)))
  THEN
    RAISE EXCEPTION 'RTB Deep V2 paid attempt not consented or not reserved';
  END IF;
  RETURN NEW;
END $$;

CREATE TRIGGER tb_deep_v2_expansion_quote_immutable BEFORE UPDATE OR DELETE
 ON tb_deep_v2_expansion_quotes FOR EACH ROW
 EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER tb_deep_v2_expansion_consent_immutable BEFORE UPDATE OR DELETE
 ON tb_deep_v2_expansion_consents FOR EACH ROW
 EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER tb_deep_v2_expansion_revocation_immutable BEFORE UPDATE OR DELETE
 ON tb_deep_v2_expansion_revocations FOR EACH ROW
 EXECUTE FUNCTION psi_block_immutable_write();

-- No job trigger invokes OpenRouter. Every expansion must be explicitly
-- quoted, approved and executed; at most two by run CHECK + stage UNIQUE.
