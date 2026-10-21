"""Contracts and independently computed quality gates for hierarchical taxonomy.

All decisions require human action. Model-declared completeness is not a proof:
coverage, structure, duplicates and novelty are checked against approved facts.
"""
from __future__ import annotations
import re
from collections import defaultdict
from .pipeline_contracts import PipelineError

KINDS=("direction","branch","hypothesis")
RELATIONS=("direct","problem_related","adjacent")
ID_PATTERN=re.compile(r"^[a-z][a-z0-9_-]{1,39}$")
STOPWORDS={"and","the","для","это","при","или","что","как","about","with","без",
           "лицо","лица","лица","тема","видео","роль","user","product"}
MAX_NODES=48

def _text(value,label,limit=700):
    if not isinstance(value,str) or not value.strip() or len(value)>limit:
        raise PipelineError("Invalid "+label,422)
    return value.strip()

def _ids(items,label,known=None,limit=48):
    if not isinstance(items,list) or len(items)>limit or len(items)!=len(set(str(x) for x in items)):
        raise PipelineError("Invalid "+label,422)
    for item in items:
        if not isinstance(item,str) or (known is None and not ID_PATTERN.fullmatch(item)) or (known is not None and item not in known):
            raise PipelineError("Unknown "+label+": "+str(item)[:40],422)
    return list(items)

def validate_tree(nodes,approved_facts,*,require_hypotheses=True):
    if not isinstance(nodes,list) or not 2<=len(nodes)<=MAX_NODES:
        raise PipelineError("Expected 2-48 hierarchical taxonomy nodes",422)
    facts={f["id"]:f for f in approved_facts}
    validated=[];seen=set();names=set()
    for raw in nodes:
        if not isinstance(raw,dict):raise PipelineError("Node is not an object",422)
        ident=_text(raw.get("id"),"node id",40)
        if not ID_PATTERN.fullmatch(ident) or ident in seen:
            raise PipelineError("Node IDs must be unique and stable",422)
        seen.add(ident)
        kind=raw.get("kind");relation=raw.get("relation")
        if kind not in KINDS or relation not in RELATIONS:
            raise PipelineError("Invalid node hierarchy type/relation",422)
        parent=raw.get("parent_id")
        if parent is not None and (not isinstance(parent,str) or not ID_PATTERN.fullmatch(parent)):
            raise PipelineError("Invalid parent ID",422)
        links=_ids(raw.get("source_fact_ids"),"source_fact_ids",facts,40)
        if relation in ("direct","problem_related") and not links:
            raise PipelineError("Direct and problem nodes require approved fact evidence",422)
        if relation=="direct" and not any(facts[x]["kind"] in ("function","product_type") for x in links):
            raise PipelineError("Direct node requires a product function/type fact",422)
        if relation=="problem_related" and not any(facts[x]["kind"]=="problem" for x in links):
            raise PipelineError("Problem-related node requires audience problem evidence",422)
        name=_text(raw.get("name"),"node title",160)
        normalized=name.casefold()
        if normalized in names:raise PipelineError("Duplicate node name",422)
        names.add(normalized)
        examples=raw.get("examples",[])
        if not isinstance(examples,list) or len(examples)>4 or any(
            not isinstance(x,str) or not x.strip() or len(x)>230 for x in examples):
            raise PipelineError("Invalid illustrative examples",422)
        validated.append({
            "id":ident,"parent_id":parent,"kind":kind,"relation":relation,
            "source_fact_ids":links,"name":name,
            "rationale":_text(raw.get("rationale"),"rationale",700),
            "definition":_text(raw.get("definition"),"definition",700),
            "include":_text(raw.get("include"),"include rules",600),
            "exclude":_text(raw.get("exclude"),"exclude rules",600),
            "examples":list(examples)
        })
    by_id={n["id"]:n for n in validated}
    roots=0
    for node in validated:
        parent=node["parent_id"]
        if parent is None:
            if node["kind"]!="direction":raise PipelineError("Only directions can be roots",422)
            roots+=1;continue
        ancestor=by_id.get(parent)
        if ancestor is None:raise PipelineError("Parent node does not exist",422)
        if KINDS.index(ancestor["kind"])>=KINDS.index(node["kind"]):
            raise PipelineError("Child must be deeper than parent",422)
        visited={node["id"]}
        while ancestor:
            if ancestor["id"] in visited:raise PipelineError("Cycle in taxonomy",422)
            visited.add(ancestor["id"])
            ancestor=by_id.get(ancestor["parent_id"])
    if roots<2:raise PipelineError("At least two distinct research directions required",422)
    if require_hypotheses and not any(n["kind"]=="hypothesis" for n in validated):
        raise PipelineError("At least one concrete research hypothesis required",422)
    return validated

def _terms(value):
    return {x for x in re.findall(r"[\w]+",value.casefold()) if len(x)>3 and x not in STOPWORDS}

def _pair_duplicates(nodes,proposed=None):
    pairs=[];seen=set()
    for i,left in enumerate(nodes):
        for right in nodes[i+1:]:
            a,b=_terms(left["name"]+" "+left["definition"]),_terms(right["name"]+" "+right["definition"])
            if a and b and len(a&b)/len(a|b)>=0.82:
                k=tuple(sorted((left["id"],right["id"])))
                seen.add(k);pairs.append({"a":k[0],"b":k[1],"reason":"Высокое совпадение названий и определений","source":"deterministic"})
    for pair in proposed or []:
        if not isinstance(pair,dict):raise PipelineError("Invalid model duplicate claim",422)
        a,b=pair.get("a"),pair.get("b")
        if not isinstance(a,str) or not isinstance(b,str):raise PipelineError("Invalid duplicate IDs",422)
        k=tuple(sorted((a,b)))
        if a==b or a not in {n["id"] for n in nodes} or b not in {n["id"] for n in nodes}:
            raise PipelineError("Model duplicate claim references an invalid node",422)
        if k not in seen:
            seen.add(k);pairs.append({"a":k[0],"b":k[1],"reason":_text(pair.get("reason"),"duplicate reason",350),"source":"opus_review"})
    return pairs

def quality_gate(nodes,facts,previous=None,model_duplicates=None):
    nodes=validate_tree(nodes,facts)
    matrix=[]
    by_fact=defaultdict(list)
    for node in nodes:
        for fid in node["source_fact_ids"]:by_fact[fid].append(node["id"])
    gaps=[]
    for f in facts:
        ids=by_fact.get(f["id"],[])
        covered=bool(ids)
        importance="severe" if f["kind"] in ("function","problem","product_type") else "warning"
        row={"fact_id":f["id"],"fact":f["text"],"kind":f["kind"],
             "covered":covered,"node_ids":ids,"gap_severity":None if covered else importance}
        matrix.append(row)
        if not covered:gaps.append(row)
    duplicates=_pair_duplicates(nodes,model_duplicates)
    previous=previous or []
    prior={n["name"].casefold() for n in previous if n["kind"]=="direction"}
    new=[n["id"] for n in nodes if n["kind"]=="direction" and n["name"].casefold() not in prior]
    substantive=len(new) if previous else 0
    severe=[g for g in gaps if g["gap_severity"]=="severe"]
    # A new direction in the second pass triggers a saturation *check* pass.
    # Finite bounded passes, no automatic paid repeat; zero new directions
    # is evidence of provisional saturation, never absolute completeness.
    return {"coverage_matrix":matrix,"gaps":gaps,"severe_gap_count":len(severe),
            "duplicates":duplicates,"duplicate_count":len(duplicates),
            "new_direction_ids":new,"new_direction_count":substantive,
            "covered_facts":len(matrix)-len(gaps),"total_facts":len(matrix),
            "passed":not severe and not duplicates and substantive==0,
            "saturation":"provisional" if not substantive else "new_directions_found",
            "method":"evidence mapping + Opus duplicate review + conservative token overlap; not proof of universal completeness"}

def review_nodes(proposals,decisions,facts):
    if not isinstance(decisions,list) or len(decisions)>90:
        raise PipelineError("Invalid human review payload",422)
    source={n["id"]:dict(n) for n in proposals}
    decided=set();merge_map={};kept={};added=[]
    for d in decisions:
        if not isinstance(d,dict) or d.get("action") not in ("approve","reject","edit","merge","add"):
            raise PipelineError("Invalid human decision",422)
        action=d["action"]
        if action=="add":
            node=d.get("node")
            if not isinstance(node,dict) or node.get("id") in source:
                raise PipelineError("Human-added node needs a fresh stable ID",422)
            added.append(node);continue
        ident=d.get("id")
        if ident not in source or ident in decided:
            raise PipelineError("Every proposal must be decided exactly once",422)
        decided.add(ident)
        if action=="merge":
            target=d.get("target_id")
            if target not in source or target==ident:
                raise PipelineError("Invalid merge target",422)
            merge_map[ident]=target
        elif action=="approve":kept[ident]=source[ident]
        elif action=="edit":
            replacement=d.get("node")
            if not isinstance(replacement,dict) or replacement.get("id")!=ident:
                raise PipelineError("Edited node must preserve its ID",422)
            kept[ident]=replacement
    if decided!=set(source):
        raise PipelineError("Review every proposed node before continuing",422)
    if added and len(added)>20:raise PipelineError("Too many manual additions",422)
    # Preserve descendant meaning when a direction is merged: map descendants
    # to the retained destination. Never silently keep a rejected orphan.
    for ident,target in merge_map.items():
        seen={ident}
        while target in merge_map:
            if target in seen:raise PipelineError("Circular merge",422)
            seen.add(target);target=merge_map[target]
        if target not in kept:raise PipelineError("Merge target must be approved",422)
        merge_map[ident]=target
    full=list(kept.values())+added
    kept_ids={n["id"] for n in full}
    for node in full:
        parent=node.get("parent_id")
        if parent in merge_map:node["parent_id"]=merge_map[parent]
        elif parent and parent not in kept_ids:
            raise PipelineError("A child of a rejected node needs explicit reparenting",422)
    result=validate_tree(full,facts)
    return result

# Transport shape is deliberately flat JSON for reliable structured output.
# The tree exists via parent_id edges, not nested unconstrained JSON arrays.
NODE_PROPERTIES={
 "id":{"type":"string"},"parent_id":{"type":["string","null"]},
 "kind":{"type":"string","enum":list(KINDS)},
 "relation":{"type":"string","enum":list(RELATIONS)},
 "source_fact_ids":{"type":"array","items":{"type":"string"}},
 "name":{"type":"string"},"rationale":{"type":"string"},
 "definition":{"type":"string"},"include":{"type":"string"},
 "exclude":{"type":"string"},"examples":{"type":"array","items":{"type":"string"}}
}
def schema_for_deep(role):
    if role not in ("deep_first","deep_second","deep_expand"):
        raise PipelineError("Unsupported deep stage",422)
    props={"nodes":{"type":"array","items":{
      "type":"object","properties":NODE_PROPERTIES,"required":list(NODE_PROPERTIES),
      "additionalProperties":False}}}
    if role!="deep_first":
        props["duplicates"]={"type":"array","items":{"type":"object",
          "properties":{"a":{"type":"string"},"b":{"type":"string"},"reason":{"type":"string"}},
          "required":["a","b","reason"],"additionalProperties":False}}
    return {"name":"research_taxonomy_deep_"+role,
            "strict":True,"schema":{"type":"object","properties":props,
             "required":list(props),"additionalProperties":False}}

def prompt_for_deep(role,inputs):
    import json
    base=(
      "You build research taxonomy for short video content, not medical advice. "
      "Return only strict JSON matching schema. Use all and ONLY human-approved facts; "
      "never invent product features. Every node needs a stable short ASCII id, "
      "parent_id null for root directions, kind direction/branch/hypothesis and "
      "relation direct/problem_related/adjacent. Each linked source_fact_ids MUST "
      "be an actual approved fact id; direct requires function/product_type evidence, "
      "problem_related requires problem evidence. Adjacent may be independent. "
      "Use clear nonoverlapping scope, inclusion and exclusion rules and illustrative "
      "video examples, 2+ top level directions, concrete hypotheses at leaves. "
      "Create branches only when conceptually independent. Reasoning concise; "
      "no guarantees of medical effect. Keep output within ~18-28 nodes."
    )
    if role=="deep_first":
        task="FIRST PASS: map main research directions, initial branches and testable hypotheses. No claim of exhaustive coverage."
    else:
        task=(
          "SECOND/EXPANSION PASS: preserve human-approved direction IDs/names/intent "
          "and respect rejected/merged directions. Return COMPLETE revised tree, not only new nodes. "
          "Fill uncovered approved facts, deepen permitted branches, explicitly search "
          "for important missing directions, correct overly similar hypotheses/levels. "
          "Include any serious semantic duplicates in duplicates array; never claim saturation "
          "without comparison to prior tree. Do not invent product functions."
        )
    return [{"role":"system","content":base+" "+task},
            {"role":"user","content":json.dumps(inputs,ensure_ascii=False,separators=(",",":"))}]
