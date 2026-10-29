-- UA-MOD-07. UAE-owned durable handoff identity and collision-forensic journal.
-- No QDP-owned table alterations, no outbound sender, no fabricated A/B/C job.
-- Project-video FK is baseline D1/SRE; QDP existence verified by adapter
-- before accepting event. ACK is NOT completed A/B/C analysis.
CREATE TABLE uae_qdp_handoff_inbox (
  event_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL,
  qdp_run_id UUID NOT NULL,
  video_id BIGINT NOT NULL,
  analysis_group TEXT NOT NULL CHECK (analysis_group IN ('A','B','C')),
  input_sha256 TEXT NOT NULL CHECK (input_sha256 ~ '^[0-9a-f]{64}$'),
  payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  source_event_type TEXT NOT NULL DEFAULT 'preparation_ready'
    CHECK (source_event_type = 'preparation_ready'),
  verified_reference_sha256 TEXT NOT NULL
    CHECK (verified_reference_sha256 ~ '^[0-9a-f]{64}$'),
  received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY (project_id,video_id)
    REFERENCES sre_project_videos(project_id,video_id) ON DELETE CASCADE,
  UNIQUE(project_id,qdp_run_id,video_id,analysis_group,input_sha256,event_id)
);
CREATE INDEX uae_qdp_inbox_lookup_idx
 ON uae_qdp_handoff_inbox(project_id,qdp_run_id,video_id,analysis_group);
CREATE TABLE uae_qdp_handoff_quarantine (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  event_id UUID NOT NULL,
  project_id TEXT NOT NULL,
  observed_payload_sha256 TEXT NOT NULL
     CHECK (observed_payload_sha256 ~ '^[0-9a-f]{64}$'),
  recorded_payload_sha256 TEXT
     CHECK (recorded_payload_sha256 IS NULL
            OR recorded_payload_sha256 ~ '^[0-9a-f]{64}$'),
  reason_code TEXT NOT NULL CHECK (reason_code IN (
    'event_payload_collision','event_preparation_collision')),
  observed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  UNIQUE(event_id,observed_payload_sha256,reason_code)
);
CREATE INDEX uae_qdp_quarantine_by_event_idx
 ON uae_qdp_handoff_quarantine(event_id,observed_at DESC);
CREATE FUNCTION uae_qdp_handoff_immutable() RETURNS trigger
 LANGUAGE plpgsql AS $uae_qdp_immutable$
BEGIN
 RAISE EXCEPTION 'UAE QDP handoff facts are immutable; no UPDATE';
END;
$uae_qdp_immutable$;
CREATE TRIGGER uae_qdp_inbox_update_guard
 BEFORE UPDATE ON uae_qdp_handoff_inbox
 FOR EACH ROW EXECUTE FUNCTION uae_qdp_handoff_immutable();
CREATE TRIGGER uae_qdp_quarantine_update_guard
 BEFORE UPDATE ON uae_qdp_handoff_quarantine
 FOR EACH ROW EXECUTE FUNCTION uae_qdp_handoff_immutable();
-- DELETE intentionally available to a later rights/TTL cleanup executor.
