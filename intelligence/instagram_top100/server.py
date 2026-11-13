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
from .archives import archive_path

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

    def stream_archive(self, path: Path, run_id: str, batch: int):
        # Copy in bounded chunks: a 100-video ZIP can exceed available RAM.
        import zipfile
        try:
            with zipfile.ZipFile(path, "r") as archive:
                # Check the ZIP index and manifest only. Reading 100 MP4s here
                # would double disk I/O and delay every large download.
                names = archive.namelist()
                if not names or names[-1] != "manifest.json":
                    raise ValueError("ZIP manifest missing.")
                manifest = json.loads(archive.read("manifest.json"))
                if manifest.get("run_id") != run_id or manifest.get("batch_number") != batch:
                    raise ValueError("ZIP manifest mismatch.")
                if len(names) != manifest.get("video_count", 0) + 1:
                    raise ValueError("ZIP member count mismatch.")
        except (OSError, ValueError, KeyError, zipfile.BadZipFile):
            return self.send_json(409, {"ok": False, "error": "ZIP повреждён."})
        name = f"instagram_reels_{run_id[:8]}_batch_{batch:03d}.zip"
        self.send_response(200)
        self.send_header("Content-Type", "application/zip")
        self.send_header("Content-Disposition", f'attachment; filename="{name}"')
        self.send_header("Content-Length", str(path.stat().st_size))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        try:
            with path.open("rb") as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    self.wfile.write(chunk)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            pass

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
        if route.path == "/api/instagram-top100/archive":
            values = parse_qs(route.query)
            try:
                ident = values.get("id", [""])[0]
                batch = int(values.get("batch", ["0"])[0])
                run = RUNS.get(ident)
                available = next((x for x in run.get("archives", [])
                                  if x.get("number") == batch and x.get("status") == "ready"), None)
                if available is None:
                    raise ValueError("ZIP этого батча ещё не готов.")
                location = archive_path(RUNS.base, ident, batch)
                if not location.is_file():
                    raise ValueError("ZIP отсутствует на диске.")
                return self.stream_archive(location, ident, batch)
            except (ValueError, OSError, TypeError):
                return self.send_json(404, {"ok": False, "error": "Архив не найден."})
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
