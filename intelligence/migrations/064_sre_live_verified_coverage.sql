-- M30 forward-only 064. One verified category decision requires a distinct
-- authentic M28 source-grounded receipt, NOT merely a paid inference.
-- No old M13/M28 migrations rewritten; no OpenRouter POST, no new budget.
CREATE UNIQUE INDEX sre_m30_expected_pair_owner
  ON sre_live_expected_pairs(project_id,run_id,item_id,category_key);
CREATE UNIQUE INDEX sre_m30_receipt_source_owner
  ON sre_live_opus_receipts(project_id,run_id,item_id,receipt_id,request_sha256);

CREATE TABLE sre_live_verified_pair_results (
  result_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  item_id BIGINT NOT NULL,
  category_key UUID NOT NULL,
  receipt_id UUID NOT NULL,
  request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  request_ordinal INTEGER NOT NULL CHECK (request_ordinal>=0),
  decision TEXT NOT NULL CHECK (decision IN ('match','no_match','uncertain')),
  verified_chunk_sha256 TEXT NOT NULL
    CHECK (verified_chunk_sha256 ~ '^[0-9a-f]{64}$'),
  trusted_actor TEXT NOT NULL CHECK
    (trusted_actor ~ '^[A-Za-z0-9:_@.-]{1,128}$'),
  verified_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,run_id,item_id,category_key),
  UNIQUE(project_id,receipt_id),
  UNIQUE(project_id,run_id,item_id,request_sha256),
  FOREIGN KEY(project_id,run_id,item_id,category_key)
    REFERENCES sre_live_expected_pairs(project_id,run_id,item_id,category_key)
    ON DELETE RESTRICT,
  FOREIGN KEY(project_id,run_id,item_id,receipt_id,request_sha256)
    REFERENCES sre_live_opus_receipts(
      project_id,run_id,item_id,receipt_id,request_sha256)
    ON DELETE RESTRICT
);
CREATE INDEX sre_m30_results_run
  ON sre_live_verified_pair_results(project_id,run_id,item_id,category_key);

CREATE FUNCTION sre_m30_guard_verified_result() RETURNS trigger
LANGUAGE plpgsql AS $m30_result$
DECLARE src RECORD;
DECLARE count_by_category BIGINT;
BEGIN
  IF TG_OP<>'INSERT' THEN
    RAISE EXCEPTION 'M30 verified result immutable and cannot be deleted';
  END IF;
  PERFORM 1 FROM sre_workspaces
    WHERE project_id=NEW.project_id FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'M30 workspace missing'; END IF;
  SELECT t.evidence_sha256,t.evidence_status,t.generation_authenticated,
         i.request_ordinal AS original_ordinal,
         q.quote AS original_quote,
         r.state AS run_state,r.cancel_requested,
         x.state AS item_state
  INTO src
  FROM sre_live_opus_receipts t
  JOIN sre_live_opus_dispatch_intents i
    ON i.project_id=t.project_id AND i.intent_id=t.intent_id
  JOIN sre_live_opus_quotes q
    ON q.project_id=i.project_id AND q.run_id=i.run_id
    AND q.item_id=i.item_id AND q.quote_sha256=i.quote_sha256
  JOIN sre_live_runs r
    ON r.project_id=i.project_id AND r.run_id=i.run_id
  JOIN sre_live_items x
    ON x.project_id=i.project_id AND x.run_id=i.run_id AND x.id=i.item_id
  WHERE t.project_id=NEW.project_id AND t.run_id=NEW.run_id
    AND t.item_id=NEW.item_id AND t.receipt_id=NEW.receipt_id
    AND t.request_sha256=NEW.request_sha256
  FOR SHARE OF t,i,q,r,x;
  IF NOT FOUND
     OR src.evidence_status<>'validated_untrusted_output'
     OR src.generation_authenticated IS NOT TRUE
     OR src.evidence_sha256 IS DISTINCT FROM NEW.verified_chunk_sha256
     OR src.original_ordinal <> NEW.request_ordinal
     OR src.run_state NOT IN ('queued','running')
     OR src.cancel_requested IS NOT FALSE
     OR src.item_state <> 'running'
     OR src.original_quote->'per_request'->NEW.request_ordinal->>'request_sha256'
           IS DISTINCT FROM NEW.request_sha256
     OR src.original_quote->'per_request'->NEW.request_ordinal->>'category_key'
           IS DISTINCT FROM NEW.category_key::text
  THEN RAISE EXCEPTION 'M30 exact authenticated source-grounded M28 receipt required'; END IF;
  SELECT count(*) INTO count_by_category
  FROM jsonb_array_elements(src.original_quote->'per_request') AS requests(part)
  WHERE requests.part->>'category_key'=NEW.category_key::text;
  IF count_by_category<>1 THEN
    RAISE EXCEPTION 'M30 cannot finish a split/multi-request category from one receipt';
  END IF;
  RETURN NEW;
END;
$m30_result$;
CREATE TRIGGER sre_m30_pair_result_insert
  BEFORE INSERT ON sre_live_verified_pair_results
  FOR EACH ROW EXECUTE FUNCTION sre_m30_guard_verified_result();
CREATE TRIGGER sre_m30_pair_result_immutable
  BEFORE UPDATE OR DELETE ON sre_live_verified_pair_results
  FOR EACH ROW EXECUTE FUNCTION sre_m30_guard_verified_result();

-- M13 had only non-evaluated and blocked states. Never promote a pair by
-- merely changing its state: require a receipt-backed immutable result.
ALTER TABLE sre_live_expected_pairs
  DROP CONSTRAINT IF EXISTS sre_live_expected_pairs_state_check;
ALTER TABLE sre_live_expected_pairs
  ADD CONSTRAINT sre_m30_expected_pair_states
  CHECK(state IN ('not_evaluated','blocked_rights','blocked_budget',
                  'match','no_match','uncertain'));

CREATE FUNCTION sre_m30_guard_pair_decision() RETURNS trigger
LANGUAGE plpgsql AS $m30_pair$
BEGIN
  IF NEW.state IN ('match','no_match','uncertain')
     AND OLD.state IS DISTINCT FROM NEW.state THEN
    IF OLD.state <> 'not_evaluated' OR NOT EXISTS (
      SELECT 1 FROM sre_live_verified_pair_results v
      JOIN sre_live_opus_receipts t
        ON t.project_id=v.project_id AND t.receipt_id=v.receipt_id
      WHERE v.project_id=NEW.project_id AND v.run_id=NEW.run_id
        AND v.item_id=NEW.item_id AND v.category_key=NEW.category_key
        AND v.decision=NEW.state
        AND t.evidence_status='validated_untrusted_output'
        AND t.generation_authenticated IS TRUE
        AND v.verified_chunk_sha256=t.evidence_sha256
    ) THEN
      RAISE EXCEPTION 'M30 no genuine category decision without verified provider evidence';
    END IF;
  END IF;
  RETURN NEW;
END;
$m30_pair$;
CREATE TRIGGER sre_m30_pair_decision_guard
  BEFORE UPDATE ON sre_live_expected_pairs
  FOR EACH ROW EXECUTE FUNCTION sre_m30_guard_pair_decision();

-- M13 validates the physical video x C9 leaf set. This second barrier
-- ensures that the *evaluated subset* has one M30 proof per pair and
-- cannot be created by manually changing M13 pair state.
CREATE FUNCTION sre_m30_guard_final_state() RETURNS trigger
LANGUAGE plpgsql AS $m30_final$
DECLARE expected_pairs BIGINT;
DECLARE evaluated_pairs BIGINT;
DECLARE proven_pairs BIGINT;
DECLARE unfinished_children BIGINT;
DECLARE missing_receipts BIGINT;
DECLARE expected_items BIGINT;
BEGIN
  IF NEW.state NOT IN ('completed','partial') THEN RETURN NEW; END IF;
  IF TG_TABLE_NAME='sre_live_items' THEN
    SELECT count(*),
           count(*) FILTER (WHERE p.state IN ('match','no_match','uncertain')),
           count(*) FILTER (WHERE v.result_id IS NOT NULL)
      INTO expected_pairs,evaluated_pairs,proven_pairs
    FROM sre_live_expected_pairs p
    LEFT JOIN sre_live_verified_pair_results v
      ON (v.project_id,v.run_id,v.item_id,v.category_key)=
         (p.project_id,p.run_id,p.item_id,p.category_key)
      AND v.decision=p.state
    WHERE p.project_id=NEW.project_id AND p.run_id=NEW.run_id
      AND p.item_id=NEW.id;
    IF expected_pairs<>jsonb_array_length(NEW.expected_hypothesis_keys) THEN
      RAISE EXCEPTION 'M30 item C9 cardinality drift';
    END IF;
  ELSIF TG_TABLE_NAME='sre_live_batches' THEN
    SELECT count(*),
           count(*) FILTER (WHERE p.state IN ('match','no_match','uncertain')),
           count(*) FILTER (WHERE v.result_id IS NOT NULL)
      INTO expected_pairs,evaluated_pairs,proven_pairs
    FROM sre_live_expected_pairs p
    LEFT JOIN sre_live_verified_pair_results v
      ON (v.project_id,v.run_id,v.item_id,v.category_key)=
         (p.project_id,p.run_id,p.item_id,p.category_key)
      AND v.decision=p.state
    WHERE p.project_id=NEW.project_id AND p.run_id=NEW.run_id
      AND p.batch_id=NEW.batch_id;
    IF NEW.state='completed' AND EXISTS (
      SELECT 1 FROM sre_live_batch_members m
      JOIN sre_live_items i ON (i.project_id,i.run_id,i.id)=
        (m.project_id,m.run_id,m.item_id)
      WHERE m.project_id=NEW.project_id AND m.run_id=NEW.run_id
        AND m.batch_id=NEW.batch_id AND i.state<>'completed'
    ) THEN RAISE EXCEPTION 'M30 batch cannot finish before all member items'; END IF;
  ELSE
    SELECT count(*),
           count(*) FILTER (WHERE p.state IN ('match','no_match','uncertain')),
           count(*) FILTER (WHERE v.result_id IS NOT NULL)
      INTO expected_pairs,evaluated_pairs,proven_pairs
    FROM sre_live_expected_pairs p
    LEFT JOIN sre_live_verified_pair_results v
      ON (v.project_id,v.run_id,v.item_id,v.category_key)=
         (p.project_id,p.run_id,p.item_id,p.category_key)
      AND v.decision=p.state
    WHERE p.project_id=NEW.project_id AND p.run_id=NEW.run_id;
    IF NEW.state='completed' THEN
      SELECT count(*) INTO unfinished_children
      FROM sre_live_items i
      WHERE i.project_id=NEW.project_id AND i.run_id=NEW.run_id
        AND i.state<>'completed';
      SELECT count(*) INTO expected_items FROM sre_live_items i
      WHERE i.project_id=NEW.project_id AND i.run_id=NEW.run_id;
      IF unfinished_children<>0 OR expected_items<>NEW.expected_items
         OR NEW.finished_items<>NEW.expected_items OR NEW.blocked_items<>0
         OR EXISTS (
           SELECT 1 FROM sre_live_batches b
           WHERE b.project_id=NEW.project_id AND b.run_id=NEW.run_id
             AND b.state<>'completed'
         )
      THEN RAISE EXCEPTION 'M30 run cannot finish before all items and batches'; END IF;
    END IF;
  END IF;
  IF expected_pairs=0 OR proven_pairs<>evaluated_pairs
     OR (NEW.state='completed' AND evaluated_pairs<>expected_pairs)
     OR (NEW.state='partial' AND
         (evaluated_pairs=0 OR evaluated_pairs>=expected_pairs))
  THEN RAISE EXCEPTION 'M30 incomplete/forged C9 decisions cannot become terminal'; END IF;
  -- UNKNOWN unaccounted provider intent overrides a misleading PARTIAL.
  IF EXISTS (
    SELECT 1 FROM sre_live_opus_dispatch_intents d
    LEFT JOIN sre_live_opus_receipts t
      ON t.project_id=d.project_id AND t.intent_id=d.intent_id
    WHERE d.project_id=NEW.project_id AND d.run_id=NEW.run_id
      AND t.receipt_id IS NULL
  ) THEN RAISE EXCEPTION 'M30 unresolved possibly paid provider intent'; END IF;
  RETURN NEW;
END;
$m30_final$;
CREATE TRIGGER sre_m30_item_final_guard
  BEFORE UPDATE ON sre_live_items
  FOR EACH ROW EXECUTE FUNCTION sre_m30_guard_final_state();
CREATE TRIGGER sre_m30_batch_final_guard
  BEFORE UPDATE ON sre_live_batches
  FOR EACH ROW EXECUTE FUNCTION sre_m30_guard_final_state();
CREATE TRIGGER sre_m30_run_final_guard
  BEFORE UPDATE ON sre_live_runs
  FOR EACH ROW EXECUTE FUNCTION sre_m30_guard_final_state();

REVOKE ALL ON sre_live_verified_pair_results FROM PUBLIC;
