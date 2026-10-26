-- M18 standalone SRE: project-scoped, bounded, revocable LOCAL embedding cache.
-- ADDITIVE 057 after 056. No raw video text, no provider/finance/results writes.
-- Cache never authenticates a project or replaces fresh D1 rights checks.

CREATE TABLE sre_local_embedding_cache (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES sre_workspaces(project_id),
  publication_id UUID NOT NULL,
  taxonomy_version_id BIGINT NOT NULL,
  taxonomy_sha256 TEXT NOT NULL CHECK(taxonomy_sha256 ~ '^[0-9a-f]{64}$'),
  contexts_sha256 TEXT NOT NULL CHECK(contexts_sha256 ~ '^[0-9a-f]{64}$'),
  model_identity_sha256 TEXT NOT NULL CHECK(model_identity_sha256 ~ '^[0-9a-f]{64}$'),
  video_id BIGINT NOT NULL,
  content_version_id BIGINT NOT NULL,
  content_sha256 TEXT NOT NULL CHECK(content_sha256 ~ '^[0-9a-f]{64}$'),
  source_id TEXT NOT NULL,
  source_policy_version_id BIGINT NOT NULL,
  input_sha256 TEXT NOT NULL CHECK(input_sha256 ~ '^[0-9a-f]{64}$'),
  vectors_sha256 TEXT NOT NULL CHECK(vectors_sha256 ~ '^[0-9a-f]{64}$'),
  vector_count INTEGER NOT NULL CHECK(vector_count BETWEEN 2 AND 97),
  dimensions INTEGER NOT NULL CHECK(dimensions BETWEEN 1 AND 4096),
  vectors JSONB NOT NULL CHECK (
    jsonb_typeof(vectors)='array'
    AND jsonb_array_length(vectors)=vector_count
    AND octet_length(vectors::text)<=2000000
  ),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  expires_at TIMESTAMPTZ NOT NULL,
  FOREIGN KEY(project_id,publication_id,taxonomy_version_id)
    REFERENCES tb_sre_taxonomy_links(
      project_id,builder_publication_id,sre_taxonomy_version_id),
  FOREIGN KEY(content_version_id,project_id,video_id,source_id,
              source_policy_version_id,content_sha256)
    REFERENCES sre_content_versions(id,project_id,video_id,source_id,
                                    source_policy_version_id,content_sha256),
  UNIQUE(project_id,publication_id,taxonomy_version_id,model_identity_sha256,
         contexts_sha256,content_version_id,content_sha256,input_sha256)
);
CREATE INDEX sre_m18_cache_project_expiry
  ON sre_local_embedding_cache(project_id,expires_at,id);
CREATE INDEX sre_m18_cache_project_source
  ON sre_local_embedding_cache(project_id,content_version_id,source_policy_version_id);

-- This is a disposable cache, not durable evidence. DELETE is intentionally
-- permitted for TTL/right-to-delete/revocation. Input pins and vector bytes
-- may NEVER be altered by UPDATE; replacement requires a new authorized
-- inference and an independently valid cache key.
CREATE FUNCTION sre_m18_immutable_entry() RETURNS trigger
LANGUAGE plpgsql AS $sre_m18_guard$
DECLARE v JSONB;
BEGIN
  IF TG_OP='INSERT' THEN
    IF NEW.expires_at<=clock_timestamp()
       OR NEW.expires_at>clock_timestamp()+interval '24 hours'
    THEN RAISE EXCEPTION 'M18 cache expiry must be future and <= 24h'; END IF;
    FOR v IN SELECT value FROM jsonb_array_elements(NEW.vectors) LOOP
      IF jsonb_typeof(v)<>'array' OR jsonb_array_length(v)<>NEW.dimensions
         OR EXISTS(SELECT 1 FROM jsonb_array_elements(v) x
                   WHERE jsonb_typeof(x.value)<>'number')
      THEN RAISE EXCEPTION 'M18 cache embedding has invalid finite numeric shape'; END IF;
    END LOOP;
  ELSE
    RAISE EXCEPTION 'M18 embedding cache entries are immutable: delete and reauthorize';
  END IF;
  RETURN NEW;
END;
$sre_m18_guard$;
CREATE TRIGGER sre_m18_entry_insert BEFORE INSERT ON sre_local_embedding_cache
  FOR EACH ROW EXECUTE FUNCTION sre_m18_immutable_entry();
CREATE TRIGGER sre_m18_entry_update BEFORE UPDATE ON sre_local_embedding_cache
  FOR EACH ROW EXECUTE FUNCTION sre_m18_immutable_entry();

REVOKE ALL ON sre_local_embedding_cache FROM PUBLIC;
