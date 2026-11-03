"""Offline contract tests for carousel cohorts, account contribution and ER."""
from __future__ import annotations

import json
import unittest

from intelligence.market_intelligence import carousel_studio_analytics as a


def post(native,slides,views,*,likes=None,comments=None,shares=None,saves=None,
         state="completed",with_manifest=True):
    metrics={"observed_counters":{
        "views":views,"likes":likes,"comments":comments,"shares":shares,"saves":saves}}
    manifest={"slides":[{"slide_index":n} for n in range(1,slides+1)]} if with_manifest else {}
    return {
        "native_id":str(native),"canonical_url":"https://www.tiktok.com/@test/photo/"+str(native),
        "state":state,"metrics_json":json.dumps(metrics),
        "analysis_json":json.dumps({"overall_story_summary":"Тестовый сценарий"}),
        "manifest_json":json.dumps(manifest),"asset_count":slides,
    }


def channel(native,kind,views,likes=None,comments=None,shares=None,saves=None):
    return {
        "native_id":str(native),"kind":kind,
        "counts_json":json.dumps({"observed_counters":{
            "views":views,"likes":likes,"comments":comments,
            "shares":shares,"saves":saves}})
    }


class TestCohorts(unittest.TestCase):
    def test_three_main_groups_9plus_and_unknown(self):
        slides=[2,3,4,5,6,7,8,9,12]
        items=[post(i+100,slides[i],(i+1)*100) for i in range(len(slides))]
        items.append(post(200,8,2000,with_manifest=False))
        result=a.build(items,[] ,source_kind="post")
        groups={g["id"]:g for g in result["groups"]}
        self.assertEqual([groups[x]["metrics"]["post_count"]
                         for x in ("1-2","3-5","6-8","9+")],[1,3,3,2])
        self.assertEqual(result["carousel_classification"]["with_verified_slide_count"],9)
        self.assertEqual(result["carousel_classification"]["without_verified_slide_count"],1)
        self.assertAlmostEqual(sum(g["share_of_verified_carousels_pct"]
                                    for g in groups.values()),100.0,places=2)
        self.assertIsNone(groups["6-8"]["share_of_observed_channel_views_pct"])
        self.assertIsNone(result["best_relative_impact_group"])
        self.assertEqual(result["carousel_classification"]["unclassified_ids"],["200"])

    def test_account_includes_video_denominator_and_index(self):
        # 6-8 slides are 1/4 of all returned posts, but 500/1000 views.
        items=[
            post("111",2,100,likes=10,comments=0,shares=0,saves=0),
            post("222",6,500,likes=50,comments=10,shares=10,saves=30),
        ]
        all_posts=[
            channel("111","photo_carousel",100,10,0,0,0),
            channel("222","photo_carousel",500,50,10,10,30),
            channel("333","video",300,30,0,0,0),
            channel("444","video",100,10,0,0,0),
        ]
        result=a.build(items,all_posts,source_kind="profile")
        groups={g["id"]:g for g in result["groups"]}
        big=groups["6-8"]
        self.assertEqual(result["channel"]["all_posts"]["post_count"],4)
        self.assertEqual(result["channel"]["ordinary_videos"]["observed"]["views"],400)
        self.assertEqual(result["channel"]["photo_posts"]["observed"]["views"],600)
        self.assertEqual(big["share_of_verified_carousels_pct"],50)
        self.assertEqual(big["share_of_observed_channel_posts_pct"],25)
        self.assertEqual(big["share_of_observed_channel_views_pct"],50)
        self.assertEqual(big["view_impact_index"],2)
        self.assertEqual(big["share_of_observed_channel_actions_pct"],66.667)
        self.assertEqual(big["er_index_vs_channel"],1.333)
        self.assertEqual(big["metrics"]["weighted_er_by_views_pct"],20)
        self.assertEqual(big["metrics"]["median_post_er_pct"],20)
        self.assertEqual(result["best_relative_impact_group"],"6-8")

    def test_weighted_er_differs_from_mean_er(self):
        items=[
            post("1",7,100,likes=50,comments=0,shares=0,saves=0),
            post("2",8,900,likes=45,comments=0,shares=0,saves=0)]
        p=[channel("1","photo_carousel",100,50,0,0,0),
           channel("2","photo_carousel",900,45,0,0,0)]
        result=a.build(items,p,source_kind="profile")
        stats=next(g for g in result["groups"] if g["id"]=="6-8")["metrics"]
        self.assertEqual(stats["mean_post_er_pct"],27.5)
        self.assertEqual(stats["median_post_er_pct"],27.5)
        self.assertEqual(stats["weighted_er_by_views_pct"],9.5)

    def test_missing_reactions_do_not_make_zero_or_fake_er(self):
        items=[post("1",4,100,likes=10,comments=None,shares=0,saves=0),
               post("2",5,0,likes=0,comments=0,shares=0,saves=0)]
        res=a.build(items,[],source_kind="post")
        g=next(g for g in res["groups"] if g["id"]=="3-5")
        self.assertIsNone(g["metrics"]["weighted_er_by_views_pct"])
        self.assertIsNone(g["metrics"]["mean_post_er_pct"])
        self.assertEqual(g["metrics"]["er_eligible_posts"],0)
        self.assertEqual(g["metrics"]["observed"]["views"],100)

    def test_profile_snapshot_not_mixed_with_later_post_metrics(self):
        # The refreshed post-detail data is 10000 views, but channel snapshot 100.
        items=[post("1",6,10000,likes=999,comments=0,shares=0,saves=0)]
        all_posts=[channel("1","photo_carousel",100,10,0,0,0),
                   channel("2","video",900,0,0,0,0)]
        res=a.build(items,all_posts,source_kind="profile")
        group=next(g for g in res["groups"] if g["id"]=="6-8")
        self.assertEqual(group["metrics"]["observed"]["views"],100)
        self.assertEqual(group["share_of_observed_channel_views_pct"],10)
        self.assertEqual(group["batches"][0]["posts"][0]["views"],100)

    def test_ranked_batches_and_unknown_views_last(self):
        items=[post(i,6,(100-i)*50,likes=0,comments=0,shares=0,saves=0)
               for i in range(1,24)]
        items.append(post(500,7,None))
        result=a.build(items,[],source_kind="post",batch_size=10)
        group=next(g for g in result["groups"] if g["id"]=="6-8")
        self.assertEqual([len(b["posts"]) for b in group["batches"]],[10,10,4])
        self.assertEqual(group["batches"][0]["start_rank"],1)
        self.assertEqual(group["batches"][0]["end_rank"],10)
        self.assertEqual(group["batches"][0]["posts"][0]["native_id"],"1")
        self.assertEqual(group["batches"][-1]["posts"][-1]["native_id"],"500")
        self.assertIsNone(group["batches"][-1]["posts"][-1]["views"])

    def test_failed_manifest_not_classified_from_claimed_asset_count(self):
        item=post("1",7,100,with_manifest=False,state="failed")
        result=a.build([item],[],source_kind="post")
        self.assertEqual(result["carousel_classification"]["with_verified_slide_count"],0)
        self.assertEqual(result["carousel_classification"]["without_verified_slide_count"],1)
        self.assertEqual(result["groups"][2]["metrics"]["post_count"],0)

    def test_group_impact_only_over_known_provider_data(self):
        items=[post("1",6,100,likes=5,comments=0,shares=0,saves=0)]
        channel_rows=[channel("1","photo_carousel",100,5,0,0,0),
                      channel("2","video",None)]
        result=a.build(items,channel_rows,source_kind="profile")
        self.assertEqual(result["channel"]["known_views_coverage_pct"],50)
        self.assertIsNone(result["groups"][2]["view_impact_index"])
        self.assertEqual(result["groups"][2]["share_of_observed_channel_views_pct"],100)
        self.assertEqual(result["scope"],"provider_returned_publications_only")

    def test_descriptive_flags_not_causal_claims(self):
        items=[post(i,6,views,likes=0,comments=0,shares=0,saves=0)
               for i,views in enumerate([1000,1000,1000,1000,10],start=10)]
        result=a.build(items,[],source_kind="post")
        group=next(g for g in result["groups"] if g["id"]=="6-8")
        self.assertEqual(group["relative_signals"][0]["post_count"],1)
        self.assertEqual(group["relative_signals"][0]["type"],"below_group_median_views")
        self.assertTrue(any("не" in note and "причин" in note for note in result["interpretation_notes"]))

    def test_creative_aggregation_never_treats_missing_gemini_fields_as_false(self):
        first=post("1",6,100,likes=10,comments=0,shares=0,saves=0)
        first["analysis_json"]=json.dumps({
            "global_hook":{"present":True},"global_cta":{"present":False},
            "product_integration":{"present":True},
            "story_pattern":"before-after -> tips -> app"})
        second=post("2",7,150,likes=4,comments=0,shares=0,saves=0)
        second["analysis_json"]=json.dumps({
            "overall_story_summary":"Нет отдельных структурных полей"})
        result=a.build([first,second],[],source_kind="post")
        group=next(g for g in result["groups"] if g["id"]=="6-8")
        self.assertEqual(group["creative"]["gemini_analyzed"],2)
        self.assertEqual(group["creative"]["hook"]["evaluated"],1)
        self.assertEqual(group["creative"]["hook"]["present_pct"],100)
        self.assertEqual(group["creative"]["cta"]["present_pct"],0)
        self.assertEqual(group["creative"]["product_integration"]["present"],1)
        self.assertEqual(group["creative"]["most_common_narrative_patterns"][0]["count"],1)

    def test_duplicate_channel_rows_are_rejected(self):
        with self.assertRaisesRegex(ValueError,"duplicate"):
            a.build([], [channel("1","video",30),channel("1","video",30)],
                    source_kind="profile")

    def test_bad_batch_size_and_source(self):
        with self.assertRaises(ValueError):
            a.build([],[],source_kind="invalid")
        with self.assertRaises(ValueError):
            a.build([],[],source_kind="post",batch_size=0)


if __name__=="__main__":
    unittest.main()
