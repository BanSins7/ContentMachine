-- UA-MOD-04 | Standalone Unified Analytics (UAE) persistence foundation.
-- Additive, PG17, no provider calls, no C score calculation, no copy of
-- PR23 016/017/018. Filename sorts after audited main 053 and peer 054-056.
-- Operates against MAIN's existing project_registry/videos/sources/policy.
-- Group A/B/C are referenced by opaque external IDs, not shadow tables.
-- Pure schema != trust/approval of any source: read side must check LIVE policy.
--
-- No hidden production activation: nothing here starts a job or qualifies data.
-- These tables may receive rows only from a future verified UAE repository.
--
-- NOTE: The migration runner records filename + sha256 inside schema_migrations
-- under one transaction and advisory lock; NEVER edit this SQL after deploy.

CREATE TABLE uae_qualification_requests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  source_policy_version_id BIGINT NOT NULL REFERENCES source_policy_versions(id) ON DELETE CASCADE,
  qdp_run_id UUID NOT NULL,
  qdp_input_sha256 TEXT NOT NULL CHECK (qdp_input_sha256 ~ '^[0-9a-f]{64}$'),
  content_kind TEXT NOT NULL CHECK (content_kind IN (
    'video','slideshow_video','photo_carousel','mixed_carousel')),
  request_key TEXT NOT NULL
    CHECK (length(request_key) BETWEEN 8 AND 180
           AND request_key ~ '^[A-Za-z0-9_.:-]+$'),
  request_sha256 TEXT NOT NULL CHECK (request_sha256 ~ '^[0-9a-f]{64}$'),
  state TEXT NOT NULL DEFAULT 'registered'
    CHECK (state IN ('registered','processing','resolved','failed','cancelled')),
  error_code TEXT CHECK (error_code IS NULL OR
      (length(error_code) BETWEEN 2 AND 80 AND error_code ~ '^[a-z][a-z0-9_]+$')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id, id),
  UNIQUE(project_id, request_key),
  CHECK (updated_at >= created_at)
);
CREATE INDEX uae_request_project_state_idx
  ON uae_qualification_requests(project_id, state, created_at DESC);
CREATE INDEX uae_request_video_idx
  ON uae_qualification_requests(project_id, video_id, source_id, created_at DESC);

-- A request is a pinned, immutable fact; status changes are the sole allowable
-- updates. This is NOT a job queue (stage UA-MOD-17).
CREATE FUNCTION uae_request_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM source_policy_versions p
     WHERE p.id = NEW.source_policy_version_id AND p.source_id = NEW.source_id
  ) THEN
    RAISE EXCEPTION 'UAE request source policy mismatch' USING ERRCODE = '23514';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF (to_jsonb(OLD)-'state'-'error_code'-'updated_at')
       IS DISTINCT FROM (to_jsonb(NEW)-'state'-'error_code'-'updated_at')
       OR OLD.state NOT IN ('registered','processing')
       OR (OLD.state = 'registered'
           AND NEW.state NOT IN ('registered','processing','resolved','failed','cancelled'))
       OR (OLD.state = 'processing'
           AND NEW.state NOT IN ('processing','resolved','failed','cancelled')) THEN
      RAISE EXCEPTION 'UAE request identity/state immutable'
        USING ERRCODE = '23514';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER uae_request_guard_trg
  BEFORE INSERT OR UPDATE ON uae_qualification_requests
  FOR EACH ROW EXECUTE FUNCTION uae_request_guard();

-- Each snapshot is immutable descriptive metadata, NEVER a duplicated catalog
-- or a Group C score store. Invalidation is separate append-only metadata.
CREATE TABLE uae_unified_snapshots (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
  request_id UUID NOT NULL,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  source_policy_version_id BIGINT NOT NULL REFERENCES source_policy_versions(id) ON DELETE CASCADE,
  qdp_run_id UUID NOT NULL,
  qdp_input_sha256 TEXT NOT NULL CHECK (qdp_input_sha256 ~ '^[0-9a-f]{64}$'),
  content_kind TEXT NOT NULL
    CHECK (content_kind IN ('video','slideshow_video','photo_carousel','mixed_carousel')),
  content_type TEXT NOT NULL CHECK (content_type IN ('video','carousel')),
  snapshot_version INTEGER NOT NULL CHECK (snapshot_version >= 1),
  gate_schema TEXT NOT NULL DEFAULT 'uae-qualification/v1'
    CHECK (gate_schema = 'uae-qualification/v1'),
  input_schema TEXT NOT NULL DEFAULT 'unified-analytics-input/v1'
    CHECK (input_schema = 'unified-analytics-input/v1'),
  status TEXT NOT NULL CHECK (status IN ('qualified','partial','blocked')),
  scope_sha256 TEXT NOT NULL CHECK (scope_sha256 ~ '^[0-9a-f]{64}$'),
  inputs_sha256 TEXT NOT NULL CHECK (inputs_sha256 ~ '^[0-9a-f]{64}$'),
  decision_sha256 TEXT NOT NULL CHECK (decision_sha256 ~ '^[0-9a-f]{64}$'),
  sre_run_id UUID,
  sre_decision_sha256 TEXT CHECK (
    sre_decision_sha256 IS NULL OR sre_decision_sha256 ~ '^[0-9a-f]{64}$'),
  group_a_analysis_id BIGINT CHECK (
    group_a_analysis_id IS NULL OR group_a_analysis_id > 0),
  group_b_analysis_id BIGINT CHECK (
    group_b_analysis_id IS NULL OR group_b_analysis_id > 0),
  calculated_metric_id BIGINT CHECK (
    calculated_metric_id IS NULL OR calculated_metric_id > 0),
  metric_snapshot_id BIGINT CHECK (
    metric_snapshot_id IS NULL OR metric_snapshot_id > 0),
  blockers JSONB NOT NULL CHECK (jsonb_typeof(blockers) = 'array'),
  readiness JSONB NOT NULL CHECK (jsonb_typeof(readiness) = 'object'),
  verified_as_of TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (verified_as_of <= created_at),
  UNIQUE(project_id, id),
  UNIQUE(project_id, video_id, source_id, snapshot_version),
  UNIQUE(project_id, request_id, inputs_sha256),
  FOREIGN KEY(project_id, request_id)
    REFERENCES uae_qualification_requests(project_id, id) ON DELETE CASCADE,
  CHECK ((
    content_type = 'video' AND content_kind IN ('video','slideshow_video')
  ) OR (
    content_type = 'carousel' AND content_kind IN ('photo_carousel','mixed_carousel')
  )),
  CHECK (
    (status = 'qualified' AND jsonb_array_length(blockers) = 0) OR
    (status IN ('partial','blocked') AND jsonb_array_length(blockers) > 0)
  ),
  CHECK (status <> 'qualified' OR (
    sre_run_id IS NOT NULL AND sre_decision_sha256 IS NOT NULL
    AND group_a_analysis_id IS NOT NULL
    AND group_b_analysis_id IS NOT NULL
    AND calculated_metric_id IS NOT NULL
    AND metric_snapshot_id IS NOT NULL
    AND readiness->>'source_policy' = 'ready'
    AND readiness->>'d1' = 'ready'
    AND readiness->>'qdp' = 'ready'
    AND readiness->>'sre' = 'ready'
    AND readiness->>'group_a' = 'ready'
    AND readiness->>'group_b' = 'ready'
    AND readiness->>'group_c' = 'ready'
  ))
);
CREATE INDEX uae_snapshot_project_status_idx
  ON uae_unified_snapshots(project_id, status, created_at DESC, id DESC);
CREATE INDEX uae_snapshot_video_versions_idx
  ON uae_unified_snapshots(project_id, video_id, source_id, snapshot_version DESC);
CREATE INDEX uae_snapshot_qdp_idx
  ON uae_unified_snapshots(project_id, qdp_run_id, video_id);
CREATE INDEX uae_snapshot_policy_idx
  ON uae_unified_snapshots(source_id, source_policy_version_id);

-- A checked flag and 64-hex digest are claims, not cryptographic source proof.
-- Missing/unavailable/revoked evidence cannot carry fake ready/verified refs.
CREATE TABLE uae_snapshot_evidence (
  project_id TEXT NOT NULL,
  snapshot_id BIGINT NOT NULL,
  port TEXT NOT NULL CHECK (port IN (
    'source_policy','d1','qdp','sre','group_a','group_b','group_c')),
  state TEXT NOT NULL CHECK (state IN (
    'complete','partial','missing','unavailable','revoked')),
  checked BOOLEAN NOT NULL DEFAULT FALSE,
  is_current BOOLEAN NOT NULL DEFAULT FALSE,
  record_key TEXT CHECK (record_key IS NULL OR
    (length(record_key) BETWEEN 1 AND 160
     AND record_key ~ '^[A-Za-z0-9._:/-]+$')),
  producer_schema TEXT CHECK (producer_schema IS NULL OR
    length(btrim(producer_schema)) BETWEEN 3 AND 128),
  producer_version TEXT CHECK (producer_version IS NULL OR
    length(btrim(producer_version)) BETWEEN 1 AND 160),
  evidence_sha256 TEXT CHECK (
    evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'),
  lineage_sha256 TEXT CHECK (
    lineage_sha256 IS NULL OR lineage_sha256 ~ '^[0-9a-f]{64}$'),
  reason_code TEXT CHECK (reason_code IS NULL OR
    (length(reason_code) BETWEEN 2 AND 80 AND reason_code ~ '^[a-z][a-z0-9_]+$')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(project_id, snapshot_id, port),
  FOREIGN KEY(project_id, snapshot_id)
    REFERENCES uae_unified_snapshots(project_id, id) ON DELETE CASCADE,
  CHECK (
    (state IN ('complete','partial')
      AND record_key IS NOT NULL AND producer_schema IS NOT NULL
      AND producer_version IS NOT NULL AND evidence_sha256 IS NOT NULL
      AND lineage_sha256 IS NOT NULL AND reason_code IS NULL)
    OR
    (state IN ('missing','unavailable','revoked')
      AND record_key IS NULL AND producer_schema IS NULL
      AND producer_version IS NULL AND evidence_sha256 IS NULL
      AND lineage_sha256 IS NULL AND reason_code IS NOT NULL
      AND checked = FALSE AND is_current = FALSE)
  )
);
CREATE INDEX uae_evidence_port_idx
  ON uae_snapshot_evidence(project_id, port, state, created_at DESC);

-- Insert-time membership and source/policy compatibility.
-- Fail-close if the referenced request differs in ANY canonical scope field.
-- Checking actual peer A/B/C IDs, LIVE SRE mode and rights at read-time is a
-- separate adapter concern: arbitrary SQL clients are NOT trusted attestors.
CREATE FUNCTION uae_snapshot_scope_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1
      FROM uae_qualification_requests r
      JOIN source_policy_versions p ON p.id = NEW.source_policy_version_id
       AND p.source_id = NEW.source_id
     WHERE r.id = NEW.request_id
       AND r.project_id = NEW.project_id AND r.video_id = NEW.video_id
       AND r.source_id = NEW.source_id
       AND r.source_policy_version_id = NEW.source_policy_version_id
       AND r.qdp_run_id = NEW.qdp_run_id
       AND r.qdp_input_sha256 = NEW.qdp_input_sha256
       AND r.content_kind = NEW.content_kind
  ) THEN
    RAISE EXCEPTION 'UAE snapshot request/source policy scope mismatch'
      USING ERRCODE = '23514';
  END IF;
  IF NEW.status = 'qualified' AND NOT EXISTS (
    SELECT 1 FROM sources s
      JOIN source_policy_versions p ON p.source_id=s.id
     WHERE s.id=NEW.source_id AND p.id=NEW.source_policy_version_id
       AND s.status='enabled' AND p.is_active IS TRUE
       AND s.can_store_metadata IS TRUE
       AND s.can_analyze_media IS TRUE
       AND s.can_store_metrics IS TRUE
       AND s.can_derive_scores IS TRUE
       AND p.permissions->>'can_store_metadata' = 'true'
       AND p.permissions->>'can_analyze_media' = 'true'
       AND p.permissions->>'can_store_metrics' = 'true'
       AND p.permissions->>'can_derive_scores' = 'true'
  ) THEN
    RAISE EXCEPTION 'UAE qualified snapshot requires current source rights'
      USING ERRCODE = '23514';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER uae_snapshot_scope_guard_trg
  BEFORE INSERT ON uae_unified_snapshots
  FOR EACH ROW EXECUTE FUNCTION uae_snapshot_scope_guard();

-- Complete qualified rows require EXACTLY seven current checked evidence
-- references. Deferral permits snapshot + children in a single transaction,
-- and also rejects removing one child after the initial insert.
CREATE FUNCTION uae_assert_qualified_evidence() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  sid BIGINT;
  pid TEXT;
  snapshot_status TEXT;
  good_count INTEGER;
BEGIN
  IF TG_TABLE_NAME = 'uae_unified_snapshots' THEN
    sid := NEW.id;
    pid := NEW.project_id;
  ELSIF TG_OP = 'DELETE' THEN
    sid := OLD.snapshot_id;
    pid := OLD.project_id;
  ELSE
    sid := NEW.snapshot_id;
    pid := NEW.project_id;
  END IF;
  SELECT status INTO snapshot_status
    FROM uae_unified_snapshots
   WHERE id = sid AND project_id = pid;
  IF NOT FOUND OR snapshot_status <> 'qualified' THEN
    RETURN NULL;
  END IF;
  SELECT COUNT(*) INTO good_count
    FROM uae_snapshot_evidence e
   WHERE e.project_id=pid AND e.snapshot_id=sid
     AND e.state='complete' AND e.checked IS TRUE AND e.is_current IS TRUE
     AND e.record_key IS NOT NULL AND e.producer_schema IS NOT NULL
     AND e.producer_version IS NOT NULL AND e.evidence_sha256 IS NOT NULL
     AND e.lineage_sha256 IS NOT NULL;
  IF good_count <> 7 THEN
    RAISE EXCEPTION 'Qualified UAE snapshot needs 7 complete, checked, current evidence ports'
      USING ERRCODE = '23514';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER uae_snapshot_qualified_check
  AFTER INSERT ON uae_unified_snapshots
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
  EXECUTE FUNCTION uae_assert_qualified_evidence();
CREATE CONSTRAINT TRIGGER uae_snapshot_evidence_qualified_check
  AFTER INSERT OR UPDATE OR DELETE ON uae_snapshot_evidence
  DEFERRABLE INITIALLY DEFERRED FOR EACH ROW
  EXECUTE FUNCTION uae_assert_qualified_evidence();

-- Immutable snapshots/evidence cannot be silently reclassified or rewritten.
-- DELETE is deliberately permitted for future privacy/retention purge.
CREATE FUNCTION uae_immutable_update_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'UAE historical records cannot be modified: append a new version'
    USING ERRCODE = '23514';
END $$;
CREATE TRIGGER uae_snapshot_immutable_update
  BEFORE UPDATE ON uae_unified_snapshots FOR EACH ROW
  EXECUTE FUNCTION uae_immutable_update_guard();
CREATE TRIGGER uae_evidence_immutable_update
  BEFORE UPDATE ON uae_snapshot_evidence FOR EACH ROW
  EXECUTE FUNCTION uae_immutable_update_guard();

-- Separate revocation ledger. A historical qualified outcome is NEVER a right
-- to read today: future read views require current source policy + NOT EXISTS
-- an invalidation event. Absence of invalidation does NOT imply rights.
CREATE TABLE uae_snapshot_invalidations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  snapshot_id BIGINT NOT NULL,
  reason_code TEXT NOT NULL
    CHECK(length(reason_code) BETWEEN 2 AND 80
          AND reason_code ~ '^[a-z][a-z0-9_]+$'),
  reason_sha256 TEXT NOT NULL CHECK (reason_sha256 ~ '^[0-9a-f]{64}$'),
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id, snapshot_id, reason_sha256),
  FOREIGN KEY(project_id, snapshot_id)
    REFERENCES uae_unified_snapshots(project_id, id) ON DELETE CASCADE
);
CREATE INDEX uae_invalidations_project_time_idx
  ON uae_snapshot_invalidations(project_id, recorded_at DESC);
CREATE TRIGGER uae_invalidations_immutable_update
  BEFORE UPDATE ON uae_snapshot_invalidations FOR EACH ROW
  EXECUTE FUNCTION uae_immutable_update_guard();

-- Minimal append-only (until explicit privacy purge) provenance without raw
-- video/media or secret peer payload copies.
CREATE TABLE uae_audit_events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
  video_id BIGINT REFERENCES videos(id) ON DELETE CASCADE,
  actor_type TEXT NOT NULL CHECK (actor_type IN ('system','worker','operator')),
  action TEXT NOT NULL CHECK (length(action) BETWEEN 2 AND 100
     AND action ~ '^[a-z][a-z0-9_]+$'),
  request_id UUID,
  snapshot_id BIGINT,
  event_sha256 TEXT NOT NULL CHECK (event_sha256 ~ '^[0-9a-f]{64}$'),
  detail JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(detail)='object'),
  recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id, event_sha256),
  CHECK (snapshot_id IS NULL OR snapshot_id > 0)
);
CREATE INDEX uae_audit_project_time_idx
  ON uae_audit_events(project_id, recorded_at DESC);
CREATE TRIGGER uae_audit_immutable_update
  BEFORE UPDATE ON uae_audit_events FOR EACH ROW
  EXECUTE FUNCTION uae_immutable_update_guard();

-- Intentionally NO:
-- * SELECT read-visibility view (UA-MOD-19; must join live source rights);
-- * durable UAE job queue (UA-MOD-17), outbox (UA-MOD-16);
-- * second metric_snapshots / calculated_metrics / cohorts / source policy;
-- * SQL that fabricates a LIVE SRE match, QDP membership, or Group C score.
