"""Stage 7: strict DTK bridge/diagnostics; zero live provider dispatch."""
from __future__ import annotations

from copy import deepcopy
import json
import unittest
from unittest.mock import patch

from intelligence.connectors import dtk
from intelligence.collector import build_preview
from intelligence.collector.adapters.dtk_tiktok import (
    prepare_keyword_task, diagnose_local_connection,
)
from intelligence.collector.contracts import CollectorError
from intelligence.collector.registry import list_sources
from intelligence.collector import http_api

PID="a"*32
PUB="11111111-1111-4111-8111-111111111111"
GEN="22222222-2222-4222-8222-222222222222"


def query_set(text="home workout"):
    return {
        "schema_version":"validated_query_set/v1",
        "project_id":PID,
        "taxonomy_publication_id":PUB,
        "taxonomy_version":3,
        "taxonomy_sha256":"b"*64,
        "generation_id":GEN,
        "validation_version":"fixture-unverified",
        "queries":[{
            "query_id":"query_home",
            "hypothesis_id":"hyp_home",
            "direction_id":"dir_home",
            "mode":"keyword",
            "text":text,
            "language":"en",
            "validation_state":"accepted",
        }]
    }


class DTKStage7Tests(unittest.TestCase):
    def test_all_twelve_operations_map_to_expected_routes(self):
        cases={
            "search_posts":({"keyword":"home workout"},"/search/posts"),
            "search_users":({"keyword":"creator"},"/search/users"),
            "video":({"aweme_id":"7234567890123456789"},"/video"),
            "related_posts":({"aweme_id":"7234567890123456789"},"/video/related"),
            "comments":({"aweme_id":"7234567890123456789"},"/video/comments"),
            "comment_replies":({"aweme_id":"7234567890123456789",
                                "comment_id":"1234567890123456789"},"/video/comments/replies"),
            "user":({"sec_user_id":"MS4wLjABAAAA"},"/user"),
            "user_posts":({"sec_user_id":"MS4wLjABAAAA"},"/user/posts"),
            "hashtag":({"name":"homeworkout"},"/hashtag"),
            "hashtag_posts":({"hashtag_id":"1234567890"},"/hashtag/posts"),
            "music":({"music_id":"1234567890"},"/music"),
            "music_posts":({"music_id":"1234567890"},"/music/posts"),
        }
        self.assertEqual(set(cases),set(dtk.ACTIONS))
        for action,(params,suffix) in cases.items():
            with self.subTest(action=action):
                route,validated=dtk.validate_action(action,params)
                self.assertEqual(route,"/api/v1/tiktok"+suffix)
                self.assertEqual(validated,params)
        for a in (None,123,[],{},False):
            with self.subTest(action_type=type(a).__name__),self.assertRaises(dtk.DTKError):
                dtk.validate_action(a,{})

    def test_adapter_matches_real_planner_and_no_unverified_dispatch(self):
        plan=build_preview(query_set())
        self.assertEqual(plan["summary"]["planned_task_count"],1)
        item=prepare_keyword_task(plan["tasks"][0])
        self.assertEqual(item["params"],{"keyword":"home workout"})
        self.assertEqual(item["query_ids"],["query_home"])
        self.assertFalse(item["external_dispatch_authorized"])
        self.assertEqual(item["external_calls"],0)

    def test_adapters_reject_unsupported_or_forged_tasks(self):
        task=build_preview(query_set())["tasks"][0]
        mutations=[
            ("operation","video"),("method","hashtag"),("source_id","apify_tiktok"),
            ("max_pages",2),("max_results_per_page",True),
            ("task_fingerprint","not-a-sha"),("project_id","foreign"),
            ("taxonomy_publication_id",GEN),("query_ids",[]),
            ("params",{"keyword":"test","cursor":"unapproved"}),
            ("params",{"keyword":"x"*121}),
        ]
        for key,value in mutations:
            changed=deepcopy(task);changed[key]=value
            with self.subTest(field=key,value=str(value)[:20]),self.assertRaises(CollectorError):
                prepare_keyword_task(changed)
        changed=deepcopy(task);changed["hidden_provider_submit"]=True
        with self.assertRaises(CollectorError):
            prepare_keyword_task(changed)

    def test_plan_excludes_queries_dtk_cannot_accept(self):
        plan=build_preview(query_set("x"*121))
        self.assertEqual(plan["tasks"],[])
        self.assertEqual(plan["rejected_queries"][0]["reason"],"provider_query_limit_exceeded")
        self.assertIn("no_supported_tasks",plan["blocks"])

    def test_registry_has_connector_but_not_automatic_execution(self):
        dtk_item=next(s for s in list_sources() if s["source_id"]=="dtk_tiktok")
        self.assertEqual(dtk_item["integration_state"],"local_rest_connector_present_manual_only")
        self.assertFalse(dtk_item["operationally_verified"])
        self.assertFalse(dtk_item["source_policy_verified"])
        self.assertEqual(dtk_item["capabilities"],[{"mode":"keyword","operation":"search_posts"}])

    def test_diagnostics_explicit_only_and_never_exposes_token(self):
        with patch("intelligence.connectors.dtk.status",return_value={
            "configured":True,"available":True,"read_scope":True,
            "supports_keyword_search":True,"operations":list(dtk.ACTIONS),
            "internal_secret":"DO_NOT_LEAK",
        }) as upstream:
            state=diagnose_local_connection()
        upstream.assert_called_once()
        self.assertTrue(state["keyword_search_transport_ready"])
        self.assertEqual(state["declared_operation_count"],12)
        self.assertFalse(state["automatic_collector_execution_enabled"])
        self.assertFalse(state["source_policy_verified"])
        self.assertNotIn("DO_NOT_LEAK",json.dumps(state))
        with patch("intelligence.connectors.dtk.status",return_value={
            "configured":False,"available":False,"read_scope":False,
            "error_code":"disabled","warning":"Disabled",
        }):
            not_ready=diagnose_local_connection()
        self.assertFalse(not_ready["keyword_search_transport_ready"])

    def test_collector_sources_offline_and_diagnostics_explicit(self):
        class Handler:
            def __init__(self):self.code=None;self.payload=None
            def json_response(self,code,payload):self.code=code;self.payload=payload
        h=Handler()
        url=lambda path:type("URL",(),{"path":path,"query":""})()
        with patch("intelligence.connectors.dtk.status",
                   side_effect=AssertionError("unexpected auth/me")):
            self.assertTrue(http_api.handle_get(h,url("/api/intelligence/collector/sources")))
        self.assertEqual(h.code,200)
        with patch("intelligence.collector.adapters.dtk_tiktok.diagnose_local_connection",
                   return_value={"source_id":"dtk_tiktok","automatic_collector_execution_enabled":False}) as diag:
            self.assertTrue(http_api.handle_get(h,url(
                "/api/intelligence/collector/sources/dtk_tiktok/diagnostics")))
            diag.assert_called_once_with()
            self.assertEqual(h.code,200)
            self.assertFalse(h.payload["automatic_collector_execution_enabled"])

if __name__=="__main__":
    unittest.main()
