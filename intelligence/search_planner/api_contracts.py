"""Read-only typed Search Planner facade DTOs and strict identity checks.

Public views are snapshots, NOT bearer tokens or authorization proofs. Raw
validated query sets are always loaded from project-scoped PostgreSQL, never
from caller-provided JSON. JSON payloads are privately serialized so callers
cannot mutate the returned view's stored data through nested dictionaries.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import re
from typing import Any

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError, canonical, sha256
from intelligence.taxonomy_builder.pipeline_repository import _pid
from .contracts import publication_uuid

SERVICE_SCHEMA = "search-planner-service/v1"
CONTEXT_VIEW_SCHEMA = "search-planner-context-preview/v1"
GENERATION_VIEW_SCHEMA = "search-planner-generation-status/v1"
VALIDATED_VIEW_SCHEMA = "search-planner-validated-view/v1"
_DIGEST = re.compile(r"[0-9a-f]{64}\Z")


def _object(value: object, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise PipelineError("Invalid " + label + " result", 409)
    return value


def _digest(value: object, label: str) -> str:
    if type(value) is not str or not _DIGEST.fullmatch(value):
        raise PipelineError("Invalid " + label + " SHA256", 409)
    return value


def _snapshot(value: Any) -> str:
    """Force JSON-safe canonical bytes; snapshots reject NaNs/non-JSON values."""
    try:
        return canonical(value)
    except (ValueError, TypeError, OverflowError, RecursionError) as exc:
        raise PipelineError("Search Planner result is not canonical JSON", 409) from exc


@dataclass(frozen=True, slots=True)
class ServiceStatus:
    schema_version: str = SERVICE_SCHEMA
    collector_state: str = "unavailable"
    external_search_ready: bool = False
    paid_calls_in_this_operation: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "collector": {
                "state": self.collector_state,
                "external_search_ready": self.external_search_ready,
                "reason": "collector_adapter_not_activated_in_sp01",
            },
            "paid_calls_in_this_operation": self.paid_calls_in_this_operation,
            "read_only_operations": [
                "preview_contexts", "get_generation", "get_generation_candidates",
                "get_validated_queries", "generation_execution_status",
                "preflight_generation_execution", "generation_budget",
            ],
        }


@dataclass(frozen=True, slots=True)
class ContextPreview:
    project_id: str
    taxonomy_publication_id: str
    taxonomy_version: int
    taxonomy_sha256: str
    context_count: int
    contexts_sha256: str
    _contexts_json: str = field(repr=False)

    @classmethod
    def from_approved(cls, publication: dict[str, Any],
                      contexts: list[dict[str, Any]],
                      *, max_contexts: int) -> "ContextPreview":
        pub = _object(publication, "approved publication")
        pid = _pid(pub.get("project_id"))
        publication_id = publication_uuid(pub.get("taxonomy_publication_id"))
        version = pub.get("taxonomy_version")
        digest = _digest(pub.get("taxonomy_sha256"), "taxonomy")
        if type(version) is not int or version < 1:
            raise PipelineError("Invalid approved taxonomy version", 409)
        if type(contexts) is not list or not 1 <= len(contexts) <= max_contexts:
            raise PipelineError("Context preview hypothesis count exceeds limit", 409)
        seen: set[str] = set()
        for raw in contexts:
            ctx = _object(raw, "hypothesis context")
            if (ctx.get("context_schema_version") != "hypothesis-context/v1"
                    or ctx.get("project_id") != pid
                    or ctx.get("taxonomy_publication_id") != publication_id
                    or ctx.get("taxonomy_sha256") != digest
                    or ctx.get("taxonomy_version") != version):
                raise PipelineError("Context ancestry/publication mismatch", 409)
            hyp = _object(ctx.get("hypothesis"), "context hypothesis")
            hid = hyp.get("id")
            if type(hid) is not str or not hid or hid in seen:
                raise PipelineError("Missing or duplicate hypothesis context ID", 409)
            seen.add(hid)
        payload = _snapshot(contexts)
        return cls(pid, publication_id, version, digest, len(contexts),
                   sha256(contexts), payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": CONTEXT_VIEW_SCHEMA,
            "project_id": self.project_id,
            "taxonomy_publication_id": self.taxonomy_publication_id,
            "taxonomy_version": self.taxonomy_version,
            "taxonomy_sha256": self.taxonomy_sha256,
            "context_count": self.context_count,
            "contexts_sha256": self.contexts_sha256,
            "contexts": json.loads(self._contexts_json),
            "provider_requests": 0,
            "spending_authorized": False,
        }


@dataclass(frozen=True, slots=True)
class GenerationStatus:
    project_id: str
    generation_id: str
    taxonomy_publication_id: str
    input_sha256: str
    state: str
    hypothesis_count: int
    batch_count: int

    @classmethod
    def from_read(cls, project_id: str, generation_id: str,
                  raw: dict[str, Any]) -> "GenerationStatus":
        pid = _pid(project_id)
        gid = publication_uuid(generation_id)
        r = _object(raw, "generation")
        if r.get("generation_id") != gid:
            raise PipelineError("Generation read identity mismatch", 409)
        pub = publication_uuid(r.get("taxonomy_publication_id"))
        digest = _digest(r.get("input_sha256"), "generation input")
        from .generation_engine.contracts import RunState
        if r.get("status") not in {x.value for x in RunState}:
            raise PipelineError("Unrecognized generation state", 409)
        for key in ("hypothesis_count", "batch_count"):
            if type(r.get(key)) is not int or not 0 <= r[key] <= 10000:
                raise PipelineError("Invalid generation counter", 409)
        return cls(pid, gid, pub, digest, r["status"],
                   r["hypothesis_count"], r["batch_count"])

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": GENERATION_VIEW_SCHEMA,
            "project_id": self.project_id,
            "generation_id": self.generation_id,
            "taxonomy_publication_id": self.taxonomy_publication_id,
            "input_sha256": self.input_sha256,
            "status": self.state,
            "hypothesis_count": self.hypothesis_count,
            "batch_count": self.batch_count,
        }


@dataclass(frozen=True, slots=True)
class ValidatedQueriesView:
    project_id: str
    validation_run_id: str
    generation_id: str
    bundle_sha256: str
    selected_count: int
    _bundle_json: str = field(repr=False)

    @classmethod
    def from_read(cls, project_id: str, validation_run_id: str,
                  raw: dict[str, Any]) -> "ValidatedQueriesView":
        pid = _pid(project_id)
        vid = publication_uuid(validation_run_id)
        r = _object(raw, "validated query")
        if (r.get("schema_version") != "validated_queries/v1"
                or r.get("validation_run_id") != vid
                or r.get("project_id") != pid
                or r.get("embedding_mode") != "semantic_local"
                or r.get("status") != "completed"):
            raise PipelineError("Untrusted or test-only validated query bundle", 409)
        gid = publication_uuid(r.get("generation_id"))
        _digest(r.get("source_bundle_sha256"), "source bundle")
        _digest(r.get("config_sha256"), "validation config")
        queries = r.get("queries")
        if type(queries) is not list or len(queries) > 480:
            raise PipelineError("Invalid validated query count", 409)
        seen: set[str] = set()
        for item in queries:
            q = _object(item, "selected query")
            cid = publication_uuid(q.get("candidate_id"))
            if cid in seen or q.get("query_mode") not in ("keyword", "hashtag"):
                raise PipelineError("Duplicate/unsupported selected query", 409)
            seen.add(cid)
        if type(r.get("coverage")) is not list or type(r.get("decisions")) is not list:
            raise PipelineError("Incomplete validated query evidence", 409)
        payload = _snapshot(r)
        return cls(pid, vid, gid, sha256(r), len(queries), payload)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": VALIDATED_VIEW_SCHEMA,
            "project_id": self.project_id,
            "validation_run_id": self.validation_run_id,
            "generation_id": self.generation_id,
            "bundle_sha256": self.bundle_sha256,
            "selected_count": self.selected_count,
            "bundle": json.loads(self._bundle_json),
            "external_search_dispatched_by_this_read": False,
        }
