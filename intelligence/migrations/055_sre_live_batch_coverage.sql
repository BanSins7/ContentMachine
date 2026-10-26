-- M13 SRE LIVE exact batch membership and pair coverage registry.
-- ADDITIVE after 054; no changes to S4, finance, model output or paid dispatch.
-- Decisions (match/no_match/uncertain) are deliberately NOT writable here:
-- verified provider receipts/evidence will be added in later LIVE stages.

CREATE TABLE sre_live_batch_members (
  batch_id UUID NOT NULL,
  run_id UUID NOT NULL,
  project_id TEXT NOT NULL,
  item_id BIGINT NOT NULL,
  ordinal INTEGER NOT NULL CHECK(ordinal>=0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(batch_id,item_id),
  UNIQUE(batch_id,ordinal),
  UNIQUE(run_id,item_id),
  UNIQUE(batch_id,run_id,project_id,item_id),
  FOREIGN KEY(batch_id,run_id,project_id)
    REFERENCES sre_live_batches(batch_id,run_id,project_id),
  FOREIGN KEY(item_id,run_id,project_id)
    REFERENCES sre_live_items(id,run_id,project_id)
);
CREATE INDEX sre_m13_members_run ON sre_live_batch_members(project_id,run_id,item_id);

CREATE TABLE sre_live_expected_pairs (
  run_id UUID NOT NULL,
  project_id TEXT NOT NULL,
  batch_id UUID NOT NULL,
  item_id BIGINT NOT NULL,
  category_key UUID NOT NULL,
  state TEXT NOT NULL DEFAULT 'not_evaluated'
    CHECK(state IN ('not_evaluated','blocked_rights','blocked_budget')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(run_id,item_id,category_key),
  FOREIGN KEY(batch_id,run_id,project_id,item_id)
    REFERENCES sre_live_batch_members(batch_id,run_id,project_id,item_id)
);
CREATE INDEX sre_m13_pair_coverage
  ON sre_live_expected_pairs(project_id,run_id,batch_id,state);

-- Reject invented hypotheses and foreign data even for a DB caller who can
-- construct an apparently well-shaped JSON manifest.
CREATE FUNCTION sre_m13_assert_member() RETURNS trigger LANGUAGE plpgsql AS $m13_member$
DECLARE src RECORD;
BEGIN
  SELECT i.manifest_sha256,i.expected_hypothesis_keys,i.state,
         r.manifest_sha256 AS run_sha,r.manifest->'expected_hypothesis_keys' AS run_keys,
         r.state AS run_state
    INTO src
  FROM sre_live_items i JOIN sre_live_runs r
    ON r.run_id=i.run_id AND r.project_id=i.project_id
  WHERE i.id=NEW.item_id AND i.run_id=NEW.run_id AND i.project_id=NEW.project_id;
  IF NOT FOUND OR src.run_state <> 'pending_consent'
     OR src.state <> 'not_evaluated'
     OR src.manifest_sha256 IS DISTINCT FROM src.run_sha
     OR src.expected_hypothesis_keys IS DISTINCT FROM src.run_keys
  THEN RAISE EXCEPTION 'M13 membership requires exact, unapproved LIVE run input'; END IF;
  RETURN NEW;
END;
$m13_member$;
CREATE TRIGGER sre_m13_member_insert BEFORE INSERT ON sre_live_batch_members
  FOR EACH ROW EXECUTE FUNCTION sre_m13_assert_member();

CREATE FUNCTION sre_m13_assert_pair() RETURNS trigger LANGUAGE plpgsql AS $m13_pair$
DECLARE item_keys JSONB;
DECLARE current_state TEXT;
BEGIN
  SELECT i.expected_hypothesis_keys,r.state
    INTO item_keys,current_state
  FROM sre_live_items i JOIN sre_live_runs r
    ON r.run_id=i.run_id AND r.project_id=i.project_id
  WHERE i.id=NEW.item_id AND i.run_id=NEW.run_id AND i.project_id=NEW.project_id;
  IF NOT FOUND OR current_state <> 'pending_consent'
     OR NOT (item_keys ? (NEW.category_key::text))
  THEN RAISE EXCEPTION 'M13 pair not in approved pinned C9 leaf set'; END IF;
  RETURN NEW;
END;
$m13_pair$;
CREATE TRIGGER sre_m13_pair_insert BEFORE INSERT ON sre_live_expected_pairs
  FOR EACH ROW EXECUTE FUNCTION sre_m13_assert_pair();

-- Membership and pair ID/owner/category are permanently immutable. A later
-- rights check may mark an unverified pair blocked; never call it no_match.
CREATE FUNCTION sre_m13_guard_pair() RETURNS trigger LANGUAGE plpgsql AS $m13_pair_guard$
BEGIN
  IF (OLD.run_id,OLD.project_id,OLD.batch_id,OLD.item_id,
      OLD.category_key,OLD.created_at) IS DISTINCT FROM
     (NEW.run_id,NEW.project_id,NEW.batch_id,NEW.item_id,
      NEW.category_key,NEW.created_at)
  THEN RAISE EXCEPTION 'M13 immutable expected pair identity'; END IF;
  IF OLD.state <> 'not_evaluated' AND NEW.state IS DISTINCT FROM OLD.state
  THEN RAISE EXCEPTION 'M13 blocked pair cannot be silently reclassified'; END IF;
  RETURN NEW;
END;
$m13_pair_guard$;
CREATE TRIGGER sre_m13_pair_update BEFORE UPDATE ON sre_live_expected_pairs
  FOR EACH ROW EXECUTE FUNCTION sre_m13_guard_pair();

CREATE FUNCTION sre_m13_guard_member() RETURNS trigger LANGUAGE plpgsql AS $m13_member_guard$
BEGIN
  RAISE EXCEPTION 'M13 batch membership is immutable';
END;
$m13_member_guard$;
CREATE TRIGGER sre_m13_member_update BEFORE UPDATE OR DELETE ON sre_live_batch_members
  FOR EACH ROW EXECUTE FUNCTION sre_m13_guard_member();
CREATE TRIGGER sre_m13_pair_delete BEFORE DELETE ON sre_live_expected_pairs
  FOR EACH ROW EXECUTE FUNCTION sre_m13_guard_member();

-- Fail-closed settlement fence: a run, batch or item cannot claim completed
-- or partial until the *entire* pinned pair set is physically present AND
-- every evaluated label has validated evidence/receipts. M13 creates only
-- pending/blocked statuses, so no LIVE completion is possible before future
-- verified evaluation stages expand the pair-state protocol.
CREATE FUNCTION sre_m13_guard_settlement() RETURNS trigger LANGUAGE plpgsql AS $m13_settle$
DECLARE total_pairs BIGINT;
DECLARE unresolved_pairs BIGINT;
DECLARE expected_pairs BIGINT;
DECLARE expected_items BIGINT;
DECLARE covered_items BIGINT;
BEGIN
  IF NEW.state NOT IN ('completed','partial') THEN RETURN NEW; END IF;
  IF TG_TABLE_NAME='sre_live_items' THEN
    SELECT jsonb_array_length(NEW.expected_hypothesis_keys) INTO expected_pairs;
    SELECT count(*),count(*) FILTER (WHERE state NOT IN ('match','no_match','uncertain'))
      INTO total_pairs,unresolved_pairs
    FROM sre_live_expected_pairs
    WHERE project_id=NEW.project_id AND run_id=NEW.run_id AND item_id=NEW.id;
    IF expected_pairs<>total_pairs OR unresolved_pairs>0 THEN
      RAISE EXCEPTION 'M13 item incomplete leaf coverage'; END IF;
  ELSIF TG_TABLE_NAME='sre_live_batches' THEN
    SELECT count(*) INTO expected_items FROM sre_live_batch_members
    WHERE project_id=NEW.project_id AND run_id=NEW.run_id AND batch_id=NEW.batch_id;
    SELECT count(*),count(*) FILTER (WHERE p.state NOT IN ('match','no_match','uncertain'))
      INTO total_pairs,unresolved_pairs
    FROM sre_live_expected_pairs p
    WHERE p.project_id=NEW.project_id AND p.run_id=NEW.run_id
      AND p.batch_id=NEW.batch_id;
    IF expected_items=0 OR total_pairs=0 OR unresolved_pairs>0 THEN
      RAISE EXCEPTION 'M13 batch incomplete leaf coverage'; END IF;
  ELSE
    SELECT expected_items INTO expected_items FROM sre_live_runs
      WHERE run_id=NEW.run_id AND project_id=NEW.project_id;
    SELECT count(*) INTO covered_items FROM sre_live_batch_members
      WHERE project_id=NEW.project_id AND run_id=NEW.run_id;
    SELECT count(*),count(*) FILTER (WHERE state NOT IN ('match','no_match','uncertain'))
      INTO total_pairs,unresolved_pairs
    FROM sre_live_expected_pairs
      WHERE project_id=NEW.project_id AND run_id=NEW.run_id;
    IF expected_items IS NULL OR covered_items<>expected_items
       OR total_pairs=0 OR unresolved_pairs>0 THEN
      RAISE EXCEPTION 'M13 run incomplete video/hypothesis coverage'; END IF;
  END IF;
  RETURN NEW;
END;
$m13_settle$;
CREATE TRIGGER sre_m13_item_settlement BEFORE UPDATE ON sre_live_items
  FOR EACH ROW EXECUTE FUNCTION sre_m13_guard_settlement();
CREATE TRIGGER sre_m13_batch_settlement BEFORE UPDATE ON sre_live_batches
  FOR EACH ROW EXECUTE FUNCTION sre_m13_guard_settlement();
CREATE TRIGGER sre_m13_run_settlement BEFORE UPDATE ON sre_live_runs
  FOR EACH ROW EXECUTE FUNCTION sre_m13_guard_settlement();

REVOKE ALL ON sre_live_batch_members,sre_live_expected_pairs FROM PUBLIC;
