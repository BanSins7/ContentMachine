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
  "positive_examples":{"type":"array","minItems":1,"maxItems":8,"items":{"type":"string"}},
  "negative_examples":{"type":"array","minItems":1,"maxItems":8,"items":{"type":"string"}}},
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

SYSTEM_V2={
 "taxonomy_generation":(
   "You design a rigorous, useful Health & Fitness video research taxonomy to maximize views. "
   "Generate 10-14 DISTINCT, specific directions spanning direct, problem_related and adjacent. "
   "Return ONLY the exact JSON shape requested by the schema. "
   "Each item MUST contain non-empty strings: name, definition, include, exclude; "
   "positive_examples MUST contain 1-3 concrete illustrative video ideas; "
   "negative_examples MUST contain 1-3 concrete counterexamples. "
   "All examples are HYPOTHETICAL, never verified existing videos. "
   "Critical grounding rule: fact_ids MUST be an array of EXACT full UUID strings copied "
   "character-for-character from approved_fact_ids_by_kind. Do not invent, alter, abbreviate, "
   "renumber or paraphrase UUIDs. "
   "Every direct category MUST cite >=1 approved 'function' or 'product_type' UUID. "
   "Every problem_related category MUST cite >=1 approved 'problem' UUID. "
   "Adjacent topics MAY have an empty fact_ids array and must NOT allege new product abilities. "
   "Do NOT use fact text or numerical indexes as fact_ids. "
   "Use relation exactly one of direct/problem_related/adjacent; origin exactly explicit/inferred. "
   "Self-check each topic's fact_ids and both nonempty example arrays before responding."),
 "taxonomy_critic":(
   "Review the proposed research taxonomy for unsupported product features, duplicate topics, "
   "weak evidence and category boundary overlaps. "
   "Report JSON with findings only; each finding must use EXACT topic_name copied from "
   "provided topics, severity one of low/medium/high, and nonempty issue and suggestion. "
   "Do NOT invent topic names, edit topics, or approve anything. If there are no valid issues, "
   "return an empty findings array.")
}
def prompt_for(role,content,version=None):
    if role not in SYSTEM:raise PipelineError("Unknown prompt role")
    if version is None:
        version={"fact_extraction":"facts-v1",
                 "taxonomy_generation":"taxonomy-v1",
                 "taxonomy_critic":"critic-v1"}[role]
    allowed={"fact_extraction":("facts-v1",),
             "taxonomy_generation":("taxonomy-v1","taxonomy-v2"),
             "taxonomy_critic":("critic-v1","critic-v2")}
    if version not in allowed[role]:raise PipelineError("Unregistered prompt version")
    material=content
    if role=="taxonomy_generation" and version=="taxonomy-v2":
        if not isinstance(content,dict) or not isinstance(content.get("approved_facts"),list):
            raise PipelineError("Approved fact list required for grounded taxonomy-v2")
        ids={"function":[],"problem":[],"audience":[],"product_type":[]}
        for fact in content["approved_facts"]:
            if (not isinstance(fact,dict) or fact.get("kind") not in ids
                or not isinstance(fact.get("id"),str)):
                raise PipelineError("Malformed approved fact list")
            ids[fact["kind"]].append(fact["id"])
        material={**content,"approved_fact_ids_by_kind":ids}
    system=SYSTEM_V2[role] if version.endswith("-v2") else SYSTEM[role]
    return [{"role":"system","content":system},
            {"role":"user","content":json.dumps(material,ensure_ascii=False,
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

def _required_keys(obj,expected):
    # Compatible with provider-supplied EXTRANEOUS commentary fields, which
    # are always discarded. All required structured fields are still fully
    # validated for evidence, type and safety before publication.
    if not isinstance(obj,dict) or not set(expected).issubset(obj):
        names=(sorted(str(k)[:32] for k in obj if isinstance(k,str)
             and k.isidentifier())[:8] if isinstance(obj,dict) else [])
        raise PipelineError("Missing required LLM JSON keys; observed fields: "+
                            ",".join(names))

def _topics_payload(response):
    if not isinstance(response,dict):
        raise PipelineError("Taxonomy response must be a JSON object")
    sources=[]
    for key in ("topics","categories"):
        if isinstance(response.get(key),list):
            sources.append(response[key])
    nested=response.get("taxonomy")
    if isinstance(nested,dict) and isinstance(nested.get("topics"),list):
        sources.append(nested["topics"])
    if len(sources)!=1:
        raise PipelineError("Expected one topics array (topics or categories), not ambiguous data")
    return sources[0]

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
        if not isinstance(source,str):
            raise PipelineError("Fact source field is not in the approved questionnaire")
        if quote not in source:
            # Accept ONLY trivial whitespace/case variations when a contiguous
            # substring can be found in the ORIGINAL field. Store that exact
            # original span, never an LLM paraphrase or inferred quote.
            pattern=r"\s+".join(re.escape(part) for part in re.split(r"\s+",quote))
            matched=re.search(pattern,source,flags=re.IGNORECASE) if pattern else None
            if matched is None:
                raise PipelineError("LLM fact source quote does not occur in questionnaire")
            quote=source[matched.start():matched.end()]
        if quote not in source:
            raise PipelineError("Cannot restore exact source evidence")
        fingerprint=(kind,text.casefold())
        if fingerprint in seen:raise PipelineError("Duplicated extracted fact")
        seen.add(fingerprint)
        output.append({"id":str(uuid4()),"kind":kind,"text":text,"source_field":field,
                       "source_quote":quote,"origin":"extracted","review_status":"pending"})
    return output

def reviewable_fact_batch(response,profile):
    """Keep each *verified* source-backed fact reviewable; quarantine others.

    An inaccurate quote must NOT be converted into verified evidence. Human
    reviewers can add an independently confirmed fact explicitly, but a
    quarantined suggestion cannot be approved with a checkbox.
    """
    _keys(response,["facts"])
    raw=_array(response["facts"],"facts",1,40)
    facts=[];excluded=[];seen=set()
    for candidate in raw:
        try:
            verified=validate_facts({"facts":[candidate]},profile)[0]
            fingerprint=(verified["kind"],verified["text"].casefold())
            if fingerprint in seen:
                raise PipelineError("Duplicate model-proposed fact")
            seen.add(fingerprint)
            facts.append(verified)
        except PipelineError as exc:
            item=candidate if isinstance(candidate,dict) else {}
            excluded.append({
                "kind":str(item.get("kind",""))[:40],
                "text":str(item.get("text",""))[:500],
                "source_field":str(item.get("source_field",""))[:80],
                "source_quote":str(item.get("source_quote",""))[:400],
                "reason":str(exc)[:240],
                "review_status":"not_source_verified"
            })
    if not facts:
        raise PipelineError("No model facts have verifiable questionnaire evidence")
    return {"facts":facts,"excluded_facts":excluded}

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

def validate_topics(response,facts,*,minimum=2):
    raw_topics=_topics_payload(response)
    if type(minimum) is not int or minimum not in (1,2):
        raise PipelineError("Invalid internal topic minimum")
    known={f["id"]:f for f in facts}
    topics=[];names=set()
    for topic in _array(raw_topics,"topics",minimum,20):
        _required_keys(topic,TOPIC_ITEM["required"])
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

def reviewable_topic_batch(response,facts):
    """Preserve only independently valid topics; quarantine other LLM drafts.

    Repairs are format-only and use the exact strings supplied by the model.
    Never infer fact IDs or invent missing examples, definitions or rules.
    """
    rows=_array(_topics_payload(response),"topics",1,20)
    valid=[];excluded=[];names=set()
    for raw in rows:
        try:
            if not isinstance(raw,dict):raise PipelineError("Topic must be an object")
            item=dict(raw);notes=[]
            for key in ("positive_examples","negative_examples"):
                val=item.get(key)
                if isinstance(val,str) and val.strip():
                    item[key]=[val];notes.append(key+" wrapped from supplied text")
                elif isinstance(val,list):
                    texts=[]
                    for value in val:
                        if isinstance(value,str) and value.strip():texts.append(value)
                        elif (isinstance(value,dict) and set(value)=={"text"} and
                              isinstance(value["text"],str) and value["text"].strip()):
                            texts.append(value["text"]);notes.append(key+" extracted supplied text")
                        else:raise PipelineError("Invalid "+key)
                    if len(texts)>8:
                        texts=texts[:8];notes.append(key+" truncated to 8 supplied examples")
                    item[key]=texts
            for key in ("include","exclude"):
                val=item.get(key)
                if (isinstance(val,list) and 1<=len(val)<=8 and
                    all(isinstance(v,str) and v.strip() for v in val)):
                    item[key]="; ".join(v.strip() for v in val)
                    notes.append(key+" joined from supplied strings")
            normalized=validate_topics({"topics":[item]},facts,minimum=1)[0]
            identity=normalized["name"].casefold()
            if identity in names:raise PipelineError("Duplicate topic name")
            names.add(identity)
            normalized["normalization_notes"]=notes
            valid.append(normalized)
        except PipelineError as error:
            raw=raw if isinstance(raw,dict) else {}
            excluded.append({"name":str(raw.get("name",""))[:160],
                             "relation":str(raw.get("relation",""))[:40],
                             "reason":str(error)[:240],
                             "review_status":"invalid_model_draft"})
    if len(valid)<2:
        from collections import Counter
        errors=Counter(x["reason"] for x in excluded)
        summary="; ".join(str(n)+"× "+name for name,n in errors.most_common(4))
        raise PipelineError("Fewer than two valid grounded taxonomy topics. "+
            str(len(valid))+" valid; "+str(len(excluded))+" rejected. Reasons: "+summary[:340])
    return {"topics":valid,"excluded_topics":excluded}

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
        if not isinstance(choice,dict) or choice.get("action") not in ("approve","edit","reject","add"):
            raise PipelineError("Invalid topic review action")
        if choice["action"]=="add":
            fields={"action","name","definition","include","exclude",
                    "positive_examples","negative_examples"}
            if set(choice)!=fields:
                raise PipelineError("Human-added adjacent topic requires full rules and examples")
            payload={key:choice[key] for key in fields if key!="action"}
            payload.update({"relation":"adjacent","origin":"inferred","fact_ids":[]})
            topic=validate_topics({"topics":[payload]},approved_facts,minimum=1)[0]
            topic["origin"]="user_added"
            topic["review_status"]="approved"
            result.append(topic)
            continue
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
    if len(used)!=len(source) or not (2<=len(result)<=20):
        raise PipelineError("Review every proposal; publish between two and 20 approved topics")
    if len({t["name"].casefold() for t in result})!=len(result):
        raise PipelineError("Duplicate published topic")
    return result
