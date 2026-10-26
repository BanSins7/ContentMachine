-- Knowledge Extraction B Stage04: append-only deterministic SINGLE EvidencePack.
-- Metadata-only pack; original text is reopened from B1 immutable A pins.
-- NO LLM execution, NO paid provider consent or inference in this migration.

CREATE TABLE knowledge_full_context_packs (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 bundle_id UUID NOT NULL,
 bundle_sha256 TEXT NOT NULL CHECK(bundle_sha256 ~ '^[0-9a-f]{64}$'),
 model_snapshot_id BIGINT NOT NULL,
 model_id TEXT NOT NULL CHECK (model_id='xiaomi/mimo-v2.6-flash'),
 catalog_sha256 TEXT NOT NULL CHECK(catalog_sha256 ~ '^[0-9a-f]{64}$'),
 config_sha256 TEXT NOT NULL CHECK(config_sha256 ~ '^[0-9a-f]{64}$'),
 manifest_sha256 TEXT NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
 payload_sha256 TEXT NOT NULL CHECK(payload_sha256 ~ '^[0-9a-f]{64}$'),
 fragment_count INTEGER NOT NULL CHECK(fragment_count>=1 AND fragment_count<=50000),
 payload_bytes INTEGER NOT NULL CHECK(payload_bytes BETWEEN 1 AND 8000000),
 manifest JSONB NOT NULL CHECK(jsonb_typeof(manifest)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(project_id,id),
 UNIQUE(project_id,bundle_id,model_snapshot_id,config_sha256),
 FOREIGN KEY (project_id,bundle_id)
   REFERENCES knowledge_input_bundles(project_id,id),
 FOREIGN KEY(model_snapshot_id,model_id)
   REFERENCES tb_model_snapshots(id,model_id),
 CHECK (manifest->>'schema_version'='evidence-pack/v1'),
 CHECK (manifest->>'bundle_sha256'=bundle_sha256),
 CHECK (manifest->>'config_sha256'=config_sha256),
 CHECK (manifest->>'catalog_sha256'=catalog_sha256),
 CHECK (manifest->>'payload_sha256'=payload_sha256),
 CHECK (manifest->>'fragment_count'=fragment_count::text),
 CHECK (manifest->>'payload_bytes'=payload_bytes::text)
);
CREATE INDEX ke_full_packs_by_bundle
 ON knowledge_full_context_packs(project_id,bundle_id,created_at DESC);
CREATE TRIGGER ke_full_pack_immutable
 BEFORE UPDATE OR DELETE ON knowledge_full_context_packs
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
