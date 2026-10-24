-- Stage 8: explicit, zero-spend execution grants for Collector DTK tasks.
-- All previously created jobs stay run_after='infinity' and generic job
-- consumers must never select them. Only the dedicated collector worker can.
ALTER TABLE collector_runs DROP CONSTRAINT collector_runs_stage_check;
ALTER TABLE collector_runs ADD CONSTRAINT collector_runs_stage_check
 CHECK (stage IN ('planned_held','released','cancelling','cancelled',
                  'completed','partial','failed','blocked'));

CREATE TABLE collector_execution_grants (
 run_id BIGINT PRIMARY KEY REFERENCES collector_runs(run_id),
 source_id TEXT NOT NULL REFERENCES sources(id),
 policy_version_id BIGINT NOT NULL REFERENCES source_policy_versions(id),
 quota_window_start TIMESTAMPTZ NOT NULL,
 request_cap INTEGER NOT NULL CHECK(request_cap BETWEEN 1 AND 50),
 reserved_remaining INTEGER NOT NULL
   CHECK(reserved_remaining >= 0 AND reserved_remaining <= request_cap),
 consumed INTEGER NOT NULL DEFAULT 0
   CHECK(consumed >= 0 AND consumed <= request_cap),
 consent_key TEXT NOT NULL CHECK(length(consent_key) BETWEEN 12 AND 100),
 consent_snapshot JSONB NOT NULL CHECK(jsonb_typeof(consent_snapshot)='object'),
 approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(run_id,consent_key),
 FOREIGN KEY(source_id,quota_window_start)
    REFERENCES source_quota_ledger(source_id,window_start_at),
 CHECK (reserved_remaining + consumed <= request_cap)
);

ALTER TABLE collector_provider_tasks
 ADD COLUMN response_summary JSONB,
 ADD COLUMN submitted_at TIMESTAMPTZ,
 ADD COLUMN polled_at TIMESTAMPTZ,
 ADD COLUMN finished_at TIMESTAMPTZ,
 ADD COLUMN last_error_code TEXT,
 ADD CONSTRAINT collector_provider_tasks_response_summary_type
   CHECK (response_summary IS NULL OR jsonb_typeof(response_summary)='object');

-- Verify that a claimed submit is never auto-released/retried after crash.
CREATE INDEX idx_collector_provider_recover
  ON collector_provider_tasks(state,run_id,job_id)
  WHERE state IN ('submit_intent','submitted');

COMMENT ON TABLE collector_execution_grants IS
 'Explicit zero-spend consent and finite provider-request quota. Never implies rights to store raw TikTok data.';
