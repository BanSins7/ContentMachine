-- Query Validation v1: immutable decisions and atomic downstream handoff.
-- Independent from SRE/RTB and from the existing paid generation ledger.
CREATE UNIQUE INDEX sqg_candidates_generation_candidate_unique
  ON sqg_candidates(generation_id,id);

CREATE TABLE qv_runs (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 generation_id UUID NOT NULL,
 source_outbox_id UUID NOT NULL UNIQUE REFERENCES sqg_outbox(id),
 config JSONB NOT NULL CHECK(jsonb_typeof(config)='object'),
 config_sha256 TEXT NOT NULL CHECK(config_sha256 ~ '^[0-9a-f]{64}$'),
 source_bundle_sha256 TEXT NOT NULL CHECK(source_bundle_sha256 ~ '^[0-9a-f]{64}$'),
 embedding_model_key TEXT NOT NULL CHECK(length(embedding_model_key) BETWEEN 1 AND 240),
 embedding_mode TEXT NOT NULL CHECK(embedding_mode IN ('semantic_local','test_fixture')),
 source_candidate_count INTEGER NOT NULL CHECK(source_candidate_count BETWEEN 0 AND 480),
 selected_count INTEGER NOT NULL CHECK(selected_count BETWEEN 0 AND source_candidate_count),
 status TEXT NOT NULL DEFAULT 'completed' CHECK(status='completed'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY (generation_id,project_id) REFERENCES sqg_generations(id,project_id),
 UNIQUE(id,generation_id), UNIQUE(id,project_id)
);
CREATE INDEX qv_runs_project ON qv_runs(project_id,created_at DESC);

CREATE TABLE qv_decisions (
 run_id UUID NOT NULL,
 generation_id UUID NOT NULL,
 candidate_id UUID NOT NULL,
 hypothesis_id TEXT NOT NULL,
 decision TEXT NOT NULL CHECK(decision IN ('selected','rejected')),
 reason_code TEXT NOT NULL CHECK(reason_code IN (
  'selected','explicit_exclusion','low_relevance','negative_scope',
  'exact_duplicate','semantic_duplicate','quota')),
 positive_similarity DOUBLE PRECISION NOT NULL CHECK(
  positive_similarity BETWEEN -1 AND 1),
 negative_similarity DOUBLE PRECISION NOT NULL CHECK(
  negative_similarity BETWEEN -1 AND 1),
 score_margin DOUBLE PRECISION NOT NULL CHECK(
  score_margin BETWEEN -2 AND 2),
 mmr_score DOUBLE PRECISION CHECK(
  mmr_score BETWEEN -2 AND 2),
 selected_rank INTEGER CHECK(selected_rank BETWEEN 1 AND 16),
 duplicate_of UUID,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(run_id,candidate_id),
 FOREIGN KEY(run_id,generation_id) REFERENCES qv_runs(id,generation_id),
 FOREIGN KEY(generation_id,candidate_id)
   REFERENCES sqg_candidates(generation_id,id),
 FOREIGN KEY(generation_id,hypothesis_id)
   REFERENCES sqg_contexts(generation_id,hypothesis_id),
 FOREIGN KEY(generation_id,duplicate_of) REFERENCES sqg_candidates(generation_id,id),
 CHECK ((decision='selected' AND reason_code='selected' AND selected_rank IS NOT NULL
          AND duplicate_of IS NULL)
      OR (decision='rejected' AND reason_code<>'selected'
          AND selected_rank IS NULL)),
 CHECK ((reason_code IN ('exact_duplicate','semantic_duplicate')) = (duplicate_of IS NOT NULL))
);
CREATE UNIQUE INDEX qv_decisions_unique_selected_rank
 ON qv_decisions(run_id,hypothesis_id,selected_rank) WHERE decision='selected';

CREATE TABLE qv_coverage (
 run_id UUID NOT NULL,
 generation_id UUID NOT NULL,
 hypothesis_id TEXT NOT NULL,
 source_count INTEGER NOT NULL CHECK(source_count BETWEEN 0 AND 16),
 selected_count INTEGER NOT NULL CHECK(selected_count BETWEEN 0 AND source_count),
 outcome TEXT NOT NULL CHECK(outcome IN ('selected','no_source_candidates','all_rejected')),
 reason TEXT,
 PRIMARY KEY(run_id,hypothesis_id),
 FOREIGN KEY(run_id,generation_id) REFERENCES qv_runs(id,generation_id),
 FOREIGN KEY(generation_id,hypothesis_id) REFERENCES sqg_contexts(generation_id,hypothesis_id),
 CHECK ((outcome='selected' AND selected_count>0 AND reason IS NULL)
    OR (outcome='no_source_candidates' AND source_count=0 AND selected_count=0
        AND length(btrim(coalesce(reason,'')))>0)
    OR (outcome='all_rejected' AND source_count>0 AND selected_count=0
        AND length(btrim(coalesce(reason,'')))>0))
);

CREATE TABLE qv_outbox (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 run_id UUID NOT NULL UNIQUE REFERENCES qv_runs(id),
 event_type TEXT NOT NULL DEFAULT 'validated_queries_ready/v1'
   CHECK(event_type='validated_queries_ready/v1'),
 payload_pointer JSONB NOT NULL CHECK(jsonb_typeof(payload_pointer)='object'),
 delivered_at TIMESTAMPTZ,
 delivery_attempts INTEGER NOT NULL DEFAULT 0 CHECK(delivery_attempts>=0),
 next_delivery_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX qv_outbox_pending ON qv_outbox(next_delivery_at) WHERE delivered_at IS NULL;

-- No historical QA evidence or candidate decisions can be silently rewritten.
CREATE TRIGGER qv_run_immutable BEFORE UPDATE OR DELETE ON qv_runs
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();
CREATE TRIGGER qv_decision_immutable BEFORE UPDATE OR DELETE ON qv_decisions
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();
CREATE TRIGGER qv_coverage_immutable BEFORE UPDATE OR DELETE ON qv_coverage
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();
