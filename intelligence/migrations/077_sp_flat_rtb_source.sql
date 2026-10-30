-- Add a THIRD, explicitly typed SP source for immutable flat RTB v1.
-- Preserve Deep C9 and RTB Deep V2 read and payment guards unchanged.
-- No migration rewrites any research run, old SP record or quote.
ALTER TABLE sqg_generations
  ADD COLUMN flat_rtb_source BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE sqg_quotes
  ADD COLUMN flat_rtb_source BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE sqg_consents
  ADD COLUMN flat_rtb_source BOOLEAN NOT NULL DEFAULT FALSE;

ALTER TABLE sqg_generations ADD CONSTRAINT sqg_flat_generation_source_check
 CHECK (NOT flat_rtb_source OR
   (publication_id IS NOT NULL AND rtb_run_id IS NOT NULL
    AND rtb_v2_publication_id IS NULL AND rtb_v2_run_id IS NULL));
ALTER TABLE sqg_quotes ADD CONSTRAINT sqg_flat_quote_source_check
 CHECK (NOT flat_rtb_source OR
   (publication_id IS NOT NULL AND rtb_v2_publication_id IS NULL));
ALTER TABLE sqg_consents ADD CONSTRAINT sqg_flat_consent_source_check
 CHECK (NOT flat_rtb_source OR
   (publication_id IS NOT NULL AND rtb_v2_publication_id IS NULL));

-- Keep the existing SQL function including all original Deep/V2 verification.
CREATE OR REPLACE FUNCTION sqg_require_deep_publication() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_TABLE_NAME = 'sqg_consents' THEN
   -- Binding cannot cross modes, project, publication or quote after approval.
   IF NEW.flat_rtb_source AND NOT EXISTS (
     SELECT 1 FROM sqg_quotes q WHERE q.id=NEW.quote_id
       AND q.project_id=NEW.project_id AND q.flat_rtb_source
       AND q.publication_id=NEW.publication_id
       AND q.rtb_v2_publication_id IS NULL
   ) THEN RAISE EXCEPTION 'SQG: mismatched flat RTB consent/quote'; END IF;
   IF NOT NEW.flat_rtb_source AND EXISTS (
     SELECT 1 FROM sqg_quotes q WHERE q.id=NEW.quote_id
       AND q.project_id=NEW.project_id AND q.flat_rtb_source
   ) THEN RAISE EXCEPTION 'SQG: flat RTB consent mode mismatch'; END IF;
   IF NEW.rtb_v2_publication_id IS NOT NULL AND NOT EXISTS (
     SELECT 1 FROM sqg_quotes q WHERE q.id=NEW.quote_id
       AND q.project_id=NEW.project_id
       AND q.rtb_v2_publication_id=NEW.rtb_v2_publication_id
       AND q.publication_id IS NULL
   ) THEN RAISE EXCEPTION 'SQG: mismatched V2 consent/quote'; END IF;
   RETURN NEW;
 END IF;
 IF NEW.flat_rtb_source THEN
   -- Never claim a flat RTB publication is a human-reviewed Deep C9.
   IF NEW.publication_id IS NULL OR NEW.rtb_v2_publication_id IS NOT NULL
      OR NOT EXISTS (
     SELECT 1 FROM tb_taxonomy_publications p
     JOIN tb_runs r ON r.id=p.run_id AND r.project_id=p.project_id
     JOIN tb_fact_review_submissions f ON f.run_id=p.run_id AND f.project_id=p.project_id
     JOIN tb_sre_taxonomy_links l ON l.builder_publication_id=p.id
       AND l.run_id=p.run_id AND l.project_id=p.project_id
     JOIN sre_taxonomy_versions v ON v.id=l.sre_taxonomy_version_id
       AND v.project_id=p.project_id
     WHERE p.project_id=NEW.project_id AND p.id=NEW.publication_id
       AND r.status='approved' AND p.approved_by='local_owner'
       AND f.reviewed_by='local_owner' AND v.status='approved'
       AND l.source_sha256=v.snapshot_checksum
       AND jsonb_array_length(p.approved_topics)>0
       AND jsonb_array_length(f.approved_facts)>0
       AND (TG_TABLE_NAME='sqg_quotes' OR
            (p.run_id=(to_jsonb(NEW)->>'rtb_run_id')::uuid
             AND p.taxonomy_version=(to_jsonb(NEW)->>'taxonomy_version')::integer))
   ) THEN RAISE EXCEPTION 'SQG: unapproved or mismatched flat RTB publication'; END IF;
   IF TG_TABLE_NAME='sqg_generations' THEN
      IF NEW.quote_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM sqg_quotes q WHERE q.id=NEW.quote_id
          AND q.project_id=NEW.project_id AND q.publication_id=NEW.publication_id
          AND q.config_id=NEW.config_id AND q.flat_rtb_source
      ) THEN RAISE EXCEPTION 'SQG: mismatched flat RTB quote'; END IF;
      IF NEW.consent_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM sqg_consents c WHERE c.id=NEW.consent_id
          AND c.project_id=NEW.project_id AND c.publication_id=NEW.publication_id
          AND c.quote_id=NEW.quote_id AND c.flat_rtb_source
      ) THEN RAISE EXCEPTION 'SQG: mismatched flat RTB consent'; END IF;
   END IF;
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

-- Existing BEFORE INSERT / UPDATE guards use this replaced function.
-- Also guard attempts to flip the source type after a run has been created.
CREATE TRIGGER sqg_generation_flat_mode_update_guard
  BEFORE UPDATE OF flat_rtb_source ON sqg_generations
  FOR EACH ROW EXECUTE FUNCTION sqg_require_deep_publication();
CREATE TRIGGER sqg_consent_flat_mode_update_guard
  BEFORE UPDATE OF flat_rtb_source ON sqg_consents
  FOR EACH ROW EXECUTE FUNCTION sqg_require_deep_publication();
