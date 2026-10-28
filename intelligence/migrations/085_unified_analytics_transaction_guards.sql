-- UA-MOD-15 | Forward-only PostgreSQL 17 transaction and producer-proof gates.
-- Keep prior 080..084 migration checksums exactly intact.
--
-- CRITICAL: 080 accepts structural seven checked=true references for a
-- qualified snapshot, but a privileged direct SQL client could forge such
-- claims without SRE/QDP/Group B/Group C reviewed producer attestations.
-- Effective approval verification is NOT implemented. Until the future
-- signed/independently verified approval wire exists, no new qualified rows
-- or checked=true/is_current=true/complete UAE evidence can be inserted.
-- This is NOT a permanent way to enable qualification by changing flags.
CREATE FUNCTION uae_v1_refuse_unapproved_qualified()
RETURNS trigger LANGUAGE plpgsql AS $uae_no_unapproved_qualified$
BEGIN
  IF NEW.status = 'qualified' THEN
    RAISE EXCEPTION 'UAE v1 qualified producer approval unavailable'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END
$uae_no_unapproved_qualified$;

CREATE TRIGGER uae_v1_no_forged_qualified_insert
 BEFORE INSERT ON uae_unified_snapshots
 FOR EACH ROW EXECUTE FUNCTION uae_v1_refuse_unapproved_qualified();

CREATE FUNCTION uae_v1_refuse_forged_complete_evidence()
RETURNS trigger LANGUAGE plpgsql AS $uae_no_unapproved_evidence$
BEGIN
  IF NEW.checked IS TRUE OR NEW.is_current IS TRUE
     OR NEW.state = 'complete' THEN
    RAISE EXCEPTION 'UAE v1 peer evidence not effectively approved'
      USING ERRCODE='23514';
  END IF;
  RETURN NEW;
END
$uae_no_unapproved_evidence$;

CREATE TRIGGER uae_v1_no_forged_complete_evidence_insert
 BEFORE INSERT ON uae_snapshot_evidence
 FOR EACH ROW EXECUTE FUNCTION uae_v1_refuse_forged_complete_evidence();

-- A 084-style payload can be inserted only if its complete seven-row evidence
-- matrix *actually exists* in the same transaction. Defer this condition
-- until COMMIT to allow snapshot/evidence/payload inserts in any order.
-- Older diagnostic snapshots without a 084 payload remain readable for
-- migration compatibility, but are NOT approved or user-visible data.
CREATE FUNCTION uae_v1_assert_payload_evidence_matrix()
RETURNS trigger LANGUAGE plpgsql AS $uae_payload_matrix$
DECLARE
  expected JSONB;
  actual JSONB;
  n INTEGER;
BEGIN
  SELECT jsonb_agg(value ORDER BY ord) INTO expected
  FROM jsonb_array_elements(NEW.payload->'evidence')
       WITH ORDINALITY AS v(value,ord);
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
      )
      ORDER BY array_position(
        ARRAY['source_policy','d1','qdp','sre',
              'group_a','group_b','group_c']::text[],e.port
      )
  ),count(*) INTO actual,n
  FROM uae_snapshot_evidence e
  WHERE e.project_id=NEW.project_id AND e.snapshot_id=NEW.snapshot_id;
  IF n <> 7 OR expected IS DISTINCT FROM actual THEN
    RAISE EXCEPTION 'UAE payload evidence matrix inconsistent'
      USING ERRCODE='23514';
  END IF;
  RETURN NULL;
END
$uae_payload_matrix$;

CREATE CONSTRAINT TRIGGER uae_payload_seven_evidence_at_commit
 AFTER INSERT ON uae_snapshot_payloads
 DEFERRABLE INITIALLY DEFERRED
 FOR EACH ROW EXECUTE FUNCTION uae_v1_assert_payload_evidence_matrix();

-- Full producer authentication and public read gates are separate future
-- stages. No GRANT, no old-record rewrite, no shadow Group A/B/C tables.
