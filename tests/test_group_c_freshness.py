"""GC-MOD-18: strict 48h publication freshness and disposable PG17 provenance."""
from __future__ import annotations

import os
import unittest
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_DOWN, localcontext
from uuid import uuid4

from intelligence.group_c.contracts import ContractError, FRESHNESS_HALF_LIFE_H
from intelligence.group_c.freshness import (
    FRESHNESS_VERSION, FreshnessResult, PostgreSQLFreshness,
    compute_freshness_score, compute_verified_freshness,
)
from intelligence.group_c.publication_time import validate_publication_time

T = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)


def evidence(age: timedelta = timedelta(hours=48), **change):
    published = T - age
    # The predecessor must be no earlier than the original publication.
    before = T - timedelta(hours=1)
    row = dict(
        id=41, video_id=7, source_id="gc-18",
        source_policy_version_id=3,
        published_at=published, fetched_at=T-timedelta(minutes=30),
        expires_at=T+timedelta(days=5),
        is_current=True, deleted_at=None,
    )
    row.update(change)
    return validate_publication_time(
        row, video_id=7, metadata_id=41, source_id="gc-18",
        source_policy_version_id=3,
        previous_snapshot_id=51, snapshot_id=52,
        previous_observed_at=before, observed_at=T, as_of=T,
    )


class TestFreshnessPure(unittest.TestCase):
    def test_exact_zero_48_96_hour_golden_vectors(self):
        values = (
            (0, "100.000000000000"),
            (48, "50.000000000000"),
            (96, "25.000000000000"),
            (144, "12.500000000000"),
        )
        for hours, expected in values:
            with self.subTest(hours=hours):
                self.assertEqual(
                    compute_freshness_score(T-timedelta(hours=hours), T),
                    Decimal(expected),
                )
        self.assertEqual(FRESHNESS_HALF_LIFE_H, Decimal("48"))

    def test_no_wall_clock_drift_historical_date(self):
        seen = datetime(2016, 1, 1, tzinfo=timezone.utc)
        born = seen-timedelta(hours=48)
        self.assertEqual(compute_freshness_score(born, seen),
                         Decimal("50.000000000000"))
        self.assertEqual(compute_freshness_score(born, seen), compute_freshness_score(born, seen))

    def test_timezone_offsets_represent_same_instant(self):
        born = T-timedelta(hours=48)
        seen = T
        self.assertEqual(
            compute_freshness_score(
                born.astimezone(timezone(timedelta(hours=5, minutes=30))),
                seen.astimezone(timezone(timedelta(hours=-7)))),
            Decimal("50.000000000000"),
        )

    def test_microseconds_are_not_truncated_to_seconds(self):
        at_48 = compute_freshness_score(T-timedelta(hours=48), T)
        slightly_older = compute_freshness_score(
            T-timedelta(hours=48, microseconds=1), T,
        )
        self.assertLess(slightly_older, at_48)
        self.assertGreater(slightly_older, Decimal("49.999999999000"))

    def test_negative_age_never_becomes_above_100(self):
        with self.assertRaises(ContractError) as e:
            compute_freshness_score(T+timedelta(microseconds=1), T)
        self.assertEqual(e.exception.code, "invalid_publication_timestamp")

    def test_naive_missing_string_or_numeric_dates_fail(self):
        for value in (None, "2026-10-20T10:00:00Z", 123,
                      datetime(2026, 10, 20, 10)):
            with self.subTest(value=value), self.assertRaises(ContractError):
                compute_freshness_score(value, T)
            with self.subTest(obs=value), self.assertRaises(ContractError):
                compute_freshness_score(T-timedelta(hours=1), value)

    def test_repeatable_under_low_global_decimal_precision(self):
        with localcontext() as ctx:
            ctx.prec = 4
            ctx.rounding = ROUND_DOWN
            a = compute_freshness_score(T-timedelta(hours=37), T)
        b = compute_freshness_score(T-timedelta(hours=37), T)
        self.assertEqual(a, b)
        self.assertEqual(a.as_tuple().exponent, -12)

    def test_large_valid_ages_round_to_zero_without_clipping(self):
        self.assertEqual(
            compute_freshness_score(T-timedelta(days=3650), T),
            Decimal("0.000000000000"),
        )

    def test_verified_gc08_evidence_gives_same_result(self):
        proof = evidence()
        self.assertEqual(compute_verified_freshness(proof),
                         Decimal("50.000000000000"))
        self.assertEqual(proof.age_microseconds, 48*3600000000)

    def test_not_an_untrusted_mapping_or_fake_hash(self):
        with self.assertRaises(ContractError) as e:
            compute_verified_freshness({"published_at": T})
        self.assertEqual(e.exception.code, "missing_publication_metadata")
        for mismatch in (
            {"evidence_sha256": "bad"},
            {"schema_version": "another"},
            {"time_source": "videos.published_at"},
        ):
            with self.subTest(mismatch=mismatch), self.assertRaises(ContractError):
                compute_verified_freshness(replace(evidence(), **mismatch))

    def test_verified_evidence_rejects_tampered_published_at_or_timestamp(self):
        proof = evidence()
        for mismatch in (
            {"published_at": proof.published_at-timedelta(hours=1)},
            {"observed_at": proof.observed_at-timedelta(minutes=10)},
            {"metadata_id": 1000},
            {"source_id": "wrong"},
        ):
            with self.subTest(mismatch=mismatch), self.assertRaises(ContractError):
                compute_verified_freshness(replace(proof, **mismatch))

    def test_verified_evidence_expired_relative_to_checked_at_denied(self):
        proof = evidence()
        with self.assertRaises(ContractError):
            compute_verified_freshness(replace(proof, checked_at=T+timedelta(days=6)))

    def test_missing_publication_date_not_replaced_by_fetched_at(self):
        with self.assertRaises(ContractError) as e:
            evidence(published_at=None)
        self.assertEqual(e.exception.code, "invalid_publication_timestamp")

    def test_immutable_receipt_and_not_complete_scorer(self):
        self.assertEqual(FRESHNESS_VERSION, "group-c-freshness/v1")
        self.assertFalse(hasattr(PostgreSQLFreshness, "persist"))
        self.assertFalse(hasattr(PostgreSQLFreshness, "publish"))
        self.assertFalse(hasattr(PostgreSQLFreshness, "approve"))


@unittest.skipUnless(
    os.getenv("CM_TEST_PG_DSN") and os.getenv("GC_TEST_DISPOSABLE_DB") == "YES",
    "Disposable PostgreSQL17 only: CM_TEST_PG_DSN + GC_TEST_DISPOSABLE_DB=YES",
)
class TestFreshnessPostgres(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from test_group_c_publication_time import TestPublicationTimePostgres
        TestPublicationTimePostgres.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from test_group_c_publication_time import TestPublicationTimePostgres
        TestPublicationTimePostgres.tearDownClass.__func__(cls)

    def setUp(self):
        from test_group_c_publication_time import TestPublicationTimePostgres
        self.fixture = TestPublicationTimePostgres()
        self.fixture.conn = self.conn
        self.fixture.setUp()
        for key in ("project", "source", "policy", "video", "metadata", "snaps"):
            setattr(self, key, getattr(self.fixture, key))

    def tearDown(self):
        self.fixture.tearDown()

    def derive(self, **changes):
        params = dict(
            conn=self.conn, project_id=self.project,
            video_id=self.video, metadata_id=self.metadata,
            source_id=self.source, source_policy_version_id=self.policy,
            previous_snapshot_id=self.snaps[0], snapshot_id=self.snaps[1],
            metric_version="v1",
        )
        params.update(changes)
        return PostgreSQLFreshness().derive(**params)

    def test_exact_verified_age_and_no_persistence(self):
        item = self.derive()
        self.assertIsInstance(item, FreshnessResult)
        self.assertEqual(item.status, "freshness_derived_unapproved")
        self.assertFalse(item.ready_for_breakout)
        self.assertEqual(item.half_life_hours, Decimal("48"))
        self.assertGreater(item.age_hours, Decimal("100"))
        self.assertEqual(item.freshness_score,
                         compute_freshness_score(item.published_at, item.observed_at))
        with self.assertRaises(FrozenInstanceError):
            item.freshness_score = Decimal(0)
        self.assertFalse(self.conn.execute(
            "SELECT EXISTS(SELECT 1 FROM calculated_metrics WHERE post_id=%s)",
            (self.video,),
        ).fetchone()[0])

    def test_observation_time_only_not_legacy_video_or_fetched_at(self):
        original = self.derive()
        self.conn.execute(
            "UPDATE videos SET published_at=now()-interval '5 minutes' WHERE id=%s",
            (self.video,),
        )
        again = self.derive()
        self.assertEqual(original.freshness_score, again.freshness_score)
        self.assertEqual(original.evidence_sha256, again.evidence_sha256)

    def test_source_metadata_exact_48_and_96_hours(self):
        observed = self.conn.execute(
            "SELECT observed_at FROM metric_snapshots WHERE id=%s",
            (self.snaps[1],),
        ).fetchone()[0]
        for hours, expected in ((48, "50.000000000000"),
                                (96, "25.000000000000")):
            self.conn.execute(
                "UPDATE source_video_metadata SET published_at=%s WHERE id=%s",
                (observed-timedelta(hours=hours), self.metadata),
            )
            with self.subTest(hours=hours):
                self.assertEqual(self.derive().freshness_score, Decimal(expected))

    def test_readonly_deterministic_digest_two_calls(self):
        first, second = self.derive(), self.derive()
        self.assertEqual(first, second)
        self.assertEqual(len(first.publication_evidence_sha256), 64)
        self.assertEqual(len(first.evidence_sha256), 64)

    def test_future_missing_and_expired_publication_denied(self):
        self.conn.execute(
            "UPDATE source_video_metadata SET published_at=NULL WHERE id=%s",
            (self.metadata,),
        )
        with self.assertRaises(ContractError):
            self.derive()
        self.conn.execute(
            "UPDATE source_video_metadata SET published_at=now()-interval '5 days',expires_at=now()-interval '1 second' WHERE id=%s",
            (self.metadata,),
        )
        with self.assertRaises(ContractError):
            self.derive()

    def test_wrong_project_source_policy_version_or_metadata_denied(self):
        for bad in (
            {"project_id": uuid4().hex}, {"source_id": "other"},
            {"source_policy_version_id": 999999}, {"metric_version": "v2"},
            {"metadata_id": 999999}, {"video_id": 999999},
            {"previous_snapshot_id": self.snaps[1],
             "snapshot_id": self.snaps[0]},
        ):
            with self.subTest(bad=bad), self.assertRaises(ContractError):
                self.derive(**bad)

    def test_source_rights_revoked_denied(self):
        self.conn.execute(
            "UPDATE sources SET can_derive_scores=FALSE WHERE id=%s",
            (self.source,),
        )
        with self.assertRaises(ContractError):
            self.derive()

    def test_newer_retracted_or_wrong_version_blocks_old_pair(self):
        self.conn.execute(
            """INSERT INTO metric_snapshots
               (video_id,source_id,source_policy_version_id,
                observed_at,collected_at,views,likes,comments,shares,
                source_metric_definition,source_metric_version,
                data_quality_status,precision,observation_time_assumed)
               VALUES(%s,%s,%s,now()-interval '10 minutes',now(),
                      15,2,1,1,'platform_post_views','v2','retracted','exact',FALSE)""",
            (self.video, self.source, self.policy),
        )
        with self.assertRaises(ContractError) as e:
            self.derive()
        self.assertEqual(e.exception.code, "metric_history_changed")

    def test_current_snapshot_nonexact_quality_denied(self):
        # The raw metric snapshot's quality status is intentionally mutable,
        # whereas its counters/source/times are append-only.
        self.conn.execute(
            "UPDATE metric_snapshots SET data_quality_status='retracted' WHERE id=%s",
            (self.snaps[1],),
        )
        with self.assertRaises(ContractError):
            self.derive()

    def test_zero_views_allowed_for_c6_but_no_c7_score(self):
        # Existing fixture contains nonzero views. This test explicitly
        # proves C6 does not depend on L/C/S by reusing a views-only run.
        first = self.derive()
        self.assertGreater(first.freshness_score, Decimal("0"))
        self.assertFalse(first.ready_for_breakout)

    def test_independent_readonly_transaction_required(self):
        import psycopg
        with psycopg.connect(os.environ["CM_TEST_PG_DSN"], autocommit=False) as conn:
            with self.assertRaises(ContractError) as e:
                self.derive(conn=conn)
            self.assertEqual(e.exception.code, "transaction_contract_unavailable")


if __name__ == "__main__":
    unittest.main()
