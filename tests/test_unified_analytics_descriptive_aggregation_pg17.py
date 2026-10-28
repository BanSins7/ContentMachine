"""UA-MOD-20 disposable PostgreSQL 17: no unqualified aggregate disclosure.

Requires CM_TEST_PG_DSN pointing to an isolated PG17 instance.
Reuses UA-MOD-15 synthetic partial persistence ONLY; this is not a
positive peer-qualified E2E approval fixture.
"""
from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import descriptive_aggregation as agg
from test_unified_analytics_transactional_persistence import TransactionalPersistencePG17


class Pins:
    def __init__(self,project:str,video_id:int,snapshot_id:int):
        self.project_id=project
        self.snapshot_id=snapshot_id
        self.candidate=SimpleNamespace(
            scope=SimpleNamespace(project_id=project,video_id=video_id)
        )


class ProbeConnection:
    def __init__(self,db):
        self.db=db
        self.info=db.info
        self.metric_query_count=0

    def transaction(self):
        return self.db.transaction()

    def execute(self,sql,args=()):
        if sql==agg.QUALIFIED_METRIC_SQL:
            self.metric_query_count+=1
            raise AssertionError("No Group C values may be read after denied UAE19")
        return self.db.execute(sql,args)


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "Disposable CM_TEST_PG_DSN PostgreSQL 17 required")
class AggregationPG17(unittest.TestCase):
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

    def test_pg17_denied_uae19_historical_partial_cannot_be_counted(self):
        fixture=self.fixture
        saved=fixture.save()
        pins=(Pins(fixture.project,fixture.video,saved.snapshot_id),)+tuple(
            Pins(fixture.project,fixture.video+n,
                 saved.snapshot_id+n) for n in range(1,5)
        )
        with fixture.connect() as db:
            proxy=ProbeConnection(db)
            with patch.object(agg,"VisibilityPins",Pins):
                # Deliberately the first entry is partial, so current rights
                # and peer details must not be materialized as metric rows.
                res=agg.QualifiedAggregationReader(proxy).summarize_qualified(pins)
            self.assertIsNone(res)
            self.assertEqual(proxy.metric_query_count,0)
            self.assertEqual(db.execute(
                "SELECT count(*) AS n FROM uae_readside_visibility_candidates "
                "WHERE project_id=%s",(fixture.project,)
            ).fetchone()["n"],0)

    def test_pg17_uae85_blocker_survives_aggregation_install(self):
        fixture=self.fixture
        with fixture.connect() as db:
            triggers=db.execute(
                "SELECT count(*) AS n FROM pg_trigger "
                "WHERE tgname IN ("
                "'uae_v1_no_forged_qualified_insert',"
                "'uae_v1_no_forged_complete_evidence_insert') "
                "AND tgenabled='O'"
            ).fetchone()["n"]
            self.assertEqual(triggers,2)
            relation=db.execute(
                "SELECT relkind FROM pg_class "
                "WHERE oid='uae_readside_visibility_candidates'::regclass"
            ).fetchone()["relkind"]
            self.assertEqual(relation,"v")


if __name__=="__main__":
    unittest.main()
