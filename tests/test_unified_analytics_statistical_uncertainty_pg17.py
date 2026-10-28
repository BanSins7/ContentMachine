"""UA-MOD-23 disposable PostgreSQL17 fail-closed quality reader probes.

These tests deliberately use UAE15 partial fixtures and DO NOT invent
future effective producer approval. CM_TEST_PG_DSN must be disposable.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import statistical_uncertainty as qua
from intelligence.unified_analytics import descriptive_aggregation as agg
from intelligence.unified_analytics import typed_segmentation as seg
from intelligence.unified_analytics import cohort_comparison as cmp

from test_unified_analytics_transactional_persistence import TransactionalPersistencePG17
from test_unified_analytics_descriptive_aggregation_pg17 import Pins


class ProtectedDB:
    def __init__(self,db):
        self.db=db
        self.info=db.info
        self.numeric_queries=0
        self.b_queries=0
        self.author_queries=0
    def transaction(self):
        return self.db.transaction()
    def execute(self,sql,args=()):
        if sql==agg.QUALIFIED_METRIC_SQL:
            self.numeric_queries+=1
            raise AssertionError("No C metrics before authorization")
        if sql==seg.GROUP_B_DIMENSIONS_SQL:
            self.b_queries+=1
            raise AssertionError("No B labels before authorization")
        if sql==cmp.AUTHOR_TIME_SQL:
            self.author_queries+=1
            raise AssertionError("No creator/time before authorization")
        return self.db.execute(sql,args)


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "Disposable PostgreSQL17 CM_TEST_PG_DSN required")
class UncertaintyPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TransactionalPersistencePG17.setUpClass()

    def setUp(self):
        self.fixture=TransactionalPersistencePG17(
            methodName="test_actual_pg17_idempotent_replay_checksum_and_all_seven_refs"
        )
        self.fixture.setUp()

    def tearDown(self):
        self.fixture.tearDown()

    def pinned(self,n=5):
        f=self.fixture
        saved=f.save()  # an ACTUAL partial, never an effective qualified row
        return tuple(
            Pins(f.project,f.video+i,saved.snapshot_id+i)
            for i in range(n)
        )

    def test_current_pg17_partial_never_generates_coverage_or_iqr(self):
        pins=self.pinned()
        with self.fixture.connect() as db:
            p=ProtectedDB(db)
            with patch.object(agg,"VisibilityPins",Pins),\
                 patch.object(seg,"VisibilityPins",Pins):
                reader=qua.QualifiedUncertaintyReader(p)
                self.assertIsNone(reader.summarize(pins))
                self.assertIsNone(reader.segment(pins,"hook_mechanism"))
            self.assertEqual(p.numeric_queries,0)
            self.assertEqual(p.b_queries,0)
            self.assertEqual(p.author_queries,0)

    def test_current_pg17_partial_never_generates_cohort_ci(self):
        left=self.pinned()
        f=self.fixture
        right=tuple(
            Pins(f.project,f.video+i+20,left[0].snapshot_id+i+20)
            for i in range(5)
        )
        now=datetime.now(timezone.utc)
        a=now-timedelta(days=20)
        b=now-timedelta(days=2)
        win=cmp.PublicationWindow(a,b)
        spec=cmp.ComparisonSpec(win,win,creator_id=1)
        with f.connect() as db:
            p=ProtectedDB(db)
            with patch.object(cmp,"VisibilityPins",Pins):
                self.assertIsNone(
                    qua.QualifiedUncertaintyReader(p).compare(
                        left,right,spec,
                    )
                )
            self.assertEqual(p.numeric_queries,0)
            self.assertEqual(p.author_queries,0)
            self.assertEqual(p.b_queries,0)


if __name__=="__main__":
    unittest.main()
