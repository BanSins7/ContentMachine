-- QDP accepts genuine SRE M11/M30/M32 LIVE UUID-backed runs without
-- forging legacy sre_classification_runs or BIGINT result identifiers.
-- Additive compatibility: all pre-existing QDP legacy FKs remain effective.
ALTER TABLE qdp_runs ALTER COLUMN sre_run_id DROP NOT NULL;
ALTER TABLE qdp_runs ADD COLUMN sre_live_run_id UUID;
ALTER TABLE qdp_runs ADD CONSTRAINT qdp_modern_sre_run_fk
  FOREIGN KEY (sre_live_run_id,project_id)
  REFERENCES sre_live_runs(run_id,project_id) ON DELETE RESTRICT;
ALTER TABLE qdp_runs ADD CONSTRAINT qdp_exactly_one_sre_source CHECK
  ((sre_run_id IS NOT NULL AND sre_live_run_id IS NULL) OR
   (sre_run_id IS NULL AND sre_live_run_id IS NOT NULL));

ALTER TABLE qdp_item_lineage ALTER COLUMN sre_result_id DROP NOT NULL;
ALTER TABLE qdp_item_lineage ADD COLUMN sre_live_result_id UUID
  REFERENCES sre_live_verified_pair_results(result_id) ON DELETE RESTRICT;
ALTER TABLE qdp_item_lineage ADD COLUMN sre_live_review_id UUID
  REFERENCES sre_live_human_reviews(review_id) ON DELETE RESTRICT;
ALTER TABLE qdp_item_lineage ADD CONSTRAINT qdp_exactly_one_sre_result CHECK
  ((sre_result_id IS NOT NULL AND sre_live_result_id IS NULL
      AND sre_live_review_id IS NULL) OR
   (sre_result_id IS NULL AND sre_live_result_id IS NOT NULL
      AND human_label_id IS NULL AND sre_live_review_id IS NOT NULL));
CREATE INDEX qdp_modern_sre_lineage
 ON qdp_item_lineage(project_id,sre_live_result_id)
 WHERE sre_live_result_id IS NOT NULL;

CREATE OR REPLACE FUNCTION qdp_validate_sre_lineage()
RETURNS trigger LANGUAGE plpgsql AS $qdp_sre_fk$
BEGIN
 IF NEW.sre_live_result_id IS NOT NULL THEN
   IF NOT EXISTS (
     SELECT 1 FROM qdp_runs q
     JOIN sre_live_items item
       ON item.run_id=q.sre_live_run_id AND item.project_id=q.project_id
       AND item.video_id=NEW.video_id
     JOIN sre_live_verified_pair_results result
       ON result.project_id=item.project_id
       AND result.run_id=item.run_id AND result.item_id=item.id
       AND result.category_key=NEW.category_key
     JOIN sre_live_human_reviews review
       ON review.review_id=NEW.sre_live_review_id
       AND review.project_id=result.project_id
       AND review.run_id=result.run_id
       AND review.item_id=result.item_id
       AND review.category_key=result.category_key
       AND review.base_result_id=result.result_id
     WHERE q.id=NEW.run_id AND q.project_id=NEW.project_id
       AND q.taxonomy_version_id=(
         SELECT r.taxonomy_version_id FROM sre_live_runs r
         WHERE r.run_id=q.sre_live_run_id AND r.project_id=q.project_id)
       AND result.result_id=NEW.sre_live_result_id
       AND item.content_version_id=NEW.content_version_id
       AND item.content_sha256=NEW.content_sha256
       AND result.decision=NEW.model_decision
       AND review.human_decision=NEW.effective_decision
   ) THEN
     RAISE EXCEPTION 'Modern SRE M30/M32 fact is not a verified QDP source'
       USING ERRCODE='23503';
   END IF;
 ELSE
   IF NOT EXISTS (
     SELECT 1 FROM qdp_runs q
     JOIN sre_classification_results r
       ON r.run_id=q.sre_run_id AND r.project_id=q.project_id
       AND r.taxonomy_version_id=q.taxonomy_version_id
     WHERE q.id=NEW.run_id AND q.project_id=NEW.project_id
       AND r.id=NEW.sre_result_id AND r.video_id=NEW.video_id
       AND r.category_key=NEW.category_key
       AND r.content_version_id=NEW.content_version_id
   ) THEN
     RAISE EXCEPTION 'Legacy SRE result is not part of the pinned QDP run'
       USING ERRCODE='23503';
   END IF;
   IF NEW.human_label_id IS NOT NULL AND NOT EXISTS (
     SELECT 1 FROM sre_human_labels h JOIN qdp_runs q
       ON q.project_id=h.project_id AND q.taxonomy_version_id=h.taxonomy_version_id
     WHERE q.id=NEW.run_id AND q.project_id=NEW.project_id
       AND h.id=NEW.human_label_id AND h.video_id=NEW.video_id
       AND h.category_key=NEW.category_key
   ) THEN
     RAISE EXCEPTION 'Legacy SRE human review belongs to another input'
       USING ERRCODE='23503';
   END IF;
 END IF;
 RETURN NEW;
END $qdp_sre_fk$;
