-- GC-MOD-12: global single-active config and append-only approved activation.
-- Additive after 058; 003's one-global-active index remains authoritative.
CREATE TABLE group_c_config_activation_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE RESTRICT,
  config_id BIGINT NOT NULL REFERENCES metric_calculation_configs(id) ON DELETE RESTRICT,
  review_request_id UUID NOT NULL REFERENCES group_c_review_requests(id) ON DELETE RESTRICT,
  review_decision_id UUID NOT NULL REFERENCES group_c_review_decisions(id) ON DELETE RESTRICT,
  review_subject_sha256 TEXT NOT NULL CHECK (review_subject_sha256 ~ '^[0-9a-f]{64}$'),
  activation_assertion_sha256 TEXT NOT NULL CHECK (activation_assertion_sha256 ~ '^[0-9a-f]{64}$'),
  kind TEXT NOT NULL CHECK (kind IN ('activated','superseded')),
  previous_config_id BIGINT REFERENCES metric_calculation_configs(id) ON DELETE RESTRICT,
  replacement_config_id BIGINT REFERENCES metric_calculation_configs(id) ON DELETE RESTRICT,
  transaction_id xid8 NOT NULL DEFAULT pg_current_xact_id(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE (review_request_id,kind,config_id),
  CHECK (
    (kind='activated' AND replacement_config_id IS NULL
     AND (previous_config_id IS NULL OR previous_config_id<>config_id))
    OR (kind='superseded' AND previous_config_id IS NULL
        AND replacement_config_id IS NOT NULL AND replacement_config_id<>config_id)
  )
);
-- Only one activation per separately signed global action; a paired
-- 'superseded' row within the same switch shares this SHA.
CREATE UNIQUE INDEX uq_gc_global_activation_signature
  ON group_c_config_activation_events(activation_assertion_sha256)
  WHERE kind='activated';
CREATE INDEX idx_gc_config_activation_project
  ON group_c_config_activation_events(project_id,created_at DESC);
CREATE INDEX idx_gc_config_activation_config
  ON group_c_config_activation_events(config_id,created_at DESC);

CREATE FUNCTION group_c_activation_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Group C activation audit is append-only'
    USING ERRCODE='23514';
END $$;
CREATE TRIGGER trg_gc_activation_immutable
  BEFORE UPDATE OR DELETE ON group_c_config_activation_events
  FOR EACH ROW EXECUTE FUNCTION group_c_activation_immutable();

CREATE FUNCTION group_c_activation_event_validate() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  request_rec RECORD;
  decision_rec RECORD;
  active_now BOOLEAN;
BEGIN
  IF NEW.transaction_id IS DISTINCT FROM pg_current_xact_id() THEN
    RAISE EXCEPTION 'Activation event transaction must be current'
      USING ERRCODE='23514';
  END IF;
  SELECT project_id,calculation_config_id,subject_sha256
    INTO request_rec FROM group_c_review_requests
    WHERE id=NEW.review_request_id;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'Activation requires reviewed config subject'
      USING ERRCODE='23514';
  END IF;
  SELECT id,request_id,project_id,subject_sha256,decision
    INTO decision_rec FROM group_c_review_decisions
    WHERE id=NEW.review_decision_id;
  IF NOT FOUND OR decision_rec.decision IS DISTINCT FROM 'approved'
     OR decision_rec.request_id IS DISTINCT FROM NEW.review_request_id
     OR decision_rec.project_id IS DISTINCT FROM NEW.project_id
     OR decision_rec.subject_sha256 IS DISTINCT FROM NEW.review_subject_sha256
     OR request_rec.project_id IS DISTINCT FROM NEW.project_id
     OR request_rec.subject_sha256 IS DISTINCT FROM NEW.review_subject_sha256
  THEN
    RAISE EXCEPTION 'Activation requires exact approved human review'
      USING ERRCODE='23514';
  END IF;
  SELECT is_active INTO active_now FROM metric_calculation_configs
    WHERE id=NEW.config_id;
  IF NEW.kind='activated' THEN
    IF request_rec.calculation_config_id IS DISTINCT FROM NEW.config_id
       OR active_now IS DISTINCT FROM TRUE THEN
      RAISE EXCEPTION 'Activation event does not match enabled reviewed config'
        USING ERRCODE='23514';
    END IF;
  ELSE
    IF request_rec.calculation_config_id IS DISTINCT FROM NEW.replacement_config_id
       OR active_now IS DISTINCT FROM FALSE THEN
      RAISE EXCEPTION 'Supersession must cite exact newly reviewed config'
        USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_gc_activation_event_validate
  BEFORE INSERT ON group_c_config_activation_events
  FOR EACH ROW EXECUTE FUNCTION group_c_activation_event_validate();

-- Deferrable commit check: ALL active toggles and active INSERTs must have
-- a matching immutable, same-transaction audited activation/supersession.
CREATE FUNCTION group_c_config_toggle_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  expected_kind TEXT;
BEGIN
  IF NEW.is_active THEN
    expected_kind := 'activated';
  ELSE
    expected_kind := 'superseded';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM group_c_config_activation_events e
    WHERE e.config_id=NEW.id AND e.kind=expected_kind
      AND e.transaction_id=pg_current_xact_id()
  ) THEN
    RAISE EXCEPTION 'Config activation state requires same-tx approved audit event'
      USING ERRCODE='23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER trg_gc_config_toggle_update
  AFTER UPDATE OF is_active ON metric_calculation_configs
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW WHEN (OLD.is_active IS DISTINCT FROM NEW.is_active)
  EXECUTE FUNCTION group_c_config_toggle_guard();
CREATE CONSTRAINT TRIGGER trg_gc_config_toggle_insert
  AFTER INSERT ON metric_calculation_configs
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW WHEN (NEW.is_active IS TRUE)
  EXECUTE FUNCTION group_c_config_toggle_guard();
