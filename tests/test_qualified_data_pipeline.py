"""Offline QDP contract/security tests; no network, OpenRouter, or PostgreSQL."""
import unittest
from datetime import datetime, timedelta, timezone

from intelligence.qualified_data.contracts import (
    QDPError, options, idempotency_key, qualify_sre_rows,
    analysis_readiness, freshness_reason, sha256,
)


class QualifiedSetTests(unittest.TestCase):
    def test_duplicate_category_matches_collapse_one_video(self):
        rows = [
            dict(video_id=5,result_id=10,category_key="a",
                 effective_decision="match",content_readable=True),
            dict(video_id=5,result_id=11,category_key="b",
                 effective_decision="match",content_readable=True),
            dict(video_id=8,result_id=12,category_key="a",
                 effective_decision="uncertain",content_readable=True),
            dict(video_id=9,result_id=13,category_key="a",
                 effective_decision="no_match",content_readable=True),
        ]
        got = qualify_sre_rows(rows)
        self.assertEqual(got["totals"],{"qualified":1,"needs_review":1,"excluded":1})
        self.assertEqual(got["qualified"],[
            {"video_id":5,"category_keys":["a","b"],"result_ids":[10,11]}])

    def test_match_without_read_rights_is_not_qualified(self):
        data = qualify_sre_rows([dict(video_id=1,result_id=1,
          category_key="a",effective_decision="match",content_readable=False)])
        self.assertEqual(data["qualified"],[])
        self.assertEqual(data["needs_review"],[1])

    def test_human_no_match_override_prevents_positive(self):
        data = qualify_sre_rows([dict(video_id=1,result_id=1,
          category_key="a",effective_decision="no_match",content_readable=True)])
        self.assertEqual(data["excluded"],[1])

    def test_not_evaluated_does_not_promote(self):
        self.assertEqual(qualify_sre_rows([dict(video_id=4,result_id=1,
          category_key="a",effective_decision="not_evaluated",
          content_readable=False)])["needs_review"],[4])

    def test_invalid_verdict_rejected(self):
        with self.assertRaises(QDPError):
            qualify_sre_rows([dict(video_id=3,category_key="a",
                effective_decision="approved",content_readable=True)])


class EnrichmentAndPreparationTests(unittest.TestCase):
    def test_options_reject_untrusted_provider_toggles(self):
        for raw in ({"fetch_tiktok":True},{"max_age_days":0},{"max_age_days":366},
                    {"max_age_days":True},{"require_published_at":"yes"},[]):
            with self.subTest(raw=raw),self.assertRaises(QDPError):
                options(raw)
        self.assertEqual(options(),{"max_age_days":None,"require_published_at":False})

    def test_freshness_requires_evidence_not_assumptions(self):
        now=datetime(2026,10,23,tzinfo=timezone.utc)
        self.assertIsNone(freshness_reason(None,options(),now=now))
        self.assertEqual(freshness_reason(None,options({"max_age_days":30}),now=now),
                         "published_at_unknown")
        self.assertEqual(freshness_reason(now-timedelta(days=31),
              options({"max_age_days":30}),now=now),"published_at_too_old")
        self.assertEqual(freshness_reason(now+timedelta(seconds=1),
              options(),now=now),"published_at_future")
        self.assertIsNone(freshness_reason(now-timedelta(days=1),
              options({"max_age_days":30}),now=now))

    def test_all_groups_blocked_by_default(self):
        result=analysis_readiness(media_asset_id=None,media_allowed=False,
               metric_snapshot_ids=[],metric_definitions=[],
               scoring_allowed=False,active_score_config_id=None)
        self.assertTrue(all(r["state"]=="blocked" for r in result.values()))
        self.assertEqual(result["C"]["reason_code"],"score_rights_missing")

    def test_c_requires_comparable_history_and_approved_configuration(self):
        params=dict(media_asset_id=20,media_allowed=True,
            metric_snapshot_ids=[3,4],metric_definitions=["views_v1","views_v1"],
            scoring_allowed=True,active_score_config_id=9)
        result=analysis_readiness(**params)
        self.assertTrue(all(x["state"]=="ready" for x in result.values()))
        params["metric_definitions"]=["views_v1","reach_v1"]
        self.assertEqual(analysis_readiness(**params)["C"]["reason_code"],
                         "metric_definition_incompatible")
        params["active_score_config_id"]=None
        self.assertEqual(analysis_readiness(**params)["C"]["reason_code"],
                         "score_configuration_missing")

    def test_stable_fingerprint_and_idempotency(self):
        self.assertEqual(sha256({"a":1,"b":2}),sha256({"b":2,"a":1}))
        self.assertEqual(idempotency_key("qdp:test:123456"),"qdp:test:123456")
        with self.assertRaises(QDPError):
            idempotency_key("short")
        with self.assertRaises(QDPError):
            idempotency_key("a"*129)


if __name__=="__main__":
    unittest.main()
