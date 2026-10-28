"""PIE-MOD-01: audit-only synthetic HMAC fixture; never authenticate real UAE data."""
from copy import deepcopy
from decimal import Decimal, InvalidOperation
import hmac
import json
from pathlib import Path
import re
import unittest

from intelligence.patterns_insights import PIEDatasetPointer, PIEBoundaryError, canonical_hash

ROOT = Path(__file__).parent / "fixtures" / "pie"
KEY = b"PIE-AUDIT-TEST-ONLY-NOT-A-PRODUCTION-KEY"
HEX64 = re.compile(r"[0-9a-f]{64}\Z")


def fixture_signature(payload):
    """Test-only integrity seal. Not a UAE receipt or production signing key."""
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hmac.new(KEY, blob, "sha256").hexdigest()


def assert_sealed_fixture(sealed):
    payload = sealed["payload"]
    assert sealed["seal"]["key_id"] == "PIE-AUDIT-TEST-ONLY"
    assert sealed["seal"]["algorithm"] == "hmac-sha256"
    assert hmac.compare_digest(sealed["seal"]["digest"], canonical_hash(payload))
    assert hmac.compare_digest(sealed["seal"]["hmac"], fixture_signature(payload))
    return payload


def validate_rowset_for_audit(sealed):
    """Only a fixture-format assertion; MUST NOT be a trusted PIE intake reader."""
    p = assert_sealed_fixture(sealed)
    assert p["fixture_kind"] == "synthetic_non_authoritative"
    assert p["schema_version"] == "pie-audit-rowset/v0"
    pointer = PIEDatasetPointer(
        project_id=p["project_id"], source_id=p["source_id"],
        source_policy_version_id=p["source_policy_version_id"],
        uae_engine_version=p["uae_engine_version"],
        uae_dataset_sha256="e"*64, uae_manifest_sha256=p["uae_manifest_sha256"],
        calculation_config_id=p["calculation_config_id"],
        source_metric_definition=p["source_metric_definition"],
        source_metric_version=p["source_metric_version"], as_of=p["as_of"])
    rows = p["observations"]
    assert isinstance(rows, list) and rows
    ids = set()
    for row in rows:
        assert type(row["video_id"]) is int and row["video_id"] > 0
        assert row["video_id"] not in ids
        ids.add(row["video_id"])
        assert row["creator_token"] and row["platform"]
        assert row["content_kind"] in (
            "video", "slideshow_video", "photo_carousel", "mixed_carousel")
        assert row["status"] == "qualified" and row["score_status"] == "complete"
        assert HEX64.fullmatch(row["observation_sha256"])
        try:
            value = Decimal(row["breakout_score"])
        except (InvalidOperation, TypeError, ValueError) as exc:
            raise AssertionError("invalid synthetic score") from exc
        assert value.is_finite()
    return pointer, tuple(sorted(ids))


class PIEOwnershipAuditTests(unittest.TestCase):
    def load(self, name):
        return json.loads((ROOT / name).read_text(encoding="utf-8"))

    def test_signed_synthetic_rows_are_replayable_only_as_test_evidence(self):
        sealed = self.load("audit_rowset.json")
        ptr, ids = validate_rowset_for_audit(sealed)
        self.assertEqual(ids, (101, 102))
        self.assertEqual(ptr.project_id, "a"*32)
        self.assertEqual(validate_rowset_for_audit(deepcopy(sealed)), (ptr, ids))
        self.assertEqual(sealed["seal"]["digest"], canonical_hash(sealed["payload"]))
        self.assertEqual(ptr.source_metric_version, "synthetic-v1")
        self.assertGreater(
            Decimal(sealed["payload"]["observations"][0]["breakout_score"]), 100)

    def test_tamper_fails_even_when_qualified_text_remains(self):
        sealed = self.load("audit_rowset.json")
        sealed["payload"]["observations"][0]["breakout_score"] = "999999"
        with self.assertRaises(AssertionError):
            validate_rowset_for_audit(sealed)

    def test_validly_sealed_aggregate_is_not_row_level_evidence(self):
        sealed = self.load("audit_aggregate_only.json")
        self.assertEqual(assert_sealed_fixture(sealed)["summary"]["sample_size"], 2000)
        with self.assertRaises((AssertionError, KeyError, PIEBoundaryError)):
            validate_rowset_for_audit(sealed)

    def test_duplicate_and_nonfinite_rows_rejected_even_if_resealed(self):
        for mutation in ("duplicate", "nonfinite", "wrong_schema"):
            sealed = self.load("audit_rowset.json")
            if mutation == "duplicate":
                sealed["payload"]["observations"][1]["video_id"] = 101
            elif mutation == "nonfinite":
                sealed["payload"]["observations"][1]["breakout_score"] = "NaN"
            else:
                sealed["payload"]["schema_version"] = "uae-to-pie/v1"
            sealed["seal"]["digest"] = canonical_hash(sealed["payload"])
            sealed["seal"]["hmac"] = fixture_signature(sealed["payload"])
            with self.subTest(mutation=mutation), self.assertRaises(AssertionError):
                validate_rowset_for_audit(sealed)

    def test_no_live_uae_port_exposed_by_audit(self):
        import intelligence.patterns_insights as pie
        for name in (
            "load_qualified_dataset", "discover_patterns", "generate_insights", "run_analysis"
        ):
            self.assertFalse(hasattr(pie, name))


if __name__ == "__main__":
    unittest.main()
