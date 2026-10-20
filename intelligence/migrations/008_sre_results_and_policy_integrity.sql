-- Structural integrity: a classification result cannot refer to a taxonomy other
-- than its run's immutable taxonomy snapshot, even when written with raw SQL.
ALTER TABLE sre_classification_runs
  ADD CONSTRAINT sre_runs_project_taxonomy_unique
  UNIQUE(id,project_id,taxonomy_version_id);
ALTER TABLE sre_classification_results
  ADD CONSTRAINT sre_results_run_taxonomy_fkey
  FOREIGN KEY(run_id,project_id,taxonomy_version_id)
  REFERENCES sre_classification_runs(id,project_id,taxonomy_version_id);
ALTER TABLE sre_classification_results
  ADD CONSTRAINT sre_match_needs_evidence
  CHECK(decision<>'match' OR jsonb_array_length(evidence)>0);

-- Read-time authorization/retention remains a service-layer responsibility.
-- At INSERT/UPDATE, a source and its currently-active reviewed policy must
-- explicitly permit storing the supplied content.
CREATE OR REPLACE FUNCTION sre_validate_content_rights() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  rights RECORD;
  asset RECORD;
BEGIN
  IF NEW.source_id IS NULL OR NEW.source_policy_version_id IS NULL THEN
    RAISE EXCEPTION 'SRE content requires D1 source and source policy version';
  END IF;
  SELECT s.can_store_metadata,s.can_analyze_media,s.retention_days,
         s.status,p.source_id AS policy_source,p.is_active
    INTO rights FROM sources s JOIN source_policy_versions p ON p.source_id=s.id
    WHERE s.id=NEW.source_id AND p.id=NEW.source_policy_version_id;
  IF NOT FOUND OR NOT rights.can_store_metadata OR rights.status<>'enabled'
     OR NOT rights.is_active THEN
    RAISE EXCEPTION 'Source and active policy do not permit storing SRE content';
  END IF;
  IF rights.retention_days IS NOT NULL AND
     (NEW.expires_at IS NULL OR NEW.expires_at>NEW.created_at+
       rights.retention_days*interval '1 day') THEN
    RAISE EXCEPTION 'SRE content retention exceeds D1 source policy';
  END IF;
  IF NEW.content ? 'transcript' OR NEW.content ? 'scenes' THEN
    IF NOT rights.can_analyze_media OR NEW.media_asset_id IS NULL THEN
      RAISE EXCEPTION 'Media-derived SRE data requires D1 analysis rights and media asset';
    END IF;
  END IF;
  IF NEW.media_asset_id IS NOT NULL THEN
    SELECT video_id,source_id,permission,expires_at INTO asset
      FROM media_assets WHERE id=NEW.media_asset_id;
    IF NOT FOUND OR asset.video_id<>NEW.video_id OR asset.source_id<>NEW.source_id
       OR asset.permission='metadata_only'
       OR (asset.expires_at IS NOT NULL AND asset.expires_at <= now())
       OR (asset.expires_at IS NOT NULL AND
           (NEW.expires_at IS NULL OR NEW.expires_at>asset.expires_at)) THEN
      RAISE EXCEPTION 'Media asset or retention does not permit SRE analysis';
    END IF;
  END IF;
  RETURN NEW;
END $$;
