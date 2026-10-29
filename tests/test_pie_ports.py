"""PIE-MOD-02: dependency capabilities are interface claims, not authorization."""
from dataclasses import FrozenInstanceError, replace
from decimal import Decimal
import sys
import unittest

from intelligence.patterns_insights import (
    CapabilityState, EXPECTED_PEER_WIRES, PIEBoundaryError,
    PIEExecutionSettings, PIEPorts, PORT_SCHEMAS, PortCapability,
)


class FakeUAE:
    def __init__(self, *, peer_schema="uae-to-pie/v1", adapter_schema="pie-uae-reader/v1",
                 state=CapabilityState.DECLARED, fail=False):
        self.peer_schema = peer_schema
        self.adapter_schema = adapter_schema
        self.state = state
        self.fail = fail
        self.probes = 0
        self.read_calls = 0

    def capability(self):
        self.probes += 1
        if self.fail:
            raise RuntimeError("SECRET=NOT_FOR_LOGGING")
        return PortCapability("uae", self.state, self.adapter_schema,
                              self.peer_schema, None if self.state is CapabilityState.DECLARED else "offline")

    def read_qualified_manifest(self, pointer):
        self.read_calls += 1
        raise AssertionError("No provider reads permitted in capabilities-only stage")


class BrokenProvider:
    def capability(self):
        return PortCapability("uae", CapabilityState.DECLARED, "pie-uae-reader/v1", "uae-to-pie/v1")
    # read method intentionally missing


class WrongPort(FakeUAE):
    def capability(self):
        self.probes += 1
        return PortCapability("source_policy", CapabilityState.DECLARED, "pie-source-policy/v1")


class WrongResult(FakeUAE):
    def capability(self):
        self.probes += 1
        return {"state": "declared", "peer_schema": "uae-to-pie/v1"}


class FakeStore:
    def __init__(self):
        self.writes = 0

    def capability(self):
        return PortCapability("store", CapabilityState.DECLARED, "pie-artifact-store/v1")

    def write_immutable(self, project_id, kind, sha256, data):
        self.writes += 1
        raise AssertionError("No persistence in PIE-MOD-02")


class PIEMod02Tests(unittest.TestCase):
    def assert_code(self, code, fn, *args):
        with self.assertRaises(PIEBoundaryError) as caught:
            fn(*args)
        self.assertEqual(caught.exception.code, code)

    def test_standalone_without_uae_or_legacy_imports(self):
        from intelligence import patterns_insights
        self.assertIsNotNone(patterns_insights.PIEPorts)
        ports = PIEPorts()
        self.assertEqual(tuple(status.port for status in ports.report()), tuple(PORT_SCHEMAS))
        for item in ports.report():
            self.assertEqual(item.state, CapabilityState.UNAVAILABLE)
            self.assertEqual(item.reason_code, "not_connected")
            self.assertFalse(item.can_attempt_call)
            self.assertFalse(item.verified_live)
        self.assertNotIn("intelligence.unified_analytics", sys.modules)
        self.assertNotIn("intelligence.analytics", sys.modules)
        self.assert_code("capability_unavailable", ports.require, "uae")

    def test_declared_uae_schema_is_only_metadata_not_qualification(self):
        provider = FakeUAE()
        ports = PIEPorts(uae=provider)
        state = ports.status("uae")
        self.assertEqual(state.state, CapabilityState.DECLARED)
        self.assertEqual(state.expected_peer_schema, "uae-to-pie/v1")
        self.assertEqual(state.observed_peer_schema, "uae-to-pie/v1")
        self.assertTrue(state.can_attempt_call)
        self.assertFalse(state.verified_live)
        self.assertIs(ports.require("uae"), provider)
        self.assertEqual(provider.read_calls, 0)
        self.assertEqual(provider.probes, 2)

    def test_peer_wire_and_adapter_schema_must_match_exactly(self):
        for changes, reason in (
            ({"peer_schema": "uae-pattern-handoff/v1"}, "peer_schema_mismatch"),
            ({"peer_schema": None}, "peer_schema_mismatch"),
            ({"adapter_schema": "pie-uae-reader/v2"}, "adapter_schema_mismatch"),
        ):
            p = FakeUAE(**changes)
            with self.subTest(changes=changes):
                status = PIEPorts(uae=p).status("uae")
                self.assertEqual(status.state, CapabilityState.INCOMPATIBLE)
                self.assertEqual(status.reason_code, reason)
                self.assertFalse(status.can_attempt_call)
                self.assertEqual(p.read_calls, 0)
                self.assert_code("capability_incompatible", PIEPorts(uae=p).require, "uae")

    def test_missing_methods_and_malicious_descriptor_are_rejected(self):
        for p, reason in ((BrokenProvider(), "missing_methods"),
                          (WrongPort(), "invalid_descriptor"),
                          (WrongResult(), "invalid_descriptor")):
            with self.subTest(provider=type(p).__name__):
                st = PIEPorts(uae=p).status("uae")
                self.assertEqual(st.state, CapabilityState.INCOMPATIBLE)
                self.assertEqual(st.reason_code, reason)

    def test_unavailable_probes_sanitize_provider_error(self):
        p = FakeUAE(fail=True)
        report = PIEPorts(uae=p).status("uae")
        self.assertEqual(report.state, CapabilityState.UNAVAILABLE)
        self.assertEqual(report.reason_code, "capability_probe_failed")
        self.assertNotIn("SECRET", repr(report))
        self.assert_code("capability_unavailable", PIEPorts(uae=p).require, "uae")
        self.assertEqual(p.read_calls, 0)

    def test_declared_unavailable_and_incompatible_states_do_not_upgrade(self):
        for state in (CapabilityState.UNAVAILABLE, CapabilityState.INCOMPATIBLE):
            p = FakeUAE(state=state)
            with self.subTest(state=state):
                status = PIEPorts(uae=p).status("uae")
                self.assertEqual(status.state, state)
                self.assertEqual(status.reason_code, "offline")
                self.assertFalse(status.can_attempt_call)

    def test_result_store_is_also_inert_and_no_provider_executes(self):
        store = FakeStore()
        p = FakeUAE()
        ports = PIEPorts(store=store, uae=p)
        report = ports.report()
        self.assertEqual(len(report), 6)
        self.assertEqual(ports.status("store").state, CapabilityState.DECLARED)
        self.assertIs(ports.require("store"), store)
        self.assertEqual(store.writes, 0)
        self.assertEqual(p.read_calls, 0)
        self.assertFalse(any(s.verified_live for s in report))

    def test_all_six_port_protocols_can_be_declared_without_dispatch(self):
        from intelligence.patterns_insights import REQUIRED_METHODS
        instances = {}
        counters = {name: 0 for name in PORT_SCHEMAS}
        for name in PORT_SCHEMAS:
            def probe(self, *, port=name):
                counters[port] += 1
                return PortCapability(port, CapabilityState.DECLARED, PORT_SCHEMAS[port],
                                      EXPECTED_PEER_WIRES[port])
            def never_execute(*args, **kwargs):
                raise AssertionError("capability negotiation must not dispatch")
            attributes = {method: never_execute for method in REQUIRED_METHODS[name]
                          if method != "capability"}
            attributes["capability"] = probe
            instances[name] = type("Fixture_" + name, (), attributes)()
        ports = PIEPorts(**instances)
        self.assertEqual(tuple(s.state for s in ports.report()),
                         (CapabilityState.DECLARED,) * 6)
        self.assertFalse(any(s.verified_live for s in ports.report()))
        self.assertEqual(counters, {name: 2 for name in PORT_SCHEMAS})
        for name, obj in instances.items():
            self.assertIs(ports.require(name), obj)

    def test_failure_reason_and_descriptor_types_strict(self):
        bad = (
            ("missing", CapabilityState.DECLARED, "pie-uae-reader/v1", None, None),
            ("uae", "declared", "pie-uae-reader/v1", "uae-to-pie/v1", None),
            ("uae", CapabilityState.DECLARED, None, None, None),
            ("uae", CapabilityState.DECLARED, "x-unsupported", None, None),
            ("uae", CapabilityState.UNAVAILABLE, None, None, "password=abc"),
            ("uae", CapabilityState.UNAVAILABLE, None, None, None),
            ("uae", CapabilityState.DECLARED, "pie-uae-reader/v1", None, "unexpected"),
        )
        for args in bad:
            with self.subTest(args=args):
                self.assert_code("invalid_capability" if args[0] == "uae" else "invalid_port",
                                 PortCapability, *args)
        self.assert_code("invalid_port", PIEPorts().status, "unknown")
        self.assert_code("invalid_port", PIEPorts().status, None)

    def test_capability_immutable_version_pinned_and_repeatable(self):
        ports = PIEPorts(uae=FakeUAE())
        before = ports.report()
        self.assertEqual(before, ports.report())
        self.assertEqual(EXPECTED_PEER_WIRES["uae"], "uae-to-pie/v1")
        with self.assertRaises(FrozenInstanceError):
            before[0].state = CapabilityState.UNAVAILABLE
        with self.assertRaises(TypeError):
            PORT_SCHEMAS["uae"] = "override/v99"
        self.assertEqual(replace(before[0], state=CapabilityState.UNAVAILABLE).state,
                         CapabilityState.UNAVAILABLE)

    def test_safe_execution_defaults_and_explicit_spend_limits(self):
        s = PIEExecutionSettings()
        self.assertFalse(s.enable_workers)
        self.assertFalse(s.enable_paid_model)
        self.assertIsNone(s.approved_spend_limit_usd)
        self.assert_code("spend_approval_required", PIEExecutionSettings,
                         False, True, None)
        for amount in (Decimal("NaN"), Decimal("Infinity"), Decimal(0), Decimal(-1),
                       1.0, "10", True):
            with self.subTest(amount=amount):
                self.assert_code("invalid_settings", PIEExecutionSettings,
                                 False, False, amount)
        valid = PIEExecutionSettings(enable_paid_model=True,
                                     approved_spend_limit_usd=Decimal("12.50"))
        self.assertEqual(valid.approved_spend_limit_usd, Decimal("12.50"))
        for b in (0, 1, "true", None):
            self.assert_code("invalid_settings", PIEExecutionSettings, b, False, None)


if __name__ == "__main__":
    unittest.main()
