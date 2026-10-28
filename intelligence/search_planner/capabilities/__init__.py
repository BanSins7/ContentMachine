"""SP-09: deterministic read-only external capability matching.

No Collector/DTK source registry, adapter imports, database writes or provider
calls. This package accepts only server-assembled capabilities via an injected
port. Missing or stale data is an explicit blocked/unavailable result.
"""
from .service import (
    CAPABILITY_SCHEMA, MATCH_SCHEMA, CapabilityMatcher,
    validate_capability_snapshot, project_collector_declarations,
)
__all__ = (
    "CAPABILITY_SCHEMA", "MATCH_SCHEMA", "CapabilityMatcher",
    "validate_capability_snapshot", "project_collector_declarations",
)
