"""Versioned UAE -> PIE research intake; no implicit publication or live entitlement.

This module is the composition root for the two independently developed peers.
The exporter, trusted receipt verifier, source rights and full row provenance
MUST be provisioned independently. It never creates qualified snapshots,
re-scores C7 or treats a positive mock as a production research approval.
"""
from __future__ import annotations

from dataclasses import dataclass, fields
import json
import secrets
from typing import Callable

from intelligence.unified_analytics.pie_research_handoff import (
    PIE_ENGINE_VERSION, PIE_MANIFEST_WIRE, PIEResearchExportReader,
    SignedResearchReceipt,
)
from intelligence.patterns_insights.contracts import PIEDatasetPointer, PIEBoundaryError
from intelligence.patterns_insights.dataset_contract import load_dataset_json
from intelligence.patterns_insights.intake.uae import (
    UAEExportDescriptor, UAEExportReceipt, UAEExportEnvelope, UAEReadBridge,
    UAEReceiptVerifier, CurrentRightsAuthority,
)
from intelligence.patterns_insights.intake.provenance import (
    ProvenanceAuthority, SourceEligibilityGate,
)


def _reject(code: str) -> None:
    raise PIEBoundaryError(code, "Current, signed UAE-to-PIE research data unavailable")


class UAEPIETransport:
    """Adapt real UAE signed row exports to PIE-owned transport types.

    select_run(pointer) is an explicitly provisioned current selection reader:
    return (persisted_run_uuid, exact current VisibilityPins tuple). It MUST
    query/verify project membership independently and never derive pins from a
    client-supplied project id or accept partial/blocked rows. UAE's exporter
    rechecks all selected pins and the persisted run within one fresh SQL tx.
    """
    def __init__(
        self,
        reader: PIEResearchExportReader,
        select_run: Callable[[PIEDatasetPointer | None], tuple],
        *,
        issuer: str,
    ):
        if (not callable(getattr(reader, "export", None))
                or not callable(select_run)
                or type(issuer) is not str or not issuer):
            _reject("uae_transport_unconfigured")
        self._reader = reader
        self._select_run = select_run
        self._issuer = issuer

    def describe(self) -> UAEExportDescriptor:
        return UAEExportDescriptor(
            peer_schema=PIE_MANIFEST_WIRE,
            producer_version=PIE_ENGINE_VERSION,
            issuer=self._issuer,
            row_level=True,
            qualified_only=True,
            read_time_rights=True,
            signed_receipts=True,
        )

    def _signed(self, pointer: PIEDatasetPointer | None, challenge: str):
        if type(challenge) is not str or len(challenge) != 64:
            _reject("uae_challenge_invalid")
        try:
            selection = self._select_run(pointer)
            if type(selection) is not tuple or len(selection) != 2:
                _reject("uae_selection_unavailable")
            run_id, pins = selection
            if type(run_id) is not str or type(pins) is not tuple:
                _reject("uae_selection_unavailable")
            exported = self._reader.export(run_id, pins, challenge)
        except PIEBoundaryError:
            raise
        except Exception as exc:
            raise PIEBoundaryError("uae_export_unavailable",
                                   "Unable to recheck current UAE run") from exc
        if exported is None:
            _reject("uae_qualified_dataset_unavailable")
        receipt = getattr(exported, "receipt", None)
        if (not isinstance(receipt, SignedResearchReceipt)
                or receipt.challenge != challenge
                or receipt.issuer != self._issuer
                or receipt.producer_version != PIE_ENGINE_VERSION
                or type(getattr(exported, "raw_manifest", None)) is not bytes):
            _reject("uae_signed_export_invalid")
        if (exported.manifest_sha256 != receipt.manifest_sha256
                or exported.dataset_sha256 != receipt.dataset_sha256):
            _reject("uae_signed_export_invalid")
        return exported

    def prepare_pointer(self) -> PIEDatasetPointer:
        """Prepare an immutable, *untrusted* pointer using live producer pins.

        No analysis or release happens here. A new nonce and full source-rights
        recheck are required when PIE later reads this pointer.
        """
        exported = self._signed(None, secrets.token_hex(32))
        try:
            manifest = json.loads(exported.raw_manifest)
            scope = manifest["scope"]
            receipt = exported.receipt
            pointer = PIEDatasetPointer(
                project_id=receipt.project_id,
                source_id=receipt.source_id,
                source_policy_version_id=receipt.source_policy_version_id,
                uae_engine_version=receipt.producer_version,
                uae_dataset_sha256=receipt.dataset_sha256,
                uae_manifest_sha256=receipt.manifest_sha256,
                calculation_config_id=scope["calculation_config_id"],
                source_metric_definition=scope["source_metric_definition"],
                source_metric_version=scope["source_metric_version"],
                as_of=scope["as_of"],
            )
            # Prevent creation of pointers to malformed or summary-only data.
            load_dataset_json(pointer, exported.raw_manifest)
            return pointer
        except (TypeError, ValueError, KeyError, AttributeError) as exc:
            raise PIEBoundaryError("uae_pointer_invalid",
                                   "UAE returned no valid research pointer") from exc

    def export_qualified_manifest(
        self, pointer: PIEDatasetPointer, challenge: str,
    ) -> UAEExportEnvelope:
        if not isinstance(pointer, PIEDatasetPointer):
            _reject("invalid_pointer")
        exported = self._signed(pointer, challenge)
        receipt = exported.receipt
        # No re-selecting a different cohort or newer analysis at read time.
        if (receipt.project_id != pointer.project_id
                or receipt.source_id != pointer.source_id
                or receipt.source_policy_version_id != pointer.source_policy_version_id
                or receipt.manifest_sha256 != pointer.uae_manifest_sha256
                or receipt.dataset_sha256 != pointer.uae_dataset_sha256):
            _reject("uae_pinned_dataset_changed")
        pie_receipt = UAEExportReceipt(
            **{field.name: getattr(receipt, field.name)
               for field in fields(UAEExportReceipt)}
        )
        return UAEExportEnvelope(exported.raw_manifest, pie_receipt)


@dataclass(frozen=True, slots=True)
class UAEPIEAdmission:
    manifest_sha256: str
    sample_size: int
    candidate_eligible: bool
    verified_live: bool
    ready_for_analysis: bool
    blockers: tuple[str, ...]


class UAEPIEPipeline:
    """Compose producer, trusted receiver, and fresh row provenance checks.

    PIE currently reserves verified_live/ready_for_analysis=False until real
    independently certified authorities and release gates are implemented;
    even a fully positive structural report does NOT authorize inference,
    insight generation or publication.
    """
    def __init__(
        self,
        transport: UAEPIETransport,
        *,
        verifier: UAEReceiptVerifier,
        rights: CurrentRightsAuthority,
        provenance: ProvenanceAuthority,
    ):
        if not isinstance(transport, UAEPIETransport):
            _reject("uae_transport_unconfigured")
        if any(component is None for component in (verifier, rights, provenance)):
            _reject("uae_pie_authority_unconfigured")
        self.transport = transport
        self.receiver = UAEReadBridge(transport, verifier, rights)
        self.gate = SourceEligibilityGate(provenance)

    def prepare_pointer(self) -> PIEDatasetPointer:
        return self.transport.prepare_pointer()

    def admit(self, pointer: PIEDatasetPointer) -> UAEPIEAdmission:
        read = self.receiver.read_qualified_manifest(pointer)
        report = self.gate.evaluate(read)
        return UAEPIEAdmission(
            manifest_sha256=report.manifest_sha256,
            sample_size=report.total_rows,
            candidate_eligible=report.candidate_eligible,
            verified_live=report.verified_live,
            ready_for_analysis=report.ready_for_analysis,
            blockers=report.blockers,
        )


__all__ = ["UAEPIETransport", "UAEPIEPipeline", "UAEPIEAdmission"]
