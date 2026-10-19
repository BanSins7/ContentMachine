-- Discovery SRE project workspaces, additive and isolated from existing D1/D0 tables.
-- Project ids correspond to the existing local projects.py UUID.hex identifiers.
-- Existing global videos/discoveries/metrics remain canonical; labels are project-scoped.
CREATE TABLE IF NOT EXISTS sre_workspaces (
  project_id TEXT PRIMARY KEY CHECK (project_id ~ '^[0-9a-f]{32}$'),
  niche_code TEXT NOT NULL DEFAULT 'health_fitness' CHECK (niche_code = 'health_fitness'),
  research_goal TEXT NOT NULL DEFAULT 'maximize_views' CHECK (research_goal = 'maximize_views'),
  active_taxonomy_id BIGINT,
  active_pipeline_id BIGINT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sre_profile_versions (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES sre_workspaces(project_id) ON DELETE CASCADE,
  version INTEGER NOT NULL CHECK (version > 0),
  product_type TEXT NOT NULL,
  functions_text TEXT NOT NULL,
  problems_text TEXT NOT NULL,
  original_intake JSONB NOT NULL DEFAULT '{}'::jsonb CHECK (jsonb_typeof(original_intake)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id,version),
  UNIQUE(id,project_id)
);
CREATE TABLE IF NOT EXISTS sre_facts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  profile_version_id BIGINT NOT NULL,
  kind TEXT NOT NULL CHECK (kind IN ('function','problem')),
  original_quote TEXT NOT NULL,
  normalized_text TEXT NOT NULL,
  origin TEXT NOT NULL CHECK (origin IN ('explicit','user_added')),
  review_status TEXT NOT NULL DEFAULT 'proposed'
    CHECK (review_status IN ('proposed','approved','rejected')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(profile_version_id,project_id)
    REFERENCES sre_profile_versions(id,project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sre_facts_project_review ON sre_facts(project_id,review_status);
CREATE TABLE IF NOT EXISTS sre_taxonomy_versions (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES sre_workspaces(project_id) ON DELETE CASCADE,
  version INTEGER NOT NULL CHECK(version>0),
  parent_id BIGINT,
  status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft','approved')),
  approved_by TEXT,
  approved_at TIMESTAMPTZ,
  snapshot_checksum TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id,version),
  UNIQUE(id,project_id),
  FOREIGN KEY(parent_id,project_id)
    REFERENCES sre_taxonomy_versions(id,project_id),
  CHECK ((status='draft' AND approved_at IS NULL) OR
         (status='approved' AND approved_at IS NOT NULL AND approved_by IS NOT NULL))
);
CREATE TABLE IF NOT EXISTS sre_taxonomy_categories (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL,
  taxonomy_version_id BIGINT NOT NULL,
  category_key UUID NOT NULL,
  name TEXT NOT NULL,
  relation_type TEXT NOT NULL CHECK(relation_type IN ('direct','problem','adjacent')),
  definition TEXT NOT NULL,
  rules JSONB NOT NULL CHECK (
    jsonb_typeof(rules)='object' AND
    jsonb_typeof(rules->'include')='array' AND
    jsonb_typeof(rules->'exclude')='array' AND
    jsonb_typeof(rules->'positive_examples')='array' AND
    jsonb_typeof(rules->'negative_examples')='array'),
  related_fact_ids JSONB NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(related_fact_ids)='array'),
  review_status TEXT NOT NULL DEFAULT 'proposed'
    CHECK(review_status IN ('proposed','approved','rejected')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(taxonomy_version_id,category_key),
  UNIQUE(id,project_id),
  UNIQUE(taxonomy_version_id,project_id,category_key),
  FOREIGN KEY(taxonomy_version_id,project_id)
    REFERENCES sre_taxonomy_versions(id,project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_sre_categories_version ON sre_taxonomy_categories(project_id,taxonomy_version_id,review_status);
CREATE TABLE IF NOT EXISTS sre_review_events (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES sre_workspaces(project_id) ON DELETE CASCADE,
  target_type TEXT NOT NULL CHECK(target_type IN ('fact','category','taxonomy','label')),
  target_id TEXT NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('approve','edit','reject','add','publish','correct')),
  before_value JSONB,
  after_value JSONB,
  reviewed_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_sre_review_events_project ON sre_review_events(project_id,created_at DESC);

CREATE TABLE IF NOT EXISTS sre_model_registry (
  model_ref TEXT PRIMARY KEY,
  provider TEXT NOT NULL,
  model_id TEXT NOT NULL,
  capability TEXT NOT NULL CHECK(capability IN ('embedding','llm_classification','taxonomy_generation')),
  available BOOLEAN NOT NULL DEFAULT FALSE,
  metadata JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(metadata)='object'),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS sre_pipeline_configs (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL REFERENCES sre_workspaces(project_id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  version INTEGER NOT NULL CHECK(version>0),
  config JSONB NOT NULL CHECK(jsonb_typeof(config)='object' AND jsonb_typeof(config->'stages')='array'),
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(project_id,name,version),
  UNIQUE(id,project_id)
);
ALTER TABLE sre_workspaces
  ADD CONSTRAINT sre_workspace_active_taxonomy
  FOREIGN KEY(active_taxonomy_id,project_id)
  REFERENCES sre_taxonomy_versions(id,project_id);
ALTER TABLE sre_workspaces
  ADD CONSTRAINT sre_workspace_active_pipeline
  FOREIGN KEY(active_pipeline_id,project_id)
  REFERENCES sre_pipeline_configs(id,project_id);

-- Global video catalog can be reused, but links/content/classification are project-specific.
CREATE TABLE IF NOT EXISTS sre_project_videos (
  project_id TEXT NOT NULL REFERENCES sre_workspaces(project_id) ON DELETE CASCADE,
  video_id BIGINT NOT NULL REFERENCES videos(id) ON DELETE CASCADE,
  added_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(project_id,video_id)
);
CREATE TABLE IF NOT EXISTS sre_content_versions (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL,
  video_id BIGINT NOT NULL,
  revision INTEGER NOT NULL CHECK(revision>0),
  content JSONB NOT NULL CHECK(jsonb_typeof(content)='object'),
  provenance JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(provenance)='object'),
  completeness TEXT NOT NULL DEFAULT 'partial' CHECK(completeness IN ('full','partial','metadata_only')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(project_id,video_id) REFERENCES sre_project_videos(project_id,video_id) ON DELETE CASCADE,
  UNIQUE(project_id,video_id,revision),
  UNIQUE(id,project_id,video_id)
);
CREATE TABLE IF NOT EXISTS sre_classification_runs (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES sre_workspaces(project_id) ON DELETE CASCADE,
  taxonomy_version_id BIGINT NOT NULL,
  pipeline_id BIGINT NOT NULL,
  resolved_models JSONB NOT NULL CHECK(jsonb_typeof(resolved_models)='object'),
  dataset_snapshot JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(dataset_snapshot)='object'),
  status TEXT NOT NULL DEFAULT 'queued' CHECK(status IN ('queued','running','completed','failed','cancelled')),
  cost_usd NUMERIC(14,6) CHECK(cost_usd IS NULL OR cost_usd>=0),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  finished_at TIMESTAMPTZ,
  UNIQUE(id,project_id),
  FOREIGN KEY(taxonomy_version_id,project_id) REFERENCES sre_taxonomy_versions(id,project_id),
  FOREIGN KEY(pipeline_id,project_id) REFERENCES sre_pipeline_configs(id,project_id)
);
CREATE INDEX IF NOT EXISTS idx_sre_runs_project ON sre_classification_runs(project_id,created_at DESC);
CREATE TABLE IF NOT EXISTS sre_classification_results (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  run_id UUID NOT NULL,
  project_id TEXT NOT NULL,
  video_id BIGINT NOT NULL,
  content_version_id BIGINT,
  taxonomy_version_id BIGINT NOT NULL,
  category_key UUID NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN ('match','no_match','uncertain','not_evaluated')),
  evidence JSONB NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(evidence)='array'),
  stage_trace JSONB NOT NULL DEFAULT '[]'::jsonb CHECK(jsonb_typeof(stage_trace)='array'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(run_id,project_id) REFERENCES sre_classification_runs(id,project_id) ON DELETE CASCADE,
  FOREIGN KEY(project_id,video_id) REFERENCES sre_project_videos(project_id,video_id),
  FOREIGN KEY(content_version_id,project_id,video_id)
    REFERENCES sre_content_versions(id,project_id,video_id),
  FOREIGN KEY(taxonomy_version_id,project_id,category_key)
    REFERENCES sre_taxonomy_categories(taxonomy_version_id,project_id,category_key),
  UNIQUE(run_id,video_id,category_key)
);
CREATE INDEX IF NOT EXISTS idx_sre_results_project_video ON sre_classification_results(project_id,video_id);
CREATE TABLE IF NOT EXISTS sre_human_labels (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL,
  video_id BIGINT NOT NULL,
  taxonomy_version_id BIGINT NOT NULL,
  category_key UUID NOT NULL,
  label TEXT NOT NULL CHECK(label IN ('match','no_match','uncertain')),
  reviewer TEXT NOT NULL,
  reason TEXT,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  superseded_at TIMESTAMPTZ,
  FOREIGN KEY(project_id,video_id) REFERENCES sre_project_videos(project_id,video_id),
  FOREIGN KEY(taxonomy_version_id,project_id,category_key)
    REFERENCES sre_taxonomy_categories(taxonomy_version_id,project_id,category_key)
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_sre_human_current
  ON sre_human_labels(project_id,video_id,taxonomy_version_id,category_key)
  WHERE superseded_at IS NULL;
CREATE TABLE IF NOT EXISTS sre_experiments (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL REFERENCES sre_workspaces(project_id) ON DELETE CASCADE,
  taxonomy_version_id BIGINT NOT NULL,
  benchmark JSONB NOT NULL CHECK(jsonb_typeof(benchmark)='object'),
  comparison JSONB NOT NULL DEFAULT '{}'::jsonb CHECK(jsonb_typeof(comparison)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(taxonomy_version_id,project_id) REFERENCES sre_taxonomy_versions(id,project_id),
  UNIQUE(id,project_id)
);
CREATE TABLE IF NOT EXISTS sre_experiment_runs (
  experiment_id UUID NOT NULL,
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  PRIMARY KEY(experiment_id,run_id),
  FOREIGN KEY(experiment_id,project_id) REFERENCES sre_experiments(id,project_id) ON DELETE CASCADE,
  FOREIGN KEY(run_id,project_id) REFERENCES sre_classification_runs(id,project_id) ON DELETE CASCADE
);

-- Approved taxonomy rows and their rules are immutable, including direct SQL edits.
CREATE OR REPLACE FUNCTION sre_guard_approved_taxonomy() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_TABLE_NAME='sre_taxonomy_versions' THEN
    IF TG_OP='DELETE' AND OLD.status='approved' THEN
      RAISE EXCEPTION 'approved taxonomy version is immutable';
    ELSIF TG_OP='UPDATE' AND OLD.status='approved' THEN
      RAISE EXCEPTION 'approved taxonomy version is immutable';
    END IF;
  ELSE
    IF TG_OP IN ('UPDATE','DELETE') AND EXISTS (
      SELECT 1 FROM sre_taxonomy_versions
      WHERE id=OLD.taxonomy_version_id AND status='approved'
    ) THEN
      RAISE EXCEPTION 'approved taxonomy categories are immutable';
    END IF;
    IF TG_OP='INSERT' AND EXISTS (
      SELECT 1 FROM sre_taxonomy_versions
      WHERE id=NEW.taxonomy_version_id AND status='approved'
    ) THEN
      RAISE EXCEPTION 'cannot add categories to approved taxonomy';
    END IF;
  END IF;
  RETURN COALESCE(NEW, OLD);
END $$;
CREATE TRIGGER sre_immutable_taxonomy_versions
  BEFORE UPDATE OR DELETE ON sre_taxonomy_versions
  FOR EACH ROW EXECUTE FUNCTION sre_guard_approved_taxonomy();
CREATE TRIGGER sre_immutable_taxonomy_categories
  BEFORE INSERT OR UPDATE OR DELETE ON sre_taxonomy_categories
  FOR EACH ROW EXECUTE FUNCTION sre_guard_approved_taxonomy();
