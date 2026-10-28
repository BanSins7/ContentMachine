"""PIE-MOD-05 mock signed UAE transport and read-time gate (NO production data)."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone, timedelta
import hmac
from hashlib import sha256
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from intelligence.patterns_insights import PIEBoundaryError, PIEDatasetPointer
from intelligence.patterns_insights.intake.uae import (
    HMACReceiptVerifier, UAEExportDescriptor, UAEExportEnvelope,
    UAEExportReceipt, UAERightsDecision, UAEReadBridge,
)
from intelligence.patterns_insights.ports import PIEPorts, CapabilityState

FIXTURE = Path(__file__).parent / 'fixtures' / 'pie' / 'uae_to_pie_v1_positive.json'
DATA = json.loads(FIXTURE.read_text('utf-8'))
POINTER = PIEDatasetPointer(**DATA['pointer'])
WIRE = json.dumps(DATA['manifest'], sort_keys=True, ensure_ascii=False,
                  separators=(',', ':')).encode('utf-8')
SECRET = b'PIE-MOD-05-UNIT-ONLY-NOT-A-PRODUCTION-KEY-00000'
ISSUER = 'uae-test-fixture'
NOW = datetime(2026, 10, 26, 12, 0, 0, tzinfo=timezone.utc)


def sign(receipt):
    message = json.dumps(receipt.signed_fields(), sort_keys=True,
                         separators=(',', ':'), ensure_ascii=False).encode('utf-8')
    return replace(receipt, signature=hmac.new(SECRET, message, sha256).hexdigest())


class Gateway:
    def __init__(self):
        self.descriptions = 0
        self.reads = 0
        self.descriptor = UAEExportDescriptor(
            'uae-to-pie/v1', POINTER.uae_engine_version, ISSUER,
            True, True, True, True,
        )
        self.mutate = lambda x: x
        self.fail = False

    def describe(self):
        self.descriptions += 1
        return self.descriptor

    def export_qualified_manifest(self, pointer, challenge):
        self.reads += 1
        if self.fail:
            raise RuntimeError('SECRET provider failure')
        receipt = UAEExportReceipt(
            POINTER.project_id, POINTER.source_id, POINTER.source_policy_version_id,
            POINTER.uae_engine_version, DATA['manifest']['producer']['run_id'],
            POINTER.uae_manifest_sha256, POINTER.uae_dataset_sha256, challenge,
            (NOW - timedelta(minutes=2)).isoformat(),
            (NOW + timedelta(minutes=2)).isoformat(), ISSUER,
            'fixture-key', '0' * 64,
        )
        result = UAEExportEnvelope(WIRE, sign(receipt))
        return self.mutate(result)


class Rights:
    def __init__(self):
        self.calls = 0
        self.granted = True
        self.last_checked = NOW - timedelta(minutes=1)
        self.mutate = lambda x: x
        self.fail = False

    def check_dataset(self, pointer, dataset, challenge):
        self.calls += 1
        if self.fail:
            raise RuntimeError('SECRET db credentials')
        result = UAERightsDecision(pointer.project_id, pointer.source_id,
                                   pointer.source_policy_version_id,
                                   dataset.manifest_sha256, challenge, self.granted,
                                   self.last_checked.isoformat())
        return self.mutate(result)


class PIEUAEHandshakeTests(unittest.TestCase):
    def setUp(self):
        self.gateway, self.rights = Gateway(), Rights()
        self.verifier = HMACReceiptVerifier({'fixture-key': SECRET})
        self.bridge = UAEReadBridge(self.gateway, self.verifier, self.rights, now=lambda: NOW)

    def denied(self, code, fn, *args):
        with self.assertRaises(PIEBoundaryError) as got:
            fn(*args)
        self.assertEqual(got.exception.code, code)

    def test_real_upstream_absent_fails_closed(self):
        bridge = UAEReadBridge()
        self.assertEqual(bridge.capability().state, CapabilityState.UNAVAILABLE)
        self.denied('capability_unavailable', bridge.read_qualified_manifest, POINTER)
        self.denied('capability_unavailable', PIEPorts(uae=bridge).require, 'uae')

    def test_no_verifier_or_rights_adapter_never_reads(self):
        for verifier, rights in ((None, self.rights), (self.verifier, None)):
            gateway = Gateway()
            bridge = UAEReadBridge(gateway, verifier, rights)
            self.assertEqual(bridge.capability().state, CapabilityState.UNAVAILABLE)
            self.denied('capability_unavailable', bridge.read_qualified_manifest, POINTER)
            self.assertEqual(gateway.reads, 0)

    def test_verified_fixture_is_only_untrusted_inference_candidate(self):
        self.assertEqual(self.bridge.capability().state, CapabilityState.DECLARED)
        self.assertEqual(PIEPorts(uae=self.bridge).status('uae').state, CapabilityState.DECLARED)
        result = self.bridge.read_qualified_manifest(POINTER)
        self.assertEqual(result.dataset.sample_size, 2)
        self.assertEqual(result.dataset.manifest_sha256, POINTER.uae_manifest_sha256)
        self.assertFalse(result.verified_live)
        self.assertFalse(result.ready_for_analysis)
        self.assertFalse(result.dataset.verified_live)
        self.assertEqual(self.rights.calls, 1)
        self.assertEqual(self.gateway.reads, 1)

    def test_capability_does_not_fetch_data_or_check_rights(self):
        self.bridge.capability()
        PIEPorts(uae=self.bridge).status('uae')
        self.assertEqual(self.gateway.reads, 0)
        self.assertEqual(self.rights.calls, 0)

    def test_legacy_summary_wire_mismatch_even_if_capability_advertised(self):
        self.gateway.descriptor = replace(self.gateway.descriptor,
                                          peer_schema='uae-pattern-handoff/v1')
        self.assertEqual(self.bridge.capability().state, CapabilityState.INCOMPATIBLE)
        self.denied('capability_incompatible', self.bridge.read_qualified_manifest, POINTER)
        self.assertEqual(self.gateway.reads, 0)

    def test_missing_read_gate_flags_refused(self):
        for name in ('row_level','qualified_only','read_time_rights','signed_receipts'):
            self.gateway.descriptor = replace(self.gateway.descriptor, **{name: False})
            with self.subTest(name=name):
                self.assertEqual(self.bridge.capability().state, CapabilityState.INCOMPATIBLE)
                self.denied('capability_incompatible', self.bridge.read_qualified_manifest, POINTER)
            self.gateway.descriptor = replace(self.gateway.descriptor, **{name: True})
        self.assertEqual(self.gateway.reads, 0)

    def test_producer_version_mismatch(self):
        self.gateway.descriptor = replace(self.gateway.descriptor, producer_version='different/v2')
        self.denied('capability_incompatible', self.bridge.read_qualified_manifest, POINTER)
        self.assertEqual(self.gateway.reads, 0)

    def test_tampered_signed_receipt_fails_before_rights(self):
        self.gateway.mutate = lambda env: UAEExportEnvelope(env.raw_manifest,
            replace(env.receipt, manifest_sha256='f'*64))
        self.denied('receipt_mismatch', self.bridge.read_qualified_manifest, POINTER)
        self.assertEqual(self.rights.calls, 0)

    def test_wrong_key_rejected(self):
        bridge = UAEReadBridge(self.gateway, HMACReceiptVerifier({'wrong-key': SECRET}),
                               self.rights, now=lambda: NOW)
        self.denied('receipt_untrusted', bridge.read_qualified_manifest, POINTER)
        self.assertEqual(self.rights.calls, 0)

    def test_receipt_replay_nonce_rejected(self):
        self.gateway.mutate = lambda env: UAEExportEnvelope(env.raw_manifest,
            sign(replace(env.receipt, challenge='a'*64)))
        with patch('intelligence.patterns_insights.intake.uae.secrets.token_hex',return_value='b'*64):
            self.denied('receipt_mismatch', self.bridge.read_qualified_manifest, POINTER)
        self.assertEqual(self.rights.calls, 0)

    def test_receipt_expiry_and_future_issue_rejected(self):
        for issued, expiry in ((NOW-timedelta(minutes=10),NOW-timedelta(minutes=1)),
                               (NOW+timedelta(minutes=1),NOW+timedelta(minutes=10))):
            def mutate(env):
                receipt = sign(replace(env.receipt, issued_at=issued.isoformat(),
                                       expires_at=expiry.isoformat()))
                return UAEExportEnvelope(env.raw_manifest, receipt)
            self.gateway.mutate=mutate
            self.denied('receipt_expired', self.bridge.read_qualified_manifest, POINTER)
        self.assertEqual(self.rights.calls, 0)

    def test_body_tampering_and_descriptive_summary_rejected(self):
        bad = json.dumps({'schema_version':'uae-pattern-handoff/v1','summary':{'sample_size':2000}}).encode()
        self.gateway.mutate=lambda env: UAEExportEnvelope(bad, env.receipt)
        self.denied('invalid_manifest_shape', self.bridge.read_qualified_manifest, POINTER)
        self.assertEqual(self.rights.calls, 0)

    def test_producer_run_id_must_match_signed_manifest(self):
        self.gateway.mutate=lambda env: UAEExportEnvelope(env.raw_manifest,
            sign(replace(env.receipt, producer_run_id='bad-run-id')))
        self.denied('receipt_mismatch', self.bridge.read_qualified_manifest, POINTER)
        self.assertEqual(self.rights.calls, 0)

    def test_rights_revoked_immediately_blocks_every_new_read(self):
        self.assertEqual(self.bridge.read_qualified_manifest(POINTER).dataset.sample_size,2)
        self.rights.granted = False
        self.denied('rights_denied', self.bridge.read_qualified_manifest, POINTER)
        self.assertEqual(self.rights.calls, 2)

    def test_wrong_project_policy_and_dataset_rights_denied(self):
        for updates in ({'project_id':'b'*32}, {'source_policy_version_id':999},
                        {'manifest_sha256':'d'*64}, {'source_id':'not-source'}, {'challenge':'f'*64}):
            self.rights.mutate=lambda decision:replace(decision,**updates)
            with self.subTest(updates=updates):
                self.denied('rights_scope_mismatch',self.bridge.read_qualified_manifest, POINTER)
        self.assertEqual(self.rights.calls, 5)

    def test_stale_rights_before_receipt_or_future_refused(self):
        for when in (NOW-timedelta(minutes=3),NOW+timedelta(seconds=1)):
            self.rights.last_checked=when
            self.denied('rights_stale',self.bridge.read_qualified_manifest, POINTER)

    def test_dependency_failures_do_not_leak_exception_text(self):
        self.gateway.fail=True
        try:
            self.bridge.read_qualified_manifest(POINTER)
        except PIEBoundaryError as ex:
            self.assertEqual(ex.code,'uae_read_failed')
            self.assertNotIn('SECRET',str(ex))
        self.gateway.fail=False
        self.rights.fail=True
        try:
            self.bridge.read_qualified_manifest(POINTER)
        except PIEBoundaryError as ex:
            self.assertEqual(ex.code,'rights_check_failed')
            self.assertNotIn('SECRET',str(ex))

    def test_unsigned_or_incomplete_gateway_rejected(self):
        self.gateway.mutate=lambda env: object()
        self.denied('invalid_export',self.bridge.read_qualified_manifest,POINTER)
        self.assertEqual(self.rights.calls, 0)

    def test_no_weak_hmac_key_or_wrong_bools(self):
        for invalid in ({},{'fixture-key':b'a'}, {'bad key':SECRET}):
            self.denied('invalid_verifier', HMACReceiptVerifier, invalid)
        self.denied('invalid_handshake', UAEExportDescriptor,
                    'uae-to-pie/v1','uae-fixture/v1',ISSUER,1,True,True,True)


if __name__ == '__main__':
    unittest.main()
