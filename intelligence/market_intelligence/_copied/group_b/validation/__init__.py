"""Fail-closed unified Group B evidence, schema and completion gate.

Nothing here invokes paid providers, SQL, video decoders or independent trust.
"""
from .schema import (
    VERSION, BLOCKS, Gate, BlockAssessment, UnifiedAssessment,
    validate_block, validate_bundle,
)
from .evidence import check_original_manifest, check_packet, check_claims

__all__=(
    "VERSION","BLOCKS","Gate","BlockAssessment","UnifiedAssessment",
    "validate_block","validate_bundle","check_original_manifest",
    "check_packet","check_claims",
)
