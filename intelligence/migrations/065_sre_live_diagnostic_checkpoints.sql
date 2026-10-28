-- M31 forward-only 065: privacy-minimal, append-only LIVE diagnostics.
-- All metrics are recomputed from committed SRE-owned tables at INSERT time.
-- No source text, prompts, API keys, result reasons or user-provided labels.
CREATE TABLE sre_live_diagnostic_checkpoints (
  checkpoint_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  actor_id TEXT NOT NULL CHECK(actor_id ~ '^[A-Za-z0-9:_@.-]{1,128}$'),
  snapshot_sha256 TEXT NOT NULL CHECK(snapshot_sha256 ~ '^[0-9a-f]{64}$'),
  run_state TEXT NOT NULL CHECK(run_state IN (
    'planned','pending_consent','queued','running','waiting_source',
    'paused_budget','paused_rights','unknown','partial','completed','failed','cancelled')),
  cancel_requested BOOLEAN NOT NULL,
  expected_items INTEGER NOT NULL CHECK(expected_items BETWEEN 1 AND 10000),
  physical_items INTEGER NOT NULL CHECK(physical_items>=0),
  expected_pairs INTEGER NOT NULL CHECK(expected_pairs BETWEEN 0 AND 480000),
  physical_pairs INTEGER NOT NULL CHECK(physical_pairs>=0),
  proven_pairs INTEGER NOT NULL CHECK(proven_pairs>=0),
  dispatched_intents INTEGER NOT NULL CHECK(dispatched_intents>=0),
  unsettled_intents INTEGER NOT NULL CHECK(unsettled_intents>=0),
  confirmed_receipts INTEGER NOT NULL CHECK(confirmed_receipts>=0),
  reserved_usd NUMERIC(24,9) NOT NULL CHECK(reserved_usd>=0),
  inference_spent_usd NUMERIC(24,9) NOT NULL CHECK(inference_spent_usd>=0),
  finance_event_count INTEGER NOT NULL CHECK(finance_event_count>=0),
  reconciliation_event_count INTEGER NOT NULL CHECK(reconciliation_event_count>=0),
  alert_code TEXT NOT NULL CHECK(alert_code IN (
    'unknown_paid_outcome','incomplete_pair_registry',
    'cost_without_complete_evidence','blocked_rights_or_budget',
    'incomplete_classification','no_alert'
  )),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  FOREIGN KEY(run_id,project_id)
    REFERENCES sre_live_runs(run_id,project_id) ON DELETE RESTRICT,
  UNIQUE(project_id,run_id,snapshot_sha256),
  CHECK(physical_items<=expected_items
    AND physical_pairs<=expected_pairs AND proven_pairs<=physical_pairs
    AND confirmed_receipts<=dispatched_intents
    AND unsettled_intents<=dispatched_intents)
);
CREATE INDEX sre_m31_diagnostics_project_time
  ON sre_live_diagnostic_checkpoints(project_id,run_id,created_at,checkpoint_id);
CREATE INDEX sre_m31_diagnostics_alert
  ON sre_live_diagnostic_checkpoints(project_id,alert_code,created_at)
  WHERE alert_code<>'no_alert';

CREATE FUNCTION sre_m31_guard_checkpoint() RETURNS trigger
LANGUAGE plpgsql AS $m31_checkpoint$
DECLARE baseline RECORD;
DECLARE got RECORD;
DECLARE got_money RECORD;
DECLARE physical_pairs INTEGER;
DECLARE proven_pairs INTEGER;
DECLARE dispatched_intents INTEGER;
DECLARE unsettled_intents INTEGER;
DECLARE confirmed_receipts INTEGER;
DECLARE finance_event_count INTEGER;
DECLARE reconciliation_event_count INTEGER;
BEGIN
  IF TG_OP<>'INSERT' THEN
    RAISE EXCEPTION 'M31 diagnostic checkpoints are immutable and undeletable';
  END IF;
  PERFORM 1 FROM sre_workspaces
    WHERE project_id=NEW.project_id FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'M31 project workspace unavailable';
  END IF;
  SELECT state,cancel_requested,expected_items
    INTO baseline FROM sre_live_runs
    WHERE project_id=NEW.project_id AND run_id=NEW.run_id
      AND execution_mode='live' FOR SHARE;
  IF NOT FOUND OR baseline.state IS DISTINCT FROM NEW.run_state
     OR baseline.cancel_requested IS DISTINCT FROM NEW.cancel_requested
     OR baseline.expected_items IS DISTINCT FROM NEW.expected_items THEN
    RAISE EXCEPTION 'M31 checkpoint run identity/state mismatch';
  END IF;
  SELECT count(*)::integer AS items,
         coalesce(sum(jsonb_array_length(expected_hypothesis_keys)),0)::integer
           AS expected_pairs
    INTO got FROM sre_live_items
    WHERE project_id=NEW.project_id AND run_id=NEW.run_id;
  IF got.items<>NEW.physical_items
     OR got.expected_pairs<>NEW.expected_pairs THEN
    RAISE EXCEPTION 'M31 checkpoint original C9 matrix mismatch';
  END IF;
  SELECT count(*)::integer INTO physical_pairs
    FROM sre_live_expected_pairs
    WHERE project_id=NEW.project_id AND run_id=NEW.run_id;
  SELECT count(*)::integer INTO proven_pairs
    FROM sre_live_verified_pair_results
    WHERE project_id=NEW.project_id AND run_id=NEW.run_id;
  SELECT count(*)::integer INTO dispatched_intents
    FROM sre_live_opus_dispatch_intents
    WHERE project_id=NEW.project_id AND run_id=NEW.run_id;
  SELECT count(*)::integer INTO unsettled_intents
    FROM sre_live_opus_dispatch_intents d
    WHERE d.project_id=NEW.project_id AND d.run_id=NEW.run_id
      AND NOT EXISTS (
        SELECT 1 FROM sre_live_opus_receipts t
          WHERE t.project_id=d.project_id AND t.intent_id=d.intent_id
      );
  SELECT count(*)::integer INTO confirmed_receipts
    FROM sre_live_opus_receipts
    WHERE project_id=NEW.project_id AND run_id=NEW.run_id;
  SELECT count(*)::integer INTO finance_event_count
    FROM sre_live_budget_events e
    JOIN sre_live_budget_reservations rs
      ON rs.project_id=e.project_id AND rs.reservation_id=e.reservation_id
    WHERE rs.project_id=NEW.project_id AND rs.run_id=NEW.run_id;
  SELECT count(*)::integer INTO reconciliation_event_count
    FROM sre_live_opus_reconciliation_events
    WHERE project_id=NEW.project_id AND run_id=NEW.run_id;
  SELECT coalesce(sum(rs.amount_usd) FILTER (WHERE rs.state='reserved'),0)::numeric(24,9)
      AS reserved,
         coalesce(sum(t.charged_usd),0)::numeric(24,9) AS spent
    INTO got_money
    FROM sre_live_budget_reservations rs
    LEFT JOIN sre_live_opus_receipts t
      ON t.project_id=rs.project_id AND t.reservation_id=rs.reservation_id
    WHERE rs.project_id=NEW.project_id AND rs.run_id=NEW.run_id;
  IF physical_pairs<>NEW.physical_pairs
     OR proven_pairs<>NEW.proven_pairs
     OR dispatched_intents<>NEW.dispatched_intents
     OR unsettled_intents<>NEW.unsettled_intents
     OR confirmed_receipts<>NEW.confirmed_receipts
     OR finance_event_count<>NEW.finance_event_count
     OR reconciliation_event_count<>NEW.reconciliation_event_count
     OR got_money.reserved<>NEW.reserved_usd
     OR got_money.spent<>NEW.inference_spent_usd THEN
    RAISE EXCEPTION 'M31 checkpoint counts or Decimal USD untrusted';
  END IF;
  IF NEW.alert_code IS DISTINCT FROM CASE
      WHEN NEW.unsettled_intents>0 THEN 'unknown_paid_outcome'
      WHEN NEW.physical_items<>NEW.expected_items
        OR NEW.physical_pairs<>NEW.expected_pairs THEN 'incomplete_pair_registry'
      WHEN NEW.confirmed_receipts>NEW.proven_pairs
        THEN 'cost_without_complete_evidence'
      WHEN EXISTS (
        SELECT 1 FROM sre_live_expected_pairs p
        WHERE p.project_id=NEW.project_id AND p.run_id=NEW.run_id
          AND p.state IN ('blocked_rights','blocked_budget')
      ) THEN 'blocked_rights_or_budget'
      WHEN NEW.proven_pairs<NEW.expected_pairs
        THEN 'incomplete_classification'
      ELSE 'no_alert'
    END THEN
    RAISE EXCEPTION 'M31 checkpoint alert priority is not source-computed';
  END IF;
  RETURN NEW;
END;
$m31_checkpoint$;
CREATE TRIGGER sre_m31_checkpoint_insert
  BEFORE INSERT ON sre_live_diagnostic_checkpoints
  FOR EACH ROW EXECUTE FUNCTION sre_m31_guard_checkpoint();
CREATE TRIGGER sre_m31_checkpoint_immutable
  BEFORE UPDATE OR DELETE ON sre_live_diagnostic_checkpoints
  FOR EACH ROW EXECUTE FUNCTION sre_m31_guard_checkpoint();
REVOKE ALL ON sre_live_diagnostic_checkpoints FROM PUBLIC;
