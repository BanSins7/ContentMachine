"""FIX-00 integration boundary tests; no DB, provider or peer modules."""
import unittest
import uuid

from intelligence.qualified_data.contracts import QDPError
from intelligence.qualified_data.integrations.contracts import (
    CapabilityState, IntegrationCapability, QDPHandoffPointer,
    require_available, SreRunReader, D1VideoReader,
    PreparationConsumer, AnalyticsResultReader,
)


class IntegrationContractsTest(unittest.TestCase):
    def test_default_is_fail_closed(self):
        c = IntegrationCapability(port="sre.live")
        self.assertEqual(c.state, CapabilityState.UNAVAILABLE)
        with self.assertRaises(QDPError) as ctx:
            require_available(c)
        self.assertEqual(ctx.exception.code, "dependency_unavailable")
        self.assertEqual(ctx.exception.status, 503)

    def test_incompatible_is_not_available(self):
        with self.assertRaises(QDPError) as ctx:
            require_available(IntegrationCapability(
                "sre.live", CapabilityState.INCOMPATIBLE, reason_code="missing_column"))
        self.assertEqual(ctx.exception.code, "dependency_incompatible")
        require_available(IntegrationCapability("sre.live", CapabilityState.AVAILABLE,
                                                observed_schema="verified/v1"))

    def test_rejects_invalid_enum(self):
        with self.assertRaises(ValueError):
            IntegrationCapability("sre.live", "available")

    def test_valid_handoff_is_immutable_reference_only(self):
        ptr = QDPHandoffPointer(
            event_id=str(uuid.uuid4()),
            project_id="a" * 32,
            qdp_run_id=str(uuid.uuid4()),
            video_id=1,
            analysis_group="A",
            input_sha256="b" * 64)
        self.assertEqual(ptr.schema_version, "qdp-handoff/v1")
        with self.assertRaises(AttributeError):
            ptr.video_id = 2

    def test_invalid_handoff_is_rejected(self):
        kwargs = {
            "event_id": str(uuid.uuid4()),
            "project_id": "a" * 32,
            "qdp_run_id": str(uuid.uuid4()),
            "video_id": 1,
            "analysis_group": "B",
            "input_sha256": "b" * 64,
        }
        for patch in (
            {"event_id": "not-uuid"}, {"project_id": "wrong"},
            {"qdp_run_id": "WRONG"}, {"video_id": 0},
            {"video_id": True}, {"analysis_group": "X"},
            {"input_sha256": "not-sha"},
            {"schema_version": "qdp-handoff/v2"},
        ):
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                QDPHandoffPointer(**dict(kwargs, **patch))

    def test_ports_only_describe_obligations(self):
        for port in (SreRunReader, D1VideoReader,
                     PreparationConsumer, AnalyticsResultReader):
            self.assertTrue(hasattr(port, "capability"))
        self.assertTrue(hasattr(SreRunReader, "read_expected_manifest"))
        self.assertTrue(hasattr(PreparationConsumer, "receive"))


if __name__ == "__main__":
    unittest.main()
