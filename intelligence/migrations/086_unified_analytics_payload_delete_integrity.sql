-- UA-MOD-15 | Prevent the evidence matrix for an immutable 084 payload
-- from being silently dismantled by a direct DELETE after commit.
-- Forward-only migration: preserve 080..085 checksums. A retention/privacy
-- purge may remove the whole parent snapshot (CASCADE removes payload);
-- direct loss of one member while its payload remains is NOT allowed.
CREATE FUNCTION uae_v1_recheck_payload_after_evidence_delete()
 RETURNS trigger LANGUAGE plpgsql AS $uae_evidence_delete$
DECLARE
  existing_payload JSONB;
  actual JSONB;
  n INTEGER;
BEGIN
  SELECT p.payload INTO existing_payload
  FROM uae_snapshot_payloads p
  WHERE p.project_id=OLD.project_id AND p.snapshot_id=OLD.snapshot_id;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  SELECT jsonb_agg(
    jsonb_build_object(
      'port',e.port,
      'state',e.state,
      'checked',e.checked,
      'is_current',e.is_current,
      'record_key',e.record_key,
      'producer_schema',e.producer_schema,
      'producer_version',e.producer_version,
      'evidence_sha256',e.evidence_sha256,
      'lineage_sha256',e.lineage_sha256,
      'reason_code',e.reason_code
    ) ORDER BY array_position(
      ARRAY['source_policy','d1','qdp','sre',
            'group_a','group_b','group_c']::text[],e.port
    )
  ),count(*) INTO actual,n
  FROM uae_snapshot_evidence e
  WHERE e.project_id=OLD.project_id AND e.snapshot_id=OLD.snapshot_id;
  IF n<>7 OR actual IS DISTINCT FROM existing_payload->'evidence' THEN
    RAISE EXCEPTION 'UAE immutable payload cannot lose one evidence member'
      USING ERRCODE='23514';
  END IF;
  RETURN NULL;
END
$uae_evidence_delete$;

CREATE CONSTRAINT TRIGGER uae_payload_evidence_deletion_guard
 AFTER DELETE ON uae_snapshot_evidence
 DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION uae_v1_recheck_payload_after_evidence_delete();

-- No UPDATE allowed by 080/084 immutability guards; INSERT after a complete
-- 7-member matrix is blocked by its existing composite PK.
-- No new producer approval, public SELECT, or Group A/B/C duplicate tables.
