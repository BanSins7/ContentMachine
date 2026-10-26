-- PIE-MOD-04: standalone, additive research evidence storage (schema only).
-- Standalone PIE owns these objects. No UAE/Group A/B/C/SRE/QDP schema changes.
-- All SHA hashes below are integrity labels, NOT producer authentication.
-- No background workers, paid requests, policies or statistical claims start here.

CREATE TABLE pie_research_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id),
  source_policy_version_id BIGINT NOT NULL REFERENCES source_policy_versions(id),
  calculation_config_id BIGINT NOT NULL CHECK (calculation_config_id > 0),
  design_sha256 TEXT NOT NULL CHECK (design_sha256 ~ '^[0-9a-f]{64}$'),
  engine_version TEXT NOT NULL CHECK (length(btrim(engine_version)) BETWEEN 1 AND 120),
  as_of TIMESTAMPTZ NOT NULL,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','blocked','cancelled','invalidated')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,id,source_id),
  CHECK (as_of <= created_at + INTERVAL '1 day')
);
CREATE INDEX pie_runs_project_created ON pie_research_runs(project_id,created_at DESC);

-- Defensively bind supplied policy ID to the same source. This is NOT a
-- current-authorization/read-time rights gate; PIE-MOD-06 must enforce that.
CREATE FUNCTION pie_run_policy_source_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM source_policy_versions p
    WHERE p.id=NEW.source_policy_version_id AND p.source_id=NEW.source_id
  ) THEN RAISE EXCEPTION 'PIE source policy does not belong to source'; END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER pie_run_source_guard
  BEFORE INSERT OR UPDATE ON pie_research_runs
  FOR EACH ROW EXECUTE FUNCTION pie_run_policy_source_guard();

CREATE TABLE pie_dataset_manifests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  dataset_sha256 TEXT NOT NULL CHECK (dataset_sha256 ~ '^[0-9a-f]{64}$'),
  manifest_sha256 TEXT NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
  producer_run_key TEXT NOT NULL CHECK (length(btrim(producer_run_key)) BETWEEN 1 AND 160),
  input_schema TEXT NOT NULL CHECK(input_schema='uae-to-pie/v1'),
  included_count INTEGER NOT NULL CHECK(included_count BETWEEN 1 AND 50000),
  excluded_count INTEGER NOT NULL CHECK(excluded_count BETWEEN 0 AND 50000),
  selection_spec JSONB NOT NULL CHECK(jsonb_typeof(selection_spec)='object'),
  provenance JSONB NOT NULL CHECK(jsonb_typeof(provenance)='object'),
  -- A schema validation cannot establish a real source receipt.
  evidence_state TEXT NOT NULL DEFAULT 'unverified'
    CHECK(evidence_state IN ('unverified','verified','quarantined','invalidated')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,id,run_id),
  UNIQUE(project_id,run_id,dataset_sha256),
  FOREIGN KEY(project_id,run_id) REFERENCES pie_research_runs(project_id,id) ON DELETE CASCADE
);
CREATE INDEX pie_manifests_run ON pie_dataset_manifests(project_id,run_id,created_at DESC);
CREATE INDEX pie_manifests_review ON pie_dataset_manifests(project_id,evidence_state);

-- Only references/hashes: never raw media, private transcripts or creator PII.
-- The membership table is immutable and scoped through its parent dataset.
CREATE TABLE pie_dataset_members (
  project_id TEXT NOT NULL,
  dataset_id UUID NOT NULL,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  uae_snapshot_id BIGINT CHECK(uae_snapshot_id > 0),
  row_sha256 TEXT CHECK(row_sha256 ~ '^[0-9a-f]{64}$'),
  included BOOLEAN NOT NULL,
  exclusion_reason TEXT CHECK(exclusion_reason ~ '^[a-z][a-z0-9_]{1,79}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,dataset_id,video_id),
  FOREIGN KEY(project_id,dataset_id) REFERENCES pie_dataset_manifests(project_id,id) ON DELETE CASCADE,
  CHECK ((included AND uae_snapshot_id IS NOT NULL AND row_sha256 IS NOT NULL AND exclusion_reason IS NULL)
    OR (NOT included AND uae_snapshot_id IS NULL AND row_sha256 IS NULL AND exclusion_reason IS NOT NULL))
);
CREATE UNIQUE INDEX pie_dataset_snapshot_unique
  ON pie_dataset_members(project_id,dataset_id,uae_snapshot_id) WHERE included;
CREATE INDEX pie_dataset_members_video ON pie_dataset_members(video_id);

CREATE TABLE pie_comparison_sets (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  dataset_id UUID NOT NULL,
  definition_sha256 TEXT NOT NULL CHECK(definition_sha256 ~ '^[0-9a-f]{64}$'),
  definition JSONB NOT NULL CHECK(jsonb_typeof(definition)='object'),
  status TEXT NOT NULL DEFAULT 'draft' CHECK(status IN ('draft','frozen')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,id,dataset_id),
  UNIQUE(project_id,dataset_id,definition_sha256),
  FOREIGN KEY(project_id,dataset_id) REFERENCES pie_dataset_manifests(project_id,id) ON DELETE CASCADE
);

CREATE TABLE pie_pattern_candidates (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  dataset_id UUID NOT NULL,
  comparison_id UUID NOT NULL,
  hypothesis_sha256 TEXT NOT NULL CHECK(hypothesis_sha256 ~ '^[0-9a-f]{64}$'),
  evidence_sha256 TEXT NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$'),
  candidate JSONB NOT NULL CHECK(jsonb_typeof(candidate)='object'),
  -- A stored candidate is never automatically statistically supported.
  status TEXT NOT NULL DEFAULT 'exploratory' CHECK(status='exploratory'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,id,dataset_id),
  UNIQUE(project_id,dataset_id,comparison_id,hypothesis_sha256,evidence_sha256),
  FOREIGN KEY(project_id,comparison_id,dataset_id)
    REFERENCES pie_comparison_sets(project_id,id,dataset_id) ON DELETE CASCADE
);
CREATE INDEX pie_candidates_scope ON pie_pattern_candidates(project_id,dataset_id,created_at DESC);

CREATE TABLE pie_pattern_validations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  candidate_id UUID NOT NULL,
  validation_sha256 TEXT NOT NULL CHECK(validation_sha256 ~ '^[0-9a-f]{64}$'),
  verdict TEXT NOT NULL CHECK(verdict IN ('supported','rejected','needs_review','invalidated','insufficient')),
  results JSONB NOT NULL CHECK(jsonb_typeof(results)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,candidate_id,validation_sha256),
  FOREIGN KEY(project_id,candidate_id) REFERENCES pie_pattern_candidates(project_id,id) ON DELETE CASCADE
);

CREATE TABLE pie_insight_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  candidate_id UUID NOT NULL,
  revision INTEGER NOT NULL CHECK(revision > 0),
  insight_sha256 TEXT NOT NULL CHECK(insight_sha256 ~ '^[0-9a-f]{64}$'),
  evidence_sha256 TEXT NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$'),
  text_payload JSONB NOT NULL CHECK(jsonb_typeof(text_payload)='object'),
  -- No pre-approval / publish shortcut; later review determines read visibility.
  state TEXT NOT NULL DEFAULT 'proposed' CHECK(state='proposed'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,candidate_id,revision),
  FOREIGN KEY(project_id,candidate_id) REFERENCES pie_pattern_candidates(project_id,id) ON DELETE CASCADE
);
CREATE INDEX pie_insights_project_candidate ON pie_insight_versions(project_id,candidate_id,revision DESC);

CREATE TABLE pie_insight_reviews (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  insight_id UUID NOT NULL,
  review_sha256 TEXT NOT NULL CHECK(review_sha256 ~ '^[0-9a-f]{64}$'),
  reviewer_id TEXT NOT NULL CHECK(length(btrim(reviewer_id)) BETWEEN 1 AND 160),
  decision TEXT NOT NULL CHECK(decision IN ('approved','rejected','changes_requested')),
  rationale JSONB NOT NULL CHECK(jsonb_typeof(rationale)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,insight_id,review_sha256),
  FOREIGN KEY(project_id,insight_id) REFERENCES pie_insight_versions(project_id,id) ON DELETE CASCADE
);

CREATE TABLE pie_spend_authorizations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
  approved_by TEXT NOT NULL CHECK(length(btrim(approved_by)) BETWEEN 1 AND 160),
  approved_usd NUMERIC(20,12) NOT NULL CHECK(approved_usd > 0),
  expires_at TIMESTAMPTZ NOT NULL,
  approval_sha256 TEXT NOT NULL CHECK(approval_sha256 ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  CHECK(expires_at > created_at)
);

CREATE TABLE pie_model_attempts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  authorization_id UUID NOT NULL,
  request_sha256 TEXT NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
  reserved_usd NUMERIC(20,12) NOT NULL CHECK(reserved_usd > 0),
  attempt_state TEXT NOT NULL DEFAULT 'planned' CHECK(attempt_state='planned'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,request_sha256), -- forbid blind retry of ambiguous paid calls
  FOREIGN KEY(project_id,authorization_id)
    REFERENCES pie_spend_authorizations(project_id,id) ON DELETE CASCADE
);
CREATE INDEX pie_model_attempts_auth ON pie_model_attempts(project_id,authorization_id);

-- Serialize budget reservations even if several workers race for the same
-- project authorization. This checks *maximum* possible spend; uncertainty
-- must never release reserved money automatically.
CREATE FUNCTION pie_model_attempt_budget_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE approved NUMERIC(20,12);
DECLARE expiration TIMESTAMPTZ;
DECLARE already_reserved NUMERIC(20,12);
BEGIN
  SELECT approved_usd,expires_at INTO approved,expiration
    FROM pie_spend_authorizations
    WHERE project_id=NEW.project_id AND id=NEW.authorization_id FOR UPDATE;
  IF NOT FOUND OR expiration<=clock_timestamp() THEN
    RAISE EXCEPTION 'PIE budget authorization unavailable or expired';
  END IF;
  SELECT COALESCE(sum(reserved_usd),0) INTO already_reserved
    FROM pie_model_attempts
    WHERE project_id=NEW.project_id AND authorization_id=NEW.authorization_id;
  IF already_reserved+NEW.reserved_usd>approved THEN
    RAISE EXCEPTION 'PIE budget reservation exceeds authorization';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER pie_attempt_budget_guard
 BEFORE INSERT ON pie_model_attempts
 FOR EACH ROW EXECUTE FUNCTION pie_model_attempt_budget_guard();

-- Immutable append-only cost events; status is derived, never overwrite money.
CREATE TABLE pie_model_attempt_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  attempt_id UUID NOT NULL,
  event_seq INTEGER NOT NULL CHECK(event_seq > 0),
  event_type TEXT NOT NULL CHECK(event_type IN ('reserved','charged','refunded','failed','uncertain')),
  amount_usd NUMERIC(20,12) CHECK(amount_usd IS NULL OR amount_usd >= 0),
  event_sha256 TEXT NOT NULL CHECK(event_sha256 ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,attempt_id,event_seq),
  FOREIGN KEY(project_id,attempt_id) REFERENCES pie_model_attempts(project_id,id) ON DELETE CASCADE,
  CHECK((event_type IN ('reserved','charged','refunded') AND amount_usd IS NOT NULL)
    OR (event_type IN ('failed','uncertain') AND amount_usd IS NULL))
);

CREATE TABLE pie_jobs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  kind TEXT NOT NULL CHECK(kind IN ('dataset','discovery','validation','insight','review','invalidation')),
  idempotency_key TEXT NOT NULL CHECK(length(btrim(idempotency_key)) BETWEEN 12 AND 160),
  state TEXT NOT NULL DEFAULT 'queued'
    CHECK(state IN ('queued','leased','completed','failed','cancelled','blocked')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
  max_attempts INTEGER NOT NULL CHECK(max_attempts BETWEEN 1 AND 20),
  fence_token BIGINT NOT NULL DEFAULT 0 CHECK(fence_token >= 0),
  lease_until TIMESTAMPTZ,
  leased_by TEXT,
  available_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,idempotency_key),
  FOREIGN KEY(project_id,run_id) REFERENCES pie_research_runs(project_id,id) ON DELETE CASCADE,
  CHECK(state <> 'leased' OR (lease_until IS NOT NULL AND leased_by IS NOT NULL AND length(btrim(leased_by)) > 0))
);
CREATE INDEX pie_jobs_claim ON pie_jobs(state,available_at) WHERE state IN ('queued','leased');

CREATE TABLE pie_outbox (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  event_key TEXT NOT NULL CHECK(length(btrim(event_key)) BETWEEN 1 AND 160),
  event_type TEXT NOT NULL CHECK(length(btrim(event_type)) BETWEEN 1 AND 100),
  payload_sha256 TEXT NOT NULL CHECK(payload_sha256 ~ '^[0-9a-f]{64}$'),
  payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,event_key),
  FOREIGN KEY(project_id,run_id) REFERENCES pie_research_runs(project_id,id) ON DELETE CASCADE
);
CREATE INDEX pie_outbox_run ON pie_outbox(project_id,run_id,created_at DESC);

CREATE TABLE pie_invalidation_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  input_sha256 TEXT NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
  event_sha256 TEXT NOT NULL CHECK(event_sha256 ~ '^[0-9a-f]{64}$'),
  reason_code TEXT NOT NULL CHECK(reason_code ~ '^[a-z][a-z0-9_]{1,79}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,run_id,event_sha256),
  FOREIGN KEY(project_id,run_id) REFERENCES pie_research_runs(project_id,id) ON DELETE CASCADE
);

-- Research and financial evidence are append-only. Maintenance may purge rows
-- through explicit DELETE/retention workflows; UPDATE is never allowed.
CREATE FUNCTION pie_reject_evidence_update() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'PIE immutable evidence: UPDATE refused on %', TG_TABLE_NAME;
END $$;
CREATE TRIGGER pie_run_immutable BEFORE UPDATE ON pie_research_runs
 FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();
CREATE TRIGGER pie_manifest_immutable BEFORE UPDATE ON pie_dataset_manifests
 FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();
CREATE TRIGGER pie_member_immutable BEFORE UPDATE ON pie_dataset_members
 FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();
CREATE TRIGGER pie_comparison_immutable BEFORE UPDATE ON pie_comparison_sets
 FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();
CREATE TRIGGER pie_candidate_immutable BEFORE UPDATE ON pie_pattern_candidates
 FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();
CREATE TRIGGER pie_validation_immutable BEFORE UPDATE ON pie_pattern_validations
 FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();
CREATE TRIGGER pie_insight_immutable BEFORE UPDATE ON pie_insight_versions
 FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();
CREATE TRIGGER pie_review_immutable BEFORE UPDATE ON pie_insight_reviews
 FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();
CREATE TRIGGER pie_approval_immutable BEFORE UPDATE ON pie_spend_authorizations
 FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();
CREATE TRIGGER pie_attempt_immutable BEFORE UPDATE ON pie_model_attempts
 FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();
CREATE TRIGGER pie_attempt_event_immutable BEFORE UPDATE ON pie_model_attempt_events
 FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();
CREATE TRIGGER pie_outbox_immutable BEFORE UPDATE ON pie_outbox
 FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();
CREATE TRIGGER pie_invalidation_immutable BEFORE UPDATE ON pie_invalidation_events
 FOR EACH ROW EXECUTE FUNCTION pie_reject_evidence_update();
-- pie_jobs is intentionally mutable by a future leased worker with fencing;
-- PIE-MOD-33 must implement transactional write predicates/lease ownership.
