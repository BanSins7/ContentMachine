-- Stage14: a NEW cost/consent/execution ledger for knowledge_snapshot_v1.
-- Never reuse legacy RTB or MiMo consent; no job triggers or implicit API calls.
CREATE TABLE knowledge_c_generation_quotes (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  snapshot_id UUID NOT NULL,
  handoff_id UUID NOT NULL,
  snapshot_sha256 TEXT NOT NULL CHECK(snapshot_sha256 ~ '^[0-9a-f]{64}$'),
  quote_sha256 TEXT NOT NULL CHECK(quote_sha256 ~ '^[0-9a-f]{64}$'),
  request_sha256 TEXT NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
  model_id TEXT NOT NULL,
  catalog_version_id BIGINT NOT NULL,
  catalog_sha256 TEXT NOT NULL CHECK(catalog_sha256 ~ '^[0-9a-f]{64}$'),
  stage_config JSONB NOT NULL CHECK(jsonb_typeof(stage_config)='object'),
  model_snapshot JSONB NOT NULL CHECK(jsonb_typeof(model_snapshot)='object'),
  quoted_usd NUMERIC(20,12) NOT NULL CHECK(quoted_usd > 0),
  quote JSONB NOT NULL CHECK(jsonb_typeof(quote)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  expires_at TIMESTAMPTZ NOT NULL DEFAULT (clock_timestamp()+interval '30 minutes'),
  UNIQUE(project_id,id),
  UNIQUE(project_id,id,snapshot_id,handoff_id),
  FOREIGN KEY(project_id,snapshot_id,snapshot_sha256)
    REFERENCES knowledge_approved_snapshots(project_id,id,snapshot_sha256),
  FOREIGN KEY(project_id,handoff_id)
    REFERENCES knowledge_c_handoffs(project_id,id),
  CHECK(quote->>'input_mode'='knowledge_snapshot_v1'),
  CHECK(quote->>'snapshot_id'=snapshot_id::text),
  CHECK(quote->>'handoff_id'=handoff_id::text),
  CHECK(quote->>'request_sha256'=request_sha256),
  CHECK(quote->>'model_id'=model_id)
);
CREATE INDEX ke_c_quotes_project ON knowledge_c_generation_quotes(project_id,created_at DESC);

CREATE TABLE knowledge_c_generation_consents (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  quote_id UUID NOT NULL,
  authorized_usd NUMERIC(20,12) NOT NULL CHECK(authorized_usd > 0),
  approved_by TEXT NOT NULL CHECK(length(btrim(approved_by)) BETWEEN 1 AND 160),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,quote_id),
  UNIQUE(project_id,id,quote_id),
  FOREIGN KEY(project_id,quote_id) REFERENCES knowledge_c_generation_quotes(project_id,id)
);
CREATE TABLE knowledge_c_generation_revocations (
  project_id TEXT NOT NULL,
  consent_id UUID NOT NULL,
  revoked_by TEXT NOT NULL CHECK(length(btrim(revoked_by)) BETWEEN 1 AND 160),
  revoked_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,consent_id),
  FOREIGN KEY(project_id,consent_id)
    REFERENCES knowledge_c_generation_consents(project_id,id)
);

CREATE TABLE knowledge_c_generation_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  quote_id UUID NOT NULL,
  consent_id UUID NOT NULL,
  snapshot_id UUID NOT NULL,
  handoff_id UUID NOT NULL,
  idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 12 AND 128),
  status TEXT NOT NULL DEFAULT 'generation_pending'
    CHECK(status IN ('generation_pending','generation_running','topic_review',
                     'approved','failed','uncertain','paused_budget','paused_price')),
  authorized_usd NUMERIC(20,12) NOT NULL CHECK(authorized_usd > 0),
  reserved_usd NUMERIC(20,12) NOT NULL DEFAULT 0 CHECK(reserved_usd >= 0),
  spent_usd NUMERIC(20,12) NOT NULL DEFAULT 0 CHECK(spent_usd >= 0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,consent_id),
  UNIQUE(project_id,idempotency_key),
  FOREIGN KEY(project_id,quote_id,snapshot_id,handoff_id)
    REFERENCES knowledge_c_generation_quotes(project_id,id,snapshot_id,handoff_id),
  FOREIGN KEY(project_id,consent_id,quote_id)
    REFERENCES knowledge_c_generation_consents(project_id,id,quote_id),
  CHECK(spent_usd <= authorized_usd OR status='paused_budget')
);
CREATE INDEX ke_c_runs_project ON knowledge_c_generation_runs(project_id,created_at DESC);

CREATE TABLE knowledge_c_generation_attempts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  status TEXT NOT NULL CHECK(status IN ('reserved','completed','failed','uncertain')),
  request_sha256 TEXT NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
  reservation_usd NUMERIC(20,12) NOT NULL CHECK(reservation_usd > 0),
  charged_usd NUMERIC(20,12) CHECK(charged_usd >= 0),
  usage JSONB,
  response_id TEXT,
  output JSONB,
  error_code TEXT,
  started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  ended_at TIMESTAMPTZ,
  UNIQUE(project_id,run_id),
  FOREIGN KEY(project_id,run_id)
    REFERENCES knowledge_c_generation_runs(project_id,id),
  CHECK((status='reserved' AND charged_usd IS NULL)
        OR status IN ('completed','failed','uncertain'))
);

CREATE TABLE knowledge_c_topic_reviews (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  reviewer_id TEXT NOT NULL,
  review_sha256 TEXT NOT NULL CHECK(review_sha256 ~ '^[0-9a-f]{64}$'),
  review JSONB NOT NULL CHECK(jsonb_typeof(review)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,run_id),
  FOREIGN KEY(project_id,run_id) REFERENCES knowledge_c_generation_runs(project_id,id),
  FOREIGN KEY(project_id,reviewer_id)
    REFERENCES knowledge_fact_review_authorizations(project_id,reviewer_id),
  CHECK(review->>'input_mode'='knowledge_snapshot_v1'),
  CHECK(review->>'run_id'=run_id::text),
  CHECK(review->>'reviewer_id'=reviewer_id)
);

-- Immutable money consents, quote pricing inputs and the final review. The run
-- and attempt are explicitly stateful, but every stage advances once only.
CREATE TRIGGER ke_c_quotes_immutable BEFORE UPDATE OR DELETE
 ON knowledge_c_generation_quotes FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_c_consents_immutable BEFORE UPDATE OR DELETE
 ON knowledge_c_generation_consents FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_c_revocations_immutable BEFORE UPDATE OR DELETE
 ON knowledge_c_generation_revocations FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_c_topic_reviews_immutable BEFORE UPDATE OR DELETE
 ON knowledge_c_topic_reviews FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE FUNCTION ke_c_guard_attempt() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.project_id IS DISTINCT FROM NEW.project_id OR
    OLD.run_id IS DISTINCT FROM NEW.run_id OR
    OLD.request_sha256 IS DISTINCT FROM NEW.request_sha256 OR
    OLD.reservation_usd IS DISTINCT FROM NEW.reservation_usd OR
    OLD.started_at IS DISTINCT FROM NEW.started_at OR
    OLD.status <> 'reserved' OR NEW.status = 'reserved' THEN
   RAISE EXCEPTION 'C attempt ledger is finalized or immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER ke_c_attempt_guard BEFORE UPDATE ON knowledge_c_generation_attempts
 FOR EACH ROW EXECUTE FUNCTION ke_c_guard_attempt();
CREATE TRIGGER ke_c_attempt_no_delete BEFORE DELETE ON knowledge_c_generation_attempts
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE FUNCTION ke_c_guard_run() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF (OLD.project_id,OLD.quote_id,OLD.consent_id,OLD.snapshot_id,OLD.handoff_id,
     OLD.idempotency_key,OLD.authorized_usd,OLD.created_at)
      IS DISTINCT FROM
    (NEW.project_id,NEW.quote_id,NEW.consent_id,NEW.snapshot_id,NEW.handoff_id,
     NEW.idempotency_key,NEW.authorized_usd,NEW.created_at) THEN
   RAISE EXCEPTION 'C run identity immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER ke_c_run_guard BEFORE UPDATE ON knowledge_c_generation_runs
 FOR EACH ROW EXECUTE FUNCTION ke_c_guard_run();
CREATE TRIGGER ke_c_run_no_delete BEFORE DELETE ON knowledge_c_generation_runs
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
