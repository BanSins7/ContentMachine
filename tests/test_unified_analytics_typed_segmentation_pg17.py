"""UA-MOD-21 conditional disposable PostgreSQL 17, no hidden B/category reads.

Synthetic UAE15 persisted partial rows must be invisible even when B
producer tables are not installed; this is a negative test, not peer E2E.
"""
from __future__ import annotations

import os
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import typed_segmentation as seg
from test_unified_analytics_transactional_persistence import TransactionalPersistencePG17
from test_unified_analytics_descriptive_aggregation_pg17 import Pins


class SafeDBProxy:
    def __init__(self,db):
        self.db=db
        self.info=db.info
        self.metrics_read=0
        self.b_labels_read=0

    def transaction(self):
        return self.db.transaction()

    def execute(self,sql,args=()):
        if sql==seg.QUALIFIED_METRIC_SQL:
            self.metrics_read+=1
            raise AssertionError("Unauthorized Group C metric query")
        if sql==seg.GROUP_B_DIMENSIONS_SQL:
            self.b_labels_read+=1
            raise AssertionError("Unauthorized Group B label query")
        return self.db.execute(sql,args)


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "Disposable PostgreSQL 17 CM_TEST_PG_DSN required")
class SegmentationPG17(unittest.TestCase):
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

    def test_pg17_partial_snapshot_cannot_leak_hook_segment(self):
        f=self.fixture
        snap=f.save()
        pins=(Pins(f.project,f.video,snap.snapshot_id),)+tuple(
            Pins(f.project,f.video+n,snap.snapshot_id+n)
            for n in range(1,5)
        )
        with f.connect() as db:
            proxy=SafeDBProxy(db)
            with patch.object(seg,"VisibilityPins",Pins):
                result=seg.TypedSegmentationReader(proxy).summarize(
                    pins,"hook_mechanism",
                )
            self.assertIsNone(result)
            self.assertEqual(proxy.metrics_read,0)
            self.assertEqual(proxy.b_labels_read,0)

    def test_pg17_partial_even_content_kind_not_counted(self):
        f=self.fixture
        snap=f.save()
        pins=(Pins(f.project,f.video,snap.snapshot_id),)+tuple(
            Pins(f.project,f.video+n,snap.snapshot_id+n)
            for n in range(1,5)
        )
        with f.connect() as db:
            proxy=SafeDBProxy(db)
            with patch.object(seg,"VisibilityPins",Pins):
                result=seg.TypedSegmentationReader(proxy).summarize(
                    pins,"content_kind",
                )
            self.assertIsNone(result)
            self.assertEqual(proxy.metrics_read,0)
            self.assertEqual(proxy.b_labels_read,0)
            # New aggregation/segmentation does not weaken UA85 guardians.
            enabled=db.execute(
                "SELECT count(*) AS n FROM pg_trigger "
                "WHERE tgname='uae_v1_no_forged_qualified_insert' "
                "AND tgenabled='O'",
            ).fetchone()["n"]
            self.assertEqual(enabled,1)


if __name__=="__main__":
    unittest.main()
