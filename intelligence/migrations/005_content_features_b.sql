-- Group B (B1-B8): source-first schema based on the 8 approved TXT specs, 2026-10-18.
-- This is an ADDITIVE database contract. No AI/media processing or rights changes.
-- Existing videos.id is the common publication identity; never fork the catalog.
-- content_type follows B2's TWO types. image_post has no B2 contract yet.
-- Existing videos.content_kind remains the more specific A-side media identity.

CREATE OR REPLACE FUNCTION b_valid_labels(labels TEXT[], allowed TEXT[])
RETURNS BOOLEAN LANGUAGE SQL IMMUTABLE AS $$
  SELECT labels IS NOT NULL AND array_position(labels, NULL) IS NULL
     AND labels <@ allowed
     AND cardinality(labels) = (SELECT count(DISTINCT value) FROM unnest(labels) AS value);
$$;

CREATE TABLE IF NOT EXISTS content_b_analyses (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id),
  source_policy_version_id BIGINT REFERENCES source_policy_versions(id) ON DELETE SET NULL,
  video_a_analysis_id BIGINT REFERENCES video_feature_a_analyses(id) ON DELETE SET NULL,
  carousel_a_analysis_id BIGINT REFERENCES carousel_feature_a_analyses(id) ON DELETE SET NULL,
  analysis_key TEXT NOT NULL UNIQUE CHECK (length(analysis_key) BETWEEN 1 AND 250),
  analysis_version TEXT NOT NULL CHECK (length(analysis_version) BETWEEN 1 AND 80),
  taxonomy_version TEXT NOT NULL DEFAULT '1.0',
  content_type TEXT NOT NULL CHECK (content_type IN ('video','carousel')),
  method_name TEXT NOT NULL CHECK (length(method_name) BETWEEN 1 AND 120),
  method_version TEXT,
  model_name TEXT,
  model_version TEXT,
  prompt_version TEXT,
  state TEXT NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending','partial','completed','failed')),
  unavailable_reasons JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(unavailable_reasons)='object'),
  method_details JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(method_details)='object'),
  needs_review BOOLEAN NOT NULL DEFAULT FALSE,
  analyzed_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (
    (content_type='video' AND carousel_a_analysis_id IS NULL)
    OR (content_type='carousel' AND video_a_analysis_id IS NULL)
  )
);
CREATE INDEX IF NOT EXISTS idx_b_video_versions ON content_b_analyses(video_id,created_at DESC,id DESC);
CREATE INDEX IF NOT EXISTS idx_b_source_versions ON content_b_analyses(source_id,created_at DESC);

CREATE OR REPLACE FUNCTION b_validate_analysis()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE kind TEXT;
BEGIN
  IF TG_OP='UPDATE' AND OLD.state='completed' THEN
    -- Retention cleanup may clear only foreign references, not content/results.
    IF to_jsonb(NEW)-'video_a_analysis_id'-'carousel_a_analysis_id'
       =to_jsonb(OLD)-'video_a_analysis_id'-'carousel_a_analysis_id'
       AND (NEW.video_a_analysis_id IS NOT DISTINCT FROM OLD.video_a_analysis_id
         OR (OLD.video_a_analysis_id IS NOT NULL AND NEW.video_a_analysis_id IS NULL))
       AND (NEW.carousel_a_analysis_id IS NOT DISTINCT FROM OLD.carousel_a_analysis_id
         OR (OLD.carousel_a_analysis_id IS NOT NULL AND NEW.carousel_a_analysis_id IS NULL))
    THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'Completed B analyses cannot be overwritten; create another analysis_key'
      USING ERRCODE='23514';
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM sources WHERE id=NEW.source_id AND status='enabled'
      AND can_analyze_media=TRUE
  ) THEN
    RAISE EXCEPTION 'B media analysis not allowed by source policy' USING ERRCODE='23514';
  END IF;
  IF NEW.source_policy_version_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM source_policy_versions
    WHERE id=NEW.source_policy_version_id AND source_id=NEW.source_id
  ) THEN
    RAISE EXCEPTION 'B policy version belongs to another source' USING ERRCODE='23514';
  END IF;
  SELECT content_kind INTO kind FROM videos WHERE id=NEW.video_id;
  IF kind IS NOT NULL AND (
    (NEW.content_type='video' AND kind NOT IN ('video','slideshow_video'))
    OR
    (NEW.content_type='carousel' AND kind NOT IN ('photo_carousel','mixed_carousel'))
  ) THEN
    RAISE EXCEPTION 'B content_type contradicts publication content_kind' USING ERRCODE='23514';
  END IF;
  IF NEW.video_a_analysis_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM video_feature_a_analyses
    WHERE id=NEW.video_a_analysis_id AND video_id=NEW.video_id AND source_id=NEW.source_id
  ) THEN
    RAISE EXCEPTION 'B video A evidence belongs to another source/publication' USING ERRCODE='23514';
  END IF;
  IF NEW.carousel_a_analysis_id IS NOT NULL AND NOT EXISTS (
    SELECT 1 FROM carousel_feature_a_analyses
    WHERE id=NEW.carousel_a_analysis_id AND video_id=NEW.video_id AND source_id=NEW.source_id
  ) THEN
    RAISE EXCEPTION 'B carousel A evidence belongs to another source/publication' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END;
$$;
CREATE OR REPLACE TRIGGER trg_b_validate_analysis
  BEFORE INSERT OR UPDATE ON content_b_analyses
  FOR EACH ROW EXECUTE FUNCTION b_validate_analysis();

-- B1 Hook: ONE hook object, multiple independent mechanisms and delivery channels.
-- Preserve verbatim voice/visual text, 5 nullable form flags and ordered parts.
CREATE TABLE IF NOT EXISTS content_b_hooks (
  analysis_id BIGINT PRIMARY KEY REFERENCES content_b_analyses(id) ON DELETE CASCADE,
  start_sec NUMERIC(12,3) CHECK(start_sec IS NULL OR start_sec>=0),
  end_sec NUMERIC(12,3) CHECK(end_sec IS NULL OR end_sec>=0),
  slide_index INTEGER CHECK(slide_index IS NULL OR slide_index>=1),
  spoken_text TEXT,
  screen_text TEXT,
  visual_description TEXT,
  mechanisms TEXT[] NOT NULL DEFAULT '{}',
  is_question BOOLEAN,
  has_number BOOLEAN,
  direct_address BOOLEAN,
  has_negation BOOLEAN,
  is_command BOOLEAN,
  delivery TEXT[] NOT NULL DEFAULT '{}',
  classification_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK(classification_status IN ('inferred','unknown')),
  taxonomy_version TEXT NOT NULL DEFAULT '1.0',
  CHECK(end_sec IS NULL OR start_sec IS NULL OR end_sec>start_sec),
  CHECK(b_valid_labels(mechanisms,ARRAY[
    'problem','promise','curiosity','myth_busting','mistake','surprise',
    'story','challenge','comparison','proof']::TEXT[])),
  CHECK(b_valid_labels(delivery,ARRAY['speech','on_screen_text','visual_action','sound']::TEXT[]))
);
CREATE TABLE IF NOT EXISTS content_b_hook_parts (
  analysis_id BIGINT NOT NULL REFERENCES content_b_hooks(analysis_id) ON DELETE CASCADE,
  part_index INTEGER NOT NULL CHECK(part_index>=1),
  start_sec NUMERIC(12,3) CHECK(start_sec IS NULL OR start_sec>=0),
  end_sec NUMERIC(12,3) CHECK(end_sec IS NULL OR end_sec>=0),
  slide_index INTEGER CHECK(slide_index IS NULL OR slide_index>=1),
  verbatim_text TEXT,
  mechanisms TEXT[] NOT NULL DEFAULT '{}',
  PRIMARY KEY(analysis_id,part_index),
  CHECK(end_sec IS NULL OR start_sec IS NULL OR end_sec>start_sec),
  CHECK(b_valid_labels(mechanisms,ARRAY[
    'problem','promise','curiosity','myth_busting','mistake','surprise',
    'story','challenge','comparison','proof']::TEXT[]))
);

-- B2: 12 video formats, 8 carousel formats, exactly one primary video format.
CREATE TABLE IF NOT EXISTS content_b_formats (
  analysis_id BIGINT PRIMARY KEY REFERENCES content_b_analyses(id) ON DELETE CASCADE,
  video_primary TEXT CHECK(video_primary IS NULL OR video_primary IN (
    'talking_head','dialogue_interview','skit','demonstration','product_showcase',
    'screen_recording','pov','reaction','voiceover_montage',
    'animation_motion_graphics','slideshow_video','vlog_bts')),
  video_secondary TEXT CHECK(video_secondary IS NULL OR video_secondary IN (
    'talking_head','dialogue_interview','skit','demonstration','product_showcase',
    'screen_recording','pov','reaction','voiceover_montage',
    'animation_motion_graphics','slideshow_video','vlog_bts')),
  carousel_format TEXT CHECK(carousel_format IS NULL OR carousel_format IN (
    'explainer','listicle','step_by_step','comparison','storytelling',
    'case_study','product_catalog','visual_gallery')),
  classification_status TEXT NOT NULL DEFAULT 'unknown'
    CHECK(classification_status IN ('inferred','unknown','needs_review')),
  taxonomy_version TEXT NOT NULL DEFAULT '1.0',
  CHECK(video_primary IS NULL OR video_secondary IS DISTINCT FROM video_primary),
  CHECK(video_primary IS NOT NULL OR video_secondary IS NULL),
  CHECK(carousel_format IS NULL OR (video_primary IS NULL AND video_secondary IS NULL))
);

-- B3: One of exactly 26 roles per *sampled* frame, slide, or meaning interval.
-- Scene cuts in Group A are NOT semantic boundaries and are never role labels.
CREATE TABLE IF NOT EXISTS content_b_story_sampling (
  analysis_id BIGINT PRIMARY KEY REFERENCES content_b_analyses(id) ON DELETE CASCADE,
  first_5_sec_fps NUMERIC(5,2) NOT NULL DEFAULT 2 CHECK(first_5_sec_fps>0),
  remaining_fps NUMERIC(5,2) NOT NULL DEFAULT 1 CHECK(remaining_fps>0),
  max_sampled_frames_target INTEGER NOT NULL DEFAULT 60 CHECK(max_sampled_frames_target>0),
  extra_at_scene_cuts BOOLEAN NOT NULL DEFAULT TRUE,
  extra_at_text_changes BOOLEAN NOT NULL DEFAULT TRUE,
  sampling_notes JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK(jsonb_typeof(sampling_notes)='object')
);
CREATE TABLE IF NOT EXISTS content_b_story_frames (
  analysis_id BIGINT NOT NULL REFERENCES content_b_analyses(id) ON DELETE CASCADE,
  frame_index INTEGER NOT NULL CHECK(frame_index>=1),
  time_sec NUMERIC(12,3) CHECK(time_sec IS NULL OR time_sec>=0),
  sampled_frame_ref TEXT,
  role TEXT NOT NULL CHECK(role IN (
    'hook','context','problem','consequence','goal','myth','mistake','cause',
    'explanation','fact','tip','step','demonstration','comparison','example',
    'proof','testimonial','product_intro','product_demo','before_after',
    'result','reveal','summary','offer','cta','other')),
  before_after_phase TEXT CHECK(before_after_phase IS NULL OR before_after_phase IN ('before','after','both')),
  needs_review BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY(analysis_id,frame_index),
  CHECK(role='before_after' OR before_after_phase IS NULL),
  CHECK(role<>'other' OR needs_review)
);
CREATE TABLE IF NOT EXISTS content_b_story_intervals (
  analysis_id BIGINT NOT NULL REFERENCES content_b_analyses(id) ON DELETE CASCADE,
  interval_index INTEGER NOT NULL CHECK(interval_index>=1),
  start_sec NUMERIC(12,3) CHECK(start_sec IS NULL OR start_sec>=0),
  end_sec NUMERIC(12,3) CHECK(end_sec IS NULL OR end_sec>=0),
  role TEXT NOT NULL CHECK(role IN (
    'hook','context','problem','consequence','goal','myth','mistake','cause',
    'explanation','fact','tip','step','demonstration','comparison','example',
    'proof','testimonial','product_intro','product_demo','before_after',
    'result','reveal','summary','offer','cta','other')),
  before_after_phase TEXT CHECK(before_after_phase IS NULL OR before_after_phase IN ('before','after','both')),
  boundary_quality TEXT NOT NULL DEFAULT 'unknown'
    CHECK(boundary_quality IN ('known','estimated','unknown')),
  needs_review BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY(analysis_id,interval_index),
  CHECK(end_sec IS NULL OR start_sec IS NULL OR end_sec>start_sec),
  CHECK(role='before_after' OR before_after_phase IS NULL),
  CHECK(role<>'other' OR needs_review)
);
CREATE TABLE IF NOT EXISTS content_b_story_slides (
  analysis_id BIGINT NOT NULL REFERENCES content_b_analyses(id) ON DELETE CASCADE,
  slide_index INTEGER NOT NULL CHECK(slide_index>=1),
  role TEXT NOT NULL CHECK(role IN (
    'hook','context','problem','consequence','goal','myth','mistake','cause',
    'explanation','fact','tip','step','demonstration','comparison','example',
    'proof','testimonial','product_intro','product_demo','before_after',
    'result','reveal','summary','offer','cta','other')),
  before_after_phase TEXT CHECK(before_after_phase IS NULL OR before_after_phase IN ('before','after','both')),
  screen_text TEXT,
  visual_description TEXT,
  needs_review BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY(analysis_id,slide_index),
  CHECK(role='before_after' OR before_after_phase IS NULL),
  CHECK(role<>'other' OR needs_review)
);
CREATE OR REPLACE VIEW content_b_story_patterns AS
SELECT a.id AS analysis_id,a.video_id,a.content_type,
  CASE WHEN a.content_type='video'
    THEN (SELECT array_agg(i.role ORDER BY i.interval_index)
          FROM content_b_story_intervals i WHERE i.analysis_id=a.id)
    ELSE (SELECT array_agg(s.role ORDER BY s.slide_index)
          FROM content_b_story_slides s WHERE s.analysis_id=a.id)
  END AS story_pattern
FROM content_b_analyses a;

-- B4: exactly four fields. AI origin is NULL unless verified, NOT inferred from appearance.
CREATE TABLE IF NOT EXISTS content_b_visual_content (
  analysis_id BIGINT PRIMARY KEY REFERENCES content_b_analyses(id) ON DELETE CASCADE,
  ai_generated BOOLEAN,
  person_visible BOOLEAN NOT NULL,
  product_visible BOOLEAN NOT NULL,
  logo_visible BOOLEAN NOT NULL
);

-- B5: four types + FOUR quantitative fields from the TXT. Do NOT delete
-- avg_shot_duration: user's B5 source explicitly includes it.
CREATE TABLE IF NOT EXISTS content_b_editing (
  analysis_id BIGINT PRIMARY KEY REFERENCES content_b_analyses(id) ON DELETE CASCADE,
  editing_type TEXT NOT NULL CHECK(editing_type IN (
    'single_take','slow_cuts','medium_cuts','fast_cuts')),
  cut_count INTEGER NOT NULL CHECK(cut_count>=0),
  cuts_per_minute NUMERIC(12,4) NOT NULL CHECK(cuts_per_minute>=0),
  avg_shot_duration NUMERIC(12,4) NOT NULL CHECK(avg_shot_duration>0),
  cuts_first_5_sec INTEGER NOT NULL CHECK(cuts_first_5_sec>=0 AND cuts_first_5_sec<=cut_count),
  CHECK (
    (editing_type='single_take' AND cut_count=0 AND cuts_per_minute=0)
    OR (editing_type='slow_cuts' AND cut_count>0 AND cuts_per_minute>0 AND cuts_per_minute<10)
    OR (editing_type='medium_cuts' AND cuts_per_minute>=10 AND cuts_per_minute<30)
    OR (editing_type='fast_cuts' AND cuts_per_minute>=30)
  )
);

-- B6: exactly three subtitle types and seven style properties.
CREATE TABLE IF NOT EXISTS content_b_subtitles (
  analysis_id BIGINT PRIMARY KEY REFERENCES content_b_analyses(id) ON DELETE CASCADE,
  subtitle_type TEXT NOT NULL CHECK(subtitle_type IN ('none','standard','dynamic')),
  position TEXT CHECK(position IS NULL OR position IN ('top','center','bottom')),
  font_size TEXT CHECK(font_size IS NULL OR font_size IN ('small','medium','large')),
  font_style TEXT CHECK(font_style IS NULL OR font_style IN ('regular','bold','extra_bold')),
  text_color TEXT CHECK(text_color IS NULL OR text_color ~ '^#[0-9A-Fa-f]{6}$'),
  highlight_color TEXT CHECK(highlight_color IS NULL OR highlight_color ~ '^#[0-9A-Fa-f]{6}$'),
  background_style TEXT CHECK(background_style IS NULL OR background_style IN ('none','outline','shadow','box')),
  display_mode TEXT CHECK(display_mode IS NULL OR display_mode IN ('line','phrase','word_by_word')),
  CHECK(
    (subtitle_type='none' AND position IS NULL AND font_size IS NULL
     AND font_style IS NULL AND text_color IS NULL AND highlight_color IS NULL
     AND background_style IS NULL AND display_mode IS NULL)
    OR (subtitle_type IN ('standard','dynamic') AND position IS NOT NULL
      AND font_size IS NOT NULL AND font_style IS NOT NULL AND text_color IS NOT NULL
      AND background_style IS NOT NULL AND display_mode IS NOT NULL)
  )
);

-- B7: exactly 12 CTA goals. Separate analysis state from occurrences (0..N).
CREATE TABLE IF NOT EXISTS content_b_cta_analyses (
  analysis_id BIGINT PRIMARY KEY REFERENCES content_b_analyses(id) ON DELETE CASCADE,
  analysis_status TEXT NOT NULL DEFAULT 'not_run'
    CHECK(analysis_status IN ('not_run','complete','partial','failed')),
  taxonomy_version TEXT NOT NULL DEFAULT '1.0',
  analyzed_at TIMESTAMPTZ,
  model_version TEXT
);
CREATE TABLE IF NOT EXISTS content_b_ctas (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  analysis_id BIGINT NOT NULL REFERENCES content_b_cta_analyses(analysis_id) ON DELETE CASCADE,
  occurrence_index INTEGER NOT NULL CHECK(occurrence_index>=1),
  cta_type TEXT NOT NULL CHECK(cta_type IN (
    'follow','like','comment','share','save','profile_visit','link_click',
    'app_install','purchase','sign_up','dm','other')),
  text TEXT NOT NULL CHECK(length(btrim(text))>0),
  spoken_text TEXT,
  screen_text TEXT,
  delivery TEXT[] NOT NULL CHECK(
    cardinality(delivery) BETWEEN 1 AND 2
    AND b_valid_labels(delivery,ARRAY['speech','on_screen_text']::TEXT[])),
  start_sec NUMERIC(12,3) CHECK(start_sec IS NULL OR start_sec>=0),
  end_sec NUMERIC(12,3) CHECK(end_sec IS NULL OR end_sec>=0),
  slide_index INTEGER CHECK(slide_index IS NULL OR slide_index>=1),
  destination_type TEXT NOT NULL CHECK(destination_type IN (
    'current_post','profile','link_in_bio','website','app_store',
    'comments','dm','other','unknown')),
  destination_service TEXT CHECK(destination_service IS NULL OR destination_service IN (
    'tiktok','instagram','youtube','telegram','whatsapp','apple_app_store',
    'google_play','other')),
  destination_url TEXT,
  destination_handle TEXT,
  destination_app_name TEXT,
  destination_keyword TEXT,
  destination_other_desc TEXT,
  extraction_status TEXT NOT NULL DEFAULT 'detected'
    CHECK(extraction_status IN ('detected','needs_review')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(analysis_id,occurrence_index),
  CHECK(end_sec IS NULL OR start_sec IS NULL OR end_sec>start_sec),
  CHECK(destination_type='other' OR destination_other_desc IS NULL)
);
CREATE INDEX IF NOT EXISTS idx_b_ctas_type ON content_b_ctas(cta_type);
CREATE INDEX IF NOT EXISTS idx_b_ctas_destination ON content_b_ctas(destination_type);
CREATE INDEX IF NOT EXISTS idx_b_ctas_slide ON content_b_ctas(analysis_id,slide_index);
CREATE INDEX IF NOT EXISTS idx_b_ctas_time ON content_b_ctas(analysis_id,start_sec);
CREATE OR REPLACE VIEW content_b_cta_summary AS
SELECT a.analysis_id,a.analysis_status,
  CASE WHEN a.analysis_status='complete' THEN EXISTS(
    SELECT 1 FROM content_b_ctas c WHERE c.analysis_id=a.analysis_id)
  ELSE NULL END AS has_cta,
  (SELECT count(*) FROM content_b_ctas c WHERE c.analysis_id=a.analysis_id) AS detected_occurrences
FROM content_b_cta_analyses a;

-- B8: hierarchical semantic topic registry, SEPARATE from flat D1 search topics.
-- Stable IDs survive renaming. Merges are explicit and require review.
CREATE TABLE IF NOT EXISTS content_b_categories (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE CHECK(slug ~ '^[a-z][a-z0-9_]{0,119}$'),
  name TEXT NOT NULL CHECK(length(btrim(name))>0),
  merged_into_id BIGINT REFERENCES content_b_categories(id) ON DELETE RESTRICT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK(merged_into_id IS NULL OR merged_into_id<>id)
);
CREATE TABLE IF NOT EXISTS content_b_topics (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  category_id BIGINT NOT NULL REFERENCES content_b_categories(id) ON DELETE RESTRICT,
  slug TEXT NOT NULL CHECK(slug ~ '^[a-z][a-z0-9_]{0,119}$'),
  name TEXT NOT NULL CHECK(length(btrim(name))>0),
  merged_into_id BIGINT REFERENCES content_b_topics(id) ON DELETE RESTRICT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(category_id,slug),
  UNIQUE(id,category_id),
  CHECK(merged_into_id IS NULL OR merged_into_id<>id)
);
CREATE TABLE IF NOT EXISTS content_b_subtopics (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  topic_id BIGINT NOT NULL REFERENCES content_b_topics(id) ON DELETE RESTRICT,
  slug TEXT NOT NULL CHECK(slug ~ '^[a-z][a-z0-9_]{0,119}$'),
  name TEXT NOT NULL CHECK(length(btrim(name))>0),
  merged_into_id BIGINT REFERENCES content_b_subtopics(id) ON DELETE RESTRICT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(topic_id,slug),
  UNIQUE(id,topic_id),
  CHECK(merged_into_id IS NULL OR merged_into_id<>id)
);
CREATE TABLE IF NOT EXISTS content_b_topic_assignments (
  analysis_id BIGINT PRIMARY KEY REFERENCES content_b_analyses(id) ON DELETE CASCADE,
  category_id BIGINT NOT NULL REFERENCES content_b_categories(id) ON DELETE RESTRICT,
  topic_id BIGINT NOT NULL,
  subtopic_id BIGINT,
  taxonomy_version TEXT NOT NULL DEFAULT '1.0',
  needs_review BOOLEAN NOT NULL DEFAULT FALSE,
  FOREIGN KEY(topic_id,category_id)
    REFERENCES content_b_topics(id,category_id) ON DELETE RESTRICT,
  FOREIGN KEY(subtopic_id,topic_id)
    REFERENCES content_b_subtopics(id,topic_id) ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_b_topic_category ON content_b_topic_assignments(category_id);
CREATE INDEX IF NOT EXISTS idx_b_topic_topic ON content_b_topic_assignments(topic_id);
CREATE INDEX IF NOT EXISTS idx_b_topic_subtopic ON content_b_topic_assignments(subtopic_id);

-- Cross-block placement: video timestamps vs carousel slide numbers are distinct.
-- 'none' for missing CTA is deliberately NOT a CTA row.
CREATE OR REPLACE FUNCTION b_validate_component()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
  aid BIGINT;
  parent_type TEXT;
  parent_state TEXT;
  parent_source TEXT;
  parent_carousel_a_id BIGINT;
BEGIN
  IF TG_OP='DELETE' THEN aid:=OLD.analysis_id; ELSE aid:=NEW.analysis_id; END IF;

  SELECT a.content_type,a.state,a.source_id,a.carousel_a_analysis_id
    INTO parent_type,parent_state,parent_source,parent_carousel_a_id
  FROM content_b_analyses a WHERE a.id=aid;
  IF NOT FOUND THEN RETURN CASE WHEN TG_OP='DELETE' THEN OLD ELSE NEW END; END IF;

  IF parent_state='completed' THEN
    RAISE EXCEPTION 'Completed B analysis components are immutable; create new analysis'
      USING ERRCODE='23514';
  END IF;
  IF TG_OP<>'DELETE' AND NOT EXISTS(
    SELECT 1 FROM sources
    WHERE id=parent_source AND status='enabled' AND can_analyze_media=TRUE
  ) THEN
    RAISE EXCEPTION 'B media rights have been revoked' USING ERRCODE='23514';
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;

  IF TG_TABLE_NAME IN ('content_b_story_frames','content_b_story_intervals',
                       'content_b_story_sampling','content_b_editing','content_b_subtitles')
     AND parent_type<>'video' THEN
    RAISE EXCEPTION 'This B classifier is video-only' USING ERRCODE='23514';
  END IF;
  IF TG_TABLE_NAME='content_b_story_slides' AND parent_type<>'carousel' THEN
    RAISE EXCEPTION 'B3 slide roles require a carousel' USING ERRCODE='23514';
  END IF;
  IF TG_TABLE_NAME='content_b_formats' THEN
    IF parent_type='video' AND (NEW.carousel_format IS NOT NULL OR
         (NEW.classification_status='inferred' AND NEW.video_primary IS NULL))
       OR parent_type='carousel' AND (NEW.video_primary IS NOT NULL OR
           NEW.video_secondary IS NOT NULL OR
           (NEW.classification_status='inferred' AND NEW.carousel_format IS NULL)) THEN
      RAISE EXCEPTION 'B2 format conflicts with content type' USING ERRCODE='23514';
    END IF;
  END IF;
  IF TG_TABLE_NAME IN ('content_b_hooks','content_b_hook_parts','content_b_ctas') THEN
    IF parent_type='video' AND NEW.slide_index IS NOT NULL THEN
      RAISE EXCEPTION 'Video B positions use seconds, not slide index' USING ERRCODE='23514';
    END IF;
    IF parent_type='carousel' AND
        (NEW.slide_index IS NULL OR NEW.start_sec IS NOT NULL OR NEW.end_sec IS NOT NULL) THEN
      RAISE EXCEPTION 'Carousel B positions require slide index and no time' USING ERRCODE='23514';
    END IF;
  END IF;
  IF TG_TABLE_NAME='content_b_story_slides' AND parent_carousel_a_id IS NOT NULL AND NOT EXISTS(
    SELECT 1 FROM carousel_feature_a_items WHERE analysis_id=parent_carousel_a_id
      AND slide_index=NEW.slide_index
  ) THEN
    RAISE EXCEPTION 'B3 slide index missing from linked A carousel'
      USING ERRCODE='23514';
  END IF;
  IF TG_TABLE_NAME='content_b_ctas' AND NEW.slide_index IS NOT NULL
    AND parent_carousel_a_id IS NOT NULL AND NOT EXISTS(
       SELECT 1 FROM carousel_feature_a_items WHERE analysis_id=parent_carousel_a_id
         AND slide_index=NEW.slide_index
    ) THEN
    RAISE EXCEPTION 'B7 CTA slide index missing from linked A carousel'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END;
$$;
CREATE OR REPLACE TRIGGER trg_b_hooks_guard BEFORE INSERT OR UPDATE OR DELETE
  ON content_b_hooks FOR EACH ROW EXECUTE FUNCTION b_validate_component();
CREATE OR REPLACE TRIGGER trg_b_hook_parts_guard BEFORE INSERT OR UPDATE OR DELETE
  ON content_b_hook_parts FOR EACH ROW EXECUTE FUNCTION b_validate_component();
CREATE OR REPLACE TRIGGER trg_b_formats_guard BEFORE INSERT OR UPDATE OR DELETE
  ON content_b_formats FOR EACH ROW EXECUTE FUNCTION b_validate_component();
CREATE OR REPLACE TRIGGER trg_b_sampling_guard BEFORE INSERT OR UPDATE OR DELETE
  ON content_b_story_sampling FOR EACH ROW EXECUTE FUNCTION b_validate_component();
CREATE OR REPLACE TRIGGER trg_b_frames_guard BEFORE INSERT OR UPDATE OR DELETE
  ON content_b_story_frames FOR EACH ROW EXECUTE FUNCTION b_validate_component();
CREATE OR REPLACE TRIGGER trg_b_intervals_guard BEFORE INSERT OR UPDATE OR DELETE
  ON content_b_story_intervals FOR EACH ROW EXECUTE FUNCTION b_validate_component();
CREATE OR REPLACE TRIGGER trg_b_slides_guard BEFORE INSERT OR UPDATE OR DELETE
  ON content_b_story_slides FOR EACH ROW EXECUTE FUNCTION b_validate_component();
CREATE OR REPLACE TRIGGER trg_b_visual_guard BEFORE INSERT OR UPDATE OR DELETE
  ON content_b_visual_content FOR EACH ROW EXECUTE FUNCTION b_validate_component();
CREATE OR REPLACE TRIGGER trg_b_edit_guard BEFORE INSERT OR UPDATE OR DELETE
  ON content_b_editing FOR EACH ROW EXECUTE FUNCTION b_validate_component();
CREATE OR REPLACE TRIGGER trg_b_subs_guard BEFORE INSERT OR UPDATE OR DELETE
  ON content_b_subtitles FOR EACH ROW EXECUTE FUNCTION b_validate_component();
CREATE OR REPLACE TRIGGER trg_b_cta_analyses_guard BEFORE INSERT OR UPDATE OR DELETE
  ON content_b_cta_analyses FOR EACH ROW EXECUTE FUNCTION b_validate_component();
CREATE OR REPLACE TRIGGER trg_b_ctas_guard BEFORE INSERT OR UPDATE OR DELETE
  ON content_b_ctas FOR EACH ROW EXECUTE FUNCTION b_validate_component();
CREATE OR REPLACE TRIGGER trg_b_topic_assignment_guard BEFORE INSERT OR UPDATE OR DELETE
  ON content_b_topic_assignments FOR EACH ROW EXECUTE FUNCTION b_validate_component();
