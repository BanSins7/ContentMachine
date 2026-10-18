-- Video Feature Data A: additive, opt-in schema only. NO analyzer or network requests.
-- Captures the agreed 14 A features, excluding avg_scene_sec, width/height and FPS.
-- video_id uses the canonical videos.id shared with Discovery.
-- Analyses are immutable-by-convention versioned observations; repeated analysis
-- creates a new analysis_key instead of overwriting an earlier result.

CREATE TABLE IF NOT EXISTS video_feature_a_analyses (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  media_asset_id BIGINT REFERENCES media_assets(id) ON DELETE SET NULL,
  source_id TEXT NOT NULL REFERENCES sources(id),
  source_policy_version_id BIGINT REFERENCES source_policy_versions(id) ON DELETE SET NULL,
  analysis_key TEXT NOT NULL UNIQUE CHECK (length(analysis_key) BETWEEN 1 AND 250),
  analysis_version TEXT NOT NULL CHECK (length(analysis_version) BETWEEN 1 AND 80),
  method_name TEXT NOT NULL CHECK (length(method_name) BETWEEN 1 AND 120),
  method_version TEXT,
  model_name TEXT,
  model_version TEXT,
  state TEXT NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending','partial','completed','failed')),
  scene_status TEXT NOT NULL DEFAULT 'not_run'
    CHECK (scene_status IN ('not_run','complete','partial','unavailable')),
  speech_status TEXT NOT NULL DEFAULT 'not_run'
    CHECK (speech_status IN ('not_run','complete','partial','unavailable')),
  screen_text_status TEXT NOT NULL DEFAULT 'not_run'
    CHECK (screen_text_status IN ('not_run','complete','partial','unavailable')),
  audio_status TEXT NOT NULL DEFAULT 'not_run'
    CHECK (audio_status IN ('not_run','complete','partial','unavailable')),
  measured_at TIMESTAMPTZ,
  duration_sec NUMERIC(12,3) CHECK (duration_sec IS NULL OR duration_sec > 0),
  aspect_ratio TEXT CHECK (aspect_ratio IS NULL OR aspect_ratio ~ '^[1-9][0-9]*:[1-9][0-9]*$'),
  spoken_words INTEGER CHECK (spoken_words IS NULL OR spoken_words >= 0),
  speech_duration_sec NUMERIC(12,3)
    CHECK (speech_duration_sec IS NULL OR speech_duration_sec >= 0),
  has_subtitles TEXT NOT NULL DEFAULT 'unknown'
    CHECK (has_subtitles IN ('true','false','unknown')),
  cta_start_sec NUMERIC(12,3) CHECK (cta_start_sec IS NULL OR cta_start_sec >= 0),
  cta_evidence JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(cta_evidence) = 'object'),
  measurement_methods JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(measurement_methods) = 'object'),
  unavailable_reasons JSONB NOT NULL DEFAULT '{}'::jsonb
    CHECK (jsonb_typeof(unavailable_reasons) = 'object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CHECK (duration_sec IS NULL OR speech_duration_sec IS NULL OR speech_duration_sec <= duration_sec),
  CHECK (duration_sec IS NULL OR cta_start_sec IS NULL OR cta_start_sec <= duration_sec)
);
CREATE INDEX IF NOT EXISTS idx_feature_a_video_time
  ON video_feature_a_analyses(video_id, created_at DESC, id DESC);
CREATE INDEX IF NOT EXISTS idx_feature_a_source_time
  ON video_feature_a_analyses(source_id, created_at DESC);

-- Technical scene boundaries, not semantic roles. Semantic roles belong to B.
CREATE TABLE IF NOT EXISTS video_feature_a_scenes (
  analysis_id BIGINT NOT NULL REFERENCES video_feature_a_analyses(id) ON DELETE CASCADE,
  scene_index INTEGER NOT NULL CHECK (scene_index >= 1),
  start_sec NUMERIC(12,3) NOT NULL CHECK (start_sec >= 0),
  end_sec NUMERIC(12,3) NOT NULL,
  measurement_method TEXT,
  confidence_pct NUMERIC(5,2)
    CHECK (confidence_pct IS NULL OR confidence_pct BETWEEN 0 AND 100),
  PRIMARY KEY (analysis_id, scene_index),
  CHECK (end_sec > start_sec)
);
CREATE INDEX IF NOT EXISTS idx_feature_a_scenes_timeline
  ON video_feature_a_scenes(analysis_id, start_sec);

-- Phrase/word-level text + timestamp observations; speech WPM uses the measured
-- total speech_duration_sec, not the full video duration or segment gaps.
CREATE TABLE IF NOT EXISTS video_feature_a_speech_segments (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  analysis_id BIGINT NOT NULL REFERENCES video_feature_a_analyses(id) ON DELETE CASCADE,
  start_sec NUMERIC(12,3) NOT NULL CHECK (start_sec >= 0),
  end_sec NUMERIC(12,3) NOT NULL,
  spoken_text TEXT NOT NULL,
  method_name TEXT,
  confidence_pct NUMERIC(5,2)
    CHECK (confidence_pct IS NULL OR confidence_pct BETWEEN 0 AND 100),
  CHECK (end_sec > start_sec)
);
CREATE INDEX IF NOT EXISTS idx_feature_a_speech_timeline
  ON video_feature_a_speech_segments(analysis_id, start_sec, id);

-- OCR-derived text appearing on the screen: separate from speech transcript.
CREATE TABLE IF NOT EXISTS video_feature_a_screen_text (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  analysis_id BIGINT NOT NULL REFERENCES video_feature_a_analyses(id) ON DELETE CASCADE,
  start_sec NUMERIC(12,3) NOT NULL CHECK (start_sec >= 0),
  end_sec NUMERIC(12,3) NOT NULL,
  screen_text TEXT NOT NULL,
  method_name TEXT,
  confidence_pct NUMERIC(5,2)
    CHECK (confidence_pct IS NULL OR confidence_pct BETWEEN 0 AND 100),
  CHECK (end_sec > start_sec)
);
CREATE INDEX IF NOT EXISTS idx_feature_a_screen_text_timeline
  ON video_feature_a_screen_text(analysis_id, start_sec, id);

-- Optional, lower-priority music/SFX event windows (not subjective mood labels).
CREATE TABLE IF NOT EXISTS video_feature_a_audio_events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  analysis_id BIGINT NOT NULL REFERENCES video_feature_a_analyses(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL CHECK (event_type IN ('music','sound_effect','other')),
  start_sec NUMERIC(12,3) NOT NULL CHECK (start_sec >= 0),
  end_sec NUMERIC(12,3) NOT NULL,
  label TEXT,
  method_name TEXT,
  confidence_pct NUMERIC(5,2)
    CHECK (confidence_pct IS NULL OR confidence_pct BETWEEN 0 AND 100),
  CHECK (end_sec > start_sec)
);
CREATE INDEX IF NOT EXISTS idx_feature_a_audio_timeline
  ON video_feature_a_audio_events(analysis_id, start_sec, id);

-- Summary is computed rather than duplicating scene_count, first_text_sec,
-- first_speech_sec, speech_wpm, or speech_coverage_pct in mutable columns.
-- Missing/unrun observations remain NULL (not falsely zero).
CREATE OR REPLACE VIEW video_feature_a_summary AS
SELECT
  a.id AS analysis_id, a.video_id, a.media_asset_id, a.source_id,
  a.analysis_version, a.state, a.measured_at, a.duration_sec, a.aspect_ratio,
  CASE WHEN a.scene_status IN ('complete','partial') THEN
    (SELECT count(*) FROM video_feature_a_scenes s WHERE s.analysis_id=a.id)
  ELSE NULL END AS scene_count,
  a.scene_status, a.spoken_words, a.speech_duration_sec,
  CASE WHEN a.spoken_words IS NOT NULL AND a.speech_duration_sec > 0 THEN
    round(a.spoken_words * 60.0 / a.speech_duration_sec, 2)
  ELSE NULL END AS speech_wpm,
  a.has_subtitles,
  CASE WHEN a.duration_sec > 0 AND a.speech_duration_sec IS NOT NULL THEN
    round(100.0 * a.speech_duration_sec / a.duration_sec, 2)
  ELSE NULL END AS speech_coverage_pct,
  CASE WHEN a.screen_text_status IN ('complete','partial') THEN
    (SELECT min(t.start_sec) FROM video_feature_a_screen_text t WHERE t.analysis_id=a.id)
  ELSE NULL END AS first_text_sec,
  CASE WHEN a.speech_status IN ('complete','partial') THEN
    (SELECT min(t.start_sec) FROM video_feature_a_speech_segments t WHERE t.analysis_id=a.id)
  ELSE NULL END AS first_speech_sec,
  a.cta_start_sec, a.speech_status, a.screen_text_status, a.audio_status
FROM video_feature_a_analyses a;

-- Never allow media-based analysis on a source whose media-analysis right is
-- disabled. The D1 manual metadata source is denied by default. Media_asset
-- linkage must refer to the same canonical video as the analysis.
CREATE OR REPLACE FUNCTION enforce_video_feature_a_rights()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM sources
                 WHERE id=NEW.source_id AND can_analyze_media=TRUE AND status='enabled') THEN
    RAISE EXCEPTION 'Media analysis not permitted by source policy: %', NEW.source_id
      USING ERRCODE='23514';
  END IF;
  IF NEW.media_asset_id IS NOT NULL AND
    NOT EXISTS (SELECT 1 FROM media_assets
                WHERE id=NEW.media_asset_id AND video_id=NEW.video_id) THEN
    RAISE EXCEPTION 'Media asset must reference the analyzed video'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END;
$$;
DROP TRIGGER IF EXISTS trg_feature_a_source_rights ON video_feature_a_analyses;
CREATE TRIGGER trg_feature_a_source_rights
  BEFORE INSERT OR UPDATE OF source_id, video_id, media_asset_id
  ON video_feature_a_analyses
  FOR EACH ROW EXECUTE FUNCTION enforce_video_feature_a_rights();
