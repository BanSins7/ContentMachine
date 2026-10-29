"""M35 optional Collector inbox: offline event, tenant, ACK, quarantine tests."""
from __future__ import annotations

import copy
import unittest
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import patch

import intelligence.relevance.collector_inbox as inbox
from intelligence.relevance.v1_contracts import SREContractError
from test_sre_module_ports import collector_event
from test_sre_contracts_v1 import PUB, PID

CONTENT_SHA = "a" * 64


class Rows:
    def __init__(self, row=None):
        self.row = row

    def fetchone(self):
        return self.row


class FakeAutocommitConn:
    def __init__(self, existing=None):
        self.autocommit = True
        self.info = SimpleNamespace(transaction_status=SimpleNamespace(name="IDLE"))
        self.existing = existing
        self.inserts = []
        self.sql = []

    def transaction(self):
        return nullcontext()

    def execute(self, sql, params=None):
        self.sql.append((sql, params))
        if sql.startswith("SET TRANSACTION"):
            return Rows()
        if sql == inbox.WORKSPACE_LOCK:
            return Rows((1,))
        if sql == inbox.INBOX_READ:
            return Rows(self.existing)
        if sql == inbox.INBOX_INSERT:
            self.inserts.append(params)
            return Rows()
        raise AssertionError("unexpected SQL " + sql[:120])


def trusted(project, actor, action):
    return project == PID and actor == "sre:collector" and action in (
        "receive_sre_collector_delivery", "backfill_sre_d1",
    )


def fake_proof(event, content_id=101):
    return {
        "project_id": PID, "content_version_id": content_id,
        "discovery_id": event["discovery_id"], "video_id": event["video_id"],
        "source_id": event["source_id"],
        "source_policy_version_id": event["source_policy_version_id"],
        "source_request_id": event["source_request_id"],
        "content_sha256": CONTENT_SHA,
    }


class CollectorInboxUnitTests(unittest.TestCase):
    def _call(self, conn, event=None, **kw):
        return inbox.receive_collector_delivery(
            conn, event or collector_event(), PUB, actor_id="sre:collector",
            actor_authorizer=trusted, **kw
        )

    def test_unauthorized_and_uncommittable_connections_never_touch_d1(self):
        e = collector_event()
        conn = FakeAutocommitConn()
        with self.assertRaises(SREContractError):
            inbox.receive_collector_delivery(
                conn, e, PUB, actor_id="client:self",
                actor_authorizer=trusted,
            )
        self.assertEqual(conn.sql, [])
        conn.autocommit = False
        with self.assertRaises(SREContractError):
            self._call(conn)
        self.assertEqual(conn.sql, [])

    def test_invalid_event_fails_before_db_access_and_never_quarantines_forgery(self):
        event = collector_event()
        event["collector_query_ids"] = ["99999999-9999-4999-8999-999999999999"]
        conn = FakeAutocommitConn()
        with self.assertRaises(SREContractError):
            self._call(conn, event)
        self.assertEqual(conn.sql, [])
        self.assertEqual(conn.inserts, [])

    def test_ack_is_after_c9_and_d1_and_contains_no_collector_query_cast(self):
        event = collector_event()
        conn = FakeAutocommitConn()
        with (patch.object(inbox, "read_authorized_taxonomy", return_value={}) as c9,
              patch.object(inbox, "_d1_event_check", return_value=None) as d1,
              patch.object(inbox, "attach_discovery", return_value={
                  "content_version_id": 101,
                  "content_sha256": CONTENT_SHA, "replayed": False,
              }) as attach,
              patch.object(inbox, "read_d1_provenance",
                           return_value=fake_proof(event)) as proof):
            result = self._call(conn, event)
        self.assertEqual(result["state"], "acked")
        self.assertFalse(result["replayed"])
        self.assertTrue(result["readable_now"])
        self.assertEqual(result["content_version_id"], 101)
        self.assertEqual(result["paid_calls"], 0)
        self.assertEqual(len(conn.inserts), 1)
        self.assertEqual(conn.inserts[0][11], "acked")
        self.assertEqual(conn.inserts[0][12:15], (101, CONTENT_SHA, None))
        self.assertFalse(any("collector_query_ids" in sql or "::bigint" in sql
                             for sql, _ in conn.sql))
        c9.assert_called_once_with(conn, PID, PUB)
        d1.assert_called_once_with(conn, event)
        attach.assert_called_once_with(conn, PID, event["discovery_id"])
        proof.assert_called_once_with(conn, PID, 101)

    def test_bad_d1_provenance_is_quarantined_without_s3_receipt(self):
        event = collector_event()
        conn = FakeAutocommitConn()
        with (patch.object(inbox, "read_authorized_taxonomy", return_value={}),
              patch.object(inbox, "_d1_event_check", side_effect=SREContractError(
                  "rights_denied", "source", "source disabled")),
              patch.object(inbox, "attach_discovery") as attach):
            result = self._call(conn, event)
        self.assertEqual(result["state"], "quarantined")
        self.assertEqual(result["quarantine_reason"], "rights_denied")
        self.assertIsNone(result["content_version_id"])
        self.assertFalse(result["readable_now"])
        self.assertEqual(conn.inserts[0][11:15],
                         ("quarantined", None, None, "rights_denied"))
        attach.assert_not_called()

    def test_wrong_d1_version_after_attach_rolls_back_and_quarantines(self):
        event = collector_event()
        conn = FakeAutocommitConn()
        bad = fake_proof(event)
        bad["source_request_id"] = 999
        with (patch.object(inbox, "read_authorized_taxonomy", return_value={}),
              patch.object(inbox, "_d1_event_check", return_value=None),
              patch.object(inbox, "attach_discovery", return_value={
                  "content_version_id": 101,
                  "content_sha256": CONTENT_SHA, "replayed": False,
              }),
              patch.object(inbox, "read_d1_provenance", return_value=bad)):
            result = self._call(conn)
        self.assertEqual(result["state"], "quarantined")
        self.assertIsNone(conn.inserts[0][12])

    def test_ack_replay_no_second_s3_attachment_and_revoked_read_not_restored(self):
        event = collector_event()
        existing = (
            PID, event["event_sha256"], PUB, event["discovery_id"],
            event["video_id"], event["source_id"],
            event["source_policy_version_id"], event["source_request_id"],
            "acked", 101, CONTENT_SHA, None,
        )
        conn = FakeAutocommitConn(existing=existing)
        with (patch.object(inbox, "read_authorized_taxonomy", return_value={}),
              patch.object(inbox, "read_d1_provenance",
                           side_effect=SREContractError(
                               "rights_denied", "content", "revoked")),
              patch.object(inbox, "attach_discovery") as attach):
            result = self._call(conn)
        self.assertEqual(result["state"], "acked")
        self.assertTrue(result["replayed"])
        self.assertFalse(result["readable_now"])
        self.assertEqual(conn.inserts, [])
        attach.assert_not_called()

    def test_global_event_uuid_cannot_be_rebound_to_other_discovery(self):
        event = collector_event()
        existing = (
            PID, event["event_sha256"], PUB, event["discovery_id"]+1,
            event["video_id"], event["source_id"],
            event["source_policy_version_id"], event["source_request_id"],
            "quarantined", None, None, "rights_denied",
        )
        with patch.object(inbox, "read_authorized_taxonomy", return_value={}):
            with self.assertRaises(SREContractError):
                self._call(FakeAutocommitConn(existing), event)

    def test_direct_d1_backfill_needs_explicit_ids_and_no_collector(self):
        for ids in ([], [True], ["uuid"], [7, 7], list(range(101))):
            with self.subTest(ids=str(ids)[:30]), self.assertRaises(SREContractError):
                inbox.backfill_selected_d1_discoveries(
                    FakeAutocommitConn(), PID, PUB, ids,
                    actor_id="sre:collector", actor_authorizer=trusted,
                )
        conn = FakeAutocommitConn()
        with (patch.object(inbox, "read_authorized_taxonomy", return_value={}),
              patch.object(inbox, "attach_discovery", side_effect=lambda c,p,d: {
                  "content_version_id": d+100,
                  "content_sha256": CONTENT_SHA, "replayed": False,
              }) as attach,
              patch.object(inbox, "read_d1_provenance",
                           side_effect=lambda c,p,content_id: {
                               "discovery_id": content_id-100
                           })):
            result = inbox.backfill_selected_d1_discoveries(
                conn, PID, PUB, [9, 7], actor_id="sre:collector",
                actor_authorizer=trusted,
            )
        self.assertEqual([x["discovery_id"] for x in result["records"]], [7, 9])
        self.assertEqual(result["imported"], 2)
        self.assertFalse(result["collector_required"])
        self.assertEqual([x.args[2] for x in attach.call_args_list], [7, 9])


if __name__ == "__main__":
    unittest.main()
