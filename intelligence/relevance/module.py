"""M02: side-effect-free public SRE module metadata and feature-gate contract.

No environment, SQL, network, provider imports or daemon initialization.
M03 will resolve deployment environment, real dependencies and runtime readiness.
Declaring a module installed never authorizes spending or starts a worker.
"""
from __future__ import annotations

from copy import deepcopy

from .v1_contracts import SREContractError, record, reject

MODULE_SCHEMA = "sre-module/v1"
MODULE_NAME = "semantic_relevance_engine"
MODULE_API_VERSION = "v1"
EXACT_LIVE_MODEL = "anthropic/claude-opus-5.5"
_FEATURES = (
    "enabled", "offline_worker", "live_classification",
    "collector_event_ingest", "qdp_event_delivery",
)
_DEFAULT_FLAGS = {key: False for key in _FEATURES}
# Contract-only flags are NOT usable capabilities until their DB/runtime
# implementations and independent release gates are complete.
_NOT_AVAILABLE = frozenset((
    "live_classification", "collector_event_ingest", "qdp_event_delivery",
))
_CAPABILITIES = {
    "approved_taxonomy_reader": "implemented_s2",
    "verified_discovery_reader": "implemented_s3",
    "offline_fixture_worker": "implemented_s4",
    "inbound_collector_contract": "contract_only_m02",
    "outbound_qdp_contract": "contract_only_m02",
    "live_run_engine": "not_implemented",
    "local_embeddings": "not_implemented",
    "openrouter_opus": "not_implemented",
    "sre_studio": "not_implemented",
}
_CONTRACTS = {
    "module": MODULE_SCHEMA,
    "taxonomy_reference": "sre-taxonomy-ref/v1",
    "discovery_reference": "sre-discovery-ref/v1",
    "collector_delivery": "sre.discovery_ready/v1",
    "qualified_result": "sre.classifications_ready/v1",
}


def validate_feature_flags(flags):
    """Strict opt-in flags, with missing features default-denied.

    No module-level env read. No flag, including `enabled`, implies provider
    credentials, runtime source permissions, project ownership or paid consent.
    """
    record(flags, "sre_features", optional=_FEATURES)
    values = dict(_DEFAULT_FLAGS)
    for key, value in flags.items():
        if type(value) is not bool:
            reject(f"sre_features.{key}", "expected explicit JSON boolean")
        if value and key in _NOT_AVAILABLE:
            reject(f"sre_features.{key}", "module capability not installed",
                   "feature_unavailable")
        values[key] = value
    if values["offline_worker"] and not values["enabled"]:
        reject("sre_features.offline_worker", "worker requires enabled module",
               "feature_disabled")
    return values


def get_module_manifest(flags=None):
    """Stable JSON-compatible description; no claim about DB/config health.

    `readiness` is conservative. Real operational status requires M03 and
    cannot be inferred merely because offline Python code exists.
    """
    resolved = validate_feature_flags(flags if flags is not None else {})
    return deepcopy({
        "schema_version": MODULE_SCHEMA,
        "module_id": MODULE_NAME,
        "api_version": MODULE_API_VERSION,
        "deployment_readiness": "runtime_preflight_required" if resolved["enabled"]
                                else "disabled",
        "features": resolved,
        "capabilities": _CAPABILITIES,
        "contracts": _CONTRACTS,
        "live_model_policy": {
            "provider": "openrouter",
            "model_id": EXACT_LIVE_MODEL,
            "allow_fallback": False,
            "allow_fast_llm": False,
            "allow_candidate_drop": False,
            "embedding_role": "rank_all",
        },
        "requires_external_feature_branches": [],
        "required_existing_main_services": [
            "taxonomy_builder.approved_deep_publication",
            "discovery.d1_postgresql",
        ],
    })


__all__ = [
    "MODULE_SCHEMA", "MODULE_NAME", "MODULE_API_VERSION",
    "EXACT_LIVE_MODEL", "validate_feature_flags", "get_module_manifest",
]
