-- M32 forward-only 066: immutable authenticated human-review chain for
-- an ORIGINAL M30 source/receipt-backed category decision.
-- Original M30 verdict, M28 payment and M26 quote are never rewritten.
-- No prompt, provider output, raw D1 text, API keys or free-form reviewer notes.
CREATE UNIQUE INDEX sre_m32_verified_review_source
  ON sre_live_verified_pair_results (
    project_id,run_id,item_id,category_key,result_id,receipt_id,
    request_sha256,verified_chunk_sha256
  );

CREATE TABLE sre_live_human_reviews (
  review_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  item_id BIGINT NOT NULL,
  category_key UUID NOT NULL,
  base_result_id UUID NOT NULL,
  receipt_id UUID NOT NULL,
  request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  quote_sha256 TEXT NOT NULL CHECK (quote_sha256 ~ '^[0-9a-f]{64}$'),
  catalog_sha256 TEXT NOT NULL CHECK (catalog_sha256 ~ '^[0-9a-f]{64}$'),
  verified_chunk_sha256 TEXT NOT NULL
    CHECK (verified_chunk_sha256 ~ '^[0-9a-f]{64}$'),
  original_model_decision TEXT NOT NULL
    CHECK (original_model_decision IN ('match','no_match','uncertain')),
  human_decision TEXT NOT NULL
    CHECK (human_decision IN ('match','no_match','uncertain')),
  review_action TEXT NOT NULL
    CHECK (review_action IN ('confirm','correct','supersede')),
  reason_code TEXT NOT NULL CHECK (reason_code IN (
    'verified_model_decision','incorrect_positive','incorrect_negative',
    'insufficient_evidence','ambiguous_content','review_correction'
  )),
  reviewer_actor TEXT NOT NULL
    CHECK (reviewer_actor ~ '^[A-Za-z0-9:_@.-]{1,128}$'),
  human_verified BOOLEAN NOT NULL CHECK(human_verified IS TRUE),
  revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 10000),
  prior_review_id UUID,
  reviewed_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,review_id),
  UNIQUE(project_id,run_id,item_id,category_key,revision),
  UNIQUE(project_id,prior_review_id),
  FOREIGN KEY (
    project_id,run_id,item_id,category_key,base_result_id,receipt_id,
    request_sha256,verified_chunk_sha256
  ) REFERENCES sre_live_verified_pair_results (
    project_id,run_id,item_id,category_key,result_id,receipt_id,
    request_sha256,verified_chunk_sha256
  ) ON DELETE RESTRICT,
  FOREIGN KEY (project_id,prior_review_id)
    REFERENCES sre_live_human_reviews(project_id,review_id)
    ON DELETE RESTRICT,
  CHECK (
    (revision=1 AND prior_review_id IS NULL
      AND review_action IN ('confirm','correct'))
    OR
    (revision>1 AND prior_review_id IS NOT NULL
      AND review_action='supersede')
  )
);
CREATE INDEX sre_m32_human_queue
  ON sre_live_human_reviews(
    project_id,run_id,item_id,category_key,revision DESC
  );
CREATE INDEX sre_m32_human_actor_time
  ON sre_live_human_reviews(project_id,reviewer_actor,reviewed_at);

CREATE FUNCTION sre_m32_guard_human_review() RETURNS trigger
LANGUAGE plpgsql AS $m32_review$
DECLARE original_row RECORD;
DECLARE prior_row RECORD;
BEGIN
  IF TG_OP<>'INSERT' THEN
    RAISE EXCEPTION 'M32 human-review evidence is immutable and undeletable';
  END IF;
  PERFORM 1 FROM sre_workspaces WHERE project_id=NEW.project_id FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'M32 project workspace unavailable'; END IF;
  SELECT v.decision AS model_decision,
         t.evidence_status,t.generation_authenticated,
         d.quote_sha256,d.catalog_sha256,
         r.cancel_requested,r.state AS run_state,
         p.state AS current_pair_state
    INTO original_row
    FROM sre_live_verified_pair_results v
    JOIN sre_live_opus_receipts t
      ON t.project_id=v.project_id AND t.receipt_id=v.receipt_id
    JOIN sre_live_opus_dispatch_intents d
      ON d.project_id=t.project_id AND d.intent_id=t.intent_id
    JOIN sre_live_expected_pairs p
      ON p.project_id=v.project_id AND p.run_id=v.run_id
       AND p.item_id=v.item_id AND p.category_key=v.category_key
    JOIN sre_live_runs r
      ON r.project_id=v.project_id AND r.run_id=v.run_id
    WHERE v.project_id=NEW.project_id AND v.run_id=NEW.run_id
      AND v.item_id=NEW.item_id AND v.category_key=NEW.category_key
      AND v.result_id=NEW.base_result_id
      AND v.receipt_id=NEW.receipt_id
      AND v.request_sha256=NEW.request_sha256
      AND v.verified_chunk_sha256=NEW.verified_chunk_sha256
    FOR SHARE OF v,t,p,r;
  IF NOT FOUND OR original_row.model_decision IS DISTINCT FROM
       NEW.original_model_decision
     OR original_row.current_pair_state IS DISTINCT FROM
       NEW.original_model_decision
     OR original_row.quote_sha256 IS DISTINCT FROM NEW.quote_sha256
     OR original_row.catalog_sha256 IS DISTINCT FROM NEW.catalog_sha256
     OR original_row.generation_authenticated IS NOT TRUE
     OR original_row.evidence_status<>'validated_untrusted_output'
     OR original_row.cancel_requested IS TRUE
     OR original_row.run_state NOT IN ('queued','running','partial','completed')
  THEN
    RAISE EXCEPTION 'M32 requires the exact original verified model result in an active/reviewable LIVE run';
  END IF;
  IF NEW.revision=1 THEN
    IF EXISTS (
      SELECT 1 FROM sre_live_human_reviews previous
      WHERE previous.project_id=NEW.project_id
        AND previous.run_id=NEW.run_id AND previous.item_id=NEW.item_id
        AND previous.category_key=NEW.category_key
    ) THEN RAISE EXCEPTION 'M32 cannot rewrite existing human review chain'; END IF;
    IF (NEW.review_action='confirm' AND (
           NEW.human_decision<>NEW.original_model_decision
           OR NEW.reason_code<>'verified_model_decision'))
       OR (NEW.review_action='correct' AND (
           NEW.human_decision=NEW.original_model_decision
           OR NEW.reason_code='verified_model_decision'))
    THEN RAISE EXCEPTION 'M32 confirmation/correction does not match human decision'; END IF;
  ELSE
    SELECT prior.revision,prior.human_decision,prior.base_result_id,
           prior.run_id,prior.item_id,prior.category_key,prior.review_id
      INTO prior_row
      FROM sre_live_human_reviews prior
      WHERE prior.project_id=NEW.project_id
        AND prior.review_id=NEW.prior_review_id
      FOR UPDATE;
    IF NOT FOUND
       OR prior_row.base_result_id IS DISTINCT FROM NEW.base_result_id
       OR prior_row.run_id IS DISTINCT FROM NEW.run_id
       OR prior_row.item_id IS DISTINCT FROM NEW.item_id
       OR prior_row.category_key IS DISTINCT FROM NEW.category_key
       OR NEW.revision<>prior_row.revision+1
       OR NEW.human_decision=prior_row.human_decision
       OR NEW.reason_code='verified_model_decision'
       OR EXISTS (
         SELECT 1 FROM sre_live_human_reviews newer
         WHERE newer.project_id=NEW.project_id
           AND newer.prior_review_id=NEW.prior_review_id
       )
    THEN RAISE EXCEPTION 'M32 stale/foreign/forked supersession denied'; END IF;
  END IF;
  RETURN NEW;
END;
$m32_review$;

CREATE TRIGGER sre_m32_review_insert
  BEFORE INSERT ON sre_live_human_reviews
  FOR EACH ROW EXECUTE FUNCTION sre_m32_guard_human_review();
CREATE TRIGGER sre_m32_review_immutable
  BEFORE UPDATE OR DELETE ON sre_live_human_reviews
  FOR EACH ROW EXECUTE FUNCTION sre_m32_guard_human_review();
REVOKE ALL ON sre_live_human_reviews FROM PUBLIC;
