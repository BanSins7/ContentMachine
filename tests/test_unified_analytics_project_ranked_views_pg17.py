"""UA24 optional disposable PostgreSQL17 readside and manifest constraints.

Positive future-peer approval is not available in current UAE. Never
run this against production; CM_TEST_PG_DSN must be disposable.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
import os
from unittest.mock import patch
from uuid import uuid4
import unittest

from intelligence.unified_analytics import project_ranked_views as rank
from test_unified_analytics_transactional_persistence import TransactionalPersistencePG17
from test_unified_analytics_descriptive_aggregation_pg17 import Pins


class ReadProbe:
    def __init__(self,conn):
        self.conn=conn
        self.info=conn.info
        self.score_reads=0
        self.writes=0
    def transaction(self):
        return self.conn.transaction()
    def execute(self,sql,args=()):
        if sql==rank.QUALIFIED_METRIC_SQL:
            self.score_reads+=1
            raise AssertionError("C score before qualified peer approval")
        if sql==rank.RUN_INSERT_SQL:
            self.writes+=1
            raise AssertionError("Never save denied snapshot run")
        return self.conn.execute(sql,args)


@unittest.skipUnless(bool(os.environ.get("CM_TEST_PG_DSN")),
                     "Isolated PostgreSQL17 CM_TEST_PG_DSN required")
class RankedRunPG17(unittest.TestCase):
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

    def _parameters(self,key):
        return (self.f.project,key,1,"breakout_desc",5,
                "a"*64,"b"*64,"c"*64,"d"*64,rank.RTB_STATUS)

    def test_partial_uae_snapshot_prevents_persisted_project_ranking(self):
        f=self.f
        saved=f.save()
        pins=(Pins(f.project,f.video,saved.snapshot_id),)+tuple(
            Pins(f.project,f.video+i,saved.snapshot_id+i)
            for i in range(1,5)
        )
        with f.connect() as db:
            probe=ReadProbe(db)
            with patch.object(rank,"VisibilityPins",Pins):
                got=rank.ProjectRankedRunStore(probe).create(
                    pins,"ua24.partial.disallowed"
                )
            self.assertIsNone(got)
            self.assertEqual(probe.score_reads,0)
            self.assertEqual(probe.writes,0)

    def test_duplicate_manifest_insert_is_idempotent(self):
        key="ua24.disposable."+uuid4().hex
        args=self._parameters(key)
        with self.f.connect() as db:
            first=db.execute(rank.RUN_INSERT_SQL,args).fetchone()
            self.assertIsNotNone(first)
            duplicate=db.execute(rank.RUN_INSERT_SQL,args).fetchone()
            self.assertIsNone(duplicate)
            stored=db.execute(rank.RUN_READ_KEY_SQL,(
                self.f.project,key,1
            )).fetchone()
            self.assertEqual(stored["run_id"],first["run_id"])
            self.assertEqual(stored["rtb_evidence_state"],rank.RTB_STATUS)

    def test_concurrent_unique_version_constraint(self):
        key="ua24.concurrent."+uuid4().hex
        args=self._parameters(key)
        def write(_):
            with self.f.connect() as db:
                row=db.execute(rank.RUN_INSERT_SQL,args).fetchone()
                return row["run_id"] if row else None
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts=list(pool.map(write,(1,2)))
        self.assertEqual(sum(x is not None for x in attempts),1)
        with self.f.connect() as db:
            record=db.execute(rank.RUN_READ_KEY_SQL,(
                self.f.project,key,1
            )).fetchone()
            self.assertEqual(record["run_id"],next(x for x in attempts if x))
            check=db.execute(
                "SELECT count(*) AS n FROM uae_project_aggregate_runs "
                "WHERE project_id=%s AND request_key=%s",
                (self.f.project,key),
            ).fetchone()
            self.assertEqual(check["n"],1)


if __name__=="__main__":
    unittest.main()
