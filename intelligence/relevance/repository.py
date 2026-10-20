"""Transactional persistence of project taxonomy without direct LLM SQL access.

Only server-controlled methods may modify draft records. The DB trigger makes
approved taxonomy versions and categories immutable at the database level.
"""
from __future__ import annotations
from uuid import UUID
from .contracts import ContractError, validate_profile, validate_taxonomy, validate_pipeline

def _project_id(value):
    if not isinstance(value,str) or len(value)!=32:
        raise ContractError("Expected local project UUID.hex")
    try:
        canonical=UUID(hex=value).hex
    except (ValueError, AttributeError):
        raise ContractError("Invalid project id") from None
    if canonical!=value:
        raise ContractError("Project id must be canonical lower-case UUID.hex")
    return canonical

def create_workspace(conn, project_id: str, *, project_lookup=None) -> None:
    """Fail closed unless the ID resolves to an existing on-disk local project.

    Service/API callers must still authenticate the requesting local owner.
    The injected lookup is intended for isolated DB fixtures, not for HTTP input.
    """
    project_id=_project_id(project_id)
    if project_lookup is None:
        from projects import get_project
        project_lookup=get_project
    record=project_lookup(project_id)
    if not isinstance(record,dict) or record.get("id")!=project_id:
        raise ContractError("Unknown project in the ContentMachine project catalog")
    conn.execute("""INSERT INTO sre_workspaces(project_id)
                    VALUES (%s) ON CONFLICT(project_id) DO NOTHING""",(project_id,))

def save_profile(conn, project_id: str, profile: dict) -> int:
    project_id=_project_id(project_id)
    validated=validate_profile(profile)
    with conn.transaction():
        conn.execute("SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR UPDATE",(project_id,)).fetchone() or _missing()
        row=conn.execute("""SELECT coalesce(max(version),0)+1
             FROM sre_profile_versions WHERE project_id=%s""",(project_id,)).fetchone()
        return conn.execute("""INSERT INTO sre_profile_versions
            (project_id,version,product_type,functions_text,problems_text,original_intake)
            VALUES (%s,%s,%s,%s,%s,%s::jsonb) RETURNING id""",
            (project_id,row[0],validated["product_type"],validated["functions_text"],
             validated["problems_text"],__import__("json").dumps(profile,ensure_ascii=False))
            ).fetchone()[0]

def _missing():
    raise ContractError("Project workspace does not exist")

def create_taxonomy_draft(conn, project_id: str, categories: list[dict], parent_id: int | None = None) -> int:
    project_id=_project_id(project_id)
    rules=validate_taxonomy(categories)
    import json
    with conn.transaction():
        conn.execute("SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR UPDATE",(project_id,)).fetchone() or _missing()
        if parent_id is not None:
            conn.execute("""SELECT 1 FROM sre_taxonomy_versions WHERE id=%s AND project_id=%s
                             AND status='approved'""",(parent_id,project_id)).fetchone() or _missing()
        version=conn.execute("""SELECT coalesce(max(version),0)+1
             FROM sre_taxonomy_versions WHERE project_id=%s""",(project_id,)).fetchone()[0]
        tid=conn.execute("""INSERT INTO sre_taxonomy_versions(project_id,version,parent_id)
                    VALUES(%s,%s,%s) RETURNING id""",(project_id,version,parent_id)).fetchone()[0]
        for row in rules:
            conn.execute("""INSERT INTO sre_taxonomy_categories
              (project_id,taxonomy_version_id,category_key,name,relation_type,definition,
               rules,related_fact_ids,origin,independent)
              VALUES(%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)""",
              (project_id,tid,row["category_key"],row["name"],row["relation_type"],
               row["definition"],json.dumps(row["rules"],ensure_ascii=False),
               json.dumps(row["related_fact_ids"],ensure_ascii=False),
               row["origin"],row["independent"]))
            _insert_examples(conn,project_id,tid,row)
        return tid

def _insert_examples(conn,project_id,tid,row):
    """Typed examples; the FK ensures verified videos belong to this project."""
    for polarity in ("positive","negative"):
        for example in row["examples"][polarity]:
            conn.execute("""INSERT INTO sre_category_examples
                    (project_id,taxonomy_version_id,category_key,polarity,example_kind,
                     example_text,video_id) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (project_id,tid,row["category_key"],polarity,
                     example["example_kind"],example["text"],example["video_id"]))

def review_category(conn, project_id: str, taxonomy_id: int, category_key: str,
                    status: str, reviewer: str, *, replacement: dict | None = None) -> None:
    project_id=_project_id(project_id)
    if status not in ("approved","rejected"):
        raise ContractError("Invalid review status")
    if not reviewer.strip():
        raise ContractError("reviewer required")
    from .contracts import validate_category
    import json
    with conn.transaction():
        conn.execute("""SELECT 1 FROM sre_taxonomy_versions WHERE id=%s
                       AND project_id=%s AND status='draft' FOR UPDATE""",
                     (taxonomy_id,project_id)).fetchone() or _missing()
        old=conn.execute("""SELECT name,definition,rules,review_status FROM sre_taxonomy_categories
                          WHERE taxonomy_version_id=%s AND project_id=%s
                          AND category_key=%s FOR UPDATE""",
                         (taxonomy_id,project_id,category_key)).fetchone()
        if not old:
            _missing()
        if replacement is None:
            conn.execute("""UPDATE sre_taxonomy_categories SET review_status=%s
                    WHERE taxonomy_version_id=%s AND project_id=%s AND category_key=%s""",
                    (status,taxonomy_id,project_id,category_key))
        else:
            clean=validate_category(replacement)
            if clean["category_key"] != str(category_key):
                raise ContractError("Cannot replace a category's stable id")
            conn.execute("""UPDATE sre_taxonomy_categories
                    SET name=%s,relation_type=%s,definition=%s,rules=%s::jsonb,
                        related_fact_ids=%s::jsonb,review_status=%s,origin=%s,independent=%s
                    WHERE taxonomy_version_id=%s AND project_id=%s AND category_key=%s""",
                    (clean["name"],clean["relation_type"],clean["definition"],
                     json.dumps(clean["rules"],ensure_ascii=False),
                     json.dumps(clean["related_fact_ids"]),status,clean["origin"],clean["independent"],
                     taxonomy_id,project_id,category_key))
            conn.execute("""DELETE FROM sre_category_examples WHERE project_id=%s
                            AND taxonomy_version_id=%s AND category_key=%s""",
                            (project_id,taxonomy_id,category_key))
            _insert_examples(conn,project_id,taxonomy_id,clean)
        conn.execute("""INSERT INTO sre_review_events
          (project_id,target_type,target_id,action,before_value,after_value,reviewed_by)
          VALUES (%s,'category',%s,%s,%s::jsonb,%s::jsonb,%s)""",
          (project_id,category_key,"approve" if status=="approved" else "reject",
           json.dumps({"name":old[0],"definition":old[1],"status":old[3]},ensure_ascii=False),
           json.dumps({"status":status},ensure_ascii=False),reviewer))

def approve_taxonomy(conn, project_id: str, taxonomy_id: int, reviewer: str) -> None:
    project_id=_project_id(project_id)
    if not reviewer or not reviewer.strip():
        raise ContractError("reviewer required")
    with conn.transaction():
        conn.execute("SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR UPDATE",
                     (project_id,)).fetchone() or _missing()
        conn.execute("""SELECT 1 FROM sre_taxonomy_versions WHERE id=%s
                    AND project_id=%s AND status='draft' FOR UPDATE""",
                    (taxonomy_id,project_id)).fetchone() or _missing()
        counts=conn.execute("""SELECT count(*) FILTER (WHERE review_status='approved'),
                             count(*) FILTER (WHERE review_status='proposed')
                   FROM sre_taxonomy_categories WHERE taxonomy_version_id=%s
                         AND project_id=%s""",(taxonomy_id,project_id)).fetchone()
        if counts[0] < 1 or counts[1] > 0:
            raise ContractError("All categories need human review, and one approved category is required")
        conn.execute("""UPDATE sre_taxonomy_versions SET status='approved',
                         approved_by=%s,approved_at=now()
                       WHERE id=%s AND project_id=%s""",(reviewer,taxonomy_id,project_id))
        conn.execute("""UPDATE sre_workspaces SET active_taxonomy_id=%s WHERE project_id=%s""",
                     (taxonomy_id,project_id))
        conn.execute("""INSERT INTO sre_review_events
                 (project_id,target_type,target_id,action,reviewed_by)
                 VALUES (%s,'taxonomy',%s,'publish',%s)""",
                 (project_id,str(taxonomy_id),reviewer))

def save_pipeline(conn, project_id: str, name: str, config: dict, created_by: str,
                  *, activate: bool = False) -> int:
    project_id=_project_id(project_id)
    cfg=validate_pipeline(config)
    if not name.strip() or not created_by.strip():
        raise ContractError("Pipeline name and creator required")
    import json
    with conn.transaction():
        conn.execute("SELECT 1 FROM sre_workspaces WHERE project_id=%s FOR UPDATE",
                     (project_id,)).fetchone() or _missing()
        version=conn.execute("""SELECT coalesce(max(version),0)+1 FROM sre_pipeline_configs
              WHERE project_id=%s AND name=%s""",(project_id,name)).fetchone()[0]
        pid=conn.execute("""INSERT INTO sre_pipeline_configs
                (project_id,name,version,config,created_by)
                VALUES (%s,%s,%s,%s::jsonb,%s) RETURNING id""",
                (project_id,name,version,json.dumps(cfg),created_by)).fetchone()[0]
        if activate:
            conn.execute("UPDATE sre_workspaces SET active_pipeline_id=%s WHERE project_id=%s",
                         (pid,project_id))
        return pid

def get_approved_context(conn, project_id: str) -> dict:
    """Read only the active approved version; always tenant scoped."""
    project_id=_project_id(project_id)
    row=conn.execute("""SELECT v.id,v.version FROM sre_workspaces w
               JOIN sre_taxonomy_versions v ON v.id=w.active_taxonomy_id AND v.project_id=w.project_id
               WHERE w.project_id=%s AND v.status='approved'""",(project_id,)).fetchone()
    if not row: _missing()
    categories=conn.execute("""SELECT category_key,name,relation_type,definition,rules,related_fact_ids
               ,origin,independent
               FROM sre_taxonomy_categories WHERE project_id=%s AND taxonomy_version_id=%s
               AND review_status='approved' ORDER BY id""",(project_id,row[0])).fetchall()
    return {"project_id":project_id,"taxonomy_version_id":row[0],"version":row[1],
            "categories":[{"category_key":str(c[0]),"name":c[1],"relation_type":c[2],
                  "definition":c[3],"rules":c[4],"related_fact_ids":c[5],
                  "origin":c[6],"independent":c[7],
                  "examples":_get_examples(conn,project_id,row[0],str(c[0]))}
                  for c in categories]}

def _get_examples(conn,project_id,taxonomy_id,category_key):
    rows=conn.execute("""SELECT polarity,example_kind,example_text,video_id
       FROM sre_category_examples WHERE project_id=%s AND taxonomy_version_id=%s
       AND category_key=%s ORDER BY id""",(project_id,taxonomy_id,category_key)).fetchall()
    examples={"positive":[],"negative":[]}
    for polarity,kind,description,video_id in rows:
        examples[polarity].append({"text":description,"example_kind":kind,"video_id":video_id})
    return examples
