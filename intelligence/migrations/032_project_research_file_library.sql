-- PSI Stage 03: research file library. Immutable bytes/version metadata live outside
-- PostgreSQL. No permissions/copyright checks and no document parsing/LLM calls.
-- Preserve 030/031 checksums and legacy project.json/research paths.
CREATE TABLE project_research_files (
 id UUID PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 source_id UUID NOT NULL,
 display_name TEXT NOT NULL CHECK (length(btrim(display_name)) BETWEEN 1 AND 110),
 uploaded_by TEXT NOT NULL CHECK (length(btrim(uploaded_by)) BETWEEN 1 AND 120),
 origin TEXT NOT NULL CHECK (origin IN ('upload','legacy')),
 active_version_id UUID,
 archived_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,id),
 UNIQUE(project_id,source_id),
 FOREIGN KEY(project_id,source_id) REFERENCES project_sources(project_id,id),
 CHECK(updated_at>=created_at),
 CHECK(archived_at IS NULL OR archived_at>=created_at)
);
CREATE INDEX psi_research_files_project ON project_research_files(project_id,archived_at,created_at DESC);

-- Pin file-version metadata to the exact underlying content and media source.
-- This prevents a correct project/source ID from being paired with a different
-- source version hash or media asset at the SQL boundary.
ALTER TABLE project_source_versions
 ADD CONSTRAINT psi_research_source_content_unique
 UNIQUE(project_id,source_id,id,content_sha256,byte_size,media_type);
ALTER TABLE project_media_assets
 ADD CONSTRAINT psi_research_asset_identity_unique
 UNIQUE(project_id,source_id,source_version_id,id);

CREATE TABLE project_research_file_versions (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 file_id UUID NOT NULL,
 source_version_id UUID NOT NULL,
 version_no INTEGER NOT NULL CHECK(version_no BETWEEN 1 AND 10000),
 content_sha256 TEXT NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
 byte_size BIGINT NOT NULL CHECK(byte_size BETWEEN 1 AND 10485760),
 mime_type TEXT NOT NULL CHECK(length(btrim(mime_type)) BETWEEN 1 AND 160),
 original_name TEXT NOT NULL CHECK(length(btrim(original_name)) BETWEEN 1 AND 110),
 uploaded_by TEXT NOT NULL CHECK(length(btrim(uploaded_by)) BETWEEN 1 AND 120),
 parser_eligibility TEXT NOT NULL CHECK(parser_eligibility IN ('supported_next_stage','deferred','unsupported')),
 storage_ref TEXT NOT NULL CHECK(length(storage_ref) BETWEEN 12 AND 512),
 media_asset_id UUID NOT NULL,
 origin TEXT NOT NULL CHECK(origin IN ('upload','legacy')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,id),
 UNIQUE(project_id,file_id,id),
 UNIQUE(project_id,file_id,version_no),
 UNIQUE(project_id,file_id,source_version_id),
 UNIQUE(project_id,media_asset_id),
 FOREIGN KEY(project_id,file_id) REFERENCES project_research_files(project_id,id),
 FOREIGN KEY(project_id,file_id,source_version_id,content_sha256,byte_size,mime_type)
    REFERENCES project_source_versions(project_id,source_id,id,content_sha256,byte_size,media_type),
 FOREIGN KEY(project_id,file_id,source_version_id,media_asset_id)
    REFERENCES project_media_assets(project_id,source_id,source_version_id,id),
 CHECK(storage_ref ~ '^research_library/[0-9a-f]{2}/[0-9a-f-]{36}/v[0-9]{5}_[0-9a-f]{64}\.bin$'
    OR (origin='legacy' AND storage_ref ~ '^research/[a-f0-9]{32}\.[a-z0-9]+$'))
);
CREATE INDEX psi_research_versions_by_file ON project_research_file_versions(project_id,file_id,version_no DESC);
CREATE INDEX psi_research_versions_sha ON project_research_file_versions(project_id,content_sha256);
ALTER TABLE project_research_files
 ADD CONSTRAINT psi_research_file_active_version
 FOREIGN KEY(project_id,id,active_version_id)
 REFERENCES project_research_file_versions(project_id,file_id,id)
 DEFERRABLE INITIALLY IMMEDIATE;
CREATE TRIGGER psi_research_versions_immutable BEFORE UPDATE OR DELETE ON project_research_file_versions
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();

-- Uploaded-file identity cannot be transferred to another project/source.
CREATE FUNCTION psi_guard_research_file_identity() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF ROW(NEW.id,NEW.project_id,NEW.source_id,NEW.origin,NEW.created_at,NEW.uploaded_by)
    IS DISTINCT FROM ROW(OLD.id,OLD.project_id,OLD.source_id,OLD.origin,OLD.created_at,OLD.uploaded_by)
 THEN RAISE EXCEPTION 'Research file identity is immutable'; END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER psi_research_file_identity BEFORE UPDATE ON project_research_files
 FOR EACH ROW EXECUTE FUNCTION psi_guard_research_file_identity();
