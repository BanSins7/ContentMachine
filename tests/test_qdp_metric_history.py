"""FIX-08 verified D1 metric histories; no score or invented observations."""
from __future__ import annotations
import unittest
from datetime import datetime, timedelta, timezone

from intelligence.qualified_data.metric_history import (
    MAX_OBSERVATIONS, read_metric_history, resolve_metric_history,
)

NOW = datetime(2026, 10, 26, 10, tzinfo=timezone.utc)


def metadata(**changes):
    data = {"video_id": 9, "source_id": "d1-real-source",
            "source_policy_version_id": 77,
            "published_at": NOW - timedelta(days=3),
            "can_store_metrics": True,
            "permissions": {"can_store_metrics": True}}
    data.update(changes)
    return data


def point(id: int, *, hours_before: float, views=100, **changes):
    p = {
        "id": id, "video_id": 9, "source_id": "d1-real-source",
        "source_policy_version_id": 77,
        "observed_at": NOW - timedelta(hours=hours_before),
        "expires_at": None, "observation_time_assumed": False,
        "data_quality_status": "ok",
        "precision": "exact", "views": views,
        "source_metric_definition": "platform_post_views",
        "source_metric_version": "v2",
        "likes": 3, "comments": None, "shares": None,
        "saves": None, "followers": None,
    }
    p.update(changes)
    return p


class HistoryTests(unittest.TestCase):
    def resolve(self, rows, **metachanges):
        return resolve_metric_history(rows, metadata(**metachanges), now=NOW)

    def test_0_and_1_points_never_become_growth(self):
        for rows, count in (([], 0), ([point(11, hours_before=1)], 1)):
            with self.subTest(count=count):
                got = self.resolve(rows)
                self.assertEqual(got["growth_pair_ids"], [])
                self.assertEqual(got["growth_reason_code"], "metric_history_insufficient")
                self.assertEqual(got["available_count"], count)

    def test_two_real_exact_points_chronological_and_no_breakout_score(self):
        older = point(101, hours_before=16, views=75)
        newer = point(102, hours_before=4, views=140)
        a = self.resolve([newer, older])
        b = self.resolve([older, newer])
        self.assertEqual(a["growth_pair_ids"], [101, 102])
        self.assertEqual(a["growth_definition"],
                         {"name":"platform_post_views","version":"v2"})
        self.assertEqual(a["metric_snapshot_ids"], [101, 102])
        self.assertEqual(a["history_sha256"], b["history_sha256"])
        self.assertEqual(a["observations"], b["observations"])
        self.assertNotIn("breakout_score", a)
        self.assertNotIn("views_growth_per_hour", a)

    def test_same_source_not_platform_generic(self):
        rows = [point(1, hours_before=4, views=80),
                point(2, hours_before=1, views=100, source_id="other-provider")]
        got = self.resolve(rows)
        self.assertEqual(got["metric_snapshot_ids"], [1])
        self.assertEqual(got["growth_pair_ids"], [])

    def test_policy_version_must_match(self):
        rows = [point(1, hours_before=4, views=80),
                point(2, hours_before=1, views=100,source_policy_version_id=78)]
        self.assertEqual(self.resolve(rows)["available_count"], 1)

    def test_definitions_and_versions_mismatch(self):
        base=point(1, hours_before=5, views=80)
        for patch in ({"source_metric_version":"v3"},
                      {"source_metric_definition":"total_plays"},
                      {"source_metric_definition":None},
                      {"source_metric_version":None}):
            with self.subTest(patch=patch):
                current=point(2,hours_before=1,views=110,**patch)
                got=self.resolve([base,current])
                self.assertEqual(got["growth_pair_ids"], [])
                self.assertEqual(got["growth_reason_code"],
                                 "metric_definition_incompatible")

    def test_null_views_are_unknown_never_zero(self):
        got = self.resolve([point(1,hours_before=2,views=0),
                            point(2,hours_before=1,views=None)])
        self.assertEqual(got["counts"]["exact_view_count"],1)
        self.assertIsNone(got["observations"][-1]["views"])
        self.assertEqual(got["growth_pair_ids"], [])

    def test_non_exact_and_assumed_timestamp_do_not_qualify(self):
        first=point(1,hours_before=4)
        for changes in ({"precision":"rounded"},
                        {"precision":"estimated"},
                        {"observation_time_assumed":True},
                        {"data_quality_status":"partial"},
                        {"data_quality_status":"unverified"},
                        {"observed_at":NOW+timedelta(hours=1)},
                        {"expires_at":NOW-timedelta(minutes=1)}):
            with self.subTest(changes=changes):
                last=point(2,hours_before=1,**changes)
                self.assertEqual(self.resolve([first,last])["growth_pair_ids"],[])

    def test_latest_retracted_or_corrected_cannot_fall_back(self):
        older=point(1,hours_before=10,views=70)
        middle=point(2,hours_before=4,views=90)
        for state in ("counter_correction","retracted"):
            with self.subTest(state=state):
                recent=point(3,hours_before=1,views=75,
                             data_quality_status=state)
                got=self.resolve([older,middle,recent])
                self.assertEqual(got["growth_pair_ids"], [])
                self.assertEqual(got["growth_reason_code"],
                                 "metric_observation_revoked_or_corrected")
                self.assertEqual(got["metric_snapshot_ids"], [1,2])

    def test_counter_drop_and_duplicate_timestamps_block(self):
        older=point(1,hours_before=5,views=120)
        for newer, reason in (
            (point(2,hours_before=1,views=99),"metric_counter_correction"),
            (point(2,hours_before=5,views=155),"metric_timestamp_incompatible"),
        ):
            got=self.resolve([older,newer])
            self.assertEqual(got["growth_reason_code"], reason)

    def test_unknown_and_inconsistent_published_at_not_assumed(self):
        older=point(1,hours_before=10,views=100)
        newer=point(2,hours_before=2,views=120)
        self.assertEqual(self.resolve([older,newer],published_at=None)
                         ["growth_reason_code"],"published_at_unknown")
        self.assertEqual(self.resolve([older,newer],
                        published_at=NOW-timedelta(hours=4))
                         ["growth_reason_code"],"metric_publication_time_invalid")

    def test_rights_disabled_performs_no_query(self):
        class DB:
            def execute(self, *_):
                raise AssertionError("No D1 read with missing storage grant")
        got=read_metric_history(DB(),metadata(can_store_metrics=False))
        self.assertEqual(got["growth_pair_ids"], [])
        got=read_metric_history(DB(),metadata(permissions={}))
        self.assertEqual(got["available_count"],0)

    def test_db_query_exact_source_policy_and_bounded(self):
        class DB:
            def execute(self,sql,args):
                self.sql,self.args=sql,args
                class Cursor:
                    def fetchall(self_inner):
                        return []
                return Cursor()
        db=DB()
        read_metric_history(db,metadata())
        self.assertIn("LIMIT 64",db.sql)
        self.assertIn("source_policy_version_id=%s",db.sql)
        self.assertEqual(db.args,(9,"d1-real-source",77))

    def test_batch_limit_hard_fails_without_partial_selection(self):
        with self.assertRaises(ValueError):
            self.resolve([point(i+1,hours_before=i+1) for i in
                          range(MAX_OBSERVATIONS+1)])

    def test_only_eligible_snapshots_serialized(self):
        got=self.resolve([point(1,hours_before=2,views=33),
                          point(2,hours_before=1,views=44,
                                data_quality_status="retracted")])
        self.assertEqual(len(got["observations"]),1)
        self.assertEqual(got["observations"][0]["id"],1)
        self.assertNotIn(2,got["metric_snapshot_ids"])


if __name__ == "__main__":
    unittest.main()
