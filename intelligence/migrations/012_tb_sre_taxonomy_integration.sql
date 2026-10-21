-- Block 7: exact association between human-published Builder and SRE classifier taxonomy.
-- All active SRE categories are derived atomically from the immutable Builder publication.
-- Inference cannot produce a second independent "approved" taxonomy.
ALTER TABLE sre_facts DROP CONSTRAINT IF EXISTS sre_facts_kind_check;
ALTER TABLE sre_facts ADD CONSTRAINT sre_facts_kind_check
  CHECK(kind IN ('function','problem','audience','product_type'));

CREATE TABLE tb_sre_taxonomy_links (
  project_id TEXT NOT NULL,
  run_id UUID PRIMARY KEY REFERENCES tb_runs(id),
  builder_publication_id UUID NOT NULL UNIQUE REFERENCES tb_taxonomy_publications(id),
  sre_taxonomy_version_id BIGINT NOT NULL UNIQUE,
  source_sha256 TEXT NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(sre_taxonomy_version_id,project_id)
    REFERENCES sre_taxonomy_versions(id,project_id),
  UNIQUE(project_id,run_id)
);

CREATE TRIGGER tb_sre_links_immutable BEFORE UPDATE OR DELETE ON tb_sre_taxonomy_links
 FOR EACH ROW EXECUTE FUNCTION tb_immutable_snapshot();
