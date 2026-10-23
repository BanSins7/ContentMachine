"""Stage II: PostgreSQL policy/budget, safe HTTP and DB trust boundary.

No DTK/network calls. Optional DB assertions use disposable CI PostgreSQL.
"""
from __future__ import annotations

from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from intelligence.collector.contracts import CollectorError, hash_object
from intelligence.collector import http_api
from intelligence.collector.policy_gate import zero_spend_cap, inspect_source_policy, require_policy
from intelligence.collector.repository import _validated_commit_request, _trusted_query_set
from intelligence.collector.query_intake import parse_validated_query_set
from intelligence.discovery import http_api as discovery_routes
from test_search_planner_foundation import snapshot as approved_snapshot
from intelligence.search_planner.contracts import validate_published_snapshot

PID="a"*32
PUB="22222222-2222-4222-8222-222222222222"
GEN="55555555-5555-4555-8555-555555555555"


def query_set():
    return {
        "schema_version":"validated_query_set/v1", "project_id":PID,
        "taxonomy_publication_id":PUB, "taxonomy_version":3,
        "taxonomy_sha256":hash_object(approved_snapshot()["tree"]),
        "generation_id":GEN, "validation_version":"query-validation/v1",
        "queries":[{
            "query_id":"query_short", "hypothesis_id":"short",
            "direction_id":"home", "mode":"keyword",
            "text":"10 minute home workout", "language":"en",
            "validation_state":"accepted",
        }],
    }


def request():
    return {
        "query_set_id":"66666666-6666-4666-8666-666666666666",
        "source_id":"dtk_tiktok", "limits":None, "plan_hash":"d"*64,
        "idempotency_key":"stage2-repeat-key-0001","confirm":True,
        "max_spend_usd":"0.00",
    }


class Stub:
    def __init__(self, row): self.row=row
    def fetchone(self): return self.row


class TrustedConn:
    def __init__(self, row): self.row=row
    def execute(self,sql,params=None): return Stub(self.row)


class FakeHandler:
    def __init__(self,body=None):
        encoded=json.dumps(body).encode("utf-8") if body is not None else b""
        self.headers={"Content-Length":str(len(encoded))}
        self.rfile=BytesIO(encoded)
        self.code=None
        self.payload=None
    def json_response(self,code,payload):
        self.code=code;self.payload=payload


class CollectorStage2UnitTests(unittest.TestCase):
    def test_uuid_query_identifier_is_accepted(self):
        q = query_set()
        q["queries"][0]["query_id"] = "12345678-1234-4234-8234-123456789abc"
        self.assertEqual(parse_validated_query_set(q).queries[0].query_id,
                         "12345678-1234-4234-8234-123456789abc")

    def test_zero_budget_including_float_and_nan_rejected(self):
        for cap in ("0","0.00","0.000000", "-0.0"):
            self.assertEqual(zero_spend_cap(cap),"0.00")
        for cap in (True,0,0.0,1,"0.01","-1","NaN","Infinity","1e100",""):
            with self.subTest(cap=cap),self.assertRaises(CollectorError):
                zero_spend_cap(cap)

    def test_explicit_commit_consent_and_idempotency_required(self):
        self.assertEqual(_validated_commit_request(request()),"stage2-repeat-key-0001")
        for key,value in (
            ("confirm",False),("max_spend_usd","0.01"),
            ("plan_hash","not-a-hash"),("idempotency_key","x"*101),
            ("query_set_id",None)
        ):
            raw=request();raw[key]=value
            if key=="query_set_id":
                # Identifier is checked by the trusted DB loader, not here.
                continue
            with self.subTest(field=key),self.assertRaises(CollectorError):
                _validated_commit_request(raw)
        raw=request();raw["user_supplied_verified"]=True
        with self.assertRaises(CollectorError):
            _validated_commit_request(raw)

    def test_http_preview_remains_no_network_and_no_db(self):
        handler=FakeHandler({"query_set":query_set(),"source_id":"dtk_tiktok",
                             "limits":None})
        with patch("socket.create_connection",side_effect=AssertionError("network")):
            self.assertTrue(discovery_routes.handle_post(
                handler,"/api/intelligence/collector/preview"))
        self.assertEqual(handler.code,200)
        self.assertFalse(handler.payload["execution_enabled"])
        self.assertFalse(handler.payload["query_provenance_verified"])
        self.assertIn("query_set_not_server_verified",handler.payload["blocks"])
        self.assertEqual(handler.payload["summary"]["planned_task_count"],1)

    def test_http_status_and_cancel_are_project_scoped(self):
        handler=FakeHandler()
        url=type("URL",(),{"path":"/api/intelligence/collector/runs/13",
                           "query":"project_id="+PID})()
        with patch("intelligence.collector.http_api.get_run",return_value={
            "run_id":13,"project_id":PID,"status":"planned_held"
        }) as call:
            self.assertTrue(discovery_routes.handle_get(handler,url))
            self.assertEqual(handler.code,200)
            call.assert_called_once_with(PID,13)
        handler=FakeHandler()
        url=type("URL",(),{"path":"/api/intelligence/collector/runs/13","query":""})()
        self.assertTrue(discovery_routes.handle_get(handler,url))
        self.assertEqual(handler.code,422)
        self.assertEqual(handler.payload["error"]["code"],"project_scope_required")
        handler=FakeHandler({"project_id":PID})
        with patch("intelligence.collector.http_api.cancel_run",return_value={
            "run_id":13,"status":"cancelled","external_calls":0,
        }) as call:
            self.assertTrue(discovery_routes.handle_post(
                handler,"/api/intelligence/collector/runs/13/cancel"))
            self.assertEqual(handler.code,200)
            call.assert_called_once_with(PID,13)

    def test_http_rejects_unknown_body_fields_and_runs_no_commit(self):
        raw=request();raw["query_set"]=query_set()
        handler=FakeHandler(raw)
        with patch("intelligence.collector.http_api.commit_run") as call:
            self.assertTrue(discovery_routes.handle_post(
                handler,"/api/intelligence/collector/runs"))
            self.assertEqual(handler.code,422)
            call.assert_not_called()

    def test_bad_body_fails_cleanly(self):
        handler=FakeHandler();handler.headers["Content-Length"]="0"
        self.assertTrue(http_api.handle_post(handler,"/api/intelligence/collector/preview"))
        self.assertEqual(handler.code,413)

    def test_trusted_query_set_requires_completed_sqg_and_rtb_lineage(self):
        raw=query_set()
        parsed=parse_validated_query_set(raw)
        def row(status="completed",data=raw,hash_value=None):
            return (PID,PUB,GEN,data,hash_value or parsed.fingerprint,
                    "trusted_query_validation/v1",status,PUB,
                    hash_object(approved_snapshot()["tree"]),3)
        with patch("intelligence.collector.repository.read_published_snapshot",
                   return_value=validate_published_snapshot(approved_snapshot())):
            qid,verified=_trusted_query_set(
                TrustedConn(row()),"66666666-6666-4666-8666-666666666666")
            self.assertEqual(qid,"66666666-6666-4666-8666-666666666666")
            self.assertEqual(verified.queries[0].hypothesis_id,"short")
            with self.assertRaises(CollectorError) as err:
                _trusted_query_set(TrustedConn(row(status="queued")),qid)
            self.assertEqual(err.exception.code,"query_set_not_verified")
            with self.assertRaises(CollectorError) as err:
                _trusted_query_set(TrustedConn(row(hash_value="0"*64)),qid)
            self.assertEqual(err.exception.code,"query_set_integrity")
            broken=query_set()
            broken["queries"][0]["direction_id"]="time"
            with self.assertRaises(CollectorError) as err:
                _trusted_query_set(TrustedConn(row(data=broken,hash_value=hash_object(broken))),qid)
            self.assertEqual(err.exception.code,"invalid_query_lineage")


class PolicyReadUnitTests(unittest.TestCase):
    def test_missing_source_is_denied_without_io(self):
        class C:
            def execute(self,sql,params=None): return Stub(None)
        denied=inspect_source_policy(C(),"dtk_tiktok",max_requests=1,max_pages=1)
        self.assertFalse(denied["allowed_for_held_plan"])
        self.assertEqual(denied["blocks"],["source_not_registered"])
        with self.assertRaises(CollectorError):
            require_policy(denied)


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"Disposable PostgreSQL required")
class CollectorStage2Postgres(unittest.TestCase):
    """Check the real registry policy gate against existing D1 tables."""
    def test_policy_quota_and_retention_are_fail_closed(self):
        import psycopg
        from intelligence.migrate import apply_migrations
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"],autocommit=True) as setup:
            apply_migrations(setup)
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"]) as conn:
            try:
                # This test transaction is rolled back: no source config leaks.
                conn.execute("""INSERT INTO sources
                   (id,provider,source_type,status,can_search,can_store_metadata,
                    retention_days) VALUES ('dtk_tiktok','evil0ctal_dtk','commercial',
                    'enabled',TRUE,TRUE,7) ON CONFLICT(id) DO UPDATE SET
                    can_search=TRUE,can_store_metadata=TRUE,retention_days=7,status='enabled'""")
                conn.execute("""INSERT INTO source_policy_versions
                   (source_id,version,is_active,capabilities,permissions,retention_policy,reviewed_at)
                   VALUES ('dtk_tiktok',1,TRUE,%s::jsonb,%s::jsonb,%s::jsonb,now())
                   ON CONFLICT (source_id,version) DO UPDATE SET
                   capabilities=EXCLUDED.capabilities,permissions=EXCLUDED.permissions,
                   retention_policy=EXCLUDED.retention_policy,reviewed_at=now(),is_active=TRUE""",
                   (json.dumps({"keyword_search":True}),
                    json.dumps({"can_search":True,"can_store_metadata":True}),
                    json.dumps({"retention_days":7})))
                conn.execute("""INSERT INTO source_quota_ledger
                   (source_id,bucket_name,window_start_at,window_end_at,limit_units)
                   VALUES ('dtk_tiktok','collector.requests',
                           date_trunc('hour',now()),date_trunc('hour',now())+interval '1 day',10)
                   ON CONFLICT(source_id,bucket_name,window_start_at)
                   DO UPDATE SET limit_units=10,reserved_units=0,consumed_units=0""")
                ok=inspect_source_policy(conn,"dtk_tiktok",max_requests=5,max_pages=1)
                self.assertTrue(ok["allowed_for_held_plan"],ok)
                self.assertIsNotNone(ok["policy_version_id"])
                self.assertEqual(ok["quota_available"],10)
                self.assertEqual(ok["quota_reserved"],0)
                self.assertFalse(ok["external_dispatch_authorized"])
                over=inspect_source_policy(conn,"dtk_tiktok",max_requests=11,max_pages=1)
                self.assertIn("quota_insufficient",over["blocks"])
                paging=inspect_source_policy(conn,"dtk_tiktok",max_requests=5,max_pages=2)
                self.assertIn("pagination_not_verified",paging["blocks"])
                conn.execute("UPDATE sources SET can_store_metadata=FALSE WHERE id='dtk_tiktok'")
                denied=inspect_source_policy(conn,"dtk_tiktok",max_requests=1,max_pages=1)
                self.assertIn("source_permissions_disabled",denied["blocks"])
            finally:
                conn.rollback()


if __name__=="__main__":
    unittest.main()
