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

from .cohort_rules import (
    COHORT_RULES_VERSION, CohortRules, CohortCandidate,
    CohortRejection, CohortSelection, PostgreSQLCohortCandidates,
    select_compatible_peers,
)
__all__ += [
    "COHORT_RULES_VERSION", "CohortRules", "CohortCandidate",
    "CohortRejection", "CohortSelection", "PostgreSQLCohortCandidates",
    "select_compatible_peers",
]

from .cohort_freeze import (
    ESTIMATOR_VERSION, FrozenCohortEvidence, CohortFreezeReceipt,
    PostgreSQLCohortFreezeWriter, build_frozen_evidence,
)
__all__ += [
    "ESTIMATOR_VERSION", "FrozenCohortEvidence", "CohortFreezeReceipt",
    "PostgreSQLCohortFreezeWriter", "build_frozen_evidence",
]

from .human_review import (
    REVIEW_SCHEMA_VERSION, HumanReviewSubject, VerifiedHumanReviewer,
    HumanReviewerVerifier, ReviewReceipt, PostgreSQLHumanReview,
    build_human_review_subject,
)
__all__ += [
    "REVIEW_SCHEMA_VERSION", "HumanReviewSubject", "VerifiedHumanReviewer",
    "HumanReviewerVerifier", "ReviewReceipt", "PostgreSQLHumanReview",
    "build_human_review_subject",
]

from .config_activation import (
    ACTIVATION_VERSION, ConfigActivationIntent, ConfigActivationReceipt,
    HumanApprovalAttestationVerifier, PostgreSQLConfigActivation,
    validate_activation_intent,
)
__all__ += [
    "ACTIVATION_VERSION", "ConfigActivationIntent", "ConfigActivationReceipt",
    "HumanApprovalAttestationVerifier", "PostgreSQLConfigActivation",
    "validate_activation_intent",
]

from .creator_ratio import (
    C1_VERSION, CreatorRatioApprovalVerifier, CreatorRatioResult,
    PostgreSQLCreatorRatio, compute_creator_ratio,
)
__all__ += [
    "C1_VERSION", "CreatorRatioApprovalVerifier", "CreatorRatioResult",
    "PostgreSQLCreatorRatio", "compute_creator_ratio",
]

from .niche_comparison import (
    NICHE_COMPARISON_VERSION, NicheComparisonApprovalVerifier,
    NicheComparisonResult, PostgreSQLNicheComparison,
    compute_niche_comparison,
)
__all__ += [
    "NICHE_COMPARISON_VERSION", "NicheComparisonApprovalVerifier",
    "NicheComparisonResult", "PostgreSQLNicheComparison",
    "compute_niche_comparison",
]

from .views_growth import (
    VIEW_GROWTH_VERSION, ViewsGrowthResult, PostgreSQLViewsGrowth,
    compute_views_growth_per_hour, compute_verified_views_growth,
)
__all__ += [
    "VIEW_GROWTH_VERSION", "ViewsGrowthResult", "PostgreSQLViewsGrowth",
    "compute_views_growth_per_hour", "compute_verified_views_growth",
]

from .raw_engagement import (
    RAW_ENGAGEMENT_VERSION, RawEngagementRates, RawEngagementPercentages,
    RawEngagementResult, PostgreSQLRawEngagement,
    compute_raw_engagement, compute_verified_raw_engagement,
)
__all__ += [
    "RAW_ENGAGEMENT_VERSION", "RawEngagementRates", "RawEngagementPercentages",
    "RawEngagementResult", "PostgreSQLRawEngagement",
    "compute_raw_engagement", "compute_verified_raw_engagement",
]

from .bayesian_engagement import (
    BAYESIAN_ENGAGEMENT_VERSION, BayesianEngagementApprovalVerifier,
    BayesianEngagementRates, BayesianEngagementResult,
    PostgreSQLBayesianEngagement, compute_bayesian_engagement,
)
__all__ += [
    "BAYESIAN_ENGAGEMENT_VERSION", "BayesianEngagementApprovalVerifier",
    "BayesianEngagementRates", "BayesianEngagementResult",
    "PostgreSQLBayesianEngagement", "compute_bayesian_engagement",
]

from .freshness import (
    FRESHNESS_VERSION, FreshnessResult, PostgreSQLFreshness,
    compute_freshness_score, compute_verified_freshness,
)
__all__ += [
    "FRESHNESS_VERSION", "FreshnessResult", "PostgreSQLFreshness",
    "compute_freshness_score", "compute_verified_freshness",
]

from .breakout_score import (
    BREAKOUT_VERSION, BreakoutComponents, BreakoutPreview,
    compute_breakout_components, assemble_breakout_preview,
)
__all__ += [
    "BREAKOUT_VERSION", "BreakoutComponents", "BreakoutPreview",
    "compute_breakout_components", "assemble_breakout_preview",
]

from .c7_persistence import (
    PERSISTED_C7_VERSION, C7PersistenceApprovalVerifier,
    PersistedC7Receipt, PostgreSQLC7Persistence,
)
__all__ += [
    "PERSISTED_C7_VERSION", "C7PersistenceApprovalVerifier",
    "PersistedC7Receipt", "PostgreSQLC7Persistence",
]

from .durable_worker import (
    WORKER_VERSION, WorkerPolicy, WorkerLease, WorkerCompletion,
    WorkerFailure, PostgreSQLGroupCWorker, retry_delay_seconds,
)
__all__ += [
    "WORKER_VERSION", "WorkerPolicy", "WorkerLease", "WorkerCompletion",
    "WorkerFailure", "PostgreSQLGroupCWorker", "retry_delay_seconds",
]

from .incremental_recompute import (
    RECOMPUTE_VERSION, OUTBOX_VERSION, RevisionPlan, RevisionOutbox,
    PostgreSQLIncrementalRecompute, determine_revision_kind,
)
__all__ += [
    "RECOMPUTE_VERSION", "OUTBOX_VERSION", "RevisionPlan", "RevisionOutbox",
    "PostgreSQLIncrementalRecompute", "determine_revision_kind",
]

from .metric_quality_gate import (
    QUALITY_VERSION, QualityDecision, PersistedQualityReceipt,
    classify_quality_failure, approved_growth_limit,
    reject_anomalous_growth,
)
__all__ += [
    "QUALITY_VERSION", "QualityDecision", "PersistedQualityReceipt",
    "classify_quality_failure", "approved_growth_limit",
    "reject_anomalous_growth",
]

from .retention import (
    RETENTION_VERSION, ERASURE_VERSION, REVOCABLE_CAUSES,
    RetentionRead, ScoreInvalidation, ErasureRequest, ErasureAuthorityVerifier,
    PostgreSQLScoreRetention,
)
__all__ += [
    "RETENTION_VERSION", "ERASURE_VERSION", "REVOCABLE_CAUSES",
    "RetentionRead", "ScoreInvalidation", "ErasureRequest",
    "ErasureAuthorityVerifier", "PostgreSQLScoreRetention",
]

from .analytics_bridge import (
    ANALYTICS_HANDOFF_VERSION, OUTBOX_EVENT_TYPE,
    PersistedC7Handoff, AnalyticsOutboxReceipt, AnalyticsDeliveryReceipt,
    PostgreSQLAnalyticsBridge,
)
__all__ += [
    "ANALYTICS_HANDOFF_VERSION", "OUTBOX_EVENT_TYPE",
    "PersistedC7Handoff", "AnalyticsOutboxReceipt", "AnalyticsDeliveryReceipt",
    "PostgreSQLAnalyticsBridge",
]

from .http_api import (
    API_VERSION, HTTPGrant, HTTPAccessVerifier, GroupCHTTPAPI,
)
__all__ += [
    "API_VERSION", "HTTPGrant", "HTTPAccessVerifier", "GroupCHTTPAPI",
]

from .studio_read import (
    STUDIO_VERSION, MAX_STUDIO_POINTS, PostgreSQLGroupCStudio,
)
__all__ += [
    "STUDIO_VERSION", "MAX_STUDIO_POINTS", "PostgreSQLGroupCStudio",
]

from .operations import (
    OPS_VERSION, MonitoringPolicy, OperationalAlert, ProjectPulse,
    RecoveryReceipt, RecoveryAuthorityVerifier, MaintenancePreview,
    alerts_for, maintenance_for, PostgreSQLGroupCOperations,
)
__all__ += [
    "OPS_VERSION", "MonitoringPolicy", "OperationalAlert", "ProjectPulse",
    "RecoveryReceipt", "RecoveryAuthorityVerifier", "MaintenancePreview",
    "alerts_for", "maintenance_for", "PostgreSQLGroupCOperations",
]

from .performance import (
    BENCHMARK_VERSION, BenchmarkPolicy, SyntheticPopulation,
    PlanMeasure, BackpressureMeasure, BenchmarkReport,
)
__all__ += [
    "BENCHMARK_VERSION", "BenchmarkPolicy", "SyntheticPopulation",
    "PlanMeasure", "BackpressureMeasure", "BenchmarkReport",
]
