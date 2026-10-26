"""RTB FIX-08: version-negotiated, read-only published Deep V2 export.

Standalone RTB-owned adapter. No Search Planner/relevance code changes and
NO paid model calls or database writes. Legacy Search Planner input_loader
still reads its own V1 tables; consumers adopt this explicit adapter when
they choose RTB Deep V2 publications.
"""
from __future__ import annotations

from copy import deepcopy
from uuid import UUID

from intelligence.knowledge_extraction.human_review import _auth, _reviewer
from intelligence.project_sources.context_builder import sha as canonical_sha
from .deep_contracts import validate_tree
from .deep_contracts_v2 import validate_deep_v2_tree
from .deep_v2_lifecycle import validate_frozen_input
from .deep_v2_publication import RTBDeepV2PublicationRepository
from .deep_v2_quote_repository import _fail
from .deep_v2_sre_bridge import prepare_deep_v2_sre_projection
from .research_contracts_v2 import (
    _project, _uuid, project_v2_tree_to_v1_core,
)

SCHEMA_V1 = "rtb-publication-export/v1"
SCHEMA_V2 = "rtb-publication-export/v2"
SUPPORTED_VERSIONS = (SCHEMA_V1, SCHEMA_V2)
CORE_FIELDS = (
    "id", "parent_id", "kind", "relation", "source_fact_ids",
    "name", "rationale", "definition", "include", "exclude", "examples",
)
V2_EXTRA_FIELDS = ("source_observation_ids", "search_signals", "validation_focus")
MAX_EXPORT_NODES = 48


def _explicit_version(accepted_versions):
    if not isinstance(accepted_versions, (list, tuple)) or not accepted_versions:
        _fail("rtb_v2_export_requires_explicit_supported_version", 422)
    if (len(accepted_versions) > 2 or
            any(not isinstance(v, str) or v not in SUPPORTED_VERSIONS
                for v in accepted_versions) or
            len(set(accepted_versions)) != len(accepted_versions)):
        _fail("rtb_v2_export_version_not_supported", 422)
    # Caller-ordered preference, never implicit downgrade.
    return accepted_versions[0]


def _approved_b9_fact_views(source):
    facts = []
    for f in source["c1_approved_facts"]:
        quotes = [
            citation["source_quote"]
            for citation in f["citations"]
            if isinstance(citation, dict) and
            isinstance(citation.get("source_quote"), str) and
            citation["source_quote"].strip()
        ]
        if not quotes:
            _fail("rtb_v2_export_fact_missing_original_quote")
        facts.append({
            "id": f["rtb_fact_id"],
            "kind": f["kind"],
            "text": f["reviewed_text"],
            "source_quote": quotes[0],
            "origin": "explicit",
            "b9_fact_sha256": f["b9_fact_id"],
            "source_role": f["source_role"],
            "subject_scope": f["subject_scope"],
        })
    return facts


def _approved_profile_view(source):
    """No implied goals/markets/languages or own-product claims from A video."""
    rows = source["c1_approved_facts"]
    own = [f for f in rows if
           f["source_role"] == "own_product" and
           f["subject_scope"] == "own_product"]
    product_type = next(
        (f["reviewed_text"] for f in own if f["kind"] == "product_type"), None,
    )
    functions = [f["reviewed_text"] for f in own if f["kind"] == "function"]
    problems = [f["reviewed_text"] for f in rows if f["kind"] == "problem"]
    return {
        "product_type": product_type,
        "product_functions": functions,
        "audience_problems": problems,
        "target_audience": None,
        "research_goal": None,
    }


def validate_publication_export(export, *, expected_version=None):
    """Pure integrity verification for DTOs exchanged between modules.

    This does not replace authenticated, source-replayed SQL loading.
    """
    if not isinstance(export, dict):
        _fail("rtb_v2_export_not_object")
    version = export.get("schema_version")
    if version not in SUPPORTED_VERSIONS or (
        expected_version is not None and version != expected_version
    ):
        _fail("rtb_v2_export_version_invalid")
    if export.get("publication_status") != "published":
        _fail("rtb_v2_export_unpublished")
    pid = _project(export.get("project_id"))
    _uuid(export.get("publication_id"), "rtb_v2_export_publication_id")
    _uuid(export.get("run_id"), "rtb_v2_export_run_id")
    if (export.get("human_reviews") != [1, 2]
            or type(export.get("sre_taxonomy_version_id")) is not int
            or export["sre_taxonomy_version_id"] < 1
            or not isinstance(export.get("source_lineage"), dict)
            or not isinstance(export.get("approved_facts"), list)
            or not export["approved_facts"]):
        _fail("rtb_v2_export_missing_approval_or_source")
    facts = export["approved_facts"]
    known = set()
    for fact in facts:
        if (not isinstance(fact, dict)
                or not all(isinstance(fact.get(k), str) and fact[k]
                           for k in ("id", "kind", "text", "source_quote",
                                     "b9_fact_sha256", "source_role", "subject_scope"))
                or fact["id"] in known):
            _fail("rtb_v2_export_invalid_approved_fact")
        known.add(fact["id"])
    data = export.get("nodes")
    if not isinstance(data, list) or not 2 <= len(data) <= MAX_EXPORT_NODES:
        _fail("rtb_v2_export_tree_invalid")
    allowed = set(CORE_FIELDS) | (set(V2_EXTRA_FIELDS) if version == SCHEMA_V2 else set())
    if any(not isinstance(node, dict) or set(node) != allowed for node in data):
        _fail("rtb_v2_export_node_field_mismatch")
    core = project_v2_tree_to_v1_core(data) if version == SCHEMA_V2 else deepcopy(data)
    verified = validate_tree(core, facts)
    if verified != core:
        _fail("rtb_v2_export_core_normalization_drift")
    if version == SCHEMA_V1:
        if "original_a_observations" in export:
            _fail("rtb_v2_export_legacy_contains_v2_observations")
    else:
        observations = export.get("original_a_observations")
        if not isinstance(observations, list):
            _fail("rtb_v2_export_original_a_missing")
        ids = {
            o.get("observation_id")
            for o in observations if isinstance(o, dict)
        }
        if None in ids or len(ids) != len(observations):
            _fail("rtb_v2_export_original_a_identity_invalid")
        for node in data:
            if not all(isinstance(node[k], list) for k in V2_EXTRA_FIELDS):
                _fail("rtb_v2_export_v2_extra_field_invalid")
            if not set(node["source_observation_ids"]).issubset(ids):
                _fail("rtb_v2_export_a_observation_not_pinned")
    computed_tree = canonical_sha(data)
    if export.get("tree_sha256") != computed_tree:
        _fail("rtb_v2_export_tree_checksum_drift")
    if export.get("v1_core_sha256") != canonical_sha(core):
        _fail("rtb_v2_export_core_checksum_drift")
    if (not isinstance(export.get("source_lineage"), dict) or
            export["source_lineage"].get("research_input_sha256") is None or
            export["source_lineage"].get("b9_fact_sha_to_sre_uuid") is None):
        _fail("rtb_v2_export_lineage_missing")
    mapping = export["source_lineage"]["b9_fact_sha_to_sre_uuid"]
    if not isinstance(mapping, dict) or {
        f["b9_fact_sha256"]: f["id"] for f in facts
    } != mapping:
        _fail("rtb_v2_export_fact_mapping_mismatch")
    checksum = export.get("export_sha256")
    body = {k: deepcopy(v) for k, v in export.items() if k != "export_sha256"}
    if checksum != canonical_sha(body):
        _fail("rtb_v2_export_checksum_drift")
    return deepcopy(export)


def build_publication_export(publication, source, *,
                             accepted_versions, human_review_numbers=(1, 2)):
    """Pure deterministic DTO from an authenticated, verified DB publication."""
    version = _explicit_version(accepted_versions)
    frozen = validate_frozen_input(source)
    pid = _project(frozen["project_id"])
    if (publication.get("project_id") != pid
            or publication.get("status") != "published"
            or publication.get("research_input_sha256") !=
            frozen["research_input_sha256"]
            or list(human_review_numbers) != [1, 2]):
        _fail("rtb_v2_export_project_or_approval_mismatch")
    projection = prepare_deep_v2_sre_projection(publication["v2_tree"], frozen)
    if (
        projection["v2_tree_sha256"] != publication["v2_tree_sha256"]
        or projection["v1_core_sha256"] != publication["v1_core_sha256"]
        or projection["v1_core"] != publication["v1_core"]
        or projection["fact_mapping"] != publication["fact_mapping"]
        or projection["sre_topics_sha256"] != publication["sre_topics_sha256"]
    ):
        _fail("rtb_v2_export_published_projection_mismatch")
    facts = _approved_b9_fact_views(frozen)
    body = {
        "schema_version": version,
        "project_id": pid,
        "publication_id": publication["publication_id"],
        "run_id": publication["run_id"],
        "publication_status": "published",
        "human_reviews": [1, 2],
        "sre_taxonomy_version_id": publication["sre_taxonomy_version_id"],
        "tree_sha256": (
            publication["v1_core_sha256"]
            if version == SCHEMA_V1 else publication["v2_tree_sha256"]
        ),
        "v1_core_sha256": publication["v1_core_sha256"],
        "nodes": deepcopy(
            publication["v1_core"] if version == SCHEMA_V1
            else publication["v2_tree"]
        ),
        "approved_facts": facts,
        "profile_snapshot": _approved_profile_view(frozen),
        "source_lineage": {
            "research_input_sha256": frozen["research_input_sha256"],
            "b9_snapshot_id": frozen["b9_snapshot_id"],
            "b9_snapshot_sha256": frozen["b9_snapshot_sha256"],
            "b9_handoff_id": frozen["b9_handoff_id"],
            "b9_handoff_sha256": frozen["b9_handoff_sha256"],
            "a_context_id": frozen["a_context_id"],
            "a_context_sha256": frozen["a_context_sha256"],
            "b9_fact_sha_to_sre_uuid": deepcopy(projection["fact_mapping"]),
            "readiness_report_sha256": publication["readiness_report_sha256"],
            "sre_topics_sha256": publication["sre_topics_sha256"],
            "observations_are_original_a_not_approved_product_facts": True,
        },
        "provider_calls": 0,
    }
    if version == SCHEMA_V2:
        body["original_a_observations"] = deepcopy(
            frozen["c2_original_video_observations"]
        )
    signed = {**body, "export_sha256": canonical_sha(body)}
    return validate_publication_export(signed, expected_version=version)


def to_search_planner_v1_snapshot(export):
    """Shape accepted by existing Search Planner v1 contracts/context_builder.

    IMPORTANT: This is an adapter output, NOT a replacement for the legacy
    input_loader's SQL path. SP V2 ingestion belongs in its separate branch.
    """
    data = validate_publication_export(export, expected_version=SCHEMA_V1)
    return {
        "project_id": data["project_id"],
        "rtb_run_id": data["run_id"],
        "taxonomy_publication_id": data["publication_id"],
        "taxonomy_version": data["sre_taxonomy_version_id"],
        "tree": deepcopy(data["nodes"]),
        "approved_facts": [{
            "id": f["id"], "kind": f["kind"], "text": f["text"],
            "source_quote": f["source_quote"], "origin": f["origin"],
        } for f in data["approved_facts"]],
        "profile_snapshot": deepcopy(data["profile_snapshot"]),
        # SP V1 contract's approved statuses mean both human gates passed,
        # NOT the historical tb_runs.status. The actual V2 state is published.
        "run_status": "approved",
        "deep_status": "approved",
        "human_reviews": [1, 2],
    }


def to_sre_taxonomy_reference(export):
    """Read-only SRE handoff: reuse already APPROVED SRE version by exact ID.

    Never creates categories or silently activates a new version. SRE queries
    its own canonical taxonomy tables, using this signed RTB source pointer.
    """
    data = validate_publication_export(export)
    body = {
        "schema_version": "rtb-sre-taxonomy-reference/v1",
        "project_id": data["project_id"],
        "publication_id": data["publication_id"],
        "sre_taxonomy_version_id": data["sre_taxonomy_version_id"],
        "sre_topics_sha256": data["source_lineage"]["sre_topics_sha256"],
        "research_input_sha256": data["source_lineage"]["research_input_sha256"],
        "b9_fact_sha_to_sre_uuid": deepcopy(
            data["source_lineage"]["b9_fact_sha_to_sre_uuid"]),
        "v1_core_sha256": data["v1_core_sha256"],
        "category_count": len(data["nodes"]),
        "no_sre_writes": True,
    }
    return {**body, "reference_sha256": canonical_sha(body)}


class RTBDeepV2PublicationExportRepository(RTBDeepV2PublicationRepository):
    """RTB-owned read-only query by publication UUID, not a payable action."""

    def export(self, project_id, publication_id, reviewer_id, *,
               accepted_versions):
        pid = _project(project_id)
        pubid = _uuid(publication_id, "rtb_v2_export_publication_id")
        actor = _reviewer(reviewer_id)
        selected = _explicit_version(accepted_versions)
        with self.connection_factory() as conn:
            with conn.transaction():
                conn.execute(
                    "SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"
                )
                _auth(conn, pid, actor)
                record = conn.execute(
                    """SELECT run_id,binding_id FROM tb_deep_v2_publications
                       WHERE project_id=%s AND id=%s""",
                    (pid, pubid),
                ).fetchone()
                if not record:
                    _fail("rtb_v2_export_publication_not_found", 404)
                run_id, binding_id = str(record[0]), str(record[1])
                state = conn.execute(
                    """SELECT status FROM tb_deep_v2_runs
                       WHERE project_id=%s AND id=%s AND binding_id=%s""",
                    (pid, run_id, binding_id),
                ).fetchone()
                if not state or state[0] != "published":
                    _fail("rtb_v2_export_not_published", 409)
                pub = self._read_publication(conn, pid, run_id)
                if not pub or pub["publication_id"] != pubid:
                    _fail("rtb_v2_export_publication_identity_drift")
                reviews = conn.execute(
                    """SELECT review_no FROM tb_deep_v2_reviews
                       WHERE project_id=%s AND run_id=%s ORDER BY review_no""",
                    (pid, run_id),
                ).fetchall()
                if [r[0] for r in reviews] != [1, 2]:
                    _fail("rtb_v2_export_human_approvals_missing")
        # Binding repository independently replays REAL B9 human-reviewed
        # snapshot and signed original A video refs; immutable DB rows permit
        # a read-only second snapshot here without source drift.
        pinned = self.bindings.get(pid, binding_id, actor)
        if pinned["research_input_sha256"] != pub["research_input_sha256"]:
            _fail("rtb_v2_export_binding_replay_drift")
        return build_publication_export(
            pub, pinned["research_input"],
            accepted_versions=[selected],
            human_review_numbers=[1, 2],
        )
