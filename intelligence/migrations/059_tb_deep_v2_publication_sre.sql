-- RTB FIX-07: independently owned, immutable Deep V2 publication and SRE link.
-- The legacy tb_taxonomy_publications/tb_sre_taxonomy_links reference tb_runs
-- and MUST NOT be reused by standalone B9-backed Deep V2 runs.

ALTER TABLE tb_deep_v2_runs DROP CONSTRAINT tb_deep_v2_runs_status_check;
ALTER TABLE tb_deep_v2_runs ADD CONSTRAINT tb_deep_v2_runs_status_check
 CHECK(status IN (
 'pass1_pending','pass1_running','review1','pass2_pending','pass2_running',
 'expand_pending','expand_running','review2','approved','published',
 'failed','uncertain','blocked_quality','paused_budget','paused_price','cancelled'
 ));

CREATE TABLE tb_deep_v2_publications (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 binding_id UUID NOT NULL,
 research_input_sha256 TEXT NOT NULL CHECK(research_input_sha256 ~ '^[0-9a-f]{64}$'),
 b9_snapshot_id UUID NOT NULL,
 b9_snapshot_sha256 TEXT NOT NULL CHECK(b9_snapshot_sha256 ~ '^[0-9a-f]{64}$'),
 a_context_id UUID NOT NULL,
 a_context_sha256 TEXT NOT NULL CHECK(a_context_sha256 ~ '^[0-9a-f]{64}$'),
 review2_sha256 TEXT NOT NULL CHECK(review2_sha256 ~ '^[0-9a-f]{64}$'),
 readiness_report_sha256 TEXT NOT NULL CHECK(readiness_report_sha256 ~ '^[0-9a-f]{64}$'),
 v2_tree JSONB NOT NULL CHECK(jsonb_typeof(v2_tree)='array'),
 v2_tree_sha256 TEXT NOT NULL CHECK(v2_tree_sha256 ~ '^[0-9a-f]{64}$'),
 v1_core JSONB NOT NULL CHECK(jsonb_typeof(v1_core)='array'),
 v1_core_sha256 TEXT NOT NULL CHECK(v1_core_sha256 ~ '^[0-9a-f]{64}$'),
 fact_mapping JSONB NOT NULL CHECK(jsonb_typeof(fact_mapping)='object'),
 sre_topics_sha256 TEXT NOT NULL CHECK(sre_topics_sha256 ~ '^[0-9a-f]{64}$'),
 sre_taxonomy_version_id BIGINT NOT NULL,
 published_by TEXT NOT NULL CHECK(length(btrim(published_by)) BETWEEN 1 AND 160),
 published_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 UNIQUE(project_id,id),
 UNIQUE(project_id,run_id),
 UNIQUE(project_id,sre_taxonomy_version_id),
 FOREIGN KEY(project_id,run_id) REFERENCES tb_deep_v2_runs(project_id,id),
 FOREIGN KEY(project_id,binding_id) REFERENCES tb_deep_v2_research_bindings(project_id,id),
 FOREIGN KEY(sre_taxonomy_version_id,project_id)
   REFERENCES sre_taxonomy_versions(id,project_id),
 FOREIGN KEY(project_id,published_by)
   REFERENCES knowledge_fact_review_authorizations(project_id,reviewer_id),
 CHECK(v2_tree_sha256=review2_sha256),
 CHECK(jsonb_array_length(v2_tree)=jsonb_array_length(v1_core)),
 CHECK(jsonb_array_length(v2_tree) BETWEEN 2 AND 48)
);

CREATE TABLE tb_deep_v2_sre_links (
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 publication_id UUID NOT NULL,
 sre_taxonomy_version_id BIGINT NOT NULL,
 source_sha256 TEXT NOT NULL CHECK(source_sha256 ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(project_id,run_id),
 UNIQUE(project_id,publication_id),
 UNIQUE(project_id,sre_taxonomy_version_id),
 FOREIGN KEY(project_id,publication_id) REFERENCES tb_deep_v2_publications(project_id,id),
 FOREIGN KEY(project_id,run_id) REFERENCES tb_deep_v2_runs(project_id,id),
 FOREIGN KEY(sre_taxonomy_version_id,project_id)
   REFERENCES sre_taxonomy_versions(id,project_id)
);

CREATE FUNCTION tb_deep_v2_publication_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r RECORD;
BEGIN
 SELECT status,binding_id,reserved_usd,quality_report INTO r
 FROM tb_deep_v2_runs
 WHERE project_id=NEW.project_id AND id=NEW.run_id FOR UPDATE;
 IF NOT FOUND OR r.status<>'approved' OR r.binding_id<>NEW.binding_id
    OR r.reserved_usd<>0
    OR NOT EXISTS (
      SELECT 1 FROM knowledge_fact_review_authorizations reviewer
      WHERE reviewer.project_id=NEW.project_id
        AND reviewer.reviewer_id=NEW.published_by AND reviewer.enabled=true
    )
    OR NOT EXISTS (
      SELECT 1 FROM tb_deep_v2_reviews h2
      JOIN tb_deep_v2_readiness_reports gate
        ON gate.project_id=h2.project_id AND gate.run_id=h2.run_id
      WHERE h2.project_id=NEW.project_id AND h2.run_id=NEW.run_id
        AND h2.review_no=2 AND h2.approved_sha256=NEW.review2_sha256
        AND h2.approved_nodes=NEW.v2_tree
        AND gate.report_sha256=NEW.readiness_report_sha256
        AND gate.approved_sha256=NEW.review2_sha256
        AND gate.report->>'passed'='true'
        AND gate.report=r.quality_report
    )
    OR NOT EXISTS (
      SELECT 1 FROM tb_deep_v2_research_bindings b
      WHERE b.project_id=NEW.project_id AND b.id=NEW.binding_id
        AND b.knowledge_snapshot_id=NEW.b9_snapshot_id
        AND b.knowledge_snapshot_sha256=NEW.b9_snapshot_sha256
        AND b.context_id=NEW.a_context_id
        AND b.context_sha256=NEW.a_context_sha256
        AND b.research_input_sha256=NEW.research_input_sha256
        AND b.research_input->'b9_fact_sha_to_sre_uuid'=NEW.fact_mapping
    )
    OR NOT EXISTS (
      SELECT 1 FROM sre_taxonomy_versions v
      WHERE v.project_id=NEW.project_id AND v.id=NEW.sre_taxonomy_version_id
        AND v.status='approved'
        AND v.snapshot_checksum=NEW.sre_topics_sha256
    )
 THEN
   RAISE EXCEPTION 'RTB Deep V2 publication lacks immutable H2/source/SRE approval';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tb_deep_v2_publication_insert_guard
 BEFORE INSERT ON tb_deep_v2_publications FOR EACH ROW
 EXECUTE FUNCTION tb_deep_v2_publication_guard();

CREATE FUNCTION tb_deep_v2_sre_link_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM tb_deep_v2_publications pub
   WHERE pub.project_id=NEW.project_id AND pub.id=NEW.publication_id
     AND pub.run_id=NEW.run_id
     AND pub.sre_taxonomy_version_id=NEW.sre_taxonomy_version_id
     AND pub.sre_topics_sha256=NEW.source_sha256
 ) THEN
   RAISE EXCEPTION 'RTB Deep V2 SRE link does not match immutable publication';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER tb_deep_v2_sre_link_insert_guard
 BEFORE INSERT ON tb_deep_v2_sre_links FOR EACH ROW
 EXECUTE FUNCTION tb_deep_v2_sre_link_guard();

CREATE TRIGGER tb_deep_v2_publication_immutable BEFORE UPDATE OR DELETE
 ON tb_deep_v2_publications FOR EACH ROW
 EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER tb_deep_v2_sre_link_immutable BEFORE UPDATE OR DELETE
 ON tb_deep_v2_sre_links FOR EACH ROW
 EXECUTE FUNCTION psi_block_immutable_write();
