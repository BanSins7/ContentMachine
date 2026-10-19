"""LLM proposal contracts. The LLM may propose but never approve or mutate the DB.

The caller injects a generation function; no specific provider or pricing is assumed.
"""
from __future__ import annotations
from uuid import UUID
from .contracts import ContractError, validate_taxonomy, validate_profile

def validate_fact_proposals(profile: dict, facts: list[dict]) -> list[dict]:
    normalized = validate_profile(profile)
    if not isinstance(facts,list):
        raise ContractError("Facts must be a list")
    results=[]
    seen=set()
    for fact in facts:
        if not isinstance(fact,dict):
            raise ContractError("Fact must be an object")
        kind=fact.get("kind")
        if kind not in ("function","problem"):
            raise ContractError("Fact kind must be function/problem")
        quote=fact.get("original_quote")
        name=fact.get("normalized_text")
        source=normalized["functions_text" if kind=="function" else "problems_text"]
        if not isinstance(quote,str) or not quote or quote not in source:
            raise ContractError("Original quote must occur verbatim in user input")
        if not isinstance(name,str) or not name.strip():
            raise ContractError("Normalized fact must be nonempty")
        key=(kind,name.casefold().strip())
        if key in seen:
            raise ContractError("Duplicate normalized fact")
        seen.add(key)
        results.append({"kind":kind,"original_quote":quote,"normalized_text":name.strip(),
                        "origin":"explicit","review_status":"proposed"})
    return results

def validate_topic_proposals(categories: list[dict], approved_fact_ids: set[str]) -> list[dict]:
    """No inferred topic can silently point to an unknown/rejected fact."""
    if not isinstance(approved_fact_ids,set):
        raise ContractError("approved_fact_ids must be a set")
    rules=validate_taxonomy(categories)
    for item in rules:
        try: UUID(item["category_key"])
        except ValueError: raise ContractError("Category key must be UUID") from None
        if not set(item["related_fact_ids"]).issubset(approved_fact_ids):
            raise ContractError("Topic references an unapproved fact")
    return rules

def propose_facts(profile: dict, generator) -> list[dict]:
    """Generator(profile)->facts; caller chooses Strong LLM and handles its costs."""
    return validate_fact_proposals(profile,generator(validate_profile(profile)))

def propose_topics(approved_facts: list[dict], generator) -> list[dict]:
    """Generator(approved_facts)->categories; requires all facts human approved."""
    if not isinstance(approved_facts,list) or not approved_facts:
        raise ContractError("Approved facts are required")
    keys=set()
    for fact in approved_facts:
        if not isinstance(fact,dict) or fact.get("review_status")!="approved":
            raise ContractError("Topic generator requires reviewed facts")
        identifier=fact.get("id")
        if not isinstance(identifier,str):
            raise ContractError("Approved fact id required")
        keys.add(identifier)
    return validate_topic_proposals(generator(approved_facts), keys)
