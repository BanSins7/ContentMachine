-- Keep explicit classification rationale separate from stage telemetry.
-- Results remain linked to immutable taxonomy and pipeline snapshots.
ALTER TABLE sre_classification_results
  ADD COLUMN IF NOT EXISTS reason TEXT;
ALTER TABLE sre_classification_results
  ADD CONSTRAINT sre_result_reason_nonempty CHECK
    (reason IS NULL OR length(btrim(reason))>0);
