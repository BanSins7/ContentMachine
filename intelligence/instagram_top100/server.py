"""127.0.0.1-only HTTP service for the isolated React + TypeScript dashboard."""
from __future__ import annotations

import json
import mimetypes
import os
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from intelligence.market_intelligence import brightdata_provider as bd
from .service import RunManager

HOST = "127.0.0.1"
PORT = int(os.environ.get("CM_INSTAGRAM_TOP100_PORT", "8767"))
ROOT = Path(__file__).resolve().parents[2]
DIST = ROOT / "instagram-top100-ui" / "dist"
RUNS = RunManager()


class Handler(BaseHTTPRequestHandler):
    server_version = "ContentMachineInstagramTop100/1"

    def log_message(self, fmt, *args):
        # No profile URLs, provider identifiers or potentially sensitive query data in logs.
        pass

    def authorized(self, write=False) -> bool:
        host = self.headers.get("Host", "").lower()
        if host not in (f"{HOST}:{PORT}", f"localhost:{PORT}"):
            return False
        origin = self.headers.get("Origin", "")
        if origin and origin not in (f"http://{HOST}:{PORT}", f"http://localhost:{PORT}",
                                        "http://127.0.0.1:5174", "http://localhost:5174"):
            return False
        return not write or self.headers.get("X-ContentMachine") == "1"

    def respond(self, status: int, data: bytes, mime: str):
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; style-src 'self' 'unsafe-inline'; "
                         "script-src 'self'; connect-src 'self'; img-src 'self' data:; "
                         "base-uri 'none'; frame-ancestors 'none'")
        self.end_headers()
        try:
            self.wfile.write(data)
        except (BrokenPipeError, ConnectionAbortedError):
            pass

    def send_json(self, status: int, payload: dict):
        self.respond(status, json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                     "application/json; charset=utf-8")

    def do_GET(self):
        if not self.authorized():
            return self.send_json(403, {"ok": False, "error": "Недопустимый origin."})
        route = urlsplit(self.path)
        if route.path == "/api/instagram-top100/health":
            return self.send_json(200, {"ok": True, "provider": bd.readiness(),
                                        "output_directory": str(RUNS.base)})
        if route.path == "/api/instagram-top100/runs":
            return self.send_json(200, {"ok": True, "runs": RUNS.recent()})
        if route.path == "/api/instagram-top100/status":
            ident = parse_qs(route.query).get("id", [""])[0]
            try:
                return self.send_json(200, {"ok": True, "run": RUNS.get(ident)})
            except ValueError as exc:
                return self.send_json(404, {"ok": False, "error": str(exc)})
        if route.path.startswith("/api/"):
            return self.send_json(404, {"ok": False, "error": "Маршрут не найден."})
        path = "index.html" if route.path in ("/", "/index.html") else route.path.lstrip("/")
        target = (DIST / path).resolve()
        if not target.is_relative_to(DIST.resolve()) or not target.is_file():
            return self.send_json(404, {"ok": False,
                                        "error": "UI не собран. Запусти npm run build в instagram-top100-ui."})
        mime, _ = mimetypes.guess_type(str(target))
        return self.respond(200, target.read_bytes(), (mime or "application/octet-stream") +
                            ("; charset=utf-8" if target.suffix in (".html", ".js", ".css") else ""))

    def do_POST(self):
        if not self.authorized(write=True):
            return self.send_json(403, {"ok": False, "error": "Недопустимый запрос."})
        route = urlsplit(self.path).path
        if route not in ("/api/instagram-top100/start",
                         "/api/instagram-top100/cancel", "/api/instagram-top100/resume"):
            return self.send_json(404, {"ok": False, "error": "Маршрут не найден."})
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if size < 2 or size > 600000:
                raise ValueError("Слишком большой или пустой запрос.")
            body = json.loads(self.rfile.read(size).decode("utf-8"))
            if not isinstance(body, dict):
                raise ValueError("Нужен JSON-объект.")
            if route.endswith("/start"):
                result = RUNS.start(body.get("usernames"),
                                    body.get("scan_limit", 500),
                                    paid_confirmed=body.get("confirm_paid") is True,
                                    rights_confirmed=body.get("confirm_rights") is True)
                return self.send_json(202, result)
            if route.endswith("/cancel"):
                result = RUNS.cancel(body.get("id", ""))
            else:
                result = RUNS.resume(body.get("id", ""))
            return self.send_json(200, result)
        except (ValueError, TypeError, OSError, KeyError, json.JSONDecodeError) as exc:
            return self.send_json(400, {"ok": False, "error": str(exc)[:350]})


def main():
    if not DIST.joinpath("index.html").is_file():
        print("React UI is missing. Run npm install && npm run build in instagram-top100-ui.")
    with ThreadingHTTPServer((HOST, PORT), Handler) as server:
        target = f"http://{HOST}:{PORT}/"
        print("Instagram Reels Top-100: " + target)
        print("Results: " + str(RUNS.base))
        if os.environ.get("CM_NO_BROWSER") != "1":
            webbrowser.open(target)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("Stopped.")


if __name__ == "__main__":
    main()
