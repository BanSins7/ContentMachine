"""PIE-MOD-05: explicit, fail-closed UAE research-manifest handshake.

This is the PIE receiving adapter, NOT a UAE provider. The current upstream UAE
branch has no row-level research export. The default bridge is unavailable.
Capability metadata alone is never authorization. A configured external gateway
must supply a scoped manifest, an independently verifiable one-use receipt and
an independently configured current-rights decision. Those checks still do not
replace future row-level provenance checks (PIE-MOD-06).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import re
import secrets
from typing import Callable, Mapping, Protocol

from ..contracts import PIEBoundaryError, PIEDatasetPointer, canonical_hash
from ..dataset_contract import MANIFEST_SCHEMA, PIEValidatedDataset, load_dataset_json
from ..ports import CapabilityState, PortCapability, PORT_SCHEMAS

_HEX = re.compile(r'[0-9a-f]{64}\Z')
_TOKEN = re.compile(r'[A-Za-z0-9._:/-]{1,160}\Z')
_REQUIRED_RECEIPT = (
    'project_id', 'source_id', 'source_policy_version_id', 'producer_version',
    'producer_run_id', 'manifest_sha256', 'dataset_sha256', 'challenge',
    'issued_at', 'expires_at', 'issuer', 'key_id',
)


def _deny(code: str, detail: str) -> None:
    raise PIEBoundaryError(code, detail)


def _clock(value: str) -> datetime:
    if type(value) is not str or len(value) > 40 or 'T' not in value:
        _deny('invalid_receipt', 'Expected explicit UTC-offset timestamp')
    try:
        result = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise PIEBoundaryError('invalid_receipt', 'Invalid receipt timestamp') from exc
    if result.tzinfo is None or result.utcoffset() is None:
        _deny('invalid_receipt', 'Receipt timestamp lacks timezone')
    return result


@dataclass(frozen=True, slots=True)
class UAEExportDescriptor:
    """Untrusted peer capability assertion, NOT evidence UAE implemented it."""
    peer_schema: str
    producer_version: str
    issuer: str
    row_level: bool
    qualified_only: bool
    read_time_rights: bool
    signed_receipts: bool

    def __post_init__(self) -> None:
        if any(type(x) is not str or not _TOKEN.fullmatch(x)
               for x in (self.peer_schema, self.producer_version, self.issuer)):
            _deny('invalid_handshake', 'Invalid UAE producer identity/schema')
        if any(type(getattr(self, x)) is not bool for x in (
            'row_level', 'qualified_only', 'read_time_rights', 'signed_receipts'
        )):
            _deny('invalid_handshake', 'Capabilities must be explicit booleans')


@dataclass(frozen=True, slots=True)
class UAEExportReceipt:
    """Exact identity/version/nonce claims to be verified by an external key."""
    project_id: str
    source_id: str
    source_policy_version_id: int
    producer_version: str
    producer_run_id: str
    manifest_sha256: str
    dataset_sha256: str
    challenge: str
    issued_at: str
    expires_at: str
    issuer: str
    key_id: str
    signature: str

    def __post_init__(self) -> None:
        for attr in ('manifest_sha256', 'dataset_sha256', 'challenge', 'signature'):
            value = getattr(self, attr)
            if type(value) is not str or not _HEX.fullmatch(value):
                _deny('invalid_receipt', 'Invalid receipt digest')
        for attr in ('issuer', 'key_id', 'producer_version', 'producer_run_id', 'source_id'):
            value = getattr(self, attr)
            if type(value) is not str or not _TOKEN.fullmatch(value):
                _deny('invalid_receipt', 'Invalid receipt token')
        if (type(self.project_id) is not str or not re.fullmatch('[0-9a-f]{32}', self.project_id)
                or type(self.source_policy_version_id) is not int or self.source_policy_version_id <= 0):
            _deny('invalid_receipt', 'Invalid receipt scope')
        if _clock(self.expires_at) <= _clock(self.issued_at):
            _deny('invalid_receipt', 'Receipt expiration must follow issuance')

    def signed_fields(self) -> dict:
        return {name: getattr(self, name) for name in _REQUIRED_RECEIPT}


@dataclass(frozen=True, slots=True)
class UAEExportEnvelope:
    raw_manifest: bytes
    receipt: UAEExportReceipt

    def __post_init__(self) -> None:
        if type(self.raw_manifest) is not bytes or not self.raw_manifest:
            _deny('invalid_export', 'Expected bounded UTF-8 manifest bytes')
        if not isinstance(self.receipt, UAEExportReceipt):
            _deny('invalid_export', 'Typed export receipt required')


@dataclass(frozen=True, slots=True)
class UAERightsDecision:
    """Claim from an independently injected current source-rights checker.

    A mock affirmative decision is NOT a production permission or a DB read gate.
    Only PIE-MOD-06 can promote checked data after complete lineage inspection.
    """
    project_id: str
    source_id: str
    source_policy_version_id: int
    manifest_sha256: str
    challenge: str
    granted: bool
    checked_at: str

    def __post_init__(self) -> None:
        if type(self.project_id) is not str or not re.fullmatch('[0-9a-f]{32}', self.project_id):
            _deny('invalid_rights_decision', 'Invalid project')
        if type(self.source_id) is not str or not _TOKEN.fullmatch(self.source_id):
            _deny('invalid_rights_decision', 'Invalid source')
        if type(self.source_policy_version_id) is not int or self.source_policy_version_id <= 0:
            _deny('invalid_rights_decision', 'Invalid policy version')
        if type(self.manifest_sha256) is not str or not _HEX.fullmatch(self.manifest_sha256):
            _deny('invalid_rights_decision', 'Invalid manifest identity')
        if type(self.challenge) is not str or not _HEX.fullmatch(self.challenge):
            _deny('invalid_rights_decision', 'Invalid per-request rights challenge')
        if type(self.granted) is not bool:
            _deny('invalid_rights_decision', 'Rights state must be boolean')
        _clock(self.checked_at)


@dataclass(frozen=True, slots=True)
class UAEReadResult:
    dataset: PIEValidatedDataset
    issuer: str
    receipt_sha256: str
    rights_checked_at: str

    @property
    def verified_live(self) -> bool:
        # Data-contract and producer-receipt checks do not prove ALL current
        # upstream project/media/QDP/SRE/A/B/C evidence is authorized.
        return False

    @property
    def ready_for_analysis(self) -> bool:
        return False


class UAETransport(Protocol):
    def describe(self) -> UAEExportDescriptor: ...
    def export_qualified_manifest(self, pointer: PIEDatasetPointer, challenge: str) -> UAEExportEnvelope: ...


class UAEReceiptVerifier(Protocol):
    def verify(self, receipt: UAEExportReceipt) -> bool: ...


class CurrentRightsAuthority(Protocol):
    def check_dataset(self, pointer: PIEDatasetPointer,
                      dataset: PIEValidatedDataset, challenge: str) -> UAERightsDecision: ...


class HMACReceiptVerifier:
    """Explicit trusted-key receipt verifier. No built-in/default secret.

    Useful for deterministic isolated tests and optionally trusted deployments
    after a negotiated key-management process. Never hardcode an app-wide key.
    """
    def __init__(self, keys: Mapping[str, bytes]):
        if not keys or any(
            type(name) is not str or not _TOKEN.fullmatch(name)
            or type(secret) is not bytes or len(secret) < 32
            for name, secret in keys.items()
        ):
            _deny('invalid_verifier', 'Explicit 32+ byte secret and bounded key ID required')
        self._keys = dict(keys)

    def verify(self, receipt: UAEExportReceipt) -> bool:
        if not isinstance(receipt, UAEExportReceipt):
            return False
        secret = self._keys.get(receipt.key_id)
        if secret is None:
            return False
        message = json.dumps(receipt.signed_fields(), sort_keys=True, ensure_ascii=False,
                             separators=(',', ':'), allow_nan=False).encode('utf-8')
        expected = hmac.new(secret, message, sha256).hexdigest()
        return hmac.compare_digest(expected, receipt.signature)


class UAEReadBridge:
    """Receiving adapter for future, independently authenticated UAE exports.

    Deliberately unavailable without three explicit dependencies. Cannot read
    UAE tables/Group C scores directly or turn historical aggregates into rows.
    """
    def __init__(self, transport: UAETransport | None = None,
                 verifier: UAEReceiptVerifier | None = None,
                 rights: CurrentRightsAuthority | None = None,
                 *, now: Callable[[], datetime] | None = None):
        self._transport = transport
        self._verifier = verifier
        self._rights = rights
        self._now = now or (lambda: datetime.now(timezone.utc))

    def _descriptor(self) -> UAEExportDescriptor:
        if self._transport is None:
            _deny('capability_unavailable', 'No live UAE research transport is configured')
        try:
            d = self._transport.describe()
        except Exception as exc:
            raise PIEBoundaryError('capability_unavailable', 'UAE handshake unavailable') from exc
        if not isinstance(d, UAEExportDescriptor):
            _deny('capability_incompatible', 'Invalid UAE handshake descriptor')
        if d.peer_schema != MANIFEST_SCHEMA:
            _deny('capability_incompatible', 'UAE research wire version mismatch')
        if not all((d.row_level, d.qualified_only, d.read_time_rights, d.signed_receipts)):
            _deny('capability_incompatible', 'UAE research export capabilities incomplete')
        if not callable(getattr(self._transport, 'export_qualified_manifest', None)):
            _deny('capability_incompatible', 'UAE row-level export is missing')
        return d

    def capability(self) -> PortCapability:
        if self._transport is None or self._verifier is None or self._rights is None:
            return PortCapability('uae', CapabilityState.UNAVAILABLE, reason_code='not_connected')
        try:
            d = self._descriptor()
        except PIEBoundaryError as exc:
            state = (CapabilityState.INCOMPATIBLE if exc.code == 'capability_incompatible'
                     else CapabilityState.UNAVAILABLE)
            return PortCapability('uae', state, reason_code=exc.code)
        return PortCapability('uae', CapabilityState.DECLARED,
                              adapter_schema=PORT_SCHEMAS['uae'], peer_schema=d.peer_schema)

    def read_qualified_manifest(self, pointer: PIEDatasetPointer) -> UAEReadResult:
        if not isinstance(pointer, PIEDatasetPointer):
            _deny('invalid_pointer', 'PIE pointer is required')
        if self._verifier is None or self._rights is None:
            _deny('capability_unavailable', 'Trusted receipt verification and current rights required')
        descriptor = self._descriptor()
        if descriptor.producer_version != pointer.uae_engine_version:
            _deny('capability_incompatible', 'Pinned engine version not available at UAE')
        challenge = secrets.token_hex(32)
        try:
            envelope = self._transport.export_qualified_manifest(pointer, challenge)  # type: ignore[union-attr]
        except Exception as exc:
            raise PIEBoundaryError('uae_read_failed', 'UAE research export failed') from exc
        if not isinstance(envelope, UAEExportEnvelope):
            _deny('invalid_export', 'UAE provided no typed research envelope')
        receipt = envelope.receipt
        if (receipt.issuer != descriptor.issuer or receipt.producer_version != descriptor.producer_version
                or receipt.challenge != challenge or receipt.project_id != pointer.project_id
                or receipt.source_id != pointer.source_id
                or receipt.source_policy_version_id != pointer.source_policy_version_id
                or receipt.manifest_sha256 != pointer.uae_manifest_sha256
                or receipt.dataset_sha256 != pointer.uae_dataset_sha256):
            _deny('receipt_mismatch', 'Export receipt does not bind exact requested input and nonce')
        now = self._now()
        if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
            _deny('invalid_clock', 'Explicit UTC-aware clock required')
        if _clock(receipt.issued_at) > now or _clock(receipt.expires_at) <= now:
            _deny('receipt_expired', 'UAE receipt is future-dated or expired')
        try:
            authenticated = self._verifier.verify(receipt)
        except Exception as exc:
            raise PIEBoundaryError('receipt_verification_failed', 'UAE receipt verifier failed') from exc
        if authenticated is not True:
            _deny('receipt_untrusted', 'UAE receipt not authenticated')
        dataset = load_dataset_json(pointer, envelope.raw_manifest)
        if dataset.as_dict()['producer']['run_id'] != receipt.producer_run_id:
            _deny('receipt_mismatch', 'Receipt producer run ID differs from manifest')
        # This authority must be independent of the untrusted UAE export and
        # must re-evaluate current project/source/retention rights on EACH read.
        try:
            decision = self._rights.check_dataset(pointer, dataset, challenge)
        except Exception as exc:
            raise PIEBoundaryError('rights_check_failed', 'Current source rights check failed') from exc
        if not isinstance(decision, UAERightsDecision):
            _deny('rights_check_failed', 'No typed current-rights decision')
        if (decision.project_id != pointer.project_id or decision.source_id != pointer.source_id
                or decision.source_policy_version_id != pointer.source_policy_version_id
                or decision.manifest_sha256 != dataset.manifest_sha256
                or decision.challenge != challenge):
            _deny('rights_scope_mismatch', 'Rights decision does not cover requested dataset')
        if not decision.granted:
            _deny('rights_denied', 'Current source policy denies research read')
        # Rights evidence must be fresh for this read, not a cached approval.
        if not _clock(receipt.issued_at) <= _clock(decision.checked_at) <= self._now():
            _deny('rights_stale', 'Rights must be checked after exporter receipt')
        return UAEReadResult(dataset, receipt.issuer, canonical_hash(receipt.signed_fields()),
                             decision.checked_at)
