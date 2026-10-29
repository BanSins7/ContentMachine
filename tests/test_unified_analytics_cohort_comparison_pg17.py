"""UA-MOD-22 disposable PG17 negative current qualified/read privacy.

Real database test uses UAE15 partial snapshots (never invented qualified
approval). Disallows author/time or score queries before full UA19 gate.
"""
from __future__ import annotations

import os
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import cohort_comparison as cmp
from test_unified_analytics_transactional_persistence import TransactionalPersistencePG17
from test_unified_analytics_descriptive_aggregation_pg17 import Pins


class ReadIsolationProxy:
    def __init__(self,db):
        self.db=db
        self.info=db.info
        self.score_reads=0
        self.author_reads=0
    def transaction(self):
        return self.db.transaction()
    def execute(self,sql,args=()):
        if sql==cmp.QUALIFIED_METRIC_SQL:
            self.score_reads+=1
            raise AssertionError("Unauthorized Group C query")
        if sql==cmp.AUTHOR_TIME_SQL:
            self.author_reads+=1
            raise AssertionError("Unauthorized author/time join")
        return self.db.execute(sql,args)


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "Disposable CM_TEST_PG_DSN PostgreSQL17 required")
class CohortComparisonPG17(unittest.TestCase):
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

    def spec(self):
        now=datetime.now(timezone.utc)
        end=now-timedelta(days=1)
        start=end-timedelta(days=30)
        window=cmp.PublicationWindow(start,end)
        return cmp.ComparisonSpec(window,window,creator_id=1)

    def test_real_pg17_partial_snapshot_cannot_expose_cohort_membership(self):
        f=self.f
        snap=f.save()
        left=(Pins(f.project,f.video,snap.snapshot_id),)+tuple(
            Pins(f.project,f.video+i,snap.snapshot_id+i)
            for i in range(1,5)
        )
        right=tuple(
            Pins(f.project,f.video+i+20,snap.snapshot_id+i+20)
            for i in range(5)
        )
        with f.connect() as db:
            guarded=ReadIsolationProxy(db)
            with patch.object(cmp,"VisibilityPins",Pins):
                result=cmp.CompatibleCohortReader(guarded).compare(
                    left,right,self.spec()
                )
            self.assertIsNone(result)
            self.assertEqual(guarded.score_reads,0)
            self.assertEqual(guarded.author_reads,0)

    def test_real_pg17_revocation_cannot_make_historical_partial_visible(self):
        f=self.f
        snap=f.save()
        with f.connect() as db:
            with db.transaction():
                db.execute(
                    "UPDATE sources SET status='revoked' WHERE id=%s",
                    (f.source,),
                )
                db.execute(
                    "INSERT INTO uae_snapshot_invalidations("
                    "project_id,snapshot_id,reason_code,reason_sha256)"
                    " VALUES(%s,%s,'source_revoked',%s)",
                    (f.project,snap.snapshot_id,"c"*64),
                )
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_readside_visibility_candidates "
                "WHERE project_id=%s",(f.project,),
            ).fetchone()["n"],0)
            trigger=db.execute(
                "SELECT count(*) AS n FROM pg_trigger "
                "WHERE tgname='uae_v1_no_forged_qualified_insert' "
                "AND tgenabled='O'",
            ).fetchone()["n"]
            self.assertEqual(trigger,1)


if __name__=="__main__":
    unittest.main()
