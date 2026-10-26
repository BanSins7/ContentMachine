"""Project-scoped standalone Search Planner facade over SP1–SP8 and workers.

Read methods are zero-spend. Explicit generation planning/quote/consent
methods persist SQL mutations; one-step paid execution requires a separate
trusted server-side owner gate AND existing SQG financial authorization.
No HTTP routes, auto-running daemons, model downloads, or Collector imports.
The default facade denies paid execution and privileged recovery actions.
"""
from __future__ import annotations

from typing import Any

from intelligence.taxonomy_builder.pipeline_repository import _pid
from intelligence.taxonomy_builder.pipeline_contracts import PipelineError

from .api_contracts import ContextPreview, GenerationStatus, ServiceStatus, ValidatedQueriesView
from .configuration import SearchPlannerOptions
from .contracts import publication_uuid, validate_published_snapshot, validate_search_policy
from .context_builder import build_hypothesis_contexts, require_published_context_batch
from .input_loader import load_approved_publication
from .generation_engine.run_manager import get_planned_generation
from .generation_engine.output_manager import get_candidates_bundle
from .ai_query.provenance import verify_candidates_bundle, candidate_manifest_sha256
from .query_validation.linkage import get_selected_query_links
from .query_validation.service import get_validated_bundle
from .ports import PublicationReader, HypothesisContextBuilder, GenerationStatusReader, ValidatedBundleReader, CandidateBundleReader
from .generation_workflow import GenerationWorkflow
from .operations import GenerationExecutionController
from .capabilities import CapabilityMatcher
from .task_planner import TaskPlanner
from .handoff import (
    VerifiedHandoffProjector, ingest_qv_to_sp_inbox,
    get_sp_handoff_status, deliver_next_held_chunk,
    reconcile_uncertain_chunk, EmptyCollectorAdapter,
)
from .query_validation.service import pending_validated_outbox


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
        candidate_bundle_reader: CandidateBundleReader | None = None,
        validated_bundle_reader: ValidatedBundleReader | None = None,
        generation_workflow: GenerationWorkflow | None = None,
        execution_controller: GenerationExecutionController | None = None,
        capability_matcher: CapabilityMatcher | None = None,
        task_planner: TaskPlanner | None = None,
        handoff_projector: VerifiedHandoffProjector | None = None,
        collector_handoff_port=None,
        handoff_owner_guard=None,
    ) -> None:
        if options is not None and not isinstance(options, SearchPlannerOptions):
            raise PipelineError("Invalid Search Planner service configuration", 422)
        self._options = options if options is not None else SearchPlannerOptions()
        self._publication_reader = publication_reader if publication_reader is not None else load_approved_publication
        self._context_builder = context_builder if context_builder is not None else build_hypothesis_contexts
        self._generation_reader = generation_reader if generation_reader is not None else get_planned_generation
        self._candidate_reader = candidate_bundle_reader if candidate_bundle_reader is not None else get_candidates_bundle
        self._validated_reader = validated_bundle_reader if validated_bundle_reader is not None else get_validated_bundle
        if generation_workflow is not None and not isinstance(generation_workflow, GenerationWorkflow):
            raise PipelineError("Invalid Search Planner generation workflow", 422)
        self._generation_workflow = generation_workflow if generation_workflow is not None else GenerationWorkflow()
        if execution_controller is not None and not isinstance(execution_controller, GenerationExecutionController):
            raise PipelineError("Invalid Search Planner execution controller", 422)
        self._execution_controller = (execution_controller if execution_controller is not None
                                      else GenerationExecutionController())
        if capability_matcher is not None and not isinstance(capability_matcher, CapabilityMatcher):
            raise PipelineError("Invalid Search Planner capability matcher", 422)
        self._capability_matcher = (capability_matcher if capability_matcher is not None
                                    else CapabilityMatcher())
        if task_planner is not None and not isinstance(task_planner, TaskPlanner):
            raise PipelineError("Invalid Search Planner trusted task planner", 422)
        self._task_planner = (task_planner if task_planner is not None
                              else TaskPlanner(capability_matcher=self._capability_matcher))
        if handoff_projector is not None and not isinstance(
                handoff_projector, VerifiedHandoffProjector):
            raise PipelineError("Invalid trusted QV handoff projector", 422)
        self._handoff_projector = (
            handoff_projector if handoff_projector is not None
            else VerifiedHandoffProjector()
        )
        self._collector_handoff_port = (
            collector_handoff_port if collector_handoff_port is not None
            else EmptyCollectorAdapter()
        )
        if any(not callable(getattr(self._collector_handoff_port, name, None))
               for name in ("available","accept","get_receipt")):
            raise PipelineError("Invalid optional Collector handoff port", 422)
        if handoff_owner_guard is not None and not callable(handoff_owner_guard):
            raise PipelineError("Invalid trusted handoff owner guard", 422)
        self._handoff_owner_guard = handoff_owner_guard
        if any(not callable(fn) for fn in (
            self._publication_reader, self._context_builder,
            self._generation_reader, self._candidate_reader, self._validated_reader,
        )):
            raise PipelineError("Search Planner dependency must be callable", 422)

    @property
    def options(self) -> SearchPlannerOptions:
        return self._options

    def status(self) -> ServiceStatus:
        """No external probe, model initialization or DB access."""
        return ServiceStatus(
            collector_state=("capability_port_configured"
                             if self._capability_matcher.port_configured
                             else "unavailable"),
        )

    def source_capabilities(self, project_id: str) -> dict[str, Any]:
        """SP-09: read-only server-provided capabilities; never a source registry."""
        return self._capability_matcher.snapshot(_pid(project_id))

    def match_query_capabilities(self, project_id: str,
                                 query: dict[str, Any]) -> dict[str, Any]:
        """SP-09: informational capability match, not a trusted QV execution task.

        SP-10/11 must independently load a verified selected QV query from DB;
        caller input here is NEVER allowed to create Collector work.
        """
        return self._capability_matcher.match(_pid(project_id), query)

    def preview_source_tasks(self, project_id: str, validation_run_id: str,
                             *, limits: dict[str, Any] | None = None) -> dict[str, Any]:
        """SP-10: held-only plan from true PostgreSQL QV, never browser JSON."""
        return self._task_planner.preview(
            _pid(project_id), publication_uuid(validation_run_id), limits=limits,
        )

    def commit_held_source_plan(self, project_id: str, validation_run_id: str,
                                idempotency_key: str,
                                *, limits: dict[str, Any] | None = None) -> dict[str, Any]:
        """SP-10: separately approved held snapshot; not a Collector release.

        Default service intentionally lacks the owner action verifier and
        fails 403 before the first mutable SQL statement.
        """
        return self._task_planner.commit_held(
            _pid(project_id), publication_uuid(validation_run_id),
            idempotency_key, limits=limits,
        )

    def preview_verified_handoff(self, project_id: str,
                                 validation_run_id: str,
                                 *, chunk_size: int = 50) -> dict[str, Any]:
        """SP-11: DB-verified 50-query Collector-compatible chunks, read-only.

        Requires only project/QV identifiers, never accepted-client JSON.
        SP-12 alone may persist/acknowledge the SP inbox. This method cannot
        authorize Collector jobs, even when all QV queries were selected.
        """
        return self._handoff_projector.preview(
            _pid(project_id), publication_uuid(validation_run_id),
            chunk_size=chunk_size,
        )

    def ingest_verified_qv_handoff(self, project_id: str,
                                   validation_run_id: str) -> dict[str, Any]:
        """SP-12: atomic immutable inbox/chunks/QV ACK, NO Collector IO."""
        return ingest_qv_to_sp_inbox(
            _pid(project_id), publication_uuid(validation_run_id),
            projector=self._handoff_projector,
        )

    def ingest_pending_qv_handoffs(self, project_id: str,
                                   *, limit: int = 10) -> list[dict[str, Any]]:
        """Explicit bounded internal dispatcher; never a background provider loop."""
        pid = _pid(project_id)
        if type(limit) is not int or not 1 <= limit <= 50:
            raise PipelineError("Invalid QV inbox drain limit", 422)
        events = pending_validated_outbox(pid, limit=limit)
        return [
            self.ingest_verified_qv_handoff(pid, event["validation_run_id"])
            for event in events
        ]

    def get_handoff_status(self, project_id: str,
                           event_id: str) -> dict[str, Any]:
        """Read-only SP inbox + separately authenticated Collector receipts."""
        return get_sp_handoff_status(
            _pid(project_id), publication_uuid(event_id),
        )

    def deliver_next_handoff_chunk(self, project_id: str,
                                   event_id: str) -> dict[str, Any]:
        """Explicit owner-gated Collector intake ONLY; no provider execution."""
        return deliver_next_held_chunk(
            _pid(project_id), publication_uuid(event_id),
            collector_port=self._collector_handoff_port,
            owner_action_guard=self._handoff_owner_guard,
        )

    def reconcile_handoff_chunk(self, project_id: str, event_id: str,
                                chunk_index: int) -> dict[str, Any]:
        """Read-only external receipt probe to resolve uncertain intake."""
        return reconcile_uncertain_chunk(
            _pid(project_id), publication_uuid(event_id), chunk_index,
            collector_port=self._collector_handoff_port,
            owner_action_guard=self._handoff_owner_guard,
        )

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
        require_published_context_batch(snapshot, policy, contexts)
        return ContextPreview.from_approved(
            snapshot, contexts,
            max_contexts=self._options.max_preview_hypotheses,
        )

    def get_generation(self, project_id: str, generation_id: str) -> GenerationStatus:
        """Project-scoped status read, no job execution."""
        pid = _pid(project_id)
        gid = publication_uuid(generation_id)
        return GenerationStatus.from_read(pid, gid, self._generation_reader(pid, gid))

    def get_validated_query_links(self, project_id: str,
                                  validation_run_id: str) -> dict[str, Any]:
        """SP-07: trusted DB-derived shared query/hypothesis provenance.

        Does not commit source tasks or contact Collector. No JSON claims
        from callers are interpreted as a validated query decision.
        """
        return get_selected_query_links(
            _pid(project_id), publication_uuid(validation_run_id),
        )

    def get_generation_candidates(self, project_id: str,
                                  generation_id: str) -> dict[str, Any]:
        """SP-05 completed SQG output preview, read-only and Collector-free.

        Legacy query_candidates/v1 remains unchanged for QV consumers.
        An immutable digest documents exactly which candidate/coverage
        projection was returned; no fabricated video/source acceptance.
        """
        pid, gid = _pid(project_id), publication_uuid(generation_id)
        raw = self._candidate_reader(pid, gid)
        verified = verify_candidates_bundle(raw)
        if verified["project_id"] != pid or verified["generation_id"] != gid:
            raise PipelineError("Foreign Search Planner candidate bundle", 409)
        return {
            "schema_version": "search-planner-candidate-view/v1",
            "project_id": pid,
            "generation_id": gid,
            "bundle_sha256": candidate_manifest_sha256(verified),
            "bundle": verified,
            "paid_calls_in_this_read": 0,
        }

    def get_validated_queries(self, project_id: str,
                              validation_run_id: str) -> ValidatedQueriesView:
        """Only production semantic QV output; no frontend JSON acceptance."""
        pid = _pid(project_id)
        vid = publication_uuid(validation_run_id)
        bundle = self._validated_reader(pid, vid, include_test=False)
        return ValidatedQueriesView.from_read(pid, vid, bundle)

    def register_generation_config(self, project_id: str,
                                   configuration: dict[str, Any]) -> dict[str, Any]:
        """SP-03: register pinned generation configuration; no spending."""
        return self._generation_workflow.register_configuration(project_id, configuration)

    def plan_generation(self, project_id: str, publication_id: str,
                        config_id: str, idempotency_key: str) -> dict[str, Any]:
        """SP-03: idempotent, zero-provider context and batch persistence."""
        return self._generation_workflow.plan(
            project_id, publication_id, config_id, idempotency_key,
        )

    def quote_generation(self, project_id: str,
                         generation_id: str) -> dict[str, Any]:
        """SP-03: quote without money authorization or external API call."""
        return self._generation_workflow.quote(project_id, generation_id)

    def generation_budget(self, project_id: str,
                          generation_id: str) -> dict[str, Any]:
        """SP-03: inspect existing financial ledger, never advance workers."""
        return self._generation_workflow.budget(project_id, generation_id)

    def authorize_generation(self, project_id: str, generation_id: str,
                             quote_id: str, authorized_usd: str) -> dict[str, Any]:
        """SP-03: fail closed unless a trusted server-side owner gate is injected."""
        return self._generation_workflow.authorize(
            project_id, generation_id, quote_id, authorized_usd,
        )

    def revoke_generation_consent(self, project_id: str,
                                  consent_id: str) -> dict[str, Any]:
        """SP-03: separate verified owner revocation, no external provider."""
        return self._generation_workflow.revoke(project_id, consent_id)

    def generation_execution_status(self, project_id: str,
                                    generation_id: str) -> dict[str, Any]:
        """SP-04: inspect batches, attempts and ledger with zero paid calls."""
        return self._execution_controller.status(project_id, generation_id)

    def preflight_generation_execution(self, project_id: str,
                                       generation_id: str) -> dict[str, Any]:
        """SP-04: safe metadata-only preview; never return raw model prompt."""
        return self._execution_controller.preflight(project_id, generation_id)

    def execute_generation_batch(self, project_id: str,
                                 generation_id: str) -> dict[str, Any]:
        """SP-04: one explicitly approved paid batch; no implicit loop."""
        return self._execution_controller.execute_one(project_id, generation_id)

    def recover_generation_dispatches(self, project_id: str,
                                      generation_id: str,
                                      *, older_than_seconds: int = 900) -> dict[str, Any]:
        """SP-04: mark stale possibly-paid attempts uncertain, not retried."""
        return self._execution_controller.recover_stale_dispatches(
            project_id, generation_id, older_than_seconds=older_than_seconds,
        )

    def stale_generation_reservations(self, project_id: str,
                                     generation_id: str,
                                     *, older_than_seconds: int = 900) -> list[str]:
        """SP-04: read-only list; manual confirmation required before cleanup."""
        return self._execution_controller.list_stale_reservations(
            project_id, generation_id, older_than_seconds=older_than_seconds,
        )

    def release_unsubmitted_generation_attempt(
        self, project_id: str, generation_id: str, attempt_id: str,
        *, older_than_seconds: int = 900,
    ) -> dict[str, Any]:
        """SP-04: operator-gated, reserved-only release; no dispatch-intent refund."""
        return self._execution_controller.release_unsubmitted(
            project_id, generation_id, attempt_id,
            older_than_seconds=older_than_seconds,
        )

    def cancel_generation(self, project_id: str,
                          generation_id: str) -> dict[str, Any]:
        """SP-04: guarded cancellation, preserves ambiguous financial reserve."""
        return self._execution_controller.cancel(project_id, generation_id)
