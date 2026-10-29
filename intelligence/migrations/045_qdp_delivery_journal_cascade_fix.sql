-- FIX-12 forward-only cleanup fix for append-only journal.
-- Direct UPDATE/DELETE remains forbidden while its parent delivery exists.
-- The pre-existing qdp_runs -> outbox -> delivery ON DELETE CASCADE FK chain
-- must be allowed to prune child audit rows when the parent is removed.
-- Do not alter already applied 044 checksum or recreate peer tables.
CREATE OR REPLACE FUNCTION qdp_reject_delivery_attempt_log_mutation()
RETURNS trigger
LANGUAGE plpgsql AS $qdp_delivery_log_protect$
BEGIN
 IF TG_OP='DELETE' AND NOT EXISTS (
    SELECT 1 FROM qdp_outbox_deliveries d
     WHERE d.event_id=OLD.event_id AND d.consumer=OLD.consumer
 ) THEN
   -- Parent has already been deleted by an authorized FK CASCADE.
   RETURN OLD;
 END IF;
 RAISE EXCEPTION 'QDP delivery attempt journal is immutable'
  USING ERRCODE='55000';
END $qdp_delivery_log_protect$;
