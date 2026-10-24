"""DTK v5.1.7 response inspector: exact Page[Content] and adversarial fixtures.

All tests run offline. No TikTok, DTK, OpenRouter, media or PostgreSQL writes.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from unittest import TestCase, mock

from intelligence.collector.contracts import CollectorError, CandidateEnvelope, ProviderPage
from intelligence.collector.planner import build_preview
from intelligence.collector.provider_inspector import (
    INSPECTOR_VERSION, inspect_dtk_search_result,
)
from intelligence.collector import worker

TASK_ID = "12345678-1234-4234-8234-123456789abc"
FIXED_NOW = datetime(2026, 10, 24, 7, 0, tzinfo=timezone.utc)
ITEM_ID = "7250000000000000000"


def search_task(max_results_per_page=20):
    qs = {
        "schema_version": "validated_query_set/v1",
        "project_id": "a"*32,
        "taxonomy_publication_id": "11111111-1111-4111-8111-111111111111",
        "taxonomy_version": 3,
        "taxonomy_sha256": "b"*64,
        "generation_id": "22222222-2222-4222-8222-222222222222",
        "validation_version": "offline-v1",
        "queries": [{
            "query_id": "query_home",
            "hypothesis_id": "hyp_home",
            "direction_id": "dir_home",
            "mode": "keyword",
            "text": "home fitness", "language": "en",
            "validation_state": "accepted",
        }],
    }
    return build_preview(qs,limits={
        "max_tasks":20, "max_pages_per_task":1,
        "max_results_per_page":max_results_per_page,
        "max_total_requests":40, "max_total_candidates":480,
    })["tasks"][0]


def item(video_id=ITEM_ID, *, handle="fitness_1"):
    return {
        "platform":"tiktok", "content_id": video_id, "kind":"video",
        "web_url":f"https://www.tiktok.com/@{handle}/video/{video_id}?is_from_webapp=1",
        "title":"Home workout", "description":"Home workout for everyone",
        "created_at":"2026-01-18T12:34:56+00:00", "duration_ms":12750,
        "is_deleted":False, "is_private":False,
        "author":{
            "platform":"tiktok", "uid":"111222333444",
            "sec_uid":"MS4wLjABAAAA",
            "unique_id":handle, "nickname":"Not retained",
            "signature":"DO NOT SAVE PRIVATE PROFILE",
        },
        "stats":{
            "play_count":120, "digg_count":14, "comment_count":0,
            "share_count":1, "collect_count":None,
        },
        "media":{"video":{"url":"https://example.org/secret_media_url"}},
        "raw":{"token":"NEVER_PERSIST_RAW"},
        "music":{"play_url":"https://example.org/secret_music"},
        "fetched_at":"2026-10-24T06:00:00+00:00",
    }


def answer(items=None, *, cursor=None, has_more=False):
    return {
        "tool_id":"dtk_tiktok",
        "state":"done","task_id":TASK_ID,
        "data":{"items":[item()] if items is None else items,
                "cursor":cursor,"has_more":has_more},
        "result_meta":{"endpoint":"tiktok.search_posts",
                       "platform":"tiktok","cached":False},
    }


def inspect(payload=None, *, task=None, allow_metrics=False):
    return inspect_dtk_search_result(
        answer() if payload is None else payload,
        search_task() if task is None else task,
        TASK_ID, fetched_at=FIXED_NOW, allow_metrics=allow_metrics,
    )


class InspectorContractTests(TestCase):
    def test_normalized_content_and_lineage_exact(self):
        outcome=inspect()
        self.assertIsInstance(outcome.page,ProviderPage)
        self.assertEqual(len(outcome.page.items),1)
        candidate=outcome.page.items[0]
        self.assertIsInstance(candidate,CandidateEnvelope)
        self.assertEqual(candidate.platform_video_id,ITEM_ID)
        self.assertEqual(candidate.platform,"tiktok")
        self.assertEqual(candidate.canonical_url,
                         f"https://www.tiktok.com/@fitness_1/video/{ITEM_ID}")
        self.assertEqual(candidate.creator_platform_id,"111222333444")
        self.assertEqual(candidate.creator_handle,"fitness_1")
        self.assertEqual(candidate.title,"Home workout")
        self.assertEqual(candidate.duration_seconds,12.75)
        self.assertEqual(candidate.published_at.isoformat(),"2026-01-18T12:34:56+00:00")
        self.assertEqual(candidate.fetched_at,FIXED_NOW)
        self.assertEqual(candidate.taxonomy_version,3)
        self.assertEqual(candidate.generation_id,search_task()["generation_id"])
        self.assertEqual(candidate.query_ids,("query_home",))
        self.assertEqual(candidate.hypothesis_ids,("hyp_home",))
        self.assertEqual(candidate.quality,"valid")
        self.assertEqual(candidate.quality_reasons,())
        self.assertIsNone(candidate.views)
        self.assertIsNone(candidate.observed_at)
        summary=outcome.summary()
        self.assertEqual(summary["accepted_count"],1)
        self.assertEqual(summary["valid_count"],1)
        self.assertEqual(summary["rejected_count"],0)
        self.assertEqual(summary["inspector_version"],INSPECTOR_VERSION)
        self.assertFalse(summary["candidate_metadata_persisted"])
        self.assertFalse(summary["metrics_persisted"])
        self.assertTrue(summary["ingestion_pending"])
        for sensitive in (
            ITEM_ID,"Home workout","fitness_1","111222333444",
            "PRIVATE PROFILE","secret_media_url","NEVER_PERSIST_RAW",
        ):
            self.assertNotIn(sensitive,str(summary))

    def test_explicit_ephemeral_metrics_and_unknown_vs_zero(self):
        outcome=inspect(allow_metrics=True)
        candidate=outcome.page.items[0]
        self.assertEqual(candidate.views,120)
        self.assertEqual(candidate.likes,14)
        self.assertEqual(candidate.comments,0)
        self.assertEqual(candidate.shares,1)
        self.assertIsNone(candidate.saves)
        self.assertEqual(candidate.observed_at,FIXED_NOW)
        self.assertFalse(outcome.summary()["metrics_persisted"])

    def test_missing_date_duration_title_are_partial_not_invented(self):
        entry=item()
        entry["title"]=None
        entry["description"]=None
        entry["created_at"]=None
        entry["duration_ms"]=None
        entry["author"]["unique_id"]=None
        observed=inspect(answer([entry]))
        self.assertEqual(observed.summary()["partial_count"],1)
        self.assertEqual(observed.summary()["valid_count"],0)
        candidate=observed.page.items[0]
        self.assertIsNone(candidate.published_at)
        self.assertIsNone(candidate.duration_seconds)
        self.assertIsNone(candidate.title)
        self.assertEqual(candidate.quality,"partial")
        self.assertEqual(set(candidate.quality_reasons),{
            "missing_title","missing_publication_date",
            "missing_duration","missing_author_handle",
        })

    def test_unknown_kind_never_qualified_as_video(self):
        entry=item()
        entry.pop("kind")
        observed=inspect(answer([entry]))
        self.assertEqual(observed.page.items[0].content_kind,"unknown")
        self.assertEqual(observed.page.items[0].quality,"partial")
        self.assertIn("content_kind_unverified",observed.page.items[0].quality_reasons)

    def test_deduplicate_by_exact_platform_id(self):
        repeated=answer([item(),item()])
        result=inspect(repeated)
        self.assertEqual(result.summary()["accepted_count"],1)
        self.assertEqual(result.summary()["rejected_count"],1)
        self.assertEqual(result.rejected[0]["reason"],"duplicate_page_video")

    def test_photo_album_live_deleted_private_and_cross_platform_rejected(self):
        cases=[
            ("image_album",{"kind":"image_album"},"not_video"),
            ("live",{"kind":"live"},"not_video"),
            ("private",{"is_private":True},"private_content"),
            ("deleted",{"is_deleted":True},"deleted_content"),
            ("false_platform",{"platform":"douyin"},"platform_mismatch"),
            ("invalid_kind",{"kind":"story"},"unknown_content_kind"),
            ("invalid_duration",{"duration_ms":True},"invalid_duration"),
            ("invalid_created_at",{"created_at":1690000000},"invalid_datetime"),
            ("future",{"created_at":"2030-01-01T00:00:00Z"},"future_publication"),
            ("invalid_author",{"author":"fitness_1"},"invalid_author"),
        ]
        for label,updates,reason in cases:
            with self.subTest(label=label):
                entry=item()
                entry.update(updates)
                result=inspect(answer([entry]))
                self.assertEqual(result.summary()["accepted_count"],0)
                self.assertEqual(result.rejected[0]["reason"],reason)

    def test_dangerous_identity_mismatches_and_host_forgeries(self):
        cases=[
            ("mismatch",{"content_id":"7350000000000000000"},"identity_mismatch"),
            ("wrong_domain",{"web_url":f"https://evil.tiktok.com/@fitness_1/video/{ITEM_ID}"},"invalid_url"),
            ("userinfo",{"web_url":f"https://www.tiktok.com@evil.com/@fitness_1/video/{ITEM_ID}"},"invalid_url"),
            ("photo_url",{"web_url":f"https://www.tiktok.com/@fitness_1/photo/{ITEM_ID}"},"identity_mismatch"),
            ("short_link",{"web_url":"https://vm.tiktok.com/abcdef"},"invalid_url"),
            ("non_numeric_id",{"content_id":"123notdecimal"},"invalid_video_id"),
            ("JS_precision",{"content_id":7250000000000000000},"invalid_video_id"),
            ("missing_url_no_author",{"web_url":None,"author":{"platform":"tiktok","uid":"999"}},"invalid_url"),
        ]
        for label,updates,reason in cases:
            with self.subTest(label=label):
                entry=item()
                entry.update(updates)
                result=inspect(answer([entry]))
                self.assertEqual(result.summary()["accepted_count"],0)
                self.assertEqual(result.rejected[0]["reason"],reason)

    def test_safe_url_synthesis_only_when_real_handle_and_id(self):
        entry=item()
        entry["web_url"]=None
        observed=inspect(answer([entry]))
        self.assertEqual(observed.summary()["valid_count"],1)
        self.assertEqual(observed.page.items[0].canonical_url,
                         f"https://www.tiktok.com/@fitness_1/video/{ITEM_ID}")

    def test_title_fallback_handles_multiline_caption_without_leaking_it(self):
        entry=item()
        entry["title"]=None
        entry["description"]="A great workout\nextra private details " + "z"*1000
        candidate=inspect(answer([entry])).page.items[0]
        self.assertEqual(candidate.title,"A great workout")
        self.assertEqual(candidate.quality,"valid")
        self.assertNotIn("private details",str(inspect(answer([entry])).summary()))

    def test_metadata_platform_mismatch_fails_whole_page(self):
        payload=answer()
        payload["result_meta"]["platform"]="douyin"
        with self.assertRaises(CollectorError) as caught:
            inspect(payload)
        self.assertEqual(caught.exception.code,"provider_platform_mismatch")

    def test_metrics_types_strict_only_when_explicitly_enabled(self):
        for bad in (True,-1,"120",2.5,2**63):
            with self.subTest(value=bad):
                entry=item()
                entry["stats"]["play_count"]=bad
                # Disabled metrics never process or retain those raw values.
                self.assertEqual(inspect(answer([entry])).summary()["valid_count"],1)
                with_metrics=inspect(answer([entry]),allow_metrics=True)
                self.assertEqual(with_metrics.summary()["accepted_count"],0)
                self.assertEqual(with_metrics.rejected[0]["reason"],"invalid_metric")

    def test_overflow_is_explicitly_reported_not_silently_truncated(self):
        rows=[item(str(7250000000000000000+i)) for i in range(4)]
        result=inspect(answer(rows),task=search_task(max_results_per_page=2))
        self.assertEqual(result.raw_item_count,4)
        self.assertEqual(result.examined_count,2)
        self.assertEqual(result.summary()["accepted_count"],2)
        self.assertEqual(result.overflow_count,2)
        self.assertEqual(result.summary()["reason_counts"]["local_result_cap"],2)
        self.assertEqual(result.rejected_count,2)

    def test_empty_page_and_pagination_is_reported_not_dispatched(self):
        empty=inspect(answer([]))
        self.assertEqual(empty.summary()["accepted_count"],0)
        self.assertEqual(empty.summary()["rejected_count"],0)
        self.assertFalse(empty.page.has_more)
        paged=inspect(answer([item()],cursor="opaque123",has_more=True))
        self.assertTrue(paged.page.has_more)
        self.assertEqual(paged.page.next_cursor,"opaque123")
        self.assertFalse(paged.summary()["pagination"]["next_page_scheduled"])
        missing=inspect(answer([item()],has_more=True))
        self.assertEqual(missing.pagination_reasons,("pagination_cursor_missing",))
        self.assertIsNone(missing.page.next_cursor)

    def test_invalid_cursor_and_has_more_fail_closed(self):
        for key,bad in (("cursor",True),("cursor","x"*321),
                        ("cursor","secret\nvalue"),("has_more",1)):
            with self.subTest(key=key,value=str(bad)[:15]),self.assertRaises(CollectorError):
                payload=answer()
                payload["data"][key]=bad
                inspect(payload)

    def test_task_identity_and_page_schema_fail_closed(self):
        for payload in (
            {"state":"running","task_id":TASK_ID,"data":{"items":[]}},
            {"state":"done","task_id":"aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa","data":{"items":[]}},
            {"state":"done","task_id":TASK_ID,"data":{"entries":[]}},
            {"state":"done","task_id":TASK_ID,"data":{"items":"not-a-list"}},
            {"state":"done","task_id":TASK_ID,"data":{"items":list(range(101))}},
        ):
            with self.subTest(payload=str(payload)[:60]),self.assertRaises(CollectorError):
                inspect(payload)
        with self.assertRaises(CollectorError):
            inspect(answer(),allow_metrics="1")
        with self.assertRaises(CollectorError):
            inspect(answer(),fetched_at=None,task=dict(search_task(),max_pages=2))

    def test_no_io_even_with_untrusted_raw_blob(self):
        payload=answer()
        payload["data"]["items"][0]["raw"]={"secret":"VERY SENSITIVE"}
        payload["data"]["items"][0]["media"]={"video":{"url":"https://private.invalid/path"}}
        with mock.patch("socket.create_connection",side_effect=AssertionError("network")),mock.patch(
                "urllib.request.urlopen",side_effect=AssertionError("network")):
            result=inspect(payload)
        self.assertEqual(result.summary()["valid_count"],1)
        self.assertNotIn("VERY SENSITIVE",str(result.summary()))
        self.assertNotIn("private.invalid",str(result.summary()))


class Stage9WorkerIntegrationTests(TestCase):
    def test_invalid_finished_page_marks_inspection_failed(self):
        source=search_task()
        claim={
            "state":"submitted","source_task":source,"run_id":5,
            "provider_task_id":TASK_ID,"worker_id":"worker",
            "prepared":{"params":{"keyword":"home fitness"}},
        }
        class Provider:
            def submit(self,*args):raise AssertionError("double submit")
            def task(self,task_id):
                return {"state":"done","task_id":task_id,"data":{"wrong_items":[]}}
        with mock.patch("intelligence.collector.worker._assert_worker_config"),mock.patch(
            "intelligence.collector.worker._claim_next",return_value=claim
        ),mock.patch("intelligence.collector.worker._update_after_provider",
            return_value=True) as save:
            result=worker.process_one(worker_id="worker",provider=Provider())
        self.assertEqual(result["status"],"inspection_failed")
        self.assertEqual(save.call_args.kwargs["next_state"],"failed")
        self.assertEqual(save.call_args.kwargs["error_code"],"provider_inspection_failed")

    def test_valid_finished_page_never_persists_candidates_or_raw_response(self):
        source=search_task()
        claim={
            "state":"submitted","source_task":source,"run_id":5,
            "provider_task_id":TASK_ID,"worker_id":"worker",
            "prepared":{"params":{"keyword":"home fitness"}},
        }
        class Provider:
            def submit(self,*args):raise AssertionError("double submit")
            def task(self,task_id):
                return answer([item()])
        with mock.patch("intelligence.collector.worker._assert_worker_config"),mock.patch(
            "intelligence.collector.worker._claim_next",return_value=claim
        ),mock.patch("intelligence.collector.worker._update_after_provider",
            return_value=True) as save:
            result=worker.process_one(worker_id="worker",provider=Provider())
        self.assertEqual(result["status"],"completed")
        self.assertEqual(result["accepted_count"],1)
        summary=save.call_args.kwargs["summary"]
        self.assertEqual(summary["valid_count"],1)
        self.assertFalse(summary["candidate_metadata_persisted"])
        self.assertFalse(summary["metrics_persisted"])
        self.assertNotIn("secret",str(summary))
        self.assertNotIn(ITEM_ID,str(summary))
