-- UA-MOD-14 | Forward-only immutable descriptive UAE snapshot payload.
-- 080 snapshot and its 7 port evidence children remain sole canonical IDs;
-- 084 adds an immutable, checksum-addressable minimal JSON metadata envelope.
-- No duplicate D1/Group A/B/C metrics, raw media copies, score computation,
-- new read/publication API, qualified claim, or public GRANT.
--
-- Existing historical snapshots (written before UA-MOD-14) are unaffected.
-- Do not edit 080/081/082/083 migration checksums after deployment.
CREATE TABLE uae_snapshot_payloads (
  project_id TEXT NOT NULL,
  snapshot_id BIGINT NOT NULL,
  assembly_schema TEXT NOT NULL
    CHECK (assembly_schema='uae-snapshot-assembly/v1'),
  payload JSONB NOT NULL CHECK (jsonb_typeof(payload)='object'),
  payload_sha256 TEXT NOT NULL
    CHECK (payload_sha256 ~ '^[0-9a-f]{64}$'),
  evidence_set_sha256 TEXT NOT NULL
    CHECK (evidence_set_sha256 ~ '^[0-9a-f]{64}$'),
  decision_sha256 TEXT NOT NULL
    CHECK (decision_sha256 ~ '^[0-9a-f]{64}$'),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY(project_id,snapshot_id),
  FOREIGN KEY(project_id,snapshot_id)
    REFERENCES uae_unified_snapshots(project_id,id) ON DELETE CASCADE,
  CHECK (payload->>'schema_version' = assembly_schema),
  CHECK (payload->>'status' IN ('partial','blocked')),
  CHECK (jsonb_typeof(payload->'evidence')='array'),
  CHECK (jsonb_array_length(payload->'evidence')=7),
  CHECK (jsonb_typeof(payload->'readiness')='object'),
  CHECK (jsonb_typeof(payload->'blockers')='array'),
  CHECK (payload->>'decision_sha256'=decision_sha256),
  CHECK (payload->>'evidence_set_sha256'=evidence_set_sha256),
  CHECK (payload->>'current_source_rights_sha256' IS NULL OR
    payload->>'current_source_rights_sha256' ~ '^[0-9a-f]{64}$')
);
CREATE INDEX uae_payload_project_recent_idx
  ON uae_snapshot_payloads(project_id,created_at DESC,snapshot_id DESC);

-- No caller may write a payload with a different status/scope/decision from
-- the immutable canonical parent. SQL-level shape is NOT authenticated
-- producer evidence: the authorized UA-MOD-14 repository rehashes JSON in
-- canonical Python serialization and rereads all seven pinned evidence rows.
CREATE FUNCTION uae_snapshot_payload_identity_guard()
 RETURNS trigger LANGUAGE plpgsql AS $uae_payload_guard$
DECLARE
  parent uae_unified_snapshots%ROWTYPE;
BEGIN
  SELECT * INTO parent FROM uae_unified_snapshots sn
   WHERE sn.project_id=NEW.project_id AND sn.id=NEW.snapshot_id;
  IF NOT FOUND
    OR parent.status NOT IN ('partial','blocked')
    OR parent.status IS DISTINCT FROM NEW.payload->>'status'
    OR parent.decision_sha256 IS DISTINCT FROM NEW.decision_sha256
    OR parent.scope_sha256 IS DISTINCT FROM NEW.payload->>'scope_sha256'
    OR parent.inputs_sha256 IS DISTINCT FROM NEW.payload->>'input_sha256'
    OR parent.video_id::text IS DISTINCT FROM NEW.payload->>'video_id'
    OR parent.project_id IS DISTINCT FROM NEW.payload->>'project_id'
    OR parent.source_id IS DISTINCT FROM NEW.payload->>'source_id'
    OR parent.source_policy_version_id::text IS DISTINCT FROM
         NEW.payload->>'source_policy_version_id'
    OR parent.content_kind IS DISTINCT FROM NEW.payload->>'content_kind'
  THEN
    RAISE EXCEPTION 'UAE snapshot payload canonical parent mismatch'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END $uae_payload_guard$;
CREATE TRIGGER uae_snapshot_payload_identity_guard_trg
 BEFORE INSERT ON uae_snapshot_payloads
 FOR EACH ROW EXECUTE FUNCTION uae_snapshot_payload_identity_guard();

-- Invalidation and retention purge are separate. A historical partial
-- snapshot is never automatically upgraded to qualified or published.
CREATE TRIGGER uae_snapshot_payload_immutable_update
 BEFORE UPDATE ON uae_snapshot_payloads
 FOR EACH ROW EXECUTE FUNCTION uae_immutable_update_guard();

COMMENT ON TABLE uae_snapshot_payloads IS
 'Immutable reference-only UAE partial/blocked snapshot payload; not an authorization surface or raw metric/media cache';
