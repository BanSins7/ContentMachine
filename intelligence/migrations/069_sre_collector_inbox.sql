-- M35 optional, authenticated-host Collector -> SRE idempotent ACK/quarantine.
-- No Collector tables, consumer worker or external service are required.
-- Versioned immutable event receipts hold only reference IDs / digests.
-- A self-hash is NOT authentication. Trusted host must authorize the producer.
CREATE TABLE sre_collector_inbox (
  event_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES sre_workspaces(project_id),
  event_sha256 TEXT NOT NULL CHECK(event_sha256 ~ '^[0-9a-f]{64}$'),
  publication_id UUID NOT NULL,
  discovery_id BIGINT NOT NULL CHECK(discovery_id > 0),
  video_id BIGINT NOT NULL CHECK(video_id > 0),
  source_id TEXT NOT NULL CHECK(source_id ~ '^[a-z][a-z0-9_.-]{0,79}$'),
  source_policy_version_id BIGINT NOT NULL CHECK(source_policy_version_id > 0),
  source_request_id BIGINT CHECK(source_request_id > 0),
  occurred_at TIMESTAMPTZ NOT NULL,
  d1_persisted_at TIMESTAMPTZ NOT NULL,
  state TEXT NOT NULL CHECK(state IN ('acked','quarantined')),
  content_version_id BIGINT CHECK(content_version_id > 0),
  content_sha256 TEXT CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
  quarantine_reason TEXT CHECK(quarantine_reason IN (
    'rights_denied','rights_expired','missing_provenance',
    'snapshot_mismatch','invalid_contract'
  )),
  actor_id TEXT NOT NULL CHECK(actor_id ~ '^[A-Za-z0-9:_@.-]{1,128}$'),
  received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  CHECK(d1_persisted_at <= occurred_at),
  CHECK (
    (state='acked' AND content_version_id IS NOT NULL
     AND content_sha256 IS NOT NULL AND quarantine_reason IS NULL)
    OR
    (state='quarantined' AND content_version_id IS NULL
     AND content_sha256 IS NULL AND quarantine_reason IS NOT NULL)
  )
);
CREATE INDEX sre_m35_inbox_project_discovery
  ON sre_collector_inbox(project_id,discovery_id,received_at);
CREATE INDEX sre_m35_inbox_quarantine
  ON sre_collector_inbox(project_id,received_at)
  WHERE state='quarantined';

-- SQL safety backstop: even a direct INSERT of an ACK cannot invent a
-- project-owned S3 revision, approved C9 mirror, or real source/request pin.
-- Historical ACK remains after M34 permitted primary S3 logical deletion;
-- it never itself grants current content readability or rights.
CREATE FUNCTION sre_m35_guard_inbox() RETURNS trigger
LANGUAGE plpgsql AS $m35_inbox$
BEGIN
  IF TG_OP <> 'INSERT' THEN
    RAISE EXCEPTION 'M35 inbox receipts are immutable';
  END IF;
  PERFORM 1 FROM sre_workspaces WHERE project_id=NEW.project_id FOR SHARE;
  IF NOT FOUND THEN
    RAISE EXCEPTION 'M35 project workspace missing';
  END IF;
  IF NEW.state='acked' AND NOT EXISTS (
    SELECT 1
    FROM sre_content_versions c
    JOIN sre_project_discoveries pd
      ON pd.project_id=c.project_id AND pd.video_id=c.video_id
      AND pd.discovery_id=NEW.discovery_id
    JOIN discoveries d
      ON d.id=pd.discovery_id AND d.video_id=c.video_id
      AND d.source_id=c.source_id
    JOIN source_policy_versions p
      ON p.id=c.source_policy_version_id AND p.source_id=c.source_id
    JOIN tb_sre_taxonomy_links l
      ON l.project_id=c.project_id
      AND l.builder_publication_id=NEW.publication_id
    WHERE c.project_id=NEW.project_id
      AND c.id=NEW.content_version_id
      AND c.video_id=NEW.video_id
      AND c.source_id=NEW.source_id
      AND c.source_policy_version_id=NEW.source_policy_version_id
      AND c.content_sha256=NEW.content_sha256
      AND d.source_request_id IS NOT DISTINCT FROM NEW.source_request_id
      AND c.provenance->>'discovery_id'=NEW.discovery_id::text
  ) THEN
    RAISE EXCEPTION 'M35 ACK requires exact project/C9/D1/S3 pin';
  END IF;
  RETURN NEW;
END
$m35_inbox$;

CREATE TRIGGER sre_m35_inbox_integrity
  BEFORE INSERT OR UPDATE OR DELETE ON sre_collector_inbox
  FOR EACH ROW EXECUTE FUNCTION sre_m35_guard_inbox();

REVOKE ALL ON TABLE sre_collector_inbox FROM PUBLIC;
REVOKE ALL ON FUNCTION sre_m35_guard_inbox() FROM PUBLIC;
