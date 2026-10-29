-- M36 forward-only 070. QDP is NOT installed or called by this migration.
-- LIVE result publication requires genuine M30 authenticated receipt + latest
-- M32 human review for *every* original approved C9 hypothesis.
CREATE VIEW sre_m36_live_reviewed_pairs AS
SELECT v.project_id,v.run_id,v.item_id,v.category_key,
       h.human_decision,h.review_id,v.decision AS original_model_decision,
       v.receipt_id,v.verified_chunk_sha256
FROM sre_live_verified_pair_results v
JOIN sre_live_runs r
 ON r.project_id=v.project_id AND r.run_id=v.run_id
JOIN sre_live_items i
 ON i.project_id=v.project_id AND i.run_id=v.run_id AND i.id=v.item_id
JOIN sre_live_expected_pairs p
 ON p.project_id=v.project_id AND p.run_id=v.run_id
 AND p.item_id=v.item_id AND p.category_key=v.category_key
JOIN sre_live_opus_receipts receipt
 ON receipt.project_id=v.project_id AND receipt.receipt_id=v.receipt_id
JOIN sre_readable_content_versions c
 ON c.project_id=i.project_id AND c.id=i.content_version_id
 AND c.content_sha256=i.content_sha256
JOIN LATERAL (
 SELECT h.review_id,h.human_decision,h.original_model_decision,h.base_result_id
 FROM sre_live_human_reviews h
 WHERE h.project_id=v.project_id AND h.run_id=v.run_id
   AND h.item_id=v.item_id AND h.category_key=v.category_key
 ORDER BY h.revision DESC LIMIT 1
) h ON TRUE
WHERE r.execution_mode='live' AND r.state='completed'
  AND r.cancel_requested=FALSE AND i.state='completed'
  AND p.state=v.decision AND h.original_model_decision=v.decision
  AND h.base_result_id=v.result_id
  AND receipt.generation_authenticated IS TRUE
  AND receipt.evidence_status='validated_untrusted_output'
  AND receipt.evidence_sha256=v.verified_chunk_sha256
  AND NOT EXISTS (
     SELECT 1 FROM sre_live_source_tombstones tomb
     WHERE tomb.project_id=i.project_id
       AND tomb.content_version_id=i.content_version_id
  );

CREATE TABLE sre_live_result_outbox (
 event_id UUID PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES sre_workspaces(project_id),
 run_id UUID NOT NULL,
 item_id BIGINT NOT NULL,
 content_version_id BIGINT NOT NULL CHECK(content_version_id>0),
 kind TEXT NOT NULL CHECK(kind IN ('ready','invalidated')),
 prior_ready_event_id UUID,
 payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object'),
 event_sha256 TEXT NOT NULL CHECK(event_sha256 ~ '^[0-9a-f]{64}$'),
 state TEXT NOT NULL DEFAULT 'pending'
   CHECK(state IN ('pending','leased','acked')),
 lease_token UUID,
 lease_generation BIGINT NOT NULL DEFAULT 0 CHECK(lease_generation>=0),
 lease_until TIMESTAMPTZ,
 attempt_count INTEGER NOT NULL DEFAULT 0 CHECK(attempt_count BETWEEN 0 AND 10000),
 acked_at TIMESTAMPTZ,
 actor_id TEXT NOT NULL CHECK(actor_id ~ '^[A-Za-z0-9:_@.-]{1,128}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(project_id,event_id),
 FOREIGN KEY(item_id,run_id,project_id)
   REFERENCES sre_live_items(id,run_id,project_id) ON DELETE RESTRICT,
 FOREIGN KEY(project_id,prior_ready_event_id)
   REFERENCES sre_live_result_outbox(project_id,event_id) ON DELETE RESTRICT,
 CHECK((kind='ready' AND prior_ready_event_id IS NULL)
    OR (kind='invalidated' AND prior_ready_event_id IS NOT NULL)),
 CHECK(
   (state='pending' AND lease_token IS NULL AND lease_until IS NULL
     AND acked_at IS NULL)
   OR (state='leased' AND lease_token IS NOT NULL
     AND lease_until IS NOT NULL AND acked_at IS NULL)
   OR (state='acked' AND lease_token IS NULL AND lease_until IS NULL
     AND acked_at IS NOT NULL)),
 CHECK(payload->>'event_id'=event_id::text
       AND payload->>'event_sha256'=event_sha256
       AND payload->>'project_id'=project_id
       AND payload->>'run_id'=run_id::text
       AND payload->>'content_version_id'=content_version_id::text)
);
CREATE UNIQUE INDEX sre_m36_one_ready_per_live_item
 ON sre_live_result_outbox(project_id,run_id,item_id) WHERE kind='ready';
CREATE UNIQUE INDEX sre_m36_one_invalidation_per_ready
 ON sre_live_result_outbox(project_id,prior_ready_event_id)
 WHERE kind='invalidated';
CREATE INDEX sre_m36_claim_queue
 ON sre_live_result_outbox(project_id,state,created_at,event_id)
 WHERE state IN ('pending','leased');

-- Durable, immutable original source-revocation or reviewer-supersession
-- intent. Materialization to an envelope is explicit/host driven. The intent
-- blocks ready delivery immediately, even if the host worker crashes.
CREATE TABLE sre_live_result_invalidation_intents (
 project_id TEXT NOT NULL,
 ready_event_id UUID NOT NULL,
 reason_code TEXT NOT NULL CHECK(reason_code IN
   ('source_revoked','rights_lost','review_superseded','operator_revoked')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(project_id,ready_event_id),
 FOREIGN KEY(project_id,ready_event_id)
   REFERENCES sre_live_result_outbox(project_id,event_id) ON DELETE RESTRICT
);
CREATE INDEX sre_m36_pending_invalidations
 ON sre_live_result_invalidation_intents(project_id,created_at,ready_event_id);

CREATE FUNCTION sre_m36_guard_result_outbox() RETURNS trigger
LANGUAGE plpgsql AS $m36_outbox$
DECLARE src RECORD;
DECLARE expected_count BIGINT;
DECLARE reviewed_count BIGINT;
DECLARE expected_decisions JSONB;
DECLARE prior_row RECORD;
BEGIN
 IF TG_OP='DELETE' THEN
   RAISE EXCEPTION 'M36 outbox immutable event deletion denied';
 END IF;
 IF TG_OP='UPDATE' THEN
   IF OLD.state='acked'
      OR NEW.event_id IS DISTINCT FROM OLD.event_id
      OR NEW.project_id IS DISTINCT FROM OLD.project_id
      OR NEW.run_id IS DISTINCT FROM OLD.run_id
      OR NEW.item_id IS DISTINCT FROM OLD.item_id
      OR NEW.content_version_id IS DISTINCT FROM OLD.content_version_id
      OR NEW.kind IS DISTINCT FROM OLD.kind
      OR NEW.prior_ready_event_id IS DISTINCT FROM OLD.prior_ready_event_id
      OR NEW.payload IS DISTINCT FROM OLD.payload
      OR NEW.event_sha256 IS DISTINCT FROM OLD.event_sha256
      OR NEW.actor_id IS DISTINCT FROM OLD.actor_id
      OR NEW.created_at IS DISTINCT FROM OLD.created_at
      OR (OLD.state='pending' AND NEW.state<>'leased')
      OR (OLD.state='leased' AND NEW.state NOT IN ('leased','acked'))
      OR (NEW.state='leased' AND
          (NEW.lease_token IS NULL OR NEW.lease_until<=clock_timestamp()
           OR NEW.lease_generation<>OLD.lease_generation+1
           OR NEW.attempt_count<>OLD.attempt_count+1
           OR NEW.acked_at IS NOT NULL))
      OR (NEW.state='acked' AND
           (OLD.state<>'leased' OR OLD.lease_token IS NULL
            OR NEW.lease_generation<>OLD.lease_generation
            OR NEW.attempt_count<>OLD.attempt_count
            OR OLD.lease_until<clock_timestamp()
            OR NEW.acked_at IS NULL OR NEW.lease_token IS NOT NULL
            OR NEW.lease_until IS NOT NULL))
   THEN RAISE EXCEPTION 'M36 outbox invalid lease/ACK/immutable mutation'; END IF;
   RETURN NEW;
 END IF;
 PERFORM 1 FROM sre_workspaces WHERE project_id=NEW.project_id FOR SHARE;
 IF NOT FOUND THEN RAISE EXCEPTION 'M36 workspace absent'; END IF;
 IF NEW.state<>'pending' OR NEW.attempt_count<>0
    OR NEW.lease_generation<>0 OR NEW.acked_at IS NOT NULL THEN
    RAISE EXCEPTION 'M36 new envelope must be pending'; END IF;
 IF NEW.kind='ready' THEN
   SELECT r.execution_mode,r.state AS run_state,r.cancel_requested,
          r.publication_id,r.taxonomy_version_id,r.taxonomy_sha256,
          r.expected_items,r.finished_items,r.blocked_items,
          i.state AS item_state,i.video_id,i.content_version_id,
          i.content_sha256,i.source_policy_version_id,
          i.expected_hypothesis_keys
   INTO src FROM sre_live_runs r JOIN sre_live_items i
     ON i.project_id=r.project_id AND i.run_id=r.run_id
     WHERE r.project_id=NEW.project_id AND r.run_id=NEW.run_id
       AND i.id=NEW.item_id FOR SHARE OF r,i;
   IF NOT FOUND OR src.execution_mode<>'live' OR src.run_state<>'completed'
      OR src.cancel_requested IS NOT FALSE
      OR src.item_state<>'completed'
      OR src.expected_items<>src.finished_items OR src.blocked_items<>0
      OR src.content_version_id<>NEW.content_version_id
      OR NEW.payload->>'schema_version'<>'sre.classifications_ready/v1'
      OR NEW.payload->>'execution_mode'<>'live'
      OR NEW.payload->>'run_status'<>'completed'
      OR NEW.payload->>'publication_id'<>src.publication_id::text
      OR NEW.payload->>'sre_taxonomy_version_id'<>src.taxonomy_version_id::text
      OR NEW.payload->>'taxonomy_sha256'<>src.taxonomy_sha256
      OR NEW.payload->>'video_id'<>src.video_id::text
      OR NEW.payload->>'content_sha256'<>src.content_sha256
      OR NEW.payload->>'source_policy_version_id'<>src.source_policy_version_id::text
      OR NEW.payload->'expected_hypothesis_keys'<>src.expected_hypothesis_keys
      OR NOT EXISTS (
        SELECT 1 FROM sre_readable_content_versions readable
        WHERE readable.project_id=NEW.project_id AND readable.id=NEW.content_version_id
      )
      OR EXISTS(
        SELECT 1 FROM sre_live_result_invalidation_intents intent
        WHERE intent.project_id=NEW.project_id
          AND intent.ready_event_id=NEW.event_id
      )
   THEN RAISE EXCEPTION 'M36 only current completed LIVE source/approved C9 result can publish'; END IF;
   SELECT count(*) INTO expected_count
   FROM sre_live_expected_pairs p
   WHERE p.project_id=NEW.project_id AND p.run_id=NEW.run_id AND p.item_id=NEW.item_id;
   SELECT count(*),
      jsonb_build_object(
       'match',COALESCE(jsonb_agg(v.category_key::text ORDER BY v.category_key::text)
                 FILTER(WHERE v.human_decision='match'),'[]'::jsonb),
       'no_match',COALESCE(jsonb_agg(v.category_key::text ORDER BY v.category_key::text)
                 FILTER(WHERE v.human_decision='no_match'),'[]'::jsonb),
       'uncertain',COALESCE(jsonb_agg(v.category_key::text ORDER BY v.category_key::text)
                 FILTER(WHERE v.human_decision='uncertain'),'[]'::jsonb)
      )
     INTO reviewed_count,expected_decisions
   FROM sre_m36_live_reviewed_pairs v
   WHERE v.project_id=NEW.project_id AND v.run_id=NEW.run_id AND v.item_id=NEW.item_id;
   IF expected_count=0 OR expected_count<>reviewed_count
      OR expected_count<>jsonb_array_length(src.expected_hypothesis_keys)
      OR NEW.payload->'decisions' IS DISTINCT FROM expected_decisions
   THEN RAISE EXCEPTION 'M36 incomplete receipt-backed human reviewed C9 coverage'; END IF;
   IF EXISTS (
     SELECT 1 FROM sre_live_opus_dispatch_intents d
     LEFT JOIN sre_live_opus_receipts t
       ON t.project_id=d.project_id AND t.intent_id=d.intent_id
     WHERE d.project_id=NEW.project_id AND d.run_id=NEW.run_id
       AND t.receipt_id IS NULL
   ) THEN RAISE EXCEPTION 'M36 UNKNOWN paid intent cannot publish'; END IF;
 ELSE
   SELECT prior.event_id,prior.project_id,prior.run_id,prior.item_id,
          prior.content_version_id,prior.payload,prior.event_sha256
     INTO prior_row FROM sre_live_result_outbox prior
     WHERE prior.project_id=NEW.project_id
       AND prior.event_id=NEW.prior_ready_event_id
       AND prior.kind='ready' FOR SHARE;
   IF NOT FOUND OR NEW.run_id<>prior_row.run_id
     OR NEW.item_id<>prior_row.item_id
     OR NEW.content_version_id<>prior_row.content_version_id
     OR NEW.payload->>'schema_version'<>'sre.results_invalidated/v1'
     OR NEW.payload->>'prior_ready_event_id'<>prior_row.event_id::text
     OR NEW.payload->>'prior_ready_event_sha256'<>prior_row.event_sha256
     OR NEW.payload->>'content_sha256'<>prior_row.payload->>'content_sha256'
     OR NOT EXISTS(
       SELECT 1 FROM sre_live_result_invalidation_intents intent
       WHERE intent.project_id=NEW.project_id
         AND intent.ready_event_id=NEW.prior_ready_event_id
         AND intent.reason_code=NEW.payload->>'reason_code')
   THEN RAISE EXCEPTION 'M36 invalidation must link immutable ready event and durable intent'; END IF;
 END IF;
 RETURN NEW;
END;
$m36_outbox$;
CREATE TRIGGER sre_m36_event_guard
 BEFORE INSERT OR UPDATE OR DELETE ON sre_live_result_outbox
 FOR EACH ROW EXECUTE FUNCTION sre_m36_guard_result_outbox();

CREATE FUNCTION sre_m36_guard_intent() RETURNS trigger
LANGUAGE plpgsql AS $m36_intent$
BEGIN
 IF TG_OP<>'INSERT' THEN RAISE EXCEPTION 'M36 invalidation intent immutable'; END IF;
 IF NOT EXISTS(
   SELECT 1 FROM sre_live_result_outbox e
   WHERE e.project_id=NEW.project_id AND e.event_id=NEW.ready_event_id
     AND e.kind='ready'
 ) THEN RAISE EXCEPTION 'M36 invalidation requires real ready event'; END IF;
 RETURN NEW;
END;
$m36_intent$;
CREATE TRIGGER sre_m36_intent_guard
 BEFORE INSERT OR UPDATE OR DELETE ON sre_live_result_invalidation_intents
 FOR EACH ROW EXECUTE FUNCTION sre_m36_guard_intent();

-- Applied AFTER M34 tombstone and M32 supersession, without modifying either
-- historical 068 or 066 migration. Transactional insertion blocks ready claim
-- immediately; delivery of the invalidation is an explicit host action.
CREATE FUNCTION sre_m36_source_tombstone_intent() RETURNS trigger
LANGUAGE plpgsql AS $m36_revoke$
BEGIN
 INSERT INTO sre_live_result_invalidation_intents
  (project_id,ready_event_id,reason_code)
 SELECT e.project_id,e.event_id,'source_revoked'
 FROM sre_live_result_outbox e
 WHERE e.project_id=NEW.project_id
   AND e.content_version_id=NEW.content_version_id AND e.kind='ready'
 ON CONFLICT DO NOTHING;
 RETURN NEW;
END;
$m36_revoke$;
CREATE TRIGGER sre_m36_source_tombstone_intent
 AFTER INSERT ON sre_live_source_tombstones
 FOR EACH ROW EXECUTE FUNCTION sre_m36_source_tombstone_intent();

CREATE FUNCTION sre_m36_human_supersession_intent() RETURNS trigger
LANGUAGE plpgsql AS $m36_review$
BEGIN
 IF NEW.revision>1 THEN
   INSERT INTO sre_live_result_invalidation_intents
    (project_id,ready_event_id,reason_code)
   SELECT e.project_id,e.event_id,'review_superseded'
   FROM sre_live_result_outbox e
   WHERE e.project_id=NEW.project_id AND e.run_id=NEW.run_id
     AND e.item_id=NEW.item_id AND e.kind='ready'
   ON CONFLICT DO NOTHING;
 END IF;
 RETURN NEW;
END;
$m36_review$;
CREATE TRIGGER sre_m36_review_supersession_intent
 AFTER INSERT ON sre_live_human_reviews
 FOR EACH ROW EXECUTE FUNCTION sre_m36_human_supersession_intent();

-- READ ONLY result projection for the future QDP adapter. A ready event alone
-- is never a permanent permission to read a source after tombstone/rights loss.
CREATE VIEW sre_m36_live_only_export AS
SELECT e.project_id,e.event_id,e.event_sha256,e.run_id,e.item_id,
       e.content_version_id,p.category_key,p.human_decision,
       p.original_model_decision,p.review_id,p.receipt_id,
       p.verified_chunk_sha256
FROM sre_live_result_outbox e
JOIN sre_m36_live_reviewed_pairs p
 ON p.project_id=e.project_id AND p.run_id=e.run_id AND p.item_id=e.item_id
WHERE e.kind='ready'
 AND NOT EXISTS (
   SELECT 1 FROM sre_live_result_invalidation_intents i
   WHERE i.project_id=e.project_id AND i.ready_event_id=e.event_id
 );

REVOKE ALL ON sre_live_result_outbox FROM PUBLIC;
REVOKE ALL ON sre_live_result_invalidation_intents FROM PUBLIC;
REVOKE ALL ON FUNCTION sre_m36_guard_result_outbox() FROM PUBLIC;
REVOKE ALL ON FUNCTION sre_m36_guard_intent() FROM PUBLIC;
REVOKE ALL ON FUNCTION sre_m36_source_tombstone_intent() FROM PUBLIC;
REVOKE ALL ON FUNCTION sre_m36_human_supersession_intent() FROM PUBLIC;
REVOKE ALL ON sre_m36_live_only_export FROM PUBLIC;
REVOKE ALL ON sre_m36_live_reviewed_pairs FROM PUBLIC;
