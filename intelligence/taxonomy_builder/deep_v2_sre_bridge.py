"""RTB-FIX-07: stable B9 UUID reuse and typed Deep V2 -> SRE projection.

Pure builder separates signed B9 facts from original A8 observations. Original
observations only become search hints, NEVER an approved product fact or
verified SRE video example. Same project + B9 fact SHA means same SRE UUID
across independently authorized runs.
"""
from __future__ import annotations

from copy import deepcopy
from uuid import UUID, NAMESPACE_URL, uuid5

from intelligence.project_sources.context_builder import sha as canonical_sha
from .deep_contracts_v2 import validate_deep_v2_tree
from .deep_v2_lifecycle import validate_frozen_input
from .pipeline_contracts import PipelineError
from .research_contracts_v2 import (
    b9_fact_sre_uuid, project_v2_tree_to_v1_core, _project,
)

PROJECTION_SCHEMA = "rtb-deep-v2-sre-projection/v1"
NODE_NAMESPACE = "contentmachine/rtb-deep-v2/sre-category"


def stable_sre_category_uuid(project_id, node_id):
    pid = _project(project_id)
    if not isinstance(node_id, str) or not node_id:
        raise PipelineError("rtb_v2_sre_node_id_invalid", 422)
    return str(uuid5(NAMESPACE_URL, NODE_NAMESPACE + "/" + pid + "/" + node_id))


def prepare_deep_v2_sre_projection(nodes, frozen_input):
    """Return deterministic SRE topics/facts and lossless 11-field V1 core.

    Caller must additionally replay the pinned Research Input from DB and the
    approved Human Review2 snapshot before writing any SRE rows.
    """
    source = validate_frozen_input(frozen_input)
    tree = validate_deep_v2_tree(nodes, source)
    project_id = _project(source["project_id"])
    expected = {}
    facts = []
    for row in source["c1_approved_facts"]:
        digest = row["b9_fact_id"]
        stable = b9_fact_sre_uuid(project_id, digest)
        if row["rtb_fact_id"] != stable or digest in expected:
            raise PipelineError("rtb_v2_sre_b9_fact_uuid_drift", 409)
        expected[digest] = stable
        quotes = [
            c.get("source_quote")
            for c in row["citations"]
            if isinstance(c, dict)
            and isinstance(c.get("source_quote"), str)
            and c["source_quote"].strip()
        ]
        if not quotes:
            raise PipelineError("rtb_v2_sre_original_quote_missing", 422)
        if row["kind"] not in ("function", "problem", "audience", "product_type"):
            raise PipelineError("rtb_v2_sre_fact_kind_unsupported", 422)
        facts.append({
            "id": stable, "b9_fact_sha256": digest,
            "kind": row["kind"], "text": row["reviewed_text"],
            "original_quote": quotes[0], "origin": "explicit",
            "source_role": row["source_role"],
            "subject_scope": row["subject_scope"],
        })
    if source["b9_fact_sha_to_sre_uuid"] != expected:
        raise PipelineError("rtb_v2_sre_mapping_tampered", 409)

    topics = []
    for node in tree:
        related = list(node["source_fact_ids"])
        if any(fid not in expected.values() for fid in related):
            raise PipelineError("rtb_v2_sre_unapproved_fact_id", 409)
        topics.append({
            "id": stable_sre_category_uuid(project_id, node["id"]),
            "source_node_id": node["id"],
            "parent_id": node["parent_id"],
            "kind": node["kind"], "relation": node["relation"],
            "name": node["name"], "definition": node["definition"],
            "include": [node["include"]], "exclude": [node["exclude"]],
            "positive_examples": deepcopy(node["examples"]),
            "negative_examples": [],
            "fact_ids": related,
            "origin": "inferred",
            "independent": not bool(related),
            "rationale": node["rationale"],
            "source_observation_ids": deepcopy(node["source_observation_ids"]),
            "search_signals": deepcopy(node["search_signals"]),
            "validation_focus": deepcopy(node["validation_focus"]),
        })
    core = project_v2_tree_to_v1_core(tree)
    return {
        "schema_version": PROJECTION_SCHEMA,
        "project_id": project_id,
        "research_input_sha256": source["research_input_sha256"],
        "b9_snapshot_id": source["b9_snapshot_id"],
        "b9_snapshot_sha256": source["b9_snapshot_sha256"],
        "a_context_id": source["a_context_id"],
        "a_context_sha256": source["a_context_sha256"],
        "v2_tree": deepcopy(tree),
        "v2_tree_sha256": canonical_sha(tree),
        "v1_core": core,
        "v1_core_sha256": canonical_sha(core),
        "fact_mapping": deepcopy(expected),
        "facts": facts,
        "topics": topics,
        "sre_topics_sha256": canonical_sha(topics),
        "provider_calls": 0,
    }


def mirror_deep_v2_sre_in_transaction(conn, projection, *, approved_by):
    """Write/reuse project-scoped facts then an approved taxonomy version.

    Must be called inside SAME outer transaction as RTB publication INSERT and
    run status transition; nothing is committed by this function.
    """
    project_id = projection["project_id"]
    if not isinstance(approved_by, str) or not approved_by.strip():
        raise PipelineError("rtb_v2_sre_publisher_missing", 422)
    workspace = conn.execute(
        """INSERT INTO sre_workspaces(project_id) VALUES(%s)
           ON CONFLICT(project_id) DO UPDATE SET project_id=EXCLUDED.project_id
           RETURNING active_taxonomy_id""",
        (project_id,),
    ).fetchone()
    previous_id = workspace[0]
    facts = projection["facts"]
    ids = [UUID(f["id"]) for f in facts]
    current = conn.execute(
        """SELECT id,project_id,kind,original_quote,normalized_text,
                  origin,review_status
           FROM sre_facts WHERE id=ANY(%s::uuid[]) FOR UPDATE""",
        (ids,),
    ).fetchall()
    existing = {str(r[0]): r for r in current}
    for fact in facts:
        row = existing.get(fact["id"])
        if row and (
            row[1] != project_id or row[2] != fact["kind"]
            or row[3] != fact["original_quote"]
            or row[4] != fact["text"]
            or row[5] != fact["origin"] or row[6] != "approved"
        ):
            raise PipelineError("rtb_v2_sre_existing_fact_content_conflict", 409)

    to_insert = [f for f in facts if f["id"] not in existing]
    if to_insert:
        number = conn.execute(
            """SELECT COALESCE(MAX(version),0)+1 FROM sre_profile_versions
               WHERE project_id=%s""",
            (project_id,),
        ).fetchone()[0]
        product = next(
            (f["text"] for f in facts if f["kind"] == "product_type"
             and f["source_role"] == "own_product"),
            "",
        )
        functions_text = "\n".join(
            f["text"] for f in facts
            if f["kind"] == "function" and f["source_role"] == "own_product"
        )
        problems_text = "\n".join(
            f["text"] for f in facts if f["kind"] == "problem"
        )
        from psycopg.types.json import Jsonb
        profile_id = conn.execute(
            """INSERT INTO sre_profile_versions(
                 project_id,version,product_type,functions_text,
                 problems_text,original_intake)
               VALUES(%s,%s,%s,%s,%s,%s) RETURNING id""",
            (
                project_id, number, product, functions_text, problems_text,
                Jsonb({
                    "origin": "rtb-deep-v2-b9-approved-only",
                    "b9_snapshot_id": projection["b9_snapshot_id"],
                    "research_input_sha256": projection["research_input_sha256"],
                    "fact_sha_to_uuid": projection["fact_mapping"],
                }),
            ),
        ).fetchone()[0]
        for fact in to_insert:
            conn.execute(
                """INSERT INTO sre_facts(
                     id,project_id,profile_version_id,kind,original_quote,
                     normalized_text,origin,review_status)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,'approved')""",
                (
                    UUID(fact["id"]), project_id, profile_id, fact["kind"],
                    fact["original_quote"], fact["text"], fact["origin"],
                ),
            )
    version = conn.execute(
        """SELECT COALESCE(MAX(version),0)+1 FROM sre_taxonomy_versions
           WHERE project_id=%s""",
        (project_id,),
    ).fetchone()[0]
    sre_id = conn.execute(
        """INSERT INTO sre_taxonomy_versions(project_id,version,parent_id,status)
           VALUES(%s,%s,%s,'draft') RETURNING id""",
        (project_id, version, previous_id),
    ).fetchone()[0]
    from psycopg.types.json import Jsonb
    for topic in projection["topics"]:
        rules = {
            "include": topic["include"],
            "exclude": topic["exclude"],
            "positive_examples": topic["positive_examples"],
            "negative_examples": topic["negative_examples"],
            "hierarchy": {
                "source_node_id": topic["source_node_id"],
                "parent_id": topic["parent_id"],
                "kind": topic["kind"],
                "rationale": topic["rationale"],
            },
            "rtb_v2": {
                "research_input_sha256": projection["research_input_sha256"],
                "source_observation_ids": topic["source_observation_ids"],
                "search_signals": topic["search_signals"],
                "validation_focus": topic["validation_focus"],
                "original_a_observations_are_not_product_facts": True,
            },
        }
        category = conn.execute(
            """INSERT INTO sre_taxonomy_categories(
                 project_id,taxonomy_version_id,category_key,name,
                 relation_type,definition,rules,related_fact_ids,
                 review_status,origin,independent)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,'approved',%s,%s)
               RETURNING id""",
            (
                project_id, sre_id, UUID(topic["id"]), topic["name"],
                topic["relation"], topic["definition"],
                Jsonb(rules), Jsonb(topic["fact_ids"]),
                topic["origin"], topic["independent"],
            ),
        ).fetchone()[0]
        for example in topic["positive_examples"]:
            conn.execute(
                """INSERT INTO sre_category_examples(
                     project_id,taxonomy_version_id,category_key,polarity,
                     example_kind,example_text,video_id)
                   VALUES(%s,%s,%s,'positive','illustrative',%s,NULL)""",
                (project_id, sre_id, UUID(topic["id"]), example),
            )
    conn.execute(
        """UPDATE sre_taxonomy_versions
           SET status='approved',approved_by=%s,approved_at=clock_timestamp(),
               snapshot_checksum=%s
           WHERE id=%s AND project_id=%s AND status='draft'""",
        (approved_by, projection["sre_topics_sha256"], sre_id, project_id),
    )
    conn.execute(
        """UPDATE sre_workspaces SET active_taxonomy_id=%s
           WHERE project_id=%s""",
        (sre_id, project_id),
    )
    return {
        "sre_taxonomy_version_id": sre_id,
        "sre_taxonomy_version": version,
        "reused_fact_count": len(existing),
        "new_fact_count": len(to_insert),
        "category_count": len(projection["topics"]),
    }
