-- GB-MOD-10: additive, deny-by-default project and run spend policies.
-- 005/055/056 must not be rewritten. This migration owns NO model dispatch.
CREATE TABLE group_b_budget_project_limits (
 project_id TEXT PRIMARY KEY REFERENCES project_registry(id),
 enabled BOOLEAN NOT NULL DEFAULT FALSE,
 lifetime_ceiling_usd NUMERIC(20,12) NOT NULL CHECK(lifetime_ceiling_usd>0),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE group_b_budget_run_limits (
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 enabled BOOLEAN NOT NULL DEFAULT FALSE,
 lifetime_ceiling_usd NUMERIC(20,12) NOT NULL CHECK(lifetime_ceiling_usd>0),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(project_id,run_id),
 FOREIGN KEY(project_id,run_id) REFERENCES group_b_runs(project_id,id)
);
CREATE TABLE group_b_budget_events (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 attempt_id UUID NOT NULL,
 event_no INTEGER NOT NULL CHECK(event_no>0),
 event_type TEXT NOT NULL CHECK(event_type IN ('reserved','submitted','uncertain','settled','failed')),
 amount_usd NUMERIC(20,12) NOT NULL CHECK(amount_usd>=0),
 evidence_sha256 TEXT NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$'),
 provider_request_id TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,attempt_id,event_no),
 FOREIGN KEY(project_id,attempt_id) REFERENCES group_b_model_attempts(project_id,id),
 FOREIGN KEY(project_id,run_id) REFERENCES group_b_runs(project_id,id),
 CHECK (event_no<>1 OR event_type='reserved')
);
CREATE INDEX group_b_budget_events_run
 ON group_b_budget_events(project_id,run_id,created_at DESC);
CREATE FUNCTION group_b_budget_event_immutable() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Group B budget events immutable' USING ERRCODE='23514';
END $$;
CREATE TRIGGER trg_group_b_budget_events_immutable
 BEFORE UPDATE OR DELETE ON group_b_budget_events
 FOR EACH ROW EXECUTE FUNCTION group_b_budget_event_immutable();

-- Once an exact quote is reserved, no other request_key may consume it.
CREATE UNIQUE INDEX group_b_attempt_one_per_quote
 ON group_b_model_attempts(project_id,quote_id);
-- Provider request IDs must be unique across all attempts in a project.
CREATE UNIQUE INDEX group_b_attempt_provider_request_unique
 ON group_b_model_attempts(project_id,provider_request_id)
 WHERE provider_request_id IS NOT NULL;

-- Immutable identity for an attempt, monotonic lifecycle. No transition from
-- uncertain to failed without a verified no-charge reconciliation in Python.
-- Database does not trust itself to authenticate upstream provider receipts.
CREATE FUNCTION group_b_budget_attempt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='UPDATE' THEN
  IF (OLD.id,OLD.project_id,OLD.run_id,OLD.quote_id,OLD.approval_id,
      OLD.request_key,OLD.request_sha256,OLD.reserved_usd,OLD.created_at)
      IS DISTINCT FROM
     (NEW.id,NEW.project_id,NEW.run_id,NEW.quote_id,NEW.approval_id,
      NEW.request_key,NEW.request_sha256,NEW.reserved_usd,NEW.created_at)
  THEN RAISE EXCEPTION 'Group B attempt identity immutable' USING ERRCODE='23514'; END IF;
  IF OLD.charge_state=NEW.charge_state OR
    OLD.charge_state IN ('settled','failed') OR
    (OLD.charge_state='reserved' AND NEW.charge_state NOT IN ('submitted','uncertain','failed')) OR
    (OLD.charge_state='submitted' AND NEW.charge_state NOT IN ('uncertain','settled','failed')) OR
    (OLD.charge_state='uncertain' AND NEW.charge_state NOT IN ('settled','failed'))
  THEN RAISE EXCEPTION 'Group B attempt lifecycle invalid' USING ERRCODE='23514'; END IF;
  IF OLD.provider_request_id IS NOT NULL AND
     OLD.provider_request_id IS DISTINCT FROM NEW.provider_request_id
  THEN RAISE EXCEPTION 'Provider request identity immutable' USING ERRCODE='23514'; END IF;
 END IF;
 IF NEW.charge_state IN ('reserved','submitted','uncertain') AND
    (NEW.charged_usd IS NOT NULL OR NEW.usage IS NOT NULL)
 THEN RAISE EXCEPTION 'Unverified charge data not allowed' USING ERRCODE='23514'; END IF;
 IF NEW.charge_state IN ('submitted','settled') AND NEW.provider_request_id IS NULL
 THEN RAISE EXCEPTION 'Provider request ID required' USING ERRCODE='23514'; END IF;
 IF NEW.charge_state='settled' AND (NEW.charged_usd IS NULL OR NEW.charged_usd>NEW.reserved_usd)
 THEN RAISE EXCEPTION 'Settlement exceeds reservation' USING ERRCODE='23514'; END IF;
 IF NEW.charge_state='failed' AND NEW.charged_usd IS DISTINCT FROM 0
 THEN RAISE EXCEPTION 'Failed must be verified uncharged' USING ERRCODE='23514'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER trg_group_b_budget_attempt_guard
 BEFORE INSERT OR UPDATE ON group_b_model_attempts
 FOR EACH ROW EXECUTE FUNCTION group_b_budget_attempt_guard();
