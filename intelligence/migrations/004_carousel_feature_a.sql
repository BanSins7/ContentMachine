-- ContentMachine A4: carousel / still-image support, additive only.
-- User-approved formats: video, slideshow_video, photo_carousel,
-- mixed_carousel, image_post. Legacy videos.content_kind stays nullable;
-- a D1 URL import is NOT sufficient proof of the precise media format.
-- Videos and slideshow_video keep using Video Feature A migration 003.
-- Photo/mixed/image analyses use independent ordered items below.

CREATE TABLE IF NOT EXISTS carousel_feature_a_analyses (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id),
  source_policy_version_id BIGINT REFERENCES source_policy_versions(id) ON DELETE SET NULL,
  analysis_key TEXT NOT NULL UNIQUE CHECK (length(analysis_key) BETWEEN 1 AND 250),
  analysis_version TEXT NOT NULL CHECK (length(analysis_version) BETWEEN 1 AND 80),
  method_name TEXT NOT NULL CHECK (length(method_name) BETWEEN 1 AND 120),
  method_version TEXT,
  model_name TEXT,
  model_version TEXT,
  content_kind TEXT NOT NULL
    CHECK (content_kind IN ('photo_carousel','mixed_carousel','image_post')),
  state TEXT NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending','partial','completed','failed')),
  slide_status TEXT NOT NULL DEFAULT 'not_run'
    CHECK (slide_status IN ('not_run','complete','partial','unavailable')),
  text_status TEXT NOT NULL DEFAULT 'not_run'
    CHECK (text_status IN ('not_run','complete','partial','unavailable')),
  audio_status TEXT NOT NULL DEFAULT 'not_run'
    CHECK (audio_status IN ('not_run','complete','partial','unavailable')),
  has_audio TEXT NOT NULL DEFAULT 'unknown'
    CHECK (has_audio IN ('true','false','unknown')),
  audio_details JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(audio_details) = 'object'),
  cta_slide_index INTEGER CHECK (cta_slide_index IS NULL OR cta_slide_index >= 1),
  cta_evidence JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(cta_evidence) = 'object'),
  measurement_methods JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(measurement_methods) = 'object'),
  unavailable_reasons JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(unavailable_reasons) = 'object'),
  measured_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_carousel_a_video_time
  ON carousel_feature_a_analyses(video_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_carousel_a_source_time
  ON carousel_feature_a_analyses(source_id, created_at DESC);

-- One row per ordered slide. A mixed carousel can contain image or video.
-- No slide duration is ever inferred from time a person spends swiping.
-- The optional linked A003 analysis describes just this video slide;
-- its segment timestamps are relative to the slide's media asset.
CREATE TABLE IF NOT EXISTS carousel_feature_a_items (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  analysis_id BIGINT NOT NULL REFERENCES carousel_feature_a_analyses(id) ON DELETE CASCADE,
  slide_index INTEGER NOT NULL CHECK (slide_index >= 1),
  item_type TEXT NOT NULL CHECK (item_type IN ('image','video')),
  media_asset_id BIGINT REFERENCES media_assets(id) ON DELETE SET NULL,
  video_a_analysis_id BIGINT REFERENCES video_feature_a_analyses(id) ON DELETE SET NULL,
  aspect_ratio TEXT CHECK (
    aspect_ratio IS NULL OR aspect_ratio ~ '^[1-9][0-9]*:[1-9][0-9]*$'
  ),
  duration_sec NUMERIC(12,3)
    CHECK (duration_sec IS NULL OR duration_sec > 0),
  text_status TEXT NOT NULL DEFAULT 'not_run'
    CHECK (text_status IN ('not_run','complete','partial','unavailable')),
  on_screen_text TEXT,
  screen_word_count INTEGER CHECK (screen_word_count IS NULL OR screen_word_count >= 0),
  ocr_method TEXT,
  ocr_confidence_pct NUMERIC(5,2)
    CHECK (ocr_confidence_pct IS NULL OR ocr_confidence_pct BETWEEN 0 AND 100),
  unavailable_reasons JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(unavailable_reasons) = 'object'),
  UNIQUE (analysis_id, slide_index),
  CHECK (item_type = 'video' OR (duration_sec IS NULL AND video_a_analysis_id IS NULL)),
  CHECK (text_status <> 'complete' OR
    (on_screen_text IS NOT NULL AND screen_word_count IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS idx_carousel_a_items_order
  ON carousel_feature_a_items(analysis_id, slide_index);

-- Prevent unauthorized analysis, mismatched platform media, stale evidence,
-- and claiming a carousel is completed before its ordered slides exist.
CREATE OR REPLACE FUNCTION enforce_carousel_a_analysis()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  item_count INTEGER;
  last_index INTEGER;
  images INTEGER;
  clips INTEGER;
  observed_kind TEXT;
BEGIN
  IF TG_OP = 'UPDATE' AND OLD.state = 'completed' THEN
    RAISE EXCEPTION 'Completed carousel analyses are immutable; create a new version'
      USING ERRCODE='23514';
  END IF;

  IF NOT EXISTS (
    SELECT 1 FROM sources
    WHERE id = NEW.source_id AND can_analyze_media = TRUE AND status = 'enabled'
  ) THEN
    RAISE EXCEPTION 'Carousel media analysis not permitted by source: %', NEW.source_id
      USING ERRCODE='23514';
  END IF;

  IF NEW.source_policy_version_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM source_policy_versions
    WHERE id = NEW.source_policy_version_id AND source_id = NEW.source_id
  ) THEN
    RAISE EXCEPTION 'Source policy version belongs to a different source'
      USING ERRCODE='23514';
  END IF;

  SELECT content_kind INTO observed_kind FROM videos WHERE id = NEW.video_id;
  IF observed_kind IS NOT NULL AND observed_kind <> NEW.content_kind THEN
    RAISE EXCEPTION 'Publication media kind conflicts with verified video metadata'
      USING ERRCODE='23514';
  END IF;

  IF NEW.state = 'completed' THEN
    SELECT count(*), coalesce(max(slide_index), 0),
           count(*) FILTER (WHERE item_type = 'image'),
           count(*) FILTER (WHERE item_type = 'video')
    INTO item_count, last_index, images, clips
    FROM carousel_feature_a_items WHERE analysis_id = NEW.id;

    IF NEW.slide_status <> 'complete' OR item_count <> last_index THEN
      RAISE EXCEPTION 'Completed carousel requires all consecutively numbered slides'
        USING ERRCODE='23514';
    END IF;
    IF (NEW.content_kind = 'image_post' AND NOT
        (item_count = 1 AND images = 1))
       OR (NEW.content_kind = 'photo_carousel' AND NOT
        (item_count >= 2 AND images = item_count))
       OR (NEW.content_kind = 'mixed_carousel' AND NOT
        (item_count >= 2 AND images >= 1 AND clips >= 1)) THEN
      RAISE EXCEPTION 'Slide types/count do not match publication kind'
        USING ERRCODE='23514';
    END IF;
    IF NEW.cta_slide_index IS NOT NULL AND NEW.cta_slide_index > last_index THEN
      RAISE EXCEPTION 'CTA slide is outside the observed carousel'
        USING ERRCODE='23514';
    END IF;
    IF NEW.text_status = 'complete' AND EXISTS (
      SELECT 1 FROM carousel_feature_a_items
      WHERE analysis_id = NEW.id AND text_status <> 'complete'
    ) THEN
      RAISE EXCEPTION 'Complete text status requires all slide texts complete'
        USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_carousel_a_analysis
  BEFORE INSERT OR UPDATE ON carousel_feature_a_analyses
  FOR EACH ROW EXECUTE FUNCTION enforce_carousel_a_analysis();

CREATE OR REPLACE FUNCTION enforce_carousel_a_item()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  parent_row carousel_feature_a_analyses%ROWTYPE;
BEGIN
  IF TG_OP = 'DELETE' THEN
    SELECT * INTO parent_row FROM carousel_feature_a_analyses
    WHERE id = OLD.analysis_id;
    IF NOT FOUND THEN
      RETURN OLD; -- Parent removed via ON DELETE CASCADE.
    END IF;
    IF parent_row.state = 'completed' THEN
      RAISE EXCEPTION 'Completed carousel slides are immutable'
        USING ERRCODE='23514';
    END IF;
    RETURN OLD;
  END IF;

  SELECT * INTO parent_row FROM carousel_feature_a_analyses
  WHERE id = NEW.analysis_id;
  IF parent_row.state = 'completed' THEN
    RAISE EXCEPTION 'Completed carousel slides are immutable'
      USING ERRCODE='23514';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM sources
    WHERE id=parent_row.source_id AND can_analyze_media=TRUE AND status='enabled'
  ) THEN
    RAISE EXCEPTION 'Source media-analysis rights were revoked'
      USING ERRCODE='23514';
  END IF;

  IF TG_OP = 'UPDATE' AND OLD.analysis_id <> NEW.analysis_id THEN
    RAISE EXCEPTION 'Cannot move a slide between analyses'
      USING ERRCODE='23514';
  END IF;

  IF NEW.media_asset_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM media_assets
    WHERE id = NEW.media_asset_id
      AND video_id = parent_row.video_id AND source_id = parent_row.source_id
      AND permission <> 'metadata_only'
      AND (expires_at IS NULL OR expires_at > now())
  ) THEN
    RAISE EXCEPTION 'Slide media asset is unavailable or not authorized for analysis'
      USING ERRCODE='23514';
  END IF;

  IF NEW.video_a_analysis_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM video_feature_a_analyses va
    WHERE va.id = NEW.video_a_analysis_id
      AND va.video_id = parent_row.video_id
      AND va.source_id = parent_row.source_id
      AND (va.media_asset_id IS NULL OR va.media_asset_id = NEW.media_asset_id)
  ) THEN
    RAISE EXCEPTION 'Video segment analysis must belong to this publication and slide media'
      USING ERRCODE='23514';
  END IF;

  RETURN NEW;
END;
$$;

CREATE OR REPLACE TRIGGER trg_carousel_a_item
  BEFORE INSERT OR UPDATE OR DELETE ON carousel_feature_a_items
  FOR EACH ROW EXECUTE FUNCTION enforce_carousel_a_item();

-- No fake average time per slide, no FPS/resolution, and no text guessed
-- for unprocessed slides. Text counts are only aggregated when all slides
-- were measured; a measured empty slide contributes actual zero.
CREATE OR REPLACE VIEW carousel_feature_a_summary AS
SELECT
  a.id AS analysis_id, a.video_id, a.source_id, a.analysis_version,
  a.content_kind, a.state, a.measured_at,
  a.slide_status, a.text_status, a.audio_status, a.has_audio,
  CASE WHEN a.slide_status IN ('complete','partial') THEN
    (SELECT count(*) FROM carousel_feature_a_items i WHERE i.analysis_id = a.id)
  ELSE NULL END AS slide_count,
  CASE WHEN a.text_status = 'complete' AND a.slide_status = 'complete' THEN
    (SELECT min(i.slide_index) FROM carousel_feature_a_items i
       WHERE i.analysis_id = a.id AND i.text_status IN ('complete','partial')
         AND length(trim(coalesce(i.on_screen_text, ''))) > 0)
  ELSE NULL END AS first_text_slide,
  CASE WHEN a.text_status = 'complete' AND a.slide_status = 'complete'
       AND NOT EXISTS (
         SELECT 1 FROM carousel_feature_a_items i
         WHERE i.analysis_id = a.id
           AND (i.text_status <> 'complete' OR i.screen_word_count IS NULL)
       )
    THEN (SELECT coalesce(sum(i.screen_word_count),0)
          FROM carousel_feature_a_items i WHERE i.analysis_id=a.id)
  ELSE NULL END AS total_screen_word_count,
  a.cta_slide_index
FROM carousel_feature_a_analyses a;
