-- Real LIVE SRE M36 -> QDP durable per-video intake.
-- This is intentionally distinct from legacy qdp_sre_inbox/run rows:
-- no synthetic sre_classification_results, no forged QDP qualification.
CREATE TABLE qdp_sre_live_events (
 event_id UUID PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 sre_run_id UUID NOT NULL,
 item_id BIGINT NOT NULL,
 video_id BIGINT NOT NULL,
 kind TEXT NOT NULL CHECK(kind IN ('ready','invalidated')),
 event_sha256 TEXT NOT NULL CHECK(event_sha256 ~ '^[0-9a-f]{64}$'),
 prior_ready_event_id UUID,
 prior_ready_event_sha256 TEXT,
 received_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 FOREIGN KEY (project_id,event_id)
   REFERENCES sre_live_result_outbox(project_id,event_id) ON DELETE RESTRICT,
 FOREIGN KEY (sre_run_id,project_id)
   REFERENCES sre_live_runs(run_id,project_id) ON DELETE RESTRICT,
 FOREIGN KEY (project_id,video_id)
   REFERENCES sre_project_videos(project_id,video_id),
 CHECK((kind='ready' AND prior_ready_event_id IS NULL
       AND prior_ready_event_sha256 IS NULL) OR
       (kind='invalidated' AND prior_ready_event_id IS NOT NULL
        AND prior_ready_event_sha256 ~ '^[0-9a-f]{64}$')),
 UNIQUE(event_id,project_id)
);
CREATE INDEX qdp_sre_live_by_project
 ON qdp_sre_live_events(project_id,sre_run_id,received_at DESC);
CREATE TABLE qdp_sre_live_tombstones (
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 prior_ready_event_id UUID NOT NULL,
 prior_ready_event_sha256 TEXT NOT NULL
   CHECK(prior_ready_event_sha256 ~ '^[0-9a-f]{64}$'),
 invalidation_event_id UUID NOT NULL UNIQUE,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(project_id,prior_ready_event_id),
 FOREIGN KEY(invalidation_event_id,project_id)
   REFERENCES qdp_sre_live_events(event_id,project_id) ON DELETE RESTRICT,
 FOREIGN KEY(project_id,prior_ready_event_id)
   REFERENCES sre_live_result_outbox(project_id,event_id) ON DELETE RESTRICT
);
CREATE OR REPLACE FUNCTION qdp_sre_live_immutable()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 RAISE EXCEPTION 'QDP LIVE SRE intake is immutable' USING ERRCODE='23514';
END $$;
CREATE TRIGGER qdp_sre_live_events_no_mutation
 BEFORE UPDATE OR DELETE ON qdp_sre_live_events
 FOR EACH ROW EXECUTE FUNCTION qdp_sre_live_immutable();
CREATE TRIGGER qdp_sre_live_tombstones_no_mutation
 BEFORE UPDATE OR DELETE ON qdp_sre_live_tombstones
 FOR EACH ROW EXECUTE FUNCTION qdp_sre_live_immutable();
REVOKE ALL ON qdp_sre_live_events,qdp_sre_live_tombstones FROM PUBLIC;
