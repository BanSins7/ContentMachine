"""Pure Group B evidence validation and manifest preparation; no peer I/O on import."""
from .evidence import (
    EvidenceFailure, MediaByteProof, VerifiedObservation, verify_local_bytes,
    validate_observations,
)
from .manifest import MediaEvidenceManifest, build_media_evidence_manifest
from .sampling import select_diverse, uncovered_time_windows
from .builder import (
    ContextBuildError, ContextLimits, ContextPacket,
    build_context_packet, build_current_context_packet,
)

__all__ = (
    "EvidenceFailure", "MediaByteProof", "VerifiedObservation",
    "verify_local_bytes", "validate_observations",
    "MediaEvidenceManifest", "build_media_evidence_manifest",
    "select_diverse", "uncovered_time_windows",
    "ContextBuildError", "ContextLimits", "ContextPacket",
    "build_context_packet", "build_current_context_packet",
)
