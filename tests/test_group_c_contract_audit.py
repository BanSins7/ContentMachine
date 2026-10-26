"""GC-MOD-01 pure audit QA: frozen C0-C7 contracts vs applied 001-004 SQL.

No database connection, no scorer and no external branch imports required.
These are contract checks, NOT an assertion that the PG runtime is guarded.
"""
import unittest
from dataclasses import FrozenInstanceError, replace
from pathlib import Path
from decimal import Decimal

from intelligence.group_c import ContractError, BREAKOUT_WEIGHTS, FRESHNESS_HALF_LIFE_H
from intelligence.group_c.contract_audit import (
    CONTRACT_VERSION, NUMERIC_STORAGE, SCALE, RAW_SNAPSHOT_FACTS,
    SNAPSHOT_PROVENANCE, PUBLICATION_PROVENANCE, CALCULATION_LINEAGE,
    DB_SCORE_STATUSES, METRIC_CONTRACTS, BLOCKER_TO_DB_STATUS,
    status_for_blocker, validate_contract_manifest,
)

ROOT = Path(__file__).resolve().parents[1] / "intelligence" / "migrations"


def sql(name):
    return (ROOT / name).read_text(encoding="utf-8").lower()


class GroupCContractAuditQA(unittest.TestCase):
    def test_complete_inventory_immutable_boundary(self):
        validate_contract_manifest()
        self.assertEqual(CONTRACT_VERSION, "group-c-contract-matrix/v1")
        self.assertEqual(NUMERIC_STORAGE, "NUMERIC(30,12)")
        self.assertEqual(SCALE, 12)
        self.assertEqual(tuple(c.code for c in METRIC_CONTRACTS),
                         tuple("C" + str(i) for i in range(8)))
        self.assertEqual([x.code for x in METRIC_CONTRACTS if x.optional_for_c7], ["C2"])
        for contract in METRIC_CONTRACTS:
            self.assertTrue(contract.inputs)
            self.assertTrue(contract.outputs)
            self.assertTrue(contract.formula)
            self.assertTrue(contract.units)
        with self.assertRaises(FrozenInstanceError):
            METRIC_CONTRACTS[0].code = "C8"
        with self.assertRaises(FrozenInstanceError):
            replace(METRIC_CONTRACTS[7], code="C8").code = "C9"

    def test_actual_schema_owns_raw_and_metric_outputs(self):
        a, b, c, d = (sql(name) for name in (
            "001_foundation.sql", "002_discovery.sql",
            "003_performance_c.sql", "004_performance_c_integrity.sql",
        ))
        self.assertIn("create table if not exists metric_snapshots", a)
        self.assertIn("create table if not exists source_video_metadata", b)
        self.assertIn("create table calculated_metrics", c)
        self.assertNotIn("create table metrics_snapshots", c)
        self.assertNotIn("create table content_posts", c)
        for field in RAW_SNAPSHOT_FACTS:
            with self.subTest(raw=field):
                self.assertRegex(a, r"(?m)^\s*" + field + r"\s+bigint\b")
        for field in SNAPSHOT_PROVENANCE:
            with self.subTest(provenance=field):
                self.assertIn(field, a + b + c + d)
        for field in PUBLICATION_PROVENANCE:
            with self.subTest(metadata=field):
                self.assertIn(field, b + d)
        for field in CALCULATION_LINEAGE:
            with self.subTest(lineage=field):
                self.assertRegex(c + d, r"(?m)^\s*" + field + r"\s+bigint\b")
        for contract in METRIC_CONTRACTS[1:]:
            for field in contract.outputs:
                with self.subTest(output=field):
                    self.assertRegex(c, r"(?m)^\s*" + field + r"\s+numeric\(")
        self.assertIn("unique nulls not distinct", c)
        self.assertIn("published_metadata_id", d)
        self.assertIn("completed results are immutable", d)

    def test_sql_status_enum_is_not_implicitly_extended(self):
        c = sql("003_performance_c.sql")
        self.assertEqual(DB_SCORE_STATUSES, frozenset((
            "complete", "insufficient_data", "pending_configuration",
            "invalid_timestamp", "incompatible_source", "counter_correction",
            "calculation_error",
        )))
        for status in DB_SCORE_STATUSES:
            self.assertIn("'" + status + "'", c)
        self.assertNotIn("'source_policy_unavailable'", c)
        self.assertNotIn("'unapproved_cohort'", c)

    def test_unapproved_unknown_and_incompatible_fail_closed(self):
        cases = {
            "missing_snapshot": "insufficient_data",
            "missing_raw_counter": "insufficient_data",
            "zero_views_rate_undefined": "insufficient_data",
            "missing_prior_cohort": "insufficient_data",
            "unapproved_config": "pending_configuration",
            "unapproved_cohort": "pending_configuration",
            "missing_alpha_or_k_or_priors": "pending_configuration",
            "invalid_observation_interval": "invalid_timestamp",
            "incompatible_metric_version": "incompatible_source",
            "source_expired_or_retracted": "incompatible_source",
            "counter_decreased": "counter_correction",
            "arithmetic_error": "calculation_error",
        }
        for reason, expected in cases.items():
            with self.subTest(reason=reason):
                self.assertEqual(status_for_blocker(reason), expected)
        self.assertNotIn("complete", BLOCKER_TO_DB_STATUS.values())
        for bad in ("", "niche_missing", "complete", "unmapped", None, True):
            with self.subTest(unknown=bad), self.assertRaises(ContractError) as error:
                status_for_blocker(bad)
            self.assertEqual(error.exception.code, "unmapped_c_blocker")
        with self.assertRaises(TypeError):
            BLOCKER_TO_DB_STATUS["invented"] = "complete"

    def test_formulas_fixed_as_raw_score_not_normalized(self):
        c, d = sql("003_performance_c.sql"), sql("004_performance_c_integrity.sql")
        self.assertEqual(BREAKOUT_WEIGHTS, tuple(map(Decimal, ("0.45", "0.30", "0.15", "0.10"))))
        self.assertEqual(FRESHNESS_HALF_LIFE_H, Decimal(48))
        self.assertNotIn("niche_outlier", METRIC_CONTRACTS[-1].inputs)
        self.assertEqual(METRIC_CONTRACTS[-1].outputs[-1], "breakout_score")
        self.assertIn("normalization_enabled = false", c)
        self.assertIn("freshness_half_life_h = 48", c)
        self.assertIn("round(0.45 * creator_ratio, 12)", d)
        self.assertIn("round(0.30 * views_growth_per_hour, 12)", d)
        self.assertIn("round(0.15 * weighted_engagement_smoothed, 12)", d)
        self.assertIn("round(0.10 * freshness_score, 12)", d)
        self.assertIn("numeric(30,12)", c)
        self.assertNotIn("breakout_score <= 100", c)
        self.assertNotIn("niche_outlier *", d)

    def test_future_score_is_not_spuriously_exposed(self):
        import intelligence.group_c as c
        self.assertFalse(hasattr(c, "compute_c_metrics"))
        self.assertFalse(hasattr(c, "score_video"))


if __name__ == "__main__":
    unittest.main()
