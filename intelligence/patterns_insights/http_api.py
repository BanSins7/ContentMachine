"""PIE-MOD-34 local read-only HTTP bridge; authenticated runtime is opt-in.

app.py checks same-origin host first. This module additionally requires a
REAL verified actor and current UAE+project+source rights. A local browser is
not proof of project membership, so the default runtime is NOT CONFIGURED.
"""
from __future__ import annotations

from urllib.parse import parse_qs
from .contracts import PIEBoundaryError
from .explorer import ExplorerPorts,explore_for_request
import re

PATH=re.compile(r"^/api/projects/([0-9a-f]{32})/pie/(patterns|datasets|candidates|validations|results|insights|reviews|overview)$")
PREFIX=re.compile(r"^/api/projects/[^/]+/pie(?:/.*)?$")
_ALLOWED=frozenset(("status","platform","b_feature_name","hypothesis_sha256",
                   "min_matched_pairs","min_supported_bins","page_size","cursor"))
_PORTS:ExplorerPorts|None=None


def install_pie_explorer_runtime(ports:ExplorerPorts|None)->None:
    """Startup-only integration port; no session IDs from query/headers.

    Host must protect this setup from any remote/untrusted access.
    """
    global _PORTS
    if ports is not None and type(ports) is not ExplorerPorts:
        raise PIEBoundaryError("pie_ports_unavailable","Expected trusted PIE runtime")
    _PORTS=ports


def handle_get(handler,url,*,ports:ExplorerPorts|None=None)->bool:
    if not PREFIX.fullmatch(url.path):return False
    route=PATH.fullmatch(url.path)
    if route is None:
        handler.json_response(404,{"ok":False,"error":{"code":"not_found","message":"Маршрут PIE недоступен."}})
        return True
    project,view=route.groups()
    # No-store and local same-origin guarantees come from app.py respond().
    try:
        parsed=parse_qs(url.query,keep_blank_values=True,strict_parsing=True,
                        max_num_fields=12)
        if (set(parsed)-_ALLOWED
            or any(len(v)!=1 for v in parsed.values())
            or any(not isinstance(v[0],str) for v in parsed.values())):
            raise PIEBoundaryError("pie_invalid_query","Неизвестные или повторные параметры.")
        runtime=ports if ports is not None else _PORTS
        if type(runtime) is not ExplorerPorts:
            raise PIEBoundaryError("pie_ports_unavailable",
                                   "Подключение прав и данных PIE пока не настроено.")
        response=explore_for_request(handler,project,view,
                                     {k:v[0] for k,v in parsed.items()},runtime)
        handler.json_response(200,response)
    except PIEBoundaryError as exc:
        code=exc.code
        if code in ("pie_access_denied","registry_project_access_denied"):
            status=403
            message="Нет действующих прав доступа к исследованию."
        elif (code.startswith("invalid_") or code.startswith("pie_invalid_")
              or code.startswith("registry_cursor_")):
            status=400
            message="Недопустимые фильтры или устаревший курсор."
        elif code in ("insight_evidence_not_visible","pie_not_found"):
            status=404
            message="Данные не найдены или недоступны."
        else:
            status=503
            message="PIE пока не готов: текущие права или исходные данные не подтверждены."
        handler.json_response(status,{"ok":False,"error":{"code":code,"message":message},
                                      "state":"not_ready","publication_allowed":False})
    except (ValueError,UnicodeError) as exc:
        handler.json_response(400,{"ok":False,"error":{
            "code":"pie_invalid_query","message":"Недопустимый запрос."}})
    except Exception:
        handler.json_response(503,{"ok":False,"error":{
            "code":"pie_data_unavailable",
            "message":"Не удалось независимо подтвердить данные PIE."},
            "state":"not_ready","publication_allowed":False})
    return True


def handle_post(handler,path)->bool:
    """No editing, approval, review or publish via project explorer HTTP."""
    if PREFIX.fullmatch(path):
        handler.json_response(405,{"ok":False,"error":{
            "code":"pie_read_only","message":"PIE Explorer доступен только для чтения."}})
        return True
    return False
