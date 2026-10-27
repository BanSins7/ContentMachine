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
from .adapters.group_a import GroupAExactPin, read_exact_a, validate_a_snapshot
from .context import (
    EvidenceFailure, MediaByteProof, VerifiedObservation,
    verify_local_bytes, validate_observations,
    MediaEvidenceManifest, build_media_evidence_manifest,
    ContextBuildError, ContextLimits, ContextPacket,
    select_diverse, uncovered_time_windows,
    build_context_packet, build_current_context_packet,
)
from .providers import (
    MODEL_CONTRACT, ModelCatalogSnapshot, ModelDescriptor, ModelProbe,
    ModelProbeState, ProviderFailure, VerifiedChatResponse,
    OpenRouterProvider, OpenRouterTransport,
    parse_model_catalog, decode_chat_response, prepare_chat_request,
)
from .budget import BudgetDenied, BudgetQuote, quote_context
from .ledger import (
    BudgetLedger, StoredQuote, StoredApproval, AttemptReservation,
    save_quote, approve_quote, reserve_attempt,
    mark_submission, mark_ambiguous, reconcile_attempt,
)
from .orchestrator import (
    DAG_VERSION, DEPENDENCIES, KIND, JobClaim, JobOrchestrator,
    enqueue_jobs, claim_local, heartbeat, finish_job, cancel_run,
    reap_expired_jobs,
)
from .worker import (
    WorkerSources, LocalResult, WorkCycle, LocalWorker, PaidWorker,
    make_b5_worker,
)
from .blocks import (
    B5MeasurementUnavailable, B5Metrics, B5EditingExecutor,
    B5EditingVerifier, analyze_b5, read_shot_timeline,
)
from .blocks import (
    B1EvidenceError, B1HookProposal, B1PreparedRequest,
    validate_b1_response, unknown_b1, prepare_b1_request,
    B1_MECHANISMS, B1_DELIVERY, B1_FLAGS, B1_OUTPUT_SCHEMA,
)
from .blocks import (
    B2FormatError, B2FormatProposal, B2PreparedRequest,
    validate_b2_response, unknown_b2, prepare_b2_request,
    B2_VIDEO_FORMATS, B2_CAROUSEL_FORMATS, B2_OUTPUT_SCHEMA,
)
from .blocks import (
    B3SamplingError, B3SamplingPlan, SamplingPolicy,
    build_b3_sampling_plan, verify_b3_plan_against_manifest,
)
from .blocks import (
    B3StoryError, B3StoryProposal, B3PreparedRequest, B3_ROLES, B3_STORY_SCHEMA,
    validate_b3_story, unknown_b3, prepare_b3_story_request, persist_b3_story,
)
from .blocks import (
    B4VisualError, B4VisualProposal, B4PreparedRequest,
    validate_b4_visual, unknown_b4, prepare_b4_request,
    B4_OUTPUT_SCHEMA, persist_b4_visual,
)
from .lineage import bind_exact_group_a
from .dependency_rules import (
    BLOCKS, ReadinessState, BlockReadiness, ReadinessReport,
    evaluate_block_dependencies,
)
from .readiness import assess_current_run, require_block_ready, require_fresh_block
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
    "GroupAExactPin", "read_exact_a", "validate_a_snapshot", "bind_exact_group_a",
    "EvidenceFailure", "MediaByteProof", "VerifiedObservation",
    "verify_local_bytes", "validate_observations",
    "MediaEvidenceManifest", "build_media_evidence_manifest",
    "ContextBuildError", "ContextLimits", "ContextPacket",
    "select_diverse", "uncovered_time_windows",
    "build_context_packet", "build_current_context_packet",
    "MODEL_CONTRACT", "ModelCatalogSnapshot", "ModelDescriptor", "ModelProbe",
    "ModelProbeState", "ProviderFailure", "VerifiedChatResponse",
    "OpenRouterProvider", "OpenRouterTransport", "parse_model_catalog",
    "decode_chat_response", "prepare_chat_request",
    "BudgetDenied", "BudgetQuote", "quote_context", "BudgetLedger",
    "StoredQuote", "StoredApproval", "AttemptReservation",
    "save_quote", "approve_quote", "reserve_attempt",
    "mark_submission", "mark_ambiguous", "reconcile_attempt",
    "DAG_VERSION", "DEPENDENCIES", "KIND", "JobClaim", "JobOrchestrator",
    "enqueue_jobs", "claim_local", "heartbeat", "finish_job", "cancel_run",
    "reap_expired_jobs", "WorkerSources", "LocalResult", "WorkCycle",
    "LocalWorker", "PaidWorker", "make_b5_worker",
    "B5MeasurementUnavailable", "B5Metrics", "B5EditingExecutor",
    "B5EditingVerifier", "analyze_b5", "read_shot_timeline",
    "B1EvidenceError", "B1HookProposal", "B1PreparedRequest",
    "validate_b1_response", "unknown_b1", "prepare_b1_request",
    "B1_MECHANISMS", "B1_DELIVERY", "B1_FLAGS", "B1_OUTPUT_SCHEMA",
    "B2FormatError", "B2FormatProposal", "B2PreparedRequest",
    "validate_b2_response", "unknown_b2", "prepare_b2_request",
    "B2_VIDEO_FORMATS", "B2_CAROUSEL_FORMATS", "B2_OUTPUT_SCHEMA",
    "B3SamplingError", "B3SamplingPlan", "SamplingPolicy",
    "build_b3_sampling_plan", "verify_b3_plan_against_manifest",
    "B4VisualError", "B4VisualProposal", "B4PreparedRequest",
    "validate_b4_visual", "unknown_b4", "prepare_b4_request",
    "B4_OUTPUT_SCHEMA", "persist_b4_visual",
    "B3StoryError", "B3StoryProposal", "B3PreparedRequest",
    "B3_ROLES", "B3_STORY_SCHEMA",
    "validate_b3_story", "unknown_b3", "prepare_b3_story_request",
    "persist_b3_story",
    "BLOCKS", "ReadinessState", "BlockReadiness", "ReadinessReport",
    "evaluate_block_dependencies", "assess_current_run",
    "require_block_ready", "require_fresh_block",
]
