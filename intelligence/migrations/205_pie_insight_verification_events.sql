-- PIE-MOD-31: immutable, non-publishing candidate verification history.
-- No ALTER to migrations 200–204; validation never grants release.
CREATE TABLE pie_candidate_insight_verification_events (
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
  verification_sha256 TEXT NOT NULL CHECK (verification_sha256 ~ '^[0-9a-f]{64}$'),
  generation_sha256 TEXT NOT NULL CHECK (generation_sha256 ~ '^[0-9a-f]{64}$'),
  source_series_id TEXT NOT NULL CHECK (source_series_id ~ '^[0-9a-f]{64}$'),
  source_revision INTEGER NOT NULL CHECK (source_revision BETWEEN 1 AND 2147483647),
  source_hypothesis_sha256 TEXT NOT NULL CHECK (source_hypothesis_sha256 ~ '^[0-9a-f]{64}$'),
  source_registry_payload_sha256 TEXT NOT NULL CHECK (source_registry_payload_sha256 ~ '^[0-9a-f]{64}$'),
  source_prompt_sha256 TEXT NOT NULL CHECK (source_prompt_sha256 ~ '^[0-9a-f]{64}$'),
  verdict TEXT NOT NULL CHECK (verdict IN ('candidate_checked_for_review','quarantined')),
  reason_code TEXT NOT NULL CHECK (reason_code IN (
    'exact_source_consistent','numeric_or_scope_contradiction',
    'fabricated_or_modified_draft','missing_counterevidence_audit'
  )),
  counterevidence_status TEXT NOT NULL
    CHECK (counterevidence_status IN ('independent_not_measured','unknown_quarantined')),
  audit JSONB NOT NULL CHECK (jsonb_typeof(audit)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,verification_sha256),
  FOREIGN KEY (project_id,generation_sha256)
    REFERENCES pie_candidate_insight_generations(project_id,generation_sha256)
    ON DELETE CASCADE,
  FOREIGN KEY (project_id,source_series_id,source_revision)
    REFERENCES pie_pattern_registry_revisions(project_id,series_id,revision)
    ON DELETE CASCADE,
  CHECK ((verdict='candidate_checked_for_review' AND reason_code='exact_source_consistent'
          AND counterevidence_status='independent_not_measured')
      OR (verdict='quarantined' AND reason_code<>'exact_source_consistent'
          AND counterevidence_status='unknown_quarantined')),
  CHECK ((audit->>'schema_version') IS NOT DISTINCT FROM 'pie-candidate-insight-verification/v1'),
  CHECK ((audit->>'project_id') IS NOT DISTINCT FROM project_id),
  CHECK ((audit->>'generation_sha256') IS NOT DISTINCT FROM generation_sha256),
  CHECK ((audit->>'source_series_id') IS NOT DISTINCT FROM source_series_id),
  CHECK ((audit->>'source_revision') IS NOT DISTINCT FROM source_revision::text),
  CHECK ((audit->>'source_hypothesis_sha256') IS NOT DISTINCT FROM source_hypothesis_sha256),
  CHECK ((audit->>'source_registry_payload_sha256') IS NOT DISTINCT FROM source_registry_payload_sha256),
  CHECK ((audit->>'source_prompt_sha256') IS NOT DISTINCT FROM source_prompt_sha256),
  CHECK ((audit->>'verdict') IS NOT DISTINCT FROM verdict),
  CHECK ((audit->>'reason_code') IS NOT DISTINCT FROM reason_code),
  CHECK ((audit->>'counterevidence_status') IS NOT DISTINCT FROM counterevidence_status),
  CHECK ((audit->'may_publish') IS NOT DISTINCT FROM 'false'::jsonb),
  CHECK ((audit->'may_public_read') IS NOT DISTINCT FROM 'false'::jsonb),
  CHECK ((audit->'causal_claim') IS NOT DISTINCT FROM 'false'::jsonb)
);
CREATE INDEX pie_candidate_insight_verify_source
  ON pie_candidate_insight_verification_events(project_id,generation_sha256,created_at DESC);
CREATE TRIGGER pie_candidate_insight_verify_immutable
  BEFORE UPDATE ON pie_candidate_insight_verification_events FOR EACH ROW
  EXECUTE FUNCTION pie_reject_evidence_update();

-- SQL cannot independently authenticate source rights; the application gates
-- BEFORE INSERT and every future read. Direct writes can never elevate status.
CREATE FUNCTION pie_candidate_insight_verify_guard() RETURNS trigger LANGUAGE plpgsql AS $pie31$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pie_candidate_insight_generations g
    JOIN pie_pattern_registry_revisions r
      ON r.project_id=g.project_id AND r.series_id=g.series_id
     AND r.revision=g.source_revision
    WHERE g.project_id=NEW.project_id
      AND g.generation_sha256=NEW.generation_sha256
      AND g.series_id=NEW.source_series_id
      AND g.source_revision=NEW.source_revision
      AND g.hypothesis_sha256=NEW.source_hypothesis_sha256
      AND g.source_prompt_sha256=NEW.source_prompt_sha256
      AND r.payload_sha256=NEW.source_registry_payload_sha256
  ) THEN
    RAISE EXCEPTION 'PIE verification not bound to exact source and generation';
  END IF;
  RETURN NEW;
END $pie31$;
CREATE TRIGGER pie_candidate_insight_verify_guard_insert
  BEFORE INSERT ON pie_candidate_insight_verification_events FOR EACH ROW
  EXECUTE FUNCTION pie_candidate_insight_verify_guard();
