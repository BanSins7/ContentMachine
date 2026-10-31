"""All Bright Data numeric post metrics: bounded, safe, source-accurate."""
import math
import unittest

from intelligence.market_intelligence.metric_fields import numeric_fields, metric_coverage


class NumericBrightDataFields(unittest.TestCase):
    def test_keeps_all_numeric_post_measures(self):
        sample = {
            "post_id": 123456789123456789,
            "play_count": 1234,
            "likes": 90,
            "comment_count": 13,
            "shares": 4,
            "statistics": {"average_watch_time": 15.3, "completion_rate": 0.72, "saves": 2, "retention_curve": [95.0, 70.5, 28.0]},
            "description": "private user data should not be archived",
            "comments": [{"text": "not allowed", "likes": 99}],
            "author": {"id": "1234", "followers": 3001, "username": "not-collected"},
        }
        got = numeric_fields(sample)
        self.assertEqual(got["play_count"],1234)
        self.assertEqual(got["statistics.average_watch_time"],15.3)
        self.assertEqual(got["statistics.completion_rate"],.72)
        self.assertEqual(got["statistics.retention_curve"],[95.0,70.5,28.0])
        self.assertEqual(got["author.followers"],3001)
        self.assertNotIn("post_id",got)
        self.assertNotIn("comments",got)
        self.assertNotIn("author.username",got)
        self.assertTrue(metric_coverage(got)["has_retention_field"])
        self.assertTrue(metric_coverage(got)["has_watch_time_field"])

    def test_never_invents_retention(self):
        values=numeric_fields({"play_count":99,"comment_count":3})
        self.assertFalse(metric_coverage(values)["has_retention_field"])
        self.assertFalse(metric_coverage(values)["has_watch_time_field"])

    def test_drops_nonfinite_identifiers_and_unbounded_lists(self):
        data={"likes":True,"views":float("inf"),"rate":float("nan"),
              "user_id":3,"timestamp":200,"width":1080,"comments":[{"like_count":5}],
              "something":{"reposts":6}}
        self.assertEqual(numeric_fields(data),{"something.reposts":6})

    def test_bounded(self):
        self.assertEqual(len(numeric_fields({f"x{i}":i for i in range(800)})),800)
        with self.assertRaises(ValueError):
            numeric_fields({f"x{i}":i for i in range(2200)})

if __name__=="__main__":
    unittest.main()
