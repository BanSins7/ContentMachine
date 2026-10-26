-- GB-MOD-03: additive, Group B-owned runtime schema. No QDP/LLM side effects.
-- Earlier main migrations end at 053; Group A branch uses 054.
-- This migration needs only tables present on baseline main, not peer branches.
-- Never modify 005_content_features_b.sql.
CREATE TABLE group_b_inbox (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 video_id BIGINT NOT NULL REFERENCES videos(id),
 source_id TEXT NOT NULL REFERENCES sources(id),
 event_key TEXT NOT NULL CHECK(length(btrim(event_key)) BETWEEN 1 AND 200),
 qdp_run_id UUID NOT NULL,
 producer_schema TEXT NOT NULL CHECK(length(btrim(producer_schema)) BETWEEN 1 AND 100),
 event_sha256 TEXT NOT NULL CHECK(event_sha256 ~ '^[0-9a-f]{64}$'),
 preparation_sha256 TEXT NOT NULL CHECK(preparation_sha256 ~ '^[0-9a-f]{64}$'),
 status TEXT NOT NULL DEFAULT 'received'
   CHECK(status IN ('received','accepted','quarantined')),
 received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,id),
 UNIQUE(project_id,id,video_id,source_id),
 UNIQUE(project_id,event_key)
);
CREATE INDEX group_b_inbox_project_received ON group_b_inbox(project_id,received_at DESC);

CREATE TABLE group_b_runs (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 inbox_id UUID NOT NULL,
 video_id BIGINT NOT NULL,
 source_id TEXT NOT NULL,
 content_type TEXT NOT NULL CHECK(content_type IN ('video','carousel')),
 video_a_analysis_id BIGINT REFERENCES video_feature_a_analyses(id),
 carousel_a_analysis_id BIGINT REFERENCES carousel_feature_a_analyses(id),
 a_snapshot_sha256 TEXT CHECK(a_snapshot_sha256 IS NULL OR a_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
 media_manifest_sha256 TEXT CHECK(media_manifest_sha256 IS NULL OR media_manifest_sha256 ~ '^[0-9a-f]{64}$'),
 source_policy_version_id BIGINT REFERENCES source_policy_versions(id),
 config_sha256 TEXT NOT NULL CHECK(config_sha256 ~ '^[0-9a-f]{64}$'),
 idempotency_key TEXT NOT NULL CHECK(length(btrim(idempotency_key)) BETWEEN 12 AND 200),
 state TEXT NOT NULL DEFAULT 'draft'
   CHECK(state IN ('draft','quoted','queued','running','partial','completed',
                   'failed','cancelled','needs_review')),
 result_analysis_id BIGINT REFERENCES content_b_analyses(id) ON DELETE SET NULL,
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 lease_token UUID,
 lease_until TIMESTAMPTZ,
 last_error_code TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,id),
 UNIQUE(project_id,id,inbox_id),
 UNIQUE(project_id,idempotency_key),
 FOREIGN KEY(project_id,inbox_id,video_id,source_id)
   REFERENCES group_b_inbox(project_id,id,video_id,source_id),
 CHECK((content_type='video' AND carousel_a_analysis_id IS NULL) OR
       (content_type='carousel' AND video_a_analysis_id IS NULL)),
 CHECK((lease_token IS NULL AND lease_until IS NULL) OR
       (lease_token IS NOT NULL AND lease_until IS NOT NULL)),
 CHECK(state<>'completed' OR result_analysis_id IS NOT NULL)
);
CREATE INDEX group_b_runs_project_state ON group_b_runs(project_id,state,created_at DESC);
CREATE INDEX group_b_runs_lease ON group_b_runs(lease_until) WHERE state='running';

CREATE TABLE group_b_block_states (
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 block TEXT NOT NULL CHECK(block IN ('B1','B2','B3','B4','B5','B6','B7','B8')),
 status TEXT NOT NULL DEFAULT 'not_run'
   CHECK(status IN ('not_run','ready','running','complete','partial','failed','unavailable')),
 evidence_sha256 TEXT CHECK(evidence_sha256 IS NULL OR evidence_sha256 ~ '^[0-9a-f]{64}$'),
 error_code TEXT,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(project_id,run_id,block),
 FOREIGN KEY(project_id,run_id) REFERENCES group_b_runs(project_id,id)
);
CREATE INDEX group_b_blocks_state ON group_b_block_states(project_id,status,block);

CREATE TABLE group_b_model_quotes (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 model_id TEXT NOT NULL CHECK(length(btrim(model_id)) BETWEEN 1 AND 200),
 request_sha256 TEXT NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
 evidence_sha256 TEXT NOT NULL CHECK(evidence_sha256 ~ '^[0-9a-f]{64}$'),
 quote_sha256 TEXT NOT NULL CHECK(quote_sha256 ~ '^[0-9a-f]{64}$'),
 quoted_usd NUMERIC(20,12) NOT NULL CHECK(quoted_usd>0),
 pricing_snapshot JSONB NOT NULL CHECK(jsonb_typeof(pricing_snapshot)='object'),
 quote_details JSONB NOT NULL CHECK(jsonb_typeof(quote_details)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 expires_at TIMESTAMPTZ NOT NULL,
 UNIQUE(project_id,id,run_id),
 UNIQUE(project_id,run_id,quote_sha256),
 FOREIGN KEY(project_id,run_id) REFERENCES group_b_runs(project_id,id),
 CHECK(expires_at>created_at)
);
CREATE INDEX group_b_quotes_run ON group_b_model_quotes(project_id,run_id,created_at DESC);

CREATE TABLE group_b_model_approvals (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 quote_id UUID NOT NULL,
 approved_by TEXT NOT NULL CHECK(length(btrim(approved_by)) BETWEEN 1 AND 160),
 authorized_usd NUMERIC(20,12) NOT NULL CHECK(authorized_usd>0),
 approved_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,id,run_id,quote_id),
 UNIQUE(project_id,quote_id),
 FOREIGN KEY(project_id,quote_id,run_id)
   REFERENCES group_b_model_quotes(project_id,id,run_id)
);

CREATE TABLE group_b_model_attempts (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 quote_id UUID NOT NULL,
 approval_id UUID NOT NULL,
 request_key TEXT NOT NULL CHECK(length(btrim(request_key)) BETWEEN 12 AND 200),
 request_sha256 TEXT NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
 charge_state TEXT NOT NULL DEFAULT 'reserved'
   CHECK(charge_state IN ('reserved','submitted','settled','failed','uncertain')),
 reserved_usd NUMERIC(20,12) NOT NULL CHECK(reserved_usd>0),
 charged_usd NUMERIC(20,12) CHECK(charged_usd IS NULL OR charged_usd>=0),
 provider_request_id TEXT,
 usage JSONB CHECK(usage IS NULL OR jsonb_typeof(usage)='object'),
 error_code TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 finalized_at TIMESTAMPTZ,
 UNIQUE(project_id,id),
 UNIQUE(project_id,run_id,request_key),
 FOREIGN KEY(project_id,approval_id,run_id,quote_id)
   REFERENCES group_b_model_approvals(project_id,id,run_id,quote_id),
 CHECK(charge_state<>'settled' OR charged_usd IS NOT NULL),
 CHECK(charge_state<>'reserved' OR (charged_usd IS NULL AND finalized_at IS NULL)),
 CHECK(charge_state NOT IN ('settled','failed','uncertain') OR finalized_at IS NOT NULL)
);
CREATE INDEX group_b_attempts_run ON group_b_model_attempts(project_id,run_id,created_at DESC);

CREATE TABLE group_b_review_events (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 revision INTEGER NOT NULL CHECK(revision>=1),
 reviewer_id TEXT NOT NULL CHECK(length(btrim(reviewer_id)) BETWEEN 1 AND 160),
 decision_sha256 TEXT NOT NULL CHECK(decision_sha256 ~ '^[0-9a-f]{64}$'),
 decision JSONB NOT NULL CHECK(jsonb_typeof(decision)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,run_id,revision),
 FOREIGN KEY(project_id,run_id) REFERENCES group_b_runs(project_id,id)
);
CREATE INDEX group_b_review_run ON group_b_review_events(project_id,run_id,created_at DESC);

CREATE TABLE group_b_outbox (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 event_key TEXT NOT NULL CHECK(length(btrim(event_key)) BETWEEN 1 AND 200),
 event_type TEXT NOT NULL CHECK(length(btrim(event_type)) BETWEEN 1 AND 80),
 payload_sha256 TEXT NOT NULL CHECK(payload_sha256 ~ '^[0-9a-f]{64}$'),
 payload JSONB NOT NULL CHECK(jsonb_typeof(payload)='object'),
 status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','delivered','failed')),
 delivered_at TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(project_id,event_key),
 FOREIGN KEY(project_id,run_id) REFERENCES group_b_runs(project_id,id),
 CHECK(status='delivered' OR delivered_at IS NULL)
);
CREATE INDEX group_b_outbox_pending ON group_b_outbox(project_id,created_at)
 WHERE status IN ('queued','failed');

-- Exact video/source compatibility; A does not own project IDs in schema 005.
CREATE FUNCTION group_b_run_lineage_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NEW.video_a_analysis_id IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM video_feature_a_analyses a
   WHERE a.id=NEW.video_a_analysis_id AND a.video_id=NEW.video_id
     AND a.source_id=NEW.source_id
 ) THEN RAISE EXCEPTION 'Group B video A pointer mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.carousel_a_analysis_id IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM carousel_feature_a_analyses a
   WHERE a.id=NEW.carousel_a_analysis_id AND a.video_id=NEW.video_id
     AND a.source_id=NEW.source_id
 ) THEN RAISE EXCEPTION 'Group B carousel A pointer mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.source_policy_version_id IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM source_policy_versions p
   WHERE p.id=NEW.source_policy_version_id AND p.source_id=NEW.source_id
 ) THEN RAISE EXCEPTION 'Group B policy pointer mismatch' USING ERRCODE='23514'; END IF;
 IF NEW.result_analysis_id IS NOT NULL AND NOT EXISTS(
  SELECT 1 FROM content_b_analyses a
   WHERE a.id=NEW.result_analysis_id AND a.video_id=NEW.video_id
     AND a.source_id=NEW.source_id AND a.content_type=NEW.content_type
     AND (NEW.state <> 'completed' OR (a.state='completed' AND NOT a.needs_review))
 ) THEN RAISE EXCEPTION 'Group B result pointer mismatch' USING ERRCODE='23514'; END IF;
 IF TG_OP='UPDATE' THEN
  IF (
  OLD.project_id IS DISTINCT FROM NEW.project_id OR
  OLD.inbox_id IS DISTINCT FROM NEW.inbox_id OR
  OLD.video_id IS DISTINCT FROM NEW.video_id OR
  OLD.source_id IS DISTINCT FROM NEW.source_id OR
  OLD.content_type IS DISTINCT FROM NEW.content_type OR
  OLD.config_sha256 IS DISTINCT FROM NEW.config_sha256 OR
  OLD.idempotency_key IS DISTINCT FROM NEW.idempotency_key
 ) THEN RAISE EXCEPTION 'Group B run identity immutable' USING ERRCODE='23514'; END IF;
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER trg_group_b_run_lineage BEFORE INSERT OR UPDATE
 ON group_b_runs FOR EACH ROW EXECUTE FUNCTION group_b_run_lineage_guard();

CREATE FUNCTION group_b_inbox_identity_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
  RAISE EXCEPTION 'Group B inbox history immutable' USING ERRCODE='23514';
 END IF;
 IF (to_jsonb(OLD)-'status') IS DISTINCT FROM (to_jsonb(NEW)-'status')
    OR OLD.status<>'received' OR NEW.status NOT IN ('accepted','quarantined') THEN
  RAISE EXCEPTION 'Group B inbox identity immutable' USING ERRCODE='23514';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER trg_group_b_inbox_guard BEFORE UPDATE OR DELETE
 ON group_b_inbox FOR EACH ROW EXECUTE FUNCTION group_b_inbox_identity_guard();

CREATE FUNCTION group_b_forbid_change() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN RAISE EXCEPTION 'Group B immutable record; create a new revision'
 USING ERRCODE='23514'; END $$;
CREATE TRIGGER trg_group_b_quote_immutable BEFORE UPDATE OR DELETE
 ON group_b_model_quotes FOR EACH ROW EXECUTE FUNCTION group_b_forbid_change();
CREATE TRIGGER trg_group_b_approval_immutable BEFORE UPDATE OR DELETE
 ON group_b_model_approvals FOR EACH ROW EXECUTE FUNCTION group_b_forbid_change();
CREATE TRIGGER trg_group_b_review_immutable BEFORE UPDATE OR DELETE
 ON group_b_review_events FOR EACH ROW EXECUTE FUNCTION group_b_forbid_change();
