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

CREATE FUNCTION sre_m34_revoke_dependents() RETURNS trigger
LANGUAGE plpgsql AS $m34_revoke$
BEGIN
  -- Vectors are disposable; delete them, not immutable audit receipts.
  DELETE FROM sre_local_embedding_cache
    WHERE project_id=NEW.project_id
      AND content_version_id=NEW.content_version_id;
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

REVOKE ALL ON sre_live_source_tombstones FROM PUBLIC;
REVOKE ALL ON sre_live_revocation_outbox FROM PUBLIC;
