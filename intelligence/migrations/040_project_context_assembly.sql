-- Stage 11: complete the Stage 01 snapshot foundation with immutable exact
-- analysis pins, corroborated grounded evidence identity and integrity checks.
-- Existing 030 tables and migration checksums are unchanged.
ALTER TABLE project_context_source_pins
 ADD CONSTRAINT psi_context_source_exact_pin UNIQUE(project_id,context_id,source_id,source_version_id);
ALTER TABLE project_evidence
 ADD CONSTRAINT psi_evidence_full_scope_key UNIQUE(project_id,source_id,source_version_id,id);

CREATE TABLE project_context_analysis_pins (
 project_id TEXT NOT NULL,
 context_id UUID NOT NULL,
 source_id UUID NOT NULL,
 source_version_id UUID NOT NULL,
 output_id UUID NOT NULL,
 output_sha256 TEXT NOT NULL CHECK(output_sha256 ~ '^[0-9a-f]{64}$'),
 schema_version TEXT NOT NULL CHECK(length(schema_version) BETWEEN 1 AND 80),
 PRIMARY KEY(project_id,context_id,output_id),
 FOREIGN KEY(project_id,context_id,source_id,source_version_id)
   REFERENCES project_context_source_pins(project_id,context_id,source_id,source_version_id),
 FOREIGN KEY(project_id,source_id,source_version_id,output_id)
   REFERENCES source_processing_outputs(project_id,source_id,source_version_id,id)
);

CREATE TABLE project_context_grounded_evidence_pins (
 project_id TEXT NOT NULL,
 context_id UUID NOT NULL,
 source_id UUID NOT NULL,
 source_version_id UUID NOT NULL,
 evidence_id UUID NOT NULL,
 quote_sha256 TEXT NOT NULL CHECK(quote_sha256 ~ '^[0-9a-f]{64}$'),
 locator_sha256 TEXT NOT NULL CHECK(locator_sha256 ~ '^[0-9a-f]{64}$'),
 PRIMARY KEY(project_id,context_id,evidence_id),
 FOREIGN KEY(project_id,context_id,source_id,source_version_id)
   REFERENCES project_context_source_pins(project_id,context_id,source_id,source_version_id),
 FOREIGN KEY(project_id,source_id,source_version_id,evidence_id)
   REFERENCES project_evidence(project_id,source_id,source_version_id,id),
 FOREIGN KEY(project_id,context_id,evidence_id)
   REFERENCES project_context_evidence_pins(project_id,context_id,evidence_id),
 FOREIGN KEY(project_id,evidence_id)
   REFERENCES project_evidence_grounding(project_id,evidence_id)
);
CREATE INDEX psi_context_evidence_scope ON project_context_grounded_evidence_pins(project_id,source_id,source_version_id);

-- Content hash contains no snapshot UUID, creation time, approval, secrets or
-- raw filesystem path. Approval is appended later as a distinct immutable row.
CREATE TABLE project_context_build_inputs (
 project_id TEXT NOT NULL,
 context_id UUID NOT NULL,
 input_fingerprint TEXT NOT NULL CHECK(input_fingerprint ~ '^[0-9a-f]{64}$'),
 builder_version TEXT NOT NULL CHECK(length(builder_version) BETWEEN 1 AND 120),
 created_at TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp(),
 PRIMARY KEY(project_id,context_id),
 FOREIGN KEY(project_id,context_id) REFERENCES project_context_snapshots(project_id,id)
);
CREATE INDEX psi_context_input_fingerprint ON project_context_build_inputs(project_id,input_fingerprint);

CREATE TRIGGER psi_context_analysis_pins_immutable BEFORE UPDATE OR DELETE ON project_context_analysis_pins
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER psi_context_grounding_pins_immutable BEFORE UPDATE OR DELETE ON project_context_grounded_evidence_pins
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
CREATE TRIGGER psi_context_build_inputs_immutable BEFORE UPDATE OR DELETE ON project_context_build_inputs
 FOR EACH ROW EXECUTE FUNCTION psi_block_immutable_write();
