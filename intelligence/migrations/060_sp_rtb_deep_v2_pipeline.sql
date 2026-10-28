-- Additive RTB Deep V2 -> Search Planner generation/quote/consent binding.
-- Preserve V1 rows and their existing foreign keys; never fabricate V1
-- publications. Historical applied migrations must remain immutable.
ALTER TABLE sqg_generations
  ALTER COLUMN publication_id DROP NOT NULL,
  ALTER COLUMN rtb_run_id DROP NOT NULL,
  ADD COLUMN rtb_v2_publication_id UUID,
  ADD COLUMN rtb_v2_run_id UUID,
  ADD COLUMN rtb_v2_export_sha256 TEXT;
ALTER TABLE sqg_quotes
  ALTER COLUMN publication_id DROP NOT NULL,
  ADD COLUMN rtb_v2_publication_id UUID;
ALTER TABLE sqg_consents
  ALTER COLUMN publication_id DROP NOT NULL,
  ADD COLUMN rtb_v2_publication_id UUID;

ALTER TABLE sqg_generations ADD CONSTRAINT sqg_generation_source_mode CHECK (
  (publication_id IS NOT NULL AND rtb_run_id IS NOT NULL
   AND rtb_v2_publication_id IS NULL AND rtb_v2_run_id IS NULL
   AND rtb_v2_export_sha256 IS NULL)
  OR
  (publication_id IS NULL AND rtb_run_id IS NULL
   AND rtb_v2_publication_id IS NOT NULL AND rtb_v2_run_id IS NOT NULL
   AND rtb_v2_export_sha256 ~ '^[0-9a-f]{64}$')
);
ALTER TABLE sqg_quotes ADD CONSTRAINT sqg_quote_source_mode CHECK (
  (publication_id IS NOT NULL AND rtb_v2_publication_id IS NULL)
  OR (publication_id IS NULL AND rtb_v2_publication_id IS NOT NULL)
);
ALTER TABLE sqg_consents ADD CONSTRAINT sqg_consent_source_mode CHECK (
  (publication_id IS NOT NULL AND rtb_v2_publication_id IS NULL)
  OR (publication_id IS NULL AND rtb_v2_publication_id IS NOT NULL)
);
ALTER TABLE sqg_generations ADD CONSTRAINT sqg_gen_v2_pub_fk
 FOREIGN KEY (project_id,rtb_v2_publication_id)
 REFERENCES tb_deep_v2_publications(project_id,id);
ALTER TABLE sqg_quotes ADD CONSTRAINT sqg_quote_v2_pub_fk
 FOREIGN KEY (project_id,rtb_v2_publication_id)
 REFERENCES tb_deep_v2_publications(project_id,id);
ALTER TABLE sqg_consents ADD CONSTRAINT sqg_consent_v2_pub_fk
 FOREIGN KEY (project_id,rtb_v2_publication_id)
 REFERENCES tb_deep_v2_publications(project_id,id);
ALTER TABLE sqg_generations ADD CONSTRAINT sqg_gen_v2_run_fk
 FOREIGN KEY (project_id,rtb_v2_run_id)
 REFERENCES tb_deep_v2_runs(project_id,id);
CREATE INDEX idx_sqg_generations_v2_pub
 ON sqg_generations(project_id,rtb_v2_publication_id)
 WHERE rtb_v2_publication_id IS NOT NULL;

-- Run V2 checks through the same existing trigger. The original V1
-- function body is reproduced below without weakening its predicates.
CREATE OR REPLACE FUNCTION sqg_require_deep_publication() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME = 'sqg_consents' THEN
   IF NEW.rtb_v2_publication_id IS NOT NULL AND NOT EXISTS (
     SELECT 1 FROM sqg_quotes q WHERE q.id=NEW.quote_id
       AND q.project_id=NEW.project_id
       AND q.rtb_v2_publication_id=NEW.rtb_v2_publication_id
       AND q.publication_id IS NULL
   ) THEN RAISE EXCEPTION 'SQG: mismatched V2 consent/quote'; END IF;
   RETURN NEW;
 END IF;
 IF NEW.rtb_v2_publication_id IS NOT NULL THEN
   IF NOT EXISTS (
      SELECT 1 FROM tb_deep_v2_publications p
      JOIN tb_deep_v2_runs r ON r.id=p.run_id AND r.project_id=p.project_id
      WHERE p.id=NEW.rtb_v2_publication_id AND p.project_id=NEW.project_id
        AND r.status='published'
        AND (SELECT COUNT(*) FROM tb_deep_v2_reviews rv
             WHERE rv.project_id=p.project_id AND rv.run_id=p.run_id)=2
        AND EXISTS (SELECT 1 FROM tb_deep_v2_reviews rv
             WHERE rv.project_id=p.project_id AND rv.run_id=p.run_id
               AND rv.review_no=2 AND rv.approved_nodes=p.v2_tree
               AND rv.approved_sha256=p.v2_tree_sha256)
   ) THEN RAISE EXCEPTION 'SQG: unpublished or unapproved V2 source'; END IF;
   IF TG_TABLE_NAME = 'sqg_generations' THEN
     IF NOT EXISTS (
       SELECT 1 FROM tb_deep_v2_publications p
       WHERE p.id=NEW.rtb_v2_publication_id AND p.project_id=NEW.project_id
         AND p.run_id=NEW.rtb_v2_run_id
         AND p.sre_taxonomy_version_id=NEW.taxonomy_version
     ) THEN RAISE EXCEPTION 'SQG: V2 run/taxonomy mismatch'; END IF;
     IF NEW.quote_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM sqg_quotes q
       WHERE q.id=NEW.quote_id AND q.project_id=NEW.project_id
         AND q.rtb_v2_publication_id=NEW.rtb_v2_publication_id
         AND q.config_id=NEW.config_id
     ) THEN RAISE EXCEPTION 'SQG: V2 quote mismatch'; END IF;
     IF NEW.consent_id IS NOT NULL AND NOT EXISTS (
       SELECT 1 FROM sqg_consents c
       WHERE c.id=NEW.consent_id AND c.project_id=NEW.project_id
         AND c.rtb_v2_publication_id=NEW.rtb_v2_publication_id
         AND c.quote_id=NEW.quote_id
     ) THEN RAISE EXCEPTION 'SQG: V2 consent mismatch'; END IF;
   END IF;
   RETURN NEW;
 END IF;
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

-- Re-evaluate V2 identity when quote/consent binding changes.
DROP TRIGGER sqg_generation_publication_guard ON sqg_generations;
CREATE TRIGGER sqg_generation_publication_guard BEFORE INSERT OR UPDATE OF
 project_id,publication_id,rtb_run_id,rtb_v2_publication_id,rtb_v2_run_id,
 taxonomy_version,config_id,quote_id,consent_id
 ON sqg_generations FOR EACH ROW EXECUTE FUNCTION sqg_require_deep_publication();
CREATE TRIGGER sqg_consent_v2_publication_guard BEFORE INSERT ON sqg_consents
 FOR EACH ROW EXECUTE FUNCTION sqg_require_deep_publication();
