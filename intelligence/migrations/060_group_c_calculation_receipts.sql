-- GC-MOD-20: additive immutable exact C7 authorization + input fingerprint.
-- 003/004/056-059 are unchanged. A result and receipt MUST be inserted
-- atomically by the GC20 writer after live source/cohort/IAM revalidation.
CREATE TABLE group_c_calculation_receipts (
  calculated_metric_id BIGINT PRIMARY KEY
    REFERENCES calculated_metrics(id) ON DELETE RESTRICT,
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE RESTRICT,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE RESTRICT,
  snapshot_id BIGINT NOT NULL,
  previous_snapshot_id BIGINT NOT NULL,
  published_metadata_id BIGINT NOT NULL,
  config_id BIGINT NOT NULL REFERENCES metric_calculation_configs(id) ON DELETE RESTRICT,
  creator_freeze_id BIGINT NOT NULL
    REFERENCES group_c_cohort_freezes(id) ON DELETE RESTRICT,
  niche_freeze_id BIGINT
    REFERENCES group_c_cohort_freezes(id) ON DELETE RESTRICT,
  prior_freeze_id BIGINT NOT NULL
    REFERENCES group_c_cohort_freezes(id) ON DELETE RESTRICT,
  review_request_id UUID NOT NULL
    REFERENCES group_c_review_requests(id) ON DELETE RESTRICT,
  review_decision_id UUID NOT NULL
    REFERENCES group_c_review_decisions(id) ON DELETE RESTRICT,
  activation_event_id UUID NOT NULL
    REFERENCES group_c_config_activation_events(id) ON DELETE RESTRICT,
  review_subject_sha256 TEXT NOT NULL CHECK (review_subject_sha256 ~ '^[0-9a-f]{64}$'),
  evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  calculation_version TEXT NOT NULL CHECK(length(calculation_version) BETWEEN 1 AND 120),
  receipt_version TEXT NOT NULL DEFAULT 'group-c-persisted-c7/v1'
    CHECK(receipt_version='group-c-persisted-c7/v1'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id,evidence_sha256),
  FOREIGN KEY(snapshot_id,video_id) REFERENCES metric_snapshots(id,video_id)
    ON DELETE RESTRICT,
  FOREIGN KEY(previous_snapshot_id,video_id) REFERENCES metric_snapshots(id,video_id)
    ON DELETE RESTRICT,
  FOREIGN KEY(published_metadata_id,video_id)
    REFERENCES source_video_metadata(id,video_id) ON DELETE RESTRICT,
  CHECK(snapshot_id <> previous_snapshot_id),
  CHECK(niche_freeze_id IS NULL OR niche_freeze_id NOT IN
        (creator_freeze_id,prior_freeze_id)),
  CHECK(creator_freeze_id <> prior_freeze_id)
);
CREATE INDEX idx_gc_c7_receipts_review
  ON group_c_calculation_receipts(project_id,review_request_id,config_id);

-- DB defense in depth: only *complete* and exact matched Group C scores
-- can acquire this immutable authorization certificate.
CREATE FUNCTION group_c_c7_receipt_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  score RECORD;
  fr RECORD;
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'Group C C7 certification is append-only'
      USING ERRCODE='23514';
  END IF;
  SELECT post_id,snapshot_id,previous_snapshot_id,published_metadata_id,
         calculation_config_id,creator_cohort_id,niche_cohort_id,
         prior_cohort_id,score_status,calculation_status
  INTO score FROM calculated_metrics WHERE id=NEW.calculated_metric_id;
  IF NOT FOUND OR score.score_status IS DISTINCT FROM 'complete'
     OR score.calculation_status IS DISTINCT FROM 'complete'
     OR score.post_id IS DISTINCT FROM NEW.video_id
     OR score.snapshot_id IS DISTINCT FROM NEW.snapshot_id
     OR score.previous_snapshot_id IS DISTINCT FROM NEW.previous_snapshot_id
     OR score.published_metadata_id IS DISTINCT FROM NEW.published_metadata_id
     OR score.calculation_config_id IS DISTINCT FROM NEW.config_id
     OR score.creator_cohort_id IS DISTINCT FROM
        (SELECT legacy_cohort_id FROM group_c_cohort_freezes WHERE id=NEW.creator_freeze_id)
     OR score.niche_cohort_id IS DISTINCT FROM
        (SELECT legacy_cohort_id FROM group_c_cohort_freezes WHERE id=NEW.niche_freeze_id)
     OR score.prior_cohort_id IS DISTINCT FROM
        (SELECT legacy_cohort_id FROM group_c_cohort_freezes WHERE id=NEW.prior_freeze_id)
  THEN
    RAISE EXCEPTION 'C7 receipt does not match immutable complete calculation'
      USING ERRCODE='23514';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM group_c_review_requests r
    JOIN group_c_review_decisions d ON d.request_id=r.id
    JOIN group_c_config_activation_events a ON a.review_request_id=r.id
    JOIN metric_calculation_configs cfg ON cfg.id=r.calculation_config_id
    WHERE r.id=NEW.review_request_id
      AND r.project_id=NEW.project_id
      AND r.target_video_id=NEW.video_id
      AND r.source_policy_version_id=
          (SELECT source_policy_version_id FROM metric_snapshots WHERE id=NEW.snapshot_id)
      AND r.calculation_config_id=NEW.config_id
      AND r.calculation_version=NEW.calculation_version
      AND r.creator_freeze_id=NEW.creator_freeze_id
      AND r.niche_freeze_id IS NOT DISTINCT FROM NEW.niche_freeze_id
      AND r.prior_freeze_id=NEW.prior_freeze_id
      AND r.subject_sha256=NEW.review_subject_sha256
      AND d.id=NEW.review_decision_id AND d.decision='approved'
      AND a.id=NEW.activation_event_id AND a.kind='activated'
      AND a.review_decision_id=d.id AND a.config_id=cfg.id
      AND a.review_subject_sha256=r.subject_sha256 AND cfg.is_active=TRUE
  ) THEN
    RAISE EXCEPTION 'C7 receipt requires matching active signed human review'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_gc_c7_receipt_guard
  BEFORE INSERT OR UPDATE OR DELETE ON group_c_calculation_receipts
  FOR EACH ROW EXECUTE FUNCTION group_c_c7_receipt_guard();
