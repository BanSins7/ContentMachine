"""M02 standalone SRE contracts, no optional Collector/QDP or paid API calls."""
from __future__ import annotations

import copy
import importlib
import subprocess
import sys
import unittest
from uuid import UUID, uuid5

from intelligence.relevance.module import (
    MODULE_SCHEMA, EXACT_LIVE_MODEL, get_module_manifest, validate_feature_flags,
)
from intelligence.relevance.ports.taxonomy import (
    TAXONOMY_REF_VERSION, reference_from_approved_snapshot,
    validate_taxonomy_reference,
)
from intelligence.relevance.ports.discovery import (
    DISCOVERY_REF_VERSION, reference_from_authorized_content,
    validate_discovery_reference,
)
from intelligence.relevance.ports.inbound import (
    COLLECTOR_DELIVERY_VERSION, validate_collector_delivery,
)
from intelligence.relevance.ports.outbound import (
    LIVE_RESULT_VERSION, validate_live_result_event,
)
from intelligence.relevance.v1_contracts import SREContractError, fingerprint

from test_sre_contracts_v1 import (
    PID, RID, PUB, CID, K1, K2, H, HH, taxonomy, content,
)

COLLECTOR_UUID = "55555555-5555-4555-8555-555555555555"
OTHER_UUID = "66666666-6666-4666-8666-666666666666"


def collector_event():
    payload = {
        "schema_version": COLLECTOR_DELIVERY_VERSION,
        "event_id": CID,
        "producer": "video_collector",
        "project_id": PID,
        "video_id": 29, "discovery_id": 7, "source_id": "collector_tiktok",
        "source_policy_version_id": 11, "source_request_id": 12,
        # textual Collector UUID, never D1's search_queries.id BIGINT
        "collector_query_ids": [COLLECTOR_UUID, OTHER_UUID],
        "d1_persisted_at": "2026-10-23T12:00:00Z",
        "occurred_at": "2026-10-23T12:00:01Z",
    }
    payload["event_sha256"] = fingerprint(payload)
    return payload


def live_event():
    decisions = {"match":[K1], "no_match":[K2], "uncertain":[]}
    payload = {
        "schema_version": LIVE_RESULT_VERSION,
        "event_id":CID, "project_id":PID, "run_id":RID,
        "publication_id":PUB, "sre_taxonomy_version_id":14,
        "taxonomy_sha256":H, "video_id":29, "content_version_id":55,
        "content_sha256":HH, "source_policy_version_id":10,
        "execution_mode":"live", "run_status":"completed",
        "expected_hypothesis_keys":sorted([K1,K2]),
        "decisions": {k:sorted(v) for k,v in decisions.items()},
        "results_sha256":fingerprint({k:sorted(v) for k,v in decisions.items()}),
        "rights_verified_at":"2026-10-23T12:00:00Z",
        "occurred_at":"2026-10-23T12:00:01Z",
    }
    payload["event_sha256"] = fingerprint(payload)
    return payload


class ModuleManifestTests(unittest.TestCase):
    def test_default_disabled_no_optional_branches_or_fallback(self):
        manifest=get_module_manifest()
        self.assertEqual(manifest["schema_version"],MODULE_SCHEMA)
        self.assertEqual(manifest["deployment_readiness"],"disabled")
        self.assertFalse(any(manifest["features"].values()))
        self.assertEqual(manifest["requires_external_feature_branches"],[])
        self.assertEqual(manifest["live_model_policy"]["model_id"],EXACT_LIVE_MODEL)
        self.assertFalse(manifest["live_model_policy"]["allow_fast_llm"])
        self.assertFalse(manifest["live_model_policy"]["allow_fallback"])
        self.assertFalse(manifest["live_model_policy"]["allow_candidate_drop"])
        self.assertEqual(manifest["capabilities"]["outbound_qdp_contract"],
                         "contract_only_m02")

    def test_module_manifest_is_not_dynamic_health_and_is_copy_safe(self):
        a=get_module_manifest({"enabled":True,"offline_worker":True})
        self.assertEqual(a["deployment_readiness"],"runtime_preflight_required")
        a["capabilities"]["live_run_engine"]="implemented"
        a["live_model_policy"]["allow_fallback"]=True
        b=get_module_manifest()
        self.assertEqual(b["capabilities"]["live_run_engine"],"not_implemented")
        self.assertFalse(b["live_model_policy"]["allow_fallback"])

    def test_feature_flag_denials_and_dependencies(self):
        bad=[{"enabled":1},{"enabled":"true"},{"enabled":None},
             {"something_else":True},{"offline_worker":True},
             {"live_classification":True},{"enabled":True,"live_classification":True},
             {"enabled":True,"collector_event_ingest":True},
             {"enabled":True,"qdp_event_delivery":True}]
        for flags in bad:
            with self.subTest(flags=flags), self.assertRaises(SREContractError):
                validate_feature_flags(flags)
        self.assertTrue(validate_feature_flags(
            {"enabled":True,"offline_worker":True})["offline_worker"])

    def test_clean_import_does_not_import_optional_provider_or_connectors(self):
        code=("import sys; import intelligence.relevance.module; "
              "from intelligence.relevance.ports import taxonomy,discovery,inbound,outbound; "
              "assert 'intelligence.collector' not in sys.modules; "
              "assert 'intelligence.qualified_data' not in sys.modules; "
              "assert 'psycopg' not in sys.modules; "
              "assert 'openai' not in sys.modules; "
              "assert 'sentence_transformers' not in sys.modules; "
              "assert 'openrouter' not in sys.modules")
        proc=subprocess.run([sys.executable,"-c",code],capture_output=True,
                            text=True,timeout=12)
        self.assertEqual(proc.returncode,0,proc.stderr)


class TaxonomyPortTests(unittest.TestCase):
    def test_c9_approved_leaf_reference_uuid5_and_version(self):
        snap=taxonomy()
        ref=reference_from_approved_snapshot(snap)
        self.assertEqual(ref["schema_version"],TAXONOMY_REF_VERSION)
        self.assertEqual(len(ref["hypothesis_links"]),2)
        self.assertEqual(ref["hypothesis_links"][0]["category_key"],
                         str(uuid5(UUID(RID),"h1")))
        self.assertEqual(validate_taxonomy_reference(ref),ref)

    def test_wrong_uuid5_duplicate_wrong_project_version_and_extra_fields(self):
        original=reference_from_approved_snapshot(taxonomy())
        changes=[
            lambda x:x.update(project_id="bad"),
            lambda x:x.update(schema_version="sre-taxonomy-ref/v2"),
            lambda x:x.update(sre_taxonomy_version_id=True),
            lambda x:x["hypothesis_links"][0].update(category_key=OTHER_UUID),
            lambda x:x["hypothesis_links"].append(copy.deepcopy(x["hypothesis_links"][0])),
            lambda x:x["hypothesis_links"].reverse(),
            lambda x:x.update(unexpected=True),
            lambda x:x.update(rtb_run_id=PID),
        ]
        for i,change in enumerate(changes):
            with self.subTest(i=i),self.assertRaises(SREContractError):
                payload=copy.deepcopy(original)
                change(payload)
                validate_taxonomy_reference(payload)


class DiscoveryPortTests(unittest.TestCase):
    def test_reference_matches_d1_authorized_content_only(self):
        ref=reference_from_authorized_content(content())
        self.assertEqual(ref["schema_version"],DISCOVERY_REF_VERSION)
        self.assertEqual(ref["discovery_id"],1)
        self.assertEqual(ref["video_id"],29)
        self.assertEqual(ref["search_query_id"],None)
        self.assertEqual(ref["source_request_id"],4)
        self.assertEqual(validate_discovery_reference(ref),ref)

    def test_cannot_cast_collector_query_uuid_to_legacy_d1_bigint(self):
        ref=reference_from_authorized_content(content())
        for value in (COLLECTOR_UUID,True,0,-2,"29",7.0):
            with self.subTest(value=value),self.assertRaises(SREContractError):
                bad=copy.deepcopy(ref)
                bad["search_query_id"]=value
                validate_discovery_reference(bad)

    def test_wrong_provenance_and_unsupported_schema_fail(self):
        ref=reference_from_authorized_content(content())
        changes=[
            lambda x:x.update(discovery_id=None),
            lambda x:x.update(schema_version="sre-discovery-ref/v2"),
            lambda x:x.update(video_id=COLLECTOR_UUID),
            lambda x:x.update(project_id=PUB),
            lambda x:x.update(content_sha256="not-sha"),
            lambda x:x.update(expires_at="2026-11-23T00:00:00"),
            lambda x:x.update(collector_query_ids=[COLLECTOR_UUID]),
        ]
        for i,change in enumerate(changes):
            with self.subTest(i=i),self.assertRaises(SREContractError):
                item=copy.deepcopy(ref)
                change(item)
                validate_discovery_reference(item)
        missing=content()
        missing["provenance"]["discovery_id"]=None
        with self.assertRaises(SREContractError):
            reference_from_authorized_content(missing)


class CollectorPortTests(unittest.TestCase):
    def test_optional_collector_delivery_integrity(self):
        event=collector_event()
        self.assertEqual(validate_collector_delivery(event),event)
        self.assertTrue(all(isinstance(x,str) for x in event["collector_query_ids"]))

    def test_unknown_schema_wrong_ids_and_wrong_producer(self):
        old=collector_event()
        changes=[
            lambda x:x.update(schema_version="sre.discovery_ready/v2"),
            lambda x:x.update(producer="untrusted_crawler"),
            lambda x:x.update(project_id=PUB),
            lambda x:x.update(video_id=COLLECTOR_UUID),
            lambda x:x.update(source_policy_version_id=True),
            lambda x:x.update(source_request_id=COLLECTOR_UUID),
            lambda x:x.update(collector_query_ids=[COLLECTOR_UUID,COLLECTOR_UUID]),
            lambda x:x.update(collector_query_ids=[99]),
            lambda x:x.update(d1_persisted_at="2026-10-24T12:00:00Z"),
            lambda x:x.update(forged_actor="admin"),
        ]
        for i,change in enumerate(changes):
            with self.subTest(i=i),self.assertRaises(SREContractError):
                payload=copy.deepcopy(old)
                change(payload)
                validate_collector_delivery(payload)

    def test_hash_tamper_without_resealing_denied(self):
        evt=collector_event()
        evt["source_id"]="different_source"
        with self.assertRaises(SREContractError) as error:
            validate_collector_delivery(evt)
        self.assertEqual(error.exception.code,"snapshot_mismatch")


class OutboundPortTests(unittest.TestCase):
    def test_complete_grounded_live_event(self):
        item=live_event()
        self.assertEqual(validate_live_result_event(item),item)
        self.assertEqual(len(item["decisions"]["match"]),1)

    def test_no_fake_no_legacy_no_partial_no_unevaluated(self):
        for field,value in [
            ("execution_mode","offline_fixture"),
            ("execution_mode","legacy"),
            ("execution_mode","test_live"),
            ("run_status","partial"),
            ("run_status","failed"),
            ("run_status","queued"),
        ]:
            with self.subTest(field=field,value=value),self.assertRaises(SREContractError):
                item=live_event()
                item[field]=value
                item["event_sha256"]=fingerprint({k:v for k,v in item.items()
                                                   if k!="event_sha256"})
                validate_live_result_event(item)
        item=live_event()
        item["decisions"]["no_match"]=[]
        item["decisions"]["not_evaluated"]=[K2]
        item["results_sha256"]=fingerprint(item["decisions"])
        item["event_sha256"]=fingerprint({k:v for k,v in item.items()
                                           if k!="event_sha256"})
        with self.assertRaises(SREContractError):
            validate_live_result_event(item)

    def test_coverage_overlap_duplicate_missing_foreign_key_and_tamper(self):
        original=live_event()
        changes=[
            lambda x:x["decisions"]["match"].append(K2),
            lambda x:x["decisions"]["no_match"].clear(),
            lambda x:x["decisions"]["match"].append(K1),
            lambda x:x["expected_hypothesis_keys"].append(OTHER_UUID),
            lambda x:x["expected_hypothesis_keys"].reverse(),
            lambda x:x.update(video_id="29"),
            lambda x:x.update(project_id=PUB),
            lambda x:x.update(rights_verified_at="2026-10-24T12:00:00Z"),
            lambda x:x.update(extra="new"),
        ]
        for i,change in enumerate(changes):
            with self.subTest(i=i),self.assertRaises(SREContractError):
                item=copy.deepcopy(original)
                change(item)
                validate_live_result_event(item)
        tampered=live_event()
        tampered["content_sha256"]="a"*64
        with self.assertRaises(SREContractError) as error:
            validate_live_result_event(tampered)
        self.assertEqual(error.exception.code,"snapshot_mismatch")


if __name__=="__main__":
    unittest.main()
