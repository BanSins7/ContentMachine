-- Video Collector Phase II: non-executable planning records only.
-- Never alter the existing 001/002/015 migrations; additive and offline.
-- A future trusted Query Validation publisher must seal collector_query_sets.
CREATE TABLE collector_query_sets (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 generation_id UUID NOT NULL,
 publication_id UUID NOT NULL REFERENCES tb_taxonomy_publications(id),
 validation_origin TEXT NOT NULL CHECK (validation_origin='trusted_query_validation/v1'),
 query_set JSONB NOT NULL CHECK (jsonb_typeof(query_set)='object'),
 query_set_sha256 TEXT NOT NULL CHECK (query_set_sha256 ~ '^[0-9a-f]{64}$'),
 validated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(generation_id,project_id) REFERENCES sqg_generations(id,project_id),
 UNIQUE (id,project_id),
 UNIQUE (id,project_id,publication_id),
 UNIQUE (project_id,generation_id,query_set_sha256)
);
CREATE INDEX idx_collector_query_sets_project ON collector_query_sets(project_id,validated_at DESC);

CREATE TABLE collector_plans (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 query_set_id UUID NOT NULL,
 source_id TEXT NOT NULL REFERENCES sources(id),
 policy_version_id BIGINT NOT NULL REFERENCES source_policy_versions(id),
 plan_sha256 TEXT NOT NULL CHECK (plan_sha256 ~ '^[0-9a-f]{64}$'),
 snapshot JSONB NOT NULL CHECK (jsonb_typeof(snapshot)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(query_set_id,project_id) REFERENCES collector_query_sets(id,project_id),
 UNIQUE (id,project_id),
 UNIQUE (project_id,plan_sha256,source_id,policy_version_id)
);
CREATE INDEX idx_collector_plans_project ON collector_plans(project_id,created_at DESC);

CREATE TABLE collector_runs (
 run_id BIGINT PRIMARY KEY REFERENCES search_runs(id),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 plan_id UUID NOT NULL,
 query_set_id UUID NOT NULL,
 policy_version_id BIGINT NOT NULL REFERENCES source_policy_versions(id),
 stage TEXT NOT NULL DEFAULT 'planned_held'
   CHECK (stage IN ('planned_held','cancelled','released')),
 authorized_by TEXT NOT NULL DEFAULT 'local_owner'
   CHECK (length(authorized_by) BETWEEN 1 AND 120),
 confirmed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(plan_id,project_id) REFERENCES collector_plans(id,project_id),
 FOREIGN KEY(query_set_id,project_id) REFERENCES collector_query_sets(id,project_id)
);
CREATE INDEX idx_collector_runs_project ON collector_runs(project_id,run_id DESC);

CREATE TABLE collector_provider_tasks (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 run_id BIGINT NOT NULL REFERENCES collector_runs(run_id),
 source_request_id BIGINT NOT NULL UNIQUE REFERENCES source_requests(id),
 job_id BIGINT NOT NULL UNIQUE REFERENCES jobs(id),
 task_sha256 TEXT NOT NULL CHECK (task_sha256 ~ '^[0-9a-f]{64}$'),
 state TEXT NOT NULL DEFAULT 'not_submitted'
   CHECK (state IN ('not_submitted','cancelled','submit_intent','unknown',
                   'submitted','completed','failed')),
 provider_task_id TEXT,
 cursor_hash TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(run_id,task_sha256),
 CHECK (provider_task_id IS NULL OR length(provider_task_id) BETWEEN 1 AND 255)
);
CREATE INDEX idx_collector_provider_tasks_run ON collector_provider_tasks(run_id,state);

CREATE TABLE collector_task_queries (
 source_request_id BIGINT NOT NULL REFERENCES source_requests(id),
 query_id TEXT NOT NULL CHECK (length(query_id) BETWEEN 1 AND 128),
 hypothesis_id TEXT NOT NULL CHECK (length(hypothesis_id) BETWEEN 1 AND 128),
 direction_id TEXT NOT NULL CHECK (length(direction_id) BETWEEN 1 AND 128),
 PRIMARY KEY(source_request_id,query_id)
);

-- The future ingest worker uses this for many-hypotheses-to-one-video provenance.
CREATE TABLE collector_video_hypotheses (
 source_request_id BIGINT NOT NULL REFERENCES source_requests(id),
 video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
 query_id TEXT NOT NULL,
 hypothesis_id TEXT NOT NULL,
 discovery_id BIGINT REFERENCES discoveries(id) ON DELETE SET NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(source_request_id,video_id,query_id),
 FOREIGN KEY(source_request_id,query_id)
   REFERENCES collector_task_queries(source_request_id,query_id)
);

-- Prevent accidental rewriting/removal of approved inputs and committed plans.
CREATE FUNCTION collector_immutable_record() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'Collector: verified query sets and plans are immutable';
END $$;
CREATE TRIGGER collector_query_sets_immutable
 BEFORE UPDATE OR DELETE ON collector_query_sets
 FOR EACH ROW EXECUTE FUNCTION collector_immutable_record();
CREATE TRIGGER collector_plans_immutable
 BEFORE UPDATE OR DELETE ON collector_plans
 FOR EACH ROW EXECUTE FUNCTION collector_immutable_record();
