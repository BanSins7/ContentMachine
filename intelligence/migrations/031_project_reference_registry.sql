-- PSI Stage 02: additive project-scoped reference identity, explicit legacy origins.
-- No network, LLM, media processing or copyright/rights checks are installed.
-- The legacy v030 rights columns remain dormant for migration compatibility.
ALTER TABLE project_sources
 ADD COLUMN label TEXT CHECK(label IS NULL OR length(label) BETWEEN 1 AND 250),
 ADD COLUMN identity_key TEXT CHECK(identity_key IS NULL OR length(identity_key) BETWEEN 1 AND 2100),
 ADD COLUMN link_kind TEXT CHECK(link_kind IS NULL OR link_kind IN ('website','account','video','shortlink')),
 ADD COLUMN ingestion_mode TEXT NOT NULL DEFAULT 'metadata_only'
    CHECK(ingestion_mode IN ('metadata_only','web_ai','video_reference'));
CREATE UNIQUE INDEX psi_reference_active_identity ON project_sources(project_id,identity_key)
 WHERE tombstoned_at IS NULL AND identity_key IS NOT NULL;
CREATE INDEX psi_reference_kind ON project_sources(project_id,link_kind,created_at DESC);

-- One reference can appear in different historical questionnaire fields.
-- The exact text and origin field survive even if the source is archived.
CREATE TABLE project_source_origins (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 source_id UUID NOT NULL,
 origin_field TEXT NOT NULL CHECK(origin_field IN (
   'product_url','competitors','competitor_socials','promoter_accounts','video_references')),
 original_line TEXT NOT NULL CHECK(length(original_line) BETWEEN 1 AND 12000),
 import_key TEXT NOT NULL CHECK(import_key ~ '^[0-9a-f]{64}$'),
 first_profile_revision INTEGER NOT NULL CHECK(first_profile_revision >= 1),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,import_key),
 FOREIGN KEY(project_id,source_id) REFERENCES project_sources(project_id,id)
);
CREATE INDEX psi_source_origins_ref ON project_source_origins(project_id,source_id);
CREATE TRIGGER psi_source_origins_immutable BEFORE UPDATE OR DELETE ON project_source_origins
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();

-- Protect canonical reference identity even before its first material version.
CREATE OR REPLACE FUNCTION psi_guard_source_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.id,NEW.project_id,NEW.source_type,NEW.origin_field,NEW.original_input,
        NEW.identity_key,NEW.link_kind)
    IS DISTINCT FROM
    ROW(OLD.id,OLD.project_id,OLD.source_type,OLD.origin_field,OLD.original_input,
        OLD.identity_key,OLD.link_kind)
 THEN RAISE EXCEPTION 'Project source identity and original input are immutable'; END IF;
 RETURN NEW;
END $$;
