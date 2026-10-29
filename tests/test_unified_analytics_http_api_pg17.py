"""UA-MOD-26 optional disposable PostgreSQL17 local API authorization.

Unapproved current UAE snapshots must never become qualified API aggregates.
All DB operations require scoped project token, not source URL/localhost.
"""
from __future__ import annotations
from hashlib import sha256
import os
from unittest.mock import patch
from uuid import UUID
import unittest

from intelligence.unified_analytics import http_service as api
from test_unified_analytics_transactional_persistence import TransactionalPersistencePG17

TOKEN="af"*32


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "Disposable CM_TEST_PG_DSN PostgreSQL17 required")
class HTTPAPIRealPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TransactionalPersistencePG17.setUpClass()

    def setUp(self):
        self.f=TransactionalPersistencePG17(
            methodName="test_actual_pg17_idempotent_replay_checksum_and_all_seven_refs"
        )
        self.f.setUp()

    def tearDown(self):
        self.f.tearDown()

    def service(self):
        return api.UAEHTTPService(
            self.f.connect,
            {self.f.project:sha256(bytes.fromhex(TOKEN)).hexdigest()},
        )

    def test_denied_token_never_connects_or_changes_snapshot(self):
        f=self.f
        with patch.object(f,"connect",side_effect=AssertionError("DB touched")):
            service=api.UAEHTTPService(f.connect,{})
            with self.assertRaises(api.UAEHTTPProblem) as caught:
                service.get(f.project,TOKEN,"snapshots",{})
            self.assertEqual(caught.exception.http_status,403)

    def test_pg17_partial_visible_for_diagnostics_not_qualified(self):
        f=self.f
        saved=f.save()
        service=self.service()
        listing=service.get(f.project,TOKEN,"snapshots",{})
        self.assertTrue(all(r["status"] in ("partial","blocked")
                            for r in listing.payload["snapshots"]))
        self.assertIn(saved.snapshot_id,[
            x["id"] for x in listing.payload["snapshots"]
        ])
        detail=service.get(f.project,TOKEN,"snapshot",{
            "snapshot_id":saved.snapshot_id
        })
        self.assertFalse(detail.payload["qualified"])
        with self.assertRaises(api.UAEHTTPProblem) as blocked:
            service.get(f.project,TOKEN,"aggregates",{})
        self.assertEqual(blocked.exception.http_status,409)
        with f.connect() as db:
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_unified_snapshots "
                "WHERE project_id=%s AND status='qualified'",
                (f.project,),
            ).fetchone()["n"],0)

    def test_pg17_job_cross_project_404_without_mutation(self):
        f=self.f
        service=self.service()
        unknown=str(UUID(int=999))
        with self.assertRaises(api.UAEHTTPProblem) as missed:
            service.get(f.project,TOKEN,"jobs",{"job_id":unknown})
        self.assertEqual(missed.exception.http_status,404)
        with self.assertRaises(api.UAEHTTPProblem) as missed:
            service.post(f.project,TOKEN,"cancel",{"job_id":unknown})
        self.assertEqual(missed.exception.http_status,404)


if __name__=="__main__":
    unittest.main()
