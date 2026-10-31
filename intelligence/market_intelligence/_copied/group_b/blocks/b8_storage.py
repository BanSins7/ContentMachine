"""GB-MOD-20 opt-in SQL005 B8 assignment: ONLY approved existing IDs.

Exactly one canonical category>topic>optional-subtopic assignment. No registry
mutations, no new slug, no merge, no automatic human approval. The caller
owns a single transaction, fresh source-rights and paid-provider admission.
"""
from __future__ import annotations
from collections.abc import Mapping

from .b8_topic import B8TopicProposal, B8TopicError
from ..context.manifest import MediaEvidenceManifest
from ..topic_registry import TaxonomySnapshot,read_topic_registry


def _deny(reason):
    raise B8TopicError(reason)


def _row(cursor):
    value=cursor.fetchone()
    if value is None:
        return None
    if isinstance(value,Mapping):
        return dict(value)
    return dict(zip((x.name for x in cursor.description),value))


def persist_b8_topic(conn,*,analysis_id:int,manifest:MediaEvidenceManifest,
                     registry:TaxonomySnapshot,proposal:B8TopicProposal,
                     admission_verifier:object):
    """Caller transaction required. Verifier must freshly authorize project,
    QDP/A version, media bytes, source rights, model receipt and registry.
    """
    if (type(analysis_id) is not int or analysis_id<=0 or
        not isinstance(manifest,MediaEvidenceManifest) or
        not manifest.verify_digest() or
        not isinstance(registry,TaxonomySnapshot) or
        not registry.verify_digest() or
        not isinstance(proposal,B8TopicProposal) or
        not proposal.can_persist):
        _deny("b8_assignment_not_approved")
    p=proposal.payload
    expected={
        "project_id":manifest.project_id,"b_run_id":manifest.b_run_id,
        "video_id":manifest.video_id,"qdp_run_id":manifest.qdp_run_id,
        "ga_result_id":manifest.ga_result_id,
        "ga_result_sha256":manifest.ga_result_sha256,
        "source_policy_version_id":manifest.source_policy_version_id,
        "source_evidence_manifest_sha256":manifest.manifest_sha256,
        "content_kind":manifest.content_kind,
        "registry_snapshot_sha256":registry.snapshot_sha256,
    }
    if any(p.get(k)!=v for k,v in expected.items()):
        _deny("b8_source_or_registry_pins_mismatch")
    verify=getattr(admission_verifier,"verify_topic_persistence",None)
    if not callable(verify):
        _deny("b8_fresh_admission_missing")
    try:
        accepted=verify(analysis_id,manifest,registry,proposal)
    except Exception:
        _deny("b8_fresh_admission_failed")
    if accepted is not True:
        _deny("b8_fresh_admission_failed")
    parent=_row(conn.execute(
        """SELECT id,video_id,source_id,content_type,state,
                  source_policy_version_id,method_details
           FROM content_b_analyses WHERE id=%s FOR UPDATE""",
        (analysis_id,)
    ))
    if parent is None or parent["state"] not in ("pending","partial"):
        _deny("b8_analysis_not_mutable")
    if (parent["video_id"]!=manifest.video_id or
        parent["source_id"]!=manifest.source_id or
        parent["source_policy_version_id"]!=manifest.source_policy_version_id or
        parent["content_type"]!=(
            "video" if manifest.content_kind in ("video","slideshow_video")
            else "carousel"
        )):
        _deny("b8_parent_source_not_current")
    details=parent["method_details"]
    pins={
        "group_b_project_id":manifest.project_id,
        "group_b_run_id":manifest.b_run_id,
        "group_b_media_evidence_manifest_sha256":manifest.manifest_sha256,
        "group_b_b8_topic_proposal_sha256":proposal.proposal_sha256,
        "group_b_b8_registry_snapshot_sha256":registry.snapshot_sha256,
    }
    if type(details) is not dict or any(details.get(k)!=v for k,v in pins.items()):
        _deny("b8_parent_version_pins_missing")
    # Hold registry table SHARE locks through the caller's commit. A bare
    # three-query snapshot under READ COMMITTED could change between the
    # freshness check and INSERT; these locks prevent that TOCTOU window.
    # Writers (merges/renames/new terms) must finish before this re-read.
    conn.execute(
        "LOCK TABLE content_b_categories, content_b_topics, "
        "content_b_subtopics IN SHARE MODE"
    )
    # Re-read authoritative SQL005 taxonomy under those saving-transaction locks.
    fresh=read_topic_registry(conn)
    if fresh.snapshot_sha256!=registry.snapshot_sha256:
        _deny("b8_registry_changed_before_save")
    category_id,category_alias=fresh.resolve("category",p["category_label"])
    topic_id,topic_alias=fresh.resolve("topic",p["topic_label"],category_id)
    if (category_alias or category_id!=p["category_id"] or
        topic_id!=p["topic_id"] or topic_id is None):
        _deny("b8_canonical_parent_or_topic_changed")
    if p["subtopic_label"]:
        subtopic_id,subtopic_alias=fresh.resolve(
            "subtopic",p["subtopic_label"],topic_id
        )
    else:
        subtopic_id,subtopic_alias=None,False
    if subtopic_id!=p["subtopic_id"]:
        _deny("b8_subtopic_id_changed")
    if conn.execute(
        "SELECT EXISTS(SELECT 1 FROM content_b_topic_assignments WHERE analysis_id=%s)",
        (analysis_id,)
    ).fetchone()[0]:
        _deny("b8_assignment_replay_forbidden")
    data=proposal.db_fields()
    conn.execute(
        """INSERT INTO content_b_topic_assignments(
             analysis_id,category_id,topic_id,subtopic_id,taxonomy_version,needs_review)
           VALUES (%s,%s,%s,%s,%s,%s)""",
        (analysis_id,data["category_id"],data["topic_id"],
         data["subtopic_id"],data["taxonomy_version"],data["needs_review"]),
    )
    conn.execute(
        """UPDATE content_b_analyses SET state='partial'
           WHERE id=%s AND state IN ('pending','partial')""",
        (analysis_id,),
    )
    stored=conn.execute(
        """SELECT category_id,topic_id,subtopic_id,taxonomy_version,needs_review
           FROM content_b_topic_assignments WHERE analysis_id=%s""",
        (analysis_id,),
    ).fetchone()
    if stored is None or tuple(stored)!=tuple(data[k] for k in (
        "category_id","topic_id","subtopic_id","taxonomy_version","needs_review"
    )):
        _deny("b8_sql005_readback_mismatch")
    return data
