"""GB-MOD-26 optional disposable PG17 operator Studio integration checks.

Real run/status/history and cancel DB transitions; no model provider, no
implied production reviewer identity or paid approval.
"""
from __future__ import annotations

import os
import unittest

from intelligence.group_b.studio_service import (
    GroupBStudioService,StudioProblem,
)
from intelligence.migrate import apply_migrations
import test_group_b_atomic_db as fixtures


class LocalTestHost:
    def verify_cancel_operator(self,project_id,run_id,conn):
        return True


def disposable_dsn():
    from psycopg import conninfo
    dsn=os.environ["CM_TEST_PG_DSN"]
    db=conninfo.conninfo_to_dict(dsn).get("dbname","").lower()
    if "test" not in db and "_ci" not in db:
        raise RuntimeError("Studio DB tests require disposable database")
    return dsn


@unittest.skipUnless(os.getenv("CM_TEST_PG_DSN"),"No disposable PostgreSQL17")
class TestB26StudioPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import psycopg
        cls.psycopg=psycopg
        cls.dsn=disposable_dsn()
        with psycopg.connect(cls.dsn) as conn:
            apply_migrations(conn)

    def prepare(self):
        base=fixtures.TestAtomicGroupBPG17("runTest")
        base.psycopg=self.psycopg
        base.dsn=self.dsn
        data=base.prepare()
        with self.psycopg.connect(self.dsn) as conn:
            saved=base.write(conn,data)
        return data[0],saved

    def service(self,*,host=None,allow=True):
        from psycopg.rows import dict_row
        return GroupBStudioService(
            lambda:self.psycopg.connect(self.dsn,row_factory=dict_row),
            operator=lambda project,action,db:allow,
            host=host,
        )

    def test_paginated_run_and_b1_b8_statuses_hide_private_media(self):
        m,saved=self.prepare()
        app=self.service()
        result=app.list_runs(m.project_id,offset=0,limit=5)
        self.assertTrue(any(row["run_id"]==m.b_run_id for row in result["items"]))
        d=app.detail(m.project_id,m.b_run_id,offset=0,limit=5)
        self.assertEqual(d["run"]["result_analysis_id"],saved.analysis_id)
        self.assertEqual([row["block"] for row in d["blocks"]],
                         [f"B{i}" for i in range(1,9)])
        self.assertEqual(d["current_qualification"],"unverified")
        self.assertFalse(d["invalidated"])
        self.assertEqual(d["review_revision"],0)
        self.assertNotIn("method_details",d["run"])
        self.assertNotIn("spoken_text",str(d))
        self.assertEqual(len(d["jobs"]),0)

    def test_wrong_project_never_gets_exact_run_or_metadata(self):
        m,_=self.prepare()
        app=self.service()
        with self.assertRaises(StudioProblem) as err:
            app.detail("0"*32,m.b_run_id)
        self.assertEqual(err.exception.http_status,404)

    def test_unapproved_evidence_quote_start_and_review_stay_closed(self):
        m,_=self.prepare()
        app=self.service()
        with self.assertRaises(StudioProblem) as err:
            app.evidence(m.project_id,m.b_run_id,block="B8")
        self.assertEqual(err.exception.http_status,424)
        with self.assertRaises(StudioProblem):
            app.results(m.project_id,m.b_run_id,block="B8")
        with self.assertRaises(StudioProblem):
            app.quote(m.project_id,m.b_run_id,{
                "model_id":"catalog/real","blocks":["B1"],
                "idempotency_key":"quote-test-123456"})
        with self.assertRaises(StudioProblem):
            app.start(m.project_id,m.b_run_id,{
                "blocks":["B5"],"request_key":"start-test-12345"})

    def test_cancel_is_transactional_and_idempotent_with_test_authority(self):
        m,_=self.prepare()
        app=self.service(host=LocalTestHost())
        stopped=app.cancel(m.project_id,m.b_run_id,{"confirm":True})
        self.assertEqual(stopped["cancelled_jobs"],0)
        self.assertFalse(stopped["paid_reservations_refunded"])
        status=app.detail(m.project_id,m.b_run_id)
        self.assertEqual(status["run"]["state"],"cancelled")
        replay=app.cancel(m.project_id,m.b_run_id,{"confirm":True})
        self.assertEqual(replay["cancelled_jobs"],0)

    def test_local_profile_is_not_sufficient_to_approve_paid_actions(self):
        m,_=self.prepare()
        app=self.service(host=LocalTestHost())
        with self.assertRaises(StudioProblem) as err:
            app.approve(m.project_id,m.b_run_id,{
                "quote_id":"00000000-0000-0000-0000-000000000001",
                "authorized_usd":"1.00","confirmation":True,
            })
        self.assertEqual(err.exception.http_status,424)

    def test_no_operator_permission_denies_read_and_cancel(self):
        m,_=self.prepare()
        app=self.service(allow=False,host=LocalTestHost())
        for action in (
            lambda:app.detail(m.project_id,m.b_run_id),
            lambda:app.cancel(m.project_id,m.b_run_id,{"confirm":True}),
        ):
            with self.assertRaises(StudioProblem) as err:
                action()
            self.assertEqual(err.exception.http_status,403)


if __name__=="__main__":
    unittest.main()
