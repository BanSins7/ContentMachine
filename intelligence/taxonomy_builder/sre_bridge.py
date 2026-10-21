"""Atomic Builder -> SRE taxonomy publication.

The Builder publication is the source of truth for AI-generated, human-reviewed
topic content; SRE is the canonical, immutable query/read model for video
classification. Both are committed or rolled back in the SAME DB transaction.
No LLM gets write access to SRE. Each Builder run can publish only once.
"""
from __future__ import annotations
from uuid import UUID
from .pipeline_contracts import PipelineError,canonical,sha256

def mirror_approved_taxonomy(conn, project_id, run, publication_id, topics, approved_facts):
    if not isinstance(approved_facts,list) or not approved_facts:
        raise PipelineError("Cannot publish without human-approved facts",409)
    fact_ids={str(f["id"]):f for f in approved_facts}
    if not topics or any(not set(t["fact_ids"]).issubset(fact_ids) for t in topics):
        raise PipelineError("Taxonomy has unapproved fact references",409)
    # SERIALIZE SRE writes for this project, including competing Builder runs.
    workspace=conn.execute("""INSERT INTO sre_workspaces(project_id)
        VALUES (%s) ON CONFLICT(project_id) DO UPDATE SET project_id=EXCLUDED.project_id
        RETURNING active_taxonomy_id""",(project_id,)).fetchone()
    previous_id=workspace[0]
    profile=run["profile"]
    if not isinstance(profile,dict):
        raise PipelineError("Builder run has no input snapshot",409)
    vrow=conn.execute("""SELECT coalesce(max(version),0)+1
      FROM sre_profile_versions WHERE project_id=%s""",(project_id,)).fetchone()
    profile_id=conn.execute("""INSERT INTO sre_profile_versions
      (project_id,version,product_type,functions_text,problems_text,original_intake)
      VALUES (%s,%s,%s,%s,%s,%s::jsonb) RETURNING id""",
      (project_id,vrow[0],str(profile.get("product_type") or "unspecified"),
       str(profile.get("product_functions") or profile.get("functions_text") or ""),
       str(profile.get("audience_problems") or profile.get("problems_text") or ""),
       canonical(profile))).fetchone()[0]
    for fact in approved_facts:
        origin="user_added" if fact.get("origin") in ("human_edited","human_added") else "explicit"
        quote=fact.get("source_quote") or fact["text"]
        conn.execute("""INSERT INTO sre_facts
          (id,project_id,profile_version_id,kind,original_quote,
           normalized_text,origin,review_status)
          VALUES (%s,%s,%s,%s,%s,%s,%s,'approved')""",
          (fact["id"],project_id,profile_id,fact["kind"],quote,fact["text"],origin))
    num=conn.execute("""SELECT coalesce(max(version),0)+1 FROM sre_taxonomy_versions
       WHERE project_id=%s""",(project_id,)).fetchone()[0]
    sre_id=conn.execute("""INSERT INTO sre_taxonomy_versions
        (project_id,version,parent_id,status)
        VALUES (%s,%s,%s,'draft') RETURNING id""",
        (project_id,num,previous_id)).fetchone()[0]
    for topic in topics:
        # String include/exclude in Builder maps to an array in SRE's
        # classifier rule DSL. Examples remain illustrative, NEVER verified videos.
        rules={"include":[topic["include"]],"exclude":[topic["exclude"]],
               "positive_examples":topic["positive_examples"],
               "negative_examples":topic["negative_examples"]}
        independent=not topic["fact_ids"]
        conn.execute("""INSERT INTO sre_taxonomy_categories
          (project_id,taxonomy_version_id,category_key,name,relation_type,
           definition,rules,related_fact_ids,review_status,origin,independent)
          VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,'approved',%s,%s)""",
          (project_id,sre_id,topic["id"],topic["name"],topic["relation"],
           topic["definition"],canonical(rules),canonical(topic["fact_ids"]),
           topic["origin"],independent))
        for polarity,items in (
            ("positive",topic["positive_examples"]),
            ("negative",topic["negative_examples"])
        ):
            for example in items:
                conn.execute("""INSERT INTO sre_category_examples
                  (project_id,taxonomy_version_id,category_key,polarity,
                   example_kind,example_text,video_id)
                  VALUES (%s,%s,%s,%s,'illustrative',%s,NULL)""",
                  (project_id,sre_id,topic["id"],polarity,example))
    conn.execute("""UPDATE sre_taxonomy_versions SET status='approved',
       approved_by='local_owner',approved_at=now(),snapshot_checksum=%s
       WHERE id=%s AND project_id=%s""",(sha256(topics),sre_id,project_id))
    conn.execute("""UPDATE sre_workspaces SET active_taxonomy_id=%s
       WHERE project_id=%s""",(sre_id,project_id))
    conn.execute("""INSERT INTO tb_sre_taxonomy_links
       (project_id,run_id,builder_publication_id,sre_taxonomy_version_id,source_sha256)
       VALUES (%s,%s,%s,%s,%s)""",
       (project_id,run["run_id"],publication_id,sre_id,sha256(topics)))
    return {"sre_taxonomy_version_id":sre_id,
            "sre_taxonomy_version":num,"sre_category_count":len(topics)}
