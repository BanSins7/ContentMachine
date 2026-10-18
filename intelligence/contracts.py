"""Source adapter boundaries. No platform-specific crawler is enabled in infrastructure v1."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

@dataclass(frozen=True)
class SourceRights:
    source_id: str
    can_store_metrics: bool = False
    can_derive_scores: bool = False
    can_analyze_media: bool = False
    retention_days: int | None = None

@dataclass(frozen=True)
class VideoCandidate:
    platform: str
    platform_video_id: str
    canonical_url: str
    platform_creator_id: str | None = None
    published_at: datetime | None = None
    title: str | None = None
    description: str | None = None

@dataclass(frozen=True)
class MetricObservation:
    source_id: str
    platform: str
    platform_video_id: str
    observed_at: datetime
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None

class SourceAdapter(Protocol):
    @property
    def rights(self) -> SourceRights: ...
    def discover(self, query: str) -> list[VideoCandidate]: ...
    def fetch_metrics(self, video_ids: list[str]) -> list[MetricObservation]: ...
