-- UA-MOD-16: source-backed revisions, idempotent inbox and transactional outbox.
-- UAE-owned only; 080..086 migration checksums remain unchanged.
-- An inbox row must be inserted only after same-transaction producer DB proof.
-- No raw producer payload, score, transcript or media is copied.
CREATE TABLE uae_revision_inbox (
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE,
  event_id UUID NOT NULL,
  video_id BIGINT NOT NULL,
  source_id TEXT NOT NULL REFERENCES sources(id) ON DELETE CASCADE,
  source_policy_version_id BIGINT NOT NULL,
  producer TEXT NOT NULL CHECK(producer IN
    ('source_policy','qdp','sre','group_a','group_b','group_c')),
  producer_record_id TEXT NOT NULL CHECK(length(producer_record_id) BETWEEN 1 AND 80),
  producer_revision BIGINT NOT NULL CHECK(producer_revision>=0),
  producer_sha256 TEXT NOT NULL CHECK(producer_sha256 ~ '^[0-9a-f]{64}$'),
  event_sha256 TEXT NOT NULL CHECK(event_sha256 ~ '^[0-9a-f]{64}$'),
  observed_at TIMESTAMPTZ NOT NULL,
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(project_id,producer,event_id),
  UNIQUE(project_id,producer,producer_record_id,producer_revision,producer_sha256),
  FOREIGN KEY(project_id,video_id) REFERENCES sre_project_videos(project_id,video_id) ON DELETE CASCADE,
  FOREIGN KEY(source_policy_version_id) REFERENCES source_policy_versions(id)
);
CREATE INDEX uae_revision_inbox_scope_idx ON
  uae_revision_inbox(project_id,video_id,source_id,received_at DESC);

CREATE TABLE uae_revision_quarantine (
  project_id TEXT NOT NULL,
  producer TEXT NOT NULL,
  event_id UUID NOT NULL,
  observed_sha256 TEXT NOT NULL CHECK(observed_sha256 ~ '^[0-9a-f]{64}$'),
  recorded_sha256 TEXT NOT NULL CHECK(recorded_sha256 ~ '^[0-9a-f]{64}$'),
  reason_code TEXT NOT NULL CHECK(reason_code='event_identity_collision'),
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(project_id,producer,event_id,observed_sha256),
  FOREIGN KEY(project_id,producer,event_id)
    REFERENCES uae_revision_inbox(project_id,producer,event_id) ON DELETE CASCADE
);

CREATE TABLE uae_revision_outbox (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL,
  snapshot_id BIGINT NOT NULL,
  invalidation_id UUID NOT NULL,
  event_sha256 TEXT NOT NULL CHECK(event_sha256 ~ '^[0-9a-f]{64}$'),
  event_type TEXT NOT NULL DEFAULT 'uae.snapshot_invalidated/v1'
    CHECK(event_type='uae.snapshot_invalidated/v1'),
  delivery_state TEXT NOT NULL DEFAULT 'pending'
    CHECK(delivery_state IN ('pending','leased','delivered','failed')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts BETWEEN 0 AND 20),
  worker_id TEXT,
  lease_until TIMESTAMPTZ,
  delivered_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id,invalidation_id),
  FOREIGN KEY(project_id,snapshot_id) REFERENCES uae_unified_snapshots(project_id,id) ON DELETE CASCADE,
  FOREIGN KEY(invalidation_id) REFERENCES uae_snapshot_invalidations(id) ON DELETE CASCADE,
  CHECK((worker_id IS NULL)=(lease_until IS NULL)),
  CHECK(lease_until IS NULL OR delivery_state='leased'),
  CHECK(delivery_state<>'delivered' OR delivered_at IS NOT NULL)
);
CREATE INDEX uae_revision_outbox_pending_idx
 ON uae_revision_outbox(project_id,created_at,id)
 WHERE delivery_state IN ('pending','failed');

-- No UPDATE can rewrite accepted producer identity, event timestamp or
-- immutable pending notification ID. Delivery transitions are separate.
CREATE FUNCTION uae_revision_immutable() RETURNS trigger LANGUAGE plpgsql AS $uae_rev$
BEGIN
  RAISE EXCEPTION 'UAE revision evidence is immutable; append a new event'
    USING ERRCODE='23514';
END $uae_rev$;
CREATE TRIGGER uae_revision_inbox_immutable
 BEFORE UPDATE ON uae_revision_inbox
 FOR EACH ROW EXECUTE FUNCTION uae_revision_immutable();
CREATE TRIGGER uae_revision_quarantine_immutable
 BEFORE UPDATE ON uae_revision_quarantine
 FOR EACH ROW EXECUTE FUNCTION uae_revision_immutable();

CREATE FUNCTION uae_revision_outbox_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $uae_out$
BEGIN
  IF (to_jsonb(NEW)-'delivery_state'-'attempts'-'worker_id'-'lease_until'-'delivered_at')
      IS DISTINCT FROM
     (to_jsonb(OLD)-'delivery_state'-'attempts'-'worker_id'-'lease_until'-'delivered_at')
     OR NEW.attempts < OLD.attempts
     OR OLD.delivery_state='delivered'
     OR (OLD.delivery_state='pending' AND NEW.delivery_state NOT IN ('pending','leased'))
     OR (OLD.delivery_state='leased' AND NEW.delivery_state NOT IN ('leased','delivered','failed'))
     OR (OLD.delivery_state='failed' AND NEW.delivery_state NOT IN ('failed','leased'))
     OR (NEW.delivery_state='leased' AND (NEW.worker_id IS NULL OR NEW.lease_until<=now()))
     OR (NEW.delivery_state='delivered' AND
         (OLD.delivery_state<>'leased' OR NEW.worker_id<>OLD.worker_id
          OR OLD.lease_until<=now() OR NEW.delivered_at IS NULL))
  THEN
    RAISE EXCEPTION 'UAE invalid outbox delivery transition'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $uae_out$;
CREATE TRIGGER uae_revision_outbox_identity_guard_trg
 BEFORE UPDATE ON uae_revision_outbox
 FOR EACH ROW EXECUTE FUNCTION uae_revision_outbox_identity_guard();

-- Every outbox receipt must identify an invalidation on the *same* snapshot
-- and project. A reference UUID alone does not prevent cross-scope reuse.
CREATE FUNCTION uae_revision_outbox_invalidation_guard()
 RETURNS trigger LANGUAGE plpgsql AS $uae_outscope$
BEGIN
 IF NOT EXISTS(SELECT 1 FROM uae_snapshot_invalidations i
               WHERE i.id=NEW.invalidation_id
                 AND i.project_id=NEW.project_id
                 AND i.snapshot_id=NEW.snapshot_id) THEN
   RAISE EXCEPTION 'UAE outbox invalidation scope mismatch' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $uae_outscope$;
CREATE TRIGGER uae_revision_outbox_invalidation_guard_trg
 BEFORE INSERT ON uae_revision_outbox
 FOR EACH ROW EXECUTE FUNCTION uae_revision_outbox_invalidation_guard();

-- NOTE: NO GRANT PUBLIC, dispatcher, subscriber, or synthetic reviewed result.
-- Full parent privacy purge cascades child notifications and source evidence.
