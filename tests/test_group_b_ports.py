"""GB-MOD-02: pure offline ports, independent import and fail-closed cases."""
from __future__ import annotations
import subprocess
import sys
import unittest
from decimal import Decimal

from intelligence.group_b.architecture import BOUNDARIES
from intelligence.group_b.configuration import GroupBConfig
from intelligence.group_b.errors import (
    DependencyContractMismatch, DependencyProbeFailed, DependencyUnavailable,
    ExecutionNotApproved,
)
from intelligence.group_b.ports import (
    EXPECTED_CONTRACTS, PORT_NAMES, GroupBPorts, PortCapability, PortState,
)


class FakeQDP:
    def __init__(self, *, available=True, contract=None, fails=False):
        self.calls = []
        self.available = available
        self.contract = contract or EXPECTED_CONTRACTS["qdp"]
        self.fails = fails

    def capability(self):
        self.calls.append("capability")
        if self.fails:
            raise RuntimeError("SENSITIVE provider secret")
        return PortCapability(self.contract, self.available, "disabled" if not self.available else "")

    def fetch_preparation(self, *, project_id, video_id, qdp_run_id):
        self.calls.append("fetch_preparation")
        return {"untrusted": True}

    def acknowledge(self, *, event_id, durable_receipt):
        self.calls.append("acknowledge")


class FakeModel:
    def __init__(self):
        self.calls = []

    def capability(self):
        self.calls.append("capability")
        return PortCapability(EXPECTED_CONTRACTS["model"], True)

    def quote(self, *, model, evidence_manifest):
        self.calls.append("quote")
        return {"price": "unknown"}

    def invoke_approved(self, *, approved_attempt, evidence_manifest):
        self.calls.append("invoke_approved")
        raise RuntimeError("Must not run during handshake")


class TestGroupBPorts(unittest.TestCase):
    def test_all_eight_empty_ports_are_unavailable(self):
        ports = GroupBPorts()
        self.assertEqual(len(ports.report()), 8)
        self.assertEqual(tuple(PORT_NAMES), tuple(EXPECTED_CONTRACTS))
        self.assertTrue(all(x.state is PortState.UNAVAILABLE for x in ports.report()))
        for name in PORT_NAMES:
            with self.subTest(name=name), self.assertRaises(DependencyUnavailable) as error:
                ports.require(name)
            self.assertEqual(error.exception.code, "dependency_unavailable")
        with self.assertRaises(ValueError):
            ports.status("foreign")

    def test_declared_is_not_remote_ready_and_never_calls_model(self):
        qdp, model = FakeQDP(), FakeModel()
        ports = GroupBPorts(qdp=qdp, model=model)
        self.assertEqual(ports.status("qdp").state, PortState.DECLARED)
        self.assertTrue(ports.status("model").can_attempt_handshake)
        self.assertIs(ports.require("qdp"), qdp)
        self.assertEqual(set(qdp.calls), {"capability"})
        self.assertEqual(model.calls, ["capability"])
        self.assertNotIn("acknowledge", qdp.calls)
        self.assertNotIn("invoke_approved", model.calls)
        self.assertEqual(ports.status("analytics").state, PortState.UNAVAILABLE)

    def test_wrong_version_missing_methods_and_invalid_probe(self):
        wrong = GroupBPorts(qdp=FakeQDP(contract="qdp-handoff/v0"))
        self.assertEqual(wrong.status("qdp").reason, "version_mismatch")
        with self.assertRaises(DependencyContractMismatch):
            wrong.require("qdp")
        missing = GroupBPorts(qdp=object())
        self.assertEqual(missing.status("qdp").reason, "missing_protocol_methods")
        with self.assertRaises(DependencyContractMismatch):
            missing.require("qdp")
        class BadProbe(FakeQDP):
            def capability(self):
                return {"available": True}
        self.assertEqual(GroupBPorts(qdp=BadProbe()).status("qdp").reason,
                         "invalid_probe_response")

    def test_failed_or_unavailable_probe_does_not_leak_secrets(self):
        ports = GroupBPorts(qdp=FakeQDP(fails=True))
        self.assertEqual(ports.status("qdp").reason, "probe_failed")
        self.assertNotIn("SENSITIVE", repr(ports.status("qdp")))
        with self.assertRaises(DependencyProbeFailed) as error:
            ports.require("qdp")
        self.assertNotIn("SENSITIVE", str(error.exception))
        off = GroupBPorts(qdp=FakeQDP(available=False))
        self.assertEqual(off.status("qdp").reason, "provider_unavailable")
        with self.assertRaises(DependencyUnavailable):
            off.require("qdp")

    def test_paid_calls_default_disabled_even_if_flag_is_enabled(self):
        cfg = GroupBConfig()
        self.assertFalse(cfg.enable_paid_execution)
        self.assertEqual(cfg.max_paid_attempt_usd, Decimal("0"))
        with self.assertRaises(ExecutionNotApproved):
            cfg.require_paid_approval()
        enabled = GroupBConfig(enable_paid_execution=True,
                               max_paid_attempt_usd=Decimal("5"))
        with self.assertRaises(ExecutionNotApproved):
            enabled.require_paid_approval()
        bad = (
            {"enable_paid_execution": 1}, {"max_paid_attempt_usd": 1},
            {"max_paid_attempt_usd": Decimal("NaN")},
            {"max_paid_attempt_usd": Decimal("Infinity")},
            {"max_paid_attempt_usd": Decimal("-1")},
            {"max_paid_attempt_usd": Decimal("1")},
            {"max_context_bytes": 0}, {"max_media_refs": False},
            {"max_attempts": -1},
        )
        for kwargs in bad:
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                GroupBConfig(**kwargs)

    def test_seven_domains_are_local_and_use_only_declared_ports(self):
        self.assertEqual(set(BOUNDARIES), {"intake", "classify", "deterministic",
            "validation", "storage", "review", "delivery"})
        for domain in BOUNDARIES.values():
            self.assertTrue(set(domain.ports) <= set(PORT_NAMES))

    def test_import_without_any_peer_modules(self):
        code = (
            "import intelligence.group_b; import intelligence.group_b.ports; "
            "import intelligence.group_b.adapters; import sys; "
            "assert not any(n.startswith(('intelligence.group_a', "
            "'intelligence.qualified_data', 'intelligence.analytics')) "
            "for n in sys.modules)"
        )
        child = subprocess.run([sys.executable, "-c", code], capture_output=True,
                               text=True, timeout=20)
        self.assertEqual(child.returncode, 0, child.stderr)


if __name__ == "__main__":
    unittest.main()
