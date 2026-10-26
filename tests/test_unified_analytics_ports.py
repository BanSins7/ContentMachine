"""UA-MOD-02: no-network, no-peer-installation, no-DB port/DTO regression."""
import unittest
from dataclasses import replace
from types import MappingProxyType

from intelligence.unified_analytics import (
    AnalyticsBoundaryError, UnifiedAnalyticsInputPointer,
    InputScope, EvidenceRef, EvidenceRead, ReadState, PatternsHandoffPointer,
    PortCapability, CapabilityState, UAEPorts, UAEConfiguration,
)
from intelligence.unified_analytics.ports import KNOWN_PEER_WIRES, PORT_SCHEMAS

PID = "a" * 32
RUN = "00112233-4455-6677-8899-aabbccddeeff"
SHA_A, SHA_B, SHA_C = "b" * 64, "c" * 64, "d" * 64

SCOPE = InputScope(PID, 13, "licensed-source", 3, "video", RUN, SHA_A)


def reference(port, scope=SCOPE, schema=None, record="verified:123", version="r1"):
    return EvidenceRef(
        scope, port, record,
        schema or KNOWN_PEER_WIRES[port] or "adapter-output/v1",
        version, SHA_B,
    )


def located(port, scope=SCOPE, schema=None):
    return EvidenceRead(port, scope, ReadState.LOCATED, reference(port, scope, schema))


class FakeReader:
    """No calls to source service; only fake adapter outcomes for contract tests."""

    def __init__(self, port, schema=None, peer=None, state=CapabilityState.DECLARED,
                 result=None):
        self.port = port
        self.schema = schema if schema is not None else PORT_SCHEMAS[port]
        self.peer = peer if peer is not None else KNOWN_PEER_WIRES[port]
        self.state = state
        self.result = result
        self.probes = 0
        self.reads = 0

    def capability(self):
        self.probes += 1
        return PortCapability(
            self.port, self.state,
            self.schema, self.peer,
            None if self.state is CapabilityState.DECLARED else "disabled",
        )

    def _read(self, scope):
        self.reads += 1
        return self.result or located(self.port, scope)

    def read_current_relevance(self, scope):
        return self._read(scope)

    def read_qualified_membership(self, scope):
        return self._read(scope)

    def read_completed_analysis(self, scope):
        return self._read(scope)

    def read_persisted_metric(self, scope):
        return self._read(scope)

    def read_canonical_video(self, scope):
        return self._read(scope)

    def read_current_policy(self, scope):
        return self._read(scope)


class BadProbe(FakeReader):
    def capability(self):
        raise RuntimeError("Postgres: secret connection string")


class NoReadMethod:
    def capability(self):
        return PortCapability("qdp", CapabilityState.DECLARED,
                              PORT_SCHEMAS["qdp"], KNOWN_PEER_WIRES["qdp"])


class PortDtoTests(unittest.TestCase):
    def test_scope_is_strict_and_maps_existing_bootstrap_pointer(self):
        raw = UnifiedAnalyticsInputPointer(
            project_id=PID, video_id=13, source_id="licensed-source",
            source_policy_version_id=3, content_kind="video", qdp_run_id=RUN,
            preparation_sha256=SHA_A, group_a_analysis_id=1,
            group_b_analysis_id=2, calculated_metric_id=3,
            sre_decision_sha256=SHA_C,
        )
        self.assertEqual(InputScope.from_pointer(raw), SCOPE)
        self.assertEqual(len(SCOPE.scope_sha256), 64)
        self.assertEqual(SCOPE.scope_sha256, InputScope.from_pointer(raw).scope_sha256)
        for kw in (
            {"project_id": "A"*32}, {"video_id": True},
            {"source_id": " source "}, {"source_policy_version_id": False},
            {"content_kind": "image_post"}, {"qdp_run_id": RUN.upper()},
            {"qdp_run_id": RUN.replace("-", "")}, {"qdp_input_sha256": "B"*64},
        ):
            with self.subTest(kw=kw), self.assertRaises(AnalyticsBoundaryError):
                replace(SCOPE, **kw)
        with self.assertRaises(AnalyticsBoundaryError):
            InputScope.from_pointer({})

    def test_exact_reference_binding_not_certificate(self):
        a = reference("group_a")
        b = replace(a, producer_version="r2")
        self.assertNotEqual(a.lineage_sha256, b.lineage_sha256)
        self.assertNotEqual(a.lineage_sha256,
                            replace(a, payload_sha256=SHA_C).lineage_sha256)
        self.assertNotEqual(a.lineage_sha256,
                            replace(a, scope=replace(SCOPE, video_id=14)).lineage_sha256)
        self.assertFalse(hasattr(a, "qualified"))
        for kw in (
            {"port": "patterns"}, {"record_key": " "},
            {"producer_version": ""}, {"producer_schema": "no-version"},
            {"payload_sha256": "z"*64}, {"schema_version": "old/v1"},
        ):
            with self.subTest(kw=kw), self.assertRaises(AnalyticsBoundaryError):
                replace(a, **kw)

    def test_missing_is_distinct_from_unavailable_or_located(self):
        self.assertIsNone(EvidenceRead("qdp", SCOPE, ReadState.MISSING,
                                       reason_code="no_qualified_item").reference)
        self.assertEqual(EvidenceRead("qdp", SCOPE, ReadState.UNAVAILABLE,
                                      reason_code="not_connected").state,
                         ReadState.UNAVAILABLE)
        self.assertIs(located("qdp").state, ReadState.LOCATED)
        for kwargs in (
            {"state": ReadState.LOCATED},
            {"state": ReadState.MISSING, "reference": reference("qdp"),
             "reason_code": "missing"},
            {"state": ReadState.UNAVAILABLE},
            {"state": ReadState.LOCATED, "reference": reference("sre")},
            {"state": ReadState.LOCATED,
             "reference": reference("qdp", scope=replace(SCOPE, video_id=22))},
        ):
            with self.subTest(kw=kwargs), self.assertRaises(AnalyticsBoundaryError):
                EvidenceRead(port="qdp", scope=SCOPE, **kwargs)

    def test_future_patterns_pointer_is_only_reference(self):
        p = PatternsHandoffPointer(PID, 9, SHA_A, SHA_B)
        self.assertEqual(p.schema_version, "uae-pattern-handoff/v1")
        self.assertFalse(hasattr(p, "publish"))
        for kw in ({"snapshot_id": 0}, {"aggregate_sha256": "X"*64},
                   {"schema_version": "unknown/v2"}):
            with self.assertRaises(AnalyticsBoundaryError):
                replace(p, **kw)

    def test_default_configuration_is_inert_and_strict(self):
        default = UAEConfiguration.from_mapping()
        self.assertFalse(default.allow_background_polling)
        self.assertFalse(default.allow_provider_requests)
        self.assertEqual(default.max_read_batch, 50)
        self.assertEqual(UAEConfiguration.from_mapping({"max_read_batch": 1}).max_read_batch, 1)
        for cfg in (
            {"max_read_batch": 0}, {"max_snapshot_page": True},
            {"allow_provider_requests": True}, {"allow_background_polling": 1},
            {"schema_version": "uae-configuration/v2"},
            {"billing_key": "secret"},
        ):
            with self.subTest(config=cfg), self.assertRaises(AnalyticsBoundaryError):
                UAEConfiguration.from_mapping(cfg)
        with self.assertRaises(AnalyticsBoundaryError):
            UAEConfiguration.from_mapping(MappingProxyType({}))

    def test_can_import_without_peers_and_c_scoring(self):
        import intelligence.unified_analytics as module
        self.assertFalse(hasattr(module, "compute_c_metrics"))
        self.assertFalse(hasattr(module, "run_qualification"))
        self.assertFalse(hasattr(module, "aggregate_project"))


class PortRegistryTests(unittest.TestCase):
    def test_all_ports_unavailable_by_default_and_no_reads(self):
        ports = UAEPorts()
        reports = ports.report()
        self.assertEqual(set(s.port for s in reports), set(PORT_SCHEMAS))
        self.assertTrue(all(s.state is CapabilityState.UNAVAILABLE for s in reports))
        self.assertTrue(all(s.reason_code == "not_connected" for s in reports))
        self.assertTrue(all(not s.qualified for s in reports))
        for key in PORT_SCHEMAS:
            with self.subTest(key=key), self.assertRaises(AnalyticsBoundaryError) as err:
                ports.require(key)
            self.assertEqual(err.exception.code, "dependency_unavailable")

    def test_successful_metadata_handshake_is_not_qualified(self):
        mocked = FakeReader("qdp")
        ports = UAEPorts(qdp=mocked)
        s = ports.status("qdp")
        self.assertEqual(s.state, CapabilityState.DECLARED)
        self.assertFalse(s.qualified)
        self.assertEqual(mocked.reads, 0)
        self.assertIs(ports.require("qdp"), mocked)
        got = ports.read("qdp", SCOPE)
        self.assertIs(got.state, ReadState.LOCATED)
        self.assertEqual(mocked.reads, 1)
        self.assertEqual(got.reference.scope, SCOPE)

    def test_peer_schema_and_adapter_schema_mismatch_fail_closed(self):
        for bad in (
            FakeReader("qdp", schema="uae-qdp-reader/v99"),
            FakeReader("qdp", peer="qdp-handoff/v99"),
        ):
            ports = UAEPorts(qdp=bad)
            self.assertEqual(ports.status("qdp").state, CapabilityState.INCOMPATIBLE)
            with self.assertRaises(AnalyticsBoundaryError) as cm:
                ports.require("qdp")
            self.assertEqual(cm.exception.code, "dependency_incompatible")
            self.assertEqual(bad.reads, 0)

    def test_missing_method_wrong_capability_or_probe_failure(self):
        self.assertEqual(UAEPorts(qdp=NoReadMethod()).status("qdp").reason_code,
                         "missing_protocol_methods")
        broken = UAEPorts(qdp=BadProbe("qdp"))
        self.assertEqual(broken.status("qdp").reason_code, "probe_failed")
        with self.assertRaises(AnalyticsBoundaryError) as cm:
            broken.require("qdp")
        self.assertEqual(cm.exception.code, "dependency_unavailable")
        self.assertNotIn("secret", str(cm.exception))
        self.assertEqual(UAEPorts(qdp=FakeReader("sre")).status("qdp").reason_code,
                         "invalid_capability")

    def test_typed_absence_and_wrong_scope_are_rejected(self):
        missing = FakeReader("qdp", result=EvidenceRead(
            "qdp", SCOPE, ReadState.MISSING, reason_code="no_qualified_item"))
        self.assertEqual(UAEPorts(qdp=missing).read("qdp", SCOPE).state, ReadState.MISSING)
        for bad in (
            EvidenceRead("qdp", replace(SCOPE, source_policy_version_id=8),
                         ReadState.MISSING, reason_code="no_qualified_item"),
            located("sre"),
            located("qdp", schema="qdp-handoff/v2"),
        ):
            with self.subTest(bad=bad), self.assertRaises(AnalyticsBoundaryError):
                UAEPorts(qdp=FakeReader("qdp", result=bad)).read("qdp", SCOPE)

    def test_groupless_peer_wire_cannot_be_faked_complete(self):
        self.assertIsNone(KNOWN_PEER_WIRES["group_b"])
        self.assertIsNone(KNOWN_PEER_WIRES["group_c"])
        for port in ("group_b", "group_c"):
            reader = FakeReader(port)
            registry = UAEPorts(**{port: reader})
            self.assertEqual(registry.status(port).state, CapabilityState.DECLARED)
            self.assertFalse(registry.status(port).qualified)
            self.assertIs(registry.read(port, SCOPE).state, ReadState.LOCATED)

    def test_invalid_port_is_typed(self):
        for method in ("status", "require", "read"):
            with self.subTest(method=method), self.assertRaises(AnalyticsBoundaryError):
                if method == "read":
                    UAEPorts().read("patterns", SCOPE)
                else:
                    getattr(UAEPorts(), method)("not_a_port")


if __name__ == "__main__":
    unittest.main()
