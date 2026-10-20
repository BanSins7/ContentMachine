-- SRE structure hardening. Additive migration: keep 006 checksum unchanged.
-- This is a scaffold: provider calls, actual UI and HTTP authorization are separate work.
ALTER TABLE sre_facts ADD CONSTRAINT sre_facts_project_identity UNIQUE(id,project_id);
ALTER TABLE sre_taxonomy_categories
  DROP CONSTRAINT IF EXISTS sre_taxonomy_categories_relation_type_check;
ALTER TABLE sre_taxonomy_categories
  ADD CONSTRAINT sre_category_relation_type
    CHECK(relation_type IN ('direct','problem','problem_related','adjacent'));
ALTER TABLE sre_taxonomy_categories
  ADD COLUMN IF NOT EXISTS origin TEXT NOT NULL DEFAULT 'inferred'
    CHECK(origin IN ('explicit','inferred','user_added')),
  ADD COLUMN IF NOT EXISTS independent BOOLEAN NOT NULL DEFAULT FALSE;

-- Typed examples. Illustrative text and verified real-video references are distinct.
CREATE TABLE IF NOT EXISTS sre_category_examples (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL,
  taxonomy_version_id BIGINT NOT NULL,
  category_key UUID NOT NULL,
  polarity TEXT NOT NULL CHECK(polarity IN ('positive','negative')),
  example_kind TEXT NOT NULL CHECK(example_kind IN ('illustrative','verified_video')),
  example_text TEXT NOT NULL CHECK(length(btrim(example_text))>0),
  video_id BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(taxonomy_version_id,project_id,category_key)
    REFERENCES sre_taxonomy_categories(taxonomy_version_id,project_id,category_key) ON DELETE CASCADE,
  FOREIGN KEY(project_id,video_id)
    REFERENCES sre_project_videos(project_id,video_id),
  CHECK ((example_kind='illustrative' AND video_id IS NULL)
      OR (example_kind='verified_video' AND video_id IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_sre_examples_category
  ON sre_category_examples(project_id,taxonomy_version_id,category_key,polarity);

-- Real FK links for JSON-related fact IDs; the DB validates approval and project scope.
CREATE TABLE IF NOT EXISTS sre_category_fact_links (
  project_id TEXT NOT NULL,
  category_id BIGINT NOT NULL,
  fact_id UUID NOT NULL,
  PRIMARY KEY(category_id,fact_id),
  FOREIGN KEY(category_id,project_id)
    REFERENCES sre_taxonomy_categories(id,project_id) ON DELETE CASCADE,
  FOREIGN KEY(fact_id,project_id)
    REFERENCES sre_facts(id,project_id)
);
CREATE INDEX IF NOT EXISTS idx_sre_category_fact_links_fact
  ON sre_category_fact_links(project_id,fact_id);

CREATE OR REPLACE FUNCTION sre_sync_category_fact_links() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  fact_value TEXT;
  fact_uuid UUID;
BEGIN
  IF jsonb_typeof(NEW.related_fact_ids)<>'array' THEN
    RAISE EXCEPTION 'Related facts must be an array';
  END IF;
  IF (NEW.independent AND jsonb_array_length(NEW.related_fact_ids)>0)
    OR (NOT NEW.independent AND jsonb_array_length(NEW.related_fact_ids)=0) THEN
    RAISE EXCEPTION 'Category requires reviewed facts or explicit independent=true';
  END IF;
  DELETE FROM sre_category_fact_links WHERE category_id=NEW.id;
  FOR fact_value IN SELECT jsonb_array_elements_text(NEW.related_fact_ids)
  LOOP
    fact_uuid := fact_value::uuid;
    IF NOT EXISTS (
      SELECT 1 FROM sre_facts WHERE id=fact_uuid AND project_id=NEW.project_id
      AND review_status='approved'
    ) THEN
      RAISE EXCEPTION 'Category fact link is not approved in this project';
    END IF;
    INSERT INTO sre_category_fact_links(project_id,category_id,fact_id)
      VALUES(NEW.project_id,NEW.id,fact_uuid);
  END LOOP;
  RETURN NEW;
END $$;
CREATE TRIGGER sre_category_sync_fact_links
  AFTER INSERT OR UPDATE OF related_fact_ids,independent ON sre_taxonomy_categories
  FOR EACH ROW EXECUTE FUNCTION sre_sync_category_fact_links();

-- Non-owner changes to FK links/examples must not bypass approved taxonomy immutability.
CREATE OR REPLACE FUNCTION sre_guard_approved_children() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  version_id BIGINT;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF TG_TABLE_NAME='sre_category_examples' THEN version_id:=NEW.taxonomy_version_id;
    ELSE SELECT taxonomy_version_id INTO version_id
      FROM sre_taxonomy_categories WHERE id=NEW.category_id;
    END IF;
  ELSE
    IF TG_TABLE_NAME='sre_category_examples' THEN version_id:=OLD.taxonomy_version_id;
    ELSE SELECT taxonomy_version_id INTO version_id
      FROM sre_taxonomy_categories WHERE id=OLD.category_id;
    END IF;
  END IF;
  IF EXISTS (SELECT 1 FROM sre_taxonomy_versions WHERE id=version_id AND status='approved') THEN
    RAISE EXCEPTION 'Approved taxonomy children are immutable';
  END IF;
  RETURN COALESCE(NEW,OLD);
END $$;
CREATE TRIGGER sre_example_immutable
  BEFORE INSERT OR UPDATE OR DELETE ON sre_category_examples
  FOR EACH ROW EXECUTE FUNCTION sre_guard_approved_children();
CREATE TRIGGER sre_fact_links_immutable
  BEFORE INSERT OR UPDATE OR DELETE ON sre_category_fact_links
  FOR EACH ROW EXECUTE FUNCTION sre_guard_approved_children();

-- Protect approved facts already cited by an approved version from being revoked.
CREATE OR REPLACE FUNCTION sre_guard_cited_fact() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP IN ('UPDATE','DELETE') AND EXISTS (
    SELECT 1 FROM sre_category_fact_links l
    JOIN sre_taxonomy_categories c ON c.id=l.category_id
    JOIN sre_taxonomy_versions v ON v.id=c.taxonomy_version_id
    WHERE l.fact_id=OLD.id AND v.status='approved'
  ) THEN
    RAISE EXCEPTION 'Fact cited in approved taxonomy is immutable';
  END IF;
  RETURN COALESCE(NEW,OLD);
END $$;
CREATE TRIGGER sre_cited_fact_immutable BEFORE UPDATE OR DELETE ON sre_facts
  FOR EACH ROW EXECUTE FUNCTION sre_guard_cited_fact();

-- Data origin and retention cannot be silently dropped from content versions.
ALTER TABLE sre_content_versions
  ADD COLUMN IF NOT EXISTS source_id TEXT REFERENCES sources(id),
  ADD COLUMN IF NOT EXISTS source_policy_version_id BIGINT REFERENCES source_policy_versions(id),
  ADD COLUMN IF NOT EXISTS media_asset_id BIGINT REFERENCES media_assets(id),
  ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ;
CREATE INDEX IF NOT EXISTS idx_sre_content_expiry ON sre_content_versions(expires_at)
  WHERE expires_at IS NOT NULL;

CREATE OR REPLACE FUNCTION sre_validate_content_rights() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  rights RECORD;
  asset RECORD;
BEGIN
  IF NEW.source_id IS NULL OR NEW.source_policy_version_id IS NULL THEN
    RAISE EXCEPTION 'SRE content requires D1 source and source policy version';
  END IF;
  SELECT s.can_store_metadata,s.can_analyze_media,s.retention_days,p.source_id AS policy_source
    INTO rights FROM sources s JOIN source_policy_versions p ON p.source_id=s.id
    WHERE s.id=NEW.source_id AND p.id=NEW.source_policy_version_id;
  IF NOT FOUND OR NOT rights.can_store_metadata OR rights.policy_source<>NEW.source_id THEN
    RAISE EXCEPTION 'Source does not permit metadata storage';
  END IF;
  IF rights.retention_days IS NOT NULL AND
     (NEW.expires_at IS NULL OR NEW.expires_at>NEW.created_at+
       rights.retention_days*interval '1 day') THEN
    RAISE EXCEPTION 'Content retention exceeds D1 source policy';
  END IF;
  IF NEW.content ? 'transcript' OR NEW.content ? 'scenes' THEN
    IF NOT rights.can_analyze_media OR NEW.media_asset_id IS NULL THEN
      RAISE EXCEPTION 'Media-derived SRE data requires source analysis rights and D1 media asset';
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
      RAISE EXCEPTION 'Invalid media asset provenance or permission';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER sre_content_rights_guard
  BEFORE INSERT OR UPDATE ON sre_content_versions
  FOR EACH ROW EXECUTE FUNCTION sre_validate_content_rights();

-- Config snapshots are append-only. A new version is required for any change.
CREATE OR REPLACE FUNCTION sre_guard_pipeline_config() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Saved pipeline configuration is immutable; create a new version';
END $$;
CREATE TRIGGER sre_immutable_pipeline_config
  BEFORE UPDATE OR DELETE ON sre_pipeline_configs
  FOR EACH ROW EXECUTE FUNCTION sre_guard_pipeline_config();

-- Each run pins an immutable pipeline, taxonomy and input content revision;
-- the existing run/results tables record this lineage without altering D1.
