"""Offline contract tests; no keys, DB or external services."""
import unittest

from intelligence.market_intelligence.contracts import (
    MarketError, validate_seeds, summarize_market, verified_rtb_context, digest
)
from intelligence.taxonomy_builder.market_intelligence_v3 import validate_format_result


class MarketContractTests(unittest.TestCase):
    def test_urls_are_normalized_and_deduplicated(self):
        seeds = validate_seeds(
            ["https://www.tiktok.com/@hello/video/1234567890123456789",
             "https://www.tiktok.com/@hello/video/1234567890123456789?utm_source=test"],
            ["https://www.tiktok.com/@hello"])
        self.assertEqual(len(seeds["videos"]), 1)
        self.assertEqual(len(seeds["authors"]), 1)
        self.assertEqual(seeds["videos"][0]["native_id"], "1234567890123456789")

    def test_rejects_arbitrary_hosts_and_account_in_video_slot(self):
        with self.assertRaises(MarketError):
            validate_seeds(["https://example.com/fake"], [])
        with self.assertRaises(MarketError):
            validate_seeds(["https://www.tiktok.com/@hello"], [])
        with self.assertRaises(MarketError):
            validate_seeds([], [])

    def test_no_evidence_never_invents_market_success(self):
        seeds = validate_seeds(["https://www.tiktok.com/@hello/video/1234567890123456789"], [])
        report = summarize_market("a"*32, seeds, [])
        self.assertEqual(report["status"], "no_discovery_data")
        self.assertEqual(report["format_families"], [])
        self.assertFalse(report["market_hypotheses_verified"])
        self.assertEqual(report["provider_requests"], 0)
        self.assertEqual(report["llm_requests"], 0)

    def test_coarse_group_with_real_metrics(self):
        seeds = validate_seeds(["https://www.tiktok.com/@hello/video/1234567890123456789"], [])
        rows = []
        for i in range(1, 7):
            rows.append(dict(video_id=i, url="https://tiktok.com/example",
                 platform="tiktok", creator_id=i%3+1, published_at=None,
                 group_b_analysis_id=i, format_family="demonstration",
                 metric_snapshot_id=i, metric_observed_at="2026-10-30T00:00:00+00:00",
                 views=1000*i, creator_ratio=2.0))
        report = summarize_market("a"*32, seeds, rows)
        fmt = report["format_families"][0]
        self.assertEqual(fmt["readiness"], "preliminary_observed")
        self.assertEqual(fmt["independent_creators"], 3)
        self.assertEqual(fmt["median_creator_ratio"], 2.0)
        self.assertEqual(fmt["trend_stage"], "unknown_insufficient_time_series")
        context = verified_rtb_context(report)
        self.assertEqual(context["market_snapshot_sha256"], digest(report))
        self.assertEqual(len(context["video_evidence"]), 6)

    def test_metric_absence_never_keeps_ratio_or_false_views(self):
        seeds = validate_seeds([], ["https://www.tiktok.com/@hello"])
        row = dict(video_id=1, url="https://www.tiktok.com/@hello/video/1234567890123456789",
                   platform="tiktok", creator_id=1,
                   metric_snapshot_id=None, views=999999, creator_ratio=900,
                   group_b_analysis_id=None, format_family="demonstration")
        report = summarize_market("a"*32, seeds, [row])
        item = report["video_evidence"][0]
        self.assertIsNone(item["views"])
        self.assertIsNone(item["creator_ratio"])
        self.assertIsNone(item["format_family"])

    def test_opus_cannot_invent_format_or_video_ids(self):
        seeds = validate_seeds([], ["https://www.tiktok.com/@hello"])
        obs = [dict(video_id=i, url="x", platform="tiktok", creator_id=i%3,
                    metric_snapshot_id=i, views=i*100, creator_ratio=2.1,
                    group_b_analysis_id=i, format_family="demonstration") for i in range(1,7)]
        report = summarize_market("a"*32, seeds, obs)
        candidate = dict(formats=[dict(format_key="tiktok:demonstration",
            name_ru="Демонстрация", hook="Вопрос", sequence=["Вопрос", "Демонстрация"],
            visual_mechanics="Действие", product_adaptation="Показать продукт",
            hypothesis_to_test="Сравнить с обычными видео", evidence_video_ids=[1,2],
            uncertainty="Неизвестно, влияет ли формат на просмотры")],
            unresolved_questions=[])
        self.assertEqual(len(validate_format_result(candidate, report)["formats"]), 1)
        candidate["formats"][0]["evidence_video_ids"]=[888]
        with self.assertRaises(MarketError):
            validate_format_result(candidate, report)


if __name__ == "__main__":
    unittest.main()
