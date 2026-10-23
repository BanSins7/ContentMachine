-- Search Planner Generation Engine foundation (schema only; NO provider calls).
-- Stage Engine-A: immutable configuration and RTB contexts, planned runs and batches.
-- Financial dispatch, quote approval, provider transport and API are later stages.
-- This migration never alters published RTB records.
CREATE TABLE sqg_config_versions (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 revision INTEGER NOT NULL CHECK (revision > 0),
 config JSONB NOT NULL CHECK (jsonb_typeof(config) = 'object'),
 config_sha256 TEXT NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
 model_catalog_snapshot_id BIGINT NOT NULL REFERENCES tb_model_snapshots(id),
 prompt_sha256 TEXT NOT NULL CHECK (prompt_sha256 ~ '^[0-9a-f]{64}$'),
 raw_schema_sha256 TEXT NOT NULL CHECK (raw_schema_sha256 ~ '^[0-9a-f]{64}$'),
 approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id, revision), UNIQUE(id,project_id)
);
CREATE INDEX idx_sqg_configs_project ON sqg_config_versions(project_id,revision DESC);

CREATE TABLE sqg_quotes (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 publication_id UUID NOT NULL REFERENCES tb_taxonomy_publications(id),
 config_id UUID NOT NULL,
 config_sha256 TEXT NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
 model_catalog_sha256 TEXT NOT NULL CHECK (model_catalog_sha256 ~ '^[0-9a-f]{64}$'),
 pricing_snapshot JSONB NOT NULL CHECK (jsonb_typeof(pricing_snapshot)='object'),
 batch_manifest_sha256 TEXT NOT NULL CHECK (batch_manifest_sha256 ~ '^[0-9a-f]{64}$'),
 quote_low_usd NUMERIC(18,8) NOT NULL CHECK (quote_low_usd>=0),
 quote_expected_usd NUMERIC(18,8) NOT NULL CHECK (quote_expected_usd>=quote_low_usd),
 quote_high_usd NUMERIC(18,8) NOT NULL CHECK (quote_high_usd>=quote_expected_usd),
 expires_at TIMESTAMPTZ NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY (config_id,project_id) REFERENCES sqg_config_versions(id,project_id),
 UNIQUE(id,project_id), UNIQUE(id,project_id,publication_id),
 CHECK (expires_at>created_at)
);
CREATE INDEX idx_sqg_quotes_scope ON sqg_quotes(project_id,publication_id,created_at DESC);

CREATE TABLE sqg_consents (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 quote_id UUID NOT NULL UNIQUE,
 publication_id UUID NOT NULL,
 authorized_usd NUMERIC(18,8) NOT NULL CHECK (authorized_usd>0 AND authorized_usd<=10000),
 spent_usd NUMERIC(18,8) NOT NULL DEFAULT 0 CHECK (spent_usd>=0),
 reserved_usd NUMERIC(18,8) NOT NULL DEFAULT 0 CHECK (reserved_usd>=0),
 authorized_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 expires_at TIMESTAMPTZ NOT NULL,
 revoked_at TIMESTAMPTZ,
 FOREIGN KEY(quote_id,project_id,publication_id)
   REFERENCES sqg_quotes(id,project_id,publication_id),
 UNIQUE(id,project_id), UNIQUE(id,project_id,publication_id),
 CHECK(expires_at>authorized_at),
 CHECK(revoked_at IS NULL OR revoked_at>=authorized_at)
);
CREATE INDEX idx_sqg_consents_project ON sqg_consents(project_id,authorized_at DESC);

CREATE TABLE sqg_generations (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 rtb_run_id UUID NOT NULL,
 publication_id UUID NOT NULL REFERENCES tb_taxonomy_publications(id),
 taxonomy_version INTEGER NOT NULL CHECK(taxonomy_version>0),
 config_id UUID NOT NULL,
 quote_id UUID,
 consent_id UUID,
 idempotency_key TEXT NOT NULL CHECK(length(idempotency_key) BETWEEN 12 AND 128),
 input_sha256 TEXT NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
 taxonomy_sha256 TEXT NOT NULL CHECK(taxonomy_sha256 ~ '^[0-9a-f]{64}$'),
 model_id TEXT NOT NULL CHECK(length(btrim(model_id))>0),
 config_snapshot JSONB NOT NULL CHECK(jsonb_typeof(config_snapshot)='object'),
 status TEXT NOT NULL DEFAULT 'awaiting_authorization' CHECK (status IN (
  'created','awaiting_authorization','queued','running',
  'paused_budget','paused_price','blocked_config','uncertain',
  'failed','completed','cancelled')),
 cancel_requested BOOLEAN NOT NULL DEFAULT FALSE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 completed_at TIMESTAMPTZ,
 FOREIGN KEY(rtb_run_id,project_id) REFERENCES tb_runs(id,project_id),
 FOREIGN KEY(config_id,project_id) REFERENCES sqg_config_versions(id,project_id),
 FOREIGN KEY(quote_id,project_id) REFERENCES sqg_quotes(id,project_id),
 FOREIGN KEY(consent_id,project_id) REFERENCES sqg_consents(id,project_id),
 UNIQUE(project_id,idempotency_key), UNIQUE(id,project_id),
 CHECK (consent_id IS NULL OR quote_id IS NOT NULL)
);
-- A consent may fund exactly one generation (and not multiple independent runs).
CREATE UNIQUE INDEX idx_sqg_single_run_per_consent
 ON sqg_generations(consent_id) WHERE consent_id IS NOT NULL;
CREATE INDEX idx_sqg_generations_recent ON sqg_generations(project_id,created_at DESC);
CREATE INDEX idx_sqg_generations_pub ON sqg_generations(project_id,publication_id);

CREATE TABLE sqg_contexts (
 generation_id UUID NOT NULL REFERENCES sqg_generations(id),
 hypothesis_id TEXT NOT NULL CHECK(length(hypothesis_id)>0 AND length(hypothesis_id)<=40),
 direction_id TEXT NOT NULL,
 branch_id TEXT,
 context JSONB NOT NULL CHECK(jsonb_typeof(context)='object'),
 context_sha256 TEXT NOT NULL CHECK(context_sha256 ~ '^[0-9a-f]{64}$'),
 PRIMARY KEY(generation_id,hypothesis_id)
);

CREATE TABLE sqg_batches (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 generation_id UUID NOT NULL REFERENCES sqg_generations(id),
 batch_index INTEGER NOT NULL CHECK(batch_index>=0),
 direction_id TEXT NOT NULL,
 hypothesis_ids JSONB NOT NULL CHECK(jsonb_typeof(hypothesis_ids)='array'),
 input_sha256 TEXT NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
 estimated_input_tokens INTEGER NOT NULL CHECK(estimated_input_tokens>=0),
 output_token_cap INTEGER NOT NULL CHECK(output_token_cap>0),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN (
   'pending','reserved','dispatching','completed','failed_known','uncertain','cancelled')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(generation_id,batch_index), UNIQUE(id,generation_id)
);
CREATE INDEX idx_sqg_batch_poll ON sqg_batches(generation_id,status,batch_index);

CREATE TABLE sqg_attempts (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 generation_id UUID NOT NULL REFERENCES sqg_generations(id),
 batch_id UUID NOT NULL,
 sample_index SMALLINT NOT NULL DEFAULT 0 CHECK(sample_index>=0),
 attempt_no SMALLINT NOT NULL DEFAULT 1 CHECK(attempt_no>0),
 request_sha256 TEXT NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
 prompt_sha256 TEXT NOT NULL CHECK(prompt_sha256 ~ '^[0-9a-f]{64}$'),
 raw_schema_sha256 TEXT NOT NULL CHECK(raw_schema_sha256 ~ '^[0-9a-f]{64}$'),
 model_catalog_sha256 TEXT NOT NULL CHECK(model_catalog_sha256 ~ '^[0-9a-f]{64}$'),
 status TEXT NOT NULL CHECK(status IN (
   'reserved','dispatch_intent','completed','failed_known','uncertain',
   'abandoned_before_dispatch','reconciled_completed','reconciled_failed')),
 reservation_usd NUMERIC(18,8) NOT NULL CHECK(reservation_usd>0),
 charged_usd NUMERIC(18,8) CHECK(charged_usd>=0),
 provider_response_id TEXT,
 usage JSONB CHECK(usage IS NULL OR jsonb_typeof(usage)='object'),
 raw_result JSONB,
 finish_reason TEXT,
 dispatch_intent_at TIMESTAMPTZ,
 started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 ended_at TIMESTAMPTZ,
 error_code TEXT,
 FOREIGN KEY(batch_id,generation_id) REFERENCES sqg_batches(id,generation_id),
 UNIQUE(batch_id,sample_index,attempt_no)
);
CREATE INDEX idx_sqg_attempts_batch ON sqg_attempts(batch_id,status);

CREATE TABLE sqg_candidates (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 generation_id UUID NOT NULL,
 batch_id UUID NOT NULL,
 hypothesis_id TEXT NOT NULL,
 direction_id TEXT NOT NULL,
 branch_id TEXT,
 source_fact_ids JSONB NOT NULL CHECK(jsonb_typeof(source_fact_ids)='array'),
 text TEXT NOT NULL CHECK(length(btrim(text)) BETWEEN 1 AND 120),
 query_mode TEXT NOT NULL CHECK(query_mode IN ('keyword','hashtag')),
 angle TEXT NOT NULL CHECK(angle IN (
  'direct','problem','solution','situation','format',
  'comparison','experience','critical','other')),
 language TEXT NOT NULL CHECK(language ~ '^[a-z]{2,3}(-[A-Z]{2})?$'),
 research_stance TEXT NOT NULL CHECK(research_stance IN ('support','challenge','neutral')),
 rationale TEXT NOT NULL CHECK(length(btrim(rationale)) BETWEEN 1 AND 280),
 raw_position INTEGER NOT NULL CHECK(raw_position>=0),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(batch_id,generation_id) REFERENCES sqg_batches(id,generation_id),
 FOREIGN KEY(generation_id,hypothesis_id) REFERENCES sqg_contexts(generation_id,hypothesis_id),
 UNIQUE(batch_id,raw_position)
);
CREATE INDEX idx_sqg_candidates_hyp ON sqg_candidates(generation_id,hypothesis_id);

CREATE TABLE sqg_hypothesis_coverage (
 generation_id UUID NOT NULL,
 hypothesis_id TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('generated','no_viable_queries')),
 candidate_count INTEGER NOT NULL CHECK(candidate_count BETWEEN 0 AND 16),
 reason TEXT,
 PRIMARY KEY(generation_id,hypothesis_id),
 FOREIGN KEY(generation_id,hypothesis_id)
    REFERENCES sqg_contexts(generation_id,hypothesis_id),
 CHECK(
   (state='generated' AND candidate_count>0 AND reason IS NULL)
   OR (state='no_viable_queries' AND candidate_count=0
       AND length(btrim(coalesce(reason,'')))>0)
 )
);

CREATE TABLE sqg_events (
 id BIGSERIAL PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 generation_id UUID NOT NULL,
 batch_id UUID,
 attempt_id UUID,
 event_kind TEXT NOT NULL CHECK(length(btrim(event_kind))>0),
 payload JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(payload)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(generation_id,project_id) REFERENCES sqg_generations(id,project_id),
 FOREIGN KEY(batch_id,generation_id) REFERENCES sqg_batches(id,generation_id)
);
CREATE INDEX idx_sqg_events_run ON sqg_events(generation_id,created_at DESC);

CREATE TABLE sqg_outbox (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 generation_id UUID NOT NULL REFERENCES sqg_generations(id),
 event_type TEXT NOT NULL CHECK(event_type='query_candidates_ready/v1'),
 payload_pointer JSONB NOT NULL CHECK(jsonb_typeof(payload_pointer)='object'),
 delivered_at TIMESTAMPTZ,
 delivery_attempts INTEGER NOT NULL DEFAULT 0 CHECK(delivery_attempts>=0),
 next_delivery_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(generation_id,event_type)
);
CREATE INDEX idx_sqg_outbox_pending ON sqg_outbox(next_delivery_at)
 WHERE delivered_at IS NULL;

-- Guard publication's project/run/version using real RTB primary tables:
-- simple UUID foreign keys alone would NOT enforce tenant scope.
CREATE FUNCTION sqg_require_deep_publication() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME = 'sqg_quotes' THEN
   IF NOT EXISTS (
     SELECT 1 FROM tb_taxonomy_publications p
     JOIN tb_deep_publications d ON d.builder_publication_id=p.id
     JOIN tb_runs r ON r.id=p.run_id AND r.project_id=p.project_id
     JOIN tb_deep_runs dr ON dr.run_id=p.run_id AND dr.project_id=p.project_id
     WHERE p.id=NEW.publication_id AND p.project_id=NEW.project_id
       AND r.status='approved' AND dr.status='approved'
       AND (SELECT COUNT(*) FROM tb_deep_reviews rv
            WHERE rv.run_id=p.run_id AND rv.project_id=p.project_id)=2
   ) THEN RAISE EXCEPTION 'SQG: only project-scoped approved deep publications'; END IF;
 ELSE
   IF NOT EXISTS (
     SELECT 1 FROM tb_taxonomy_publications p
     JOIN tb_deep_publications d ON d.builder_publication_id=p.id
     JOIN tb_runs r ON r.id=p.run_id AND r.project_id=p.project_id
     JOIN tb_deep_runs dr ON dr.run_id=p.run_id AND dr.project_id=p.project_id
     WHERE p.id=NEW.publication_id AND p.project_id=NEW.project_id
       AND p.run_id=NEW.rtb_run_id AND p.taxonomy_version=NEW.taxonomy_version
       AND r.status='approved' AND dr.status='approved'
       AND (SELECT COUNT(*) FROM tb_deep_reviews rv
            WHERE rv.run_id=p.run_id AND rv.project_id=p.project_id)=2
   ) THEN RAISE EXCEPTION 'SQG: mismatched or unapproved RTB publication'; END IF;
   IF NEW.quote_id IS NOT NULL AND NOT EXISTS (
     SELECT 1 FROM sqg_quotes q WHERE q.id=NEW.quote_id
        AND q.project_id=NEW.project_id AND q.publication_id=NEW.publication_id
        AND q.config_id=NEW.config_id
   ) THEN RAISE EXCEPTION 'SQG: mismatched quote'; END IF;
   IF NEW.consent_id IS NOT NULL AND NOT EXISTS (
     SELECT 1 FROM sqg_consents c WHERE c.id=NEW.consent_id
        AND c.project_id=NEW.project_id
        AND c.publication_id=NEW.publication_id AND c.quote_id=NEW.quote_id
   ) THEN RAISE EXCEPTION 'SQG: mismatched consent'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER sqg_quote_publication_guard BEFORE INSERT ON sqg_quotes
 FOR EACH ROW EXECUTE FUNCTION sqg_require_deep_publication();
CREATE TRIGGER sqg_generation_publication_guard BEFORE INSERT OR UPDATE OF
  project_id,publication_id,rtb_run_id,taxonomy_version,config_id,quote_id,consent_id
 ON sqg_generations FOR EACH ROW EXECUTE FUNCTION sqg_require_deep_publication();

-- Protect once-approved configuration and quoted historical amount.
CREATE FUNCTION sqg_reject_immutable_write() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'SQG immutable snapshot cannot be updated or deleted'; END $$;
CREATE TRIGGER sqg_config_immutable BEFORE UPDATE OR DELETE ON sqg_config_versions
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();
CREATE TRIGGER sqg_quote_immutable BEFORE UPDATE OR DELETE ON sqg_quotes
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();
CREATE TRIGGER sqg_context_immutable BEFORE UPDATE OR DELETE ON sqg_contexts
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();
CREATE TRIGGER sqg_event_immutable BEFORE UPDATE OR DELETE ON sqg_events
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();
CREATE TRIGGER sqg_candidate_immutable BEFORE UPDATE OR DELETE ON sqg_candidates
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();
