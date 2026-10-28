"""Unified Analytics standalone import surface (no live adapters or writes).

UA-MOD-18: explicit standalone worker, fenced snapshot+ack commit and manual recovery.
No Group C scorer, peer imports, provider calls or DB autoconnection.
"""
from .contracts import (
    AnalyticsBoundaryError,
    UnifiedAnalyticsInputPointer,
    canonical_hash,
    content_type_for_kind,
)
from .configuration import UAEConfiguration
from .dto import (
    InputScope, EvidenceRef, EvidenceRead, ReadState, PatternsHandoffPointer,
)
from .lineage import (
    LINEAGE_SCHEMA, CanonicalLineagePins, CanonicalLineage, resolve_canonical_lineage,
)
from .sre_adapter import (
    SRE_PEER_WIRE, SREApprovedRelevancePins, SREApprovedRelevanceReader,
)
from .qdp_adapter import QDP_PEER_WIRE, QDPQualifiedPins, QDPQualifiedSetReader
from .qdp_handoff import QDPHandoffEvent, QDPHandoffOutcome, register_verified_handoff
from .group_a_adapter import GA_PEER_WIRE, GroupAVerifiedPins, GroupAVerifiedFactsReader
from .group_b_adapter import B_READER_WIRE, GroupBExactPins, GroupBExactReader
from .group_c_adapter import (
    C_READER_WIRE, GroupCPersistedPins, PersistedScoreFacts,
    GroupCPersistedScoreReader,
)
from .source_policy_adapter import (
    RIGHTS_VERSION, SourceRightsPins, CurrentSourceRights,
    SourcePolicyCurrentReader, GuardedUAEReads,
)
from .joined_context import (
    JOIN_SCHEMA, JoinedCandidatePins, CandidateExclusion,
    JoinedInputContext, JoinedSelection, JoinedContextAssembler,
)
from .qualification_orchestrator import (
    DECISION_SCHEMA, QualificationStatus, BlockerSeverity,
    QualificationBlocker, PortQualification, QualificationDecision,
    QualificationOrchestrator,
)
from .snapshot_assembly import (
    SNAPSHOT_ASSEMBLY_SCHEMA, canonical_decimal, normalize_payload,
    SnapshotEvidence, ImmutableSnapshotDraft, PersistedSnapshotReceipt,
    ImmutableSnapshotAssembler, ImmutableSnapshotRepository,
)
from .revision_events import (
    EVENT_WIRE, KINDS, RevisionEvent, RevisionOutcome, RevisionEventProcessor,
)
from .job_queue import (
    QUEUE_WIRE, QueueSpec, QueueReceipt, QueueLease, QualificationJobQueue,
)
from .worker import (
    WORKER_WIRE, WorkerLimits, CandidateResolver,
    WorkOutcome, QualificationWorker,
)
from .readside_visibility import (
    VISIBILITY_WIRE, VisibilityPins, VisibleSnapshot, VisibleSnapshotReader,
)
from .descriptive_aggregation import (
    AGGREGATION_WIRE, MIN_SAMPLE, MetricStatistics, DescriptiveAggregate,
    QualifiedAggregationReader, summarize_qualified,
)
from .typed_segmentation import (
    SEGMENTATION_WIRE, DIMENSIONS, TypedSegment,
    TypedSegmentation, TypedSegmentationReader,
)
from .cohort_comparison import (
    COMPARISON_WIRE, PublicationWindow, ComparisonSpec,
    CohortBinComparison, CompatibleCohortComparison, CompatibleCohortReader,
)
from .statistical_uncertainty import (
    UNCERTAINTY_WIRE, ObservedMissingness, EmpiricalInterquartileRange,
    UncertaintyDisclosure, QualifiedSummaryQuality,
    QualifiedSegmentQuality, QualifiedSegmentationQuality,
    QualifiedBinQuality, QualifiedComparisonQuality,
    QualifiedUncertaintyReader,
)
from .project_ranked_views import (
    PROJECT_RUN_WIRE, PAGE_WIRE, EXPORT_WIRE, SORT_MODES,
    RankedVideo, ProjectRankRun, PageCursor, RankedPage,
    RankedExport, ProjectRankedRunStore,
)
from .pie_research_handoff import (
    PIE_MANIFEST_WIRE, PIE_DATASET_WIRE, PIE_BUILD_VERSION,
    PIE_ENGINE_VERSION, PIE_SELECTION_RULE, SignedResearchReceipt,
    SignedResearchExport, ReceiptSigner, PIEResearchExportReader,
)
from .qualification import (
    GATE_SCHEMA, ObservationState, QualificationObservation, QualificationInput,
    validate_request, canonical_gate_hash, evaluate_qualification,
)
from .ports import (
    CapabilityState, PortCapability, PortStatus, UAEPorts,
    SREReader, QDPReader, GroupAReader, GroupBReader, GroupCReader,
    D1Reader, SourcePolicyReader, PatternsPublisher,
)

__all__ = [
    "AnalyticsBoundaryError", "UnifiedAnalyticsInputPointer",
    "canonical_hash", "content_type_for_kind", "UAEConfiguration",
    "InputScope", "EvidenceRef", "EvidenceRead", "ReadState",
    "PatternsHandoffPointer", "CapabilityState", "PortCapability",
    "PortStatus", "UAEPorts", "SREReader", "QDPReader", "GroupAReader",
    "GroupBReader", "GroupCReader", "D1Reader", "SourcePolicyReader",
    "PatternsPublisher",
    "GATE_SCHEMA", "ObservationState", "QualificationObservation", "QualificationInput",
    "validate_request", "canonical_gate_hash", "evaluate_qualification",
    "LINEAGE_SCHEMA", "CanonicalLineagePins", "CanonicalLineage",
    "resolve_canonical_lineage",
    "SRE_PEER_WIRE", "SREApprovedRelevancePins",
    "SREApprovedRelevanceReader",
    "QDP_PEER_WIRE", "QDPQualifiedPins", "QDPQualifiedSetReader",
    "QDPHandoffEvent", "QDPHandoffOutcome", "register_verified_handoff",
    "GA_PEER_WIRE", "GroupAVerifiedPins", "GroupAVerifiedFactsReader",
    "B_READER_WIRE", "GroupBExactPins", "GroupBExactReader",
    "C_READER_WIRE", "GroupCPersistedPins", "PersistedScoreFacts",
    "GroupCPersistedScoreReader",
    "RIGHTS_VERSION", "SourceRightsPins", "CurrentSourceRights",
    "SourcePolicyCurrentReader", "GuardedUAEReads",
    "JOIN_SCHEMA", "JoinedCandidatePins", "CandidateExclusion",
    "JoinedInputContext", "JoinedSelection", "JoinedContextAssembler",
    "DECISION_SCHEMA", "QualificationStatus", "BlockerSeverity",
    "QualificationBlocker", "PortQualification", "QualificationDecision",
    "QualificationOrchestrator",
    "SNAPSHOT_ASSEMBLY_SCHEMA", "canonical_decimal", "normalize_payload",
    "SnapshotEvidence", "ImmutableSnapshotDraft", "PersistedSnapshotReceipt",
    "ImmutableSnapshotAssembler", "ImmutableSnapshotRepository",
    "EVENT_WIRE", "KINDS", "RevisionEvent", "RevisionOutcome",
    "RevisionEventProcessor",
    "QUEUE_WIRE", "QueueSpec", "QueueReceipt", "QueueLease",
    "QualificationJobQueue",
    "WORKER_WIRE", "WorkerLimits", "CandidateResolver",
    "WorkOutcome", "QualificationWorker",
    "VISIBILITY_WIRE", "VisibilityPins", "VisibleSnapshot",
    "VisibleSnapshotReader",
    "AGGREGATION_WIRE", "MIN_SAMPLE", "MetricStatistics",
    "DescriptiveAggregate", "QualifiedAggregationReader",
    "summarize_qualified",
    "SEGMENTATION_WIRE", "DIMENSIONS", "TypedSegment",
    "TypedSegmentation", "TypedSegmentationReader",
    "COMPARISON_WIRE", "PublicationWindow", "ComparisonSpec",
    "CohortBinComparison", "CompatibleCohortComparison",
    "CompatibleCohortReader",
    "UNCERTAINTY_WIRE", "ObservedMissingness",
    "EmpiricalInterquartileRange", "UncertaintyDisclosure",
    "QualifiedSummaryQuality", "QualifiedSegmentQuality",
    "QualifiedSegmentationQuality", "QualifiedBinQuality",
    "QualifiedComparisonQuality", "QualifiedUncertaintyReader",
    "PROJECT_RUN_WIRE", "PAGE_WIRE", "EXPORT_WIRE", "SORT_MODES",
    "RankedVideo", "ProjectRankRun", "PageCursor", "RankedPage",
    "RankedExport", "ProjectRankedRunStore",
    "PIE_MANIFEST_WIRE", "PIE_DATASET_WIRE", "PIE_BUILD_VERSION",
    "PIE_ENGINE_VERSION", "PIE_SELECTION_RULE", "SignedResearchReceipt",
    "SignedResearchExport", "ReceiptSigner", "PIEResearchExportReader",
]
