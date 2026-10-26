-- Knowledge Extraction B Stage03: immutable source/context-grounded input inventory.
-- Append only after A 041. No modification to A context/RTB or historical SQL.
-- No copyrighted-material permission gate. SQL rejects cross-project pin swapping.

CREATE TABLE knowledge_input_bundles (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 context_id UUID NOT NULL,
 context_sha256 TEXT NOT NULL CHECK (context_sha256 ~ '^[0-9a-f]{64}$'),
 config_sha256 TEXT NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
 bundle_sha256 TEXT NOT NULL CHECK (bundle_sha256 ~ '^[0-9a-f]{64}$'),
 schema_version TEXT NOT NULL DEFAULT 'knowledge-input-bundle/v1'
   CHECK (schema_version='knowledge-input-bundle/v1'),
 readiness TEXT NOT NULL CHECK (readiness IN ('ready','approved_partial','video_only','blocked')),
 bundle JSONB NOT NULL CHECK (jsonb_typeof(bundle)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(project_id,id),
 UNIQUE(project_id,id,context_id),
 UNIQUE(project_id,context_id,config_sha256),
 FOREIGN KEY (project_id,context_id) REFERENCES project_context_snapshots(project_id,id),
 CHECK (bundle->>'schema_version'='knowledge-input-bundle/v1'),
 CHECK (bundle->>'context_sha256'=context_sha256),
 CHECK (bundle->>'config_sha256'=config_sha256),
 CHECK (bundle->>'readiness'=readiness)
);
CREATE INDEX ke_bundles_by_context ON knowledge_input_bundles(project_id,context_id,created_at DESC);

CREATE TABLE knowledge_input_fragments (
 project_id TEXT NOT NULL,
 bundle_id UUID NOT NULL,
 context_id UUID NOT NULL,
 fragment_id TEXT NOT NULL CHECK (fragment_id ~ '^[0-9a-f]{64}$'),
 origin_kind TEXT NOT NULL CHECK (origin_kind IN
  ('context_profile','questionnaire_source','document_segment','web_page')),
 source_id UUID,
 source_version_id UUID,
 output_id UUID,
 profile_revision INTEGER,
 profile_hash TEXT CHECK (profile_hash IS NULL OR profile_hash ~ '^[0-9a-f]{64}$'),
 field_path TEXT,
 segment_index INTEGER NOT NULL CHECK (segment_index>=0),
 text_sha256 TEXT NOT NULL CHECK (text_sha256 ~ '^[0-9a-f]{64}$'),
 char_count INTEGER NOT NULL CHECK (char_count>=1),
 locator JSONB NOT NULL CHECK (jsonb_typeof(locator)='object'),
 source_role TEXT NOT NULL CHECK (source_role IN
  ('own_product','audience_research','competitor','creative_reference','other')),
 subject_scope TEXT NOT NULL CHECK (subject_scope IN
  ('own_product','audience_research','competitor','creative_reference','other')),
 subject_id TEXT CHECK (subject_id IS NULL OR length(subject_id) BETWEEN 1 AND 128),
 trust_label TEXT NOT NULL CHECK (length(trust_label) BETWEEN 1 AND 100),
 language TEXT CHECK (language IS NULL OR length(language) BETWEEN 2 AND 40),
 required BOOLEAN NOT NULL DEFAULT TRUE,
 PRIMARY KEY(project_id,bundle_id,fragment_id),
 UNIQUE(project_id,bundle_id,origin_kind,source_id,output_id,field_path,segment_index),
 FOREIGN KEY(project_id,bundle_id,context_id)
   REFERENCES knowledge_input_bundles(project_id,id,context_id),
 FOREIGN KEY(project_id,context_id,source_id,source_version_id)
   REFERENCES project_context_source_pins(project_id,context_id,source_id,source_version_id),
 FOREIGN KEY(project_id,context_id,output_id)
   REFERENCES project_context_analysis_pins(project_id,context_id,output_id),
 FOREIGN KEY(project_id,profile_revision,profile_hash)
   REFERENCES project_profile_versions(project_id,revision,profile_hash),
 CHECK (
  (origin_kind='context_profile'
   AND source_id IS NULL AND source_version_id IS NULL AND output_id IS NULL
   AND field_path IS NOT NULL AND profile_revision IS NOT NULL AND profile_hash IS NOT NULL)
  OR
  (origin_kind='questionnaire_source'
   AND source_id IS NOT NULL AND source_version_id IS NOT NULL AND output_id IS NULL
   AND field_path IS NOT NULL AND profile_revision IS NOT NULL AND profile_hash IS NOT NULL)
  OR
  (origin_kind IN ('document_segment','web_page')
   AND source_id IS NOT NULL AND source_version_id IS NOT NULL AND output_id IS NOT NULL
   AND field_path IS NULL AND profile_revision IS NULL AND profile_hash IS NULL)
 )
);
CREATE INDEX ke_fragments_by_source ON knowledge_input_fragments(project_id,context_id,source_id,source_version_id);
CREATE INDEX ke_fragments_by_output ON knowledge_input_fragments(project_id,context_id,output_id);

CREATE TABLE knowledge_input_video_refs (
 project_id TEXT NOT NULL,
 bundle_id UUID NOT NULL,
 context_id UUID NOT NULL,
 source_id UUID NOT NULL,
 source_version_id UUID NOT NULL,
 output_id UUID NOT NULL,
 output_sha256 TEXT NOT NULL CHECK (output_sha256 ~ '^[0-9a-f]{64}$'),
 schema_version TEXT NOT NULL CHECK (schema_version='video-intelligence/v1'),
 PRIMARY KEY(project_id,bundle_id,output_id),
 FOREIGN KEY(project_id,bundle_id,context_id)
   REFERENCES knowledge_input_bundles(project_id,id,context_id),
 FOREIGN KEY(project_id,context_id,source_id,source_version_id)
   REFERENCES project_context_source_pins(project_id,context_id,source_id,source_version_id),
 FOREIGN KEY(project_id,context_id,output_id)
   REFERENCES project_context_analysis_pins(project_id,context_id,output_id),
 FOREIGN KEY(project_id,source_id,source_version_id,output_id)
   REFERENCES source_processing_outputs(project_id,source_id,source_version_id,id)
);
CREATE INDEX ke_video_refs_by_context ON knowledge_input_video_refs(project_id,context_id);

CREATE TRIGGER ke_bundle_immutable BEFORE UPDATE OR DELETE ON knowledge_input_bundles
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_fragment_immutable BEFORE UPDATE OR DELETE ON knowledge_input_fragments
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_video_ref_immutable BEFORE UPDATE OR DELETE ON knowledge_input_video_refs
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
