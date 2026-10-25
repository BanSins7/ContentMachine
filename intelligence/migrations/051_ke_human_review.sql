-- B Stage12 / B8: explicit human decisions. No auto-approval or publishing.
-- Provision reviewer grants only through privileged project administration.
CREATE TABLE knowledge_fact_review_authorizations (
  project_id TEXT NOT NULL REFERENCES project_registry(id),
  reviewer_id TEXT NOT NULL CHECK(length(btrim(reviewer_id)) BETWEEN 1 AND 160),
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  granted_by TEXT NOT NULL CHECK(length(btrim(granted_by)) BETWEEN 1 AND 160),
  granted_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  PRIMARY KEY(project_id,reviewer_id)
);

CREATE TABLE knowledge_fact_review_sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  gate_id UUID NOT NULL,
  dedup_id UUID NOT NULL,
  grounding_id UUID NOT NULL,
  normalization_id UUID NOT NULL,
  run_id UUID NOT NULL,
  batch_id UUID NOT NULL,
  reviewer_id TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'open'
    CHECK(status IN ('open','submitted')),
  revision INTEGER NOT NULL DEFAULT 0 CHECK(revision BETWEEN 0 AND 50000),
  gate_report_sha256 TEXT NOT NULL CHECK(gate_report_sha256 ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,id),
  UNIQUE(project_id,gate_id),
  UNIQUE(project_id,run_id),
  UNIQUE(project_id,id,gate_id,dedup_id),
  FOREIGN KEY(project_id,gate_id,dedup_id,grounding_id,normalization_id,run_id,batch_id)
    REFERENCES knowledge_fact_quality_gates(
      project_id,id,dedup_id,grounding_id,normalization_id,run_id,batch_id),
  FOREIGN KEY(project_id,reviewer_id)
    REFERENCES knowledge_fact_review_authorizations(project_id,reviewer_id)
);
CREATE INDEX ke_review_sessions_by_project ON knowledge_fact_review_sessions(project_id,created_at DESC);

CREATE TABLE knowledge_fact_review_events (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  session_id UUID NOT NULL,
  revision INTEGER NOT NULL CHECK(revision BETWEEN 1 AND 50000),
  reviewer_id TEXT NOT NULL,
  target_kind TEXT NOT NULL CHECK(target_kind IN ('group','conflict','warnings')),
  target_id TEXT NOT NULL,
  decision TEXT NOT NULL CHECK(decision IN (
    'approve','reject','select_a','select_b','reject_both','not_conflicting','defer',
    'acknowledge')),
  reviewed_text TEXT,
  note TEXT,
  source_support_affirmed BOOLEAN NOT NULL DEFAULT FALSE,
  subject_scope_affirmed BOOLEAN NOT NULL DEFAULT FALSE,
  prev_sha256 TEXT NOT NULL CHECK(prev_sha256 ~ '^[0-9a-f]{64}$'),
  event_sha256 TEXT NOT NULL CHECK(event_sha256 ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,session_id,revision),
  UNIQUE(project_id,session_id,id),
  FOREIGN KEY(project_id,session_id) REFERENCES knowledge_fact_review_sessions(project_id,id),
  FOREIGN KEY(project_id,reviewer_id)
    REFERENCES knowledge_fact_review_authorizations(project_id,reviewer_id),
  CHECK(
    (target_kind='group' AND decision IN ('approve','reject')
      AND target_id ~ '^[0-9a-f]{64}$' AND reviewed_text IS NOT NULL
      AND length(reviewed_text) BETWEEN 1 AND 800)
    OR (target_kind='conflict' AND decision IN
      ('select_a','select_b','reject_both','not_conflicting','defer')
      AND target_id ~ '^[0-9a-f]{64}$' AND reviewed_text IS NULL)
    OR (target_kind='warnings' AND decision='acknowledge'
      AND target_id='gate_warnings' AND reviewed_text IS NULL)
  ),
  CHECK(note IS NULL OR length(note) BETWEEN 1 AND 2000),
  CHECK(decision <> 'approve' OR (source_support_affirmed AND subject_scope_affirmed)),
  CHECK(decision = 'approve' OR (NOT source_support_affirmed AND NOT subject_scope_affirmed))
);
CREATE INDEX ke_review_events_session ON knowledge_fact_review_events(project_id,session_id,revision);

CREATE TABLE knowledge_fact_review_submissions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  project_id TEXT NOT NULL,
  session_id UUID NOT NULL,
  gate_id UUID NOT NULL,
  dedup_id UUID NOT NULL,
  reviewer_id TEXT NOT NULL,
  terminal_revision INTEGER NOT NULL CHECK(terminal_revision BETWEEN 0 AND 50000),
  submission_sha256 TEXT NOT NULL CHECK(submission_sha256 ~ '^[0-9a-f]{64}$'),
  submission JSONB NOT NULL CHECK(jsonb_typeof(submission)='object'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
  UNIQUE(project_id,session_id),
  UNIQUE(project_id,id),
  FOREIGN KEY(project_id,session_id,gate_id,dedup_id)
    REFERENCES knowledge_fact_review_sessions(project_id,id,gate_id,dedup_id),
  FOREIGN KEY(project_id,reviewer_id)
    REFERENCES knowledge_fact_review_authorizations(project_id,reviewer_id),
  CHECK(submission->>'schema_version'='knowledge-human-review/v1'),
  CHECK(submission->>'session_id'=session_id::text),
  CHECK(submission->>'gate_id'=gate_id::text),
  CHECK(submission->>'reviewer_id'=reviewer_id)
);

CREATE TRIGGER ke_review_events_immutable BEFORE UPDATE OR DELETE
 ON knowledge_fact_review_events FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER ke_review_submissions_immutable BEFORE UPDATE OR DELETE
 ON knowledge_fact_review_submissions FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE FUNCTION ke_guard_submitted_review() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF OLD.status='submitted' OR
    (NEW.project_id,NEW.gate_id,NEW.dedup_id,NEW.grounding_id,NEW.normalization_id,
     NEW.run_id,NEW.batch_id,NEW.reviewer_id,NEW.gate_report_sha256,NEW.created_at)
       IS DISTINCT FROM
    (OLD.project_id,OLD.gate_id,OLD.dedup_id,OLD.grounding_id,OLD.normalization_id,
     OLD.run_id,OLD.batch_id,OLD.reviewer_id,OLD.gate_report_sha256,OLD.created_at)
    OR NEW.revision < OLD.revision OR NEW.revision > OLD.revision + 1 OR
       (OLD.status='open' AND NEW.status NOT IN ('open','submitted')) THEN
   RAISE EXCEPTION 'Review sessions cannot be reassigned or reopened';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER ke_review_session_guard BEFORE UPDATE ON knowledge_fact_review_sessions
 FOR EACH ROW EXECUTE FUNCTION ke_guard_submitted_review();
CREATE TRIGGER ke_review_session_no_delete BEFORE DELETE ON knowledge_fact_review_sessions
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
