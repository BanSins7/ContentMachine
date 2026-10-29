-- GA-MOD-18: additive immutability fence for completed GA18 A003 snapshots.
-- Applies ONLY to GA18 versioned rows, leaving all legacy A003 writers as-is.
-- A003 003/004 objects are not redefined and historical migrations stay intact.

CREATE OR REPLACE FUNCTION ga18_guard_completed_video_parent()
RETURNS trigger LANGUAGE plpgsql AS $ga18_parent$
BEGIN
  IF OLD.analysis_version LIKE 'GA18-%' AND OLD.state='completed' THEN
    -- Retention FK cleanup: allow ONLY an existing pointer being nulled.
    IF to_jsonb(NEW) - 'media_asset_id' - 'source_policy_version_id'
       = to_jsonb(OLD) - 'media_asset_id' - 'source_policy_version_id'
       AND (NEW.media_asset_id IS NOT DISTINCT FROM OLD.media_asset_id
            OR (OLD.media_asset_id IS NOT NULL AND NEW.media_asset_id IS NULL))
       AND (NEW.source_policy_version_id IS NOT DISTINCT FROM OLD.source_policy_version_id
            OR (OLD.source_policy_version_id IS NOT NULL AND NEW.source_policy_version_id IS NULL))
       AND (NEW.media_asset_id IS DISTINCT FROM OLD.media_asset_id
            OR NEW.source_policy_version_id IS DISTINCT FROM OLD.source_policy_version_id)
    THEN
      RETURN NEW;
    END IF;
    RAISE EXCEPTION 'Completed GA18 A003 parent is immutable; write a new analysis'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END;
$ga18_parent$;
DROP TRIGGER IF EXISTS trg_ga18_completed_video_parent ON video_feature_a_analyses;
CREATE TRIGGER trg_ga18_completed_video_parent
  BEFORE UPDATE ON video_feature_a_analyses
  FOR EACH ROW EXECUTE FUNCTION ga18_guard_completed_video_parent();


CREATE OR REPLACE FUNCTION ga18_guard_completed_video_child()
RETURNS trigger LANGUAGE plpgsql AS $ga18_child$
DECLARE parent_version TEXT;
DECLARE parent_state TEXT;
DECLARE old_parent_id BIGINT;
BEGIN
  old_parent_id=CASE WHEN TG_OP='INSERT' THEN NEW.analysis_id ELSE OLD.analysis_id END;
  SELECT analysis_version,state INTO parent_version,parent_state
    FROM video_feature_a_analyses WHERE id=old_parent_id;
  -- Parent absent during legitimate retention ON DELETE CASCADE.
  IF NOT FOUND THEN
    IF TG_OP='DELETE' THEN RETURN OLD; END IF;
    RAISE EXCEPTION 'A003 parent unavailable' USING ERRCODE='23503';
  END IF;
  IF parent_version LIKE 'GA18-%' AND parent_state='completed' THEN
    RAISE EXCEPTION 'Completed GA18 A003 evidence is immutable; version instead'
      USING ERRCODE='23514';
  END IF;
  IF TG_OP='UPDATE' AND NEW.analysis_id<>OLD.analysis_id THEN
    RAISE EXCEPTION 'Cannot relocate A003 segment to another analysis'
      USING ERRCODE='23514';
  END IF;
  IF TG_OP='DELETE' THEN RETURN OLD; END IF;
  RETURN NEW;
END;
$ga18_child$;

DROP TRIGGER IF EXISTS trg_ga18_scene_immutable ON video_feature_a_scenes;
CREATE TRIGGER trg_ga18_scene_immutable
  BEFORE INSERT OR UPDATE OR DELETE ON video_feature_a_scenes
  FOR EACH ROW EXECUTE FUNCTION ga18_guard_completed_video_child();

DROP TRIGGER IF EXISTS trg_ga18_speech_immutable ON video_feature_a_speech_segments;
CREATE TRIGGER trg_ga18_speech_immutable
  BEFORE INSERT OR UPDATE OR DELETE ON video_feature_a_speech_segments
  FOR EACH ROW EXECUTE FUNCTION ga18_guard_completed_video_child();

DROP TRIGGER IF EXISTS trg_ga18_text_immutable ON video_feature_a_screen_text;
CREATE TRIGGER trg_ga18_text_immutable
  BEFORE INSERT OR UPDATE OR DELETE ON video_feature_a_screen_text
  FOR EACH ROW EXECUTE FUNCTION ga18_guard_completed_video_child();

DROP TRIGGER IF EXISTS trg_ga18_audio_immutable ON video_feature_a_audio_events;
CREATE TRIGGER trg_ga18_audio_immutable
  BEFORE INSERT OR UPDATE OR DELETE ON video_feature_a_audio_events
  FOR EACH ROW EXECUTE FUNCTION ga18_guard_completed_video_child();

-- Compatibility view exposes the authoritatively source-pinned checksum and
-- coverage certificate metadata without mutating legacy 003 A-field views.
CREATE OR REPLACE VIEW ga18_video_snapshot_receipts AS
SELECT id AS analysis_id,video_id,media_asset_id,source_id,
       source_policy_version_id,analysis_key,analysis_version,state,
       measured_at,duration_sec,aspect_ratio,
       measurement_methods->>'ga_asset_sha256' AS source_media_sha256,
       measurement_methods->>'ga18_snapshot_sha256' AS snapshot_sha256,
       measurement_methods->'ga18_coverage' AS coverage,
       unavailable_reasons
FROM video_feature_a_analyses
WHERE analysis_version LIKE 'GA18-%';
