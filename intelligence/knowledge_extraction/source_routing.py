"""B1 Stage03: conservative source ownership and subject routing.

No source material may assign its own role or subject. Unmapped research
documents and unknown kinds are explicitly OTHER, never product capabilities.
"""
from __future__ import annotations
from dataclasses import dataclass
import re
from uuid import UUID

from .contracts import ContractError, SOURCE_ROLES, default_source_role

CONFIRMED_BY = "local_owner"
PROFILE_PRODUCT = frozenset(("name","product_description","product_functions","product_type","product_url"))
PROFILE_AUDIENCE = frozenset(("audience_problems",))


@dataclass(frozen=True)
class Routing:
    role: str
    scope: str
    subject_id: str | None


def _subject(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value,str) or not (1 <= len(value) <= 128) or re.search(r"[^a-zA-Z0-9:._-]",value):
        raise ContractError("invalid_subject_id")
    return value


def normalize_routing(config: dict | None, source_ids: set[str]) -> dict:
    """Only explicit owner-confirmed exceptions. Config itself is immutable in B1."""
    if config is None:
        return {}
    if not isinstance(config,dict) or set(config) != {"source_routes"}:
        raise ContractError("invalid_routing_config")
    rows=config["source_routes"]
    if not isinstance(rows,dict) or len(rows)>180:
        raise ContractError("invalid_routing_config")
    result={}
    for sid,rule in sorted(rows.items()):
        try:
            sid=str(UUID(sid))
        except (ValueError,TypeError):
            raise ContractError("invalid_routing_source_id")
        if sid not in source_ids or sid in result:
            raise ContractError("foreign_routing_source")
        if (not isinstance(rule,dict) or set(rule)!={"role","subject_id","confirmed_by"}
            or not isinstance(rule["role"],str) or rule["role"] not in SOURCE_ROLES or rule["confirmed_by"]!=CONFIRMED_BY):
            raise ContractError("unconfirmed_routing")
        result[sid]={"role":rule["role"],"subject_id":_subject(rule["subject_id"]),
                     "confirmed_by":CONFIRMED_BY}
    return result


def route_source(source: dict, project_id: str, routes: dict) -> Routing:
    sid=source["source_id"]
    if sid in routes:
        role=routes[sid]["role"]
        subject_id=routes[sid]["subject_id"]
    else:
        role=default_source_role(source["source_type"])
        subject_id=None
    if role=="own_product":
        subject_id=subject_id or project_id
    elif role=="competitor":
        # Stable identity per source until owner groups actual competitor entities.
        subject_id=subject_id or sid
    return Routing(role,role,subject_id)


def route_profile_field(path: str, project_id: str) -> Routing:
    base=path.split("[",1)[0].split(".",1)[0]
    if base in PROFILE_PRODUCT:
        return Routing("own_product","own_product",project_id)
    if base in PROFILE_AUDIENCE:
        return Routing("audience_research","audience_research",None)
    return Routing("other","other",None)
