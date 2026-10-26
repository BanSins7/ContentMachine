"""Read-only, project-scoped facade over existing SP1–SP8.

No app routes, SQL writes, paid provider calls, model downloads, background
workers or Collector imports. Later SP stages extend these same interfaces;
this foundation must work when the separate Collector package is absent.
"""
from __future__ import annotations

from typing import Any

from intelligence.taxonomy_builder.pipeline_repository import _pid
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

from .api_contracts import ContextPreview, GenerationStatus, ServiceStatus, ValidatedQueriesView
from .configuration import SearchPlannerOptions
from .contracts import publication_uuid, validate_published_snapshot, validate_search_policy
from .context_builder import build_hypothesis_contexts
from .input_loader import load_approved_publication
from .generation_engine.run_manager import get_planned_generation
from .query_validation.service import get_validated_bundle
from .ports import PublicationReader, HypothesisContextBuilder, GenerationStatusReader, ValidatedBundleReader


class SearchPlannerService:
    """Injectable facade; trusted readers retain their existing SQL project guards.

    Custom readers are for supervised assembly and test fixtures only. Never
    expose these injection hooks through user-controlled HTTP request bodies.
    """

    def __init__(
        self, *,
        options: SearchPlannerOptions | None = None,
        publication_reader: PublicationReader | None = None,
        context_builder: HypothesisContextBuilder | None = None,
        generation_reader: GenerationStatusReader | None = None,
        validated_bundle_reader: ValidatedBundleReader | None = None,
    ) -> None:
        if options is not None and not isinstance(options, SearchPlannerOptions):
            raise PipelineError("Invalid Search Planner service configuration", 422)
        self._options = options if options is not None else SearchPlannerOptions()
        self._publication_reader = publication_reader if publication_reader is not None else load_approved_publication
        self._context_builder = context_builder if context_builder is not None else build_hypothesis_contexts
        self._generation_reader = generation_reader if generation_reader is not None else get_planned_generation
        self._validated_reader = validated_bundle_reader if validated_bundle_reader is not None else get_validated_bundle
        if any(not callable(fn) for fn in (
            self._publication_reader, self._context_builder,
            self._generation_reader, self._validated_reader,
        )):
            raise PipelineError("Search Planner dependency must be callable", 422)

    @property
    def options(self) -> SearchPlannerOptions:
        return self._options

    def status(self) -> ServiceStatus:
        """No external probe, model initialization or DB access."""
        return ServiceStatus()

    def preview_contexts(self, project_id: str, publication_id: str,
                         search_policy: dict[str, Any]) -> ContextPreview:
        """One approved read, deterministic v1 context build, zero writes/spend."""
        pid = _pid(project_id)
        pubid = publication_uuid(publication_id)
        policy = validate_search_policy(search_policy)
        snapshot = validate_published_snapshot(self._publication_reader(pid, pubid))
        if snapshot["project_id"] != pid or snapshot["taxonomy_publication_id"] != pubid:
            raise PipelineError("Foreign approved publication returned by reader", 409)
        contexts = self._context_builder(snapshot, policy)
        return ContextPreview.from_approved(
            snapshot, contexts,
            max_contexts=self._options.max_preview_hypotheses,
        )

    def get_generation(self, project_id: str, generation_id: str) -> GenerationStatus:
        """Project-scoped status read, no job execution."""
        pid = _pid(project_id)
        gid = publication_uuid(generation_id)
        return GenerationStatus.from_read(pid, gid, self._generation_reader(pid, gid))

    def get_validated_queries(self, project_id: str,
                              validation_run_id: str) -> ValidatedQueriesView:
        """Only production semantic QV output; no frontend JSON acceptance."""
        pid = _pid(project_id)
        vid = publication_uuid(validation_run_id)
        bundle = self._validated_reader(pid, vid, include_test=False)
        return ValidatedQueriesView.from_read(pid, vid, bundle)
