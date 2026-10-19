-- ContentMachine C Performance Metrics & Breakout Score, v1.0 (2026-10-19).
-- Additive migration: keep the existing videos, creators, topics, metric_snapshots.
-- NO calculator is enabled and NO parameters (alpha, k, priors) are invented.
-- Breakout formula intentionally uses RAW components; no normalization.

-- videos is the canonical post identity: videos.platform_video_id = platform_post_id.
-- content_kind is an older unvalidated downloader field; this new analytic value
-- is deliberately nullable so existing rows aren't silently classified.
ALTER TABLE videos
    ADD COLUMN c_content_type TEXT
        CHECK (c_content_type IS NULL OR c_content_type IN ('video', 'carousel'));

-- metric_snapshots is already the append-only metric fact table. Extend it,
-- rather than creating a duplicate metrics_snapshots relation.
ALTER TABLE metric_snapshots
    ADD COLUMN source_metric_definition TEXT,
    ADD COLUMN source_metric_version TEXT,
    ADD COLUMN ingest_job_id BIGINT REFERENCES jobs(id) ON DELETE SET NULL,
    ADD COLUMN data_quality_status TEXT NOT NULL DEFAULT 'ok'
        CHECK (data_quality_status IN
          ('ok','partial','unverified','incompatible_source','counter_correction',
           'invalid_timestamp','retracted')),
    ADD COLUMN raw_payload_ref TEXT,
    ADD COLUMN snapshot_key TEXT;
CREATE UNIQUE INDEX uq_c_metric_snapshot_key
    ON metric_snapshots(snapshot_key) WHERE snapshot_key IS NOT NULL;

-- A composite key allows DB-enforced proof that cohort members and a
-- previous snapshot refer to the SAME video as the current snapshot.
ALTER TABLE metric_snapshots
    ADD CONSTRAINT uq_c_snapshot_id_video UNIQUE (id, video_id);
CREATE INDEX idx_c_snapshot_video_time_source
    ON metric_snapshots(video_id, observed_at DESC, source_id);

-- Retention changes (expires_at, data_quality_status) and compliant DELETE are
-- allowed, but the observed facts, source identity and timestamps cannot mutate.
CREATE FUNCTION cm_c_snapshot_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.video_id, OLD.source_id, OLD.observed_at, OLD.views,
        OLD.likes, OLD.comments, OLD.shares, OLD.saves, OLD.followers,
        OLD.precision, OLD.collected_at, OLD.source_metric_definition,
        OLD.source_metric_version, OLD.snapshot_key)
       IS DISTINCT FROM
       (NEW.video_id, NEW.source_id, NEW.observed_at, NEW.views,
        NEW.likes, NEW.comments, NEW.shares, NEW.saves, NEW.followers,
        NEW.precision, NEW.collected_at, NEW.source_metric_definition,
        NEW.source_metric_version, NEW.snapshot_key)
    THEN
        RAISE EXCEPTION 'Raw metric snapshot facts are immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_cm_c_snapshot_guard
    BEFORE UPDATE ON metric_snapshots
    FOR EACH ROW EXECUTE FUNCTION cm_c_snapshot_guard();

CREATE TABLE comparison_cohorts (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    purpose TEXT NOT NULL CHECK (purpose IN ('creator','niche','engagement_prior')),
    definition_version TEXT NOT NULL CHECK (length(definition_version) BETWEEN 1 AND 120),
    platform TEXT NOT NULL CHECK (platform IN ('youtube','tiktok','instagram')),
    creator_id BIGINT REFERENCES creators(id) ON DELETE CASCADE,
    topic_id BIGINT REFERENCES topics(id) ON DELETE CASCADE,
    content_type TEXT CHECK (content_type IS NULL OR content_type IN ('video','carousel')),
    source_id TEXT REFERENCES sources(id) ON DELETE SET NULL,
    source_metric_definition TEXT,
    evaluated_as_of TIMESTAMPTZ NOT NULL,
    age_window JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(age_window) = 'object'),
    time_window JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(time_window) = 'object'),
    post_count INTEGER NOT NULL CHECK (post_count >= 0),
    median_views NUMERIC(30,12) CHECK (median_views IS NULL OR median_views >= 0),
    prior_like_rate NUMERIC(30,12)
        CHECK (prior_like_rate IS NULL OR prior_like_rate >= 0),
    prior_comment_rate NUMERIC(30,12)
        CHECK (prior_comment_rate IS NULL OR prior_comment_rate >= 0),
    prior_share_rate NUMERIC(30,12)
        CHECK (prior_share_rate IS NULL OR prior_share_rate >= 0),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK (purpose <> 'creator' OR creator_id IS NOT NULL),
    CHECK (purpose <> 'niche' OR topic_id IS NOT NULL),
    UNIQUE(id, purpose)
);
CREATE INDEX idx_c_cohorts_selection
    ON comparison_cohorts(purpose, platform, content_type, evaluated_as_of DESC);
CREATE INDEX idx_c_cohorts_creator
    ON comparison_cohorts(creator_id, evaluated_as_of DESC) WHERE creator_id IS NOT NULL;
CREATE INDEX idx_c_cohorts_topic
    ON comparison_cohorts(topic_id, evaluated_as_of DESC) WHERE topic_id IS NOT NULL;

CREATE TABLE comparison_cohort_members (
    cohort_id BIGINT NOT NULL REFERENCES comparison_cohorts(id) ON DELETE CASCADE,
    comparison_post_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    comparison_snapshot_id BIGINT NOT NULL,
    added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (cohort_id, comparison_post_id, comparison_snapshot_id),
    FOREIGN KEY (comparison_snapshot_id, comparison_post_id)
        REFERENCES metric_snapshots(id, video_id) ON DELETE CASCADE
);
CREATE INDEX idx_c_cohort_members_post
    ON comparison_cohort_members(comparison_post_id, cohort_id);

-- An absent alpha or Bayesian k is a real PENDING configuration, not a zero.
-- v1 locks the raw Breakout weights and 48-hour Freshness half-life.
CREATE TABLE metric_calculation_configs (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    calculation_version TEXT NOT NULL UNIQUE
        CHECK (length(calculation_version) BETWEEN 1 AND 120),
    alpha NUMERIC(30,12) CHECK (alpha IS NULL OR alpha > 0),
    bayesian_k NUMERIC(30,12) CHECK (bayesian_k IS NULL OR bayesian_k > 0),
    prior_source_policy JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(prior_source_policy) = 'object'),
    cohort_selection_rules JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(cohort_selection_rules) = 'object'),
    growth_policy JSONB NOT NULL DEFAULT '{}'::jsonb
        CHECK (jsonb_typeof(growth_policy) = 'object'),
    freshness_half_life_h NUMERIC(14,6) NOT NULL DEFAULT 48
        CHECK (freshness_half_life_h = 48),
    weights_json JSONB NOT NULL DEFAULT
      '{"creator_ratio":0.45,"views_growth_per_hour":0.30,
        "weighted_engagement_smoothed":0.15,"freshness_score":0.10}'::jsonb
        CHECK (weights_json =
          '{"creator_ratio":0.45,"views_growth_per_hour":0.30,
            "weighted_engagement_smoothed":0.15,"freshness_score":0.10}'::jsonb),
    normalization_enabled BOOLEAN NOT NULL DEFAULT FALSE
        CHECK (normalization_enabled = FALSE),
    is_active BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_c_one_active_calculation_config
    ON metric_calculation_configs(is_active) WHERE is_active = TRUE;

-- Derived results are independent of raw facts and versioned by ALL inputs.
-- NULLS NOT DISTINCT (PostgreSQL 15+) ensures idempotency even when
-- optional cohort/previous-snapshot references are NULL.
CREATE TABLE calculated_metrics (
    id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    post_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
    snapshot_id BIGINT NOT NULL,
    calculation_config_id BIGINT NOT NULL
        REFERENCES metric_calculation_configs(id) ON DELETE RESTRICT,
    previous_snapshot_id BIGINT,
    creator_cohort_id BIGINT REFERENCES comparison_cohorts(id) ON DELETE RESTRICT,
    niche_cohort_id BIGINT REFERENCES comparison_cohorts(id) ON DELETE RESTRICT,
    prior_cohort_id BIGINT REFERENCES comparison_cohorts(id) ON DELETE RESTRICT,
    creator_median_views NUMERIC(30,12),
    niche_median_views NUMERIC(30,12),
    alpha_used NUMERIC(30,12),
    k_used NUMERIC(30,12),
    p_l_used NUMERIC(30,12),
    p_c_used NUMERIC(30,12),
    p_s_used NUMERIC(30,12),
    like_rate_pct NUMERIC(30,12),
    comment_rate_pct NUMERIC(30,12),
    share_rate_pct NUMERIC(30,12),
    engagement_rate_pct NUMERIC(30,12),
    weighted_engagement_raw NUMERIC(30,12),
    like_rate_smoothed NUMERIC(30,12),
    comment_rate_smoothed NUMERIC(30,12),
    share_rate_smoothed NUMERIC(30,12),
    weighted_engagement_smoothed NUMERIC(30,12),
    creator_ratio NUMERIC(30,12),
    niche_outlier NUMERIC(30,12),
    views_growth_per_hour NUMERIC(30,12),
    freshness_score NUMERIC(30,12),
    relative_views_index NUMERIC(30,12),
    score_creator_component NUMERIC(30,12),
    score_growth_component NUMERIC(30,12),
    score_engagement_component NUMERIC(30,12),
    score_freshness_component NUMERIC(30,12),
    breakout_score NUMERIC(30,12),
    calculation_status TEXT NOT NULL DEFAULT 'pending_configuration'
       CHECK (calculation_status IN ('complete','insufficient_data',
             'pending_configuration','invalid_timestamp','incompatible_source',
             'counter_correction','calculation_error')),
    score_status TEXT NOT NULL DEFAULT 'pending_configuration'
       CHECK (score_status IN ('complete','insufficient_data',
             'pending_configuration','invalid_timestamp','incompatible_source',
             'counter_correction','calculation_error')),
    calculated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (snapshot_id, post_id)
        REFERENCES metric_snapshots(id, video_id) ON DELETE CASCADE,
    FOREIGN KEY (previous_snapshot_id, post_id)
        REFERENCES metric_snapshots(id, video_id) ON DELETE RESTRICT,
    CHECK (previous_snapshot_id IS DISTINCT FROM snapshot_id),
    CHECK (creator_median_views IS NULL OR creator_median_views >= 0),
    CHECK (niche_median_views IS NULL OR niche_median_views >= 0),
    CHECK (alpha_used IS NULL OR alpha_used > 0),
    CHECK (k_used IS NULL OR k_used > 0),
    CHECK (p_l_used IS NULL OR p_l_used >= 0),
    CHECK (p_c_used IS NULL OR p_c_used >= 0),
    CHECK (p_s_used IS NULL OR p_s_used >= 0),
    CHECK (freshness_score IS NULL OR
           (freshness_score >= 0 AND freshness_score <= 100)),
    CHECK (
       (score_status = 'complete'
        AND breakout_score IS NOT NULL
        AND creator_ratio IS NOT NULL
        AND views_growth_per_hour IS NOT NULL
        AND weighted_engagement_smoothed IS NOT NULL
        AND freshness_score IS NOT NULL
        AND score_creator_component IS NOT NULL
        AND score_growth_component IS NOT NULL
        AND score_engagement_component IS NOT NULL
        AND score_freshness_component IS NOT NULL
        AND breakout_score = score_creator_component + score_growth_component
                             + score_engagement_component + score_freshness_component)
       OR (score_status <> 'complete' AND breakout_score IS NULL)
    ),
    CONSTRAINT uq_c_calculation_lineage UNIQUE NULLS NOT DISTINCT
      (snapshot_id, calculation_config_id, previous_snapshot_id,
       creator_cohort_id, niche_cohort_id, prior_cohort_id)
);
CREATE INDEX idx_c_calculated_snapshot
    ON calculated_metrics(snapshot_id, calculation_config_id);
CREATE INDEX idx_c_calculated_complete_score
    ON calculated_metrics(breakout_score DESC) WHERE score_status='complete';
CREATE INDEX idx_c_calculated_post
    ON calculated_metrics(post_id, calculated_at DESC);

-- Each cohort-reference must be of the declared purpose; otherwise a creator
-- median could be silently substituted for a niche/prior cohort.
CREATE FUNCTION cm_c_validate_result_cohorts() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.creator_cohort_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM comparison_cohorts
                       WHERE id=NEW.creator_cohort_id AND purpose='creator') THEN
        RAISE EXCEPTION 'creator_cohort_id must refer to creator purpose';
    END IF;
    IF NEW.niche_cohort_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM comparison_cohorts
                       WHERE id=NEW.niche_cohort_id AND purpose='niche') THEN
        RAISE EXCEPTION 'niche_cohort_id must refer to niche purpose';
    END IF;
    IF NEW.prior_cohort_id IS NOT NULL
       AND NOT EXISTS (SELECT 1 FROM comparison_cohorts
                       WHERE id=NEW.prior_cohort_id AND purpose='engagement_prior') THEN
        RAISE EXCEPTION 'prior_cohort_id must refer to engagement_prior purpose';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_cm_c_validate_result_cohorts
    BEFORE INSERT OR UPDATE ON calculated_metrics
    FOR EACH ROW EXECUTE FUNCTION cm_c_validate_result_cohorts();

-- A C calculation is only meaningful if a policy gate in the future service
-- explicitly permits deriving scores from EVERY participating source.
-- This migration does not grant any rights, run collectors, or calculate scores.
