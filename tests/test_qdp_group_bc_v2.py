"""QDP v2 -> Group B real wire compatibility and B/C fan-out regressions."""
from __future__ import annotations

from dataclasses import asdict
from types import SimpleNamespace
from unittest import TestCase
from unittest.mock import patch

from intelligence.group_b.adapters.qdp import QDPHandoff
from intelligence.group_b.contracts import canonical_hash
from intelligence.group_b.errors import DependencyUnavailable
from intelligence.group_bc.qdp_b_v2 import read_qdp_v2
from intelligence.qualified_data.integrations.contracts import QDPHandoffPointer
from intelligence.qualified_data.integrations.group_b import (
    QDP_EVENT, QDP_PREPARATION, from_installed_group_b, group_b_capability,
)
from intelligence.qualified_data.group_bc_runtime import dispatch_groups_bc_once

PROJECT = "c" * 32
EVENT = "00000000-0000-4000-8000-000000000041"
RUN = "00000000-0000-4000-8000-000000000042"
INPUTS = {
    "result_schema":"qdp-preparation/v2", "group":"B",
    "enrichment_source_id":"source-1", "source_policy_version_id":9,
    "metadata_id":4, "media_asset_id":14, "media_sha256":"e" * 64,
    "media_kind":"video", "media_size_bytes":1024,
}
INPUT_SHA = canonical_hash(INPUTS)
PAYLOAD = {
    "schema_version":"qdp-event/v1", "event_type":"preparation_ready",
    "project_id":PROJECT,"run_id":RUN,"video_id":3,
    "analysis_group":"B","state":"ready","reason_code":None,
    "input_sha256":INPUT_SHA,
}
EVENT_SHA = canonical_hash(PAYLOAD)


class _Rows:
    def __init__(self,row):
        self.row=row

    def fetchall(self):
        return []

    def fetchone(self):
        return self.row


class _Connection:
    def __init__(self,row):
        self.row=row
        self.queries=[]

    def execute(self,query,args=None):
        self.queries.append(query)
        if "unnest" in query:
            return _Rows(None)
        if "SELECT %s <= now()" in query:
            return _Rows((False,))
        return _Rows(self.row)


def fixture():
    return {
        "event_id": EVENT,
        "event_payload":PAYLOAD,
        "event_sha":EVENT_SHA,
        "event_version":"qdp-event/v1",
        "event_type":"preparation_ready",
        "delivery_state":"leased",
        "qdp_state":"completed","cancel_requested":False,
        "preparation_state":"ready","inputs":INPUTS,
        "input_sha256":INPUT_SHA,"source_id":"source-1",
        "source_policy_version_id":9,"metadata_id":4,"media_asset_id":14,
        "source_state":"enabled","can_analyze_media":True,
        "policy_active":True,
        "policy_permissions":{"can_analyze_media":True},
        "content_kind":"video","media_sha256":"e" * 64,
        "media_video_id":3,"media_source_id":"source-1",
        "media_kind":"video","media_permission":"analysis_allowed",
        "media_expires_at":None,"metadata_current":True,
        "deleted_at":None,"metadata_expires_at":None,
    }


class QDPGroupBCV2Tests(TestCase):
    def pointer(self):
        return QDPHandoffPointer(
            event_id=EVENT,project_id=PROJECT,qdp_run_id=RUN,
            video_id=3,analysis_group="B",input_sha256=INPUT_SHA,
        )

    def test_qdp_b_v2_reader_authoritative_event_and_hash(self):
        conn=_Connection(fixture())
        result=read_qdp_v2(conn,QDPHandoff.parse(asdict(self.pointer())),EVENT_SHA)
        self.assertEqual(result.event_sha256,EVENT_SHA)
        self.assertEqual(result.input_sha256,INPUT_SHA)
        self.assertEqual(result.source_policy_version_id,9)
        self.assertEqual(result.media_asset_id,14)
        self.assertEqual(result.content_type,"video")
        self.assertTrue(any("qdp_group_current_preparations" in q for q in conn.queries))
        self.assertTrue(any("FOR SHARE OF o,d,q,s,sp,v,m,md" in q for q in conn.queries))

    def test_rejects_changed_payload_or_revoked_rights(self):
        for mutation in (
            {"event_sha":"0" * 64},{"input_sha256":"0" * 64},
            {"policy_active":False},{"media_permission":"metadata_only"},
            {"metadata_current":False},{"event_version":"unsupported"},
        ):
            data=dict(fixture(),**mutation)
            with self.subTest(mutation=mutation),self.assertRaises(DependencyUnavailable):
                read_qdp_v2(
                    _Connection(data),QDPHandoff.parse(asdict(self.pointer())),EVENT_SHA)

    def test_qdp_producer_ack_only_after_real_b_receiver_receipt(self):
        from intelligence.group_bc.qdp_b_v2 import read_qdp_v2
        self.assertEqual(group_b_capability(
            peer_event_contract=QDP_EVENT,
            peer_preparation_contract=QDP_PREPARATION).state.value,"available")
        consumer=from_installed_group_b(lambda: object(),enabled=True)
        durable=SimpleNamespace(event_id=EVENT,project_id=PROJECT,
                                receipt_key="gb-"+"f"*64,run_id=RUN)
        with patch("intelligence.group_b.intake.receive_qdp_delivery",
                   return_value=durable) as receive:
            receipt=consumer.receive(self.pointer(),EVENT_SHA)
        self.assertEqual(receipt["state"],"committed")
        self.assertEqual(receipt["consumer"],"group_b")
        self.assertEqual(receipt["payload_sha256"],EVENT_SHA)
        self.assertEqual(receive.call_args.kwargs["preparation_reader"],read_qdp_v2)
        self.assertEqual(receive.call_args.args[1],asdict(self.pointer()))
        self.assertIsNone(receive.call_args.kwargs.get("acknowledger"))

    def test_b_and_c_independent_qdp_deliveries(self):
        state={"B":{"state":"intake_available"},"C":{"state":"intake_available"}}
        with patch("intelligence.qualified_data.group_bc_runtime.readiness",
                   return_value=state),patch(
                       "intelligence.qualified_data.integrations.group_b.from_installed_group_b",
                       return_value=object()),patch(
                       "intelligence.qualified_data.integrations.group_c.from_installed_group_c",
                       return_value=object()),patch(
                       "intelligence.qualified_data.outbox_delivery.dispatch_once",
                       side_effect=[{"state":"acknowledged"},{"state":"pending"}]) as dispatch:
            result=dispatch_groups_bc_once(project_id=PROJECT)
        self.assertEqual([x.args[0] for x in dispatch.call_args_list],
                         ["group_b","group_c"])
        self.assertEqual(result["results"]["B"]["state"],"acknowledged")
        self.assertEqual(result["results"]["C"]["state"],"pending")
        self.assertIs(result["group_b_analysis_complete"],False)
        self.assertIs(result["group_c_score_complete"],False)
