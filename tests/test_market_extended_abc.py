"""Offline behavioral checks of copied A/B/C integrations; no paid I/O."""
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from intelligence.market_intelligence.extended_c import enrich_exact_c
from intelligence.market_intelligence.extended_b import assemble_b1_b8
from intelligence.market_intelligence._copied.group_b.blocks.b1_hook import MECHANISMS
from intelligence.market_intelligence._copied.group_b.blocks.b2_format import VIDEO_FORMATS
from intelligence.market_intelligence._copied.group_b.blocks.b3_story import ROLES
from intelligence.market_intelligence._copied.group_b.blocks.b7_cta import CTA_TYPES


class ExtendedABCRegression(unittest.TestCase):
    def test_group_c_c4_c5_c6_c7_real_math_but_no_fake_approval(self):
        published=datetime(2026,1,1,tzinfo=timezone.utc)
        observed=published+timedelta(hours=24)
        rows=[];hist={}
        for i in range(1,13):
            rows.append({
               "video_id":i,"platform":"tiktok",
               "creator_id":"creator_a" if i<=4 else "creator_b" if i<=8 else "creator_c",
               "published_at":published.isoformat(),
               "publication_age_hours":24.0,
               "metric_snapshot_id":i,"metric_source_id":"provider",
               "metric_definition":"platform_post_views",
               "metric_version":"v1",
               "creator_ratio":1.5,"views_growth_per_hour":100.0
            })
            hist[i]=[{"views":1000+i*10,
                      "likes":90,"comments":12,"shares":5,
                      "observed_at":observed.isoformat()}]
        result=enrich_exact_c(rows,hist)
        self.assertEqual(len(result),12)
        c=result[0]["group_c"]
        self.assertEqual(c["c0"],"eligible_latest_snapshot")
        self.assertEqual(c["c4"]["state"],"exact_unapproved")
        self.assertIsNotNone(c["c6_freshness"])
        self.assertEqual(c["c5"]["state"],"observed_peer_prior_unapproved")
        self.assertEqual(c["c7_breakout_preview"]["state"],
                         "diagnostic_unapproved_not_persistable")
        self.assertFalse(c["original_full_c_certified"])

    def test_group_c_missing_history_never_becomes_zero(self):
        row={"video_id":7,"platform":"youtube",
             "creator_id":"someone","metric_snapshot_id":None,
             "creator_ratio":None,"views_growth_per_hour":None}
        c=enrich_exact_c([row],{})[0]["group_c"]
        self.assertIsNone(c["c4"])
        self.assertIsNone(c["c5"])
        self.assertIsNone(c["c7_breakout_preview"])
        self.assertFalse(c["original_full_c_certified"])

    def test_group_b_all_eight_explicit_states(self):
        visual={"frames":[{"ref":"real_ref"}],
                "group_a_subtitles":{"has_subtitles":"true",
                                     "proof_kind":"embedded_subtitle_packets",
                                     "observation_sha256":"a"*64}}
        labels={"state":"model_observed_partial",
                "evidence_frame_refs":["real_ref"],
                "hook_mechanisms":[MECHANISMS[0]],"hook_text":"hello",
                "hook_visual_description":"a person",
                "format_family":VIDEO_FORMATS[0],
                "story_roles":[ROLES[0]],
                "cta_type":CTA_TYPES[0],"cta_text":None}
        extra={"state":"model_observed_partial",
               "person_visible":"present",
               "product_visible":"unknown",
               "logo_visible":"unknown",
               "topic_hint":"face exercises",
               "evidence_frame_refs":["real_ref"]}
        measurements=SimpleNamespace(measurement=SimpleNamespace(
            scenes=(),duration_sec=10))
        output=assemble_b1_b8(visual=visual,inferred=labels,
            measurements=measurements,transcript=[],extras=extra)
        self.assertEqual(set(output["blocks"]),{"B"+str(i) for i in range(1,9)})
        self.assertEqual(output["blocks"]["B4"]["visual_features"]["person_visible"],"present")
        self.assertEqual(output["blocks"]["B6"]["embedded_subtitle_present"],True)
        self.assertIsNone(output["blocks"]["B5"]["certified_cut_count"])
        self.assertIsNone(output["blocks"]["B8"]["approved_topic_id"])
        self.assertEqual(output["complete_original_blocks"],[])

    def test_group_b_forged_frame_ref_is_rejected(self):
        with self.assertRaises(ValueError):
            assemble_b1_b8(
                visual={"frames":[{"ref":"real"}]},
                inferred={"state":"model_observed_partial",
                          "evidence_frame_refs":["forged"]},
                measurements=SimpleNamespace(measurement=SimpleNamespace(
                    scenes=(),duration_sec=12)),transcript=[])


if __name__=="__main__":
    unittest.main()
