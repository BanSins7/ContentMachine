"""Durable, explicitly authorized Instagram profile research and MP4 download.

Bright Data discovers public Reels (ranked by observed public views).
The existing local_worker exact downloader verifies shortcode before writing MP4.
No paid LLM / ASR calls and no anonymous browsing/cookie circumvention.
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from intelligence.market_intelligence import brightdata_provider as bd
from .core import select_reels, usernames
from .archives import append_download, inventory_from_run, package_available
from .diagnostics import probe_brightdata

DATASET = "gd_lyclm20il4r5helnj"  # existing CM Instagram Reels dataset
MAX_SCAN = 5000
MAX_ROW_BYTES = 3_000_000
POLL_SECONDS = 10
POLL_LIMIT_SECONDS = 900


def provider_trigger(profile_url: str, scan_limit: int) -> str:
    """Paid action. Caller MUST persist 'submitting' before invoking this.

    Reels Discover no longer accepts start_date/end_date (Bright Data, Aug 2026).
    Dates are checked locally against verified date_posted when available.
    Sending unsupported dates previously resulted in empty snapshots.
    """
    if not 100 <= scan_limit <= MAX_SCAN:
        raise ValueError("Число проверяемых Reels должно быть от 100 до 5000.")
    payload = {
        "input": [{"url": profile_url, "num_of_posts": scan_limit}],
        "limit_per_input": scan_limit,
    }
    # This service requires explicit paid + rights consent in RunManager.start.
    # Do not enable unrelated Market Intelligence billing flags when saving a token.
    response = bd._api("POST", "/datasets/v3/trigger", bd._read_only_token(),
                       query={"dataset_id": bd.dataset_id("instagram"),
                              "format": "json", "include_errors": "true",
                              "notify": "false", "type": "discover_new",
                              "discover_by": "url"},
                       payload=payload)
    snapshot = response.get("snapshot_id") if isinstance(response, dict) else None
    if not isinstance(snapshot, str) or not bd.SNAPSHOT_RE.fullmatch(snapshot):
        raise ValueError("Bright Data не вернул корректный snapshot_id. Автоповтор запрещён.")
    return snapshot


def provider_records(snapshot_id: str) -> list[dict]:
    """Read full NDJSON rather than the bounded JSON endpoint used for post metrics."""
    if not bd.SNAPSHOT_RE.fullmatch(snapshot_id):
        raise ValueError("Некорректный Bright Data snapshot ID.")
    url = bd.ENDPOINT + "/datasets/v3/snapshot/" + snapshot_id + "?" + urlencode({"format": "ndjson"})
    req = Request(url, headers={"Authorization": "Bearer " + bd._read_only_token(),
                                 "Accept": "application/x-ndjson",
                                 "User-Agent": "ContentMachine-InstagramTop100/1"})
    records = []
    with urlopen(req, timeout=180) as response:
        for line in response:
            if len(line) > MAX_ROW_BYTES:
                raise ValueError("Bright Data вернул слишком большую запись.")
            if not line.strip():
                continue
            row = json.loads(line)
            if isinstance(row, dict):
                records.append(row)
            if len(records) >= 10000:
                raise ValueError("Лимит 10000 записей достигнут — выборка может быть неполной.")
    return records


def existing_verified_mp4(account: str, post: dict) -> bool:
    """Reuse a file only with matching source ID and actual checksum."""
    root = Path.home() / "Downloads" / "InstagramArchive" / account
    video = root / (post["shortcode"] + ".mp4")
    meta = video.with_suffix(".json")
    if not video.is_file() or not meta.is_file():
        return False
    try:
        import hashlib
        info = json.loads(meta.read_text(encoding="utf-8"))
        if (info.get("verified_shortcode") != post["shortcode"]
            or info.get("source_url") != post["url"]
            or not info.get("video_sha256") or video.stat().st_size <= 0):
            return False
        checksum = hashlib.sha256()
        with video.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                checksum.update(chunk)
        return checksum.hexdigest() == info["video_sha256"]
    except (OSError, ValueError, TypeError):
        return False


def existing_downloader(account: str, post: dict) -> tuple[bool, str]:
    """Reuse Content Machine's existing exact Instagram downloader in MP4-only mode."""
    import sys
    worker_path = str(Path(__file__).resolve().parents[2] / "worker")
    if worker_path not in sys.path:
        sys.path.insert(0, worker_path)
    import local_worker as w
    job_id = uuid.uuid4().hex
    video = Path.home() / "Downloads" / "InstagramArchive" / account / (post["shortcode"] + ".mp4")
    with w.job_lock:
        w.jobs[job_id] = {"job_id": job_id, "video_path": str(video), "status": "queued",
                          "phase": "queued", "progress": None}
    try:
        w.exact_download_job(job_id, {"account": account, "shortcode": post["shortcode"],
                                     "source_url": post["url"], "engine": "off"})
        with w.job_lock:
            outcome = dict(w.jobs.pop(job_id))
        return outcome.get("status") == "complete", str(outcome.get("error") or "")
    except Exception as exc:
        with w.job_lock:
            w.jobs.pop(job_id, None)
        return False, str(exc)[:250]


class RunManager:
    def __init__(self, base: Path | None = None):
        self.base = base or Path.home() / "Downloads" / "ContentMachineInstagramTop100"
        self.runs_dir = self.base / "runs"
        self.runs_dir.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.thread: threading.Thread | None = None
        self.stop = threading.Event()
        self.active_id: str | None = None

    def _path(self, run_id: str) -> Path:
        if (len(run_id) != 32 or any(ch not in "0123456789abcdef" for ch in run_id)):
            raise ValueError("Некорректный run_id.")
        return self.runs_dir / (run_id + ".json")

    def _write(self, run: dict):
        path = self._path(run["id"])
        run["updated_at"] = datetime.now(timezone.utc).isoformat()
        temp = path.with_suffix(".json.tmp")
        with self.lock:
            temp.write_text(json.dumps(run, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(temp, path)

    def get(self, run_id: str) -> dict:
        path = self._path(run_id)
        if not path.is_file():
            raise ValueError("Запуск не найден.")
        with self.lock:
            return json.loads(path.read_text(encoding="utf-8"))

    def recent(self) -> list[dict]:
        items = []
        for path in sorted(self.runs_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)[:100]:
            try:
                run = json.loads(path.read_text(encoding="utf-8"))
                items.append({"id": run["id"], "created_at": run["created_at"],
                              "status": run["status"], "total_accounts": len(run["accounts"])})
            except (OSError, ValueError, KeyError):
                continue
        return sorted(items, key=lambda row: row["created_at"], reverse=True)

    def start(self, raw: str | list[str], scan_limit: int, *,
              paid_confirmed: bool, rights_confirmed: bool) -> dict:
        handles = usernames(raw)
        if type(scan_limit) is not int or not 100 <= scan_limit <= MAX_SCAN:
            raise ValueError("Лимит просмотра Reels на аккаунт: 100–5000.")
        if not paid_confirmed or not rights_confirmed:
            raise ValueError("Подтверди платный сбор Bright Data и права на скачивание.")
        if not bd.readiness()["configured"]:
            raise ValueError("Сначала сохрани API-ключ Bright Data в интерфейсе.")
        today = datetime.now(timezone.utc).date()
        since = date(today.year, 11, 1)
        if today < since:
            raise ValueError("Ноябрь этого года ещё не наступил.")
        # A read-only, nonbillable GET must succeed before any paid trigger.
        # Otherwise dozens of usernames would all become ambiguous jobs
        # after the same DNS/TLS/proxy outage.
        diagnostic = probe_brightdata()
        if not diagnostic["ok"]:
            raise ValueError("Запуск отменён до оплаты: " + diagnostic["message"])
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise ValueError("Уже выполняется сбор. Дождись завершения или останови его.")
            now = datetime.now(timezone.utc).isoformat()
            run = {"schema_version": "instagram-reels-top100/v1",
                   "id": uuid.uuid4().hex, "created_at": now,
                   "updated_at": now, "status": "running",
                   "since": since.isoformat(), "until": today.isoformat(),
                   "scan_limit_per_account": scan_limit, "top_per_account": 100,
                   "ranking_scope": "public_views_within_provider_returned_posts",
                   "archive_inventory": [], "archives": [],
                   "archive_status": "pending", "archive_error": None,
                   "accounts": [
                       {"username": handle, "profile_url": "https://www.instagram.com/" + handle + "/",
                        "status": "queued", "snapshot_id": None,
                        "observed": 0, "eligible": 0, "selected": 0,
                        "downloaded": 0, "cached": 0, "failed": 0,
                        "posts": [], "error": None, "rejected": {}}
                       for handle in handles]}
            self._write(run)
            self._launch(run["id"])
            return {"ok": True, "id": run["id"], "accounts": len(handles),
                    "start_date": since.isoformat(), "end_date": today.isoformat()}

    def _launch(self, run_id: str):
        self.stop.clear()
        self.active_id = run_id
        self.thread = threading.Thread(target=self._execute, args=(run_id,),
                                       name="instagram-top100-" + run_id[:8], daemon=True)
        self.thread.start()

    def cancel(self, run_id: str) -> dict:
        with self.lock:
            if self.active_id != run_id or self.thread is None or not self.thread.is_alive():
                raise ValueError("Этот запуск сейчас не выполняется.")
            self.stop.set()
        return {"ok": True, "cancel_requested": True}

    def resume(self, run_id: str) -> dict:
        """Continue known snapshots/downloads ONLY; never duplicate a paid trigger."""
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                raise ValueError("Сначала заверши выполняющийся запуск.")
            run = self.get(run_id)
            # A completed run may be explicitly resumed to create/rebuild ZIPs,
            # without any new discovery or media downloads.
            needs_provider = any(row["status"] in
                ("queued", "submitting", "waiting_snapshot", "ranking", "failed")
                and (row["status"] != "failed" or row.get("snapshot_id"))
                for row in run["accounts"])
            if needs_provider and not bd.readiness()["configured"]:
                raise ValueError("Bright Data не настроен: API-ключ не сохранён.")
            for row in run["accounts"]:
                if row["status"] == "partial" and row["snapshot_id"]:
                    # Explicit resume retries failed media only; no new paid trigger.
                    for post in row["posts"]:
                        if post["status"] == "failed":
                            post["status"] = "queued"
                            post["error"] = None
                    row["failed"] = 0
                    row["status"] = "downloading"
                if row["status"] == "failed" and row["snapshot_id"]:
                    row["status"] = "waiting_snapshot"
                # A run interrupted before a paid trigger can remain queued.
                # Once a trigger was attempted, its snapshot must be present.
                if row["status"] in ("submitting", "trigger_unknown") and not row["snapshot_id"]:
                    row["status"] = "trigger_unknown"
                    row["error"] = "Неизвестен результат платного запуска. Проверь Bright Data вручную."
            run["archive_error"] = None
            run["archive_status"] = "pending"
            run["status"] = "running"
            inventory_from_run(run)
            self._write(run)
            self._launch(run_id)
        return {"ok": True, "id": run_id}

    def _sync_archives(self, run: dict, *, final: bool):
        """Write full 100-MP4 ZIPs during work, partial tail when run ends."""
        run["archive_status"] = "building"
        self._write(run)
        package_available(run, self.base, include_partial=final,
                          on_archive=self._write)
        run["archive_error"] = None
        run["archive_status"] = "ready" if final else "building"
        self._write(run)

    def _execute(self, run_id: str):
        run = self.get(run_id)
        inventory_from_run(run)  # Recover completed downloads from pre-upgrade runs.
        self._write(run)
        try:
            for item in run["accounts"]:
                if self.stop.is_set():
                    run["status"] = "cancelled"
                    break
                if item["status"] in ("complete", "no_results", "trigger_unknown", "failed"):
                    continue
                try:
                    self._account(run, item)
                except Exception as exc:
                    item["error"] = str(exc)[:350]
                    item["status"] = "failed"
                    self._write(run)
                try:
                    # New full batches are available as soon as 100 MP4s finish.
                    self._sync_archives(run, final=False)
                except Exception as exc:
                    run["archive_error"] = str(exc)[:350]
                    run["archive_status"] = "failed"
                    self._write(run)
            else:
                if any(x["status"] in ("waiting_snapshot", "interrupted", "trigger_unknown", "partial") for x in run["accounts"]):
                    run["status"] = "partial"
                elif any(x["status"] == "failed" for x in run["accounts"]):
                    run["status"] = "partial"
                elif all(x["status"] == "no_results" for x in run["accounts"]):
                    run["status"] = "no_results"
                else:
                    run["status"] = "complete"
            if self.stop.is_set():
                run["status"] = "cancelled"
        finally:
            try:
                # A final ZIP may legitimately contain 1..99 MP4s.
                # It also preserves downloaded media when the run was cancelled.
                self._sync_archives(run, final=True)
            except Exception as exc:
                run["archive_status"] = "failed"
                run["archive_error"] = str(exc)[:350]
                if run["status"] == "complete":
                    run["status"] = "partial"
            self._write(run)
            with self.lock:
                if self.active_id == run_id:
                    self.active_id = None

    def _account(self, run: dict, item: dict):
        since = date.fromisoformat(run["since"])
        until = date.fromisoformat(run["until"])
        handle = item["username"]
        if not item["snapshot_id"] and not item["posts"]:
            # Ambiguous POST must never be retried automatically.
            if item["status"] not in ("queued",):
                item["status"] = "trigger_unknown"
                item["error"] = "Платный запуск уже мог состояться; snapshot неизвестен."
                self._write(run)
                return
            item["status"] = "submitting"
            self._write(run)
            try:
                snapshot = provider_trigger(item["profile_url"], run["scan_limit_per_account"])
            except Exception as exc:
                item["status"] = "trigger_unknown"
                item["error"] = "Проверь историю Bright Data перед новым запросом: " + str(exc)[:230]
                # Fail fast. The preceding POST may or may not have been
                # billed; NEVER submit the other accounts automatically.
                self.stop.set()
                self._write(run)
                return
            item["snapshot_id"] = snapshot
            item["status"] = "waiting_snapshot"
            self._write(run)

        if not item["posts"]:
            item["status"] = "waiting_snapshot"
            self._write(run)
            deadline = time.monotonic() + POLL_LIMIT_SECONDS
            while time.monotonic() < deadline and not self.stop.is_set():
                progress = bd.progress(item["snapshot_id"])
                if progress == "ready":
                    break
                if progress == "failed":
                    raise ValueError("Bright Data сообщил об ошибке snapshot.")
                time.sleep(POLL_SECONDS)
            else:
                item["status"] = "waiting_snapshot"
                item["error"] = "Ожидание snapshot приостановлено. Нажми «Продолжить»."
                self._write(run)
                return
            item["status"] = "ranking"
            self._write(run)
            records = provider_records(item["snapshot_id"])
            selection = select_reels(handle, records, since, until)
            item["observed"] = selection["provider_records"]
            item["eligible"] = selection["verified_ranked"]
            item["selected"] = selection["selected"]
            item["rejected"] = selection["rejected"]
            item["posts"] = [{**post, "status": "queued", "error": None} for post in selection["posts"]]
            item["error"] = None
            self._write(run)
            if not item["posts"]:
                # Empty provider output and zero verified Reels are different
                # outcomes and must not be marked as successful downloads.
                if not records:
                    item["error"] = ("Bright Data вернул 0 записей. Проверь snapshot "
                                     "и период публикаций в кабинете Bright Data.")
                elif selection["rejected"]["invalid_record"] == len(records):
                    item["error"] = ("Все записи Bright Data содержат ошибку или "
                                     "неподдерживаемый формат. Проверь snapshot.")
                    item["status"] = "failed"
                    self._write(run)
                    return
                else:
                    item["error"] = ("Получены записи Bright Data, но ни одна не "
                                     "прошла проверку автора, даты, формата и просмотров.")
                item["status"] = "no_results"
                self._write(run)
                return

        item["status"] = "downloading"
        self._write(run)
        for post in item["posts"]:
            if self.stop.is_set():
                item["status"] = "interrupted"
                self._write(run)
                return
            if post["status"] in ("downloaded", "cached", "failed"):
                continue
            if existing_verified_mp4(handle, post):
                post["status"] = "cached"
                item["cached"] += 1
            else:
                post["status"] = "downloading"
                self._write(run)
                succeeded, error = existing_downloader(handle, post)
                # Never package an unverified or partially written MP4.
                succeeded = succeeded and existing_verified_mp4(handle, post)
                post["status"] = "downloaded" if succeeded else "failed"
                if succeeded:
                    item["downloaded"] += 1
                else:
                    item["failed"] += 1
                    post["error"] = (error or "Файл MP4 не прошёл проверку.")[:240]
            append_download(run, handle, post)
            self._write(run)
        item["status"] = "partial" if item["failed"] else "complete"
        self._write(run)
