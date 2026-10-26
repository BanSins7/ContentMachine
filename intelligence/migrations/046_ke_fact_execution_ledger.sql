-- B Stage07: explicit one-run consent, cost reservation, and durable MiMo attempt ledger.
-- Separate from legacy Taxonomy Builder tb_runs and its spending consents.
-- No API invokes OpenRouter automatically; attempts happen only on explicit advance.

CREATE TABLE knowledge_fact_cost_quotes (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 batch_id UUID NOT NULL,
 batch_sha256 TEXT NOT NULL CHECK(batch_sha256 ~ '^[0-9a-f]{64}$'),
 catalog_sha256 TEXT NOT NULL CHECK(catalog_sha256 ~ '^[0-9a-f]{64}$'),
 quoted_usd NUMERIC(24,12) NOT NULL CHECK(quoted_usd > 0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 expires_at TIMESTAMPTZ NOT NULL,
 FOREIGN KEY(project_id,batch_id) REFERENCES knowledge_fact_request_batches(project_id,id),
 UNIQUE(project_id,id,batch_id),
 CHECK(expires_at > created_at)
);
CREATE INDEX ke_fact_quotes_by_batch ON knowledge_fact_cost_quotes(project_id,batch_id,created_at DESC);

CREATE TABLE knowledge_fact_spending_consents (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 batch_id UUID NOT NULL,
 quote_id UUID NOT NULL UNIQUE,
 authorized_usd NUMERIC(24,12) NOT NULL CHECK(authorized_usd > 0 AND authorized_usd <= 10000),
 authorized_by TEXT NOT NULL CHECK(length(btrim(authorized_by)) > 0),
 authorized_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(project_id,quote_id,batch_id) REFERENCES knowledge_fact_cost_quotes(project_id,id,batch_id),
 UNIQUE(project_id,id,quote_id,batch_id)
);
CREATE TABLE knowledge_fact_consent_revocations (
 consent_id UUID PRIMARY KEY,
 project_id TEXT NOT NULL,
 revoked_by TEXT NOT NULL CHECK(length(btrim(revoked_by)) > 0),
 revoked_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(consent_id) REFERENCES knowledge_fact_spending_consents(id)
);

CREATE TABLE knowledge_fact_execution_runs (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 batch_id UUID NOT NULL,
 quote_id UUID NOT NULL,
 consent_id UUID NOT NULL UNIQUE,
 idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 12 AND 128),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
   'pending','running','ready_for_normalization','failed','uncertain',
   'paused_budget','paused_price','revoked'
 )),
 authorized_usd NUMERIC(24,12) NOT NULL CHECK(authorized_usd > 0),
 spent_usd NUMERIC(24,12) NOT NULL DEFAULT 0 CHECK(spent_usd >= 0),
 reserved_usd NUMERIC(24,12) NOT NULL DEFAULT 0 CHECK(reserved_usd >= 0),
 next_pack_index INTEGER NOT NULL DEFAULT 0 CHECK(next_pack_index BETWEEN 0 AND 256),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY(project_id,batch_id) REFERENCES knowledge_fact_request_batches(project_id,id),
 FOREIGN KEY(project_id,quote_id,batch_id) REFERENCES knowledge_fact_cost_quotes(project_id,id,batch_id),
 FOREIGN KEY(project_id,consent_id,quote_id,batch_id)
   REFERENCES knowledge_fact_spending_consents(project_id,id,quote_id,batch_id),
 UNIQUE(project_id,id,batch_id),
 UNIQUE(project_id,idempotency_key)
);
CREATE INDEX ke_fact_runs_by_batch ON knowledge_fact_execution_runs(project_id,batch_id,created_at DESC);

CREATE TABLE knowledge_fact_execution_attempts (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 batch_id UUID NOT NULL,
 pack_index INTEGER NOT NULL CHECK(pack_index BETWEEN 0 AND 255),
 request_sha256 TEXT NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
 status TEXT NOT NULL CHECK(status IN ('reserved','completed','error','uncertain')),
 reservation_usd NUMERIC(24,12) NOT NULL CHECK(reservation_usd > 0),
 charged_usd NUMERIC(24,12) CHECK(charged_usd >= 0),
 provider_response_id TEXT,
 usage JSONB,
 draft_candidates JSONB,
 candidate_count INTEGER CHECK(candidate_count BETWEEN 0 AND 80),
 candidate_limit_reached BOOLEAN,
 error_code TEXT,
 started_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 ended_at TIMESTAMPTZ,
 UNIQUE(project_id,run_id,pack_index),
 FOREIGN KEY(project_id,run_id,batch_id) REFERENCES knowledge_fact_execution_runs(project_id,id,batch_id),
 FOREIGN KEY(project_id,batch_id,pack_index) REFERENCES knowledge_fact_request_parts(project_id,batch_id,pack_index),
 CHECK (draft_candidates IS NULL OR jsonb_typeof(draft_candidates)='object')
);
CREATE INDEX ke_fact_attempts_by_run ON knowledge_fact_execution_attempts(project_id,run_id,pack_index);

CREATE TRIGGER ke_fact_quote_immutable BEFORE UPDATE OR DELETE ON knowledge_fact_cost_quotes
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_fact_consent_immutable BEFORE UPDATE OR DELETE ON knowledge_fact_spending_consents
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_fact_revocation_immutable BEFORE UPDATE OR DELETE ON knowledge_fact_consent_revocations
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
