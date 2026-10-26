-- PSI 04 — document parsing queue metadata and deterministic project-scoped cache.
-- Existing file/source bytes, project profiles and previous migrations stay immutable.
-- No AI or external requests; an explicit user request enqueues a free local job.
CREATE TABLE project_document_extract_cache (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
 parser_version TEXT NOT NULL CHECK (length(parser_version) BETWEEN 1 AND 100),
 config_sha256 TEXT NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
 result_sha256 TEXT NOT NULL CHECK (result_sha256 ~ '^[0-9a-f]{64}$'),
 result JSONB NOT NULL CHECK (jsonb_typeof(result)='object'),
 result_state TEXT NOT NULL CHECK (result_state IN ('completed','partial','unavailable')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,content_sha256,parser_version,config_sha256),
 UNIQUE(project_id,id)
);
CREATE INDEX psi_document_cache_project ON project_document_extract_cache(project_id,created_at DESC);
CREATE TRIGGER psi_document_cache_immutable BEFORE UPDATE OR DELETE ON project_document_extract_cache
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();

CREATE TABLE project_document_extract_requests (
 project_id TEXT NOT NULL,
 job_id UUID NOT NULL,
 file_id UUID NOT NULL,
 file_version_id UUID NOT NULL,
 parser_version TEXT NOT NULL CHECK(length(parser_version) BETWEEN 1 AND 100),
 config JSONB NOT NULL CHECK(jsonb_typeof(config)='object'),
 config_sha256 TEXT NOT NULL CHECK(config_sha256 ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(project_id,job_id),
 UNIQUE(project_id,file_version_id,parser_version,config_sha256),
 FOREIGN KEY(project_id,job_id) REFERENCES source_extraction_jobs(project_id,id),
 FOREIGN KEY(project_id,file_id,file_version_id)
    REFERENCES project_research_file_versions(project_id,file_id,id)
);
CREATE INDEX psi_document_requests_file ON project_document_extract_requests(project_id,file_id,created_at DESC);
CREATE TRIGGER psi_document_request_immutable BEFORE UPDATE OR DELETE ON project_document_extract_requests
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();

CREATE UNIQUE INDEX psi_document_one_job_output ON source_processing_outputs(project_id,extraction_job_id)
 WHERE extraction_job_id IS NOT NULL AND output_type='document_segments';
