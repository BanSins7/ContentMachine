-- SP-15B: durable, project-scoped lease for NON-BILLABLE QV->SP inbox.
-- Additive only. No OpenRouter/Collector execution consent or task is created.
-- A lease guards competing foreground supervisors, NOT provider POST safety.
CREATE TABLE sp_supervisor_leases (
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 worker_kind TEXT NOT NULL CHECK(worker_kind='qv_inbox'),
 owner_id UUID NOT NULL,
 fence_generation BIGINT NOT NULL DEFAULT 1 CHECK(fence_generation > 0),
 acquired_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 heartbeat_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 lease_until TIMESTAMPTZ NOT NULL,
 PRIMARY KEY(project_id,worker_kind),
 CHECK(lease_until >= heartbeat_at)
);
CREATE INDEX sp_supervisor_leases_expiry ON sp_supervisor_leases(lease_until);
