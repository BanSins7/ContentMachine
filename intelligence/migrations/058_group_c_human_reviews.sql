-- GC-MOD-11: authenticated human review requests + immutable decisions.
-- Appenditive to 057. DOES NOT approve cohorts automatically or activate C7.
-- The service must verify a signed, project-scoped HUMAN IAM attestation
-- via an externally injected reviewer verifier before INSERT of decisions.
-- Never replace immutable 003/004/056/057 migration checksums.

CREATE TABLE group_c_review_requests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE RESTRICT,
  target_video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE RESTRICT,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE RESTRICT,
  source_policy_version_id BIGINT NOT NULL REFERENCES source_policy_versions(id) ON DELETE RESTRICT,
  metric_definition TEXT NOT NULL CHECK(metric_definition='platform_post_views'),
  metric_version TEXT NOT NULL CHECK(length(btrim(metric_version)) BETWEEN 1 AND 120),
  calculation_config_id BIGINT NOT NULL REFERENCES metric_calculation_configs(id) ON DELETE RESTRICT,
  calculation_version TEXT NOT NULL,
  alpha NUMERIC(30,12) NOT NULL CHECK(alpha>0),
  bayesian_k NUMERIC(30,12) NOT NULL CHECK(bayesian_k>0),
  creator_freeze_id BIGINT NOT NULL REFERENCES group_c_cohort_freezes(id) ON DELETE RESTRICT,
  niche_freeze_id BIGINT REFERENCES group_c_cohort_freezes(id) ON DELETE RESTRICT,
  prior_freeze_id BIGINT NOT NULL REFERENCES group_c_cohort_freezes(id) ON DELETE RESTRICT,
  minimum_creator_posts INTEGER NOT NULL CHECK(minimum_creator_posts>=1),
  minimum_prior_posts INTEGER NOT NULL CHECK(minimum_prior_posts>=1),
  minimum_niche_posts INTEGER CHECK(minimum_niche_posts IS NULL OR minimum_niche_posts>=1),
  subject_sha256 TEXT NOT NULL CHECK(subject_sha256 ~ '^[0-9a-f]{64}$'),
  review_payload JSONB NOT NULL CHECK(jsonb_typeof(review_payload)='object'),
  schema_version TEXT NOT NULL DEFAULT 'group-c-human-review/v1'
    CHECK(schema_version='group-c-human-review/v1'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id,subject_sha256),
  CHECK(creator_freeze_id<>prior_freeze_id),
  CHECK(niche_freeze_id IS NULL OR
        (niche_freeze_id<>creator_freeze_id AND niche_freeze_id<>prior_freeze_id)),
  CHECK((niche_freeze_id IS NULL)=(minimum_niche_posts IS NULL))
);
CREATE INDEX idx_gc_review_requests_project
  ON group_c_review_requests(project_id,created_at DESC);

CREATE TABLE group_c_review_decisions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  request_id UUID NOT NULL UNIQUE REFERENCES group_c_review_requests(id) ON DELETE RESTRICT,
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE RESTRICT,
  subject_sha256 TEXT NOT NULL CHECK(subject_sha256 ~ '^[0-9a-f]{64}$'),
  decision TEXT NOT NULL CHECK(decision IN ('approved','rejected')),
  reviewer_id TEXT NOT NULL CHECK(length(btrim(reviewer_id)) BETWEEN 3 AND 200),
  identity_provider TEXT NOT NULL CHECK(length(btrim(identity_provider)) BETWEEN 3 AND 200),
  authentication_event_id TEXT NOT NULL
    CHECK(length(btrim(authentication_event_id)) BETWEEN 8 AND 256),
  attestation_sha256 TEXT NOT NULL CHECK(attestation_sha256 ~ '^[0-9a-f]{64}$'),
  reviewer_note TEXT NOT NULL CHECK(length(btrim(reviewer_note)) BETWEEN 12 AND 4000),
  reviewed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(identity_provider,authentication_event_id),
  CHECK(lower(reviewer_id) NOT IN ('system','automation','bot','anonymous')),
  CHECK(lower(identity_provider) NOT IN ('self-reported','unverified'))
);
CREATE INDEX idx_gc_review_decisions_project
  ON group_c_review_decisions(project_id,reviewed_at DESC);

CREATE FUNCTION group_c_review_immutable() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Group C human review evidence is append-only'
    USING ERRCODE='23514';
END $$;
CREATE TRIGGER trg_gc_review_request_immutable
  BEFORE UPDATE OR DELETE ON group_c_review_requests
  FOR EACH ROW EXECUTE FUNCTION group_c_review_immutable();
CREATE TRIGGER trg_gc_review_decision_immutable
  BEFORE UPDATE OR DELETE ON group_c_review_decisions
  FOR EACH ROW EXECUTE FUNCTION group_c_review_immutable();

CREATE FUNCTION group_c_review_request_validate() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  f RECORD;
  cfg RECORD;
  freeze_id BIGINT;
  expected_purpose TEXT;
  threshold INTEGER;
BEGIN
  SELECT calculation_version,alpha,bayesian_k,is_active,
         normalization_enabled,freshness_half_life_h,weights_json
    INTO cfg
    FROM metric_calculation_configs WHERE id=NEW.calculation_config_id;
  IF NOT FOUND OR cfg.calculation_version IS DISTINCT FROM NEW.calculation_version
       OR cfg.alpha IS DISTINCT FROM NEW.alpha
       OR cfg.bayesian_k IS DISTINCT FROM NEW.bayesian_k
       OR cfg.is_active IS DISTINCT FROM FALSE
       OR cfg.normalization_enabled IS DISTINCT FROM FALSE
       OR cfg.freshness_half_life_h IS DISTINCT FROM 48
       OR cfg.weights_json IS DISTINCT FROM
          '{"creator_ratio":0.45,"views_growth_per_hour":0.30,
            "weighted_engagement_smoothed":0.15,"freshness_score":0.10}'::jsonb
  THEN
    RAISE EXCEPTION 'Human review needs exact inactive C config and positive alpha/k'
      USING ERRCODE='23514';
  END IF;
  FOR freeze_id,expected_purpose,threshold IN
    SELECT NEW.creator_freeze_id,'creator',NEW.minimum_creator_posts
    UNION ALL
    SELECT NEW.prior_freeze_id,'engagement_prior',NEW.minimum_prior_posts
    UNION ALL
    SELECT NEW.niche_freeze_id,'niche',NEW.minimum_niche_posts
      WHERE NEW.niche_freeze_id IS NOT NULL
  LOOP
    SELECT project_id,target_video_id,source_id,source_policy_version_id,
           source_metric_definition,source_metric_version,purpose,
           member_count,state
      INTO f FROM group_c_cohort_freezes WHERE id=freeze_id;
    IF NOT FOUND OR f.state IS DISTINCT FROM 'frozen_unreviewed'
       OR f.project_id IS DISTINCT FROM NEW.project_id
       OR f.target_video_id IS DISTINCT FROM NEW.target_video_id
       OR f.source_id IS DISTINCT FROM NEW.source_id
       OR f.source_policy_version_id IS DISTINCT FROM NEW.source_policy_version_id
       OR f.source_metric_definition IS DISTINCT FROM NEW.metric_definition
       OR f.source_metric_version IS DISTINCT FROM NEW.metric_version
       OR f.purpose IS DISTINCT FROM expected_purpose
       OR f.member_count < threshold THEN
      RAISE EXCEPTION 'Human review cohort evidence mismatch or below reviewed minimum'
        USING ERRCODE='23514';
    END IF;
  END LOOP;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_gc_review_request_validate
  BEFORE INSERT ON group_c_review_requests
  FOR EACH ROW EXECUTE FUNCTION group_c_review_request_validate();

CREATE FUNCTION group_c_review_decision_validate() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  r RECORD;
BEGIN
  SELECT project_id,subject_sha256 INTO r
    FROM group_c_review_requests WHERE id=NEW.request_id;
  IF NOT FOUND OR r.project_id IS DISTINCT FROM NEW.project_id
     OR r.subject_sha256 IS DISTINCT FROM NEW.subject_sha256 THEN
    RAISE EXCEPTION 'Human decision requires exact pinned review subject'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER trg_gc_review_decision_validate
  BEFORE INSERT ON group_c_review_decisions
  FOR EACH ROW EXECUTE FUNCTION group_c_review_decision_validate();
