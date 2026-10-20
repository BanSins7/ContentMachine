-- Classifications are only valid for user-approved categories.
-- Rejected proposal rows remain for audit but must not be classified.
CREATE OR REPLACE FUNCTION sre_require_approved_category_for_result()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM sre_taxonomy_categories c
    JOIN sre_taxonomy_versions v
      ON v.id=c.taxonomy_version_id AND v.project_id=c.project_id
    WHERE c.project_id=NEW.project_id
      AND c.taxonomy_version_id=NEW.taxonomy_version_id
      AND c.category_key=NEW.category_key
      AND c.review_status='approved' AND v.status='approved'
  ) THEN
    RAISE EXCEPTION 'Classification requires a user-approved taxonomy category';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER sre_classification_category_approved
  BEFORE INSERT OR UPDATE ON sre_classification_results
  FOR EACH ROW EXECUTE FUNCTION sre_require_approved_category_for_result();
