-- C schema integrity hardening: source date lineage and immutable versioned inputs.
-- Separate additive migration because 003 may already have been deployed.
-- Does NOT introduce a calculator or guess alpha, k or prior rates.

ALTER TABLE source_video_metadata
    ADD CONSTRAINT uq_c_metadata_id_video UNIQUE(id, video_id);

ALTER TABLE calculated_metrics
    ADD COLUMN published_metadata_id BIGINT;
ALTER TABLE calculated_metrics
    ADD CONSTRAINT fk_c_calculated_publication_metadata
      FOREIGN KEY(published_metadata_id, post_id)
      REFERENCES source_video_metadata(id, video_id) ON DELETE CASCADE;

-- Versioned formulas cannot silently change after results reference them.
-- is_active may change, but all formula inputs and its version identity cannot.
CREATE FUNCTION cm_c_config_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.calculation_version, OLD.alpha, OLD.bayesian_k,
        OLD.prior_source_policy, OLD.cohort_selection_rules,
        OLD.growth_policy, OLD.freshness_half_life_h,
        OLD.weights_json, OLD.normalization_enabled)
       IS DISTINCT FROM
       (NEW.calculation_version, NEW.alpha, NEW.bayesian_k,
        NEW.prior_source_policy, NEW.cohort_selection_rules,
        NEW.growth_policy, NEW.freshness_half_life_h,
        NEW.weights_json, NEW.normalization_enabled)
    THEN
        RAISE EXCEPTION 'Create a NEW calculation config version; parameters are immutable';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_cm_c_config_guard
    BEFORE UPDATE ON metric_calculation_configs
    FOR EACH ROW EXECUTE FUNCTION cm_c_config_guard();

-- Freeze a published cohort so that membership and medians stay reproducible.
ALTER TABLE comparison_cohorts
    ADD COLUMN is_frozen BOOLEAN NOT NULL DEFAULT FALSE;

CREATE FUNCTION cm_c_cohort_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF OLD.is_frozen AND OLD IS DISTINCT FROM NEW THEN
        RAISE EXCEPTION 'Frozen cohorts cannot be rewritten or unfrozen';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_cm_c_cohort_guard
    BEFORE UPDATE ON comparison_cohorts
    FOR EACH ROW EXECUTE FUNCTION cm_c_cohort_guard();

CREATE FUNCTION cm_c_cohort_member_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE cohort BIGINT;
BEGIN
    cohort := CASE WHEN TG_OP='DELETE' THEN OLD.cohort_id ELSE NEW.cohort_id END;
    IF EXISTS (SELECT 1 FROM comparison_cohorts WHERE id=cohort AND is_frozen) THEN
        RAISE EXCEPTION 'Members of a frozen cohort cannot change';
    END IF;
    RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END;
END $$;
CREATE TRIGGER trg_cm_c_cohort_member_guard
    BEFORE INSERT OR UPDATE OR DELETE ON comparison_cohort_members
    FOR EACH ROW EXECUTE FUNCTION cm_c_cohort_member_guard();

-- A COMPLETE result must have evidence for age, interval, creator and priors.
-- Niche Outlier is intentionally NOT a mandatory Breakout Score component.
ALTER TABLE calculated_metrics
    ADD CONSTRAINT chk_c_complete_has_inputs CHECK (
        score_status <> 'complete' OR (
            previous_snapshot_id IS NOT NULL
            AND published_metadata_id IS NOT NULL
            AND creator_cohort_id IS NOT NULL
            AND creator_median_views IS NOT NULL
            AND alpha_used IS NOT NULL
            AND k_used IS NOT NULL
            AND p_l_used IS NOT NULL
            AND p_c_used IS NOT NULL
            AND p_s_used IS NOT NULL
            AND score_creator_component = ROUND(0.45 * creator_ratio, 12)
            AND score_growth_component = ROUND(0.30 * views_growth_per_hour, 12)
            AND score_engagement_component =
                ROUND(0.15 * weighted_engagement_smoothed, 12)
            AND score_freshness_component = ROUND(0.10 * freshness_score, 12)
        )
    );

CREATE FUNCTION cm_c_result_integrity() RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    v_now RECORD;
    v_before RECORD;
    v_published TIMESTAMPTZ;
    v_alpha NUMERIC;
    v_k NUMERIC;
BEGIN
    IF TG_OP='UPDATE' AND OLD.score_status='complete'
       AND OLD IS DISTINCT FROM NEW THEN
        RAISE EXCEPTION 'Completed results are immutable; create a new calculation version';
    END IF;
    IF NEW.score_status <> 'complete' THEN
        RETURN NEW;
    END IF;
    SELECT observed_at, views, source_metric_definition, video_id, source_id
      INTO v_now FROM metric_snapshots WHERE id=NEW.snapshot_id;
    SELECT observed_at, views, source_metric_definition, video_id, source_id
      INTO v_before FROM metric_snapshots WHERE id=NEW.previous_snapshot_id;
    IF v_now.views IS NULL OR v_before.views IS NULL
       OR v_now.observed_at <= v_before.observed_at
       OR v_now.views < v_before.views
       OR v_now.source_metric_definition IS NULL
       OR v_now.source_metric_definition IS DISTINCT FROM v_before.source_metric_definition
       OR v_now.source_id IS DISTINCT FROM v_before.source_id THEN
        RAISE EXCEPTION 'Invalid growth interval, source or view definition';
    END IF;
    SELECT published_at INTO v_published
      FROM source_video_metadata
      WHERE id=NEW.published_metadata_id AND deleted_at IS NULL
        AND (expires_at IS NULL OR expires_at>now());
    IF v_published IS NULL OR v_published > v_now.observed_at THEN
        RAISE EXCEPTION 'Missing or invalid publication evidence';
    END IF;
    IF NOT EXISTS (
      SELECT 1 FROM comparison_cohorts
      WHERE id=NEW.creator_cohort_id AND purpose='creator' AND is_frozen
        AND median_views IS NOT NULL
    ) THEN
        RAISE EXCEPTION 'Creator cohort must be frozen with a valid median';
    END IF;
    IF NEW.niche_cohort_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM comparison_cohorts
      WHERE id=NEW.niche_cohort_id AND purpose='niche' AND is_frozen
    ) THEN
        RAISE EXCEPTION 'Niche cohort must be frozen';
    END IF;
    IF NEW.prior_cohort_id IS NOT NULL AND NOT EXISTS (
      SELECT 1 FROM comparison_cohorts
      WHERE id=NEW.prior_cohort_id AND purpose='engagement_prior' AND is_frozen
    ) THEN
        RAISE EXCEPTION 'Prior cohort must be frozen';
    END IF;
    SELECT alpha,bayesian_k INTO v_alpha,v_k
      FROM metric_calculation_configs WHERE id=NEW.calculation_config_id;
    IF v_alpha IS NULL OR v_k IS NULL
       OR v_alpha IS DISTINCT FROM NEW.alpha_used
       OR v_k IS DISTINCT FROM NEW.k_used THEN
        RAISE EXCEPTION 'Config alpha and k must be set and identical to used parameters';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER trg_cm_c_result_integrity
    BEFORE INSERT OR UPDATE ON calculated_metrics
    FOR EACH ROW EXECUTE FUNCTION cm_c_result_integrity();

-- Expanded append-only fact guard: provenance must not change either.
CREATE OR REPLACE FUNCTION cm_c_snapshot_guard() RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF (OLD.video_id,OLD.source_id,OLD.observed_at,OLD.views,OLD.likes,
        OLD.comments,OLD.shares,OLD.saves,OLD.followers,OLD.precision,
        OLD.collected_at,OLD.source_metric_definition,OLD.source_metric_version,
        OLD.snapshot_key,OLD.source_policy_version_id,
        OLD.observation_time_assumed,OLD.ingest_job_id,OLD.raw_payload_ref)
       IS DISTINCT FROM
       (NEW.video_id,NEW.source_id,NEW.observed_at,NEW.views,NEW.likes,
        NEW.comments,NEW.shares,NEW.saves,NEW.followers,NEW.precision,
        NEW.collected_at,NEW.source_metric_definition,NEW.source_metric_version,
        NEW.snapshot_key,NEW.source_policy_version_id,
        NEW.observation_time_assumed,NEW.ingest_job_id,NEW.raw_payload_ref)
    THEN
        RAISE EXCEPTION 'Raw metric snapshots and provenance are immutable';
    END IF;
    RETURN NEW;
END $$;

-- Retention rights still require C service enforcement for reads/recalculation.
-- Deletion of expired source metadata cascades to derived results that used it.
