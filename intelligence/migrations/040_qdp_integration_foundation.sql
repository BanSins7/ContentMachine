-- QDP FIX-03: independently deployable, additive integration foundation.
-- Never rewrite the applied 016_qualified_data_pipeline.sql checksum.
-- The 040 prefix sorts after main's existing 030..039 PSI migrations.
-- Owns only QDP tables/views, never copies the SRE/Collector/Analytics schemas.
--
-- This migration is independent of the unfinished SRE execution_mode and
-- content_sha256 additions: it is safely installable before a LIVE SRE producer.
-- A table's presence does NOT mean that an external peer is connected.

CREATE TABLE qdp_sre_inbox (
 event_id UUID PRIMARY KEY,
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 sre_run_id UUID NOT NULL,
 schema_version TEXT NOT NULL CHECK (length(schema_version) BETWEEN 1 AND 80),
 event_type TEXT NOT NULL CHECK
   (event_type IN ('sre.classification_run_completed',
                   'sre.classification_invalidated', 'sre.rights_revoked')),
 payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
 payload JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
 state TEXT NOT NULL DEFAULT 'received' CHECK
   (state IN ('received','accepted','quarantined')),
 reason_code TEXT,
 received_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 processed_at TIMESTAMPTZ,
 CHECK ((state = 'received' AND processed_at IS NULL AND reason_code IS NULL)
     OR (state = 'accepted' AND processed_at IS NOT NULL AND reason_code IS NULL)
     OR (state = 'quarantined' AND processed_at IS NOT NULL
          AND reason_code IS NOT NULL AND length(reason_code) BETWEEN 1 AND 80))
);
CREATE INDEX idx_qdp_sre_inbox_pending ON qdp_sre_inbox(received_at,event_id)
 WHERE state = 'received';
CREATE INDEX idx_qdp_sre_inbox_project_run ON qdp_sre_inbox(project_id,sre_run_id,received_at DESC);

-- A manifest cannot be invented from already existing legacy qdp_runs.
-- It is populated by FIX-06 from the DB-authoritative LIVE SRE snapshot.
CREATE TABLE qdp_run_manifests (
 run_id UUID PRIMARY KEY,
 project_id TEXT NOT NULL,
 taxonomy_sha256 TEXT NOT NULL CHECK (taxonomy_sha256 ~ '^[0-9a-f]{64}$'),
 manifest_sha256 TEXT NOT NULL CHECK (manifest_sha256 ~ '^[0-9a-f]{64}$'),
 selected_sha256 TEXT NOT NULL CHECK (selected_sha256 ~ '^[0-9a-f]{64}$'),
 expected_pairs INTEGER NOT NULL CHECK (expected_pairs > 0),
 expected_videos INTEGER NOT NULL CHECK (expected_videos > 0 AND expected_videos <= 10000),
 review_watermark JSONB NOT NULL DEFAULT '{}'::jsonb
   CHECK (jsonb_typeof(review_watermark)='object'),
 manifest JSONB NOT NULL CHECK (jsonb_typeof(manifest)='object'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY (run_id,project_id) REFERENCES qdp_runs(id,project_id) ON DELETE CASCADE
);
CREATE INDEX idx_qdp_run_manifests_project ON qdp_run_manifests(project_id,created_at DESC);

-- Per-leaf evidence retained for downstream provenance, including negative
-- rows. No independent copy of the canonical D1 video/source catalog.
CREATE TABLE qdp_item_lineage (
 run_id UUID NOT NULL,
 project_id TEXT NOT NULL,
 video_id BIGINT NOT NULL,
 category_key UUID NOT NULL,
 sre_result_id BIGINT NOT NULL REFERENCES sre_classification_results(id),
 content_version_id BIGINT NOT NULL REFERENCES sre_content_versions(id),
 content_sha256 TEXT NOT NULL CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
 evidence_sha256 TEXT NOT NULL CHECK (evidence_sha256 ~ '^[0-9a-f]{64}$'),
 model_decision TEXT NOT NULL CHECK
   (model_decision IN ('match','no_match','uncertain','not_evaluated')),
 effective_decision TEXT NOT NULL CHECK
   (effective_decision IN ('match','no_match','uncertain','not_evaluated')),
 human_label_id BIGINT REFERENCES sre_human_labels(id),
 metadata_observation_id BIGINT REFERENCES source_video_metadata(id),
 captured_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY (run_id,video_id,category_key),
 -- All SRE classifications (including no_match/needs_review) deserve lineage.
 -- qdp_items contains only QUALIFIED videos: never FK the SRE audit to it.
 FOREIGN KEY (run_id,project_id) REFERENCES qdp_runs(id,project_id) ON DELETE CASCADE,
 FOREIGN KEY (project_id,video_id)
   REFERENCES sre_project_videos(project_id,video_id)
);
-- Enforce SRE result lineage even when it was excluded/reviewed and has no
-- qdp_item row. Otherwise a valid result_id from another project/run could
-- be smuggled into an immutable QDP audit record.
CREATE OR REPLACE FUNCTION qdp_validate_sre_lineage() RETURNS trigger
LANGUAGE plpgsql AS $qdp_sre_fk$
BEGIN
 IF NOT EXISTS (
   SELECT 1 FROM qdp_runs q
   JOIN sre_classification_results r
     ON r.run_id=q.sre_run_id AND r.project_id=q.project_id
    AND r.taxonomy_version_id=q.taxonomy_version_id
   WHERE q.id=NEW.run_id AND q.project_id=NEW.project_id
     AND r.id=NEW.sre_result_id AND r.video_id=NEW.video_id
     AND r.category_key=NEW.category_key
     AND r.content_version_id=NEW.content_version_id
 ) THEN
   RAISE EXCEPTION 'SRE result is not part of the pinned QDP run'
     USING ERRCODE='23503';
 END IF;
 IF NEW.human_label_id IS NOT NULL AND NOT EXISTS (
   SELECT 1 FROM sre_human_labels h JOIN qdp_runs q
     ON q.project_id=h.project_id AND q.taxonomy_version_id=h.taxonomy_version_id
   WHERE q.id=NEW.run_id AND q.project_id=NEW.project_id
     AND h.id=NEW.human_label_id AND h.video_id=NEW.video_id
     AND h.category_key=NEW.category_key
 ) THEN
   RAISE EXCEPTION 'SRE human review belongs to a different QDP input'
     USING ERRCODE='23503';
 END IF;
 RETURN NEW;
END $qdp_sre_fk$;
CREATE TRIGGER qdp_check_sre_lineage BEFORE INSERT ON qdp_item_lineage
 FOR EACH ROW EXECUTE FUNCTION qdp_validate_sre_lineage();

CREATE INDEX idx_qdp_item_lineage_result ON qdp_item_lineage(sre_result_id);
CREATE INDEX idx_qdp_item_lineage_project_video ON qdp_item_lineage(project_id,video_id);

-- Durable event to be delivered, never an assertion of downstream analysis.
CREATE TABLE qdp_outbox (
 event_id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 video_id BIGINT,
 analysis_group TEXT CHECK (analysis_group IN ('A','B','C')),
 event_type TEXT NOT NULL CHECK
   (event_type IN ('qualified_set_created','preparation_ready',
                   'preparation_blocked','preparation_invalidated',
                   'run_completed')),
 schema_version TEXT NOT NULL DEFAULT 'qdp-event/v1'
   CHECK (schema_version = 'qdp-event/v1'),
 payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
 payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY (run_id,project_id) REFERENCES qdp_runs(id,project_id) ON DELETE CASCADE,
 FOREIGN KEY (run_id,project_id,video_id)
   REFERENCES qdp_items(run_id,project_id,video_id) ON DELETE CASCADE,
 CHECK ((event_type IN ('preparation_ready','preparation_blocked',
                        'preparation_invalidated') AND video_id IS NOT NULL
        AND analysis_group IS NOT NULL)
     OR (event_type NOT IN ('preparation_ready','preparation_blocked',
                            'preparation_invalidated') AND analysis_group IS NULL))
);
CREATE INDEX idx_qdp_outbox_project_run ON qdp_outbox(project_id,run_id,created_at DESC);
CREATE INDEX idx_qdp_outbox_group ON qdp_outbox(analysis_group,created_at,event_id)
 WHERE analysis_group IS NOT NULL;

-- One durable state per event/consumer. ACK is a persisted downstream inbox
-- acceptance receipt, NOT video analysis completion.
CREATE TABLE qdp_outbox_deliveries (
 event_id UUID NOT NULL REFERENCES qdp_outbox(event_id) ON DELETE CASCADE,
 consumer TEXT NOT NULL CHECK (consumer IN ('group_a','group_b','group_c','analytics')),
 state TEXT NOT NULL DEFAULT 'pending' CHECK
   (state IN ('pending','leased','acknowledged','dead_letter')),
 attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
 max_attempts INTEGER NOT NULL DEFAULT 8 CHECK (max_attempts BETWEEN 1 AND 100),
 lease_generation BIGINT NOT NULL DEFAULT 0 CHECK (lease_generation >= 0),
 locked_by TEXT,
 lease_until TIMESTAMPTZ,
 run_after TIMESTAMPTZ NOT NULL DEFAULT now(),
 reason_code TEXT,
 updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY (event_id,consumer),
 CHECK ((state = 'leased' AND locked_by IS NOT NULL AND lease_until IS NOT NULL)
        OR (state <> 'leased' AND locked_by IS NULL AND lease_until IS NULL))
);
CREATE INDEX idx_qdp_delivery_claim ON qdp_outbox_deliveries(state,run_after,event_id)
 WHERE state IN ('pending','leased');
CREATE INDEX idx_qdp_delivery_expired ON qdp_outbox_deliveries(lease_until)
 WHERE state = 'leased';

CREATE TABLE qdp_delivery_receipts (
 event_id UUID NOT NULL,
 consumer TEXT NOT NULL,
 receipt_key TEXT NOT NULL CHECK (length(receipt_key) BETWEEN 8 AND 160),
 payload_sha256 TEXT NOT NULL CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
 acknowledged_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 PRIMARY KEY(event_id,consumer),
 FOREIGN KEY(event_id,consumer)
   REFERENCES qdp_outbox_deliveries(event_id,consumer) ON DELETE CASCADE
);
CREATE INDEX idx_qdp_receipts_acked ON qdp_delivery_receipts(acknowledged_at);

-- Versioned revocation; absence of an invalidation is NOT proof of current
-- source/media/metric rights, which must also be checked at read time.
CREATE TABLE qdp_invalidations (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 video_id BIGINT,
 analysis_group TEXT CHECK (analysis_group IN ('A','B','C')),
 reason_code TEXT NOT NULL CHECK (length(reason_code) BETWEEN 1 AND 80),
 origin TEXT NOT NULL CHECK
   (origin IN ('sre','human_review','d1_source','retention',
               'configuration','operator')),
 provenance_sha256 TEXT NOT NULL CHECK (provenance_sha256 ~ '^[0-9a-f]{64}$'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY (run_id,project_id) REFERENCES qdp_runs(id,project_id) ON DELETE CASCADE,
 FOREIGN KEY (run_id,project_id,video_id)
   REFERENCES qdp_items(run_id,project_id,video_id) ON DELETE CASCADE,
 CHECK (analysis_group IS NULL OR video_id IS NOT NULL)
);
CREATE INDEX idx_qdp_invalidations_lookup
 ON qdp_invalidations(project_id,run_id,video_id,analysis_group,created_at DESC);

-- This helper only checks durable invalidation state. Do NOT treat it as
-- independently authorized current content/metrics/media (FIX-10/15).
CREATE VIEW qdp_unrevoked_preparations AS
SELECT p.*
  FROM qdp_preparations p
 WHERE NOT EXISTS (
       SELECT 1 FROM qdp_invalidations i
        WHERE i.project_id=p.project_id AND i.run_id=p.run_id
          AND (i.video_id IS NULL OR i.video_id=p.video_id)
          AND (i.analysis_group IS NULL OR i.analysis_group=p.analysis_group)
 );

-- Strict immutability on published manifest, lineage, events, receipts and
-- invalidation facts (DELETE remains available for permitted retention purge).
CREATE OR REPLACE FUNCTION qdp_guard_integration_fact() RETURNS trigger
LANGUAGE plpgsql AS $qdp_fact$
BEGIN
 RAISE EXCEPTION 'QDP integration fact is immutable; append a new revision/event';
END $qdp_fact$;
CREATE TRIGGER qdp_manifest_immutable BEFORE UPDATE ON qdp_run_manifests
 FOR EACH ROW EXECUTE FUNCTION qdp_guard_integration_fact();
CREATE TRIGGER qdp_lineage_immutable BEFORE UPDATE ON qdp_item_lineage
 FOR EACH ROW EXECUTE FUNCTION qdp_guard_integration_fact();
CREATE TRIGGER qdp_outbox_immutable BEFORE UPDATE ON qdp_outbox
 FOR EACH ROW EXECUTE FUNCTION qdp_guard_integration_fact();
CREATE TRIGGER qdp_receipt_immutable BEFORE UPDATE ON qdp_delivery_receipts
 FOR EACH ROW EXECUTE FUNCTION qdp_guard_integration_fact();
CREATE TRIGGER qdp_invalidation_immutable BEFORE UPDATE ON qdp_invalidations
 FOR EACH ROW EXECUTE FUNCTION qdp_guard_integration_fact();
