-- B Stage06: immutable *unsent* facts-v3 request blueprints.
-- No paid attempts or provider replies: those belong to Stage07.
-- B3 references only previously prepared Full/Multi EvidencePacks.

CREATE TABLE knowledge_fact_request_batches (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 bundle_id UUID NOT NULL,
 full_pack_id UUID,
 multi_plan_id UUID,
 model_snapshot_id BIGINT NOT NULL,
 model_id TEXT NOT NULL CHECK(model_id='xiaomi/mimo-v2.6-flash'),
 catalog_sha256 TEXT NOT NULL CHECK(catalog_sha256 ~ '^[0-9a-f]{64}$'),
 prompt_version TEXT NOT NULL DEFAULT 'facts-v3' CHECK(prompt_version='facts-v3'),
 prompt_sha256 TEXT NOT NULL CHECK(prompt_sha256 ~ '^[0-9a-f]{64}$'),
 schema_sha256 TEXT NOT NULL CHECK(schema_sha256 ~ '^[0-9a-f]{64}$'),
 batch_sha256 TEXT NOT NULL CHECK(batch_sha256 ~ '^[0-9a-f]{64}$'),
 request_count INTEGER NOT NULL CHECK(request_count BETWEEN 1 AND 256),
 reserved_estimate_usd NUMERIC(24,12) NOT NULL CHECK(reserved_estimate_usd>0),
 batch JSONB NOT NULL CHECK(jsonb_typeof(batch)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(project_id,id),
 UNIQUE(project_id,id,bundle_id),
 UNIQUE(project_id,full_pack_id,prompt_sha256,schema_sha256),
 UNIQUE(project_id,multi_plan_id,prompt_sha256,schema_sha256),
 FOREIGN KEY(project_id,bundle_id) REFERENCES knowledge_input_bundles(project_id,id),
 FOREIGN KEY(project_id,full_pack_id) REFERENCES knowledge_full_context_packs(project_id,id),
 FOREIGN KEY(project_id,multi_plan_id,bundle_id)
    REFERENCES knowledge_multi_pack_plans(project_id,id,bundle_id),
 FOREIGN KEY(model_snapshot_id,model_id) REFERENCES tb_model_snapshots(id,model_id),
 CHECK ((full_pack_id IS NOT NULL) <> (multi_plan_id IS NOT NULL)),
 CHECK(batch->>'schema_version'='facts-v3-request-batch/v1'),
 CHECK(batch->>'prompt_sha256'=prompt_sha256),
 CHECK(batch->>'schema_sha256'=schema_sha256),
 CHECK(batch->>'request_count'=request_count::text),
 CHECK(batch->>'model_id'=model_id),
 CHECK(batch->>'catalog_sha256'=catalog_sha256)
);
CREATE INDEX ke_fact_requests_by_bundle ON knowledge_fact_request_batches(project_id,bundle_id,created_at DESC);

CREATE TABLE knowledge_fact_request_parts (
 project_id TEXT NOT NULL,
 batch_id UUID NOT NULL,
 bundle_id UUID NOT NULL,
 pack_index INTEGER NOT NULL CHECK(pack_index BETWEEN 0 AND 255),
 request_sha256 TEXT NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
 request_bytes INTEGER NOT NULL CHECK(request_bytes>0 AND request_bytes<=12000000),
 pack_payload_sha256 TEXT NOT NULL CHECK(pack_payload_sha256 ~ '^[0-9a-f]{64}$'),
 pack_manifest_sha256 TEXT NOT NULL CHECK(pack_manifest_sha256 ~ '^[0-9a-f]{64}$'),
 quoted_reservation_usd NUMERIC(24,12) NOT NULL CHECK(quoted_reservation_usd>0),
 source_chunk_count INTEGER NOT NULL CHECK(source_chunk_count BETWEEN 1 AND 100000),
 PRIMARY KEY(project_id,batch_id,pack_index),
 FOREIGN KEY(project_id,batch_id,bundle_id)
     REFERENCES knowledge_fact_request_batches(project_id,id,bundle_id)
);
CREATE TRIGGER ke_fact_request_batch_immutable BEFORE UPDATE OR DELETE ON knowledge_fact_request_batches
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_fact_request_part_immutable BEFORE UPDATE OR DELETE ON knowledge_fact_request_parts
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
