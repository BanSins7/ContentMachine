"""Offline declared tool capabilities. Never probe DTK or claim live readiness.

DTK transport is ALREADY bundled into this self-contained Collector branch.
An older PR #13 is historical only. Capability declarations are NOT proof
of provider connectivity, policy approval, provider pricing or search consent.
"""
from __future__ import annotations

from .contracts import CollectorError, SourceDescriptor

REGISTRY_VERSION = "collector-source-registry/v2-dtk-installed"

_SOURCES = {
    "dtk_tiktok": SourceDescriptor(
        source_id="dtk_tiktok",
        platform="tiktok",
        integration_state="local_rest_connector_present_manual_only",
        operations=(("keyword", "search_posts"),),
        operationally_verified=False,
        source_policy_verified=False,
        pricing_status="not_quoted",
    ),
    "apify_tiktok": SourceDescriptor(
        source_id="apify_tiktok",
        platform="tiktok",
        integration_state="future_adapter_not_implemented",
        operations=(),
        operationally_verified=False,
        source_policy_verified=False,
        pricing_status="unknown_paid_provider",
    ),
    "youtube_official": SourceDescriptor(
        source_id="youtube_official",
        platform="youtube",
        integration_state="future_adapter_not_implemented",
        operations=(),
        operationally_verified=False,
        source_policy_verified=False,
        pricing_status="quota_not_verified",
    ),
    "instagram_official": SourceDescriptor(
        source_id="instagram_official",
        platform="instagram",
        integration_state="future_adapter_not_implemented",
        operations=(),
        operationally_verified=False,
        source_policy_verified=False,
        pricing_status="capabilities_not_verified",
    ),
}


def list_sources() -> list[dict]:
    return [_SOURCES[source_id].to_dict() for source_id in sorted(_SOURCES)]


def match_source(source_id: str, mode: str) -> tuple[SourceDescriptor, str | None]:
    if type(source_id) is not str or source_id not in _SOURCES:
        raise CollectorError("unknown_source", "Unregistered collector source")
    source = _SOURCES[source_id]
    return source, source.operation_for(mode)
