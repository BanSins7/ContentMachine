"""UA25 disposable PostgreSQL17 negative handoff tests (no forged approval).

The current UAE084/085 database blocks effective qualified output, so real
positive signed producer E2E remains outside this regression suite.
"""
from __future__ import annotations

import os
from unittest.mock import patch
from uuid import UUID
import unittest

from intelligence.unified_analytics import pie_research_handoff as pie
from intelligence.unified_analytics import project_ranked_views as rank
from test_unified_analytics_transactional_persistence import TransactionalPersistencePG17
from test_unified_analytics_descriptive_aggregation_pg17 import Pins


class SourceGuard:
    def __init__(self,db):
        self.db=db
        self.info=db.info
        self.metric_calls=0
        self.b_calls=0
        self.a_calls=0
    def transaction(self):
        return self.db.transaction()
    def execute(self,sql,args=()):
        if sql==pie.QUALIFIED_METRIC_SQL:
            self.metric_calls+=1
            raise AssertionError("No metric allowed before verified live")
        if sql==pie.GROUP_B_DIMENSIONS_SQL:
            self.b_calls+=1
            raise AssertionError("No B labels before verified live")
        if sql in (pie.A_VIDEO_SQL,pie.A_CAROUSEL_SQL):
            self.a_calls+=1
            raise AssertionError("No A features before verified live")
        return self.db.execute(sql,args)


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "Disposable PostgreSQL17 CM_TEST_PG_DSN required")
class PIEHandoffPG17(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        TransactionalPersistencePG17.setUpClass()

    def setUp(self):
        self.fixture=TransactionalPersistencePG17(
            methodName="test_actual_pg17_idempotent_replay_checksum_and_all_seven_refs"
        )
        self.fixture.setUp()
        self.signer=pie.ReceiptSigner(
            b"disposable test signing key 32 bytes long!!",
            key_id="test.temporary.key",
        )

    def tearDown(self):
        self.fixture.tearDown()

    def test_pg17_partial_cannot_create_signed_evidence(self):
        f=self.fixture
        stored=f.save()
        pins=(Pins(f.project,f.video,stored.snapshot_id),)+tuple(
            Pins(f.project,f.video+i,stored.snapshot_id+i)
            for i in range(1,5)
        )
        with f.connect() as db:
            guarded=SourceGuard(db)
            with patch.object(pie,"VisibilityPins",Pins):
                output=pie.PIEResearchExportReader(
                    guarded,self.signer,
                ).export(str(UUID(int=123)),pins,"a"*64)
            self.assertIsNone(output)
            self.assertEqual(guarded.metric_calls,0)
            self.assertEqual(guarded.b_calls,0)
            self.assertEqual(guarded.a_calls,0)

    def test_pg17_negative_no_unqualified_manifest_or_global_grant(self):
        f=self.fixture
        f.save()
        with f.connect() as db:
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_readside_visibility_candidates "
                "WHERE project_id=%s",(f.project,),
            ).fetchone()["n"],0)
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_unified_snapshots "
                "WHERE project_id=%s AND status='qualified'",(f.project,),
            ).fetchone()["n"],0)
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM pg_trigger "
                "WHERE tgname='uae_v1_no_forged_qualified_insert' "
                "AND tgenabled='O'",
            ).fetchone()["n"],1)


if __name__=="__main__":
    unittest.main()
