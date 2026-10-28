-- PIE-MOD-32: append-only, never-publish reviewer decisions, suggestions,
-- disagreement/appeal resolution, and independently labelled calibration sets.
-- This is NOT the legacy pie_insight_reviews table and cannot alter proposals.
CREATE TABLE pie_candidate_insight_review_events (
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
  generation_sha256 TEXT NOT NULL CHECK (generation_sha256 ~ '^[0-9a-f]{64}$'),
  verification_sha256 TEXT NOT NULL CHECK (verification_sha256 ~ '^[0-9a-f]{64}$'),
  event_seq INTEGER NOT NULL CHECK (event_seq BETWEEN 1 AND 100000),
  parent_event_sha256 TEXT CHECK (parent_event_sha256 IS NULL OR parent_event_sha256 ~ '^[0-9a-f]{64}$'),
  event_sha256 TEXT NOT NULL CHECK (event_sha256 ~ '^[0-9a-f]{64}$'),
  request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  review_edit_version INTEGER NOT NULL DEFAULT 0 CHECK (review_edit_version BETWEEN 0 AND 100000),
  actor_id TEXT NOT NULL CHECK (length(actor_id) BETWEEN 1 AND 160 AND actor_id ~ '^[A-Za-z0-9_.:@-]+$'),
  actor_role TEXT NOT NULL CHECK (actor_role IN ('reviewer','appellant','adjudicator','curator')),
  grant_sha256 TEXT NOT NULL CHECK (grant_sha256 ~ '^[0-9a-f]{64}$'),
  event_kind TEXT NOT NULL CHECK (event_kind IN (
    'review_decision','change_suggestion','appeal','adjudication',
    'calibration_vote','calibration_gold')),
  decision_code TEXT,
  reason_code TEXT NOT NULL CHECK (reason_code IN (
    'evidence_consistent','insufficient_support','numerical_disagreement',
    'rights_or_scope_dispute','missing_counterevidence',
    'clarify_uncertainty','clarify_scope','correct_source_unit',
    'request_more_evidence','challenge_prior_decision','policy_reassessment',
    'independent_calibration','unresolved_disagreement')),
  edit_code TEXT,
  calibration_label TEXT,
  target_event_sha256 TEXT,
  event_payload JSONB NOT NULL CHECK (jsonb_typeof(event_payload)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY (project_id,generation_sha256,event_seq),
  UNIQUE (project_id,event_sha256),
  UNIQUE (project_id,generation_sha256,request_sha256),
  FOREIGN KEY (project_id,generation_sha256)
    REFERENCES pie_candidate_insight_generations(project_id,generation_sha256)
    ON DELETE CASCADE,
  FOREIGN KEY (project_id,verification_sha256)
    REFERENCES pie_candidate_insight_verification_events(project_id,verification_sha256)
    ON DELETE CASCADE,
  CHECK ((event_kind='change_suggestion' AND review_edit_version>0)
      OR (event_kind<>'change_suggestion' AND review_edit_version=0)),
  CHECK ((event_seq=1 AND parent_event_sha256 IS NULL) OR
         (event_seq>1 AND parent_event_sha256 IS NOT NULL)),
  CHECK (
    (event_kind='review_decision' AND actor_role='reviewer'
      AND decision_code IN ('review_candidate_internal','reject_candidate','request_changes')
      AND edit_code IS NULL AND calibration_label IS NULL AND target_event_sha256 IS NULL)
    OR
    (event_kind='change_suggestion' AND actor_role='reviewer'
      AND decision_code IS NULL
      AND edit_code IN ('clarify_uncertainty','clarify_scope','correct_source_unit',
                        'add_caveat','retest_hypothesis')
      AND calibration_label IS NULL AND target_event_sha256 IS NULL)
    OR
    (event_kind='appeal' AND actor_role='appellant'
      AND decision_code IS NULL AND edit_code IS NULL AND calibration_label IS NULL
      AND target_event_sha256 ~ '^[0-9a-f]{64}$')
    OR
    (event_kind='adjudication' AND actor_role='adjudicator'
      AND decision_code IN ('uphold_rejection','return_to_review')
      AND edit_code IS NULL AND calibration_label IS NULL
      AND target_event_sha256 ~ '^[0-9a-f]{64}$')
    OR
    (event_kind='calibration_vote' AND actor_role='reviewer'
      AND decision_code IS NULL AND edit_code IS NULL AND target_event_sha256 IS NULL
      AND calibration_label IN ('candidate_consistent','candidate_contradictory','uncertain'))
    OR
    (event_kind='calibration_gold' AND actor_role='curator'
      AND decision_code IS NULL AND edit_code IS NULL AND target_event_sha256 IS NULL
      AND calibration_label IN ('candidate_consistent','candidate_contradictory','uncertain'))
  ),
  CHECK ((event_payload->>'schema_version') IS NOT DISTINCT FROM 'pie-human-review-event/v1'),
  CHECK ((event_payload->>'project_id') IS NOT DISTINCT FROM project_id),
  CHECK ((event_payload->>'generation_sha256') IS NOT DISTINCT FROM generation_sha256),
  CHECK ((event_payload->>'verification_sha256') IS NOT DISTINCT FROM verification_sha256),
  CHECK ((event_payload->>'event_seq') IS NOT DISTINCT FROM event_seq::text),
  CHECK ((event_payload->>'parent_event_sha256') IS NOT DISTINCT FROM parent_event_sha256),
  CHECK ((event_payload->>'request_sha256') IS NOT DISTINCT FROM request_sha256),
  CHECK ((event_payload->>'review_edit_version') IS NOT DISTINCT FROM review_edit_version::text),
  CHECK ((event_payload->>'actor_id') IS NOT DISTINCT FROM actor_id),
  CHECK ((event_payload->>'actor_role') IS NOT DISTINCT FROM actor_role),
  CHECK ((event_payload->>'grant_sha256') IS NOT DISTINCT FROM grant_sha256),
  CHECK ((event_payload->>'event_kind') IS NOT DISTINCT FROM event_kind),
  CHECK ((event_payload->>'decision_code') IS NOT DISTINCT FROM decision_code),
  CHECK ((event_payload->>'reason_code') IS NOT DISTINCT FROM reason_code),
  CHECK ((event_payload->>'edit_code') IS NOT DISTINCT FROM edit_code),
  CHECK ((event_payload->>'calibration_label') IS NOT DISTINCT FROM calibration_label),
  CHECK ((event_payload->>'target_event_sha256') IS NOT DISTINCT FROM target_event_sha256),
  CHECK ((event_payload->'may_publish') IS NOT DISTINCT FROM 'false'::jsonb),
  CHECK ((event_payload->'may_public_read') IS NOT DISTINCT FROM 'false'::jsonb),
  CHECK ((event_payload->'original_insight_modified') IS NOT DISTINCT FROM 'false'::jsonb)
);
CREATE INDEX pie_candidate_insight_review_history
  ON pie_candidate_insight_review_events(project_id,generation_sha256,event_seq DESC);

CREATE UNIQUE INDEX pie_candidate_review_one_calibration_vote_per_reviewer
  ON pie_candidate_insight_review_events(project_id,generation_sha256,actor_id)
  WHERE event_kind='calibration_vote';
CREATE UNIQUE INDEX pie_candidate_review_one_appeal_per_decision
  ON pie_candidate_insight_review_events(project_id,generation_sha256,target_event_sha256)
  WHERE event_kind='appeal';
CREATE UNIQUE INDEX pie_candidate_review_one_adjudication_per_appeal
  ON pie_candidate_insight_review_events(project_id,generation_sha256,target_event_sha256)
  WHERE event_kind='adjudication';

CREATE TRIGGER pie_candidate_review_event_immutable
 BEFORE UPDATE ON pie_candidate_insight_review_events FOR EACH ROW
 EXECUTE FUNCTION pie_reject_evidence_update();

-- SQL guards work even for an app bug or direct SQL writer. Live reviewer
-- authentication, approval-grant signatures and external rights remain HOST ONLY.
CREATE FUNCTION pie_candidate_review_chain_guard()
RETURNS trigger LANGUAGE plpgsql AS $pie32$
DECLARE latest RECORD;
DECLARE target RECORD;
DECLARE prior RECORD;
DECLARE source_series TEXT;
DECLARE source_revision INTEGER;
DECLARE review_verdict TEXT;
DECLARE voters_count INTEGER;
DECLARE agreeing_count INTEGER;
DECLARE edit_count INTEGER;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended(
    'pie-review:' || NEW.project_id || ':' || NEW.generation_sha256,0));

  SELECT g.series_id,g.source_revision INTO source_series,source_revision
    FROM pie_candidate_insight_generations g
    WHERE g.project_id=NEW.project_id
      AND g.generation_sha256=NEW.generation_sha256;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'PIE review source missing';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pie_pattern_registry_revisions newer
     WHERE newer.project_id=NEW.project_id AND newer.series_id=source_series
       AND newer.revision>source_revision
  ) THEN
    RAISE EXCEPTION 'PIE review cannot target stale pattern revision';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pie_pattern_registry_revisions r
    JOIN pie_pattern_registry_invalidations i
      ON i.project_id=r.project_id AND (
        (i.target_kind='uae_manifest' AND i.target_sha256=r.payload->>'dataset_manifest_sha256')
        OR (i.target_kind='dataset_snapshot' AND i.target_sha256=r.source_snapshot_sha256)
        OR (i.target_kind='matched_rows' AND i.target_sha256=r.source_rows_sha256)
        OR (i.target_kind='quality_report' AND i.target_sha256=r.source_quality_sha256)
        OR (i.target_kind='holdout' AND i.target_sha256=r.payload->>'source_holdout_sha256')
        OR (i.target_kind='numeric_report' AND i.target_sha256=r.payload->>'source_technical_numeric_sha256')
        OR (i.target_kind='robustness_report' AND i.target_sha256=r.payload->>'source_robustness_sha256')
        OR (i.target_kind='study_design' AND i.target_sha256=r.payload->>'study_design_sha256')
        OR (i.target_kind='stratum' AND i.target_sha256=r.stratum_sha256)
      )
    WHERE r.project_id=NEW.project_id AND r.series_id=source_series
      AND r.revision=source_revision
  ) THEN
    RAISE EXCEPTION 'PIE review cannot target revoked evidence';
  END IF;

  SELECT v.verdict INTO review_verdict FROM pie_candidate_insight_verification_events v
    WHERE v.project_id=NEW.project_id
      AND v.verification_sha256=NEW.verification_sha256
      AND v.generation_sha256=NEW.generation_sha256;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'PIE verification not bound to candidate generation';
  END IF;

  SELECT event_seq,event_sha256,verification_sha256 INTO latest
    FROM pie_candidate_insight_review_events
    WHERE project_id=NEW.project_id AND generation_sha256=NEW.generation_sha256
    ORDER BY event_seq DESC LIMIT 1;
  IF FOUND THEN
    IF NEW.event_seq<>latest.event_seq+1
       OR NEW.parent_event_sha256<>latest.event_sha256
       OR NEW.verification_sha256<>latest.verification_sha256 THEN
      RAISE EXCEPTION 'PIE review sequence/parent mismatch';
    END IF;
  ELSIF NEW.event_seq<>1 OR NEW.parent_event_sha256 IS NOT NULL THEN
    RAISE EXCEPTION 'PIE review genesis requires sequence one';
  END IF;

  IF NEW.event_kind='change_suggestion' THEN
    SELECT count(*) INTO edit_count FROM pie_candidate_insight_review_events
      WHERE project_id=NEW.project_id AND generation_sha256=NEW.generation_sha256
        AND event_kind='change_suggestion';
    IF NEW.review_edit_version<>edit_count+1 THEN
      RAISE EXCEPTION 'PIE suggested edit version must be consecutive';
    END IF;
  END IF;

  IF NEW.actor_role IN ('reviewer','adjudicator','curator') AND EXISTS (
    SELECT 1 FROM pie_pattern_registry_series s
     WHERE s.project_id=NEW.project_id AND s.series_id=source_series
       AND s.owner_id=NEW.actor_id
  ) THEN
    RAISE EXCEPTION 'PIE author cannot self-review';
  END IF;
  IF NEW.event_kind='review_decision'
      AND NEW.decision_code='review_candidate_internal'
      AND review_verdict<>'candidate_checked_for_review' THEN
    RAISE EXCEPTION 'PIE quarantined candidate cannot be approved';
  END IF;
  IF NEW.event_kind='appeal' THEN
    SELECT actor_id,event_kind,decision_code INTO target
      FROM pie_candidate_insight_review_events
      WHERE project_id=NEW.project_id AND generation_sha256=NEW.generation_sha256
        AND event_sha256=NEW.target_event_sha256;
    IF NOT FOUND OR target.event_kind<>'review_decision'
       OR target.decision_code NOT IN ('reject_candidate','request_changes')
       OR NEW.actor_id=target.actor_id
       OR NOT EXISTS (
         SELECT 1 FROM pie_pattern_registry_series s
         WHERE s.project_id=NEW.project_id AND s.series_id=source_series
           AND s.owner_id=NEW.actor_id
       ) THEN
      RAISE EXCEPTION 'PIE appeal must be lodged by original author against denial';
    END IF;
  END IF;
  IF NEW.event_kind='adjudication' THEN
    SELECT actor_id,event_kind,target_event_sha256 INTO target
      FROM pie_candidate_insight_review_events
      WHERE project_id=NEW.project_id AND generation_sha256=NEW.generation_sha256
        AND event_sha256=NEW.target_event_sha256;
    IF NOT FOUND OR target.event_kind<>'appeal' OR NEW.actor_id=target.actor_id THEN
      RAISE EXCEPTION 'PIE adjudication requires separate appeal actor';
    END IF;
    SELECT actor_id INTO prior
      FROM pie_candidate_insight_review_events
      WHERE project_id=NEW.project_id AND generation_sha256=NEW.generation_sha256
        AND event_sha256=target.target_event_sha256;
    IF NOT FOUND OR NEW.actor_id=prior.actor_id THEN
      RAISE EXCEPTION 'PIE adjudicator cannot be original decision maker';
    END IF;
  END IF;
  IF NEW.event_kind='calibration_gold' THEN
    SELECT count(DISTINCT actor_id) INTO voters_count
      FROM pie_candidate_insight_review_events
      WHERE project_id=NEW.project_id AND generation_sha256=NEW.generation_sha256
        AND event_kind='calibration_vote'
        AND actor_id<>NEW.actor_id;
    SELECT count(DISTINCT actor_id) INTO agreeing_count
      FROM pie_candidate_insight_review_events
      WHERE project_id=NEW.project_id AND generation_sha256=NEW.generation_sha256
        AND event_kind='calibration_vote'
        AND calibration_label=NEW.calibration_label
        AND actor_id<>NEW.actor_id;
    IF voters_count<2 OR agreeing_count<>voters_count THEN
      RAISE EXCEPTION 'PIE calibration gold requires unanimous independent votes';
    END IF;
    IF EXISTS (
      SELECT 1 FROM pie_candidate_insight_review_events
      WHERE project_id=NEW.project_id AND generation_sha256=NEW.generation_sha256
        AND event_kind='calibration_gold'
    ) THEN
      RAISE EXCEPTION 'PIE calibration gold already finalized';
    END IF;
  END IF;
  RETURN NEW;
END $pie32$;
CREATE TRIGGER pie_candidate_review_chain_guard_insert
 BEFORE INSERT ON pie_candidate_insight_review_events FOR EACH ROW
 EXECUTE FUNCTION pie_candidate_review_chain_guard();

-- No direct public release view, model proposal mutation, or publication outbox.
