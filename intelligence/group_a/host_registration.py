"""Explicit local host mount for GA21; NO app.py modification or auto-hooks.

The host must opt in at its ThreadingHTTPServer registration point:

    HandlerWithGA = mount_group_a_handler(app.Handler, facade)
    ThreadingHTTPServer((app.HOST, app.PORT), HandlerWithGA)

No routes are registered simply by importing Group A.
"""
from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from .http_api import GroupAHTTPFacade

STATIC={
    "/group-a":("group-a.html","text/html; charset=utf-8"),
    "/group-a.js":("group-a.js","text/javascript; charset=utf-8"),
    "/group-a.css":("group-a.css","text/css; charset=utf-8"),
}


def mount_group_a_handler(host_handler_class,facade):
    if not isinstance(facade,GroupAHTTPFacade):
        raise TypeError("Explicit GA21 security facade required")
    if not all(callable(getattr(host_handler_class,name,None))
               for name in ("do_GET","do_POST","authorized","respond","json_response")):
        raise TypeError("Host must supply secured local HTTP handler methods")

    class GroupAMountedHandler(host_handler_class):
        def do_GET(self):
            route=urlparse(self.path)
            if route.path in STATIC:
                host=self.headers.get("Host","").lower()
                origin=self.headers.get("Origin","")
                if (host not in facade.allowed_hosts or
                    (origin and origin not in {
                        "http://"+h for h in facade.allowed_hosts
                    }) or not self.authorized(mutation=False)):
                    return self.json_response(
                        403,{"ok":False,"error":{"code":"local_origin_required"}}
                    )
                # Static assets never receive a project token or local path;
                # any actual project data still needs GA21 per-request auth.
                filename,mime=STATIC[route.path]
                if route.path!="/group-a" and route.query:
                    return self.json_response(400,{
                        "ok":False,"error":{"code":"invalid_query"}
                    })
                data=(Path(__file__).resolve().parent/"site"/filename).read_bytes()
                return self.respond(200,data,mime)
            if facade.handle(self,route,"GET"):
                return
            return super().do_GET()

        def do_POST(self):
            route=urlparse(self.path)
            if facade.handle(self,route,"POST"):
                return
            return super().do_POST()

    GroupAMountedHandler.__name__="GroupAMountedHandler"
    return GroupAMountedHandler
