"""D1.2 domain: typed, versioned representations. No IO and no API requests."""
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

Platform = Literal["youtube", "tiktok", "instagram"]
Quality = Literal["valid", "partial", "rejected"]

@dataclass(frozen=True, slots=True)
class Candidate:
    platform: Platform
    platform_video_id: str
    canonical_url: str
    title: str | None = None
    published_at: datetime | None = None
    duration_seconds: float | None = None
    creator_platform_id: str | None = None
    views: int | None = None
    likes: int | None = None
    comments: int | None = None
    shares: int | None = None
    saves: int | None = None
    observed_at: datetime | None = None

@dataclass(frozen=True, slots=True)
class ValidationResult:
    state: Quality
    candidate: Candidate | None
    reasons: tuple[str, ...] = ()
