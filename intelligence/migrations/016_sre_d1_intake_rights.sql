-- S3: additive D1-to-SRE content intake, immutable revision identity and rights.
-- Existing migrations remain immutable. Old D1 manual-import policies have no
-- semantic classification grant and therefore remain denied by default.
ALTER TABLE sre_content_versions
  ADD COLUMN IF NOT EXISTS content_sha256 TEXT,
  ADD COLUMN IF NOT EXISTS intake_fingerprint TEXT,
  ADD COLUMN IF NOT EXISTS metadata_observation_id BIGINT
    REFERENCES source_video_metadata(id);
ALTER TABLE sre_content_versions
  ADD CONSTRAINT sre_s3_content_sha_check
  CHECK (content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'),
  ADD CONSTRAINT sre_s3_intake_fingerprint_check
  CHECK (intake_fingerprint IS NULL OR intake_fingerprint ~ '^[0-9a-f]{64}$');
CREATE UNIQUE INDEX sre_s3_unique_revision_key
  ON sre_content_versions(project_id,video_id,intake_fingerprint)
  WHERE intake_fingerprint IS NOT NULL;
CREATE INDEX sre_s3_content_observation_lookup
  ON sre_content_versions(metadata_observation_id)
  WHERE metadata_observation_id IS NOT NULL;

CREATE TABLE sre_project_discoveries (
  project_id TEXT NOT NULL,
  video_id BIGINT NOT NULL,
  discovery_id BIGINT NOT NULL REFERENCES discoveries(id),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(project_id, discovery_id),
  FOREIGN KEY(project_id,video_id)
    REFERENCES sre_project_videos(project_id,video_id) ON DELETE CASCADE
);
CREATE INDEX sre_project_discoveries_video
  ON sre_project_discoveries(project_id,video_id,discovery_id);

-- Reject direct SQL insertion that bypasses the new semantic policy gate.
-- The service also checks at every read: no immutable row implies permanent
-- permission to reuse or transmit its contents.
CREATE OR REPLACE FUNCTION sre_validate_content_rights() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  rights RECORD;
  asset RECORD;
  observation RECORD;
  policy_retention INTEGER;
BEGIN
  IF NEW.source_id IS NULL OR NEW.source_policy_version_id IS NULL THEN
    RAISE EXCEPTION 'SRE content requires source and active policy';
  END IF;
  SELECT s.can_store_metadata,s.can_analyze_media,s.retention_days,s.status,
         p.source_id AS policy_source,p.is_active,p.reviewed_at,
         p.permissions,p.retention_policy
    INTO rights FROM sources s
    JOIN source_policy_versions p ON p.source_id=s.id
    WHERE s.id=NEW.source_id AND p.id=NEW.source_policy_version_id;
  IF NOT FOUND OR rights.status<>'enabled'
      OR NOT rights.can_store_metadata OR NOT rights.is_active
      OR rights.reviewed_at IS NULL
      OR rights.permissions->'can_store_metadata' IS DISTINCT FROM 'true'::jsonb
      OR rights.permissions->'can_semantic_classify' IS DISTINCT FROM 'true'::jsonb THEN
    RAISE EXCEPTION 'D1 source policy does not authorize SRE semantic storage';
  END IF;
  IF NEW.metadata_observation_id IS NOT NULL THEN
    SELECT video_id,source_id,source_policy_version_id,is_current,
           expires_at,deleted_at INTO observation
    FROM source_video_metadata WHERE id=NEW.metadata_observation_id;
    IF NOT FOUND OR observation.video_id<>NEW.video_id
        OR observation.source_id<>NEW.source_id
        OR observation.source_policy_version_id<>NEW.source_policy_version_id
        OR NOT observation.is_current OR observation.deleted_at IS NOT NULL
        OR (observation.expires_at IS NOT NULL AND observation.expires_at<=now())
        OR (observation.expires_at IS NOT NULL
             AND (NEW.expires_at IS NULL OR NEW.expires_at>observation.expires_at)) THEN
      RAISE EXCEPTION 'D1 metadata observation is not usable by SRE';
    END IF;
  END IF;
  IF NEW.intake_fingerprint IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM sre_project_discoveries d
    WHERE d.project_id=NEW.project_id AND d.video_id=NEW.video_id
    AND d.discovery_id=(NEW.provenance->>'discovery_id')::bigint
    AND EXISTS (
      SELECT 1 FROM discoveries raw WHERE raw.id=d.discovery_id
      AND raw.video_id=NEW.video_id AND raw.source_id=NEW.source_id)
  ) THEN
    RAISE EXCEPTION 'SRE intake requires a verified project discovery';
  END IF;
  IF rights.retention_days IS NOT NULL AND
     (NEW.expires_at IS NULL OR NEW.expires_at>NEW.created_at+
      rights.retention_days * interval '1 day') THEN
    RAISE EXCEPTION 'SRE retention exceeds source policy';
  END IF;
  IF rights.retention_policy ? 'retention_days'
    AND jsonb_typeof(rights.retention_policy->'retention_days')='number' THEN
    policy_retention := (rights.retention_policy->>'retention_days')::integer;
    IF policy_retention<=0 OR NEW.expires_at IS NULL
       OR NEW.expires_at>NEW.created_at+policy_retention * interval '1 day' THEN
      RAISE EXCEPTION 'SRE retention exceeds reviewed policy';
    END IF;
  END IF;
  IF NEW.content ? 'transcript' OR NEW.content ? 'scenes' THEN
    IF NOT rights.can_analyze_media
       OR rights.permissions->'can_analyze_media' IS DISTINCT FROM 'true'::jsonb
       OR NEW.media_asset_id IS NULL THEN
       RAISE EXCEPTION 'Media content requires explicitly reviewed media rights';
    END IF;
  END IF;
  IF NEW.media_asset_id IS NOT NULL THEN
    SELECT video_id,source_id,permission,expires_at INTO asset
    FROM media_assets WHERE id=NEW.media_asset_id;
    IF NOT FOUND OR asset.video_id<>NEW.video_id OR asset.source_id<>NEW.source_id
       OR asset.permission='metadata_only'
       OR (asset.expires_at IS NOT NULL AND asset.expires_at<=now())
       OR (asset.expires_at IS NOT NULL AND
           (NEW.expires_at IS NULL OR NEW.expires_at>asset.expires_at)) THEN
      RAISE EXCEPTION 'SRE media asset provenance is insufficient';
    END IF;
  END IF;
  RETURN NEW;
END $$;

-- Additional read-time restrictions, including dynamic revocation of source,
-- reviewed policy, metadata observation, retention and media rights.
-- Replaces the old weaker view without modifying the original migration.
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
       AND (m.expires_at IS NULL OR m.expires_at>now())));

-- Old stored content is still subject to the strengthened read view.
-- Source-rights revocation becomes effective without a worker or HTTP call.
