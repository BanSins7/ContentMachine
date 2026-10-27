-- SP-15D: immutable, project-scoped, provider-evidence-only invoice reconciliation.
-- No provider request is made by migration. No automatic release of uncertain
-- reservations. Never write evidence received from an unauthenticated browser.
CREATE TABLE sp_invoice_reconciliations (
 id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
 project_id TEXT NOT NULL REFERENCES project_registry(id),
 generation_id UUID NOT NULL,
 attempt_id UUID NOT NULL UNIQUE REFERENCES sqg_attempts(id),
 provider_name TEXT NOT NULL DEFAULT 'openrouter' CHECK(provider_name='openrouter'),
 provider_response_id TEXT NOT NULL CHECK(length(provider_response_id) BETWEEN 1 AND 240),
 request_sha256 TEXT NOT NULL CHECK(request_sha256 ~ '^[0-9a-f]{64}$'),
 invoice_line_id TEXT NOT NULL CHECK(length(invoice_line_id) BETWEEN 8 AND 240),
 invoice_sha256 TEXT NOT NULL CHECK(invoice_sha256 ~ '^[0-9a-f]{64}$'),
 provider_account_sha256 TEXT NOT NULL CHECK(provider_account_sha256 ~ '^[0-9a-f]{64}$'),
 charge_usd NUMERIC(18,8) NOT NULL CHECK(charge_usd>=0),
 verification_method TEXT NOT NULL CHECK(verification_method='account_billing_export_exact_request'),
 provider_verified_at TIMESTAMPTZ NOT NULL,
 decision TEXT NOT NULL CHECK(decision='reconciled_failed'),
 owner_action TEXT NOT NULL CHECK(owner_action='search_planner.generation.reconcile_invoice'),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY(generation_id,project_id) REFERENCES sqg_generations(id,project_id),
 -- A billing account is shared by multiple SP projects: the same provider
 -- invoice line or response can NEVER settle two different attempts.
 UNIQUE(provider_account_sha256,invoice_line_id),
 UNIQUE(provider_account_sha256,provider_response_id),
 UNIQUE(project_id,invoice_line_id),
 UNIQUE(id,project_id),
 CHECK(length(btrim(provider_response_id))>0),
 CHECK(length(btrim(invoice_line_id))>0)
);
CREATE INDEX sp_invoice_reconciliations_project
 ON sp_invoice_reconciliations(project_id,created_at DESC);
CREATE TRIGGER sp_invoice_reconciliations_immutable
 BEFORE UPDATE OR DELETE ON sp_invoice_reconciliations
 FOR EACH ROW EXECUTE FUNCTION sqg_reject_immutable_write();
