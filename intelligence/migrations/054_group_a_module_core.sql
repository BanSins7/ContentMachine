-- GA-MOD-03, isolated forward-only PostgreSQL persistence. No QDP table references.
-- Depends ONLY on main foundation: project_registry, sre_project_videos, videos,
-- sources, source_policy_versions, media_assets, A003 and A004.
-- Do NOT modify/checksum-edit archived 017_group_a_technical_extraction.sql.
-- No downloaded media, fabricated QDP readiness, or live worker activation.

CREATE TABLE IF NOT EXISTS ga_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES project_registry(id) ON DELETE CASCADE
     CHECK (project_id ~ '^[a-f0-9]{32}$'),
  qdp_run_id UUID NOT NULL,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  source_id TEXT NOT NULL REFERENCES sources(id),
  source_policy_version_id BIGINT NOT NULL REFERENCES source_policy_versions(id),
  qdp_input_sha256 TEXT NOT NULL CHECK (qdp_input_sha256 ~ '^[0-9a-f]{64}$'),
  manifest_sha256 TEXT NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
  config_sha256 TEXT NOT NULL CHECK (config_sha256 ~ '^[0-9a-f]{64}$'),
  config JSONB NOT NULL CHECK (jsonb_typeof(config) = 'object'),
  content_kind TEXT NOT NULL CHECK (content_kind IN
    ('video','slideshow_video','photo_carousel','mixed_carousel','image_post')),
  stage_version TEXT NOT NULL DEFAULT 'group-a-assembly/v1'
    CHECK (stage_version = 'group-a-assembly/v1'),
  state TEXT NOT NULL DEFAULT 'queued'
    CHECK (state IN ('queued','running','partial','completed','blocked',
                    'failed','cancelled','invalidated')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 20),
  worker_id TEXT,
  lease_until TIMESTAMPTZ,
  deadline_at TIMESTAMPTZ,
  invalidated_at TIMESTAMPTZ,
  invalidated_reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  FOREIGN KEY (project_id, video_id)
    REFERENCES sre_project_videos(project_id, video_id) ON DELETE CASCADE,
  UNIQUE (id, project_id, video_id, source_id),
  UNIQUE (id, project_id, video_id),
  UNIQUE (id, project_id),
  UNIQUE (project_id,qdp_run_id,video_id,qdp_input_sha256,manifest_sha256,config_sha256),
  CHECK ((worker_id IS NULL) = (lease_until IS NULL)),
  CHECK (lease_until IS NULL OR state='running'),
  CHECK (attempts <= max_attempts),
  CHECK (finished_at IS NULL OR finished_at >= created_at),
  CHECK ((invalidated_at IS NULL) = (invalidated_reason IS NULL)),
  CHECK (state <> 'invalidated' OR invalidated_at IS NOT NULL),
  CHECK (deadline_at IS NULL OR deadline_at > created_at)
);
CREATE INDEX IF NOT EXISTS idx_ga_runs_project_time
  ON ga_runs(project_id, created_at DESC, id);
CREATE INDEX IF NOT EXISTS idx_ga_runs_claim
  ON ga_runs(state, created_at, id) WHERE state IN ('queued','running');
CREATE INDEX IF NOT EXISTS idx_ga_runs_pins ON ga_runs(video_id, qdp_run_id, qdp_input_sha256);

-- Every ordered asset pin belongs to the canonical D1 video/source. Hash/size
-- truth about local bytes is verified by the GA-MOD-05 resolver, NOT the SQL.
CREATE TABLE IF NOT EXISTS ga_media_refs (
  run_id UUID NOT NULL,
  project_id TEXT NOT NULL,
  video_id BIGINT NOT NULL,
  source_id TEXT NOT NULL,
  slide_index INTEGER NOT NULL CHECK (slide_index BETWEEN 1 AND 100),
  media_asset_id BIGINT NOT NULL REFERENCES media_assets(id),
  media_kind TEXT NOT NULL CHECK (media_kind IN ('image','video')),
  asset_sha256 TEXT NOT NULL CHECK (asset_sha256 ~ '^[0-9a-f]{64}$'),
  byte_size BIGINT NOT NULL CHECK (byte_size BETWEEN 1 AND 2147483648),
  verified_at TIMESTAMPTZ NOT NULL,
  PRIMARY KEY(run_id,slide_index),
  UNIQUE (run_id,media_asset_id),
  FOREIGN KEY (run_id,project_id,video_id,source_id)
    REFERENCES ga_runs(id,project_id,video_id,source_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ga_media_refs_asset
  ON ga_media_refs(media_asset_id,run_id);

-- Durable inbox: ACK only after actual committed receipt + job; QDP is NOT
-- integrated yet. Event ID cannot move to another project or different hash.
CREATE TABLE IF NOT EXISTS ga_inbox (
  event_id UUID PRIMARY KEY,
  run_id UUID NOT NULL,
  project_id TEXT NOT NULL,
  qdp_run_id UUID NOT NULL,
  video_id BIGINT NOT NULL,
  input_sha256 TEXT NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
  payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  pointer JSONB NOT NULL CHECK (jsonb_typeof(pointer) = 'object'),
  receipt_state TEXT NOT NULL DEFAULT 'recorded'
    CHECK (receipt_state IN ('recorded','quarantined')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(run_id,project_id,video_id)
    REFERENCES ga_runs(id,project_id,video_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_ga_inbox_run ON ga_inbox(run_id,created_at);

-- Each deterministic step has its own monotonic attempts, lease fences, and
-- deadline. Workers cannot rewrite the identity of a completed step.
CREATE TABLE IF NOT EXISTS ga_steps (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id UUID NOT NULL,
  project_id TEXT NOT NULL,
  step_key TEXT NOT NULL CHECK (length(step_key) BETWEEN 1 AND 120),
  step_kind TEXT NOT NULL CHECK (step_kind IN
    ('technical','frames','audio_decode','vad','asr','speech_metrics','ocr',
     'text_tracking','subtitles','audio_events','slides','mixed_slides','assembly')),
  state TEXT NOT NULL DEFAULT 'queued'
    CHECK (state IN ('queued','running','completed','partial','failed','blocked','cancelled')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
  max_attempts INTEGER NOT NULL DEFAULT 3 CHECK (max_attempts BETWEEN 1 AND 20),
  worker_id TEXT,
  lease_until TIMESTAMPTZ,
  deadline_at TIMESTAMPTZ,
  input_sha256 TEXT NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
  output_sha256 TEXT CHECK (output_sha256 ~ '^[0-9a-f]{64}$'),
  failure_code TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  FOREIGN KEY(run_id,project_id) REFERENCES ga_runs(id,project_id) ON DELETE CASCADE,
  UNIQUE (run_id,step_key),
  CHECK (attempts <= max_attempts),
  CHECK ((worker_id IS NULL) = (lease_until IS NULL)),
  CHECK (lease_until IS NULL OR state='running'),
  CHECK (state <> 'completed' OR (output_sha256 IS NOT NULL AND finished_at IS NOT NULL)),
  CHECK (deadline_at IS NULL OR deadline_at > created_at)
);
CREATE INDEX IF NOT EXISTS idx_ga_steps_ready ON ga_steps(state,created_at)
  WHERE state IN ('queued','running');

-- Stage outputs are append-only versioned facts; completed values never UPDATE.
-- Canonical A003/A004 snapshots are only pointers and must match the run's video.
CREATE TABLE IF NOT EXISTS ga_results (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id UUID NOT NULL,
  project_id TEXT NOT NULL,
  video_id BIGINT NOT NULL,
  source_id TEXT NOT NULL,
  state TEXT NOT NULL CHECK (state IN ('partial','completed')),
  video_a_analysis_id BIGINT REFERENCES video_feature_a_analyses(id) ON DELETE SET NULL,
  carousel_a_analysis_id BIGINT REFERENCES carousel_feature_a_analyses(id) ON DELETE SET NULL,
  result_schema TEXT NOT NULL DEFAULT 'group-a-to-b/v1'
    CHECK (result_schema = 'group-a-to-b/v1'),
  result_sha256 TEXT NOT NULL CHECK (result_sha256 ~ '^[0-9a-f]{64}$'),
  evidence JSONB NOT NULL CHECK (jsonb_typeof(evidence)='array'),
  snapshot JSONB NOT NULL CHECK (jsonb_typeof(snapshot)='object'),
  method_version TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(run_id,result_sha256),
  FOREIGN KEY(run_id,project_id,video_id,source_id)
    REFERENCES ga_runs(id,project_id,video_id,source_id) ON DELETE CASCADE,
  -- Only ONE A003/A004 pointer may exist. If retention deletes the
  -- upstream A analysis both may turn NULL; published facts stay append-only.
  CHECK (video_a_analysis_id IS NULL OR carousel_a_analysis_id IS NULL)
);
CREATE INDEX IF NOT EXISTS idx_ga_results_video ON ga_results(project_id,video_id,created_at DESC);

-- Handoff outbox cannot be published unless a committed result exists. A receipt
-- is not a completed analysis. Delivery mutations never alter pinned payloads.
CREATE TABLE IF NOT EXISTS ga_outbox (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id UUID NOT NULL,
  project_id TEXT NOT NULL,
  result_id BIGINT NOT NULL REFERENCES ga_results(id) ON DELETE CASCADE,
  event_type TEXT NOT NULL DEFAULT 'group_a.result_ready/v1'
    CHECK (event_type = 'group_a.result_ready/v1'),
  payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
  state TEXT NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending','leased','delivered','failed')),
  attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts BETWEEN 0 AND 20),
  worker_id TEXT,
  lease_until TIMESTAMPTZ,
  delivered_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(run_id,project_id) REFERENCES ga_runs(id,project_id) ON DELETE CASCADE,
  UNIQUE(run_id,event_type,payload_sha256),
  CHECK ((worker_id IS NULL) = (lease_until IS NULL)),
  CHECK (lease_until IS NULL OR state='leased'),
  CHECK (state <> 'delivered' OR delivered_at IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS idx_ga_outbox_delivery ON ga_outbox(state,created_at)
  WHERE state IN ('pending','leased','failed');

CREATE TABLE IF NOT EXISTS ga_cache_refs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id UUID NOT NULL,
  project_id TEXT NOT NULL,
  media_asset_id BIGINT NOT NULL REFERENCES media_assets(id),
  cache_kind TEXT NOT NULL CHECK (cache_kind IN
    ('frames','probe','transcript','vision','audio','ocr')),
  input_sha256 TEXT NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
  result_sha256 TEXT NOT NULL CHECK (result_sha256 ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  FOREIGN KEY(run_id,project_id) REFERENCES ga_runs(id,project_id) ON DELETE CASCADE,
  UNIQUE(run_id,media_asset_id,cache_kind,input_sha256),
  CHECK (expires_at IS NULL OR expires_at > created_at)
);

-- Security invariant: project membership and source policy must be true at
-- insertion; never create a second D1 videos table or QDP shadow catalog.
CREATE OR REPLACE FUNCTION ga_run_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE current_kind TEXT;
BEGIN
  IF TG_OP='UPDATE' THEN
    IF (OLD.project_id,OLD.qdp_run_id,OLD.video_id,OLD.source_id,
        OLD.source_policy_version_id,OLD.qdp_input_sha256,OLD.manifest_sha256,
        OLD.config_sha256,OLD.config,OLD.content_kind,OLD.stage_version)
       IS DISTINCT FROM
       (NEW.project_id,NEW.qdp_run_id,NEW.video_id,NEW.source_id,
        NEW.source_policy_version_id,NEW.qdp_input_sha256,NEW.manifest_sha256,
        NEW.config_sha256,NEW.config,NEW.content_kind,NEW.stage_version) THEN
      RAISE EXCEPTION 'GA run identity is immutable' USING ERRCODE='23514';
    END IF;
    IF OLD.state IN ('completed','invalidated','cancelled','blocked')
      AND NEW IS DISTINCT FROM OLD THEN
      -- Invalidation of completed output is an explicit exception.
      IF NOT (OLD.state='completed' AND NEW.state='invalidated'
              AND NEW.invalidated_at IS NOT NULL
              AND NEW.invalidated_reason IS NOT NULL
              AND (to_jsonb(NEW)-'state'-'invalidated_at'-'invalidated_reason'-'updated_at')
                = (to_jsonb(OLD)-'state'-'invalidated_at'-'invalidated_reason'-'updated_at'))
      THEN
        RAISE EXCEPTION 'Terminal GA run is immutable' USING ERRCODE='23514';
      END IF;
    END IF;
    IF NEW.attempts < OLD.attempts THEN
      RAISE EXCEPTION 'GA run attempts cannot decrease' USING ERRCODE='23514';
    END IF;
  END IF;
  IF NOT EXISTS (
    SELECT 1 FROM sre_project_videos pv
    WHERE pv.project_id=NEW.project_id AND pv.video_id=NEW.video_id
  ) THEN RAISE EXCEPTION 'GA video is not in project' USING ERRCODE='23514'; END IF;
  -- Retiring or invalidating a run MUST remain possible after rights are
  -- revoked; continuing processing must not.
  IF (TG_OP='INSERT' OR NEW.state IN ('queued','running','partial','completed')) AND
     NOT EXISTS (
       SELECT 1 FROM sources s JOIN source_policy_versions sp
         ON sp.source_id=s.id
       WHERE s.id=NEW.source_id AND sp.id=NEW.source_policy_version_id
         AND s.status='enabled' AND s.can_analyze_media=TRUE AND sp.is_active=TRUE
         AND sp.permissions->>'can_analyze_media'='true'
     ) THEN
    RAISE EXCEPTION 'GA source policy is not currently enabled' USING ERRCODE='23514';
  END IF;
  SELECT content_kind INTO current_kind FROM videos WHERE id=NEW.video_id;
  IF current_kind IS NOT NULL AND current_kind IS DISTINCT FROM NEW.content_kind THEN
    RAISE EXCEPTION 'GA canonical video kind mismatch' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END; $$;
CREATE TRIGGER ga_runs_validate BEFORE INSERT OR UPDATE ON ga_runs
  FOR EACH ROW EXECUTE FUNCTION ga_run_guard();

CREATE OR REPLACE FUNCTION ga_media_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE a RECORD; r RECORD;
BEGIN
  IF TG_OP='UPDATE' THEN
    RAISE EXCEPTION 'GA media pins are immutable; create a new run' USING ERRCODE='23514';
  END IF;
  SELECT video_id,source_id,sha256,permission,expires_at
    INTO a FROM media_assets WHERE id=NEW.media_asset_id;
  IF NOT FOUND OR a.video_id<>NEW.video_id OR a.source_id<>NEW.source_id
       OR a.sha256 IS NULL OR a.sha256<>NEW.asset_sha256
       OR a.permission='metadata_only'
       OR (a.expires_at IS NOT NULL AND a.expires_at <= now()) THEN
    RAISE EXCEPTION 'GA asset pin is foreign, expired or hash mismatched'
      USING ERRCODE='23514';
  END IF;
  SELECT content_kind,state INTO r FROM ga_runs WHERE id=NEW.run_id;
  IF NOT FOUND OR r.state IN ('completed','invalidated','cancelled') THEN
    RAISE EXCEPTION 'Cannot mutate completed GA media manifest'
      USING ERRCODE='23514';
  END IF;
  IF (r.content_kind IN ('video','slideshow_video') AND NEW.media_kind<>'video')
    OR (r.content_kind='image_post' AND NEW.media_kind<>'image')
    OR (r.content_kind='photo_carousel' AND NEW.media_kind<>'image') THEN
    RAISE EXCEPTION 'Media kind does not match run kind' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END; $$;
CREATE TRIGGER ga_media_validate BEFORE INSERT OR UPDATE ON ga_media_refs
  FOR EACH ROW EXECUTE FUNCTION ga_media_guard();

-- Fence completed step output, stable inputs and monotonic leases.
CREATE OR REPLACE FUNCTION ga_step_guard() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP='UPDATE' THEN
    IF (OLD.run_id,OLD.project_id,OLD.step_key,OLD.step_kind,OLD.input_sha256,
        OLD.created_at) IS DISTINCT FROM
       (NEW.run_id,NEW.project_id,NEW.step_key,NEW.step_kind,NEW.input_sha256,
        NEW.created_at) THEN
      RAISE EXCEPTION 'GA step identity changed' USING ERRCODE='23514';
    END IF;
    IF OLD.state IN ('completed','cancelled') AND NEW IS DISTINCT FROM OLD THEN
      RAISE EXCEPTION 'Terminal GA step is immutable' USING ERRCODE='23514';
    END IF;
    IF NEW.attempts < OLD.attempts THEN
      RAISE EXCEPTION 'GA step attempt fence cannot rewind' USING ERRCODE='23514';
    END IF;
  END IF;
  RETURN NEW;
END; $$;
CREATE TRIGGER ga_step_validate BEFORE UPDATE ON ga_steps
  FOR EACH ROW EXECUTE FUNCTION ga_step_guard();

CREATE OR REPLACE FUNCTION ga_result_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r RECORD; a RECORD;
BEGIN
  IF TG_OP='UPDATE' THEN
    -- Retention/FK cleanup may null out only an A003/A004 pointer; it cannot
    -- replace or edit any originally observed measurement or evidence.
    IF to_jsonb(NEW)-'video_a_analysis_id'-'carousel_a_analysis_id'
       = to_jsonb(OLD)-'video_a_analysis_id'-'carousel_a_analysis_id'
       AND (NEW.video_a_analysis_id IS NOT DISTINCT FROM OLD.video_a_analysis_id
            OR NEW.video_a_analysis_id IS NULL)
       AND (NEW.carousel_a_analysis_id IS NOT DISTINCT FROM OLD.carousel_a_analysis_id
            OR NEW.carousel_a_analysis_id IS NULL)
    THEN RETURN NEW; END IF;
    RAISE EXCEPTION 'GA result snapshots are append-only' USING ERRCODE='23514';
  END IF;
  SELECT content_kind,project_id,video_id,source_id
    INTO r FROM ga_runs WHERE id=NEW.run_id;
  IF NOT FOUND OR r.project_id<>NEW.project_id OR r.video_id<>NEW.video_id
      OR r.source_id<>NEW.source_id THEN
    RAISE EXCEPTION 'GA result lineage mismatch' USING ERRCODE='23514';
  END IF;
  IF r.content_kind IN ('video','slideshow_video') THEN
    IF NEW.video_a_analysis_id IS NULL OR NEW.carousel_a_analysis_id IS NOT NULL THEN
      RAISE EXCEPTION 'GA requires A003 analysis' USING ERRCODE='23514';
    END IF;
    SELECT video_id,source_id INTO a FROM video_feature_a_analyses
      WHERE id=NEW.video_a_analysis_id;
  ELSE
    IF NEW.carousel_a_analysis_id IS NULL OR NEW.video_a_analysis_id IS NOT NULL THEN
      RAISE EXCEPTION 'GA requires A004 analysis' USING ERRCODE='23514';
    END IF;
    SELECT video_id,source_id INTO a FROM carousel_feature_a_analyses
      WHERE id=NEW.carousel_a_analysis_id;
  END IF;
  IF NOT FOUND OR a.video_id<>NEW.video_id OR a.source_id<>NEW.source_id THEN
    RAISE EXCEPTION 'GA A003/A004 result belongs to a different video or source'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END; $$;
CREATE TRIGGER ga_result_validate BEFORE INSERT OR UPDATE ON ga_results
  FOR EACH ROW EXECUTE FUNCTION ga_result_guard();

CREATE OR REPLACE FUNCTION ga_outbox_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE x RECORD;
BEGIN
  IF TG_OP='UPDATE' THEN
    IF (OLD.run_id,OLD.project_id,OLD.result_id,OLD.event_type,
        OLD.payload_sha256,OLD.payload,OLD.created_at)
       IS DISTINCT FROM
       (NEW.run_id,NEW.project_id,NEW.result_id,NEW.event_type,
        NEW.payload_sha256,NEW.payload,NEW.created_at) THEN
      RAISE EXCEPTION 'GA outbox evidence is immutable' USING ERRCODE='23514';
    END IF;
    IF OLD.state='delivered' AND NEW IS DISTINCT FROM OLD THEN
      RAISE EXCEPTION 'GA delivery receipt is terminal' USING ERRCODE='23514';
    END IF;
    IF NEW.attempts<OLD.attempts THEN
      RAISE EXCEPTION 'GA outbox attempt rewind' USING ERRCODE='23514';
    END IF;
  END IF;
  SELECT run_id,project_id,state INTO x FROM ga_results WHERE id=NEW.result_id;
  IF NOT FOUND OR x.run_id<>NEW.run_id OR x.project_id<>NEW.project_id THEN
    RAISE EXCEPTION 'GA outbox result lineage mismatch' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END; $$;
CREATE TRIGGER ga_outbox_validate BEFORE INSERT OR UPDATE ON ga_outbox
  FOR EACH ROW EXECUTE FUNCTION ga_outbox_guard();

CREATE OR REPLACE FUNCTION ga_inbox_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE x RECORD;
BEGIN
  IF TG_OP='UPDATE' THEN
    RAISE EXCEPTION 'GA inbox receipts are immutable' USING ERRCODE='23514';
  END IF;
  SELECT qdp_run_id,qdp_input_sha256 INTO x FROM ga_runs WHERE id=NEW.run_id;
  IF NOT FOUND OR x.qdp_run_id<>NEW.qdp_run_id
      OR x.qdp_input_sha256<>NEW.input_sha256 THEN
    RAISE EXCEPTION 'GA inbox QDP pointer does not match run' USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END; $$;
CREATE TRIGGER ga_inbox_validate BEFORE INSERT OR UPDATE ON ga_inbox
  FOR EACH ROW EXECUTE FUNCTION ga_inbox_guard();

CREATE OR REPLACE FUNCTION ga_cache_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE x RECORD;
BEGIN
  IF TG_OP='UPDATE' THEN
    RAISE EXCEPTION 'GA cache pins are immutable' USING ERRCODE='23514';
  END IF;
  SELECT video_id,source_id INTO x FROM ga_runs WHERE id=NEW.run_id;
  IF NOT FOUND OR NOT EXISTS(
    SELECT 1 FROM ga_media_refs mr WHERE mr.run_id=NEW.run_id
      AND mr.media_asset_id=NEW.media_asset_id
      AND mr.video_id=x.video_id AND mr.source_id=x.source_id
  ) THEN RAISE EXCEPTION 'GA cache can only pin a verified run asset'
    USING ERRCODE='23514'; END IF;
  RETURN NEW;
END; $$;
CREATE TRIGGER ga_cache_validate BEFORE INSERT OR UPDATE ON ga_cache_refs
  FOR EACH ROW EXECUTE FUNCTION ga_cache_guard();

-- A local-only read projection. It does NOT imply verified QDP readiness.
-- Public live results MUST be gated again by the external QDP reader (GA-MOD-04).
CREATE OR REPLACE VIEW ga_local_readable_results AS
SELECT r.id AS run_id,r.project_id,r.qdp_run_id,r.video_id,
       r.manifest_sha256,r.qdp_input_sha256,r.content_kind,
       o.id AS result_id,o.state AS result_state,o.result_sha256,
       o.video_a_analysis_id,o.carousel_a_analysis_id,o.snapshot
FROM ga_runs r
JOIN ga_results o ON o.run_id=r.id AND o.project_id=r.project_id
 AND o.video_id=r.video_id AND o.source_id=r.source_id
JOIN sources s ON s.id=r.source_id AND s.status='enabled'
 AND s.can_analyze_media=TRUE
JOIN source_policy_versions sp ON sp.id=r.source_policy_version_id
 AND sp.source_id=r.source_id AND sp.is_active=TRUE
 AND sp.permissions->>'can_analyze_media'='true'
WHERE r.state IN ('partial','completed')
 AND ((r.content_kind IN ('video','slideshow_video') AND o.video_a_analysis_id IS NOT NULL)
      OR (r.content_kind NOT IN ('video','slideshow_video') AND o.carousel_a_analysis_id IS NOT NULL))
 AND EXISTS(SELECT 1 FROM ga_media_refs mr WHERE mr.run_id=r.id)
 AND NOT EXISTS (
    SELECT 1 FROM ga_media_refs mr
    LEFT JOIN media_assets a ON a.id=mr.media_asset_id
    WHERE mr.run_id=r.id
      AND (a.id IS NULL OR a.video_id<>r.video_id OR a.source_id<>r.source_id
           OR mr.asset_sha256 IS DISTINCT FROM a.sha256
           OR a.permission='metadata_only'
           OR (a.expires_at IS NOT NULL AND a.expires_at<=now()))
 );

-- Read-only legacy inventory; no copy, rewrite, or schema dependency on QDP.
-- A1 017 is *not* a main migration: only expose it if already installed.
DO $ga_legacy$
BEGIN
  IF to_regclass('group_a_tasks') IS NOT NULL THEN
    EXECUTE 'CREATE OR REPLACE VIEW ga_legacy_a1_inventory AS
      SELECT id::text AS legacy_task_id,project_id,qdp_run_id::text AS qdp_run_id,
             video_id,status,analysis_id
      FROM group_a_tasks';
  ELSE
    EXECUTE 'CREATE OR REPLACE VIEW ga_legacy_a1_inventory AS
      SELECT NULL::text AS legacy_task_id,NULL::text AS project_id,
             NULL::text AS qdp_run_id,NULL::bigint AS video_id,
             NULL::text AS status,NULL::bigint AS analysis_id
      WHERE FALSE';
  END IF;
END;
$ga_legacy$;
