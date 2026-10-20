-- Preserve the provider's actual sub-micro-dollar usage cost in PostgreSQL.
-- OpenRouter can return more than 8 fractional decimal places (e.g. $0.000048472).
-- These alters do not rewrite existing quote/approval values semantically.
ALTER TABLE tb_runs
  ALTER COLUMN authorized_usd TYPE NUMERIC(24,15),
  ALTER COLUMN spent_usd TYPE NUMERIC(24,15),
  ALTER COLUMN reserved_usd TYPE NUMERIC(24,15);
ALTER TABLE tb_stage_attempts
  ALTER COLUMN reservation_usd TYPE NUMERIC(24,15),
  ALTER COLUMN charged_usd TYPE NUMERIC(24,15);
ALTER TABLE tb_cost_quotes
  ALTER COLUMN low_usd TYPE NUMERIC(24,15),
  ALTER COLUMN expected_usd TYPE NUMERIC(24,15),
  ALTER COLUMN high_usd TYPE NUMERIC(24,15);
ALTER TABLE tb_spending_consents
  ALTER COLUMN authorized_usd TYPE NUMERIC(24,15);
