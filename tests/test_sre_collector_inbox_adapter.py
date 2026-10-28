"""M37 Collector producer adapter tests, no Collector package needed."""
import unittest
from unittest.mock import patch
from intelligence.relevance.collector_inbox_adapter import (
    CollectorDurableInboxAdapter, ACK_VERSION,
)
from intelligence.relevance.ports.inbound import validate_collector_delivery
from intelligence.relevance.v1_contracts import SREContractError
from test_sre_module_ports import collector_event
from test_sre_contracts_v1 import PUB


class Result:
    def __init__(self,row):
        self.row=row
    def fetchone(self):
        return self.row


class Connect:
    def __init__(self,ready=True):
        self.ready=ready
        self.statements=[]
    def __call__(self):
        return self
    def __enter__(self):
        return self
    def __exit__(self,*args):
        return False
    def execute(self,sql):
        self.statements.append(sql)
        return Result((self.ready,self.ready,self.ready))


class AdapterTests(unittest.TestCase):
    def make(self,conn,*,enabled=True,publication=lambda pid:PUB):
        return CollectorDurableInboxAdapter(
            conn,publication,actor_id="collector:server",
            actor_authorizer=lambda pid,actor,action:True,enabled=enabled)

    def test_disabled_by_default_and_missing_schema_not_advertised(self):
        conn=Connect()
        adapter=self.make(conn,enabled=False)
        self.assertEqual(adapter.capabilities(),{
            "schema_version":"sre.discovery_ready/v1",
            "authenticated":True,"durable_inbox":False,"ready":False,
        })
        self.assertEqual(conn.statements,[])
        with self.assertRaises(SREContractError):
            adapter.deliver(collector_event())
        absent=self.make(Connect(ready=False))
        self.assertIs(absent.capabilities()["ready"],False)
        with self.assertRaises(SREContractError):
            absent.deliver(collector_event())

    def test_successful_trusted_m35_ack_exact_collector_format(self):
        conn=Connect()
        adapter=self.make(conn)
        event=collector_event()
        def accept(db,received,publication,*,actor_id,actor_authorizer):
            self.assertEqual(received,event)
            self.assertEqual(publication,PUB)
            self.assertEqual(actor_id,"collector:server")
            self.assertTrue(callable(actor_authorizer))
            return {"state":"acked","project_id":event["project_id"],
                    "event_id":event["event_id"],
                    "approved_publication_id":publication}
        with patch("intelligence.relevance.collector_inbox_adapter.receive_collector_delivery",
                   side_effect=accept) as real:
            receipt=adapter.deliver(event)
            replay=adapter.deliver(event)
        self.assertEqual(real.call_count,2)
        self.assertEqual(receipt,replay)
        self.assertEqual(set(receipt),{
            "schema_version","event_id","project_id","event_sha256",
            "persisted","durable_receipt_id",
        })
        self.assertEqual(receipt["schema_version"],ACK_VERSION)
        self.assertIs(receipt["persisted"],True)
        self.assertEqual(receipt["event_sha256"],event["event_sha256"])
        self.assertTrue(receipt["durable_receipt_id"].startswith("sre:m35:"))

    def test_quarantine_or_foreign_project_never_becomes_ack(self):
        conn=Connect();adapter=self.make(conn)
        evt=collector_event()
        for receipt in (
            {"state":"quarantined","event_id":evt["event_id"],
             "project_id":evt["project_id"],
             "approved_publication_id":PUB},
            {"state":"acked","event_id":evt["event_id"],
             "project_id":"0"*32,"approved_publication_id":PUB},
        ):
            with patch("intelligence.relevance.collector_inbox_adapter.receive_collector_delivery",
                       return_value=receipt):
                with self.assertRaises(SREContractError):
                    adapter.deliver(evt)

    def test_project_publication_never_read_from_collector_message(self):
        conn=Connect()
        evt=collector_event()
        adapter=self.make(conn,publication=lambda pid:"bad-publication")
        with patch("intelligence.relevance.collector_inbox_adapter.receive_collector_delivery") as write:
            with self.assertRaises(SREContractError):
                adapter.deliver(evt)
            write.assert_not_called()

    def test_wrong_hash_uuid_as_bigint_and_invalid_actor_ports_blocked(self):
        conn=Connect()
        adapter=self.make(conn)
        evt=collector_event()
        altered=dict(evt);altered["collector_query_ids"]=[123]
        with patch("intelligence.relevance.collector_inbox_adapter.receive_collector_delivery") as write:
            with self.assertRaises(SREContractError):
                adapter.deliver(altered)
            write.assert_not_called()
        with self.assertRaises(SREContractError):
            CollectorDurableInboxAdapter(conn,lambda pid:PUB,
                actor_id="",actor_authorizer=lambda *a:True,enabled=True)


if __name__=="__main__":
    unittest.main()
