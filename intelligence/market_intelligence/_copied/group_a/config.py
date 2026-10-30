"""Offline-safe Group A settings; no implicit activation from environment.

An enabled feature flag is merely a deployment REQUEST. It never substitutes
for installed adapters, installed database schema, verified media, or handoff.
"""
from __future__ import annotations

from dataclasses import dataclass
import os

TRUTHY = frozenset(("1", "true", "yes", "on"))
FALSY = frozenset(("0", "false", "no", "off"))


def flag_value(value, *, name: str) -> bool:
    if type(value) is not str:
        raise ValueError(name + " must be a boolean string")
    normalized = value.strip().lower()
    if normalized in TRUTHY:
        return True
    if normalized in FALSY:
        return False
    raise ValueError(name + " must be one of true/false/1/0/yes/no/on/off")


@dataclass(frozen=True, slots=True)
class GASettings:
    enabled: bool = False
    qdp_handoff_enabled: bool = False
    allow_psi_reuse: bool = False

    def __post_init__(self):
        if any(type(getattr(self, name)) is not bool for name in (
            "enabled", "qdp_handoff_enabled", "allow_psi_reuse"
        )):
            raise ValueError("Group A flags must be bool")


def settings_from_environment(environ=None) -> GASettings:
    env = os.environ if environ is None else environ
    return GASettings(
        enabled=flag_value(env.get("CM_GROUP_A_ENABLED", "0"), name="CM_GROUP_A_ENABLED"),
        qdp_handoff_enabled=flag_value(
            env.get("CM_GROUP_A_QDP_HANDOFF_ENABLED", "0"),
            name="CM_GROUP_A_QDP_HANDOFF_ENABLED"),
        allow_psi_reuse=flag_value(
            env.get("CM_GROUP_A_PSI_REUSE_ENABLED", "0"),
            name="CM_GROUP_A_PSI_REUSE_ENABLED"),
    )
