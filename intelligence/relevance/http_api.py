"""M04 minimal, GET-only, default-off localhost bootstrap adapter for SRE.

No endpoint in M04 starts an offline worker, consults live D1 content,
opens PostgreSQL or sends model/provider requests. Future authenticated
project-scoped SRE APIs will live behind this separate routing boundary.
This adapter relies on the host app's same-origin/host check performed
BEFORE it is called; it does not replace project authentication.
"""
from __future__ import annotations

from .config import load_settings
from .health import diagnose
from .module import get_module_manifest

_PREFIX = "/api/sre/v1/"
_EXACT = frozenset((f"{_PREFIX}manifest", f"{_PREFIX}health"))


def handle_get(handler, parsed_url):
    """Return whether a path is claimed, avoiding interception of other apps.

    GET manifest is discoverable even when disabled, but it is inert;
    health returns 503 until an authenticated project-specific read-only
    preflight becomes available in the later M38/39 routes. No credentials.
    """
    path = parsed_url.path
    if not path.startswith(_PREFIX):
        return False
    if path not in _EXACT:
        handler.json_response(404, {"error": "SRE route unavailable"})
        return True
    if parsed_url.query:
        handler.json_response(400, {"error": "Unexpected query parameters"})
        return True
    try:
        settings = load_settings()
        if path.endswith("/manifest"):
            manifest = get_module_manifest(dict(settings.flags))
            handler.json_response(200, {"ok": True, **manifest})
        else:
            health = diagnose(settings)
            code = 200 if health["status"] == "available" else 503
            handler.json_response(code, {"ok": code == 200, **health})
    except Exception:
        # No leaked invalid env values, secrets, DSN or exception details.
        handler.json_response(503, {"ok": False, "error": "SRE configuration unavailable"})
    return True


def handle_post(handler, route):
    """Future mutation endpoints need actor authorization and CSRF protection.

    Host app must apply its existing same-origin and X-ContentMachine checks
    before delegating. This stage refuses ALL POST /api/sre/v1/* requests.
    """
    if not route.startswith(_PREFIX):
        return False
    handler.json_response(405, {"error": "SRE mutation route not available"})
    return True


__all__ = ["handle_get", "handle_post"]
