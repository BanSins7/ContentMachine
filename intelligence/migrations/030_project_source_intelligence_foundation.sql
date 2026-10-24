-- Project Source Intelligence, stage 01: contracts and data foundation ONLY.
-- Additive to project_registry, project_profile_versions and jobs (001,003).
-- Sources are NOT the global 'sources' table used for Discovery providers.
-- Project-source permissions are DENY by default; this migration starts no jobs.

CREATE TABLE project_sources (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 source_type TEXT NOT NULL CHECK(source_type IN (
   'questionnaire_field','product_website','competitor_website','competitor_account',
   'promoter_account','user_video_reference','research_link','research_document','other')),
 origin_field TEXT CHECK(origin_field IS NULL OR length(origin_field) BETWEEN 1 AND 120),
 original_input TEXT NOT NULL CHECK(length(original_input)<=12000),
 canonical_url TEXT CHECK(canonical_url IS NULL OR length(canonical_url)<=2048),
 platform TEXT CHECK(platform IS NULL OR length(platform)<=40),
 platform_native_id TEXT CHECK(platform_native_id IS NULL OR length(platform_native_id)<=250),
 user_notes TEXT CHECK(user_notes IS NULL OR length(user_notes)<=12000),
 status TEXT NOT NULL DEFAULT 'registered' CHECK(status IN (
   'registered','queued','processing','completed','partial','unavailable','failed','cancelled','needs_review')),
 can_fetch BOOLEAN NOT NULL DEFAULT FALSE,
 can_store BOOLEAN NOT NULL DEFAULT FALSE,
 can_analyze BOOLEAN NOT NULL DEFAULT FALSE,
 can_send_to_llm BOOLEAN NOT NULL DEFAULT FALSE,
 rights_policy JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(rights_policy)='object'),
 active_version_id UUID,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 tombstoned_at TIMESTAMPTZ,
 UNIQUE(project_id,id),
 CHECK(updated_at>=created_at),
 CHECK(tombstoned_at IS NULL OR tombstoned_at>=created_at)
);
CREATE INDEX psi_sources_project ON project_sources(project_id,created_at DESC);
CREATE INDEX psi_sources_active ON project_sources(project_id,source_type) WHERE tombstoned_at IS NULL;

CREATE TABLE project_source_versions (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 source_id UUID NOT NULL,
 version_no INTEGER NOT NULL CHECK(version_no>=1),
 content_sha256 TEXT CHECK(content_sha256 IS NULL OR content_sha256 ~ '^[0-9a-f]{64}$'),
 fetched_or_uploaded_at TIMESTAMPTZ,
 media_type TEXT CHECK(media_type IS NULL OR length(media_type)<=160),
 byte_size BIGINT CHECK(byte_size IS NULL OR byte_size>=0),
 initial_extraction_status TEXT NOT NULL DEFAULT 'registered' CHECK(initial_extraction_status IN (
   'registered','queued','processing','completed','partial','unavailable','failed','cancelled','needs_review')),
 source_locator JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(source_locator)='object'),
 immutable_asset_ref TEXT CHECK(immutable_asset_ref IS NULL OR length(immutable_asset_ref)<=512),
 policy_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(policy_snapshot)='object'),
 recorded_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,id),
 UNIQUE(project_id,source_id,id),
 UNIQUE(project_id,source_id,version_no),
 FOREIGN KEY(project_id,source_id) REFERENCES project_sources(project_id,id),
 CHECK((content_sha256 IS NULL)=(byte_size IS NULL)),
 CHECK(immutable_asset_ref IS NULL OR content_sha256 IS NOT NULL)
);
CREATE INDEX psi_versions_lookup ON project_source_versions(project_id,source_id,version_no DESC);
ALTER TABLE project_sources ADD CONSTRAINT psi_active_version_same_source
 FOREIGN KEY(project_id,id,active_version_id)
 REFERENCES project_source_versions(project_id,source_id,id) DEFERRABLE INITIALLY IMMEDIATE;

CREATE TABLE project_media_assets (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 source_id UUID NOT NULL,
 source_version_id UUID NOT NULL,
 asset_kind TEXT NOT NULL CHECK(asset_kind IN (
   'document','video','audio','frame','subtitle','image','other')),
 content_sha256 TEXT NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
 byte_size BIGINT NOT NULL CHECK(byte_size>=0),
 media_type TEXT NOT NULL CHECK(length(btrim(media_type)) BETWEEN 1 AND 160),
 storage_ref TEXT NOT NULL CHECK(length(btrim(storage_ref)) BETWEEN 1 AND 512),
 rights_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(rights_snapshot)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,id),
 FOREIGN KEY(project_id,source_id,source_version_id)
   REFERENCES project_source_versions(project_id,source_id,id)
);
CREATE INDEX psi_media_by_version ON project_media_assets(project_id,source_version_id);

-- Linked to (but not implicitly leased by) the existing durable jobs table.
-- Stage 01 deliberately does not change jobs.lease_next, which is global.
CREATE TABLE source_extraction_jobs (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 source_id UUID NOT NULL,
 source_version_id UUID NOT NULL,
 stage TEXT NOT NULL CHECK(stage IN (
   'web_fetch','document_parse','video_download','asr','ocr','vision',
   'timeline_fusion','evidence_normalization')),
 state TEXT NOT NULL DEFAULT 'queued' CHECK(state IN (
   'queued','processing','completed','partial','unavailable','failed','cancelled','needs_review')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 max_attempts INTEGER NOT NULL DEFAULT 3 CHECK(max_attempts BETWEEN 1 AND 20),
 idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 1 AND 200),
 global_job_id BIGINT REFERENCES jobs(id),
 lease_until TIMESTAMPTZ,
 cost_estimate_usd NUMERIC(18,8) NOT NULL DEFAULT 0 CHECK(cost_estimate_usd>=0),
 cost_actual_usd NUMERIC(18,8) CHECK(cost_actual_usd IS NULL OR cost_actual_usd>=0),
 charge_state TEXT NOT NULL DEFAULT 'not_applicable' CHECK(charge_state IN (
   'not_applicable','reserved','charged','unknown','refunded')),
 consent_ref TEXT CHECK(consent_ref IS NULL OR length(consent_ref)<=250),
 output_ref TEXT CHECK(output_ref IS NULL OR length(output_ref)<=512),
 error_code TEXT CHECK(error_code IS NULL OR length(error_code)<=120),
 error_message TEXT CHECK(error_message IS NULL OR length(error_message)<=500),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,id),
 UNIQUE(project_id,source_id,source_version_id,id),
 UNIQUE(project_id,idempotency_key),
 UNIQUE(global_job_id),
 FOREIGN KEY(project_id,source_id,source_version_id)
   REFERENCES project_source_versions(project_id,source_id,id),
 CHECK(attempts<=max_attempts),
 CHECK(cost_estimate_usd=0 OR consent_ref IS NOT NULL),
 CHECK(updated_at>=created_at)
);
CREATE INDEX psi_jobs_pending ON source_extraction_jobs(project_id,state,created_at DESC);
CREATE INDEX psi_jobs_version ON source_extraction_jobs(project_id,source_version_id);

CREATE TABLE source_processing_outputs (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 source_id UUID NOT NULL,
 source_version_id UUID NOT NULL,
 extraction_job_id UUID,
 output_type TEXT NOT NULL CHECK(output_type IN (
   'document_segments','web_segments','transcript','visual_scenes','ocr','video_analysis','other')),
 schema_version TEXT NOT NULL CHECK(length(schema_version) BETWEEN 1 AND 80),
 output JSONB NOT NULL CHECK(jsonb_typeof(output)='object'),
 output_sha256 TEXT NOT NULL CHECK(output_sha256 ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,id),
 UNIQUE(project_id,source_id,source_version_id,id),
 FOREIGN KEY(project_id,source_id,source_version_id)
   REFERENCES project_source_versions(project_id,source_id,id),
 FOREIGN KEY(project_id,source_id,source_version_id,extraction_job_id)
   REFERENCES source_extraction_jobs(project_id,source_id,source_version_id,id)
);
CREATE INDEX psi_outputs_version ON source_processing_outputs(project_id,source_version_id,created_at DESC);

CREATE TABLE project_evidence (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 source_id UUID NOT NULL,
 source_version_id UUID NOT NULL,
 processing_output_id UUID,
 evidence_kind TEXT NOT NULL CHECK(evidence_kind IN (
   'user_claim','external_claim','observed_media','model_inference')),
 exact_text_or_observation TEXT NOT NULL CHECK(length(btrim(exact_text_or_observation))>0),
 locator JSONB NOT NULL CHECK(jsonb_typeof(locator)='object'),
 extraction_engine TEXT NOT NULL CHECK(length(btrim(extraction_engine)) BETWEEN 1 AND 250),
 extraction_version TEXT NOT NULL CHECK(length(btrim(extraction_version)) BETWEEN 1 AND 250),
 source_quote TEXT,
 confidence NUMERIC(5,4) CHECK(confidence BETWEEN 0 AND 1),
 quality_status TEXT NOT NULL DEFAULT 'unknown' CHECK(quality_status IN ('unknown','complete','partial','unavailable')),
 review_status TEXT NOT NULL DEFAULT 'pending' CHECK(review_status IN ('pending','needs_review')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,id),
 FOREIGN KEY(project_id,source_id,source_version_id)
   REFERENCES project_source_versions(project_id,source_id,id),
 FOREIGN KEY(project_id,source_id,source_version_id,processing_output_id)
   REFERENCES source_processing_outputs(project_id,source_id,source_version_id,id),
 CHECK(evidence_kind <> 'model_inference' OR review_status='needs_review')
);
CREATE INDEX psi_evidence_version ON project_evidence(project_id,source_version_id,created_at DESC);

CREATE TABLE project_evidence_reviews (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 evidence_id UUID NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('approved','rejected')),
 reviewed_by TEXT NOT NULL CHECK(length(btrim(reviewed_by)) BETWEEN 1 AND 120),
 review_reason TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,evidence_id),
 FOREIGN KEY(project_id,evidence_id) REFERENCES project_evidence(project_id,id)
);

-- The old profile hash remains unchanged. This unique constraint only lets
-- the new context pin a revision AND the exact old hash at the SQL boundary.
ALTER TABLE project_profile_versions
 ADD CONSTRAINT psi_profile_version_hash_key UNIQUE(project_id,revision,profile_hash);

CREATE TABLE project_context_snapshots (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 context_version INTEGER NOT NULL CHECK(context_version>=1),
 context_sha256 TEXT NOT NULL CHECK(context_sha256 ~ '^[0-9a-f]{64}$'),
 profile_revision INTEGER NOT NULL CHECK(profile_revision>=1),
 profile_hash TEXT NOT NULL CHECK(profile_hash ~ '^[0-9a-f]{64}$'),
 schema_version TEXT NOT NULL DEFAULT 'project-context/v1'
  CHECK(schema_version='project-context/v1'),
 context_payload JSONB NOT NULL CHECK(jsonb_typeof(context_payload)='object'),
 source_manifest JSONB NOT NULL CHECK(jsonb_typeof(source_manifest)='array'),
 rights_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(rights_snapshot)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,id),
 UNIQUE(project_id,context_version),
 FOREIGN KEY(project_id,profile_revision,profile_hash)
   REFERENCES project_profile_versions(project_id,revision,profile_hash)
);
CREATE INDEX psi_context_project ON project_context_snapshots(project_id,context_version DESC);

CREATE TABLE project_context_source_pins (
 project_id TEXT NOT NULL,
 context_id UUID NOT NULL,
 source_id UUID NOT NULL,
 source_version_id UUID NOT NULL,
 PRIMARY KEY(project_id,context_id,source_id),
 FOREIGN KEY(project_id,context_id) REFERENCES project_context_snapshots(project_id,id),
 FOREIGN KEY(project_id,source_id,source_version_id)
   REFERENCES project_source_versions(project_id,source_id,id)
);
CREATE TABLE project_context_evidence_pins (
 project_id TEXT NOT NULL,
 context_id UUID NOT NULL,
 evidence_id UUID NOT NULL,
 PRIMARY KEY(project_id,context_id,evidence_id),
 FOREIGN KEY(project_id,context_id) REFERENCES project_context_snapshots(project_id,id),
 FOREIGN KEY(project_id,evidence_id) REFERENCES project_evidence(project_id,id)
);

CREATE TABLE project_context_approvals (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 context_id UUID NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('approved','approved_partial','rejected')),
 approved_by TEXT NOT NULL CHECK(length(btrim(approved_by)) BETWEEN 1 AND 120),
 approval_reason TEXT,
 rights_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(rights_snapshot)='object'),
 approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,context_id),
 FOREIGN KEY(project_id,context_id) REFERENCES project_context_snapshots(project_id,id)
);

-- Versions, outputs, evidence, review decisions and published/candidate context
-- snapshots are append-only. New analysis -> new row, never overwrite history.
CREATE FUNCTION psi_block_immutable_write() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Project Source Intelligence history is immutable'; END $$;
CREATE TRIGGER psi_versions_immutable BEFORE UPDATE OR DELETE ON project_source_versions
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER psi_assets_immutable BEFORE UPDATE OR DELETE ON project_media_assets
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER psi_outputs_immutable BEFORE UPDATE OR DELETE ON source_processing_outputs
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER psi_evidence_immutable BEFORE UPDATE OR DELETE ON project_evidence
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER psi_evidence_review_immutable BEFORE UPDATE OR DELETE ON project_evidence_reviews
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER psi_context_immutable BEFORE UPDATE OR DELETE ON project_context_snapshots
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER psi_context_source_pin_immutable BEFORE UPDATE OR DELETE ON project_context_source_pins
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER psi_context_evidence_pin_immutable BEFORE UPDATE OR DELETE ON project_context_evidence_pins
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER psi_context_approval_immutable BEFORE UPDATE OR DELETE ON project_context_approvals
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
