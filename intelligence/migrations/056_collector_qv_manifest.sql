-- VC-MOD-02 / 056: immutable Search Planner QV -> Collector publication ledger.
-- Applied AFTER main 016_query_validation.sql and collector 054/055.
-- Never edit a previously deployed file or duplicate a second Query Validation engine.
-- Publisher VC-MOD-04 writes these structures and ACKs qv_outbox in SAME transaction.

-- Composite event lineage; both columns already have independent unique constraints.
ALTER TABLE qv_outbox
 ADD CONSTRAINT qv_outbox_id_run_vc_uq UNIQUE (id,run_id);

CREATE TABLE collector_qv_manifests (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 qv_outbox_id UUID NOT NULL UNIQUE,
 validation_run_id UUID NOT NULL UNIQUE,
 generation_id UUID NOT NULL,
 publication_id UUID NOT NULL,
 taxonomy_version INTEGER NOT NULL CHECK(taxonomy_version>0),
 taxonomy_sha256 TEXT NOT NULL CHECK(taxonomy_sha256 ~ '^[0-9a-f]{64}$'),
 qv_bundle_sha256 TEXT NOT NULL CHECK(qv_bundle_sha256 ~ '^[0-9a-f]{64}$'),
 manifest_sha256 TEXT NOT NULL CHECK(manifest_sha256 ~ '^[0-9a-f]{64}$'),
 embedding_mode TEXT NOT NULL DEFAULT 'semantic_local' CHECK(embedding_mode='semantic_local'),
 selected_count INTEGER NOT NULL CHECK(selected_count BETWEEN 0 AND 480),
 source_candidate_count INTEGER NOT NULL CHECK(source_candidate_count BETWEEN 0 AND 480),
 shard_count INTEGER NOT NULL CHECK(shard_count BETWEEN 0 AND 480),
 coverage_count INTEGER NOT NULL CHECK(coverage_count BETWEEN 1 AND 48),
 status TEXT NOT NULL CHECK(status IN ('published','no_queries')),
 published_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY (qv_outbox_id,validation_run_id) REFERENCES qv_outbox(id,run_id),
 FOREIGN KEY (validation_run_id,project_id) REFERENCES qv_runs(id,project_id),
 FOREIGN KEY (validation_run_id,generation_id) REFERENCES qv_runs(id,generation_id),
 FOREIGN KEY (generation_id,project_id) REFERENCES sqg_generations(id,project_id),
 FOREIGN KEY (publication_id) REFERENCES tb_taxonomy_publications(id),
 UNIQUE (id,project_id),
 UNIQUE (id,project_id,validation_run_id),
 UNIQUE (project_id,validation_run_id,manifest_sha256),
 CHECK (selected_count<=source_candidate_count),
 CHECK ((selected_count=0 AND shard_count=0 AND status='no_queries')
     OR (selected_count>0 AND shard_count>0 AND status='published'))
);
CREATE INDEX idx_collector_qv_manifest_project
 ON collector_qv_manifests(project_id,published_at DESC);
CREATE INDEX idx_collector_qv_manifest_source
 ON collector_qv_manifests(generation_id,project_id);

-- Shards only reference REAL immutable collector_query_sets from migration 054.
CREATE TABLE collector_query_shards (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 manifest_id UUID NOT NULL,
 project_id TEXT NOT NULL,
 query_set_id UUID NOT NULL,
 shard_ordinal INTEGER NOT NULL CHECK(shard_ordinal BETWEEN 0 AND 479),
 query_count INTEGER NOT NULL CHECK(query_count BETWEEN 1 AND 50),
 shard_sha256 TEXT NOT NULL CHECK(shard_sha256 ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(manifest_id,project_id) REFERENCES collector_qv_manifests(id,project_id),
 FOREIGN KEY(query_set_id,project_id) REFERENCES collector_query_sets(id,project_id),
 UNIQUE(manifest_id,shard_ordinal),
 UNIQUE(manifest_id,query_set_id),
 UNIQUE(id,manifest_id,project_id),
 UNIQUE(id,project_id)
);
CREATE INDEX idx_collector_query_shards_scope
 ON collector_query_shards(project_id,manifest_id,shard_ordinal);

-- Each selected candidate is assigned to exactly one shard, never truncated.
-- A QV UUID is never cast into the legacy D1 search_queries.id BIGINT.
CREATE TABLE collector_manifest_queries (
 manifest_id UUID NOT NULL,
 project_id TEXT NOT NULL,
 validation_run_id UUID NOT NULL,
 shard_id UUID NOT NULL,
 candidate_id UUID NOT NULL,
 hypothesis_id TEXT NOT NULL CHECK(length(hypothesis_id) BETWEEN 1 AND 128),
 direction_id TEXT NOT NULL CHECK(length(direction_id) BETWEEN 1 AND 128),
 rank_in_hypothesis INTEGER NOT NULL CHECK(rank_in_hypothesis BETWEEN 1 AND 16),
 ordinal_in_shard INTEGER NOT NULL CHECK(ordinal_in_shard BETWEEN 0 AND 49),
 source_payload JSONB NOT NULL CHECK(jsonb_typeof(source_payload)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(manifest_id,candidate_id),
 FOREIGN KEY(manifest_id,project_id,validation_run_id)
  REFERENCES collector_qv_manifests(id,project_id,validation_run_id),
 FOREIGN KEY(shard_id,manifest_id,project_id)
  REFERENCES collector_query_shards(id,manifest_id,project_id),
 FOREIGN KEY(validation_run_id,candidate_id)
  REFERENCES qv_decisions(run_id,candidate_id),
 UNIQUE(shard_id,ordinal_in_shard),
 UNIQUE(project_id,validation_run_id,candidate_id)
);
CREATE INDEX idx_collector_manifest_queries_hyp
 ON collector_manifest_queries(project_id,hypothesis_id,manifest_id);
CREATE INDEX idx_collector_manifest_queries_shard
 ON collector_manifest_queries(shard_id,ordinal_in_shard);

-- Preserve reasons for hypotheses with no selected queries.
CREATE TABLE collector_manifest_coverage (
 manifest_id UUID NOT NULL,
 project_id TEXT NOT NULL,
 validation_run_id UUID NOT NULL,
 hypothesis_id TEXT NOT NULL CHECK(length(hypothesis_id) BETWEEN 1 AND 128),
 source_count INTEGER NOT NULL CHECK(source_count BETWEEN 0 AND 16),
 selected_count INTEGER NOT NULL CHECK(selected_count BETWEEN 0 AND 16),
 outcome TEXT NOT NULL CHECK(outcome IN ('selected','no_source_candidates','all_rejected')),
 reason TEXT,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(manifest_id,hypothesis_id),
 FOREIGN KEY(manifest_id,project_id,validation_run_id)
  REFERENCES collector_qv_manifests(id,project_id,validation_run_id),
 FOREIGN KEY(validation_run_id,hypothesis_id)
  REFERENCES qv_coverage(run_id,hypothesis_id),
 CHECK(selected_count <= source_count),
 CHECK ((outcome='selected' AND selected_count>0 AND reason IS NULL)
    OR (outcome='no_source_candidates' AND source_count=0
        AND selected_count=0 AND length(btrim(coalesce(reason,'')))>0)
    OR (outcome='all_rejected' AND source_count>0
        AND selected_count=0 AND length(btrim(coalesce(reason,'')))>0))
);
CREATE INDEX idx_collector_manifest_coverage_project
 ON collector_manifest_coverage(project_id,manifest_id,outcome);

-- Existing collector_immutable_record trigger function is in 054.
CREATE TRIGGER collector_qv_manifests_immutable BEFORE UPDATE OR DELETE
 ON collector_qv_manifests FOR EACH ROW EXECUTE FUNCTION collector_immutable_record();
CREATE TRIGGER collector_query_shards_immutable BEFORE UPDATE OR DELETE
 ON collector_query_shards FOR EACH ROW EXECUTE FUNCTION collector_immutable_record();
CREATE TRIGGER collector_manifest_queries_immutable BEFORE UPDATE OR DELETE
 ON collector_manifest_queries FOR EACH ROW EXECUTE FUNCTION collector_immutable_record();
CREATE TRIGGER collector_manifest_coverage_immutable BEFORE UPDATE OR DELETE
 ON collector_manifest_coverage FOR EACH ROW EXECUTE FUNCTION collector_immutable_record();

COMMENT ON TABLE collector_qv_manifests IS
 'Exactly one immutable publication per production QV run/outbox; 0 selected means no_queries with no shards. Publisher transaction also ACKs QV source.';
COMMENT ON TABLE collector_manifest_queries IS
 'Versioned UUID candidate provenance; does not write legacy D1 BIGINT query foreign keys.';
