-- M33 forward-only 067. Freeze privacy-minimal human-gold evaluation cohorts.
-- Source: exact, currently latest M32 human head and real M30 source receipt.
-- No training, no provider POST, no guessed model confidence or raw text.
CREATE TABLE sre_live_gold_benchmark_snapshots (
  benchmark_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  snapshot_sha256 TEXT NOT NULL CHECK(snapshot_sha256 ~ '^[0-9a-f]{64}$'),
  split_seed TEXT NOT NULL CHECK(split_seed ~ '^[A-Za-z0-9._-]{1,64}$'),
  holdout_percent INTEGER NOT NULL CHECK(holdout_percent BETWEEN 10 AND 90),
  manifest JSONB NOT NULL CHECK(
    jsonb_typeof(manifest)='array'
    AND jsonb_array_length(manifest) BETWEEN 1 AND 1000),
  actor_id TEXT NOT NULL CHECK(actor_id ~ '^[A-Za-z0-9:_@.-]{1,128}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,run_id,snapshot_sha256),
  FOREIGN KEY(run_id,project_id) REFERENCES sre_live_runs(run_id,project_id)
    ON DELETE RESTRICT
);
CREATE INDEX sre_m33_gold_project_time
  ON sre_live_gold_benchmark_snapshots(project_id,run_id,created_at,benchmark_id);

CREATE FUNCTION sre_m33_guard_gold_snapshot() RETURNS trigger
LANGUAGE plpgsql AS $m33_gold$
DECLARE sample JSONB;
DECLARE original_row RECORD;
DECLARE seen TEXT[] := ARRAY[]::TEXT[];
DECLARE sample_key TEXT;
DECLARE count_rows INTEGER := 0;
BEGIN
  IF TG_OP<>'INSERT' THEN
    RAISE EXCEPTION 'M33 gold benchmark snapshots are immutable and undeletable';
  END IF;
  PERFORM 1 FROM sre_workspaces
    WHERE project_id=NEW.project_id FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'M33 foreign project workspace'; END IF;
  IF NOT EXISTS (
    SELECT 1 FROM sre_live_runs r WHERE r.project_id=NEW.project_id
      AND r.run_id=NEW.run_id AND r.execution_mode='live'
      AND r.state IN ('queued','running','partial','completed')
      AND r.cancel_requested=FALSE
  ) THEN RAISE EXCEPTION 'M33 cancelled, unknown or non-LIVE source run'; END IF;
  FOR sample IN SELECT value FROM jsonb_array_elements(NEW.manifest)
  LOOP
    count_rows:=count_rows+1;
    IF jsonb_typeof(sample)<>'object'
      OR (SELECT count(*) FROM jsonb_object_keys(sample))<>14
      OR NOT (sample ?& ARRAY[
        'item_id','video_id','content_version_id','content_sha256',
        'category_key','original_result_id','human_review_id',
        'human_revision','model_decision','gold_decision',
        'quote_sha256','catalog_sha256','model_revision','split'])
      OR sample->>'split' NOT IN ('train','eval')
      OR sample->>'model_decision' NOT IN ('match','no_match','uncertain')
      OR sample->>'gold_decision' NOT IN ('match','no_match','uncertain')
    THEN RAISE EXCEPTION 'M33 malformed/missing gold source field'; END IF;
    sample_key:=(sample->>'item_id')||'/'||(sample->>'category_key');
    IF sample_key=ANY(seen) THEN
      RAISE EXCEPTION 'M33 duplicate source category in immutable cohort';
    END IF;
    seen:=array_append(seen,sample_key);
    SELECT v.result_id::text AS model_result_id,v.decision AS model,
           h.review_id::text AS review_id,h.revision,h.human_decision,
           i.video_id,i.content_version_id,i.content_sha256,
           d.quote_sha256,d.catalog_sha256,d.model_revision,
           t.generation_authenticated,t.evidence_status
      INTO original_row
      FROM sre_live_human_reviews h
      JOIN sre_live_verified_pair_results v
        ON v.project_id=h.project_id AND v.result_id=h.base_result_id
       AND v.run_id=h.run_id AND v.item_id=h.item_id
       AND v.category_key=h.category_key
      JOIN sre_live_items i
        ON i.project_id=v.project_id AND i.run_id=v.run_id AND i.id=v.item_id
      JOIN sre_live_opus_receipts t
        ON t.project_id=v.project_id AND t.receipt_id=v.receipt_id
      JOIN sre_live_opus_dispatch_intents d
        ON d.project_id=t.project_id AND d.intent_id=t.intent_id
      WHERE h.project_id=NEW.project_id AND h.run_id=NEW.run_id
        AND h.item_id=(sample->>'item_id')::bigint
        AND h.category_key=(sample->>'category_key')::uuid
        AND h.review_id=(sample->>'human_review_id')::uuid
        AND h.human_verified=TRUE
        AND NOT EXISTS (
          SELECT 1 FROM sre_live_human_reviews newer
          WHERE newer.project_id=h.project_id AND newer.run_id=h.run_id
            AND newer.item_id=h.item_id AND newer.category_key=h.category_key
            AND newer.revision>h.revision)
      FOR SHARE OF h,v,i,t,d;
    IF NOT FOUND OR original_row.model_result_id IS DISTINCT FROM
            sample->>'original_result_id'
       OR original_row.model IS DISTINCT FROM sample->>'model_decision'
       OR original_row.review_id IS DISTINCT FROM sample->>'human_review_id'
       OR original_row.revision::text IS DISTINCT FROM sample->>'human_revision'
       OR original_row.human_decision IS DISTINCT FROM sample->>'gold_decision'
       OR original_row.video_id::text IS DISTINCT FROM sample->>'video_id'
       OR original_row.content_version_id::text IS DISTINCT FROM sample->>'content_version_id'
       OR original_row.content_sha256 IS DISTINCT FROM sample->>'content_sha256'
       OR original_row.quote_sha256 IS DISTINCT FROM sample->>'quote_sha256'
       OR original_row.catalog_sha256 IS DISTINCT FROM sample->>'catalog_sha256'
       OR original_row.model_revision IS DISTINCT FROM sample->>'model_revision'
       OR original_row.generation_authenticated IS NOT TRUE
       OR original_row.evidence_status<>'validated_untrusted_output'
    THEN RAISE EXCEPTION 'M33 gold sample not current authenticated M30/M32 source'; END IF;
  END LOOP;
  IF count_rows<>jsonb_array_length(NEW.manifest) THEN
    RAISE EXCEPTION 'M33 gold sample count mismatch'; END IF;
  RETURN NEW;
END;
$m33_gold$;
CREATE TRIGGER sre_m33_gold_insert
  BEFORE INSERT ON sre_live_gold_benchmark_snapshots
  FOR EACH ROW EXECUTE FUNCTION sre_m33_guard_gold_snapshot();
CREATE TRIGGER sre_m33_gold_immutable
  BEFORE UPDATE OR DELETE ON sre_live_gold_benchmark_snapshots
  FOR EACH ROW EXECUTE FUNCTION sre_m33_guard_gold_snapshot();
REVOKE ALL ON sre_live_gold_benchmark_snapshots FROM PUBLIC;
