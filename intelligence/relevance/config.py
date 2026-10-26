"""M03 SRE-only runtime settings; zero I/O at import and no paid start.

Call `load_settings` explicitly with an injectable mapping; it never reads
the root project .env or initializes a provider, worker or PostgreSQL connection.
CM_SRE_* knobs are separate from host configuration, all default deny.

Secret data (DB DSN / OpenRouter key) never appears in repr, public manifests,
health reports or raised errors. M22-M27 will add actual live connectors.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from collections.abc import Mapping
import os
import re

from .module import EXACT_LIVE_MODEL, validate_feature_flags
from .v1_contracts import SREContractError, reject

_BOOLEANS = {
    "CM_SRE_ENABLED": "enabled",
    "CM_SRE_OFFLINE_WORKER": "offline_worker",
    "CM_SRE_LIVE_CLASSIFICATION": "live_classification",
    "CM_SRE_COLLECTOR_EVENT_INGEST": "collector_event_ingest",
    "CM_SRE_QDP_EVENT_DELIVERY": "qdp_event_delivery",
}
_INTS = {
    "CM_SRE_WORKER_LEASE_SECONDS": ("worker_lease_seconds", 60, 5, 3600),
    "CM_SRE_MAX_BATCH_ITEMS": ("max_batch_items", 100, 1, 1000),
    "CM_SRE_MAX_ATTEMPTS": ("max_attempts", 3, 1, 10),
}
_LIVE_MODEL_KEY = "CM_SRE_MODEL_ID"
_LOCAL_MODEL_KEY = "CM_SRE_LOCAL_EMBEDDING_MODEL"
_DB_ENABLED = "CM_DB_ENABLED"
_DB_DSN = "CM_DB_DSN"
_PROVIDER_KEY = "CM_SRE_OPENROUTER_API_KEY"
_SECRETS = frozenset((_DB_DSN, _PROVIDER_KEY))
_KNOWN = frozenset((*_BOOLEANS, *_INTS, _LIVE_MODEL_KEY, _LOCAL_MODEL_KEY,
                    _DB_ENABLED, *_SECRETS))
_LOCAL_MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]{0,159}\Z")


def _flag(value, label):
    if type(value) is not str:
        reject(label, "expected explicit string boolean")
    if value in ("1", "true"):
        return True
    if value in ("0", "false"):
        return False
    reject(label, "expected 1/0 or true/false (lowercase)")


def _bound_int(value, name, low, high):
    if type(value) is not str or not re.fullmatch(r"(?:0|[1-9][0-9]{0,5})", value):
        reject(name, "expected bounded decimal integer")
    parsed = int(value)
    if not low <= parsed <= high:
        reject(name, "value outside allowed range")
    return parsed


def _optional_secret(value, label):
    if type(value) is not str:
        reject(label, "secret must be a string")
    if len(value) > 8192:
        reject(label, "secret exceeds safe maximum")
    # String is deliberately not inspected further and NEVER logged.
    return value


@dataclass(frozen=True, slots=True)
class SRERuntimeSettings:
    flags: dict[str, bool]
    worker_lease_seconds: int
    max_batch_items: int
    max_attempts: int
    model_id: str
    local_embedding_model: str | None
    database_enabled: bool
    database_configured: bool
    provider_key_configured: bool
    _database_dsn: str = field(default="", repr=False, compare=False)
    _provider_key: str = field(default="", repr=False, compare=False)

    def public_dict(self):
        """No credentials (even masked lengths), no DSN, no provider calls."""
        return {
            "features": dict(self.flags),
            "worker_lease_seconds": self.worker_lease_seconds,
            "max_batch_items": self.max_batch_items,
            "max_attempts": self.max_attempts,
            "model_id": self.model_id,
            "local_embedding_model": self.local_embedding_model,
            "database_enabled": self.database_enabled,
            "database_configured": self.database_configured,
            "provider_key_configured": self.provider_key_configured,
        }


def load_settings(source=None):
    """Parse explicit config only; `source=None` reads env when invoked.

    Unknown CM_SRE_* env knobs fail closed to catch incorrect deployment flags.
    Provided mappings are cloned/read once; no .env, database, subprocess, DNS.
    An API key is NOT consent, and absence never triggers a fallback provider.
    """
    if source is None:
        source = os.environ
    if not isinstance(source, Mapping):
        reject("config", "expected key/string mapping")
    for name in source:
        if type(name) is not str:
            reject("config", "configuration keys must be strings")
        if name.startswith("CM_SRE_") and name not in _KNOWN:
            reject("config", "unsupported CM_SRE_ configuration key")
    flags = {}
    for env_key, key in _BOOLEANS.items():
        if env_key in source:
            flags[key] = _flag(source[env_key], env_key)
    flags = validate_feature_flags(flags)
    numerics = {}
    for env_key, (name, default, low, high) in _INTS.items():
        numerics[name] = (_bound_int(source[env_key], env_key, low, high)
                          if env_key in source else default)
    model = source.get(_LIVE_MODEL_KEY, EXACT_LIVE_MODEL)
    if type(model) is not str or model != EXACT_LIVE_MODEL:
        reject(_LIVE_MODEL_KEY, "unsupported live model; exact Opus required",
               "unsupported_model")
    embedding = source.get(_LOCAL_MODEL_KEY, "")
    if type(embedding) is not str or (embedding and
        (_LOCAL_MODEL_PATTERN.fullmatch(embedding) is None or ".." in embedding or
         embedding.startswith("/"))):
        reject(_LOCAL_MODEL_KEY, "invalid local model identifier")
    db_enabled = _flag(source[_DB_ENABLED], _DB_ENABLED) if _DB_ENABLED in source else False
    db_dsn = _optional_secret(source.get(_DB_DSN, ""), _DB_DSN)
    provider_key = _optional_secret(source.get(_PROVIDER_KEY, ""), _PROVIDER_KEY)
    return SRERuntimeSettings(
        flags=flags, model_id=model, local_embedding_model=embedding or None,
        database_enabled=db_enabled,
        database_configured=bool(db_dsn.strip()),
        provider_key_configured=bool(provider_key.strip()),
        _database_dsn=db_dsn, _provider_key=provider_key, **numerics,
    )


def connect_for_probe(settings):
    """Explicit, opt-in DB connection factory used only by diagnostic callers.

    Never auto-connect on import or health GET. Caller owns connection lifetime.
    Do not expose the underlying DSN to clients, exceptions or logs.
    """
    if not settings.database_enabled or not settings.database_configured:
        reject("config", "host PostgreSQL is not configured", "blocked_config")
    try:
        import psycopg
    except ImportError:
        reject("dependencies", "PostgreSQL driver is unavailable", "blocked_dependency")
    try:
        return psycopg.connect(settings._database_dsn, connect_timeout=3)
    except Exception:
        # Intentionally suppress exception chaining, which might contain
        # host names/user names/DSN credentials.
        raise SREContractError("blocked_config", "database", "unavailable PostgreSQL") from None


def optional_dependency(name):
    """Report installed optional runtime packages without importing them."""
    from importlib.util import find_spec
    if name not in ("psycopg", "sentence_transformers", "openai"):
        reject("dependency", "unsupported dependency probe")
    try:
        return find_spec(name) is not None
    except (ImportError, ValueError, AttributeError):
        return False


__all__ = ["SRERuntimeSettings", "load_settings", "connect_for_probe",
           "optional_dependency"]
