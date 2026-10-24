"""Strict, versioned source-intelligence DTOs. No network, files or DB calls.

All timestamps are aware UTC instants; locators describe *where* a claim came
from, not whether it has been independently proven. The DB enforces project
scope and immutability independently of these application-level models.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

PROJECT_ID = r"^[0-9a-f]{32}$"
SHA256 = r"^[0-9a-f]{64}$"
ProjectID = Annotated[str, StringConstraints(pattern=PROJECT_ID)]
Hash = Annotated[str, StringConstraints(pattern=SHA256)]
Short = Annotated[str, StringConstraints(min_length=1, max_length=250, strip_whitespace=True)]


class StrictDTO(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, validate_assignment=True)

    @field_validator("created_at", "updated_at", "recorded_at", "fetched_or_uploaded_at",
                     "completed_at", "lease_until", "approved_at", check_fields=False)
    @classmethod
    def timezone_required(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("timestamp must include a timezone")
        return value

    def json_round_trip(self) -> "StrictDTO":
        return type(self).model_validate_json(self.model_dump_json())


class ProcessingStatus(str, Enum):
    registered = "registered"
    queued = "queued"
    processing = "processing"
    completed = "completed"
    partial = "partial"
    unavailable = "unavailable"
    failed = "failed"
    cancelled = "cancelled"
    needs_review = "needs_review"


class RightsPolicy(StrictDTO):
    # Explicit permission grants must be recorded before any use of source data.
    can_fetch: bool = False
    can_store: bool = False
    can_analyze: bool = False
    can_send_to_llm: bool = False
    retention_days: int | None = Field(default=None, ge=1)
    basis: str | None = Field(default=None, max_length=500)


class ProjectSource(StrictDTO):
    schema_version: Literal["project-source/v1"] = "project-source/v1"
    source_id: UUID
    project_id: ProjectID
    source_type: Literal["questionnaire_field", "product_website", "competitor_website",
                         "competitor_account", "promoter_account", "user_video_reference",
                         "research_link", "research_document", "other"]
    origin_field: str | None = Field(default=None, max_length=120)
    original_input: str = Field(max_length=12000)
    canonical_url: str | None = Field(default=None, max_length=2048)
    platform: str | None = Field(default=None, max_length=40)
    platform_native_id: str | None = Field(default=None, max_length=250)
    user_notes: str | None = Field(default=None, max_length=12000)
    status: ProcessingStatus = ProcessingStatus.registered
    rights: RightsPolicy = Field(default_factory=RightsPolicy)
    active_version_id: UUID | None = None
    created_at: datetime
    updated_at: datetime
    tombstoned_at: datetime | None = None

    @field_validator("canonical_url")
    @classmethod
    def https_only(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if (parts.scheme != "https" or not parts.hostname or parts.username or parts.password
                or parts.port not in (None, 443) or parts.fragment):
            raise ValueError("canonical_url must be an HTTPS URL with no userinfo, port or fragment")
        return value

    @model_validator(mode="after")
    def times_ordered(self):
        if self.updated_at < self.created_at:
            raise ValueError("updated_at precedes created_at")
        return self


class SourceVersion(StrictDTO):
    schema_version: Literal["source-version/v1"] = "source-version/v1"
    source_version_id: UUID
    source_id: UUID
    project_id: ProjectID
    version_no: int = Field(ge=1)
    content_sha256: Hash | None = None
    fetched_or_uploaded_at: datetime | None = None
    media_type: str | None = Field(default=None, max_length=160)
    byte_size: int | None = Field(default=None, ge=0)
    extraction_status: ProcessingStatus = ProcessingStatus.registered
    source_locator: dict = Field(default_factory=dict)
    immutable_asset_ref: str | None = Field(default=None, max_length=512)
    policy_snapshot: RightsPolicy = Field(default_factory=RightsPolicy)
    recorded_at: datetime

    @model_validator(mode="after")
    def content_pair(self):
        if (self.content_sha256 is None) != (self.byte_size is None):
            raise ValueError("content_sha256 and byte_size must be present together")
        if self.immutable_asset_ref is not None and self.content_sha256 is None:
            raise ValueError("asset requires a content hash")
        return self


class MediaAsset(StrictDTO):
    schema_version: Literal["project-media-asset/v1"] = "project-media-asset/v1"
    asset_id: UUID
    project_id: ProjectID
    source_id: UUID
    source_version_id: UUID
    asset_kind: Literal["document", "video", "audio", "frame", "subtitle", "image", "other"]
    content_sha256: Hash
    byte_size: int = Field(ge=0)
    media_type: Short
    storage_ref: Short  # Opaque storage key, never a public absolute filesystem path.
    rights_snapshot: RightsPolicy = Field(default_factory=RightsPolicy)
    created_at: datetime


class ExtractionJob(StrictDTO):
    schema_version: Literal["source-job/v1"] = "source-job/v1"
    job_id: UUID
    project_id: ProjectID
    source_id: UUID
    source_version_id: UUID
    stage: Literal["web_fetch", "document_parse", "video_download", "asr", "ocr",
                   "vision", "timeline_fusion", "evidence_normalization"]
    state: ProcessingStatus = ProcessingStatus.queued
    attempts: int = Field(default=0, ge=0)
    max_attempts: int = Field(default=3, ge=1, le=20)
    idempotency_key: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    lease_until: datetime | None = None
    created_at: datetime
    updated_at: datetime
    cost_estimate_usd: Decimal = Field(default=Decimal("0"), ge=0)
    cost_actual_usd: Decimal | None = Field(default=None, ge=0)
    consent_ref: str | None = Field(default=None, max_length=250)
    output_ref: str | None = Field(default=None, max_length=512)
    error_code: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def valid_job(self):
        if self.attempts > self.max_attempts or self.updated_at < self.created_at:
            raise ValueError("invalid attempts or timestamps")
        if self.cost_estimate_usd > 0 and not self.consent_ref:
            raise ValueError("priced job must have explicit consent reference")
        return self


class Locator(StrictDTO):
    kind: Literal["questionnaire", "pdf", "docx", "spreadsheet", "web", "video", "text"]
    field_path: str | None = None
    start_char: int | None = Field(default=None, ge=0)
    end_char: int | None = Field(default=None, ge=1)
    page: int | None = Field(default=None, ge=1)
    block: int | None = Field(default=None, ge=0)
    paragraph: int | None = Field(default=None, ge=0)
    sheet: str | None = None
    cell: str | None = None
    url: str | None = None
    selector: str | None = None
    start_ms: int | None = Field(default=None, ge=0)
    end_ms: int | None = Field(default=None, ge=1)
    frame_id: str | None = None

    @model_validator(mode="after")
    def located(self):
        k = self.kind
        if self.start_char is not None or self.end_char is not None:
            if self.start_char is None or self.end_char is None or self.end_char <= self.start_char:
                raise ValueError("character range must be nonempty")
        if self.start_ms is not None or self.end_ms is not None:
            if self.start_ms is None or self.end_ms is None or self.end_ms <= self.start_ms:
                raise ValueError("time range must be nonempty")
        if k == "questionnaire" and not (self.field_path and self.start_char is not None):
            raise ValueError("questionnaire locator requires field and range")
        if k == "pdf" and self.page is None:
            raise ValueError("PDF locator requires page")
        if k == "docx" and self.paragraph is None and self.cell is None:
            raise ValueError("DOCX locator requires paragraph or cell")
        if k == "spreadsheet" and not (self.sheet and self.cell):
            raise ValueError("spreadsheet locator requires sheet/cell")
        if k == "web" and not (self.url and (self.selector or self.start_char is not None)):
            raise ValueError("web locator requires URL and selector/range")
        if k == "video" and self.start_ms is None and not self.frame_id:
            raise ValueError("video locator requires verified time range or frame reference")
        if k == "text" and self.start_char is None:
            raise ValueError("text locator requires range")
        return self


class ExtractedSegment(StrictDTO):
    schema_version: Literal["extracted-segment/v1"] = "extracted-segment/v1"
    segment_id: UUID
    project_id: ProjectID
    source_id: UUID
    source_version_id: UUID
    text: Short
    locator: Locator
    extraction_engine: Short
    extraction_version: Short
    quality_status: Literal["unknown", "complete", "partial", "unavailable"] = "unknown"
    created_at: datetime


class EvidenceRecord(StrictDTO):
    schema_version: Literal["evidence-record/v1"] = "evidence-record/v1"
    evidence_id: UUID
    project_id: ProjectID
    source_id: UUID
    source_version_id: UUID
    evidence_kind: Literal["user_claim", "external_claim", "observed_media", "model_inference"]
    exact_text_or_observation: Short
    locator: Locator
    extraction_engine: Short
    extraction_version: Short
    source_quote: str | None = None
    confidence: Decimal | None = Field(default=None, ge=0, le=1)
    quality_status: Literal["unknown", "complete", "partial", "unavailable"] = "unknown"
    review_status: Literal["pending", "needs_review"] = "pending"
    created_at: datetime

    @model_validator(mode="after")
    def quote_kind(self):
        if self.evidence_kind == "model_inference" and self.review_status != "needs_review":
            raise ValueError("model inference must require human review")
        return self


class VideoSpan(StrictDTO):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str | None = None
    frame_refs: list[str] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def ordered(self):
        if self.end_ms <= self.start_ms:
            raise ValueError("invalid video time interval")
        return self


class VideoAnalysis(StrictDTO):
    schema_version: Literal["video-analysis/v1"] = "video-analysis/v1"
    analysis_id: UUID
    project_id: ProjectID
    source_id: UUID
    source_version_id: UUID
    canonical_video_url: str
    media_sha256: Hash
    duration_ms: int = Field(ge=1)
    transcript_status: ProcessingStatus
    transcript_segments: list[VideoSpan] = Field(default_factory=list)
    scenes: list[VideoSpan] = Field(default_factory=list)
    ocr_spans: list[VideoSpan] = Field(default_factory=list)
    aligned_timeline: list[VideoSpan] = Field(default_factory=list)
    summary: str | None = None
    creative_analysis: dict = Field(default_factory=dict)
    sampled_frame_refs: list[str] = Field(default_factory=list)
    coverage: Decimal = Field(ge=0, le=1)
    observations: list[str] = Field(default_factory=list)
    inferences: list[str] = Field(default_factory=list)
    status: ProcessingStatus
    model_version: str | None = None
    prompt_version: str | None = None
    processing_cost_usd: Decimal = Field(default=Decimal("0"), ge=0)
    completed_at: datetime | None = None

    @model_validator(mode="after")
    def bounded_timeline(self):
        for span in (*self.transcript_segments, *self.scenes, *self.ocr_spans, *self.aligned_timeline):
            if span.end_ms > self.duration_ms:
                raise ValueError("timeline extends past video duration")
        return self


class SourcePin(StrictDTO):
    source_id: UUID
    source_version_id: UUID
    content_sha256: Hash | None = None
    status: ProcessingStatus


class ProjectContextSnapshot(StrictDTO):
    schema_version: Literal["project-context/v1"] = "project-context/v1"
    context_id: UUID
    project_id: ProjectID
    version: int = Field(ge=1)
    immutable_context_hash: Hash
    pinned_profile_revision: int = Field(ge=1)
    pinned_profile_hash: Hash
    pinned_source_versions: list[SourcePin] = Field(default_factory=list)
    evidence_ids: list[UUID] = Field(default_factory=list)
    approved_fact_ids: list[UUID] = Field(default_factory=list)
    analysis_refs: list[UUID] = Field(default_factory=list)
    unprocessed_source_manifest: list[UUID] = Field(default_factory=list)
    rights_snapshot: RightsPolicy = Field(default_factory=RightsPolicy)
    approval_status: Literal["pending"] = "pending"  # decisions stored separately
    created_at: datetime

    @model_validator(mode="after")
    def no_duplicate_source_pins(self):
        ids = [x.source_id for x in self.pinned_source_versions]
        if len(ids) != len(set(ids)):
            raise ValueError("snapshot pins multiple versions of a single source")
        return self


DTO_TYPES = (ProjectSource, SourceVersion, MediaAsset, ExtractionJob, ExtractedSegment,
             EvidenceRecord, VideoAnalysis, ProjectContextSnapshot)


def json_schemas() -> dict[str, dict]:
    """Machine-readable contracts, generated from the same executable source."""
    return {cls.__name__: cls.model_json_schema() for cls in DTO_TYPES}
