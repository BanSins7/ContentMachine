"""SP-13 Deep C9 picker read-only PostgreSQL evidence gate.

SQL fake exercises project identity, both Human Reviews, Deep publication
final exact tree check, no flat C9 fallback, and bounded latest-first results.
"""
from __future__ import annotations

from datetime import datetime,timezone
from unittest.mock import patch
import unittest

from intelligence.search_planner.input_loader import list_approved_publications
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

PID="a"*32
PUB="44444444-4444-4444-8444-444444444444"
T=datetime(2026,10,26,11,tzinfo=timezone.utc)


class DBResult:
    def __init__(self, rows):self.rows=rows
    def fetchall(self):return self.rows


class FakeConnection:
    def __init__(self,rows):
        self.rows=rows
        self.calls=[]
        self.transactions=0
    def __enter__(self):return self
    def __exit__(self,*a):return False
    def transaction(self):
        self.transactions+=1
        return self
    def execute(self,sql,args=None):
        self.calls.append((sql,args))
        if sql.startswith("SET TRANSACTION"):
            return DBResult([])
        if "FROM tb_deep_publications d" in sql:
            return DBResult(self.rows)
        raise AssertionError("Unexpected SQL "+sql)


class ApprovedC9PickerSP13(unittest.TestCase):
    def test_latest_reviewed_c9_publications_are_revalidated_in_same_snapshot(self):
        db=FakeConnection([(PUB,1,T)])
        expected={
            "taxonomy_publication_id":PUB,
            "taxonomy_version":1,
            "taxonomy_sha256":"0"*64,
            "tree":[
                {"id":"hyp1","kind":"hypothesis"},
                {"id":"dir1","kind":"direction"},
            ],
        }
        with patch(
            "intelligence.search_planner.input_loader.read_published_snapshot",
            return_value=expected,
        ) as trusted:
            outcome=list_approved_publications(PID,connect=lambda:db)
        self.assertEqual(outcome["count"],1)
        self.assertEqual(outcome["publications"][0]["taxonomy_sha256"],"0"*64)
        self.assertEqual(outcome["publications"][0]["approved_hypothesis_count"],1)
        self.assertEqual(outcome["publications"][0]["source_status"],"approved_deep_c9")
        self.assertEqual(outcome["provider_requests_in_this_read"],0)
        self.assertEqual(db.transactions,1)
        self.assertIn("READ ONLY",db.calls[0][0])
        sql=db.calls[1][0]
        self.assertIn("JOIN tb_deep_runs dr",sql)
        self.assertIn("dr.status='approved'",sql)
        self.assertIn("tb_deep_reviews rv",sql)
        self.assertIn("rv.review_no=1",sql)
        self.assertIn("rv.review_no=2",sql)
        self.assertIn("rv.approved_nodes=d.tree",sql)
        self.assertEqual(db.calls[1][1],(PID,30))
        trusted.assert_called_once_with(db,PID,PUB)

    def test_no_approved_publications_is_honest_empty_result(self):
        db=FakeConnection([])
        result=list_approved_publications(PID,connect=lambda:db)
        self.assertEqual(result["publications"],[])
        self.assertEqual(result["count"],0)

    def test_cross_project_and_large_query_limits_fail_before_sql(self):
        db=FakeConnection([])
        for arg in (0,51,True,"50",1.2):
            with self.subTest(limit=arg),self.assertRaises(PipelineError):
                list_approved_publications(PID,limit=arg,connect=lambda:db)
        with self.assertRaises(PipelineError):
            list_approved_publications("invalid-project",connect=lambda:db)
        self.assertEqual(db.calls,[])

    def test_stale_version_refused_before_returning_publication(self):
        db=FakeConnection([(PUB,3,T)])
        with patch(
            "intelligence.search_planner.input_loader.read_published_snapshot",
            return_value={
                "taxonomy_version":2,
                "taxonomy_sha256":"0"*64,
                "tree":[],
            },
        ):
            with self.assertRaisesRegex(PipelineError,"changed during listing"):
                list_approved_publications(PID,connect=lambda:db)


if __name__=="__main__":
    unittest.main()
