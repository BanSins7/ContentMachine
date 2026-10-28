"""Structural dependency ports; NO concrete Collector or network imports.

These protocols establish type expectations for injected dependencies. They
do NOT mean that an adapter is installed or an operation is authorized.
Concrete source readiness and handoff schema are separately version-pinned in
SP-09 and SP-11/12. None of these ports are automatically invoked on import.
"""
from __future__ import annotations

from typing import Any, ContextManager, Mapping, Protocol, Sequence


class PublicationReader(Protocol):
    def __call__(self, project_id: str, publication_id: str) -> dict[str, Any]: ...


class HypothesisContextBuilder(Protocol):
    def __call__(self, publication: dict[str, Any],
                 search_policy: dict[str, Any]) -> list[dict[str, Any]]: ...


class GenerationStatusReader(Protocol):
    def __call__(self, project_id: str, generation_id: str) -> dict[str, Any]: ...


class CandidateBundleReader(Protocol):
    def __call__(self, project_id: str, generation_id: str) -> dict[str, Any]: ...


class ValidatedBundleReader(Protocol):
    def __call__(self, project_id: str, validation_run_id: str,
                 *, include_test: bool = False) -> dict[str, Any]: ...


class PostgreSQLConnectionFactory(Protocol):
    def __call__(self) -> ContextManager[Any]: ...


class EmbeddingEncoder(Protocol):
    mode: str
    model_key: str

    def encode(self, texts: Sequence[str]) -> list[list[float]]: ...


class GenerationTransport(Protocol):
    def ensure_available(self) -> bool: ...
    def invoke_once(self, payload: dict[str, Any]) -> Any: ...


class CollectorCapabilitiesPort(Protocol):
    """Optional SP-09 discovery port; does not dispatch search operations."""

    def snapshot(self, project_id: str) -> Mapping[str, Any]: ...


class CollectorHandoffPort(Protocol):
    """Optional trusted SERVER-OWNED durable Collector QuerySet intake port.

    SP12 does not import the unmerged Collector branch. No source-provider
    calls are permitted by this port. Unknown submit results are never
    automatically retried; get_receipt is a read-only independent re-read.
    """
    def available(self) -> bool: ...
    def capabilities(self) -> Mapping[str, Any]: ...

    def accept(self, *, project_id: str, handoff: Mapping[str, Any],
               idempotency_key: str) -> Mapping[str, Any]: ...

    def get_receipt(self, *, project_id: str,
                    idempotency_key: str) -> Mapping[str, Any] | None: ...
