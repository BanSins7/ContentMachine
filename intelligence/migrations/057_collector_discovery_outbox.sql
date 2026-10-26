-- VC-MOD-02 / 057: canonical D1 page receipts + one durable SRE-ready outbox.
-- Dependency: 056 manifest and 054/055 existing Collector, 001/002 Discovery.
-- Provider "done" is not a persisted page; VC-MOD-09/10 will write all rows
-- and terminal state in one transaction under a valid worker lease fence.
-- No second video table, no extra SRE classifier DB, no duplicate event queue.

-- Scoped parent keys for future page/tenant FKs.
ALTER TABLE collector_runs
 ADD CONSTRAINT collector_runs_run_project_vc_uq UNIQUE(run_id,project_id);
ALTER TABLE collector_provider_tasks
 ADD CONSTRAINT collector_provider_task_source_vc_uq
 UNIQUE(id,run_id,source_request_id);

-- Ensure D1 video FK matches the discovery/metadata identity, not any other video.
ALTER TABLE discoveries
 ADD CONSTRAINT collector_discovery_id_video_uq UNIQUE(id,video_id);
ALTER TABLE source_video_metadata
 ADD CONSTRAINT collector_source_metadata_id_video_uq UNIQUE(id,video_id);

-- Immutable SUCCESS receipts: 0 accepted is allowed for an empty/rejected page.
-- A task has at most one committed receipt per explicit page ordinal.
CREATE TABLE collector_ingest_pages (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 project_id TEXT NOT NULL,
 run_id BIGINT NOT NULL,
 provider_task_row_id BIGINT NOT NULL,
 source_request_id BIGINT NOT NULL,
 page_ordinal INTEGER NOT NULL CHECK(page_ordinal BETWEEN 0 AND 4),
 provider_task_id TEXT NOT NULL CHECK(length(provider_task_id) BETWEEN 1 AND 255),
 provider_request_id TEXT,
 page_sha256 TEXT NOT NULL CHECK(page_sha256 ~ '^[0-9a-f]{64}$'),
 snapshot_sha256 TEXT NOT NULL CHECK(snapshot_sha256 ~ '^[0-9a-f]{64}$'),
 cursor_sha256 TEXT CHECK(cursor_sha256 ~ '^[0-9a-f]{64}$'),
 lease_generation BIGINT NOT NULL CHECK(lease_generation>0),
 fetched_at TIMESTAMPTZ NOT NULL,
 persisted_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 state TEXT NOT NULL DEFAULT 'persisted' CHECK(state='persisted'),
 has_more BOOLEAN NOT NULL DEFAULT FALSE,
 raw_count INTEGER NOT NULL CHECK(raw_count BETWEEN 0 AND 100),
 examined_count INTEGER NOT NULL CHECK(examined_count BETWEEN 0 AND 100),
 accepted_count INTEGER NOT NULL CHECK(accepted_count BETWEEN 0 AND 100),
 rejected_count INTEGER NOT NULL CHECK(rejected_count BETWEEN 0 AND 100),
 duplicate_count INTEGER NOT NULL CHECK(duplicate_count BETWEEN 0 AND 100),
 inserted_count INTEGER NOT NULL CHECK(inserted_count BETWEEN 0 AND 100),
 saved_count INTEGER NOT NULL CHECK(saved_count BETWEEN 0 AND 100),
 qa_summary JSONB NOT NULL DEFAULT '{}'::jsonb
   CHECK(jsonb_typeof(qa_summary)='object'),
 metrics_persisted BOOLEAN NOT NULL DEFAULT FALSE CHECK(metrics_persisted=FALSE),
 retention_until TIMESTAMPTZ,
 FOREIGN KEY(run_id,project_id) REFERENCES collector_runs(run_id,project_id),
 FOREIGN KEY(provider_task_row_id,run_id,source_request_id)
  REFERENCES collector_provider_tasks(id,run_id,source_request_id),
 UNIQUE(provider_task_row_id,page_ordinal),
 UNIQUE(id,project_id,run_id,source_request_id),
 UNIQUE(id,project_id,source_request_id),
 CHECK(examined_count <= raw_count),
 CHECK(accepted_count + rejected_count <= examined_count),
 CHECK(saved_count <= accepted_count),
 CHECK(duplicate_count + inserted_count = saved_count),
 CHECK(retention_until IS NULL OR retention_until >= persisted_at)
);
CREATE INDEX idx_collector_ingest_pages_project
 ON collector_ingest_pages(project_id,run_id,persisted_at DESC);
CREATE INDEX idx_collector_ingest_pages_request
 ON collector_ingest_pages(source_request_id,page_ordinal);

-- Canonical D1 associations for exactly the video observations in this page.
-- Project ownership is inherited from the composite checkpoint FK.
CREATE TABLE collector_ingest_page_videos (
 page_id BIGINT NOT NULL,
 project_id TEXT NOT NULL,
 source_request_id BIGINT NOT NULL,
 video_id BIGINT NOT NULL REFERENCES videos(id),
 discovery_id BIGINT NOT NULL,
 source_metadata_id BIGINT NOT NULL,
 quality TEXT NOT NULL CHECK(quality IN ('valid','partial')),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(page_id,video_id),
 FOREIGN KEY(page_id,project_id,source_request_id)
  REFERENCES collector_ingest_pages(id,project_id,source_request_id),
 FOREIGN KEY(discovery_id,video_id)
  REFERENCES discoveries(id,video_id),
 FOREIGN KEY(source_metadata_id,video_id)
  REFERENCES source_video_metadata(id,video_id),
 UNIQUE(page_id,project_id,source_request_id,video_id),
 UNIQUE(page_id,project_id,video_id,discovery_id,source_metadata_id)
);
CREATE INDEX idx_collector_ingest_page_videos_project_video
 ON collector_ingest_page_videos(project_id,video_id,page_id);
CREATE INDEX idx_collector_ingest_page_videos_request
 ON collector_ingest_page_videos(source_request_id,video_id);

-- Many-to-many: a single physical provider request may represent several
-- QV candidate UUIDs, and the same canonical video may appear in many projects.
CREATE TABLE collector_page_video_queries (
 page_id BIGINT NOT NULL,
 project_id TEXT NOT NULL,
 source_request_id BIGINT NOT NULL,
 video_id BIGINT NOT NULL,
 candidate_id TEXT NOT NULL CHECK(length(candidate_id) BETWEEN 1 AND 128),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(page_id,video_id,candidate_id),
 FOREIGN KEY(page_id,project_id,source_request_id,video_id)
  REFERENCES collector_ingest_page_videos(page_id,project_id,source_request_id,video_id),
 FOREIGN KEY(source_request_id,candidate_id)
  REFERENCES collector_task_queries(source_request_id,query_id),
 FOREIGN KEY(source_request_id,video_id,candidate_id)
  REFERENCES collector_video_hypotheses(source_request_id,video_id,query_id)
);
CREATE INDEX idx_collector_page_video_queries_candidate
 ON collector_page_video_queries(project_id,candidate_id,video_id);

-- SRE-ready event is durable even if the SRE module/worker is absent.
-- Keep EXACTLY ONE Collector SRE delivery queue, not a second Discovery queue.
CREATE TABLE collector_sre_outbox (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 page_id BIGINT NOT NULL,
 video_id BIGINT NOT NULL,
 discovery_id BIGINT NOT NULL,
 source_metadata_id BIGINT NOT NULL,
 event_type TEXT NOT NULL DEFAULT 'collector.sre_ready/v1'
   CHECK(event_type='collector.sre_ready/v1'),
 delivery_key TEXT NOT NULL CHECK(delivery_key ~ '^[0-9a-f]{64}$'),
 event_payload JSONB NOT NULL CHECK(jsonb_typeof(event_payload)='object'),
 state TEXT NOT NULL DEFAULT 'pending'
   CHECK(state IN ('pending','leased','delivered','blocked')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts>=0),
 lease_generation BIGINT NOT NULL DEFAULT 0 CHECK(lease_generation>=0),
 leased_by TEXT,
 lease_until TIMESTAMPTZ,
 next_delivery_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 delivered_at TIMESTAMPTZ,
 last_error_code TEXT,
 retention_until TIMESTAMPTZ,
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(page_id,project_id,video_id,discovery_id,source_metadata_id)
  REFERENCES collector_ingest_page_videos
  (page_id,project_id,video_id,discovery_id,source_metadata_id),
 UNIQUE(project_id,delivery_key),
 UNIQUE(page_id,video_id,discovery_id),
 CHECK(event_payload->>'schema_version'='collector.sre_ready/v1'),
 CHECK(event_payload->>'project_id'=project_id),
 CHECK(event_payload->>'evidence_scope'='metadata_only'),
 CHECK(event_payload->>'relevance_state'='not_evaluated'),
 CHECK(event_payload->>'delivery_key'=delivery_key),
 CHECK(event_payload->'video'->>'video_id'=video_id::text),
 CHECK(event_payload->'video'->>'discovery_id'=discovery_id::text),
 CHECK(event_payload->'video'->>'source_metadata_id'=source_metadata_id::text),
 CHECK((state='delivered')=(delivered_at IS NOT NULL)),
 CHECK((state='leased')=(leased_by IS NOT NULL AND lease_until IS NOT NULL)),
 CHECK(retention_until IS NULL OR retention_until >= created_at)
);
CREATE INDEX idx_collector_sre_outbox_due
 ON collector_sre_outbox(next_delivery_at,id)
 WHERE state='pending';
CREATE INDEX idx_collector_sre_outbox_lease
 ON collector_sre_outbox(lease_until) WHERE state='leased';
CREATE INDEX idx_collector_sre_outbox_project
 ON collector_sre_outbox(project_id,state,created_at DESC);
CREATE INDEX idx_collector_sre_outbox_video
 ON collector_sre_outbox(project_id,video_id,created_at DESC);

-- Immutable page/lineage receipts. The one outbox changes ONLY delivery fields.
CREATE TRIGGER collector_ingest_pages_immutable BEFORE UPDATE OR DELETE
 ON collector_ingest_pages FOR EACH ROW EXECUTE FUNCTION collector_immutable_record();
CREATE TRIGGER collector_ingest_page_videos_immutable BEFORE UPDATE OR DELETE
 ON collector_ingest_page_videos FOR EACH ROW EXECUTE FUNCTION collector_immutable_record();
CREATE TRIGGER collector_page_video_queries_immutable BEFORE UPDATE OR DELETE
 ON collector_page_video_queries FOR EACH ROW EXECUTE FUNCTION collector_immutable_record();

CREATE FUNCTION collector_guard_sre_outbox_identity()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN
   RAISE EXCEPTION 'Collector SRE delivery history may not be deleted';
 END IF;
 IF (OLD.id,OLD.project_id,OLD.page_id,OLD.video_id,OLD.discovery_id,
     OLD.source_metadata_id,OLD.event_type,OLD.delivery_key,OLD.event_payload,
     OLD.created_at) IS DISTINCT FROM
    (NEW.id,NEW.project_id,NEW.page_id,NEW.video_id,NEW.discovery_id,
     NEW.source_metadata_id,NEW.event_type,NEW.delivery_key,NEW.event_payload,
     NEW.created_at) THEN
   RAISE EXCEPTION 'Collector SRE event identity is immutable';
 END IF;
 RETURN NEW;
END $$;
CREATE TRIGGER collector_sre_outbox_identity_guard
 BEFORE UPDATE OR DELETE ON collector_sre_outbox
 FOR EACH ROW EXECUTE FUNCTION collector_guard_sre_outbox_identity();

COMMENT ON TABLE collector_ingest_pages IS
 'Immutable checkpoint written transactionally with real D1 video/discovery/metadata associations, not provider done counters.';
COMMENT ON TABLE collector_sre_outbox IS
 'One project-scoped durable not_evaluated SRE-ready queue; delivery may be offline, never emits classifier matches.';
