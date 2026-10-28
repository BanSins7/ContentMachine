"""M37 pinned Git source mirror and negative interoperability evidence."""
import tempfile
import unittest
from pathlib import Path
from uuid import uuid4

from intelligence.relevance.peer_contract_audit import (
    PEERS, VERSION, git_blob_id, inspect_peer_mirror, report_without_mirror,
)
from intelligence.relevance.ports.outbound import validate_live_result_event
from intelligence.relevance.ports.inbound import validate_collector_delivery
from intelligence.relevance.v1_contracts import SREContractError, fingerprint
from test_sre_module_ports import live_event, collector_event


class M37PinnedSourceMirrorTests(unittest.TestCase):
    def test_exact_pinned_commit_sha_and_real_git_blobs_present(self):
        self.assertEqual(PEERS["collector"]["commit"],
                         "8e17f32aeb2501ec4dc0f6aca892e360201a6e8d")
        self.assertEqual(PEERS["qdp"]["commit"],
                         "a0bb24e1c2d6ebd7be0a69c57a1978b66747cbfa")
        self.assertEqual(git_blob_id(b"hello"),
                         "b6fc4c620b67d95f953a5c1c1230aaab5db5a1b0")
        self.assertEqual(len(PEERS["collector"]["files"]),2)
        self.assertEqual(len(PEERS["qdp"]["files"]),3)

    def test_missing_peer_source_cannot_claim_compatibility(self):
        with tempfile.TemporaryDirectory() as scratch:
            result=inspect_peer_mirror(scratch)
        self.assertEqual(result["schema_version"],VERSION)
        self.assertEqual(result["ecosystem_connected"],"blocked")
        self.assertFalse(result["real_e2e_executed"])
        self.assertFalse(result["qdp_reread_uses_sre_m30_m32_tables"])
        self.assertEqual(len(result["files"]),5)
        self.assertTrue(all(f["state"]=="missing" for f in result["files"]))
        self.assertEqual(result["peer_source_states"]["qdp"],
                         "blocked_missing_or_changed_source")

    def test_unpinned_fake_checkout_is_sha_mismatch_not_verified(self):
        with tempfile.TemporaryDirectory() as scratch:
            for peer,meta in PEERS.items():
                for path in meta["files"]:
                    target=Path(scratch)/peer/path
                    target.parent.mkdir(parents=True,exist_ok=True)
                    target.write_text("VERSION = 'sre.classifications_ready/v1'\n",
                                      encoding="utf-8")
            result=inspect_peer_mirror(scratch)
        self.assertEqual(result["ecosystem_connected"],"blocked")
        self.assertTrue(all(x["state"]=="sha_mismatch" for x in result["files"]))

    def test_unverified_source_report_does_not_claim_peer_e2e(self):
        report=report_without_mirror()
        self.assertEqual(report["ecosystem_connected"],"blocked")
        self.assertFalse(report["local_mirror_verified"])
        self.assertFalse(report["real_e2e_executed"])
        self.assertEqual({i["code"] for i in report["findings"]},{
            "COLLECTOR_ADAPTER_NOT_INSTALLED","QDP_EVENT_AND_STORAGE_MISMATCH"})
        self.assertEqual(report["paid_calls"],0)


class M37IdDomainAndEventConflictTests(unittest.TestCase):
    def test_uuid_collector_query_is_never_d1_bigint(self):
        event=collector_event()
        checked=validate_collector_delivery(event)
        self.assertIsInstance(checked["collector_query_ids"][0],str)
        self.assertIsInstance(checked["discovery_id"],int)
        self.assertIsInstance(checked["source_request_id"],int)
        wrong=dict(event)
        wrong["source_request_id"]=checked["collector_query_ids"][0]
        wrong["event_sha256"]=fingerprint({
            k:v for k,v in wrong.items() if k!="event_sha256"
        })
        with self.assertRaises(SREContractError):
            validate_collector_delivery(wrong)

    def test_qdp_run_summary_cannot_impersonate_sre_m36_item_event(self):
        original=live_event()
        summary={
            "schema_version":"sre-analytics-event/v1",
            "event_id":str(uuid4()),
            "event_type":"sre.classification_run_completed",
            "project_id":original["project_id"],
            "run_id":original["run_id"],
            "publication_id":original["publication_id"],
            "taxonomy_version_id":original["sre_taxonomy_version_id"],
            "occurred_at":original["occurred_at"],
        }
        with self.assertRaises(SREContractError):
            validate_live_result_event(summary)
        with self.assertRaises(SREContractError):
            validate_collector_delivery(summary)

    def test_sre_item_ready_is_not_a_qdp_run_completion(self):
        ready=validate_live_result_event(live_event())
        self.assertNotEqual(ready["schema_version"],"sre-analytics-event/v1")
        self.assertIn("decisions",ready)
        self.assertNotIn("event_type",ready)
        self.assertNotIn("taxonomy_version_id",ready)
        # An event envelope hash is integrity evidence, never actor identity.
        self.assertEqual(ready["event_sha256"],fingerprint({
            k:v for k,v in ready.items() if k!="event_sha256"
        }))


if __name__=="__main__":
    unittest.main()
