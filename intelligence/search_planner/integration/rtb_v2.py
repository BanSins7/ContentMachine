"""SP-side consumer boundary for RTB FIX-08 verified Deep V2 exports.

RTB owns the approved export and reviewer authorization. SP must never trust
browser-uploaded JSON, raw V2 nodes, or a silent 14-to-11 projection.
The verified producer and validator are optional server-owned dependencies.
This read-only preview deliberately does NOT authorize SQG generation or
replace V1's existing tb_taxonomy_publications FK.
"""
from __future__ import annotations

from importlib import import_module

from intelligence.taxonomy_builder.pipeline_contracts import PipelineError
from intelligence.taxonomy_builder.pipeline_repository import _pid
from intelligence.search_planner.contracts import publication_uuid

EXPORT_V1 = "rtb-publication-export/v1"
EXPORT_V2 = "rtb-publication-export/v2"
NODE_CORE = frozenset((
    "id", "parent_id", "kind", "relation", "source_fact_ids", "name",
    "rationale", "definition", "include", "exclude", "examples",
))
NODE_V2 = NODE_CORE | frozenset((
    "source_observation_ids", "search_signals", "validation_focus",
))


class RTBV2VerifiedExportPreview:
    """Accept ONLY server-owned RTB export port with its reviewer bound.

    A callable must provide (project_id, publication_id, accepted_versions).
    The real RTB verifier is imported *only on explicit preview invocation*
    after the independent RTB branch has been integrated. The optional
    verifier injection is for isolated contract tests, never HTTP input.
    """

    def __init__(self, *, export_supplier=None, validator=None):
        if export_supplier is not None and not callable(export_supplier):
            raise PipelineError("Untrusted RTB export source", 422)
        if validator is not None and not callable(validator):
            raise PipelineError("Invalid RTB verification function", 422)
        self._supplier = export_supplier
        self._validator = validator

    def preview(self, project_id, publication_id, *, accepted_version=EXPORT_V2):
        pid = _pid(project_id)
        pub = publication_uuid(publication_id)
        if accepted_version not in (EXPORT_V1, EXPORT_V2):
            raise PipelineError("Unsupported RTB export version", 422)
        if self._supplier is None:
            raise PipelineError("Trusted RTB V2 publication export is not connected", 503)
        checker = self._validator
        if checker is None:
            try:
                module = import_module("intelligence.taxonomy_builder.publication_export_v2")
            except ModuleNotFoundError as exc:
                if exc.name == "intelligence.taxonomy_builder.publication_export_v2":
                    raise PipelineError("RTB Deep V2 export module not installed", 503) from None
                raise
            checker = module.validate_publication_export
        # Actual producer must enforce the server reviewer/ACL, immutable DB
        # source replay, approvals, source policy and no provider spend.
        data = self._supplier(pid, pub, accepted_versions=[accepted_version])
        try:
            confirmed = checker(data, expected_version=accepted_version)
        except PipelineError:
            raise
        except (ValueError, TypeError, KeyError) as exc:
            raise PipelineError("RTB source export integrity verification failed", 409) from exc
        if (type(confirmed) is not dict or
                confirmed.get("project_id") != pid or
                confirmed.get("publication_id") != pub or
                confirmed.get("schema_version") != accepted_version or
                confirmed.get("publication_status") != "published" or
                confirmed.get("human_reviews") != [1, 2] or
                type(confirmed.get("nodes")) is not list or
                not 2 <= len(confirmed["nodes"]) <= 48 or
                type(confirmed.get("approved_facts")) is not list or
                not confirmed["approved_facts"] or
                type(confirmed.get("source_lineage")) is not dict):
            raise PipelineError("RTB V2 approved export identity mismatch", 409)
        required_node_keys = NODE_V2 if accepted_version == EXPORT_V2 else NODE_CORE
        if any(type(node) is not dict or set(node) != required_node_keys
               for node in confirmed["nodes"]):
            raise PipelineError("RTB export schema cannot be silently downgraded", 409)
        lineage = confirmed["source_lineage"]
        required = ("research_input_sha256", "b9_snapshot_id",
                    "b9_snapshot_sha256", "a_context_id",
                    "a_context_sha256", "readiness_report_sha256")
        if any(not isinstance(lineage.get(k), str) or not lineage[k] for k in required):
            raise PipelineError("RTB B9/A8 approved provenance unavailable", 409)
        if type(confirmed.get("export_sha256")) is not str or len(confirmed["export_sha256"]) != 64:
            raise PipelineError("RTB verified export hash unavailable", 409)
        # No node contents, citations or media are returned through the
        # diagnostic endpoint. This is NOT an approved Context publication.
        return {
            "schema_version": "search-planner-rtb-export-preview/v1",
            "project_id": pid, "publication_id": pub,
            "rtb_export_schema_version": accepted_version,
            "export_sha256": confirmed["export_sha256"],
            "tree_sha256": confirmed["tree_sha256"],
            "v1_core_sha256": confirmed["v1_core_sha256"],
            "approved_fact_count": len(confirmed["approved_facts"]),
            "node_count": len(confirmed["nodes"]),
            "a8_observations_present": (
                type(confirmed.get("original_a_observations")) is list
                if accepted_version == EXPORT_V2 else False
            ),
            "review1_and_review2_verified_by_rtb": True,
            "source_identity_verified_by_rtb": True,
            "v2_context_consumer_activated": False,
            "sqg_generation_authorized": False,
            "legacy_v1_fk_replaced": False,
            "paid_provider_calls": 0,
            "external_source_calls": 0,
        }
