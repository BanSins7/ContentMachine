-- QDP FIX-12 forward-only durable per-consumer delivery attempt journal.
-- Do not rewrite applied 040 / 041 / 042 / 043 or peer-owned schemas.
-- This is QDP's producer-side audit, not the consumer's inbox/job table.
CREATE TABLE qdp_delivery_attempt_log (
 id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
 event_id UUID NOT NULL,
 consumer TEXT NOT NULL,
 lease_generation BIGINT NOT NULL CHECK (lease_generation > 0),
 attempt INTEGER NOT NULL CHECK (attempt > 0),
 transition TEXT NOT NULL CHECK (
   transition IN ('leased','retry_scheduled','acknowledged','dead_letter')),
 reason_code TEXT,
 occurred_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 FOREIGN KEY (event_id,consumer)
   REFERENCES qdp_outbox_deliveries(event_id,consumer) ON DELETE CASCADE,
 CHECK ((transition IN ('leased','acknowledged') AND reason_code IS NULL)
    OR (transition IN ('retry_scheduled','dead_letter')
       AND reason_code IS NOT NULL
       AND length(reason_code) BETWEEN 1 AND 120))
);
CREATE INDEX idx_qdp_delivery_attempt_event
 ON qdp_delivery_attempt_log(event_id,consumer,id);
CREATE INDEX idx_qdp_delivery_attempt_by_transition
 ON qdp_delivery_attempt_log(transition,occurred_at);

CREATE FUNCTION qdp_reject_delivery_attempt_log_mutation() RETURNS trigger
LANGUAGE plpgsql AS $qdp_delivery_log_protect$
BEGIN
 RAISE EXCEPTION 'QDP delivery attempt journal is immutable'
  USING ERRCODE='55000';
END $qdp_delivery_log_protect$;

CREATE TRIGGER qdp_delivery_attempt_log_immutable
 BEFORE UPDATE OR DELETE ON qdp_delivery_attempt_log
 FOR EACH ROW EXECUTE FUNCTION qdp_reject_delivery_attempt_log_mutation();
