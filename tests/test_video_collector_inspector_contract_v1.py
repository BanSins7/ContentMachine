"""VC-MOD-08: page-normalizer durable DTO, strict provenance and no-leak QA.

Only offline fixtures. No DTK/network, database, paid provider, or video ingest.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from intelligence.collector.contracts import CollectorError
from intelligence.collector.inspector_contract import (
    SCHEMA_VERSION, serialize_inspected_page, parse_inspected_page,
)
from intelligence.collector.provider_inspector import inspect_dtk_search_result
from intelligence.taxonomy_builder.pipeline_contracts import sha256
from test_video_collector_inspector_stage9 import (
    TASK_ID, ITEM_ID, FIXED_NOW, answer, item, inspect, search_task,
)

PID="a"*32


def round_trip(outcome):
    document=serialize_inspected_page(outcome)
    transport=json.loads(json.dumps(document,sort_keys=True))
    page,receipt=parse_inspected_page(
        transport,project_id=PID,provider_task_id=TASK_ID,
    )
    return document,page,receipt


class InspectorV1Tests(TestCase):
    def test_pinned_offline_dtk_page_fixture_matches_exact_expected_qa(self):
        path=Path(__file__).resolve().parent/"fixtures/vc_dtk_inspector_page_v1.json"
        fixture=json.loads(path.read_text(encoding="utf-8"))
        self.assertEqual(fixture["schema_version"],"collector.dtk_inspector_fixture/v1")
        result=inspect(fixture["page"],allow_metrics=True)
        doc,page,receipt=round_trip(result)
        for name in ("raw_item_count","accepted_count","valid_count",
                     "partial_count","rejected_count","overflow_count"):
            with self.subTest(name=name):
                self.assertEqual(doc[name],fixture["expect"][name])
        self.assertEqual(doc["quality_reasons"],fixture["expect"]["reasons"])
        self.assertEqual([x["quality"] for x in doc["items"]],["valid","partial"])
        self.assertEqual(page.items[0].comments,0)
        self.assertIsNone(page.items[0].saves)
        self.assertIsNone(page.items[1].published_at)
        self.assertIsNone(page.items[1].duration_seconds)
        self.assertEqual(len(doc["rejected"]),2)
        self.assertTrue(receipt["ingestion_pending"])

    def test_round_trip_has_exact_identity_provenance_and_no_claim_of_ingest(self):
        original=inspect()
        doc,page,receipt=round_trip(original)
        self.assertEqual(doc["schema_version"],SCHEMA_VERSION)
        self.assertEqual(doc["project_id"],PID)
        self.assertEqual(page.project_id,original.page.project_id)
        self.assertEqual(page.provider_task_id,TASK_ID)
        self.assertEqual(page.taxonomy_publication_id,original.page.taxonomy_publication_id)
        self.assertEqual(page.generation_id,original.page.generation_id)
        self.assertEqual(page.query_ids,original.page.query_ids)
        self.assertEqual(page.hypothesis_ids,original.page.hypothesis_ids)
        self.assertEqual(page.fetched_at,FIXED_NOW)
        self.assertEqual(len(page.items),1)
        self.assertEqual(page.items[0],original.page.items[0])
        self.assertEqual(receipt["payload_sha256"],doc["payload_sha256"])
        self.assertEqual(len(doc["payload_sha256"]),64)
        self.assertFalse(receipt["raw_result_persisted"])
        self.assertFalse(receipt["metrics_persisted"])
        self.assertTrue(receipt["ingestion_pending"])

    def test_null_metric_is_not_zero_and_observation_clock_is_preserved(self):
        old=inspect(allow_metrics=True)
        doc,page,receipt=round_trip(old)
        first=page.items[0]
        self.assertEqual(first.views,120)
        self.assertEqual(first.likes,14)
        self.assertEqual(first.comments,0)
        self.assertEqual(first.shares,1)
        self.assertIsNone(first.saves)
        self.assertEqual(first.observed_at,FIXED_NOW)
        self.assertFalse(receipt["metrics_persisted"])
        self.assertIsInstance(doc["items"][0]["comments"],int)
        self.assertIsNone(doc["items"][0]["saves"])

    def test_partial_missing_fields_survive_round_trip_without_inventions(self):
        raw=item()
        raw["title"]=None
        raw["description"]=None
        raw["duration_ms"]=None
        raw["created_at"]=None
        raw["author"]["unique_id"]=None
        raw.pop("kind")
        doc,page,receipt=round_trip(inspect(answer([raw])))
        self.assertEqual(receipt["partial_count"],1)
        self.assertEqual(receipt["valid_count"],0)
        self.assertEqual(page.items[0].content_kind,"unknown")
        self.assertIsNone(page.items[0].duration_seconds)
        self.assertIsNone(page.items[0].title)
        self.assertIsNone(page.items[0].published_at)
        self.assertIsNone(page.items[0].creator_handle)
        self.assertIn("content_kind_unverified",page.items[0].quality_reasons)

    def test_mixed_rejected_duplicate_partial_and_local_overflow_are_accounted(self):
        valid=item(ITEM_ID)
        duplicate=item(ITEM_ID)
        bad=item("7250000000000000090")
        bad["kind"]="live"
        too_late=item("7250000000000000091")
        observed=inspect(answer([valid,duplicate,bad,too_late]),
                         task=search_task(max_results_per_page=3))
        doc,page,receipt=round_trip(observed)
        self.assertEqual(receipt["raw_item_count"],4)
        self.assertEqual(receipt["accepted_count"],1)
        self.assertEqual(receipt["rejected_count"],3)
        self.assertEqual(receipt["overflow_count"],1)
        self.assertEqual(receipt["valid_count"],1)
        self.assertEqual(doc["quality_reasons"]["duplicate_page_video"],1)
        self.assertEqual(doc["quality_reasons"]["not_video"],1)
        self.assertEqual(doc["quality_reasons"]["local_result_cap"],1)
        self.assertEqual([x["reason"] for x in doc["rejected"]],
                         ["duplicate_page_video","not_video","local_result_cap"])

    def test_empty_page_round_trip_preserves_empty_not_no_provider_result(self):
        doc,page,receipt=round_trip(inspect(answer([])))
        self.assertEqual(page.items,())
        self.assertEqual(doc["raw_item_count"],0)
        self.assertEqual(receipt["accepted_count"],0)
        self.assertEqual(receipt["rejected_count"],0)
        self.assertTrue(receipt["ingestion_pending"])

    def test_opaque_provider_cursor_raw_and_signed_media_are_never_serialized(self):
        value=item()
        value["raw"]={"access_token":"a-secret","private_metadata":"private"}
        value["media"]={"video":{"signed_url":"https://media.invalid/secret"}}
        value["description"]="NEVER-STORE-DESCRIPTION"
        observed=inspect(answer([value],has_more=True,cursor="opaque_secret_cursor"))
        doc,page,receipt=round_trip(observed)
        encoded=json.dumps(doc)
        for secret in ("a-secret","private_metadata","media.invalid",
                       "NEVER-STORE-DESCRIPTION","opaque_secret_cursor",
                       "signed_url"):
            self.assertNotIn(secret,encoded)
        self.assertTrue(doc["has_more"])
        self.assertTrue(doc["cursor_present"])
        self.assertTrue(receipt["cursor_present"])
        self.assertIsNone(page.next_cursor)
        self.assertFalse(doc["raw_result_persisted"])

    def test_digest_tampering_and_extra_fields_fail_closed(self):
        good=serialize_inspected_page(inspect())
        for mutation in (
            {"extra_secret":"raw"},
            {"selected_count":1},
            {"project_id":"b"*32},
            {"ingestion_pending":False},
            {"raw_result_persisted":True},
            {"accepted_count":2},
        ):
            with self.subTest(mutation=mutation):
                clone=deepcopy(good)
                clone.update(mutation)
                with self.assertRaises(CollectorError):
                    parse_inspected_page(clone,project_id=PID,provider_task_id=TASK_ID)

    def test_recomputed_hash_cannot_hide_invalid_candidate_or_counts(self):
        good=serialize_inspected_page(inspect())
        for mutate in (
            lambda d: d["items"][0].update({"content_kind":"image_album"}),
            lambda d: d["items"][0].update({"provider_task_id":
                "11111111-1111-4111-8111-111111111111"}),
            lambda d: d["items"][0].update({"quality":"partial"}),
            lambda d: d["items"][0].update({"raw_provider_blob":{"token":"secret"}}),
            lambda d: d["items"][0].update({"published_at":"2030-99-12"}),
            lambda d: d.update({"quality_reasons":{"not_a_reason":1}}),
            lambda d: d.update({"raw_item_count":99}),
        ):
            raw=deepcopy(good)
            mutate(raw)
            raw["payload_sha256"]=sha256({
                k:v for k,v in raw.items() if k!="payload_sha256"
            })
            with self.assertRaises(CollectorError):
                parse_inspected_page(raw,project_id=PID,provider_task_id=TASK_ID)

    def test_rejected_count_and_indices_cannot_be_forged_with_new_digest(self):
        initial=serialize_inspected_page(
            inspect(answer([item(),item()])))
        self.assertEqual(initial["rejected_count"],1)
        for mutation in (
            lambda d: d["rejected"][0].update({"reason":"fabricated"}),
            lambda d: d["rejected"][0].update({"index":120}),
            lambda d: d["rejected"][0].update({"token":"private"}),
        ):
            raw=deepcopy(initial)
            mutation(raw)
            raw["payload_sha256"]=sha256({
                k:v for k,v in raw.items() if k!="payload_sha256"
            })
            with self.assertRaises(CollectorError):
                parse_inspected_page(raw,project_id=PID,provider_task_id=TASK_ID)

    def test_wrong_project_and_provider_task_do_not_parse(self):
        doc=serialize_inspected_page(inspect())
        for pid,tid in (
            ("b"*32,TASK_ID),
            (PID,"11111111-1111-4111-8111-111111111111"),
            (PID,"not-uuid"),
        ):
            with self.subTest(pid=pid,tid=tid),self.assertRaises(CollectorError):
                parse_inspected_page(doc,project_id=pid,provider_task_id=tid)

    def test_provider_response_tool_and_endpoint_identity_fail_closed(self):
        for attr,key,value,code in (
            ("root","tool_id","wrong_provider","provider_source_mismatch"),
            ("meta","endpoint","tiktok.search_users","provider_endpoint_mismatch"),
            ("meta","platform","douyin","provider_platform_mismatch"),
        ):
            data=answer()
            if attr=="root":
                data[key]=value
            else:
                data["result_meta"][key]=value
            with self.subTest(attr=attr,key=key),self.assertRaises(CollectorError) as exc:
                inspect(data)
            self.assertEqual(exc.exception.code,code)
        payload=answer()
        payload["result_meta"]=["malformed"]
        with self.assertRaises(CollectorError):
            inspect(payload)

    def test_serialization_requires_real_inspection_not_user_json(self):
        with self.assertRaises(CollectorError):
            serialize_inspected_page({"page":answer()})
        result=inspect()
        with patch("intelligence.connectors.dtk.submit",
                   side_effect=AssertionError("No POST")),patch(
             "intelligence.connectors.dtk.task",
             side_effect=AssertionError("No GET")):
            doc=serialize_inspected_page(result)
            parsed=parse_inspected_page(doc,project_id=PID,
                                        provider_task_id=TASK_ID)
        self.assertEqual(parsed[1]["accepted_count"],1)

if __name__=="__main__":
    import unittest
    unittest.main()
