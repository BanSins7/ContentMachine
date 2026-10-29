-- M24 standalone SRE explicit paid-execution consent, NEVER C/Query consent.
-- No implicit provider dispatch, no budget ledger and no result creation.
CREATE UNIQUE INDEX sre_m24_quote_composite_owner
  ON sre_live_opus_quotes(project_id,run_id,item_id,quote_sha256);

CREATE TABLE sre_live_opus_consents (
  consent_id UUID PRIMARY KEY,
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  item_id BIGINT NOT NULL,
  quote_sha256 TEXT NOT NULL CHECK(quote_sha256 ~ '^[0-9a-f]{64}$'),
  actor_id TEXT NOT NULL CHECK(actor_id ~ '^[A-Za-z0-9:_@.-]{1,128}$'),
  consent_stage TEXT NOT NULL DEFAULT 'sre_live_opus_paid_execution'
    CHECK(consent_stage='sre_live_opus_paid_execution'),
  max_usd NUMERIC(24,9) NOT NULL CHECK(max_usd>0),
  granted_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  confirmation_sha256 TEXT NOT NULL
    CHECK(confirmation_sha256 ~ '^[0-9a-f]{64}$'),
  revoked_at TIMESTAMPTZ,
  revoked_by TEXT CHECK(revoked_by ~ '^[A-Za-z0-9:_@.-]{1,128}$'),
  revoke_reason TEXT CHECK(revoke_reason IN (
      'user_revoked','operator_revoked','security_revoked')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  FOREIGN KEY(project_id,run_id,item_id,quote_sha256)
    REFERENCES sre_live_opus_quotes(project_id,run_id,item_id,quote_sha256)
    ON DELETE CASCADE,
  UNIQUE(project_id,quote_sha256),
  CHECK(expires_at > granted_at
        AND expires_at <= granted_at + INTERVAL '300 seconds'),
  CHECK((revoked_at IS NULL AND revoked_by IS NULL AND revoke_reason IS NULL)
     OR (revoked_at IS NOT NULL AND revoked_by IS NOT NULL
         AND revoke_reason IS NOT NULL AND revoked_at >= granted_at))
);

CREATE INDEX sre_m24_consent_project_run
  ON sre_live_opus_consents(project_id,run_id,item_id,expires_at DESC);

-- Quote itself may be revoked/deleted for privacy. An FK makes all consents
-- disappear with it, so a stale copied consent never gains paid authority.
CREATE FUNCTION sre_m24_guard_consent() RETURNS trigger
LANGUAGE plpgsql AS $sre_m24_guard$
DECLARE
  pinned_max NUMERIC(24,9);
  quote_expires TIMESTAMPTZ;
BEGIN
  IF TG_OP='INSERT' THEN
    SELECT q.max_usd,q.expires_at INTO pinned_max,quote_expires
    FROM sre_live_opus_quotes q
    WHERE q.project_id=NEW.project_id AND q.run_id=NEW.run_id
      AND q.item_id=NEW.item_id AND q.quote_sha256=NEW.quote_sha256
    FOR SHARE;
    IF pinned_max IS NULL OR NEW.max_usd <> pinned_max
       OR NEW.expires_at > quote_expires
       OR NEW.granted_at > clock_timestamp() + INTERVAL '30 seconds'
       OR NEW.expires_at <= clock_timestamp()
       OR NEW.revoked_at IS NOT NULL THEN
      RAISE EXCEPTION 'M24 SRE consent cannot outlive/override exact quote';
    END IF;
    RETURN NEW;
  END IF;

  -- Immutable original actor/quote/limit/stage/approval data; the ONLY
  -- mutable transition is one-way explicit revocation with audit fields.
  IF (to_jsonb(NEW)-'revoked_at'-'revoked_by'-'revoke_reason') IS DISTINCT FROM
     (to_jsonb(OLD)-'revoked_at'-'revoked_by'-'revoke_reason') THEN
    RAISE EXCEPTION 'M24 SRE consent identity, stage, cap immutable';
  END IF;
  IF OLD.revoked_at IS NOT NULL OR NEW.revoked_at IS NULL
     OR NEW.revoked_by IS NULL OR NEW.revoke_reason IS NULL
     OR NEW.revoked_at < OLD.granted_at
     OR NEW.revoked_at > clock_timestamp() + INTERVAL '30 seconds' THEN
    RAISE EXCEPTION 'M24 consent can only transition to revoked once';
  END IF;
  RETURN NEW;
END;
$sre_m24_guard$;
CREATE TRIGGER sre_m24_validate_insert
  BEFORE INSERT ON sre_live_opus_consents
  FOR EACH ROW EXECUTE FUNCTION sre_m24_guard_consent();
CREATE TRIGGER sre_m24_revocation_only
  BEFORE UPDATE ON sre_live_opus_consents
  FOR EACH ROW EXECUTE FUNCTION sre_m24_guard_consent();

-- DELETE intentionally permitted for privacy/retention and parent cleanup.
-- Explicit revocation (UPDATE) must be used in the normal live workflow.
REVOKE ALL ON sre_live_opus_consents FROM PUBLIC;
