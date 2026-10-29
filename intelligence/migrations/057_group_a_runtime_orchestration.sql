-- GA-MOD-20, additive and Group-A-owned durable runtime coordination.
-- Do not rewrite GA 054 / 055 / 056 or other-module migration checksums.

CREATE TABLE ga_step_dependencies (
    run_id UUID NOT NULL REFERENCES ga_runs(id) ON DELETE CASCADE,
    step_key TEXT NOT NULL,
    depends_on_step_key TEXT NOT NULL,
    PRIMARY KEY (run_id,step_key,depends_on_step_key),
    FOREIGN KEY (run_id,step_key)
        REFERENCES ga_steps(run_id,step_key) ON DELETE CASCADE,
    FOREIGN KEY (run_id,depends_on_step_key)
        REFERENCES ga_steps(run_id,step_key) ON DELETE CASCADE,
    CHECK (step_key<>depends_on_step_key)
);
CREATE INDEX idx_ga20_dep_upstream ON ga_step_dependencies(run_id,depends_on_step_key);

CREATE TABLE ga_step_resource_specs (
    step_id BIGINT PRIMARY KEY REFERENCES ga_steps(id) ON DELETE CASCADE,
    cpu_slots SMALLINT NOT NULL CHECK (cpu_slots BETWEEN 1 AND 64),
    gpu_slots SMALLINT NOT NULL CHECK (gpu_slots BETWEEN 0 AND 8),
    priority SMALLINT NOT NULL DEFAULT 0 CHECK (priority BETWEEN -20 AND 20),
    max_runtime_sec INTEGER NOT NULL CHECK (max_runtime_sec BETWEEN 1 AND 3600),
    allow_retry BOOLEAN NOT NULL DEFAULT TRUE
);
CREATE TABLE ga_step_attempts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    step_id BIGINT NOT NULL REFERENCES ga_steps(id) ON DELETE CASCADE,
    run_id UUID NOT NULL REFERENCES ga_runs(id) ON DELETE CASCADE,
    attempt_no INTEGER NOT NULL CHECK (attempt_no BETWEEN 1 AND 20),
    token UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    worker_id TEXT NOT NULL CHECK (length(worker_id) BETWEEN 1 AND 120),
    state TEXT NOT NULL DEFAULT 'leased'
        CHECK (state IN ('leased','completed','partial','failed','lost','cancelled')),
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    lease_until TIMESTAMPTZ NOT NULL,
    deadline_at TIMESTAMPTZ NOT NULL,
    finished_at TIMESTAMPTZ,
    output_sha256 TEXT CHECK (output_sha256~'^[0-9a-f]{64}$'),
    error_code TEXT CHECK (error_code IS NULL OR error_code~'^[a-z][a-z0-9_]{1,79}$'),
    UNIQUE (step_id,attempt_no),
    CHECK (finished_at IS NULL OR state<>'leased'),
    CHECK (state='leased' OR finished_at IS NOT NULL)
);
CREATE UNIQUE INDEX idx_ga20_single_live_attempt ON ga_step_attempts(step_id)
    WHERE state='leased';
CREATE TABLE ga_resource_claims (
    attempt_id BIGINT PRIMARY KEY REFERENCES ga_step_attempts(id) ON DELETE CASCADE,
    cpu_slots SMALLINT NOT NULL CHECK (cpu_slots BETWEEN 1 AND 64),
    gpu_slots SMALLINT NOT NULL CHECK (gpu_slots BETWEEN 0 AND 8),
    state TEXT NOT NULL CHECK (state IN ('active','released')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    released_at TIMESTAMPTZ,
    CHECK ((state='active' AND released_at IS NULL) OR
           (state='released' AND released_at IS NOT NULL))
);
CREATE INDEX idx_ga20_active_resources ON ga_resource_claims(state)
    WHERE state='active';

CREATE TABLE ga_run_control (
    run_id UUID PRIMARY KEY REFERENCES ga_runs(id) ON DELETE CASCADE,
    paused BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ga_outbox_attempts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    outbox_id BIGINT NOT NULL REFERENCES ga_outbox(id) ON DELETE CASCADE,
    attempt_no INTEGER NOT NULL CHECK (attempt_no BETWEEN 1 AND 20),
    token UUID NOT NULL DEFAULT gen_random_uuid() UNIQUE,
    worker_id TEXT NOT NULL CHECK (length(worker_id) BETWEEN 1 AND 120),
    state TEXT NOT NULL DEFAULT 'leased'
        CHECK (state IN ('leased','acknowledged','failed','lost')),
    lease_until TIMESTAMPTZ NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at TIMESTAMPTZ,
    UNIQUE (outbox_id,attempt_no),
    CHECK ((state='leased' AND finished_at IS NULL) OR
           (state<>'leased' AND finished_at IS NOT NULL))
);
CREATE UNIQUE INDEX idx_ga20_single_outbox_attempt ON ga_outbox_attempts(outbox_id)
    WHERE state='leased';

CREATE TABLE ga_outbox_ack_receipts (
    outbox_id BIGINT PRIMARY KEY REFERENCES ga_outbox(id) ON DELETE CASCADE,
    attempt_id BIGINT NOT NULL UNIQUE REFERENCES ga_outbox_attempts(id),
    result_id BIGINT NOT NULL REFERENCES ga_results(id),
    result_sha256 TEXT NOT NULL CHECK (result_sha256~'^[0-9a-f]{64}$'),
    consumer_receipt_sha256 TEXT NOT NULL CHECK
        (consumer_receipt_sha256~'^[0-9a-f]{64}$'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE ga_invalidation_audit (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id UUID NOT NULL REFERENCES ga_runs(id) ON DELETE CASCADE,
    previous_state TEXT NOT NULL,
    reason_code TEXT NOT NULL CHECK (reason_code~'^[a-z][a-z0-9_]{1,79}$'),
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE(run_id)
);

-- Prevent a mutable DAG or changing resource budget after first claim.
CREATE OR REPLACE FUNCTION ga20_guard_step_deps() RETURNS trigger
LANGUAGE plpgsql AS $ga20_deps$
BEGIN
    IF TG_OP<>'INSERT' THEN
        RAISE EXCEPTION 'GA dependency edges are immutable' USING ERRCODE='23514';
    END IF;
    IF EXISTS (
        SELECT 1 FROM ga_steps s WHERE s.run_id=NEW.run_id
          AND s.step_key IN (NEW.step_key,NEW.depends_on_step_key)
          AND (s.attempts<>0 OR s.state<>'queued')
    ) THEN
        RAISE EXCEPTION 'Cannot change GA dependency graph after execution'
          USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$ga20_deps$;
CREATE TRIGGER ga20_dependency_immutable BEFORE INSERT OR UPDATE OR DELETE
    ON ga_step_dependencies FOR EACH ROW EXECUTE FUNCTION ga20_guard_step_deps();

CREATE OR REPLACE FUNCTION ga20_guard_specs() RETURNS trigger
LANGUAGE plpgsql AS $ga20_specs$
BEGIN
    IF TG_OP='DELETE' THEN
        RAISE EXCEPTION 'GA resource specifications are immutable'
          USING ERRCODE='23514';
    END IF;
    IF TG_OP='UPDATE' AND NEW IS DISTINCT FROM OLD THEN
        RAISE EXCEPTION 'GA resource specifications are immutable'
          USING ERRCODE='23514';
    END IF;
    IF TG_OP='INSERT' AND EXISTS (
        SELECT 1 FROM ga_steps WHERE id=NEW.step_id AND
        (attempts>0 OR state<>'queued')
    ) THEN
        RAISE EXCEPTION 'Cannot add GA resources to running step'
          USING ERRCODE='23514';
    END IF;
    RETURN NEW;
END;
$ga20_specs$;
CREATE TRIGGER ga20_specs_immutable BEFORE INSERT OR UPDATE OR DELETE
    ON ga_step_resource_specs FOR EACH ROW EXECUTE FUNCTION ga20_guard_specs();

-- Retain all history but expose ONLY locally eligible, current pins.
-- IMPORTANT: a live QDP permission check is impossible in this SQL view.
-- Every external read MUST also authenticate current QDP/project rights.
CREATE VIEW ga20_locally_current_results AS
SELECT g.id AS result_id,g.run_id,g.project_id,g.video_id,
       g.result_sha256,g.snapshot,g.video_a_analysis_id,
       g.carousel_a_analysis_id,r.qdp_run_id,r.source_id,
       r.source_policy_version_id,r.manifest_sha256
  FROM ga_results g
  JOIN ga_runs r ON r.id=g.run_id AND r.project_id=g.project_id
  JOIN sources s ON s.id=r.source_id
  JOIN source_policy_versions sp ON sp.id=r.source_policy_version_id
      AND sp.source_id=r.source_id
 WHERE r.state='completed' AND g.state='completed'
   AND r.invalidated_at IS NULL AND r.invalidated_reason IS NULL
   AND s.status='enabled' AND s.can_analyze_media=TRUE
   AND sp.is_active=TRUE AND sp.permissions->>'can_analyze_media'='true'
   AND (SELECT count(*) FROM ga_media_refs mr
        WHERE mr.run_id=r.id)=r.expected_media_items
   AND NOT EXISTS (
       SELECT 1 FROM ga_media_refs mr
       LEFT JOIN media_assets a ON a.id=mr.media_asset_id
       WHERE mr.run_id=r.id AND
       (a.id IS NULL OR a.video_id<>r.video_id OR a.source_id<>r.source_id
        OR a.sha256 IS DISTINCT FROM mr.asset_sha256
        OR a.media_kind IS DISTINCT FROM mr.media_kind
        OR a.permission IS DISTINCT FROM 'analysis_allowed'
        OR (a.expires_at IS NOT NULL AND a.expires_at<=now()))
   );
