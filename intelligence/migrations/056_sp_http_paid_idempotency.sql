-- Search Planner SP-13: durable local owner's one-shot paid HTTP request ledger.
-- ADDITIVE ONLY. Browser duplicate/lost HTTP responses may NEVER authorize a
-- second SQG worker batch just because the prior response was not received.
-- This ledger is separate from the canonical SQG financial/dispatch ledger;
-- it does not authorize money or substitute for backend consent verification.

CREATE TABLE sp_http_paid_actions (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 generation_id UUID NOT NULL,
 idempotency_key TEXT NOT NULL
   CHECK(length(idempotency_key) BETWEEN 12 AND 128),
 request_sha256 TEXT NOT NULL
   CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
 action TEXT NOT NULL DEFAULT 'execute_one'
   CHECK(action='execute_one'),
 state TEXT NOT NULL DEFAULT 'in_progress'
   CHECK(state IN ('in_progress','completed','uncertain')),
 result JSONB,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 finished_at TIMESTAMPTZ,
 FOREIGN KEY (generation_id,project_id)
  REFERENCES sqg_generations(id,project_id),
 UNIQUE(project_id,idempotency_key),
 CHECK((state='in_progress' AND result IS NULL AND finished_at IS NULL)
    OR (state='completed' AND jsonb_typeof(result)='object'
        AND finished_at IS NOT NULL)
    OR (state='uncertain' AND result IS NULL AND finished_at IS NOT NULL))
);
CREATE INDEX sp_http_paid_actions_project_generation
 ON sp_http_paid_actions(project_id,generation_id,created_at DESC);

CREATE FUNCTION sp_http_paid_action_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.id,NEW.project_id,NEW.generation_id,NEW.idempotency_key,
        NEW.request_sha256,NEW.action,NEW.created_at)
    IS DISTINCT FROM
    ROW(OLD.id,OLD.project_id,OLD.generation_id,OLD.idempotency_key,
        OLD.request_sha256,OLD.action,OLD.created_at)
    OR OLD.state <> 'in_progress'
    OR NEW.state NOT IN ('completed','uncertain')
    THEN
    RAISE EXCEPTION 'SP paid HTTP idempotency record cannot be reassigned or replayed';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER sp_http_paid_actions_update
 BEFORE UPDATE ON sp_http_paid_actions
 FOR EACH ROW EXECUTE FUNCTION sp_http_paid_action_guard();
CREATE TRIGGER sp_http_paid_actions_no_delete
 BEFORE DELETE ON sp_http_paid_actions
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();
