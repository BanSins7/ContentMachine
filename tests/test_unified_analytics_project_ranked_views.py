"""UA-MOD-24 synthetic future-positive rank/pagination tests.

The mock represents hypothetical future effective peer approvals; UA85
currently forbids actual positive qualified records in production.
"""
from __future__ import annotations

from decimal import Decimal
from uuid import UUID
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics import project_ranked_views as rank
from test_unified_analytics_descriptive_aggregation import (
    Pins, FakeDB, MockVisible, Cursor,
)

SHA="d"*64


class RankDB(FakeDB):
    def __init__(self,pins,values=None):
        vals=values or tuple((Decimal(i),i*10) for i in range(1,len(pins)+1))
        super().__init__(pins,vals)
        self.store={}
        self.run_selects=0
        self.run_inserts=0
        self.seq=0

    def execute(self,sql,args=()):
        if sql==rank.RUN_INSERT_SQL:
            self.sql.append(sql)
            self.run_inserts+=1
            (project,key,revision,mode,n,compat,lineage,stats,ranked,rtb)=args
            k=(project,key,revision)
            if k in self.store:
                return Cursor([])
            self.seq+=1
            run_id=str(UUID(int=self.seq))
            row=dict(run_id=run_id,project_id=project,request_key=key,
                     run_revision=revision,schema_version=rank.PROJECT_RUN_WIRE,
                     sort_mode=mode,sample_size=n,
                     compatibility_sha256=compat,lineage_sha256=lineage,
                     stats_sha256=stats,ranked_sha256=ranked,
                     rtb_evidence_state=rtb)
            self.store[k]=row
            return Cursor([{"run_id":run_id}])
        if sql==rank.RUN_READ_KEY_SQL:
            self.sql.append(sql)
            self.run_selects+=1
            return Cursor([self.store[args]] if args in self.store else [])
        if sql==rank.RUN_READ_ID_SQL:
            self.sql.append(sql)
            self.run_selects+=1
            row=next((v for v in self.store.values()
                      if v["run_id"]==args[1] and v["project_id"]==args[0]),None)
            return Cursor([row] if row else [])
        return super().execute(sql,args)


def fixture(n=5,values=None):
    pins=tuple(Pins(i) for i in range(1,n+1))
    for p in pins:
        p.candidate.fingerprint=SHA
    return pins,RankDB(pins,values)


class SafeRankTest(unittest.TestCase):
    def setUp(self):
        self.pins,self.db=fixture()
        self.reader=rank.ProjectRankedRunStore(self.db)

    def create(self,key="project-ranking-v1",rev=1,mode="breakout_desc"):
        with patch.object(rank,"VisibilityPins",Pins),\
             patch.object(rank,"VisibleSnapshotReader",MockVisible):
            return self.reader.create(
                self.pins,key,run_revision=rev,sort_mode=mode
            )

    def page(self,run,limit=2,cursor=None,pins=None):
        with patch.object(rank,"VisibilityPins",Pins),\
             patch.object(rank,"VisibleSnapshotReader",MockVisible):
            return self.reader.page(run.run_id,pins or self.pins,
                                    limit=limit,cursor=cursor)

    def export(self,run):
        with patch.object(rank,"VisibilityPins",Pins),\
             patch.object(rank,"VisibleSnapshotReader",MockVisible):
            return self.reader.export(run.run_id,self.pins)

    def test_versioned_manifest_no_raw_score_cache(self):
        r=self.create()
        self.assertIsInstance(r,rank.ProjectRankRun)
        self.assertEqual(r.sample_size,5)
        self.assertEqual(r.breakout.median,"3")
        self.assertEqual(r.views.median,"30")
        self.assertEqual(r.rtb_evidence_state,rank.RTB_STATUS)
        self.assertEqual(self.db.run_inserts,1)
        row=next(iter(self.db.store.values()))
        self.assertNotIn("breakout_score",row)
        self.assertNotIn("views",row)
        self.assertNotIn("creator_id",row)
        self.assertEqual(self.db.auth_count,5)
        self.assertEqual(self.db.query_count,5)

    def test_replay_same_revision_is_idempotent(self):
        a=self.create()
        b=self.create()
        self.assertEqual(a,b)
        self.assertEqual(self.db.run_inserts,2)
        self.assertEqual(len(self.db.store),1)

    def test_new_revision_is_separate_immutable_run(self):
        a=self.create(rev=1)
        b=self.create(rev=2)
        self.assertNotEqual(a.run_id,b.run_id)
        self.assertEqual(len(self.db.store),2)
        self.assertEqual(a.ranked_sha256,b.ranked_sha256)

    def test_duplicate_request_key_same_version_new_sort_denied(self):
        self.create(mode="breakout_desc")
        with self.assertRaises(AnalyticsBoundaryError):
            self.create(mode="views_desc")
        self.assertEqual(len(self.db.store),1)

    def test_changed_score_during_replay_denied(self):
        self.create()
        self.db.rows[self.pins[0].snapshot_id]["breakout_score"]=Decimal(100)
        with self.assertRaises(AnalyticsBoundaryError):
            self.create()
        self.assertEqual(len(self.db.store),1)

    def test_ranked_breakout_sorted_and_ties_by_video_id(self):
        self.db.rows[self.pins[0].snapshot_id]["breakout_score"]=Decimal(5)
        self.db.rows[self.pins[1].snapshot_id]["breakout_score"]=Decimal(5)
        self.db.rows[self.pins[2].snapshot_id]["breakout_score"]=Decimal(5)
        run=self.create()
        all_items=self.export(run).items
        self.assertEqual(tuple(x.video_id for x in all_items),(1,2,3,5,4))

    def test_views_desc_sort_key(self):
        run=self.create(mode="views_desc")
        exp=self.export(run)
        self.assertEqual(tuple(x.video_id for x in exp.items),(5,4,3,2,1))
        self.assertEqual(exp.items[0].views,50)

    def test_video_asc_sort_key(self):
        self.db.rows[self.pins[4].snapshot_id]["breakout_score"]=Decimal(999)
        run=self.create(mode="video_id_asc")
        self.assertEqual(tuple(x.video_id for x in self.export(run).items),
                         (1,2,3,4,5))

    def test_stable_three_pages_no_overlap_and_end(self):
        run=self.create()
        first=self.page(run)
        second=self.page(run,cursor=first.cursor)
        third=self.page(run,cursor=second.cursor)
        self.assertEqual(tuple(x.video_id for x in first.items),(5,4))
        self.assertEqual(tuple(x.video_id for x in second.items),(3,2))
        self.assertEqual(tuple(x.video_id for x in third.items),(1,))
        self.assertIsNone(third.cursor)
        self.assertEqual(
            {x.video_id for pg in (first,second,third) for x in pg.items},
            {1,2,3,4,5}
        )

    def test_first_page_one_record_not_global_one_sample(self):
        run=self.create()
        p=self.page(run,limit=1)
        self.assertEqual(len(p.items),1)
        self.assertEqual(p.run.sample_size,5)

    def test_cursor_idempotent_replay_same_page(self):
        run=self.create()
        a=self.page(run,limit=2)
        b=self.page(run,limit=2)
        self.assertEqual(a,b)
        self.assertEqual(self.page(run,cursor=a.cursor),
                         self.page(run,cursor=b.cursor))

    def test_cursor_from_different_run_rejected(self):
        a=self.create(rev=1)
        b=self.create(rev=2)
        c=self.page(a).cursor
        with self.assertRaises(AnalyticsBoundaryError):
            self.page(b,cursor=c)

    def test_forged_cursor_checksum_rejected(self):
        run=self.create()
        with self.assertRaises(AnalyticsBoundaryError):
            rank.PageCursor(
                run.run_id,run.ranked_sha256,run.sort_mode,1,"0"*64
            )

    def test_stale_rank_cursor_rejected_after_digest_change(self):
        run=self.create()
        c=self.page(run).cursor
        self.db.rows[self.pins[0].snapshot_id]["views"]=1000
        self.assertIsNone(self.page(run,cursor=c))

    def test_one_denied_member_suppresses_entire_page_and_export(self):
        run=self.create()
        self.db.denied.add(self.pins[-1].snapshot_id)
        before=self.db.query_count
        self.assertIsNone(self.page(run))
        self.assertIsNone(self.export(run))
        self.assertEqual(self.db.query_count,before)

    def test_denied_first_member_no_metric_query(self):
        self.db.denied.add(self.pins[0].snapshot_id)
        self.assertIsNone(self.create())
        self.assertEqual(self.db.query_count,0)
        self.assertEqual(self.db.run_inserts,0)

    def test_partial_and_empty_set_no_io(self):
        with patch.object(rank,"VisibilityPins",Pins):
            self.assertIsNone(self.reader.create((),"project-ranking-v1"))
            self.assertIsNone(self.reader.create(
                self.pins[:4],"project-ranking-v1"
            ))
        self.assertEqual(self.db.sql,[])

    def test_no_score_missing_or_approximate(self):
        self.db.rows[self.pins[-1].snapshot_id]["breakout_score"]=None
        self.assertIsNone(self.create())
        self.db.rows[self.pins[-1].snapshot_id]["breakout_score"]=Decimal(5)
        self.db.rows[self.pins[-1].snapshot_id]["precision"]="rounded"
        self.assertIsNone(self.create())
        self.assertEqual(self.db.run_inserts,0)

    def test_no_mixed_source_or_config_ranking(self):
        self.db.rows[self.pins[-1].snapshot_id]["source_metric_version"]="bad-v2"
        self.assertIsNone(self.create())
        self.db.rows[self.pins[-1].snapshot_id]["source_metric_version"]="source-v1"
        self.db.rows[self.pins[-1].snapshot_id]["platform"]="instagram"
        self.assertIsNone(self.create())

    def test_pinned_provenance_drift_denies_reuse(self):
        run=self.create()
        self.pins[-1].candidate.fingerprint="a"*64
        self.assertIsNone(self.page(run))
        self.assertEqual(len(self.db.store),1)

    def test_new_metric_ranking_digest_denies_read(self):
        run=self.create()
        self.db.rows[self.pins[3].snapshot_id]["views"]=400
        self.assertIsNone(self.export(run))

    def test_more_than_50_or_duplicate_video_rejected(self):
        with patch.object(rank,"VisibilityPins",Pins):
            for bad in (self.pins+self.pins[:1],
                        tuple(Pins(i) for i in range(1,52))):
                with self.assertRaises(AnalyticsBoundaryError):
                    self.reader.create(bad,"project-ranking-v1")
        self.assertEqual(self.db.sql,[])

    def test_sort_and_revision_type_restrictions_preio(self):
        with patch.object(rank,"VisibilityPins",Pins):
            for bad in ("score_asc","",True,None):
                with self.assertRaises(AnalyticsBoundaryError):
                    self.reader.create(self.pins,"project-ranking-v1",
                                       sort_mode=bad)
            for bad in (0,-1,True,"2"):
                with self.assertRaises(AnalyticsBoundaryError):
                    self.reader.create(self.pins,"project-ranking-v1",
                                       run_revision=bad)
        self.assertEqual(self.db.sql,[])

    def test_invalid_page_size_rejected(self):
        with self.assertRaises(AnalyticsBoundaryError):
            self.reader.page("nope",self.pins,limit=0)
        with self.assertRaises(AnalyticsBoundaryError):
            self.reader.page("nope",self.pins,limit=21)
        with self.assertRaises(AnalyticsBoundaryError):
            self.reader.page("nope",self.pins,limit=True)

    def test_unauthorized_unknown_run_no_public_catalog(self):
        run=self.create()
        empty=str(UUID(int=999))
        with patch.object(rank,"VisibilityPins",Pins),\
             patch.object(rank,"VisibleSnapshotReader",MockVisible):
            self.assertIsNone(self.reader.page(empty,self.pins))
        self.assertEqual(len(self.db.store),1)

    def test_nonidle_transaction_denied(self):
        self.db.info.transaction_status=2
        with patch.object(rank,"VisibilityPins",Pins):
            with self.assertRaises(AnalyticsBoundaryError):
                self.reader.create(self.pins,"project-ranking-v1")

    def test_score_change_late_member_returns_no_partial_page(self):
        run=self.create()
        self.db.denied.add(self.pins[-1].snapshot_id)
        self.assertIsNone(self.page(run,limit=1))

    def test_export_is_internal_typed_no_network_or_csv(self):
        run=self.create()
        out=self.export(run)
        self.assertIsInstance(out,rank.RankedExport)
        self.assertEqual(len(out.items),5)
        self.assertTrue(all(type(p.views) is int for p in out.items))
        self.assertFalse(hasattr(out,"public_url"))
        self.assertFalse(hasattr(out,"raw_media"))

    def test_sql_no_qualified_status_promotion_or_grant(self):
        for s in (rank.RUN_INSERT_SQL,rank.RUN_READ_KEY_SQL,
                  rank.RUN_READ_ID_SQL):
            self.assertNotIn("UPDATE uae_unified_snapshots",s)
            self.assertNotIn("GRANT ",s)
            self.assertNotIn("SET status='qualified'",s)
        self.assertIn("ON CONFLICT",rank.RUN_INSERT_SQL)
        self.assertIn("AND id=%s::uuid",rank.RUN_READ_ID_SQL)


if __name__=="__main__":
    unittest.main()
