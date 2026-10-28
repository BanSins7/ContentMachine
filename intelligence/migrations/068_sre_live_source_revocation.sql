-- M34 forward-only 068: source-revocation suppression + durable outbox.
-- This migration intentionally retains immutable money/receipt/audit hashes.
-- No direct deletion of other modules' D1 storage or unverified financial rows.
CREATE TABLE sre_live_source_tombstones (
  project_id TEXT NOT NULL REFERENCES sre_workspaces(project_id),
  content_version_id BIGINT NOT NULL,
  video_id BIGINT NOT NULL,
  content_sha256 TEXT NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
  source_policy_version_id BIGINT NOT NULL,
  reason_code TEXT NOT NULL CHECK(reason_code IN
    ('rights_revoked','source_deleted','retention_expired','privacy_delete')),
  actor_id TEXT NOT NULL CHECK(actor_id ~ '^[A-Za-z0-9:_@.-]{1,128}$'),
  tombstoned_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,content_version_id),
  CHECK(content_version_id>0 AND video_id>0 AND source_policy_version_id>0)
);
CREATE INDEX sre_m34_tombstone_by_policy
  ON sre_live_source_tombstones(project_id,source_policy_version_id,video_id);

CREATE TABLE sre_live_revocation_outbox (
  event_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL,
  content_version_id BIGINT NOT NULL,
  run_id UUID NOT NULL,
  state TEXT NOT NULL DEFAULT 'pending'
    CHECK(state IN ('pending','acknowledged')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  acknowledged_at TIMESTAMPTZ,
  actor_id TEXT,
  UNIQUE(project_id,content_version_id,run_id),
  FOREIGN KEY(project_id,content_version_id)
    REFERENCES sre_live_source_tombstones(project_id,content_version_id)
    ON DELETE RESTRICT,
  FOREIGN KEY(run_id,project_id)
    REFERENCES sre_live_runs(run_id,project_id) ON DELETE RESTRICT,
  CHECK ((state='pending' AND acknowledged_at IS NULL AND actor_id IS NULL)
      OR (state='acknowledged' AND acknowledged_at IS NOT NULL
          AND actor_id ~ '^[A-Za-z0-9:_@.-]{1,128}$'))
);
CREATE INDEX sre_m34_outbox_pending
  ON sre_live_revocation_outbox(project_id,run_id,event_id)
  WHERE state='pending';

-- Preserve offline-run identity after purging S4 item rows. Without this
-- durable fence a late worker could append S4 stage_execution hashes after
-- both result and item were physically deleted.
CREATE TABLE sre_offline_revocation_fences (
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  content_version_id BIGINT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,run_id,content_version_id),
  FOREIGN KEY(project_id,content_version_id)
    REFERENCES sre_live_source_tombstones(project_id,content_version_id)
      ON DELETE RESTRICT,
  FOREIGN KEY(run_id,project_id)
    REFERENCES sre_offline_runs(run_id,project_id) ON DELETE RESTRICT
);
CREATE INDEX sre_m34_offline_run_fenced
  ON sre_offline_revocation_fences(project_id,run_id);

CREATE FUNCTION sre_m34_guard_tombstone() RETURNS trigger
LANGUAGE plpgsql AS $m34_tombstone$
DECLARE source_row RECORD;
BEGIN
  IF TG_OP<>'INSERT' THEN
    RAISE EXCEPTION 'M34 revocation tombstone is permanent';
  END IF;
  -- Trusted operation takes project EXCLUSIVE; direct privileged SQL
  -- coordinates with the same workspace lock and verifies exact identity.
  PERFORM 1 FROM sre_workspaces WHERE project_id=NEW.project_id FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'M34 workspace missing'; END IF;
  SELECT video_id,content_sha256,source_policy_version_id INTO source_row
    FROM sre_content_versions WHERE project_id=NEW.project_id
      AND id=NEW.content_version_id FOR SHARE;
  IF NOT FOUND OR source_row.video_id IS DISTINCT FROM NEW.video_id
     OR source_row.content_sha256 IS DISTINCT FROM NEW.content_sha256
     OR source_row.source_policy_version_id IS DISTINCT FROM
        NEW.source_policy_version_id THEN
    RAISE EXCEPTION 'M34 revocation does not match original tenant content';
  END IF;
  RETURN NEW;
END;
$m34_tombstone$;
CREATE TRIGGER sre_m34_tombstone_insert
  BEFORE INSERT ON sre_live_source_tombstones
  FOR EACH ROW EXECUTE FUNCTION sre_m34_guard_tombstone();
CREATE TRIGGER sre_m34_tombstone_immutable
  BEFORE UPDATE OR DELETE ON sre_live_source_tombstones
  FOR EACH ROW EXECUTE FUNCTION sre_m34_guard_tombstone();
CREATE TRIGGER sre_m34_offline_fence_immutable
  BEFORE UPDATE OR DELETE ON sre_offline_revocation_fences
  FOR EACH ROW EXECUTE FUNCTION sre_m34_guard_tombstone();

CREATE FUNCTION sre_m34_revoke_dependents() RETURNS trigger
LANGUAGE plpgsql AS $m34_revoke$
BEGIN
  -- Vectors are disposable; delete them, not immutable audit receipts.
  DELETE FROM sre_local_embedding_cache
    WHERE project_id=NEW.project_id
      AND content_version_id=NEW.content_version_id;
  INSERT INTO sre_offline_revocation_fences
    (project_id,run_id,content_version_id)
  SELECT DISTINCT i.project_id,i.run_id,i.content_version_id
  FROM sre_offline_items i
  WHERE i.project_id=NEW.project_id
    AND i.content_version_id=NEW.content_version_id
  ON CONFLICT DO NOTHING;
  -- Only S4 OFFLINE_FIXTURE result rows may be physically deleted.
  -- These can contain derived evidence snippets, never provider receipts.
  DELETE FROM sre_classification_results result
  USING sre_classification_runs run
    WHERE run.id=result.run_id AND run.project_id=result.project_id
      AND run.execution_mode='offline_fixture'
      AND result.project_id=NEW.project_id
      AND result.content_version_id=NEW.content_version_id;
  -- S4 is offline_fixture only: drop raw derived contexts rather than
  -- expose text in a fake-result history. Historical LIVE paid/financial
  -- records cannot be physically removed without separate retention review.
  -- Delete S4-only execution metadata by the still-present run/video
  -- mapping before removing its last offline item row.
  DELETE FROM sre_stage_executions stage
  USING sre_offline_items i
  WHERE stage.project_id=i.project_id AND stage.run_id=i.run_id
    AND stage.video_id=i.video_id AND i.project_id=NEW.project_id
    AND i.content_version_id=NEW.content_version_id;
  DELETE FROM sre_offline_items
    WHERE project_id=NEW.project_id
      AND content_version_id=NEW.content_version_id;
  -- M34 safe funds cleanup: release ONLY pre-dispatch M25 holds.
  -- A paid/unknown intent or a settled receipt must never be refunded by
  -- source revocation. The original M25 trigger enforces the same
  -- never-release-after-possible-dispatch invariant and updates the exact
  -- NUMERIC(24,9) counters/journal atomically.
  UPDATE sre_live_budget_reservations res
  SET state='released',released_at=clock_timestamp(),
      release_actor=NEW.actor_id,release_reason='rights_revoked'
  FROM sre_live_items i
  WHERE res.project_id=NEW.project_id AND res.project_id=i.project_id
    AND res.run_id=i.run_id AND res.item_id=i.id
    AND i.content_version_id=NEW.content_version_id
    AND res.state='reserved'
    AND NOT EXISTS (
      SELECT 1 FROM sre_live_attempt_fences f
      WHERE f.project_id=res.project_id AND f.run_id=res.run_id
        AND f.item_id=res.item_id AND f.state IN (
          'dispatch_intent','unknown','completed','failed_known'))
    AND NOT EXISTS (
      SELECT 1 FROM sre_live_opus_dispatch_intents d
      WHERE d.project_id=res.project_id
        AND d.reservation_id=res.reservation_id
    );
  -- M34 also invalidates stale human authorization for unsent work.
  -- Genuine M26 intent/paid requests retain their original consent
  -- evidence; they are not erased or reinterpreted as unapproved spending.
  UPDATE sre_live_opus_consents consent
  SET revoked_at=GREATEST(clock_timestamp(),consent.granted_at),
      revoked_by=NEW.actor_id,revoke_reason='security_revoked'
  FROM sre_live_items i
  WHERE consent.project_id=NEW.project_id
    AND consent.project_id=i.project_id
    AND consent.run_id=i.run_id AND consent.item_id=i.id
    AND i.content_version_id=NEW.content_version_id
    AND consent.revoked_at IS NULL
    AND NOT EXISTS (
      SELECT 1 FROM sre_live_opus_dispatch_intents d
      WHERE d.project_id=consent.project_id
        AND d.run_id=consent.run_id
        AND d.item_id=consent.item_id
        AND d.consent_id=consent.consent_id
    )
    AND NOT EXISTS (
      SELECT 1 FROM sre_live_attempt_fences f
      WHERE f.project_id=consent.project_id
        AND f.run_id=consent.run_id AND f.item_id=consent.item_id
        AND f.state IN (
          'dispatch_intent','unknown','completed','failed_known')
    );
  INSERT INTO sre_live_revocation_outbox
    (project_id,content_version_id,run_id)
  SELECT DISTINCT i.project_id,i.content_version_id,i.run_id
  FROM sre_live_items i
  WHERE i.project_id=NEW.project_id
    AND i.content_version_id=NEW.content_version_id
  ON CONFLICT DO NOTHING;
  RETURN NEW;
END;
$m34_revoke$;
CREATE TRIGGER sre_m34_tombstone_dependents
  AFTER INSERT ON sre_live_source_tombstones
  FOR EACH ROW EXECUTE FUNCTION sre_m34_revoke_dependents();

CREATE FUNCTION sre_m34_guard_outbox() RETURNS trigger
LANGUAGE plpgsql AS $m34_outbox$
BEGIN
  IF TG_OP='DELETE' THEN
    RAISE EXCEPTION 'M34 revocation outbox cannot be deleted';
  ELSIF TG_OP='UPDATE' THEN
    IF OLD.state<>'pending' OR NEW.state<>'acknowledged'
       OR NEW.project_id IS DISTINCT FROM OLD.project_id
       OR NEW.content_version_id IS DISTINCT FROM OLD.content_version_id
       OR NEW.run_id IS DISTINCT FROM OLD.run_id
       OR NEW.event_id IS DISTINCT FROM OLD.event_id
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.acknowledged_at IS NULL OR NEW.actor_id IS NULL
       OR NOT EXISTS (
         SELECT 1 FROM sre_live_runs r
         WHERE r.project_id=OLD.project_id AND r.run_id=OLD.run_id
           AND (r.cancel_requested=TRUE OR
                r.state IN ('cancelled','completed','partial','failed'))
       )
    THEN RAISE EXCEPTION 'M34 outbox cannot acknowledge an active run'; END IF;
  END IF;
  RETURN NEW;
END;
$m34_outbox$;
CREATE TRIGGER sre_m34_outbox_guard
  BEFORE UPDATE OR DELETE ON sre_live_revocation_outbox
  FOR EACH ROW EXECUTE FUNCTION sre_m34_guard_outbox();

-- Preserve the S3 readable-content view's existing shape and D1 checks,
-- adding permanent M34 source-level suppression even after policy regrant.
CREATE OR REPLACE VIEW sre_readable_content_versions AS
SELECT c.*
FROM sre_content_versions c
JOIN sources s ON s.id=c.source_id
JOIN source_policy_versions p ON p.id=c.source_policy_version_id
  AND p.source_id=s.id
LEFT JOIN source_video_metadata obs ON obs.id=c.metadata_observation_id
LEFT JOIN media_assets m ON m.id=c.media_asset_id
WHERE s.status='enabled' AND s.can_store_metadata
  AND p.is_active AND p.reviewed_at IS NOT NULL
  AND p.permissions->'can_store_metadata'='true'::jsonb
  AND p.permissions->'can_semantic_classify'='true'::jsonb
  AND (c.expires_at IS NULL OR c.expires_at>now())
  AND (s.retention_days IS NULL OR
       c.created_at+s.retention_days*interval '1 day'>now())
  AND (NOT (p.retention_policy ? 'retention_days'
           AND jsonb_typeof(p.retention_policy->'retention_days')='number')
       OR (c.created_at + 
           (p.retention_policy->>'retention_days')::integer * interval '1 day'>now()))
  AND (c.intake_fingerprint IS NULL OR EXISTS (
       SELECT 1 FROM sre_project_discoveries pd
       JOIN discoveries d ON d.id=pd.discovery_id
       WHERE pd.project_id=c.project_id AND pd.video_id=c.video_id
         AND d.video_id=c.video_id AND d.source_id=c.source_id
         AND pd.discovery_id=(c.provenance->>'discovery_id')::bigint))
  AND (c.metadata_observation_id IS NULL OR
       (obs.id IS NOT NULL AND obs.video_id=c.video_id
        AND obs.source_id=c.source_id
        AND obs.source_policy_version_id=c.source_policy_version_id
        AND obs.is_current AND obs.deleted_at IS NULL
        AND (obs.expires_at IS NULL OR obs.expires_at>now())))
  AND ((NOT (c.content ? 'transcript' OR c.content ? 'scenes'))
       OR (s.can_analyze_media AND
           p.permissions->'can_analyze_media'='true'::jsonb AND m.id IS NOT NULL))
  AND (m.id IS NULL OR (m.video_id=c.video_id AND m.source_id=c.source_id
       AND m.permission<>'metadata_only'
       AND (m.expires_at IS NULL OR m.expires_at>now())))
  AND NOT EXISTS (
    SELECT 1 FROM sre_live_source_tombstones revoked
    WHERE revoked.project_id=c.project_id
      AND revoked.content_version_id=c.id
  );

-- Every read of SRE's formerly public LIVE result view now excludes the
-- exact tombstoned source, even if its D1 policy was re-enabled later.
CREATE OR REPLACE VIEW sre_live_classification_results AS
SELECT result.*
FROM sre_classification_results result
JOIN sre_classification_runs run
  ON run.id=result.run_id AND run.project_id=result.project_id
WHERE run.execution_mode='live'
  AND run.status IN ('completed','partial')
  AND EXISTS (
    SELECT 1 FROM sre_readable_content_versions content
    WHERE content.id=result.content_version_id
      AND content.project_id=result.project_id
      AND content.video_id=result.video_id)
  AND NOT EXISTS (
    SELECT 1 FROM sre_live_source_tombstones revoked
    WHERE revoked.project_id=result.project_id
      AND revoked.content_version_id=result.content_version_id
  );

-- Keep a restarted/on-going cache writer from reinstating revoked vectors.
CREATE FUNCTION sre_m34_guard_cache_on_revoke() RETURNS trigger
LANGUAGE plpgsql AS $m34_cache$
BEGIN
  -- Serialize direct SQL writes with the exclusive project tombstone lock.
  PERFORM 1 FROM sre_workspaces WHERE project_id=NEW.project_id FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'M34 workspace unavailable'; END IF;
  IF EXISTS (
    SELECT 1 FROM sre_live_source_tombstones revoked
    WHERE revoked.project_id=NEW.project_id
      AND revoked.content_version_id=NEW.content_version_id
  ) THEN RAISE EXCEPTION 'M34 revoked source cannot be recached'; END IF;
  RETURN NEW;
END;
$m34_cache$;
CREATE TRIGGER sre_m34_cache_no_recreate
  BEFORE INSERT ON sre_local_embedding_cache
  FOR EACH ROW EXECUTE FUNCTION sre_m34_guard_cache_on_revoke();

-- SQL boundary too: even privileged inserts cannot resurrect a revoked
-- source via new items, Opus intents, receipts, human reviews or gold reports.
CREATE FUNCTION sre_m34_block_derived_after_revoke() RETURNS trigger
LANGUAGE plpgsql AS $m34_derived$
DECLARE selected_content BIGINT;
BEGIN
  -- All SRE direct SQL derived writes share-lock the same workspace row
  -- that revocation holds EXCLUSIVE; no post-commit resurrection race.
  PERFORM 1 FROM sre_workspaces WHERE project_id=NEW.project_id FOR SHARE;
  IF NOT FOUND THEN RAISE EXCEPTION 'M34 workspace unavailable'; END IF;
  IF TG_TABLE_NAME IN (
    'sre_live_items','sre_offline_items','sre_classification_results',
    'sre_live_opus_quotes','sre_live_opus_consents',
    'sre_live_budget_reservations','sre_live_opus_dispatch_intents',
    'sre_live_verified_pair_results','sre_live_human_reviews'
  ) THEN
    IF TG_TABLE_NAME IN (
      'sre_live_items','sre_offline_items','sre_classification_results'
    ) THEN
      selected_content:=NEW.content_version_id;
    ELSE
      SELECT i.content_version_id INTO selected_content
      FROM sre_live_items i WHERE i.project_id=NEW.project_id
        AND i.run_id=NEW.run_id AND i.id=NEW.item_id;
    END IF;
    IF EXISTS (
      SELECT 1 FROM sre_live_source_tombstones t
      WHERE t.project_id=NEW.project_id
        AND t.content_version_id=selected_content
    ) THEN
      RAISE EXCEPTION 'M34 revoked source cannot create derived result';
    END IF;
  ELSIF TG_TABLE_NAME='sre_stage_executions' THEN
    IF EXISTS (
      SELECT 1 FROM sre_offline_revocation_fences f
      WHERE f.project_id=NEW.project_id AND f.run_id=NEW.run_id
    ) THEN
      RAISE EXCEPTION 'M34 revoked offline run cannot recreate stage evidence';
    END IF;
  ELSIF TG_TABLE_NAME='sre_live_gold_benchmark_snapshots' THEN
    IF EXISTS (
      SELECT 1 FROM jsonb_array_elements(NEW.manifest) AS src(data)
      JOIN sre_live_source_tombstones t
        ON t.project_id=NEW.project_id
       AND t.content_version_id=(src.data->>'content_version_id')::bigint
    ) THEN
      RAISE EXCEPTION 'M34 revoked source cannot enter gold snapshot';
    END IF;
  END IF;
  RETURN NEW;
END;
$m34_derived$;
CREATE TRIGGER sre_m34_block_live_item
  BEFORE INSERT ON sre_live_items
  FOR EACH ROW EXECUTE FUNCTION sre_m34_block_derived_after_revoke();
CREATE TRIGGER sre_m34_block_offline_item
  BEFORE INSERT ON sre_offline_items
  FOR EACH ROW EXECUTE FUNCTION sre_m34_block_derived_after_revoke();
-- A late S4 worker must not recreate evidence after the same transaction
-- purged its original S4 output and context.
CREATE TRIGGER sre_m34_block_classification_result
  BEFORE INSERT OR UPDATE ON sre_classification_results
  FOR EACH ROW EXECUTE FUNCTION sre_m34_block_derived_after_revoke();
CREATE TRIGGER sre_m34_block_quote
  BEFORE INSERT ON sre_live_opus_quotes
  FOR EACH ROW EXECUTE FUNCTION sre_m34_block_derived_after_revoke();
CREATE TRIGGER sre_m34_block_consent
  BEFORE INSERT ON sre_live_opus_consents
  FOR EACH ROW EXECUTE FUNCTION sre_m34_block_derived_after_revoke();
CREATE TRIGGER sre_m34_block_reservation
  BEFORE INSERT ON sre_live_budget_reservations
  FOR EACH ROW EXECUTE FUNCTION sre_m34_block_derived_after_revoke();
CREATE TRIGGER sre_m34_block_dispatch
  BEFORE INSERT ON sre_live_opus_dispatch_intents
  FOR EACH ROW EXECUTE FUNCTION sre_m34_block_derived_after_revoke();
CREATE TRIGGER sre_m34_block_verified_result
  BEFORE INSERT ON sre_live_verified_pair_results
  FOR EACH ROW EXECUTE FUNCTION sre_m34_block_derived_after_revoke();
CREATE TRIGGER sre_m34_block_human_review
  BEFORE INSERT ON sre_live_human_reviews
  FOR EACH ROW EXECUTE FUNCTION sre_m34_block_derived_after_revoke();
CREATE TRIGGER sre_m34_block_stage_execution
  BEFORE INSERT OR UPDATE ON sre_stage_executions
  FOR EACH ROW EXECUTE FUNCTION sre_m34_block_derived_after_revoke();
CREATE TRIGGER sre_m34_block_gold_snapshot
  BEFORE INSERT ON sre_live_gold_benchmark_snapshots
  FOR EACH ROW EXECUTE FUNCTION sre_m34_block_derived_after_revoke();

REVOKE ALL ON sre_offline_revocation_fences FROM PUBLIC;
REVOKE ALL ON sre_live_source_tombstones FROM PUBLIC;
REVOKE ALL ON sre_live_revocation_outbox FROM PUBLIC;
