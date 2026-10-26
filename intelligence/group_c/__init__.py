"""Group C standalone performance analytics module: boundary-only bootstrap.

Runtime scoring, source access and integrations are deliberately unavailable
until later GC-MOD stages. Do not treat this as a live C0-C7 calculator.
"""
from .contracts import (
    ContractError, GroupCInputPointer, BREAKOUT_WEIGHTS,
    FRESHNESS_HALF_LIFE_H, canonical_hash, content_type_for_kind,
)
__all__ = [
    "ContractError", "GroupCInputPointer", "BREAKOUT_WEIGHTS",
    "FRESHNESS_HALF_LIFE_H", "canonical_hash", "content_type_for_kind",
]

from .ports import (
    AnalyticsPublisher, CapabilityState, CohortApprovalReader,
    ConfigApprovalReader, CreatorTopicRegistry, D1FactsReader, GroupCPorts,
    IntegrationCapability, PORT_SCHEMAS, QDPHandoffNotice, QDPPreparationReader,
    SourcePolicyReader, require_available,
)

__all__ += [
    "AnalyticsPublisher", "CapabilityState", "CohortApprovalReader",
    "ConfigApprovalReader", "CreatorTopicRegistry", "D1FactsReader",
    "GroupCPorts", "IntegrationCapability", "PORT_SCHEMAS",
    "QDPHandoffNotice", "QDPPreparationReader", "SourcePolicyReader",
    "require_available",
]

from .qdp_intake import QDPCurrentPreparationAdapter, QDPGroupCConsumer
__all__ += ["QDPCurrentPreparationAdapter", "QDPGroupCConsumer"]
