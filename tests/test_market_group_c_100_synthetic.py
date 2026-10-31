"""100-video no-network stress regression for actual MI Group C math.

Exactly 100 independent video IDs, two platforms, ten authors and
100 distinct canonical D1 source objects. No QDP and no fabricated retention.
Test verifies deterministic C0/C1/C3/C4/C5/C6/C7 and source-scoped receipts.
"""
from __future__ import annotations
from datetime import datetime,timedelta,timezone
from decimal import Decimal
import copy
import unittest
from intelligence.market_intelligence.performance import enrich
from intelligence.market_intelligence.extended_c import enrich_exact_c
from intelligence.market_intelligence.group_c_report import verify_report


class HundredVideoMath(unittest.TestCase):
    @staticmethod
    def fixture():
        observed=datetime(2026,10,1,12,tzinfo=timezone.utc)
        published=observed-timedelta(hours=30)
        earlier=observed-timedelta(hours=2)
        videos=[]
        histories={}
        for index in range(100):
            video_id=index+1
            views=2000+index*17
            platform="tiktok" if index<50 else "youtube"
            author=str(index//10)
            src=f"distinct-publication-source-{video_id}"
            videos.append({
                "video_id":video_id,
                "creator_id":author,"platform":platform,
                "published_at":published.isoformat()})
            meta={
                "video_id":video_id,"source_id":src,
                "source_policy_version_id":100,
                "metric_provider":"brightdata",
                "source_metric_definition":"platform_post_views",
                "source_metric_version":"v1",
                "data_quality_status":"ok",
                "precision":"exact",
                "observation_time_assumed":False,
                "collected_at":(observed+timedelta(minutes=1)).isoformat(),
                "expires_at":None,"likes":round(views*.11),
                "comments":round(views*.013),"shares":round(views*.008)}
            histories[video_id]=[
                {**meta,"id":video_id*10,"views":views-230,
                 "observed_at":earlier.isoformat()},
                {**meta,"id":video_id*10+1,"views":views,
                 "observed_at":observed.isoformat()}]
        return videos,histories

    def _run(self):
        rows,history=self.fixture()
        # MI performance derives creator baseline and pairwise growth using
        # each video's original D1 source ID, but peers share the provider.
        return enrich_exact_c(enrich(rows,history),history,
                              raw_histories=history,verify_math=True)

    def test_all_100_are_unique_exact_and_sealed(self):
        result=self._run()
        self.assertEqual(len(result),100)
        self.assertEqual(len({r["video_id"] for r in result}),100)
        self.assertEqual({r["platform"] for r in result},{"tiktok","youtube"})
        for row in result:
            c=row["group_c"]
            with self.subTest(video=row["video_id"]):
                self.assertEqual(c["c0"],"eligible_latest_snapshot")
                self.assertIsNotNone(c["c1_creator_ratio"])
                self.assertIsNotNone(c["c3_views_growth_per_hour"])
                self.assertIsNotNone(c["c4"])
                self.assertIsNotNone(c["c5"])
                self.assertIsNotNone(c["c6_freshness"])
                self.assertIsNotNone(c["c7_breakout_preview"])
                self.assertTrue(verify_report(c["native_report"],c))
                self.assertFalse(c["original_full_c_certified"])
                self.assertEqual(c["c7_breakout_preview"]["state"],
                    "diagnostic_unapproved_not_persistable")
        self.assertTrue(all(r["group_c"]["c2_niche_outlier"]["state"]
                        =="unavailable" for r in result))

    def test_100_video_outcomes_repeat_exactly(self):
        first=self._run()
        second=self._run()
        self.assertEqual(
            [r["group_c"]["native_report"]["report_sha256"] for r in first],
            [r["group_c"]["native_report"]["report_sha256"] for r in second])

    def test_one_bad_metric_invalidates_only_its_video(self):
        videos,histories=self.fixture()
        histories[44][-1]["precision"]="approximate"
        rows=enrich_exact_c(enrich(videos,histories),histories,
                            raw_histories=histories,verify_math=False)
        c=next(row["group_c"] for row in rows if row["video_id"]==44)
        self.assertEqual(c["c0"],"unavailable")
        self.assertIsNone(c["c7_breakout_preview"])
        good=next(row["group_c"] for row in rows if row["video_id"]==1)
        self.assertEqual(good["c0"],"eligible_latest_snapshot")


if __name__=="__main__":
    unittest.main()
