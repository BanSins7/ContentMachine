"""Black-box D1 smoke on an ephemeral GitHub-hosted Windows runner.

This is a CI-only test, NOT a script to run on the user's production database.
No downloading videos, cloud APIs or paid operations.
"""
from __future__ import annotations
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = "http://127.0.0.1:8765"
SERVER = None

def check(value, message):
    if not value:
        raise AssertionError(message)
    print("PASS:", message, flush=True)

def api(path, data=None, header=True):
    headers = {"Content-Type": "application/json"}
    if data is not None and header:
        headers["X-ContentMachine"] = "1"
    request = Request(BASE + path,
                      data=None if data is None else json.dumps(data).encode("utf-8"),
                      headers=headers, method="GET" if data is None else "POST")
    try:
        with urlopen(request, timeout=8) as response:
            return response.status, json.load(response)
    except HTTPError as exc:
        return exc.code, json.load(exc)

def wait_for(callback, seconds=90):
    deadline = time.monotonic() + seconds
    last_error = None
    while time.monotonic() < deadline:
        try:
            result = callback()
            if result is not None:
                return result
        except (URLError, OSError, ValueError, TimeoutError) as exc:
            last_error = exc
        time.sleep(0.25)
    raise AssertionError("Timed out: " + repr(last_error))

def start():
    global SERVER
    env = dict(os.environ, CM_NO_BROWSER="1", PYTHONUNBUFFERED="1")
    SERVER = subprocess.Popen([sys.executable, "-u", str(ROOT / "app.py")],
                              cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    def ready():
        check(SERVER.poll() is None, "local HTTP process running")
        code, state = api("/api/state")
        return state if code == 200 and state.get("ok") else None
    state = wait_for(ready, seconds=35)
    check(all(p in state.get("platforms", {}) for p in ("instagram", "tiktok", "youtube")),
          "Instagram, TikTok and YouTube modules registered")

def stop():
    global SERVER
    if SERVER is not None:
        if SERVER.poll() is None:
            SERVER.terminate()
            try:
                SERVER.wait(timeout=12)
            except subprocess.TimeoutExpired:
                SERVER.kill()
                SERVER.wait(timeout=8)
        SERVER = None

def run_status(run_id):
    def completed():
        code, result = api("/api/intelligence/search-runs?id=" + str(run_id))
        check(code == 200, "run accessible via HTTP")
        return result if result["state"] in ("completed", "failed", "partial", "cancelled") else None
    return wait_for(completed)

def youtube_url():
    return "https://www.youtube.com/shorts/" + uuid.uuid4().hex[:11]

def main():
    check(os.environ.get("CM_WINDOWS_SMOKE_EPHEMERAL_DB") == "YES",
          "explicit ephemeral test database guard")
    check(os.environ.get("CI") == "true", "CI-only guard")
    check(os.environ.get("CM_DB_ENABLED") == "1" and bool(os.environ.get("CM_DB_DSN")),
          "PostgreSQL enabled")
    import psycopg
    with psycopg.connect(os.environ["CM_DB_DSN"], connect_timeout=5) as conn:
        check(conn.execute("SHOW server_version_num").fetchone()[0].startswith("17"),
              "PostgreSQL 17 running")
        check(conn.execute("SELECT to_regclass('public.search_runs')").fetchone()[0] is not None,
              "migration 002 applied")
        check(conn.execute("SELECT to_regclass('public.carousel_feature_a_items')").fetchone()[0] is not None,
              "migration 004 carousel schema applied")
        check(conn.execute("SELECT to_regclass('public.content_b_ctas')").fetchone()[0] is not None,
              "migration 005 B1-B8 schema applied")
        check(conn.execute("SELECT to_regclass('public.content_b_story_slides')").fetchone()[0] is not None,
              "B3 slide-role schema applied")
        check(conn.execute("SELECT to_regclass('public.content_b_subtopics')").fetchone()[0] is not None,
              "B8 hierarchy applied")
    try:
        start()
        code, health = api("/api/intelligence/health")
        schema_version = str(health.get("schema_version") or "")
        schema_number = schema_version.split("_", 1)[0]
        check(code == 200 and health.get("database") == "connected"
              and schema_number.isdigit() and int(schema_number) >= 2,
              "health endpoint and schema version (at least D1 migration 002)")
        code, preview = api("/api/intelligence/preview", {})
        check(code == 200 and "youtube" in preview.get("blocked", []),
              "D1 external source collection remains blocked")
        code, _ = api("/api/intelligence/preview", {}, header=False)
        check(code == 403, "write API refuses missing local request header")
        for platform, url in (
            ("youtube", youtube_url()),
            ("tiktok", "https://www.tiktok.com/@publicuser/video/7234567890123456789"),
            ("instagram", "https://www.instagram.com/reel/abcdefghi123/")
        ):
            code, found = api("/api/find", {"platform": platform, "target": url})
            check(code == 200 and found.get("count") == 1,
                  platform + " URL routing works without network download")
        # TikTok /photo/ URLs are metadata-only publications, not videos to download.
        photo_id = str(7000000000000000000 + (uuid.uuid4().int % 1000000000000000000))
        photo_link = "https://www.tiktok.com/@publicuser/photo/" + photo_id
        code, queued_photo = api("/api/intelligence/manual-imports", {
            "format": "urls", "text": photo_link,
            "topic": "windows-d1-photo", "idempotency_key": uuid.uuid4().hex})
        check(code == 202 and queued_photo.get("queued_rows") == 1,
              "TikTok photo publication accepted for metadata-only D1 import")
        photo_run = run_status(queued_photo["run_id"])
        check(photo_run["state"] == "completed" and photo_run["counts"]["new"] == 1,
              "TikTok photo publication is indexed without media download")
        with psycopg.connect(os.environ["CM_DB_DSN"], connect_timeout=5) as conn:
            row = conn.execute(
                "SELECT content_kind FROM videos WHERE platform='tiktok' AND platform_video_id=%s",
                (photo_id,)).fetchone()
        check(row == (None,), "photo URL alone does not invent number/type of slides")

        links = [youtube_url() for _ in range(51)]
        text = "\n".join(links + ["not-a-url"])
        def import_rows(key):
            status, result = api("/api/intelligence/manual-imports", {
                "format": "urls", "text": text,
                "topic": "windows-d1-smoke", "idempotency_key": key})
            check(status == 202 and result["queued_rows"] == 52, "52-row import queued")
            return run_status(result["run_id"])
        first = import_rows(uuid.uuid4().hex)
        check(first["state"] == "completed" and first["counts"]["new"] == 51
              and first["counts"]["rejected"] == 1,
              "multi-chunk import persists 51 new rows, rejects one invalid row")
        check(first["flags"].get("invalid_url") == 1, "quality flags preserved")
        second = import_rows(uuid.uuid4().hex)
        check(second["state"] == "completed" and second["counts"]["new"] == 0
              and second["counts"]["duplicate"] == 51, "reimport deduplicates all valid IDs")
        code, catalog = api("/api/intelligence/videos?limit=50")
        check(code == 200 and len(catalog["videos"]) > 0, "catalog query")
        item = catalog["videos"][0]
        code, trace = api("/api/intelligence/videos/" + str(item["id"]) + "/trace")
        check(code == 200 and bool(trace["discoveries"]), "provenance trace available")
        code, label = api("/api/intelligence/human-labels", {
            "video_id": item["id"], "topic": "windows-d1-smoke", "label": "relevant"})
        check(code == 201 and label["label"] == "relevant", "human relevance label persisted")

        # Real Windows process termination + stale lease simulates a crashed worker.
        stop()
        from intelligence.discovery import service
        recovery_url = youtube_url()
        recovery = service.create_import(recovery_url, "urls", "windows-d1-recovery",
                                         uuid.uuid4().hex)
        with service.connect() as conn, conn.transaction():
            row = conn.execute("""UPDATE jobs SET state='running',
                  locked_by='crashed-windows-worker', attempts=1,
                  lease_generation=1, lease_until=now()-interval '1 second',
                  updated_at=now() WHERE job_type='discovery.import'
                  AND payload->>'run_id'=%s RETURNING id""",
                  (str(recovery["run_id"]),)).fetchone()
            check(bool(row), "simulated lost worker holding expired lease")
        start()
        finished = run_status(recovery["run_id"])
        check(finished["state"] == "completed" and finished["counts"]["new"] == 1,
              "crashed job recovered after Windows server restart")
        with service.connect() as conn:
            n = conn.execute("SELECT count(*) FROM videos WHERE platform_video_id=%s",
                             (recovery_url.rsplit("/", 1)[-1],)).fetchone()[0]
        check(n == 1, "restart did not duplicate recovered record")

        stop()
        cancelled_url = youtube_url()
        cancelled = service.create_import(cancelled_url, "urls", "windows-d1-cancel",
                                          uuid.uuid4().hex)
        service.cancel_run(cancelled["run_id"])
        start()
        status = run_status(cancelled["run_id"])
        check(status["state"] == "cancelled" and status["finished_at"],
              "cancelled run remains cancelled after restart with finished_at")
        with service.connect() as conn:
            n = conn.execute("SELECT count(*) FROM videos WHERE platform_video_id=%s",
                             (cancelled_url.rsplit("/", 1)[-1],)).fetchone()[0]
        check(n == 0, "cancelled queued task never writes data")
        print("WINDOWS D1 SMOKE PASSED: DB, HTTP, import, dedup, trace, label, restart, cancel", flush=True)
    finally:
        stop()

if __name__ == "__main__":
    main()
