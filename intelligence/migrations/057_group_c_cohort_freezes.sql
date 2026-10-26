-- GC-MOD-10: additive immutable provenance for frozen Group C cohorts.
-- Existing 003/004 comparisons and their checksum MUST NOT change.
-- 057 is stage-owned, unapproved (human review is GC-MOD-11/12).
-- PostgreSQL 17, transactionally installed via intelligence.migrate.

CREATE TABLE group_c_cohort_freezes (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE RESTRICT,
  legacy_cohort_id BIGINT NOT NULL UNIQUE
    REFERENCES comparison_cohorts(id) ON DELETE RESTRICT,
  target_video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE RESTRICT,
  target_snapshot_id BIGINT NOT NULL,
  target_metadata_id BIGINT NOT NULL,
  purpose TEXT NOT NULL CHECK (purpose IN ('creator','niche','engagement_prior')),
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
  source_policy_version_id BIGINT NOT NULL REFERENCES source_policy_versions(id),
  source_metric_definition TEXT NOT NULL CHECK (source_metric_definition='platform_post_views'),
  source_metric_version TEXT NOT NULL CHECK (length(btrim(source_metric_version)) > 0),
  rules_version TEXT NOT NULL CHECK (rules_version='group-c-cohort-rules/v1'),
  rules_sha256 TEXT NOT NULL CHECK (rules_sha256 ~ '^[0-9a-f]{64}$'),
  selection_sha256 TEXT NOT NULL CHECK (selection_sha256 ~ '^[0-9a-f]{64}$'),
  evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  rules_payload JSONB NOT NULL CHECK (jsonb_typeof(rules_payload)='object'),
  estimator_version TEXT NOT NULL
    CHECK (estimator_version='group-c-cohort-estimator/v1'),
  member_count INTEGER NOT NULL CHECK (member_count > 0),
  median_views NUMERIC(30,12),
  prior_like_rate NUMERIC(30,12),
  prior_comment_rate NUMERIC(30,12),
  prior_share_rate NUMERIC(30,12),
  state TEXT NOT NULL DEFAULT 'frozen_unreviewed'
    CHECK (state='frozen_unreviewed'),
  sealed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id,purpose,rules_sha256),
  UNIQUE(project_id,evidence_sha256),
  FOREIGN KEY(target_snapshot_id,target_video_id)
    REFERENCES metric_snapshots(id,video_id) ON DELETE RESTRICT,
  FOREIGN KEY(target_metadata_id,target_video_id)
    REFERENCES source_video_metadata(id,video_id) ON DELETE RESTRICT,
  CHECK(
    (purpose IN ('creator','niche') AND median_views IS NOT NULL
     AND prior_like_rate IS NULL AND prior_comment_rate IS NULL
     AND prior_share_rate IS NULL)
    OR
    (purpose='engagement_prior' AND median_views IS NULL
     AND prior_like_rate IS NOT NULL AND prior_comment_rate IS NOT NULL
     AND prior_share_rate IS NOT NULL)
  ),
  CHECK(median_views IS NULL OR median_views >= 0),
  CHECK(prior_like_rate IS NULL OR prior_like_rate BETWEEN 0 AND 1),
  CHECK(prior_comment_rate IS NULL OR prior_comment_rate BETWEEN 0 AND 1),
  CHECK(prior_share_rate IS NULL OR prior_share_rate BETWEEN 0 AND 1)
);
CREATE INDEX idx_gc_cohort_freezes_project
  ON group_c_cohort_freezes(project_id,purpose,sealed_at DESC);

CREATE TABLE group_c_cohort_freeze_members (
  freeze_id BIGINT NOT NULL REFERENCES group_c_cohort_freezes(id)
    ON DELETE RESTRICT,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE RESTRICT,
  snapshot_id BIGINT NOT NULL,
  metadata_id BIGINT NOT NULL,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
  source_policy_version_id BIGINT NOT NULL REFERENCES source_policy_versions(id),
  metric_definition TEXT NOT NULL CHECK(metric_definition='platform_post_views'),
  metric_version TEXT NOT NULL CHECK(length(btrim(metric_version)) > 0),
  published_at TIMESTAMPTZ NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL,
  views BIGINT NOT NULL CHECK(views>=0),
  likes BIGINT CHECK(likes IS NULL OR likes>=0),
  comments BIGINT CHECK(comments IS NULL OR comments>=0),
  shares BIGINT CHECK(shares IS NULL OR shares>=0),
  evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
  PRIMARY KEY(freeze_id,video_id),
  UNIQUE(freeze_id,snapshot_id),
  FOREIGN KEY(snapshot_id,video_id) REFERENCES metric_snapshots(id,video_id)
    ON DELETE RESTRICT,
  FOREIGN KEY(metadata_id,video_id) REFERENCES source_video_metadata(id,video_id)
    ON DELETE RESTRICT,
  CHECK(published_at <= observed_at)
);
CREATE INDEX idx_gc_cohort_freeze_members_snapshot
  ON group_c_cohort_freeze_members(snapshot_id,freeze_id);

-- A freeze row and each member become permanently immutable once inserted.
-- Retention erasure, if legally required, needs an explicitly reviewed
-- GC-MOD-24 mechanism; ordinary application writes may not erase evidence.
CREATE FUNCTION group_c_cohort_freeze_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Frozen Group C cohort evidence is immutable'
    USING ERRCODE='23514';
END $$;
CREATE TRIGGER trg_gc_cohort_freeze_immutable
  BEFORE UPDATE OR DELETE ON group_c_cohort_freezes
  FOR EACH ROW EXECUTE FUNCTION group_c_cohort_freeze_immutable();
CREATE TRIGGER trg_gc_cohort_member_immutable
  BEFORE UPDATE OR DELETE ON group_c_cohort_freeze_members
  FOR EACH ROW EXECUTE FUNCTION group_c_cohort_freeze_immutable();

-- Verify membership is complete when the write transaction commits.
-- Also binds this artifact to the existing frozen C schema parent and
-- prevents inserting a freeze containing an unfinished sample.
CREATE FUNCTION group_c_cohort_freeze_commit_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  old_cohort RECORD;
  peer_count INTEGER;
  legacy_count INTEGER;
BEGIN
  SELECT id,purpose,platform,source_id,source_metric_definition,
         post_count,is_frozen,median_views,prior_like_rate,
         prior_comment_rate,prior_share_rate
    INTO old_cohort FROM comparison_cohorts WHERE id=NEW.legacy_cohort_id;
  IF NOT FOUND OR old_cohort.is_frozen IS DISTINCT FROM TRUE
     OR old_cohort.purpose IS DISTINCT FROM NEW.purpose
     OR old_cohort.source_id IS DISTINCT FROM NEW.source_id
     OR old_cohort.source_metric_definition IS DISTINCT FROM NEW.source_metric_definition
     OR old_cohort.post_count IS DISTINCT FROM NEW.member_count
     OR old_cohort.median_views IS DISTINCT FROM NEW.median_views
     OR old_cohort.prior_like_rate IS DISTINCT FROM NEW.prior_like_rate
     OR old_cohort.prior_comment_rate IS DISTINCT FROM NEW.prior_comment_rate
     OR old_cohort.prior_share_rate IS DISTINCT FROM NEW.prior_share_rate
  THEN
    RAISE EXCEPTION 'Group C cohort freeze parent mismatch/unfrozen'
      USING ERRCODE='23514';
  END IF;
  SELECT count(*) INTO peer_count FROM group_c_cohort_freeze_members
   WHERE freeze_id=NEW.id;
  SELECT count(*) INTO legacy_count FROM comparison_cohort_members
   WHERE cohort_id=NEW.legacy_cohort_id;
  IF peer_count IS DISTINCT FROM NEW.member_count
     OR legacy_count IS DISTINCT FROM NEW.member_count
     OR EXISTS(
       SELECT 1 FROM group_c_cohort_freeze_members f
        WHERE f.freeze_id=NEW.id
          AND NOT EXISTS (
            SELECT 1 FROM comparison_cohort_members m
             WHERE m.cohort_id=NEW.legacy_cohort_id
               AND m.comparison_post_id=f.video_id
               AND m.comparison_snapshot_id=f.snapshot_id
          )
     ) THEN
    RAISE EXCEPTION 'Group C cohort freeze contains incomplete/mismatched members'
      USING ERRCODE='23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER trg_gc_cohort_freeze_commit
  AFTER INSERT ON group_c_cohort_freezes
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION group_c_cohort_freeze_commit_guard();
