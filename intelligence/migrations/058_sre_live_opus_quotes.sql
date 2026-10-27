-- M23 SRE final Opus quote: forward-only 058, immutable and project-owned.
-- Stores only rates/upper bounds/hashes: NO raw D1 data, no consent, no paid IO.
CREATE TABLE sre_live_opus_quotes (
  quote_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  project_id TEXT NOT NULL,
  run_id UUID NOT NULL,
  item_id BIGINT NOT NULL,
  batch_manifest_sha256 TEXT NOT NULL CHECK (
    batch_manifest_sha256 ~ '^[0-9a-f]{64}$'),
  catalog_sha256 TEXT NOT NULL CHECK(catalog_sha256 ~ '^[0-9a-f]{64}$'),
  quote_sha256 TEXT NOT NULL CHECK(quote_sha256 ~ '^[0-9a-f]{64}$'),
  min_usd NUMERIC(24,9) NOT NULL CHECK (min_usd=0),
  max_usd NUMERIC(24,9) NOT NULL CHECK (max_usd>0),
  issued_at TIMESTAMPTZ NOT NULL,
  expires_at TIMESTAMPTZ NOT NULL,
  quote JSONB NOT NULL CHECK (jsonb_typeof(quote)='object'
                           AND octet_length(quote::text)<=1048576),
  FOREIGN KEY(run_id,project_id)
    REFERENCES sre_live_runs(run_id,project_id),
  FOREIGN KEY(item_id,run_id,project_id)
    REFERENCES sre_live_items(id,run_id,project_id),
  UNIQUE(project_id,quote_sha256),
  CHECK (expires_at > issued_at AND
         expires_at <= issued_at + INTERVAL '300 seconds'),
  CHECK (quote->>'schema_version'='sre-opus-final-quote/v1'
         AND quote->>'status'='issued_immutable_upper_bound_not_approved'
         AND quote->>'currency'='USD'
         AND quote->>'project_id'=project_id
         AND quote->>'batch_manifest_sha256'=batch_manifest_sha256
         AND quote->>'catalog_sha256'=catalog_sha256
         AND quote->>'quote_sha256'=quote_sha256
         AND quote->>'min_total_usd'='0.000000000'
         AND quote->>'max_total_usd'=to_char(max_usd,'FM999999999999990.000000000')
         AND quote->>'reserve_upper_bound_usd'=to_char(max_usd,'FM999999999999990.000000000')
         AND quote->>'dispatch_allowed'='false'
         AND quote->>'requires_dedicated_sre_consent'='true'
         AND quote->>'requires_atomic_budget_reservation'='true'
         AND quote->>'token_count_exact'='false'
         AND quote->>'paid_calls'='0')
);
CREATE INDEX sre_m23_quote_project_run
  ON sre_live_opus_quotes(project_id,run_id,item_id,expires_at DESC);

CREATE FUNCTION sre_m23_quote_immutable() RETURNS trigger
LANGUAGE plpgsql AS $sre_m23_lock$
BEGIN
  RAISE EXCEPTION 'M23 immutable Opus quote cannot be updated/deleted';
END;
$sre_m23_lock$;
CREATE TRIGGER sre_m23_quote_no_update
  BEFORE UPDATE ON sre_live_opus_quotes
  FOR EACH ROW EXECUTE FUNCTION sre_m23_quote_immutable();
CREATE TRIGGER sre_m23_quote_no_delete
  BEFORE DELETE ON sre_live_opus_quotes
  FOR EACH ROW EXECUTE FUNCTION sre_m23_quote_immutable();

REVOKE ALL ON sre_live_opus_quotes FROM PUBLIC;
