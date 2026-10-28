"""M37 opt-in Collector -> M35 adapter matching the peer DurableSREInboxPort.

No import or runtime dependency on intelligence.collector. A trusted host must
inject independent project->approved-C9 publication resolution, DB connection
and authorizer. This object is NOT an HTTP/webhook adapter: SHA integrity is
never sender authentication. Disabled by default; no paid/network calls.
"""
from __future__ import annotations

from .collector_inbox import receive_collector_delivery
from .ports.inbound import COLLECTOR_DELIVERY_VERSION, validate_collector_delivery
from .v1_contracts import canonical_uuid, project_id, reject

ACK_VERSION = "sre.inbox_ack/v1"


class CollectorDurableInboxAdapter:
    """Implements Collector's capabilities()/deliver() protocol, SRE-owned.

    Only install this object in authenticated server-side Collector dispatch.
    The publication resolver MUST read authoritative project scope; it MUST
    NOT accept incoming collector_provenance as C9 approval. The M35 receiver
    additionally rechecks project-approved H1/H2 and original D1 policy.
    """

    def __init__(self, connect, publication_resolver, *,
                 actor_id, actor_authorizer, enabled=False):
        if (not callable(connect) or not callable(publication_resolver)
                or not callable(actor_authorizer) or type(enabled) is not bool
                or type(actor_id) is not str or not actor_id):
            reject("adapter", "trusted DB, publication, actor and permission ports required",
                   "invalid_contract")
        self._connect = connect
        self._publication = publication_resolver
        self._actor = actor_id
        self._authorized = actor_authorizer
        self._enabled = enabled

    def _schema_ready(self):
        try:
            with self._connect() as conn:
                row = conn.execute(
                    """SELECT to_regclass('sre_workspaces') IS NOT NULL,
                              to_regclass('sre_collector_inbox') IS NOT NULL,
                              to_regclass('sre_content_versions') IS NOT NULL"""
                ).fetchone()
                return row == (True, True, True)
        except Exception:
            # Never advertise a ready inbox on a failed schema check.
            return False

    def capabilities(self):
        installed = self._schema_ready() if self._enabled else False
        return {
            "schema_version": COLLECTOR_DELIVERY_VERSION,
            "authenticated": True,
            "durable_inbox": installed,
            "ready": self._enabled and installed,
        }

    def deliver(self, message):
        if not self._enabled or not self._schema_ready():
            reject("collector_delivery", "operator-disabled or missing durable SRE schema",
                   "rights_denied")
        event = validate_collector_delivery(message)
        pid = project_id(event["project_id"])
        # Resolution is independent of received event. Host must map project
        # to an explicitly approved publication before this adapter can write.
        publication = canonical_uuid(
            self._publication(pid), "trusted_approved_publication_id"
        )
        with self._connect() as conn:
            result = receive_collector_delivery(
                conn, event, publication,
                actor_id=self._actor, actor_authorizer=self._authorized,
            )
        if (result.get("state") != "acked"
                or result.get("event_id") != event["event_id"]
                or result.get("project_id") != pid
                or result.get("approved_publication_id") != publication):
            # A durable QUARANTINE is NOT a Collector success receipt.
            reject("collector_delivery",
                   "SRE event not committed as authenticated, current D1 intake",
                   "rights_denied")
        return {
            "schema_version": ACK_VERSION,
            "event_id": event["event_id"],
            "project_id": pid,
            "event_sha256": event["event_sha256"],
            "persisted": True,
            "durable_receipt_id": "sre:m35:" + event["event_id"],
        }


__all__ = ["ACK_VERSION", "CollectorDurableInboxAdapter"]
