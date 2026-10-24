-- PSI Stage 10: grounded, independently cited evidence. Additive; no rewrite of 030-038.
-- project_evidence is the canonical immutable record; every occurrence owns an
-- independent evidence_id, even when quotes are byte-identical across sources.
CREATE TABLE project_evidence_exact_groups (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 evidence_kind TEXT NOT NULL CHECK(evidence_kind IN ('user_claim','external_claim','observed_media','model_inference')),
 knowledge_scope TEXT NOT NULL CHECK(knowledge_scope IN ('operational_product','competitor_research','creative_reference','general_research')),
 normalized_quote_sha256 TEXT NOT NULL CHECK(normalized_quote_sha256 ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(project_id,id),
 UNIQUE(project_id,evidence_kind,knowledge_scope,normalized_quote_sha256)
);
CREATE TABLE project_evidence_normalization_runs (
 project_id TEXT NOT NULL,
 job_id UUID NOT NULL,
 source_id UUID NOT NULL,
 source_version_id UUID NOT NULL,
 input_output_id UUID,
 input_profile_revision INTEGER,
 input_profile_hash TEXT CHECK(input_profile_hash IS NULL OR input_profile_hash ~ '^[0-9a-f]{64}$'),
 input_field_path TEXT CHECK(input_field_path IS NULL OR length(input_field_path) BETWEEN 1 AND 250),
 input_sha256 TEXT NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
 result_output_id UUID,
 evidence_count INTEGER NOT NULL DEFAULT 0 CHECK(evidence_count BETWEEN 0 AND 450),
 rejected_count INTEGER NOT NULL DEFAULT 0 CHECK(rejected_count BETWEEN 0 AND 450),
 finished_at TIMESTAMPTZ,
 PRIMARY KEY(project_id,job_id),
 UNIQUE NULLS NOT DISTINCT (project_id,source_id,source_version_id,input_output_id,input_profile_revision,input_field_path),
 FOREIGN KEY(project_id,source_id,source_version_id,job_id) REFERENCES source_extraction_jobs(project_id,source_id,source_version_id,id),
 FOREIGN KEY(project_id,source_id,source_version_id,input_output_id) REFERENCES source_processing_outputs(project_id,source_id,source_version_id,id),
 FOREIGN KEY(project_id,source_id,source_version_id,result_output_id) REFERENCES source_processing_outputs(project_id,source_id,source_version_id,id),
 FOREIGN KEY(project_id,input_profile_revision,input_profile_hash)
    REFERENCES project_profile_versions(project_id,revision,profile_hash),
 CHECK ((input_output_id IS NOT NULL AND input_profile_revision IS NULL AND input_profile_hash IS NULL AND input_field_path IS NULL)
     OR (input_output_id IS NULL AND input_profile_revision IS NOT NULL AND input_profile_hash IS NOT NULL AND input_field_path IS NOT NULL))
);
CREATE INDEX psi_evidence_runs_source ON project_evidence_normalization_runs(project_id,source_id,source_version_id);
CREATE TABLE project_evidence_grounding (
 project_id TEXT NOT NULL,
 evidence_id UUID NOT NULL,
 source_id UUID NOT NULL,
 source_version_id UUID NOT NULL,
 input_output_id UUID,
 profile_revision INTEGER,
 profile_hash TEXT,
 quote_sha256 TEXT NOT NULL CHECK(quote_sha256 ~ '^[0-9a-f]{64}$'),
 normalized_quote_sha256 TEXT NOT NULL CHECK(normalized_quote_sha256 ~ '^[0-9a-f]{64}$'),
 locator_sha256 TEXT NOT NULL CHECK(locator_sha256 ~ '^[0-9a-f]{64}$'),
 input_sha256 TEXT NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
 exact_group_id UUID NOT NULL,
 knowledge_scope TEXT NOT NULL CHECK(knowledge_scope IN ('operational_product','competitor_research','creative_reference','general_research')),
 trust_label TEXT NOT NULL CHECK(length(trust_label) BETWEEN 4 AND 90),
 claim_key TEXT CHECK(claim_key IS NULL OR length(claim_key) BETWEEN 1 AND 250),
 typed_value JSONB CHECK(typed_value IS NULL OR jsonb_typeof(typed_value)='object'),
 metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(metadata)='object'),
 citation TEXT NOT NULL CHECK(length(citation) BETWEEN 50 AND 350),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(project_id,evidence_id),
 UNIQUE NULLS NOT DISTINCT (project_id,source_id,source_version_id,input_output_id,locator_sha256,quote_sha256),
 FOREIGN KEY(project_id,evidence_id) REFERENCES project_evidence(project_id,id),
 FOREIGN KEY(project_id,source_id,source_version_id) REFERENCES project_source_versions(project_id,source_id,id),
 FOREIGN KEY(project_id,source_id,source_version_id,input_output_id) REFERENCES source_processing_outputs(project_id,source_id,source_version_id,id),
 FOREIGN KEY(project_id,profile_revision,profile_hash) REFERENCES project_profile_versions(project_id,revision,profile_hash),
 FOREIGN KEY(project_id,exact_group_id) REFERENCES project_evidence_exact_groups(project_id,id),
 CHECK((input_output_id IS NULL)=(profile_revision IS NOT NULL)),
 CHECK((profile_revision IS NULL)=(profile_hash IS NULL))
);
CREATE INDEX psi_evidence_grounding_group ON project_evidence_grounding(project_id,exact_group_id);
CREATE INDEX psi_evidence_grounding_claim ON project_evidence_grounding(project_id,source_id,claim_key) WHERE claim_key IS NOT NULL;
CREATE TABLE project_evidence_relationships (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 evidence_id_a UUID NOT NULL,
 evidence_id_b UUID NOT NULL,
 relation TEXT NOT NULL CHECK(relation IN ('lexical_duplicate_candidate','possible_conflict')),
 review_state TEXT NOT NULL DEFAULT 'needs_review' CHECK(review_state='needs_review'),
 score NUMERIC(6,5) CHECK(score IS NULL OR score BETWEEN 0 AND 1),
 reason TEXT NOT NULL CHECK(length(reason) BETWEEN 5 AND 250),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(project_id,evidence_id_a,evidence_id_b,relation),
 FOREIGN KEY(project_id,evidence_id_a) REFERENCES project_evidence(project_id,id),
 FOREIGN KEY(project_id,evidence_id_b) REFERENCES project_evidence(project_id,id),
 CHECK(evidence_id_a<evidence_id_b)
);
CREATE INDEX psi_evidence_rel_source ON project_evidence_relationships(project_id,evidence_id_a);
CREATE TRIGGER psi_evidence_exact_group_immutable BEFORE UPDATE OR DELETE ON project_evidence_exact_groups FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER psi_evidence_grounding_immutable BEFORE UPDATE OR DELETE ON project_evidence_grounding FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER psi_evidence_relationship_immutable BEFORE UPDATE OR DELETE ON project_evidence_relationships FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
