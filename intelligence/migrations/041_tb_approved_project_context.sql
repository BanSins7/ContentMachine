-- Stage 12: optional RTB mode pinned to a human-approved Project Context.
-- Legacy quote/run rows have no binding and keep their original semantics.
ALTER TABLE project_context_approvals
 ADD CONSTRAINT psi_approval_exact_context UNIQUE(project_id,id,context_id);
ALTER TABLE tb_cost_quotes
 ADD CONSTRAINT tb_context_quote_ref UNIQUE(project_id,id);
ALTER TABLE tb_runs
 ADD CONSTRAINT tb_context_run_quote_ref UNIQUE(project_id,id,quote_id);

CREATE TABLE tb_context_quote_bindings (
 project_id TEXT NOT NULL,
 quote_id UUID NOT NULL,
 context_id UUID NOT NULL,
 approval_id UUID NOT NULL,
 context_sha256 TEXT NOT NULL CHECK(context_sha256 ~ '^[0-9a-f]{64}$'),
 selection_sha256 TEXT NOT NULL CHECK(selection_sha256 ~ '^[0-9a-f]{64}$'),
 token_budget INTEGER NOT NULL CHECK(token_budget BETWEEN 100 AND 8000),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(project_id,quote_id),
 UNIQUE(project_id,quote_id,context_id),
 FOREIGN KEY(project_id,quote_id) REFERENCES tb_cost_quotes(project_id,id),
 FOREIGN KEY(project_id,context_id) REFERENCES project_context_snapshots(project_id,id),
 FOREIGN KEY(project_id,approval_id,context_id)
   REFERENCES project_context_approvals(project_id,id,context_id)
);
CREATE INDEX tb_context_quotes_by_snapshot ON tb_context_quote_bindings(project_id,context_id);

CREATE TABLE tb_context_run_bindings (
 project_id TEXT NOT NULL,
 run_id UUID NOT NULL,
 quote_id UUID NOT NULL,
 context_id UUID NOT NULL,
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(project_id,run_id),
 FOREIGN KEY(project_id,run_id,quote_id) REFERENCES tb_runs(project_id,id,quote_id),
 FOREIGN KEY(project_id,quote_id,context_id)
   REFERENCES tb_context_quote_bindings(project_id,quote_id,context_id)
);
CREATE INDEX tb_context_runs_by_context ON tb_context_run_bindings(project_id,context_id);

CREATE TRIGGER tb_context_quotes_immutable BEFORE UPDATE OR DELETE ON tb_context_quote_bindings
 FOR EACH ROW EXECUTE FUNCTION tb_immutable_snapshot();
CREATE TRIGGER tb_context_runs_immutable BEFORE UPDATE OR DELETE ON tb_context_run_bindings
 FOR EACH ROW EXECUTE FUNCTION tb_immutable_snapshot();
