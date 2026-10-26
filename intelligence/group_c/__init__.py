"""Group C standalone module: ports, canonical D1/C0 and isolated arithmetic.

The GC-MOD-07 pure calculator lives in group_c.pure_math and is intentionally
NOT exported as a root/production scorer. Real scoring still requires current
rights, approved cohort/config and trustworthy publication evidence before
persistence or Analytics publishing. No automatic provider jobs start here.
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

from .d1 import (
    PostgreSQLD1FactsReader, PostgreSQLCreatorTopicRegistry,
    PostgreSQLSourcePolicyReader, local_d1_ports, read_scoped_d1_bundle,
)
__all__ += [
    "PostgreSQLD1FactsReader", "PostgreSQLCreatorTopicRegistry",
    "PostgreSQLSourcePolicyReader", "local_d1_ports", "read_scoped_d1_bundle",
]

from .c0 import (
    C0Pair, C0Snapshot, C0_SCHEMA_VERSION, MAX_COUNTER,
    VIEW_DEFINITIONS, read_c0_pair, validate_c0_pair, validate_c0_snapshot,
)
__all__ += [
    "C0Pair", "C0Snapshot", "C0_SCHEMA_VERSION", "MAX_COUNTER",
    "VIEW_DEFINITIONS", "read_c0_pair", "validate_c0_pair", "validate_c0_snapshot",
]

from .publication_time import (
    PUBLICATION_EVIDENCE_VERSION, PublicationTimeEvidence,
    read_publication_time, validate_publication_time,
)
__all__ += [
    "PUBLICATION_EVIDENCE_VERSION", "PublicationTimeEvidence",
    "read_publication_time", "validate_publication_time",
]
