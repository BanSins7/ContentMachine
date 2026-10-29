-- PIE-MOD-30: append-only unverified Insight generation artifacts.
-- Generated research explanations are NEVER supported/published/approved.
CREATE TABLE pie_candidate_insight_generations (
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
  generation_sha256 TEXT NOT NULL CHECK(generation_sha256 ~ '^[0-9a-f]{64}$'),
  series_id TEXT NOT NULL CHECK(series_id ~ '^[0-9a-f]{64}$'),
  source_revision INTEGER NOT NULL CHECK(source_revision BETWEEN 1 AND 2147483647),
  hypothesis_sha256 TEXT NOT NULL CHECK(hypothesis_sha256 ~ '^[0-9a-f]{64}$'),
  source_prompt_sha256 TEXT NOT NULL CHECK(source_prompt_sha256 ~ '^[0-9a-f]{64}$'),
  source_facts_sha256 TEXT NOT NULL CHECK(source_facts_sha256 ~ '^[0-9a-f]{64}$'),
  candidate_draft_sha256 TEXT NOT NULL CHECK(candidate_draft_sha256 ~ '^[0-9a-f]{64}$'),
  evidence_ids JSONB NOT NULL CHECK(jsonb_typeof(evidence_ids)='array'),
  generation_mode TEXT NOT NULL CHECK(generation_mode IN ('local_deterministic','provider_reported')),
  model_attempt_id UUID,
  model_response_sha256 TEXT CHECK(model_response_sha256 IS NULL OR model_response_sha256 ~ '^[0-9a-f]{64}$'),
  artifact JSONB NOT NULL CHECK(jsonb_typeof(artifact)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,generation_sha256),
  FOREIGN KEY(project_id,series_id,source_revision)
    REFERENCES pie_pattern_registry_revisions(project_id,series_id,revision)
    ON DELETE CASCADE,
  FOREIGN KEY(project_id,model_attempt_id)
    REFERENCES pie_model_dispatch_outcomes(project_id,attempt_id)
    ON DELETE CASCADE,
  UNIQUE(project_id,model_attempt_id),
  CHECK ((generation_mode='local_deterministic' AND model_attempt_id IS NULL
          AND model_response_sha256 IS NULL)
      OR (generation_mode='provider_reported' AND model_attempt_id IS NOT NULL
          AND model_response_sha256 IS NOT NULL)),
  CHECK ((artifact->>'schema_version') IS NOT DISTINCT FROM 'pie-candidate-insight-generation/v1'),
  CHECK ((artifact->>'project_id') IS NOT DISTINCT FROM project_id),
  CHECK ((artifact->>'generation_mode') IS NOT DISTINCT FROM generation_mode),
  CHECK ((artifact->>'source_series_id') IS NOT DISTINCT FROM series_id),
  CHECK ((artifact->>'source_revision') IS NOT DISTINCT FROM source_revision::text),
  CHECK ((artifact->>'source_hypothesis_sha256') IS NOT DISTINCT FROM hypothesis_sha256),
  CHECK ((artifact->>'source_prompt_sha256') IS NOT DISTINCT FROM source_prompt_sha256),
  CHECK ((artifact->>'source_facts_sha256') IS NOT DISTINCT FROM source_facts_sha256),
  CHECK ((artifact->>'candidate_draft_sha256') IS NOT DISTINCT FROM candidate_draft_sha256),
  CHECK ((artifact->'evidence_ids') IS NOT DISTINCT FROM evidence_ids),
  CHECK ((artifact->'approved_live') IS NOT DISTINCT FROM 'false'::jsonb),
  CHECK ((artifact->'can_publish') IS NOT DISTINCT FROM 'false'::jsonb),
  CHECK ((artifact->'independent_holdout_confirmed') IS NOT DISTINCT FROM 'false'::jsonb),
  CHECK ((artifact->'causal_claim') IS NOT DISTINCT FROM 'false'::jsonb),
  CHECK ((artifact->>'review_state') IS NOT DISTINCT FROM 'unverified_candidate')
);
CREATE INDEX pie_candidate_insight_generation_source
  ON pie_candidate_insight_generations(project_id,series_id,source_revision,created_at DESC);
CREATE TRIGGER pie_candidate_insight_generation_immutable
  BEFORE UPDATE ON pie_candidate_insight_generations FOR EACH ROW
  EXECUTE FUNCTION pie_reject_evidence_update();

-- Direct INSERTs cannot attach a foreign pattern hypothesis or claim a model
-- outcome that was uncertain, refunded or linked to a different source prompt.
CREATE FUNCTION pie_candidate_insight_generation_guard() RETURNS trigger LANGUAGE plpgsql AS $pie30$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pie_pattern_registry_revisions r
     WHERE r.project_id=NEW.project_id AND r.series_id=NEW.series_id
       AND r.revision=NEW.source_revision AND r.hypothesis_sha256=NEW.hypothesis_sha256
  ) THEN
    RAISE EXCEPTION 'PIE insight source identity mismatch';
  END IF;
  IF EXISTS (
    SELECT 1 FROM pie_pattern_registry_revisions newer
     WHERE newer.project_id=NEW.project_id
       AND newer.series_id=NEW.series_id
       AND newer.revision>NEW.source_revision
  ) THEN
    RAISE EXCEPTION 'PIE candidate Insight requires latest pattern revision';
  END IF;
  IF NEW.generation_mode='provider_reported' AND NOT EXISTS (
    SELECT 1 FROM pie_model_dispatch_outcomes o
      JOIN pie_model_dispatch_claims c
        ON c.project_id=o.project_id AND c.attempt_id=o.attempt_id
     WHERE o.project_id=NEW.project_id AND o.attempt_id=NEW.model_attempt_id
       AND o.outcome='reported_charged' AND o.response_sha256=NEW.model_response_sha256
       AND c.prompt_sha256=NEW.source_prompt_sha256
  ) THEN
    RAISE EXCEPTION 'PIE insight requires exact reported model response provenance';
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
     WHERE r.project_id=NEW.project_id AND r.series_id=NEW.series_id
       AND r.revision=NEW.source_revision
  ) THEN
    RAISE EXCEPTION 'PIE revoked source cannot generate a new insight artifact';
  END IF;
  RETURN NEW;
END $pie30$;
CREATE TRIGGER pie_candidate_insight_generation_guard_insert
  BEFORE INSERT ON pie_candidate_insight_generations FOR EACH ROW
  EXECUTE FUNCTION pie_candidate_insight_generation_guard();

-- No public read API: even generation artifacts must be checked against
-- current source rights, invalidation and review in future stage31+.
