"""GB-MOD-04: no-network QDP v1 parsing, authoritative read, ACK order."""
from __future__ import annotations
from contextlib import contextmanager
from dataclasses import asdict, replace
from unittest.mock import patch
from uuid import uuid4
import unittest

from intelligence.group_b.contracts import ContractError, canonical_hash
from intelligence.group_b.adapters.qdp import (
    QDPHandoff, VerifiedPreparation, read_current_preparation, require_qdp_schema,
)
from intelligence.group_b.intake import (
    InboxReceipt, _TransactionOutcome, receive_qdp_delivery,
)
from intelligence.group_b.errors import DependencyUnavailable, DependencyProbeFailed

PAYLOAD = {
    "event_id": str(uuid4()), "project_id": "a"*32, "qdp_run_id": str(uuid4()),
    "video_id": 75, "analysis_group": "B",
    "input_sha256": "b"*64, "schema_version": "qdp-handoff/v1",
}
INPUTS = {
    "result_schema":"qdp-preparation/v1","enrichment_source_id":"source-a",
    "source_policy_version_id":8, "metadata_id":4,"media_asset_id":6,
    "media_sha256":"c"*64, "media_kind":"video",
}
PAYLOAD = dict(PAYLOAD, input_sha256=canonical_hash(INPUTS))


class QueryResult:
    def __init__(self, row=None, rows=None):
        self.row, self.rows = row, rows
    def fetchone(self):
        return self.row
    def fetchall(self):
        return self.rows if self.rows is not None else []


def good_row(payload=PAYLOAD):
    return {
        "event_payload":payload,"event_sha":canonical_hash(payload),
        "event_type":"preparation_ready","event_version":"qdp-event/v1",
        "delivery_state":"leased","qdp_state":"completed","cancel_requested":False,
        "preparation_state":"ready","input_sha256":canonical_hash(INPUTS),
        "inputs":INPUTS,
        "source_id":"source-a","source_policy_version_id":8,
        "media_asset_id":6,"metadata_id":4,"metadata":{"content_kind":"video"},
        "enrichment_expires_at":None,"source_state":"enabled",
        "can_analyze_media":True,"policy_active":True,
        "policy_permissions":{"can_analyze_media":True},
        "content_kind":"video","media_sha256":"c"*64,
        "media_video_id":75,"media_source_id":"source-a",
        "media_permission":"analysis","media_expires_at":None,
    }


class FakeQDPConn:
    def __init__(self,row=None,missing=()):
        self.row = row
        self.missing = missing
    def execute(self,query,args=()):
        if "to_regclass(" in query:
            return QueryResult(rows=[(k,) for k in self.missing])
        if "FROM qdp_outbox o" in query:
            return QueryResult(row=self.row)
        if "SELECT %s <= now()" in query:
            return QueryResult(row=(False,))
        raise AssertionError("Unexpected query")


class RecordingConn:
    def __init__(self, log):
        self.log = log
    def __enter__(self):
        self.log.append("open")
        return self
    def __exit__(self,exc_type,exc,tb):
        self.log.append("rollback" if exc_type else "commit")
        return False
    @contextmanager
    def transaction(self):
        self.log.append("transaction")
        yield
        self.log.append("transaction_done")


class RecordingAcknowledger:
    def __init__(self,log,fail=False):
        self.log,self.fail=log,fail
    def acknowledge(self,*,event_id,durable_receipt):
        self.log.append("ack")
        if self.fail:
            raise RuntimeError("provider secret")


class TestQDPContract(unittest.TestCase):
    def test_strict_group_ids_hash_and_schema(self):
        self.assertEqual(QDPHandoff.parse(PAYLOAD).analysis_group,"B")
        bad = [
            dict(PAYLOAD, analysis_group="A"), dict(PAYLOAD, analysis_group="C"),
            dict(PAYLOAD, video_id=True), dict(PAYLOAD, video_id=0),
            dict(PAYLOAD, project_id="A"*32), dict(PAYLOAD, input_sha256="F"*64),
            dict(PAYLOAD, event_id=str(uuid4()).upper()),
            dict(PAYLOAD, qdp_run_id="not uuid"),
            dict(PAYLOAD, schema_version="qdp-handoff/v2"),
            dict(PAYLOAD, secret="unknown"),
        ]
        for item in bad:
            with self.subTest(item=item), self.assertRaises(ContractError):
                QDPHandoff.parse(item)

    def test_missing_peer_schema_is_unavailable(self):
        with self.assertRaises(DependencyUnavailable):
            require_qdp_schema(FakeQDPConn(missing=("qdp_outbox","qdp_preparations")))
        require_qdp_schema(FakeQDPConn())

    def test_exact_authoritative_qdp_preparation(self):
        row=good_row()
        with self.subTest("success"):
            got=read_current_preparation(FakeQDPConn(row),QDPHandoff.parse(PAYLOAD),
                                         canonical_hash(PAYLOAD))
            self.assertEqual(got.source_id,"source-a")
            self.assertEqual(got.content_type,"video")
            self.assertEqual(got.media_sha256,"c"*64)

    def test_revoke_hash_and_lineage_all_fail_closed(self):
        cases = [
            {"event_type":"preparation_blocked"},
            {"event_version":"qdp-event/v2"},
            {"delivery_state":"dead_letter"},
            {"qdp_state":"cancelled"},
            {"cancel_requested":True},
            {"preparation_state":"blocked"},
            {"source_state":"disabled"},
            {"can_analyze_media":False},
            {"policy_active":False},
            {"policy_permissions":{"can_analyze_media":False}},
            {"media_source_id":"source-b"},
            {"media_video_id":99},
            {"media_sha256":"d"*64},
            {"content_kind":"image_post"},
            {"metadata":{"content_kind":"mixed_carousel"}},
            {"media_permission":"metadata_only"},
            {"inputs":dict(INPUTS, media_asset_id=99)},
            {"event_payload":dict(PAYLOAD,analysis_group="A")},
        ]
        for changes in cases:
            with self.subTest(changes=changes):
                row=dict(good_row(),**changes)
                with self.assertRaises(DependencyUnavailable):
                    read_current_preparation(FakeQDPConn(row),QDPHandoff.parse(PAYLOAD),
                                             canonical_hash(PAYLOAD))
        with self.assertRaises(DependencyUnavailable):
            read_current_preparation(FakeQDPConn(),QDPHandoff.parse(PAYLOAD),
                                     canonical_hash(PAYLOAD))
        with self.assertRaises(DependencyUnavailable):
            read_current_preparation(FakeQDPConn(good_row()),QDPHandoff.parse(PAYLOAD),
                                     "f"*64)

    def test_carousel_requires_real_slide_manifest(self):
        inputs=dict(INPUTS,media_kind="carousel")
        evt=dict(PAYLOAD,input_sha256=canonical_hash(inputs))
        row=dict(good_row(evt),inputs=inputs,input_sha256=canonical_hash(inputs),
                 content_kind="photo_carousel",metadata={"content_kind":"photo_carousel"})
        with self.assertRaises(DependencyUnavailable) as error:
            read_current_preparation(FakeQDPConn(row),QDPHandoff.parse(evt),
                                     canonical_hash(evt))
        self.assertEqual(error.exception.reason,"carousel_media_manifest_unavailable")

    def test_ack_happens_only_after_connection_commit(self):
        log=[]
        outcome=_TransactionOutcome(InboxReceipt(
            PAYLOAD["event_id"],PAYLOAD["project_id"],"inbox-id","run-id","gb-receipt",True),None)
        with patch("intelligence.group_b.intake._receive_tx",return_value=outcome):
            result=receive_qdp_delivery(lambda:RecordingConn(log),PAYLOAD,canonical_hash(PAYLOAD),
                                        acknowledger=RecordingAcknowledger(log))
        self.assertTrue(result.acknowledged)
        self.assertFalse(result.execution_ready)
        self.assertEqual(log,["open","transaction","transaction_done","commit","ack"])

    def test_conflict_is_committed_but_never_acked(self):
        log=[]
        outcome=_TransactionOutcome(None,"event_hash_conflict")
        with patch("intelligence.group_b.intake._receive_tx",return_value=outcome):
            with self.assertRaises(ContractError):
                receive_qdp_delivery(lambda:RecordingConn(log),PAYLOAD,
                                     canonical_hash(PAYLOAD),
                                     acknowledger=RecordingAcknowledger(log))
        self.assertEqual(log,["open","transaction","transaction_done","commit"])

    def test_ack_failure_does_not_undo_durable_inbox(self):
        log=[]
        outcome=_TransactionOutcome(InboxReceipt(
            PAYLOAD["event_id"],PAYLOAD["project_id"],"inbox-id","run-id","gb-receipt",False),None)
        with patch("intelligence.group_b.intake._receive_tx",return_value=outcome):
            with self.assertRaises(DependencyProbeFailed) as error:
                receive_qdp_delivery(lambda:RecordingConn(log),PAYLOAD,
                                     canonical_hash(PAYLOAD),
                                     acknowledger=RecordingAcknowledger(log,fail=True))
        self.assertNotIn("secret",str(error.exception))
        self.assertEqual(log[-2:],["commit","ack"])

    def test_no_qdp_peer_module_loaded(self):
        import sys
        self.assertNotIn("intelligence.qualified_data",sys.modules)


if __name__=="__main__":
    unittest.main()
