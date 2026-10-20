"""Project-scoped fact review; distinct from taxonomy topic review."""
from __future__ import annotations
import json
from .contracts import ContractError
from .repository import _project_id, _missing
from .builder import validate_fact_proposals

def store_fact_proposals(conn, project_id: str, profile_version_id: int,
                         profile: dict, facts: list[dict]) -> list[str]:
    project_id=_project_id(project_id)
    validated=validate_fact_proposals(profile,facts)
    ids=[]
    with conn.transaction():
        conn.execute("""SELECT 1 FROM sre_profile_versions WHERE id=%s
                        AND project_id=%s FOR UPDATE""",
                        (profile_version_id,project_id)).fetchone() or _missing()
        existing=conn.execute("""SELECT 1 FROM sre_facts WHERE profile_version_id=%s
                AND project_id=%s LIMIT 1""",(profile_version_id,project_id)).fetchone()
        if existing:
            raise ContractError("Fact proposals already exist for this profile version")
        for row in validated:
            ident=conn.execute("""INSERT INTO sre_facts
                   (project_id,profile_version_id,kind,original_quote,normalized_text,origin)
                   VALUES (%s,%s,%s,%s,%s,'explicit') RETURNING id""",
                   (project_id,profile_version_id,row["kind"],row["original_quote"],
                    row["normalized_text"])).fetchone()[0]
            ids.append(str(ident))
    return ids

def review_fact(conn, project_id: str, fact_id: str, reviewer: str,
                status: str, corrected_text: str | None = None) -> None:
    project_id=_project_id(project_id)
    if not reviewer or not reviewer.strip():
        raise ContractError("reviewer required")
    if status not in ("approved","rejected"):
        raise ContractError("Fact review must approve or reject")
    with conn.transaction():
        row=conn.execute("""SELECT normalized_text,review_status FROM sre_facts
             WHERE id=%s AND project_id=%s FOR UPDATE""",(fact_id,project_id)).fetchone()
        if not row: _missing()
        if row[1]!="proposed":
            raise ContractError("Reviewed facts are immutable; create a new profile version")
        name=corrected_text.strip() if corrected_text is not None else row[0]
        if not name:
            raise ContractError("Fact text cannot be empty")
        conn.execute("""UPDATE sre_facts SET normalized_text=%s,review_status=%s
                        WHERE id=%s AND project_id=%s""",(name,status,fact_id,project_id))
        conn.execute("""INSERT INTO sre_review_events
          (project_id,target_type,target_id,action,before_value,after_value,reviewed_by)
          VALUES (%s,'fact',%s,%s,%s::jsonb,%s::jsonb,%s)""",
          (project_id,str(fact_id),"approve" if status=="approved" else "reject",
           json.dumps({"name":row[0],"status":row[1]},ensure_ascii=False),
           json.dumps({"name":name,"status":status},ensure_ascii=False),reviewer))

def get_approved_facts(conn, project_id: str, profile_version_id: int) -> list[dict]:
    project_id=_project_id(project_id)
    rows=conn.execute("""SELECT id,kind,original_quote,normalized_text,review_status
            FROM sre_facts WHERE project_id=%s AND profile_version_id=%s
            AND review_status='approved' ORDER BY kind,id""",
            (project_id,profile_version_id)).fetchall()
    return [{"id":str(r[0]),"kind":r[1],"original_quote":r[2],
             "normalized_text":r[3],"review_status":r[4]} for r in rows]

def add_manual_fact(conn,project_id: str,profile_version_id: int,
                    kind: str,text: str,reviewer: str) -> str:
    """Human-added facts keep their provenance; they are NOT attributed to the input text."""
    project_id=_project_id(project_id)
    if kind not in ("function","problem"):
        raise ContractError("Invalid manual fact kind")
    if not isinstance(text,str) or not text.strip() or not isinstance(reviewer,str) or not reviewer.strip():
        raise ContractError("Manual facts require text and reviewer")
    with conn.transaction():
        conn.execute("""SELECT 1 FROM sre_profile_versions WHERE project_id=%s
                        AND id=%s FOR UPDATE""",(project_id,profile_version_id)).fetchone() or _missing()
        fid=conn.execute("""INSERT INTO sre_facts
          (project_id,profile_version_id,kind,original_quote,normalized_text,origin,review_status)
          VALUES (%s,%s,%s,%s,%s,'user_added','approved') RETURNING id""",
          (project_id,profile_version_id,kind,text.strip(),text.strip())).fetchone()[0]
        conn.execute("""INSERT INTO sre_review_events
            (project_id,target_type,target_id,action,after_value,reviewed_by)
            VALUES (%s,'fact',%s,'add',%s::jsonb,%s)""",
            (project_id,str(fid),json.dumps({"text":text.strip(),"kind":kind,"origin":"user_added"},
                ensure_ascii=False),reviewer.strip()))
        return str(fid)
