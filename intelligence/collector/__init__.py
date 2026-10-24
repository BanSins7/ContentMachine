"""Video Collector offline planning contracts. No live provider calls or persistence."""

from .query_intake import parse_validated_query_set
from .planner import build_preview, validate_limits
from .registry import list_sources, match_source
from .contracts import CollectorError

__all__ = ("parse_validated_query_set", "build_preview", "validate_limits",
           "list_sources", "match_source", "CollectorError")
