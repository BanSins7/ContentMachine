"""Taxonomy Builder prompt/schema contracts with deterministic evidence verification.

Untrusted model output is *never* an approved user fact. Facts and topics
require two distinct human gates. The LLM may suggest independent adjacent
themes but cannot assert product capabilities without an approved source fact.
"""
from __future__ import annotations
import json,re
from uuid import uuid4
from .pipeline_contracts import PipelineError,sha256

FACT_FIELDS=("product_type","product_functions","audience_problems","product_description","audience","target_audience","niche")
FACT_KINDS=("function","problem","audience","product_type")
ROLES=("fact_extraction","taxonomy_generation","taxonomy_critic")
RELATIONS=("direct","problem_related","adjacent")
EXTRACTION_SCHEMA={
  "type":"object","additionalProperties":False,
  "properties":{"facts":{"type":"array","maxItems":40,"items":{
    "type":"object","additionalProperties":False,
    "properties":{"kind":{"type":"string","enum":list(FACT_KINDS)},
                  "text":{"type":"string"},"source_field":{"type":"string"},
                  "source_quote":{"type":"string"}},
    "required":["kind","text","source_field","source_quote"]}}},
  "required":["facts"]
}
TOPIC_ITEM={
 "type":"object","additionalProperties":False,
 "properties":{
  "name":{"type":"string"},"relation":{"type":"string","enum":list(RELATIONS)},
  "origin":{"type":"string","enum":["explicit","inferred"]},
  "fact_ids":{"type":"array","items":{"type":"string"}},
  "definition":{"type":"string"},"include":{"type":"string"},
  "exclude":{"type":"string"},
  "positive_examples":{"type":"array","items":{"type":"string"}},
  "negative_examples":{"type":"array","items":{"type":"string"}}},
 "required":["name","relation","origin","fact_ids","definition",
             "include","exclude","positive_examples","negative_examples"]
}
TAXONOMY_SCHEMA={
 "type":"object","additionalProperties":False,
 "properties":{"topics":{"type":"array","minItems":2,"maxItems":20,"items":TOPIC_ITEM}},
 "required":["topics"]
}
CRITIC_SCHEMA={
 "type":"object","additionalProperties":False,
 "properties":{"findings":{"type":"array","maxItems":30,"items":{
   "type":"object","additionalProperties":False,
   "properties":{"topic_name":{"type":"string"},"issue":{"type":"string"},
                 "suggestion":{"type":"string"},"severity":{"type":"string",
                 "enum":["low","medium","high"]}},
   "required":["topic_name","issue","suggestion","severity"]}}},
 "required":["findings"]
}
SCHEMAS={"fact_extraction":EXTRACTION_SCHEMA,"taxonomy_generation":TAXONOMY_SCHEMA,
         "taxonomy_critic":CRITIC_SCHEMA}
SYSTEM={
 "fact_extraction":("You extract evidence-grounded atomic claims from a Health & Fitness "
 "product questionnaire. Do not invent features, problems or audiences. Every fact must "
 "include the exact case-sensitive source_quote occurring in source_field. Return JSON only."),
 "taxonomy_generation":("Generate relevant, broad video research taxonomy for maximum "
 "potential views in Health & Fitness. Categories direct, problem_related, adjacent. "
 "Ground direct/problem_related in confirmed fact IDs. Adjacent may be independent ideas "
 "but MUST NOT claim product features not in confirmed facts. All examples are illustrative, "
 "NOT verified real videos. Be diverse; avoid duplicate topic names. Return JSON only."),
 "taxonomy_critic":("Review proposed research taxonomy. Report only issues and suggestions "
 "with severity; do not edit the topics or approve them. Return JSON only.")
}

def schema_for(role):
    if role not in SCHEMAS:raise PipelineError("Unknown Builder stage")
    return {"name":"tb_"+role,"strict":True,"schema":SCHEMAS[role]}

def prompt_for(role,content):
    if role not in SYSTEM:raise PipelineError("Unknown prompt role")
    return [{"role":"system","content":SYSTEM[role]},
            {"role":"user","content":json.dumps(content,ensure_ascii=False,
                                                sort_keys=True)}]

def _text(value,field,max_chars=1000):
    if not isinstance(value,str) or not value.strip() or len(value)>max_chars:
        raise PipelineError("Invalid "+field)
    return value.strip()

def _array(value,name,min_len=0,max_len=40):
    if not isinstance(value,list) or len(value)<min_len or len(value)>max_len:
        raise PipelineError("Invalid "+name)
    return value

def _keys(obj,expected):
    if not isinstance(obj,dict) or set(obj)!=set(expected):
        raise PipelineError("Unexpected or missing LLM JSON keys")

def validate_facts(response,profile):
    _keys(response,["facts"])
    if not isinstance(profile,dict):raise PipelineError("Profile missing")
    output=[];seen=set()
    for f in _array(response["facts"],"facts",1,40):
        _keys(f,("kind","text","source_field","source_quote"))
        kind=f["kind"];field=f["source_field"]
        if kind not in FACT_KINDS or field not in FACT_FIELDS:
            raise PipelineError("Unsupported fact kind or source field")
        text=_text(f["text"],"fact text",500)
        quote=_text(f["source_quote"],"source quote",1600)
        source=profile.get(field)
        if not isinstance(source,str) or quote not in source:
            raise PipelineError("LLM fact source quote does not occur verbatim in questionnaire")
        fingerprint=(kind,text.casefold())
        if fingerprint in seen:raise PipelineError("Duplicated extracted fact")
        seen.add(fingerprint)
        output.append({"id":str(uuid4()),"kind":kind,"text":text,"source_field":field,
                       "source_quote":quote,"origin":"extracted","review_status":"pending"})
    return output

def review_facts(facts,decisions):
    existing={f["id"]:f for f in facts}
    if not isinstance(decisions,list) or len(decisions)>80:
        raise PipelineError("Invalid fact review submissions")
    approved=[];seen=set()
    for item in decisions:
        if not isinstance(item,dict) or item.get("action") not in ("approve","edit","reject","add"):
            raise PipelineError("Invalid fact review action")
        action=item["action"]
        if action=="add":
            if set(item)!={"action","kind","text"} or item["kind"] not in FACT_KINDS:
                raise PipelineError("Invalid human-added fact")
            approved.append({"id":str(uuid4()),"kind":item["kind"],
                            "text":_text(item["text"],"human fact",500),
                            "origin":"human_added","source_field":None,"source_quote":None})
            continue
        if set(item)-{"action","id","text"} or item.get("id") not in existing:
            raise PipelineError("Unknown fact id for human review")
        fid=item["id"]
        if fid in seen:raise PipelineError("Fact reviewed twice")
        seen.add(fid)
        if action=="reject":continue
        source=existing[fid]
        changed=_text(item.get("text"),"edited fact",500) if action=="edit" else source["text"]
        approved.append({**source,"text":changed,"origin":
                         "human_edited" if action=="edit" else "source_verified",
                         "review_status":"approved"})
    if len(seen)!=len(existing):raise PipelineError("Every extracted fact must be reviewed")
    if not approved:raise PipelineError("Approve or add at least one grounded fact")
    return approved

def validate_topics(response,facts):
    _keys(response,["topics"])
    known={f["id"]:f for f in facts}
    topics=[];names=set()
    for topic in _array(response["topics"],"topics",2,20):
        _keys(topic,TOPIC_ITEM["required"])
        relation=topic["relation"];origin=topic["origin"]
        if relation not in RELATIONS or origin not in ("explicit","inferred"):
            raise PipelineError("Unknown taxonomy relation or origin")
        name=_text(topic["name"],"topic name",160)
        if name.casefold() in names:raise PipelineError("Duplicate topic name")
        names.add(name.casefold())
        ids=_array(topic["fact_ids"],"topic fact IDs",0,30)
        if not all(isinstance(x,str) and x in known for x in ids) or len(ids)!=len(set(ids)):
            raise PipelineError("Taxonomy cites nonexistent or duplicate approved fact IDs")
        if relation in ("direct","problem_related") and not ids:
            raise PipelineError("Direct and problem topics require approved fact links")
        if relation=="direct" and not any(known[x]["kind"] in ("function","product_type") for x in ids):
            raise PipelineError("Direct topic requires a function or product-type fact")
        if relation=="problem_related" and not any(known[x]["kind"]=="problem" for x in ids):
            raise PipelineError("Problem topic requires approved audience problem")
        def examples(key):
            values=_array(topic[key],key,1,8)
            return [_text(v,key,230) for v in values]
        topics.append({"id":str(uuid4()),"name":name,"relation":relation,
          "origin":origin,"fact_ids":ids,
          "independent":not ids,"definition":_text(topic["definition"],"definition",750),
          "include":_text(topic["include"],"include",600),
          "exclude":_text(topic["exclude"],"exclude",600),
          "positive_examples":examples("positive_examples"),
          "negative_examples":examples("negative_examples"),
          "example_kind":"illustrative","review_status":"pending"})
    return topics

def validate_critic(response,topics):
    _keys(response,["findings"])
    names={t["name"] for t in topics}
    findings=[]
    for item in _array(response["findings"],"findings",0,30):
        _keys(item,("topic_name","issue","suggestion","severity"))
        if item["topic_name"] not in names or item["severity"] not in ("low","medium","high"):
            raise PipelineError("Critic referenced nonexistent topic")
        findings.append({"topic_name":item["topic_name"],"severity":item["severity"],
            "issue":_text(item["issue"],"critic issue",700),
            "suggestion":_text(item["suggestion"],"critic suggestion",700)})
    return findings

def review_topics(proposals,choices,approved_facts):
    source={t["id"]:t for t in proposals}
    if not isinstance(choices,list) or not choices or len(choices)>35:
        raise PipelineError("Review at least two topics")
    used=set();result=[]
    for choice in choices:
        if not isinstance(choice,dict) or choice.get("action") not in ("approve","edit","reject"):
            raise PipelineError("Invalid topic review action")
        fid=choice.get("id")
        if fid not in source or fid in used:raise PipelineError("Unknown/duplicated topic review")
        used.add(fid)
        if choice["action"]=="reject":continue
        t=dict(source[fid])
        if choice["action"]=="edit":
            for key in ("name","definition","include","exclude"):
                if key in choice:t[key]=_text(choice[key],key,750)
            t["origin"]="user_added"
        t["review_status"]="approved"
        result.append(t)
    if len(used)!=len(source) or len(result)<2:
        raise PipelineError("All proposals must be reviewed; approve at least two")
    if len({t["name"].casefold() for t in result})!=len(result):
        raise PipelineError("Duplicate published topic")
    return result
