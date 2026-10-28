"""UA-MOD-21 independent typed B1/B2/B3/B5/B6/B7/B8 extraction tests.

All positive approval/DB fixtures are SYNTHETIC and patched. They do not
certify effective UAE-085, SRE, QDP, GA/GB/GC production approvals.
"""
from __future__ import annotations

from contextlib import contextmanager
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import patch
import unittest

from intelligence.unified_analytics import AnalyticsBoundaryError
from intelligence.unified_analytics import typed_segmentation as seg
from test_unified_analytics_descriptive_aggregation import (
    Pins, FakeDB, MockVisible, Cursor,
)

RUN="00112233-4455-4677-8899-aabbccddeeff"


def make_fact(p):
    b=p.candidate.group_b
    s=p.candidate.scope
    return dict(
        analysis_id=b.b_analysis_id,
        video_id=s.video_id,
        source_id=s.source_id,
        source_policy_version_id=s.source_policy_version_id,
        content_type="video",
        analysis_version=b.analysis_version,
        taxonomy_version=b.taxonomy_version,
        hook_mechanisms=["curiosity","promise","curiosity"],
        hook_status="inferred",hook_taxonomy=b.taxonomy_version,
        video_primary="talking_head",carousel_format=None,
        format_status="inferred",format_taxonomy=b.taxonomy_version,
        story_roles=["hook","context","hook"],
        editing_type="fast_cuts",subtitle_type="dynamic",
        cta_status="complete",cta_taxonomy=b.taxonomy_version,
        cta_types=["follow","like","follow"],cta_review_count=0,
        topic_id=42,topic_needs_review=False,
        topic_taxonomy=b.taxonomy_version,
        topic_merged_into=None,category_merged_into=None,
    )


class SegmentFakeDB(FakeDB):
    def __init__(self,pins):
        values=tuple((Decimal(i),i*10) for i in range(1,len(pins)+1))
        super().__init__(pins,values)
        self.b_rows={p.candidate.group_b.b_analysis_id:make_fact(p) for p in pins}
        self.b_reads=0
    def execute(self,sql,args=()):
        if sql==seg.GROUP_B_DIMENSIONS_SQL:
            self.b_reads+=1
            self.sql.append(sql)
            return Cursor([self.b_rows[args[-1]]]
                          if args[-1] in self.b_rows else [])
        return super().execute(sql,args)


def fixture(n=5):
    pins=tuple(Pins(i) for i in range(1,n+1))
    for p in pins:
        p.candidate.scope.source_policy_version_id=5
        p.candidate.group_b.b_analysis_id=p.candidate.scope.video_id+5000
        p.candidate.group_b.b_run_id=RUN
    db=SegmentFakeDB(pins)
    return pins,db


@contextmanager
def approved_mock():
    with patch.object(seg,"VisibilityPins",Pins),\
         patch.object(seg,"VisibleSnapshotReader",MockVisible):
        yield


class PureTypedLabels(unittest.TestCase):
    def setUp(self):
        self.pins,self.db=fixture()
        self.row=self.db.b_rows[self.pins[0].candidate.group_b.b_analysis_id]
        self.tax=self.pins[0].candidate.group_b.taxonomy_version

    def labels(self,dimension,kind="video",data=None):
        return seg._typed_values(
            data if data is not None else self.row,dimension,kind,self.tax
        )

    def test_exact_eight_dimensions(self):
        self.assertEqual(seg.DIMENSIONS,frozenset((
            "content_kind","hook_mechanism","video_format","story_role",
            "editing_type","subtitle_type","cta_type","topic_id",
        )))

    def test_content_kind(self):
        self.assertEqual(self.labels("content_kind"),("video",))
        self.assertEqual(self.labels("content_kind","photo_carousel"),
                         ("photo_carousel",))

    def test_hook_multilabel_dedup_sorted(self):
        self.assertEqual(self.labels("hook_mechanism"),("curiosity","promise"))

    def test_format_one_video_label(self):
        self.assertEqual(self.labels("video_format"),("talking_head",))

    def test_semantic_story_roles_dedup(self):
        self.assertEqual(self.labels("story_role"),("context","hook"))

    def test_editing_and_subtitles(self):
        self.assertEqual(self.labels("editing_type"),("fast_cuts",))
        self.assertEqual(self.labels("subtitle_type"),("dynamic",))

    def test_cta_types_dedup(self):
        self.assertEqual(self.labels("cta_type"),("follow","like"))

    def test_b8_topic_namespaced_not_d1(self):
        self.assertEqual(self.labels("topic_id"),("b_topic:42",))

    def test_zero_cta_requires_completed_analysis(self):
        row=dict(self.row,cta_types=[])
        self.assertEqual(self.labels("cta_type",data=row),("none_observed",))
        for state in ("not_run","partial","failed"):
            self.assertEqual(self.labels("cta_type",data=dict(
                row,cta_status=state)),())

    def test_unknown_labels_are_empty_not_false_none(self):
        self.assertEqual(self.labels("hook_mechanism",data=dict(
            self.row,hook_status="unknown")),())
        self.assertEqual(self.labels("video_format",data=dict(
            self.row,format_status="unknown")),())
        self.assertEqual(self.labels("topic_id",data=dict(
            self.row,topic_id=None)),())

    def test_nonapplicable_carousel_editing_not_none_observed(self):
        self.assertEqual(self.labels("editing_type","photo_carousel"),())
        self.assertEqual(self.labels("subtitle_type","photo_carousel"),())
        self.assertEqual(self.labels("video_format","photo_carousel",
                                     dict(self.row,carousel_format="listicle")),
                         ("listicle",))

    def test_invalid_label_and_unknown_dimension_fail_closed(self):
        with self.assertRaises(AnalyticsBoundaryError):
            self.labels("hook_mechanism",data=dict(
                self.row,hook_mechanisms=["fake_hook"]))
        with self.assertRaises(AnalyticsBoundaryError):
            self.labels("story_role",data=dict(self.row,story_roles=["alien"]))
        with self.assertRaises(AnalyticsBoundaryError):
            self.labels("cta_type",data=dict(self.row,cta_types=["nope"]))
        with self.assertRaises(AnalyticsBoundaryError):
            self.labels("hook_mechanism",data=dict(self.row,hook_mechanisms="curiosity"))
        with self.assertRaises(AnalyticsBoundaryError):
            self.labels("made_up_dimension")

    def test_b_version_drift_is_not_a_bucket(self):
        with self.assertRaises(AnalyticsBoundaryError):
            self.labels("hook_mechanism",data=dict(
                self.row,hook_taxonomy="wrong"))
        with self.assertRaises(AnalyticsBoundaryError):
            self.labels("video_format",data=dict(
                self.row,format_taxonomy="wrong"))
        with self.assertRaises(AnalyticsBoundaryError):
            self.labels("cta_type",data=dict(
                self.row,cta_taxonomy="wrong"))

    def test_topic_review_or_merge_is_denied(self):
        for change in (
            {"topic_needs_review":True},
            {"topic_merged_into":44},
            {"category_merged_into":45},
            {"topic_taxonomy":"old"},
            {"topic_id":False},
        ):
            with self.assertRaises(AnalyticsBoundaryError):
                self.labels("topic_id",data=dict(self.row,**change))

    def test_unknown_not_counted_as_none(self):
        for dim in ("video_format","hook_mechanism"):
            self.assertEqual(self.labels(dim,data=dict(
                self.row,format_status="unknown",
                hook_status="unknown")),())


class AuthorizedSegmentedReader(unittest.TestCase):
    def setUp(self):
        self.pins,self.db=fixture()
        self.reader=seg.TypedSegmentationReader(self.db)

    def run_dim(self,dim="cta_type",min_sample=5):
        with approved_mock():
            return self.reader.summarize(self.pins,dim,min_sample=min_sample)

    def test_eight_dimensions_produce_only_typed_segments(self):
        want={
            "content_kind":("video",),
            "hook_mechanism":("curiosity","promise"),
            "video_format":("talking_head",),
            "story_role":("context","hook"),
            "editing_type":("fast_cuts",),
            "subtitle_type":("dynamic",),
            "cta_type":("follow","like"),
            "topic_id":("b_topic:42",),
        }
        for dim,labels in want.items():
            self.db.b_reads=0
            got=self.run_dim(dim)
            self.assertIsInstance(got,seg.TypedSegmentation)
            self.assertEqual(tuple(x.value for x in got.segments),labels)
            self.assertTrue(all(x.sample_size==5 for x in got.segments))
            self.assertTrue(all(x.breakout.median=="3" for x in got.segments))
            self.assertEqual(got.dimension,dim)
            self.assertFalse(got.causal_claims_allowed)
            self.assertEqual(self.db.b_reads,0 if dim=="content_kind" else 5)

    def test_repeated_cta_one_video_one_membership_per_category(self):
        for fact in self.db.b_rows.values():
            fact["cta_types"]=["follow","follow","follow","like","like"]
        result=self.run_dim()
        self.assertEqual([(v.value,v.sample_size) for v in result.segments],
                         [("follow",5),("like",5)])

    def test_rare_label_no_count_or_low_n_group_leak(self):
        pins,db=fixture(6)
        target=db.b_rows[pins[-1].candidate.group_b.b_analysis_id]
        target["cta_types"]=["follow","rare"]
        # Unsupported labels are denied, not silently leaked.
        with approved_mock():
            with self.assertRaises(AnalyticsBoundaryError):
                seg.TypedSegmentationReader(db).summarize(pins,"cta_type")
        target["cta_types"]=["follow","purchase"]
        with approved_mock():
            result=seg.TypedSegmentationReader(db).summarize(pins,"cta_type")
        self.assertEqual(tuple(x.value for x in result.segments),("follow","like"))
        self.assertFalse(hasattr(result,"suppressed_count"))
        self.assertNotIn("purchase",repr(result))

    def test_no_bucket_under_min_sample(self):
        for fact in self.db.b_rows.values():
            fact["cta_types"]=["follow"] if fact["video_id"]<3 else ["like"]
        self.assertIsNone(self.run_dim())

    def test_deny_one_hidden_member_returns_nothing(self):
        self.db.denied.add(self.pins[1].snapshot_id)
        self.assertIsNone(self.run_dim())
        self.assertEqual(self.db.auth_count,2)
        self.assertEqual(self.db.query_count,1)
        self.assertEqual(self.db.b_reads,1)

    def test_hidden_first_member_never_reads_b_or_group_c(self):
        self.db.denied.add(self.pins[0].snapshot_id)
        self.assertIsNone(self.run_dim())
        self.assertEqual(self.db.query_count,0)
        self.assertEqual(self.db.b_reads,0)

    def test_missing_b_analysis_denies_entire_result(self):
        self.db.b_rows.pop(self.pins[2].candidate.group_b.b_analysis_id)
        self.assertIsNone(self.run_dim("story_role"))

    def test_mismatched_b_source_or_id_denies_entire_result(self):
        self.db.b_rows[self.pins[2].candidate.group_b.b_analysis_id][
            "source_id"]="unknown.source"
        self.assertIsNone(self.run_dim("video_format"))

    def test_cross_version_strata_denied(self):
        self.pins[-1].candidate.group_a.analysis_version="A-v2"
        self.assertIsNone(self.run_dim("content_kind"))

    def test_score_unknown_blocks_every_segment(self):
        self.db.rows[self.pins[-1].snapshot_id]["breakout_score"]=None
        self.assertIsNone(self.run_dim("hook_mechanism"))

    def test_unexpected_b_labels_fail_closed(self):
        self.db.b_rows[self.pins[0].candidate.group_b.b_analysis_id][
            "cta_types"]=["sent_money"]
        with self.assertRaises(AnalyticsBoundaryError):
            self.run_dim()

    def test_empty_and_subthreshold_suppress_no_io(self):
        with approved_mock():
            for n in (0,1,2,3,4):
                self.assertIsNone(self.reader.summarize(
                    self.pins[:n],"content_kind"
                ))
        self.assertEqual(self.db.sql,[])

    def test_invalid_dimension_limit_duplicate_video_denied_pre_io(self):
        with approved_mock():
            for dim in ("b4_visual","B1","",None):
                with self.assertRaises(AnalyticsBoundaryError):
                    self.reader.summarize(self.pins,dim)
            for invalid in (0,1,True,51):
                with self.assertRaises(AnalyticsBoundaryError):
                    self.reader.summarize(self.pins,"content_kind",
                                          min_sample=invalid)
            dupe=self.pins[0]
            with self.assertRaises(AnalyticsBoundaryError):
                self.reader.summarize((dupe,dupe)+self.pins[2:],
                                      "content_kind")
        self.assertEqual(self.db.sql,[])

    def test_nonidle_connection_refused(self):
        self.db.info.transaction_status=2
        with approved_mock():
            with self.assertRaises(AnalyticsBoundaryError):
                self.reader.summarize(self.pins,"content_kind")

    def test_query_does_not_leak_raw_text(self):
        q=seg.GROUP_B_DIMENSIONS_SQL
        for f in ("group_b_runs run","content_b_analyses a",
                  "run.project_id=%s","run.id=%s::uuid",
                  "run.result_analysis_id=%s","a.needs_review IS FALSE",
                  "content_b_hooks","content_b_formats","content_b_story_frames",
                  "content_b_story_intervals","content_b_story_slides",
                  "content_b_ctas","content_b_topic_assignments"):
            self.assertIn(f,q)
        for forbidden in ("screen_text", "spoken_text", "verbatim_text",
                          "local_path", "prompt", "raw_payload", "UPDATE "):
            self.assertNotIn(forbidden,q)
        self.assertIn("GROUP_B_DIMENSIONS_SQL",seg.__all__)

    def test_same_transaction_visibility_precedes_b_sql(self):
        self.run_dim("hook_mechanism")
        self.assertGreaterEqual(self.db.auth_count,5)
        self.assertEqual(self.db.query_count,5)
        self.assertEqual(self.db.b_reads,5)


if __name__=="__main__":
    unittest.main()
