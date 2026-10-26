"""Local-only configuration; never expose database credentials in HTTP responses."""
from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV_FILE = ROOT / ".env"

def local_env() -> dict[str, str]:
    """Read simple KEY=VALUE records without executing shell syntax."""
    if not ENV_FILE.is_file():
        return {}
    result = {}
    for line in ENV_FILE.read_text(encoding="utf-8-sig").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        if key in {"CM_DB_DSN", "CM_DB_ENABLED", "POSTGRES_PASSWORD", "CM_PG_PORT",
                    "CM_DTK_ENABLED", "CM_DTK_BASE_URL", "CM_DTK_API_KEY",
                    "CM_COLLECTOR_LIVE_ENABLED", "CM_DTK_ARCHIVE_DISABLED_CONFIRMED",
                    "CM_COLLECTOR_QV_CONSUMER_ENABLED", "CM_COLLECTOR_SRE_DELIVERY_ENABLED"}:
            result[key] = value.strip().strip('"').strip("'")
    return result

def setting(name: str, default: str = "") -> str:
    return os.environ.get(name, local_env().get(name, default))

def enabled() -> bool:
    return setting("CM_DB_ENABLED", "0").strip().lower() in ("1", "true", "yes", "on")

def dsn() -> str:
    return setting("CM_DB_DSN")

def connect_timeout() -> int:
    return 3
