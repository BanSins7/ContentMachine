"""Read-only integration diagnostics; never authorizes Collector or provider work."""
from .readiness import read_integration_readiness
from .collector_v1 import assess_collector_v1_projection

__all__ = ("read_integration_readiness", "assess_collector_v1_projection")
