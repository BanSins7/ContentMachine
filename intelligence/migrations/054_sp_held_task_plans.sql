-- Search Planner SP-10: owner-approved immutable HELD preview, never dispatch.
-- New additive migration; do not edit historic 015/016 SQL.
-- No Collector registry or provider job dependency.

CREATE TABLE sp_held_task_plans (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 validation_run_id UUID NOT NULL,
 plan_hash TEXT NOT NULL CHECK(plan_hash ~ '^[0-9a-f]{64}$'),
 snapshot JSONB NOT NULL CHECK(jsonb_typeof(snapshot)='object'
   AND snapshot->>'schema_version'='search-planner-held-task-plan/v1'
   AND snapshot->>'status'='held_preview_no_dispatch'
   AND snapshot->>'execution_enabled'='false'
   AND snapshot->>'external_dispatch_authorized'='false'
   AND snapshot->>'plan_hash'=plan_hash),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(validation_run_id,project_id) REFERENCES qv_runs(id,project_id),
 UNIQUE(id,project_id),
 UNIQUE(project_id,validation_run_id,plan_hash)
);
CREATE INDEX sp_held_task_plans_qv_idx
 ON sp_held_task_plans(project_id,validation_run_id,created_at DESC);

CREATE TABLE sp_held_plan_approvals (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 plan_id UUID NOT NULL,
 idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 12 AND 128),
 owner_action TEXT NOT NULL DEFAULT 'search_planner.plan.commit_held'
   CHECK(owner_action='search_planner.plan.commit_held'),
 approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(plan_id,project_id) REFERENCES sp_held_task_plans(id,project_id),
 UNIQUE(project_id,idempotency_key),
 UNIQUE(plan_id)
);
CREATE INDEX sp_held_plan_approvals_project_idx
 ON sp_held_plan_approvals(project_id,approved_at DESC);

-- Plan and approval evidence are append-only. Any replan gets a new hash.
CREATE TRIGGER sp_held_task_plan_immutable
 BEFORE UPDATE OR DELETE ON sp_held_task_plans
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();
CREATE TRIGGER sp_held_plan_approval_immutable
 BEFORE UPDATE OR DELETE ON sp_held_plan_approvals
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();
