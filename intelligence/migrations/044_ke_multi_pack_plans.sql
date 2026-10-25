-- B Stage05 Multi-Pack: full original-fragment coverage, exact byte spans.
-- Append-only metadata; original source text remains in A, not in SQL packs.
-- Reuses existing verified model snapshot. No paid execution/consent here.

CREATE TABLE knowledge_multi_pack_plans (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 bundle_id UUID NOT NULL,
 bundle_sha256 TEXT NOT NULL CHECK(bundle_sha256 ~ '^[0-9a-f]{64}$'),
 model_snapshot_id BIGINT NOT NULL,
 model_id TEXT NOT NULL CHECK(model_id='xiaomi/mimo-v2.6-flash'),
 catalog_sha256 TEXT NOT NULL CHECK(catalog_sha256 ~ '^[0-9a-f]{64}$'),
 pricing_sha256 TEXT NOT NULL CHECK(pricing_sha256 ~ '^[0-9a-f]{64}$'),
 config_sha256 TEXT NOT NULL CHECK(config_sha256 ~ '^[0-9a-f]{64}$'),
 plan_sha256 TEXT NOT NULL CHECK(plan_sha256 ~ '^[0-9a-f]{64}$'),
 pack_count INTEGER NOT NULL CHECK(pack_count BETWEEN 2 AND 256),
 span_count INTEGER NOT NULL CHECK(span_count BETWEEN 2 AND 100000),
 estimated_usd NUMERIC(18,8) NOT NULL CHECK(estimated_usd>=0),
 reserved_usd NUMERIC(18,8) NOT NULL CHECK(reserved_usd>0),
 budget_cap_usd NUMERIC(18,8) NOT NULL CHECK(budget_cap_usd>=reserved_usd),
 plan JSONB NOT NULL CHECK(jsonb_typeof(plan)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(project_id,id),
 UNIQUE(project_id,id,bundle_id),
 UNIQUE(project_id,bundle_id,model_snapshot_id,config_sha256),
 FOREIGN KEY(project_id,bundle_id) REFERENCES knowledge_input_bundles(project_id,id),
 FOREIGN KEY(model_snapshot_id,model_id) REFERENCES tb_model_snapshots(id,model_id),
 CHECK(plan->>'schema_version'='evidence-multi-pack-plan/v1'),
 CHECK(plan->>'bundle_sha256'=bundle_sha256),
 CHECK(plan->>'config_sha256'=config_sha256),
 CHECK(plan->>'pricing_sha256'=pricing_sha256),
 CHECK(plan->>'pack_count'=pack_count::text),
 CHECK(plan->'quote'->>'reserved_usd' IS NOT NULL)
);
CREATE INDEX ke_multi_plans_by_bundle ON knowledge_multi_pack_plans(project_id,bundle_id,created_at DESC);

CREATE TABLE knowledge_multi_pack_parts (
 project_id TEXT NOT NULL,
 plan_id UUID NOT NULL,
 bundle_id UUID NOT NULL,
 pack_index INTEGER NOT NULL CHECK(pack_index>=0 AND pack_index<256),
 payload_sha256 TEXT NOT NULL CHECK(payload_sha256 ~ '^[0-9a-f]{64}$'),
 manifest_sha256 TEXT NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
 payload_bytes INTEGER NOT NULL CHECK(payload_bytes BETWEEN 1 AND 8000000),
 span_count INTEGER NOT NULL CHECK(span_count>0 AND span_count<=100000),
 input_token_upper_estimate INTEGER NOT NULL CHECK(input_token_upper_estimate>0),
 estimated_usd NUMERIC(18,8) NOT NULL CHECK(estimated_usd>=0),
 reserved_usd NUMERIC(18,8) NOT NULL CHECK(reserved_usd>0),
 manifest JSONB NOT NULL CHECK(jsonb_typeof(manifest)='object'),
 PRIMARY KEY(project_id,plan_id,pack_index),
 UNIQUE(project_id,plan_id,bundle_id,pack_index),
 FOREIGN KEY(project_id,plan_id,bundle_id)
   REFERENCES knowledge_multi_pack_plans(project_id,id,bundle_id),
 CHECK(manifest->>'schema_version'='evidence-multi-pack/v1'),
 CHECK(manifest->>'pack_index'=pack_index::text),
 CHECK(manifest->>'payload_sha256'=payload_sha256),
 CHECK(manifest->>'payload_bytes'=payload_bytes::text),
 CHECK(manifest->>'span_count'=span_count::text)
);

CREATE TABLE knowledge_multi_pack_spans (
 project_id TEXT NOT NULL,
 plan_id UUID NOT NULL,
 bundle_id UUID NOT NULL,
 pack_index INTEGER NOT NULL,
 chunk_id TEXT NOT NULL CHECK(chunk_id ~ '^[0-9a-f]{64}$'),
 fragment_id TEXT NOT NULL CHECK(fragment_id ~ '^[0-9a-f]{64}$'),
 char_start INTEGER NOT NULL CHECK(char_start>=0),
 char_end INTEGER NOT NULL CHECK(char_end>char_start),
 text_sha256 TEXT NOT NULL CHECK(text_sha256 ~ '^[0-9a-f]{64}$'),
 original_text_sha256 TEXT NOT NULL CHECK(original_text_sha256 ~ '^[0-9a-f]{64}$'),
 byte_start INTEGER NOT NULL CHECK(byte_start>=0),
 byte_end INTEGER NOT NULL CHECK(byte_end>byte_start),
 PRIMARY KEY(project_id,plan_id,chunk_id),
 UNIQUE(project_id,plan_id,fragment_id,char_start),
 FOREIGN KEY(project_id,plan_id,bundle_id,pack_index)
   REFERENCES knowledge_multi_pack_parts(project_id,plan_id,bundle_id,pack_index),
 FOREIGN KEY(project_id,bundle_id,fragment_id)
   REFERENCES knowledge_input_fragments(project_id,bundle_id,fragment_id)
);
CREATE INDEX ke_multi_spans_by_fragment ON knowledge_multi_pack_spans(project_id,bundle_id,fragment_id,char_start);

CREATE TRIGGER ke_multi_plan_immutable BEFORE UPDATE OR DELETE ON knowledge_multi_pack_plans
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_multi_part_immutable BEFORE UPDATE OR DELETE ON knowledge_multi_pack_parts
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_multi_span_immutable BEFORE UPDATE OR DELETE ON knowledge_multi_pack_spans
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
