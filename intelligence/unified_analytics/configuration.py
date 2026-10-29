"""UA-MOD-02 pure operator configuration; no peer activation or implicit work."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Any

from .contracts import AnalyticsBoundaryError

CONFIG_SCHEMA = "uae-configuration/v1"


@dataclass(frozen=True, slots=True)
class UAEConfiguration:
    """Limits for future local reads only. Not permissions or source approvals."""

    max_read_batch: int = 50
    max_snapshot_page: int = 100
    allow_background_polling: bool = False
    allow_provider_requests: bool = False
    schema_version: str = CONFIG_SCHEMA

    def __post_init__(self) -> None:
        if type(self.max_read_batch) is not int or not 1 <= self.max_read_batch <= 50:
            raise AnalyticsBoundaryError("invalid_config", "max_read_batch must be 1..50")
        if type(self.max_snapshot_page) is not int or not 1 <= self.max_snapshot_page <= 100:
            raise AnalyticsBoundaryError("invalid_config", "max_snapshot_page must be 1..100")
        for name in ("allow_background_polling", "allow_provider_requests"):
            # These features are not implemented by this module; cannot opt in
            # by merely changing a property or environment variable.
            if getattr(self, name) is not False:
                raise AnalyticsBoundaryError("unsupported_operation", name + " is not enabled")
        if self.schema_version != CONFIG_SCHEMA:
            raise AnalyticsBoundaryError("unsupported_schema_version", "Unknown UAE config")

    @classmethod
    def from_mapping(cls, source: Mapping[str, Any] | None = None) -> "UAEConfiguration":
        if source is None:
            return cls()
        if type(source) is not dict:
            raise AnalyticsBoundaryError("invalid_config", "Expected explicit config object")
        allowed = {"max_read_batch", "max_snapshot_page",
                   "allow_background_polling", "allow_provider_requests", "schema_version"}
        if set(source) - allowed or any(type(k) is not str for k in source):
            raise AnalyticsBoundaryError("invalid_config", "Unknown configuration fields")
        return cls(**source)
