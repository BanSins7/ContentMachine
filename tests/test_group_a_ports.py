"""Pure GA-MOD-02 adapter boundary tests; no DB, network, FFmpeg or QDP imports."""
from __future__ import annotations

from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch
from uuid import uuid4

from intelligence.group_a.adapters.unavailable import (
    PortUnavailable, MissingAdapter, UTCClock, disconnected_ports,
)
from intelligence.group_a.capabilities import (
    capability_snapshot, live_handoff_ready, require_live_handoff,
)
from intelligence.group_a.config import GASettings, flag_value, settings_from_environment
from intelligence.group_a.contracts import GroupAError
from intelligence.group_a.contracts_v1 import ContractViolation, canonical_sha256
from intelligence.group_a.ports import (
    PEER_VERSIONS, REQUIRED_EXECUTION_PORTS, GAPorts, QDPReader,
    MediaCatalog, PeerCapability, STATE_AVAILABLE, STATE_INCOMPATIBLE,
    negotiate, unavailable,
)
from intelligence.group_a.service import GroupAService, NotReady, readiness


PROJECT = "a" * 32
RUN = str(uuid4())
EVENT = str(uuid4())
INPUT = "b" * 64


def pointer():
    return {
        "schema_version": "qdp-handoff/v1", "event_id": EVENT,
        "project_id": PROJECT, "qdp_run_id": RUN,
        "video_id": 77, "analysis_group": "A",
        "input_sha256": INPUT,
    }


def manifest():
    content = {
        "schema_version": "ga-media-manifest/v1", "project_id": PROJECT,
        "qdp_run_id": RUN, "video_id": 77, "qdp_input_sha256": INPUT,
        "content_kind": "video", "source_id": "src:fixture",
        "source_policy_version_id": 100,
        "verified_at": "2026-10-26T09:00:00Z",
        "items": [{
            "index": 1, "kind": "video", "media_asset_id": 5,
            "sha256": "c" * 64, "byte_size": 160,
            "verified_local_path_ref": "ga-local:fixture",
            "duration_sec": "2.000",
        }],
    }
    return dict(content, manifest_sha256=canonical_sha256(content))


class FakeReader:
    def __init__(self, port, *, version=None, answer=None):
        self.port = port
        self.version = PEER_VERSIONS[port] if version is None else version
        self.answer = answer
        self.calls = 0

    def capability(self):
        return negotiate(self.port, self.version)

    def read_preparation(self, pointer):
        self.calls += 1
        return self.answer

    def read_verified_manifest(self, pointer):
        self.calls += 1
        return self.answer

    def read_verified(self, media_sha256):
        self.calls += 1
        return self.answer


class FakeSink:
    def __init__(self, port):
        self.port = port
        self.writes = []

    def capability(self):
        return negotiate(self.port, PEER_VERSIONS[self.port])

    def write_immutable(self, result):
        self.writes.append(result)
        raise AssertionError("No A003 writes allowed during GA-MOD-02")

    def publish_once(self, event):
        self.writes.append(event)
        raise AssertionError("No Group B handoff allowed during GA-MOD-02")

    def enqueue_once(self, task):
        self.writes.append(task)
        raise AssertionError("No jobs allowed during GA-MOD-02")


class FrozenClock:
    def utcnow(self):
        return datetime(2026, 10, 26, 9, tzinfo=timezone.utc)


def fake_ports(*, prepared=None, verified=None, qdp_version=None):
    q = FakeReader("qdp", version=qdp_version,
                   answer={"pointer": pointer(), "state": "ready"} if prepared is None else prepared)
    m = FakeReader("media", answer=manifest() if verified is None else verified)
    tx, vs = FakeReader("transcript"), FakeReader("vision")
    w, h, jobs = FakeSink("results"), FakeSink("handoff"), FakeSink("queue")
    ports = GAPorts(qdp=q, media=m, transcript=tx, vision=vs,
                    results=w, handoff=h, clock=FrozenClock(), queue=jobs)
    return ports, q, m, (w, h, jobs)


class GroupAPortTests(unittest.TestCase):
    def test_no_peers_default_diagnostic_is_fail_closed(self):
        ports = disconnected_ports()
        for name in PEER_VERSIONS:
            self.assertEqual(getattr(ports, name).capability().state, "unavailable")
            with self.subTest(name=name), self.assertRaises(PortUnavailable):
                getattr(ports, name).read_verified("a" * 64)
        self.assertEqual(UTCClock().utcnow().tzinfo, timezone.utc)
        self.assertTrue(isinstance(ports.qdp, QDPReader))
        self.assertTrue(isinstance(ports.media, MediaCatalog))
        svc = GroupAService()
        diagnostic = svc.capabilities()
        self.assertEqual(diagnostic["state"], "unavailable")
        self.assertIn("qdp_unavailable", diagnostic["reason_codes"])
        self.assertIn("durable_handoff_not_installed", diagnostic["reason_codes"])
        self.assertEqual(diagnostic["peers"]["qdp"]["observed_version"], None)
        json.dumps(diagnostic)
        self.assertFalse(live_handoff_ready())
        self.assertEqual(capability_snapshot()["adapter_readiness"]["state"], "unavailable")
        with self.assertRaises(GroupAError):
            require_live_handoff()

    def test_capability_versions_are_exact(self):
        for port, expected in PEER_VERSIONS.items():
            self.assertEqual(negotiate(port, expected).state, STATE_AVAILABLE)
            self.assertEqual(negotiate(port, expected + "-new").state, STATE_INCOMPATIBLE)
            self.assertEqual(negotiate(port, None).state, "unavailable")
            with self.assertRaises(ValueError):
                PeerCapability(port, "available", expected + "/wrong")
            with self.assertRaises(ValueError):
                PeerCapability(port, "available", expected, "unexpected")
        with self.assertRaises(ValueError):
            negotiate("foreign", "v1")
        with self.assertRaises(ValueError):
            negotiate("qdp", 123)
        self.assertEqual(unavailable("qdp").reason_code, "adapter_not_connected")

    def test_flags_explicit_and_not_live_permission(self):
        self.assertFalse(GASettings().enabled)
        for value in ("on", "YES", "1", "true"):
            self.assertTrue(flag_value(value, name="test"))
        for value in ("off", "no", "0", "false"):
            self.assertFalse(flag_value(value, name="test"))
        for value in ("", "enabled", "maybe", "2", None, True):
            with self.subTest(value=value), self.assertRaises(ValueError):
                flag_value(value, name="test")
        flags = settings_from_environment({
            "CM_GROUP_A_ENABLED": "true",
            "CM_GROUP_A_QDP_HANDOFF_ENABLED": "true",
            "CM_GROUP_A_PSI_REUSE_ENABLED": "false",
        })
        self.assertTrue(flags.enabled and flags.qdp_handoff_enabled)
        self.assertFalse(flags.allow_psi_reuse)
        self.assertFalse(live_handoff_ready())
        self.assertIn("durable_handoff_not_installed",
                      readiness(fake_ports()[0], flags))
        with self.assertRaises(ValueError):
            GASettings(enabled=1)

    def test_plan_reads_neither_peers_nor_database_by_default(self):
        ports, q, m, sinks = fake_ports()
        svc = GroupAService(ports, GASettings())
        plan = svc.inspect(pointer()).wire()
        self.assertEqual(plan["state"], "unavailable")
        self.assertIsNone(plan["manifest_sha256"])
        self.assertEqual(q.calls, 0)
        self.assertEqual(m.calls, 0)
        self.assertEqual([len(x.writes) for x in sinks], [0, 0, 0])
        with self.assertRaises(NotReady) as ctx:
            svc.start(pointer())
        self.assertIn("durable_handoff_not_installed", ctx.exception.reason_codes)
        self.assertEqual([len(x.writes) for x in sinks], [0, 0, 0])

    def test_fakes_may_validate_current_readiness_without_starting_jobs(self):
        ports, q, m, sinks = fake_ports()
        svc = GroupAService(ports, GASettings(enabled=True,qdp_handoff_enabled=True))
        plan = svc.validate_authoritative_pointer(pointer()).wire()
        self.assertEqual(plan["state"], "plan_only")
        self.assertEqual(plan["manifest_sha256"], manifest()["manifest_sha256"])
        self.assertEqual(q.calls, 1)
        self.assertEqual(m.calls, 1)
        self.assertEqual([len(x.writes) for x in sinks], [0, 0, 0])
        self.assertEqual(svc.inspect(pointer()).state, "unavailable")

    def test_unavailable_peer_never_reads_preparation(self):
        ports, q, m, sinks = fake_ports(qdp_version="qdp-handoff/v9")
        svc = GroupAService(ports, GASettings(enabled=True,qdp_handoff_enabled=True))
        plan = svc.validate_authoritative_pointer(pointer())
        self.assertEqual(plan.state, "unavailable")
        self.assertEqual(q.calls + m.calls, 0)
        self.assertEqual([len(x.writes) for x in sinks], [0,0,0])

    def test_revoked_snapshot_fails_closed_and_does_not_read_media(self):
        ports, q, m, _ = fake_ports(prepared={"pointer": pointer(), "state": "blocked"})
        result = GroupAService(ports).validate_authoritative_pointer(pointer())
        self.assertEqual(result.state, "blocked")
        self.assertEqual(result.reason_codes, ("preparation_changed_or_revoked",))
        self.assertEqual(m.calls, 0)

    def test_foreign_project_or_media_revision_is_rejected(self):
        pointer_mut = dict(pointer(), project_id="e" * 32)
        ports, q, m, _ = fake_ports()
        self.assertEqual(
            GroupAService(ports).validate_authoritative_pointer(pointer_mut).state, "blocked"
        )
        ports, _, _, _ = fake_ports(verified=dict(manifest(), video_id=88))
        with self.assertRaises(ContractViolation) as ctx:
            GroupAService(ports).validate_authoritative_pointer(pointer())
        self.assertEqual(ctx.exception.code, "manifest_digest_mismatch")
        # Version-matched but foreign manifest must also be denied after rehash.
        foreign = dict(manifest(), video_id=88)
        foreign["manifest_sha256"] = canonical_sha256({
            k:v for k,v in foreign.items() if k != "manifest_sha256"
        })
        ports, _, _, _ = fake_ports(verified=foreign)
        with self.assertRaises(ContractViolation) as ctx:
            GroupAService(ports).validate_authoritative_pointer(pointer())
        self.assertEqual(ctx.exception.code, "lineage_mismatch")

    def test_peer_failures_are_explicit_unavailable_without_writes(self):
        ports, q, m, sinks = fake_ports()
        def fail(_):
            raise ConnectionError("hidden internal provider URL must not leak")
        q.read_preparation = fail
        plan = GroupAService(ports).validate_authoritative_pointer(pointer())
        self.assertEqual(plan.state, "unavailable")
        self.assertEqual(plan.reason_codes, ("qdp_read_failed",))
        self.assertEqual(m.calls, 0)
        ports, q, m, sinks = fake_ports()
        m.read_verified_manifest = fail
        plan = GroupAService(ports).validate_authoritative_pointer(pointer())
        self.assertEqual(plan.state, "unavailable")
        self.assertEqual(plan.reason_codes, ("media_read_failed",))
        self.assertEqual([len(x.writes) for x in sinks], [0, 0, 0])

    def test_no_import_or_ambient_env_dependency(self):
        # Never import private upstream objects even if a hostile codepath asks.
        from intelligence.group_a import ports, service, capabilities
        self.assertNotIn("intelligence.qualified_data", ports.__dict__)
        self.assertNotIn("intelligence.project_sources", service.__dict__)
        with patch.dict("os.environ", {
            "CM_GROUP_A_ENABLED": "1",
            "CM_GROUP_A_QDP_HANDOFF_ENABLED": "1",
        }):
            self.assertFalse(capabilities.live_handoff_ready())


if __name__ == "__main__":
    unittest.main()
