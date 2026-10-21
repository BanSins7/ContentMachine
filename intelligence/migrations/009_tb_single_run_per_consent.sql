-- Spending authorization belongs to ONE run, never a group of runs.
-- An additional execution needs a fresh quote and a separate human consent.
CREATE UNIQUE INDEX uq_tb_one_run_per_consent ON tb_runs(consent_id);
