"""VC-MOD-06: trusted manifest access and held/release routing without DTK calls."""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
from unittest import TestCase
from unittest.mock import patch
from urllib.parse import urlsplit

from intelligence.collector.contracts import CollectorError, hash_object
from intelligence.collector import http_api
from intelligence.collector.manifest_access import (
    list_published_manifests, get_published_manifest,
    preview_published_shard, commit_published_shard,
)

P = "a"*32
OTHER = "b"*32
M = "11111111-1111-4111-8111-111111111111"
Q = "22222222-2222-4222-8222-222222222222"
S = "33333333-3333-4333-8333-333333333333"
G = "44444444-4444-4444-8444-444444444444"
PUB = "55555555-5555-4555-8555-555555555555"
VALIDATION = "66666666-6666-4666-8666-666666666666"
CID = "77777777-7777-4777-8777-777777777777"
NOW = datetime(2026,10,26,tzinfo=timezone.utc)

QUERYSET = {
  "schema_version": "validated_query_set/v1",
  "project_id": P, "taxonomy_publication_id": PUB,
  "taxonomy_version":1, "taxonomy_sha256":"c"*64,
  "generation_id":G, "validation_version":"qv:"+VALIDATION,
  "queries":[{"query_id":CID,"hypothesis_id":"hyp_one",
              "direction_id":"dir_one","mode":"keyword",
              "text":"home workout","language":"en",
              "validation_state":"accepted"}],
}
QSHA = hash_object(QUERYSET)


class Result:
    def __init__(self, data): self.data=data
    def fetchone(self): return self.data[0] if self.data else None
    def fetchall(self): return list(self.data)


class DB:
    def __init__(self, *, empty=False):
        self.empty = empty
        self.calls = []
        self.missing = False
    def __enter__(self): return self
    def __exit__(self, *args): return False
    def transaction(self): return self
    def execute(self, sql, params=None):
        self.calls.append((sql,params))
        if "FROM collector_qv_manifests" in sql:
            if self.missing or (params and OTHER in params):
                return Result([])
            if "ORDER BY published_at DESC" in sql:
                return Result([(M, VALIDATION, "d"*64,
                                "no_queries" if self.empty else "published",
                                0 if self.empty else 1,0 if self.empty else 1,
                                1, NOW)])
            return Result([(M,VALIDATION,G,PUB,1,"c"*64,"d"*64,
                            "no_queries" if self.empty else "published",
                            0 if self.empty else 1,1,
                            0 if self.empty else 1,1,NOW)])
        if "FROM collector_query_shards s" in sql:
            if self.empty: return Result([])
            if "SELECT s.shard_sha256" in sql:
                if params[2]!=Q: return Result([])
                return Result([("e"*64,QSHA)])
            return Result([(S,Q,0,1,"e"*64,QSHA,QUERYSET)])
        if "FROM collector_manifest_coverage" in sql:
            return Result([("hyp_one",1,0 if self.empty else 1,
                            "all_rejected" if self.empty else "selected",
                            "filtered" if self.empty else None)])
        if "FROM collector_manifest_queries" in sql:
            return Result([(0,0) if self.empty else (1,1)])
        raise AssertionError("Unexpected SQL "+sql[:110])


class ManifestAccessTests(TestCase):
    def test_list_and_detail_are_project_scoped_read_only(self):
        db=DB()
        rows=list_published_manifests(P,connect=lambda:db)
        self.assertEqual(rows["manifests"][0]["selected_count"],1)
        self.assertEqual(rows["external_calls"],0)
        full=get_published_manifest(P,M,connect=lambda:db)
        self.assertEqual(full["shards"][0]["query_set_id"],Q)
        self.assertEqual(full["coverage"][0]["hypothesis_id"],"hyp_one")
        self.assertEqual(full["selected_count"],1)
        self.assertFalse(any(sql.lstrip().upper().startswith(("INSERT","UPDATE","DELETE"))
                             for sql,_ in db.calls))

    def test_wrong_project_or_missing_manifest_is_invisible(self):
        db=DB()
        self.assertEqual(list_published_manifests(OTHER,connect=lambda:db)["manifests"],[])
        with self.assertRaises(CollectorError) as err:
            get_published_manifest(OTHER,M,connect=lambda:db)
        self.assertEqual(err.exception.http_status,404)
        with self.assertRaises(CollectorError):
            get_published_manifest(P,"not-a-uuid",connect=lambda:db)

    def test_no_queries_manifest_has_no_shards_and_blocks_preview(self):
        db=DB(empty=True)
        details=get_published_manifest(P,M,connect=lambda:db)
        self.assertEqual(details["shard_count"],0)
        self.assertEqual(details["shards"],[])
        with self.assertRaises(CollectorError) as e:
            preview_published_shard({"project_id":P,"manifest_id":M,
              "query_set_id":Q,"source_id":"dtk_tiktok","limits":None},connect=lambda:db)
        self.assertEqual(e.exception.code,"no_queries")

    def test_preview_cannot_promote_browser_queryset_json(self):
        db=DB()
        req={"project_id":P,"manifest_id":M,"query_set_id":Q,
             "source_id":"dtk_tiktok","limits":None}
        with patch("intelligence.collector.manifest_access.trusted_preview",
                   return_value={"project_id":P,"trusted_query_set_id":Q,
                                 "plan_hash":"f"*64,"tasks":[],
                                 "blocks":[]}) as fn:
            result=preview_published_shard(req,connect=lambda:db)
        self.assertTrue(result["preview_only"])
        self.assertEqual(result["manifest_id"],M)
        fn.assert_called_once()
        for key,value in (("project_id",OTHER),("query_set_id",G)):
            with self.subTest(key=key):
                changed=dict(req);changed[key]=value
                with self.assertRaises(CollectorError):
                    preview_published_shard(changed,connect=lambda:db)
        changed={**req,"query_set":QUERYSET}
        with self.assertRaises(CollectorError):
            preview_published_shard(changed,connect=lambda:db)
        changed={**req,"query_set_id":G}
        with self.assertRaises(CollectorError) as e:
            preview_published_shard(changed,connect=lambda:db)
        self.assertEqual(e.exception.code,"query_set_not_in_manifest")

    def test_held_commit_pins_owner_manifest_and_plan_without_dispatch(self):
        db=DB()
        req={"project_id":P,"manifest_id":M,"query_set_id":Q,
             "source_id":"dtk_tiktok","limits":None,
             "plan_hash":"d"*64,"idempotency_key":"collector-commit-000001",
             "confirm":True,"max_spend_usd":"0"}
        with patch("intelligence.collector.manifest_access.commit_run",
                   return_value={"project_id":P,"run_id":12,"status":"planned_held",
                                 "external_calls":0,"replayed":False}) as commit, patch(
             "intelligence.connectors.dtk.submit") as post:
            value=commit_published_shard(req,connect=lambda:db)
        self.assertEqual(value["status"],"planned_held")
        self.assertEqual(value["manifest_id"],M)
        self.assertEqual(value["query_set_id"],Q)
        self.assertEqual(value["external_calls"],0)
        self.assertEqual(commit.call_count,1)
        post.assert_not_called()
        with self.assertRaises(CollectorError):
            commit_published_shard({**req,"confirm":False},connect=lambda:db)
        with self.assertRaises(CollectorError):
            commit_published_shard({**req,"max_spend_usd":"10"},connect=lambda:db)
        with self.assertRaises(CollectorError):
            commit_published_shard({**req,"project_id":OTHER},connect=lambda:db)

    def test_held_commit_rejects_wrong_response_scope(self):
        req={"project_id":P,"manifest_id":M,"query_set_id":Q,
             "source_id":"dtk_tiktok","limits":None,"plan_hash":"f"*64,
             "idempotency_key":"collector-commit-000002",
             "confirm":True,"max_spend_usd":"0"}
        with patch("intelligence.collector.manifest_access.commit_run",
                   return_value={"project_id":OTHER,"run_id":12,"status":"planned_held"}):
            with self.assertRaises(CollectorError):
                commit_published_shard(req,connect=lambda:DB())

    def test_manifest_integrity_rejects_missing_shard(self):
        class Broken(DB):
            def execute(self,sql,params=None):
                if "FROM collector_query_shards s" in sql: return Result([])
                return super().execute(sql,params)
        with self.assertRaises(CollectorError) as e:
            get_published_manifest(P,M,connect=lambda:Broken())
        self.assertEqual(e.exception.code,"manifest_integrity")


class Handler:
    def __init__(self,body=None):
        data=json.dumps(body or {}).encode()
        self.headers={"Content-Length":str(len(data))}
        self.rfile=BytesIO(data)
        self.response=None
    def json_response(self,status,body):
        self.response=(status,body)


class ManifestHTTPTests(TestCase):
    def test_manifest_get_routes_only_read_by_explicit_project(self):
        h=Handler()
        with patch("intelligence.collector.manifest_access.list_published_manifests",
                   return_value={"manifests":[]}) as fn:
            self.assertTrue(http_api.handle_get(h,urlsplit(
                "/api/intelligence/collector/manifests?project_id="+P)))
        self.assertEqual(h.response[0],200)
        fn.assert_called_once_with(P)
        h=Handler()
        with patch("intelligence.collector.manifest_access.get_published_manifest",
                   return_value={"manifest_id":M}) as fn:
            self.assertTrue(http_api.handle_get(h,urlsplit(
                "/api/intelligence/collector/manifests/"+M+"?project_id="+P)))
        self.assertEqual(h.response[0],200)
        fn.assert_called_once_with(P,M)

    def test_manifest_post_routes_do_not_use_raw_query_json(self):
        req={"project_id":P,"manifest_id":M,"query_set_id":Q,
             "source_id":"dtk_tiktok","limits":None}
        with patch("intelligence.collector.manifest_access.preview_published_shard",
                   return_value={"preview_only":True}) as fn:
            h=Handler(req)
            self.assertTrue(http_api.handle_post(
                h,"/api/intelligence/collector/manifest-preview"))
            self.assertEqual(h.response[0],200)
            fn.assert_called_once_with(req)
        with patch("intelligence.collector.manifest_access.commit_published_shard",
                   return_value={"status":"planned_held"}) as fn:
            h=Handler(req)
            self.assertTrue(http_api.handle_post(
                h,"/api/intelligence/collector/manifest-runs"))
            self.assertEqual(h.response[0],201)
            fn.assert_called_once_with(req)
