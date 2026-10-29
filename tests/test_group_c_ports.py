"""GC-MOD-02: strict, dependency-free integration seam tests (no DB/live peers)."""
import dataclasses
import importlib
import unittest
from dataclasses import replace
from uuid import UUID

from intelligence.group_c.contracts import ContractError, GroupCInputPointer
from intelligence.group_c.ports import (
    CapabilityState, ConfigApprovalReader, CohortApprovalReader, CreatorTopicRegistry,
    D1FactsReader, QDPPreparationReader, SourcePolicyReader, AnalyticsPublisher,
    GroupCPorts, IntegrationCapability, PORT_SCHEMAS, QDPHandoffNotice,
    require_available,
)

RUN = "00112233-4455-6677-8899-aabbccddeeff"
EVENT = "11112233-4455-6677-8899-aabbccddeeff"
WIRE = {"event_id": EVENT, "project_id": "a" * 32, "qdp_run_id": RUN,
        "video_id": 5, "analysis_group": "C", "input_sha256": "b" * 64,
        "schema_version": "qdp-handoff/v1"}


class Probe:
    """Test-only probe with all required callable method slots, no real IO."""
    def __init__(self, port, state=CapabilityState.AVAILABLE, version=None):
        self.port = port
        self.state = state
        self.version = version if version is not None else PORT_SCHEMAS[port]
        self.probe_calls = 0

    def capability(self, conn):
        self.probe_calls += 1
        return IntegrationCapability(self.port, self.state, self.version, "fixture")

    def __getattr__(self, key):
        if key.startswith("read_") or key in {"assert_eligible", "enqueue_persisted_result"}:
            return lambda *args: None
        raise AttributeError(key)


class TestGroupCPorts(unittest.TestCase):
    def test_standalone_import_no_peer_code(self):
        # No QDP/Analytics/Collector import is necessary to access port declarations.
        p = importlib.import_module("intelligence.group_c.ports")
        self.assertIn("GroupCPorts", vars(p))
        for cls in (QDPPreparationReader, D1FactsReader, CreatorTopicRegistry,
                    SourcePolicyReader, CohortApprovalReader, ConfigApprovalReader,
                    AnalyticsPublisher):
            self.assertTrue(hasattr(cls, "capability"))

    def test_schema_versions_are_frozen_and_complete(self):
        self.assertEqual(tuple(PORT_SCHEMAS), (
            "qdp", "d1", "registry", "source_policy", "cohorts", "config", "analytics"))
        self.assertEqual(PORT_SCHEMAS["qdp"], "qdp-handoff/v1")
        with self.assertRaises(TypeError):
            PORT_SCHEMAS["qdp"] = "qdp-handoff/v2"

    def test_qdp_exact_wire_and_no_implicit_normalized_pointer(self):
        notice = QDPHandoffNotice.from_mapping(WIRE)
        self.assertEqual(notice.event_id, EVENT)
        self.assertEqual(notice.video_id, WIRE["video_id"])
        self.assertIsInstance(UUID(notice.qdp_run_id), UUID)
        self.assertFalse(hasattr(notice, "source_id"))
        self.assertFalse(hasattr(notice, "metric_snapshot_id"))
        self.assertFalse(hasattr(notice, "preparation_sha256"))
        self.assertNotIsInstance(notice, GroupCInputPointer)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            notice.video_id = 200
        self.assertEqual(QDPHandoffNotice.from_mapping(dict(reversed(list(WIRE.items())))), notice)

    def test_qdp_wire_adversarial_inputs(self):
        for change in (
            {"event_id": "not-uuid"}, {"event_id": EVENT.upper()},
            {"qdp_run_id": RUN.replace("-", "")}, {"qdp_run_id": True},
            {"project_id": "A" * 32}, {"project_id": "a" * 31},
            {"video_id": 0}, {"video_id": True}, {"analysis_group": "B"},
            {"analysis_group": "A"}, {"analysis_group": "c"},
            {"input_sha256": "B" * 64}, {"input_sha256": "b" * 63},
            {"schema_version": "qdp-handoff/v2"},
        ):
            with self.subTest(change=change), self.assertRaises(ContractError):
                QDPHandoffNotice.from_mapping({**WIRE, **change})
        for raw in (None, [], "", 1, {**WIRE, "source_id": "unknown"},
                    {k: v for k, v in WIRE.items() if k != "schema_version"},
                    {**WIRE, 3: "not a string key"}):
            with self.subTest(value=raw), self.assertRaises(ContractError):
                QDPHandoffNotice.from_mapping(raw)

    def test_unconfigured_ports_never_implicitly_ready(self):
        p = GroupCPorts()
        for port in PORT_SCHEMAS:
            with self.subTest(port=port):
                self.assertEqual(p.capability(port, None).state, CapabilityState.UNAVAILABLE)
                with self.assertRaises(ContractError) as e:
                    p.require(port, None)
                self.assertEqual(e.exception.code, "dependency_unavailable")
        with self.assertRaises(ContractError) as e:
            p.require_all(("d1", "qdp"), None)
        self.assertEqual(e.exception.code, "dependency_unavailable")

    def test_probes_are_version_bound_and_typed(self):
        for port in PORT_SCHEMAS:
            with self.subTest(port=port):
                obj = Probe(port)
                bundle = GroupCPorts(**{port: obj})
                self.assertIs(bundle.require(port, None), obj)
                self.assertEqual(obj.probe_calls, 1)
                for state, code in ((CapabilityState.UNAVAILABLE, "dependency_unavailable"),
                                    (CapabilityState.INCOMPATIBLE, "dependency_incompatible")):
                    with self.assertRaises(ContractError) as e:
                        GroupCPorts(**{port: Probe(port, state)}).require(port, None)
                    self.assertEqual(e.exception.code, code)
                with self.assertRaises(ContractError) as e:
                    GroupCPorts(**{port: Probe(port, version="v2")}).require(port, None)
                self.assertEqual(e.exception.code, "dependency_incompatible")

    def test_wrong_port_and_malformed_probe_fail_closed(self):
        class WrongPort:
            def capability(self, conn):
                return IntegrationCapability("config", CapabilityState.AVAILABLE,
                                             PORT_SCHEMAS["config"])

        class BadProbe:
            def capability(self, conn):
                return {"state": "available"}

        class Crashed:
            def capability(self, conn):
                raise ConnectionError("offline")

        class NoMethods:
            def capability(self, conn):
                return IntegrationCapability("qdp", CapabilityState.AVAILABLE,
                                             PORT_SCHEMAS["qdp"])

        for adapter, code in ((WrongPort(), "dependency_incompatible"),
                              (BadProbe(), "dependency_incompatible"),
                              (Crashed(), "dependency_unavailable"),
                              (NoMethods(), "dependency_incompatible"),
                              (object(), "dependency_incompatible")):
            with self.subTest(adapter=type(adapter).__name__), self.assertRaises(ContractError) as e:
                GroupCPorts(qdp=adapter).require("qdp", None)
            self.assertEqual(e.exception.code, code)
        for port in (None, "", "SRE", 0, "qdp/v2"):
            with self.assertRaises(ContractError):
                GroupCPorts().capability(port, None)
        with self.assertRaises(ContractError):
            require_available(None, "qdp")
        with self.assertRaises(ContractError):
            require_available(IntegrationCapability("config"), "qdp")

    def test_capability_input_validation(self):
        for kwargs in (
            {"port": "bad"}, {"port": "qdp", "state": "available"},
            {"port": "qdp", "state": CapabilityState.AVAILABLE},
            {"port": "qdp", "observed_schema": ""},
            {"port": "qdp", "reason_code": " "},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ContractError):
                IntegrationCapability(**kwargs)

    def test_require_all_never_returns_partial_map(self):
        d1 = Probe("d1")
        qdp = Probe("qdp")
        bundle = GroupCPorts(d1=d1, qdp=qdp)
        ready = bundle.require_all(("qdp", "d1"), None)
        self.assertIs(ready["qdp"], qdp)
        self.assertIs(ready["d1"], d1)
        with self.assertRaises(TypeError):
            ready["config"] = object()
        for requested in ([], (), "qdp", ["qdp", "qdp"]):
            with self.subTest(requested=requested), self.assertRaises(ContractError):
                bundle.require_all(requested, None)
        with self.assertRaises(ContractError) as e:
            bundle.require_all(("qdp", "config"), None)
        self.assertEqual(e.exception.code, "dependency_unavailable")


if __name__ == "__main__":
    unittest.main()
