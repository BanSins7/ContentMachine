"""Offline tests: zero network, model or Bright Data charges."""
import unittest
from datetime import date
from intelligence.instagram_top100.core import select_reels, username, usernames


def post(code, views, published="2026-11-10", *, author="demo.account", kind="reel", **rest):
    return {"url": "https://www.instagram.com/" + kind + "/" + code + "/",
            "user_posted": author, "views": views, "date_posted": published, **rest}


class InstagramTop100Tests(unittest.TestCase):
    def test_handles(self):
        self.assertEqual(username("@Demo.Account"), "demo.account")
        self.assertEqual(username("https://www.instagram.com/demo.account/"), "demo.account")
        self.assertEqual(usernames("@a @b,https://instagram.com/A/"), ["a", "b"])

    def test_unsafe_username(self):
        for raw in ("../bad", "https://evil.com/a", "@explore", "@a/b", "a..b"):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                username(raw)

    def test_views_are_the_only_rank_metric(self):
        records = [post("ABCDE01", 20), post("ABCDE02", 100),
                   post("ABCDE03", 999, "2026-10-31"),
                   post("ABCDE04", 0), post("ABCDE05", None)]
        result = select_reels("demo.account", records, date(2026, 11, 1), date(2026, 11, 13))
        self.assertEqual([x["shortcode"] for x in result["posts"]],
                         ["ABCDE02", "ABCDE01", "ABCDE04"])
        self.assertEqual(result["rejected"]["outside_period"], 1)
        self.assertEqual(result["rejected"]["missing_views"], 1)
        self.assertFalse(result["coverage_complete"])

    def test_no_unverified_dates_or_authors(self):
        records = [post("ABCDE01", 100, None),
                   post("ABCDE02", 20, author="another"),
                   {"url":"https://evil.com/reel/ABCDE03/","user_posted":"demo.account",
                    "views":900,"date_posted":"2026-11-03"}]
        self.assertEqual(select_reels("demo.account", records, date(2026, 11, 1),
                                      date(2026, 11, 13))["selected"], 0)

    def test_photo_unsupported_unless_proven_video(self):
        records = [post("ABCDE01", 100, kind="p"),
                   post("ABCDE02", 80, kind="p", is_video=True)]
        result = select_reels("demo.account", records, date(2026, 11, 1), date(2026, 11, 13))
        self.assertEqual(result["posts"][0]["shortcode"], "ABCDE02")
        self.assertEqual(result["selected"], 1)

    def test_collaborator(self):
        record = post("ABCDE01", 44, author="primary")
        record["coauthor_producers"] = [{"username": "demo.account"}]
        result = select_reels("demo.account", [record], date(2026, 11, 1), date(2026, 11, 13))
        self.assertEqual(result["selected"], 1)

    def test_conflicting_duplicates_excluded(self):
        records = [post("ABCDE01", 100), post("ABCDE01", 120), post("ABCDE02", 5)]
        result = select_reels("demo.account", records, date(2026, 11, 1), date(2026, 11, 13))
        self.assertEqual(result["selected"], 1)
        self.assertEqual(result["posts"][0]["shortcode"], "ABCDE02")
        self.assertEqual(result["rejected"]["conflicting_duplicate"], 1)

    def test_limit_and_shortfall(self):
        records = [post("VIDEO%06d" % i, 1000-i) for i in range(130)]
        result = select_reels("demo.account", records, date(2026, 11, 1), date(2026, 11, 13))
        self.assertEqual(result["selected"], 100)
        self.assertEqual(result["verified_ranked"], 130)
        self.assertEqual(result["posts"][0]["views"], 1000)


if __name__ == "__main__":
    unittest.main()
