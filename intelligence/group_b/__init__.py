"""Standalone Group B package: contracts, injection and offline capability checks.

Importing this package never connects to QDP, Group A, Analytics, a database
or a paid model. B1–B8 execution is added only in subsequent GB-MOD stages.
"""
from .contracts import (
    ContractError, GroupBInputPointer, canonical_hash, content_type_for_kind,
)
from .configuration import GroupBConfig
from .errors import (
    GroupBError, DependencyUnavailable, DependencyContractMismatch,
    DependencyProbeFailed, ExecutionNotApproved,
)
from .adapters.qdp import QDPHandoff, VerifiedPreparation
from .intake import InboxReceipt, receive_qdp_delivery
from .repository import (
    RuntimeSchemaStatus, inspect_runtime_schema, require_runtime_schema,
)
from .ports import (
    GroupBPorts, PortCapability, PortStatus, PortState, EXPECTED_CONTRACTS,
)

__all__ = [
    "ContractError", "GroupBInputPointer", "canonical_hash",
    "content_type_for_kind", "GroupBConfig", "GroupBPorts", "PortCapability",
    "PortStatus", "PortState", "EXPECTED_CONTRACTS", "GroupBError",
    "DependencyUnavailable", "DependencyContractMismatch",
    "DependencyProbeFailed", "ExecutionNotApproved", "RuntimeSchemaStatus",
    "inspect_runtime_schema", "require_runtime_schema",
    "QDPHandoff", "VerifiedPreparation", "InboxReceipt", "receive_qdp_delivery",
]
